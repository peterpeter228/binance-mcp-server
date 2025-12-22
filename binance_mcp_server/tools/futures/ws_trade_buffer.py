"""
WebSocket Trade Buffer Manager for Real-time Volume Profile.

Manages WebSocket connections to Binance Futures aggTrade streams and maintains
ring buffers for each subscribed symbol. Designed for high-performance local
volume profile calculations without REST API calls.

Key Features:
- Auto-reconnect on WebSocket disconnection
- Per-symbol ring buffers with configurable max age
- Thread-safe buffer access
- No external REST API calls
- Singleton pattern for global buffer management

Usage:
    # Get the global buffer manager
    manager = get_ws_trade_buffer_manager()
    
    # Subscribe to a symbol (starts WebSocket if needed)
    manager.subscribe("BTCUSDT")
    
    # Get trades for volume profile calculation
    trades = manager.get_trades("BTCUSDT", window_minutes=240)
"""

import os
import time
import json
import logging
import threading
import asyncio
from typing import Dict, Any, Optional, List, Callable
from collections import deque
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


@dataclass
class WSTradeRecord:
    """Represents a single aggregated trade from WebSocket."""
    agg_trade_id: int
    price: float
    qty: float
    timestamp_ms: int
    is_buyer_maker: bool  # True = seller is aggressor (sell)
    
    @property
    def side(self) -> str:
        """Return aggressor side: 'sell' if buyer_maker, else 'buy'."""
        return "sell" if self.is_buyer_maker else "buy"


class TradeRingBuffer:
    """
    Thread-safe ring buffer for storing trades with time-based eviction.
    
    Maintains trades for a configurable time window, automatically
    removing trades older than max_age_minutes.
    """
    
    def __init__(self, max_age_minutes: int = 300, max_size: int = 500000):
        """
        Initialize the ring buffer.
        
        Args:
            max_age_minutes: Maximum age of trades to keep (default 300 = 5 hours)
            max_size: Maximum number of trades to store
        """
        self._trades: deque = deque(maxlen=max_size)
        self._lock = threading.RLock()
        self.max_age_minutes = max_age_minutes
        self.max_size = max_size
        self._last_cleanup = time.time()
        self._cleanup_interval = 60  # Cleanup every 60 seconds
    
    def add_trade(self, trade: WSTradeRecord):
        """Add a trade to the buffer."""
        with self._lock:
            self._trades.append(trade)
            
            # Periodic cleanup
            if time.time() - self._last_cleanup > self._cleanup_interval:
                self._cleanup_old_trades()
    
    def add_trades_batch(self, trades: List[WSTradeRecord]):
        """Add multiple trades efficiently."""
        with self._lock:
            for trade in trades:
                self._trades.append(trade)
            
            if time.time() - self._last_cleanup > self._cleanup_interval:
                self._cleanup_old_trades()
    
    def _cleanup_old_trades(self):
        """Remove trades older than max_age_minutes."""
        cutoff_ms = int((time.time() - self.max_age_minutes * 60) * 1000)
        
        # Remove from front (oldest trades)
        while self._trades and self._trades[0].timestamp_ms < cutoff_ms:
            self._trades.popleft()
        
        self._last_cleanup = time.time()
    
    def get_trades(self, window_minutes: int) -> List[WSTradeRecord]:
        """
        Get trades within the specified window.
        
        Args:
            window_minutes: Time window in minutes
            
        Returns:
            List of trades within the window
        """
        with self._lock:
            cutoff_ms = int((time.time() - window_minutes * 60) * 1000)
            return [t for t in self._trades if t.timestamp_ms >= cutoff_ms]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get buffer statistics."""
        with self._lock:
            if not self._trades:
                return {
                    "trade_count": 0,
                    "oldest_trade_ms": None,
                    "newest_trade_ms": None,
                    "buffer_duration_minutes": 0
                }
            
            oldest_ms = self._trades[0].timestamp_ms
            newest_ms = self._trades[-1].timestamp_ms
            duration_minutes = (newest_ms - oldest_ms) / 60000
            
            return {
                "trade_count": len(self._trades),
                "oldest_trade_ms": oldest_ms,
                "newest_trade_ms": newest_ms,
                "buffer_duration_minutes": round(duration_minutes, 1)
            }
    
    def clear(self):
        """Clear all trades from the buffer."""
        with self._lock:
            self._trades.clear()


class BinanceWSClient:
    """
    WebSocket client for Binance Futures aggTrade streams.
    
    Features:
    - Auto-reconnect with exponential backoff
    - Multiple symbol subscriptions
    - Callback-based trade handling
    """
    
    # WebSocket endpoints
    FUTURES_WS_URL = "wss://fstream.binance.com/ws"
    FUTURES_TESTNET_WS_URL = "wss://stream.binancefuture.com/ws"
    
    def __init__(
        self,
        on_trade: Callable[[str, WSTradeRecord], None],
        testnet: bool = False,
        reconnect_delay_base: float = 1.0,
        reconnect_delay_max: float = 60.0
    ):
        """
        Initialize the WebSocket client.
        
        Args:
            on_trade: Callback function (symbol, trade) for each trade
            testnet: Whether to use testnet endpoint
            reconnect_delay_base: Base delay for reconnection (seconds)
            reconnect_delay_max: Maximum delay for reconnection (seconds)
        """
        self.on_trade = on_trade
        self.testnet = testnet
        self.reconnect_delay_base = reconnect_delay_base
        self.reconnect_delay_max = reconnect_delay_max
        
        self._subscribed_symbols: set = set()
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_attempt = 0
        self._lock = threading.Lock()
        self._connected = threading.Event()
    
    @property
    def ws_url(self) -> str:
        """Get appropriate WebSocket URL."""
        if self.testnet:
            return self.FUTURES_TESTNET_WS_URL
        return self.FUTURES_WS_URL
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected.is_set()
    
    def subscribe(self, symbol: str):
        """
        Subscribe to aggTrade stream for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
        """
        symbol = symbol.lower()
        
        with self._lock:
            if symbol in self._subscribed_symbols:
                return
            
            self._subscribed_symbols.add(symbol)
            
            # Start WebSocket thread if not running
            if not self._running:
                self._start_ws_thread()
            elif self._connected.is_set():
                # Send subscription message
                self._send_subscribe(symbol)
    
    def unsubscribe(self, symbol: str):
        """Unsubscribe from a symbol's stream."""
        symbol = symbol.lower()
        
        with self._lock:
            if symbol in self._subscribed_symbols:
                self._subscribed_symbols.remove(symbol)
                if self._connected.is_set():
                    self._send_unsubscribe(symbol)
    
    def _start_ws_thread(self):
        """Start the WebSocket event loop in a separate thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
    
    def _run_event_loop(self):
        """Run the asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        try:
            self._loop.run_until_complete(self._ws_loop())
        except Exception as e:
            logger.error(f"WebSocket event loop error: {e}")
        finally:
            self._loop.close()
    
    async def _ws_loop(self):
        """Main WebSocket connection loop with auto-reconnect."""
        import websockets
        
        while self._running:
            try:
                # Build stream URL for all subscribed symbols
                streams = [f"{s}@aggTrade" for s in self._subscribed_symbols]
                if not streams:
                    await asyncio.sleep(1)
                    continue
                
                url = f"{self.ws_url}/{'/'.join(streams)}"
                
                logger.info(f"Connecting to WebSocket: {url}")
                
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    self._connected.set()
                    self._reconnect_attempt = 0
                    
                    logger.info(f"WebSocket connected, subscribed to {len(streams)} streams")
                    
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            self._handle_message(data)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON from WebSocket: {message[:100]}")
                        except Exception as e:
                            logger.error(f"Error handling WebSocket message: {e}")
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected.clear()
                self._ws = None
                
                if not self._running:
                    break
                
                # Calculate reconnect delay with exponential backoff
                delay = min(
                    self.reconnect_delay_base * (2 ** self._reconnect_attempt),
                    self.reconnect_delay_max
                )
                self._reconnect_attempt += 1
                
                logger.warning(f"WebSocket disconnected: {e}. Reconnecting in {delay:.1f}s...")
                await asyncio.sleep(delay)
    
    def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket message."""
        # aggTrade message format
        if "e" in data and data["e"] == "aggTrade":
            symbol = data["s"].upper()
            trade = WSTradeRecord(
                agg_trade_id=data["a"],
                price=float(data["p"]),
                qty=float(data["q"]),
                timestamp_ms=data["T"],
                is_buyer_maker=data["m"]
            )
            self.on_trade(symbol, trade)
    
    def _send_subscribe(self, symbol: str):
        """Send subscription message to WebSocket."""
        if self._ws and self._loop:
            msg = {
                "method": "SUBSCRIBE",
                "params": [f"{symbol}@aggTrade"],
                "id": int(time.time() * 1000)
            }
            asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(msg)),
                self._loop
            )
    
    def _send_unsubscribe(self, symbol: str):
        """Send unsubscription message to WebSocket."""
        if self._ws and self._loop:
            msg = {
                "method": "UNSUBSCRIBE",
                "params": [f"{symbol}@aggTrade"],
                "id": int(time.time() * 1000)
            }
            asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(msg)),
                self._loop
            )
    
    def stop(self):
        """Stop the WebSocket client."""
        self._running = False
        self._connected.clear()
        
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        
        if self._thread:
            self._thread.join(timeout=5)


class WSTradeBufferManager:
    """
    Singleton manager for WebSocket trade buffers.
    
    Coordinates WebSocket connections and per-symbol trade buffers
    for real-time volume profile calculations.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._buffers: Dict[str, TradeRingBuffer] = {}
        self._buffer_lock = threading.Lock()
        
        # Determine testnet from environment
        testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
        
        # Create WebSocket client
        self._ws_client = BinanceWSClient(
            on_trade=self._on_trade,
            testnet=testnet
        )
        
        # Buffer settings
        self.max_age_minutes = 360  # Keep 6 hours of data
        self.max_trades_per_symbol = 500000
        
        logger.info(f"WSTradeBufferManager initialized (testnet={testnet})")
    
    def _get_buffer(self, symbol: str) -> TradeRingBuffer:
        """Get or create buffer for a symbol."""
        symbol = symbol.upper()
        
        with self._buffer_lock:
            if symbol not in self._buffers:
                self._buffers[symbol] = TradeRingBuffer(
                    max_age_minutes=self.max_age_minutes,
                    max_size=self.max_trades_per_symbol
                )
            return self._buffers[symbol]
    
    def _on_trade(self, symbol: str, trade: WSTradeRecord):
        """Callback for incoming trades."""
        buffer = self._get_buffer(symbol)
        buffer.add_trade(trade)
    
    def subscribe(self, symbol: str):
        """
        Subscribe to a symbol's trade stream.
        
        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
        """
        symbol = symbol.upper()
        self._get_buffer(symbol)  # Ensure buffer exists
        self._ws_client.subscribe(symbol)
        logger.info(f"Subscribed to {symbol} aggTrade stream")
    
    def unsubscribe(self, symbol: str):
        """Unsubscribe from a symbol's trade stream."""
        symbol = symbol.upper()
        self._ws_client.unsubscribe(symbol)
    
    def get_trades(self, symbol: str, window_minutes: int) -> List[WSTradeRecord]:
        """
        Get trades for a symbol within the specified window.
        
        Args:
            symbol: Trading symbol
            window_minutes: Time window in minutes
            
        Returns:
            List of trades within the window
        """
        symbol = symbol.upper()
        buffer = self._get_buffer(symbol)
        return buffer.get_trades(window_minutes)
    
    def get_buffer_stats(self, symbol: str) -> Dict[str, Any]:
        """
        Get buffer statistics for a symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Buffer statistics dictionary
        """
        symbol = symbol.upper()
        buffer = self._get_buffer(symbol)
        stats = buffer.get_stats()
        stats["is_connected"] = self._ws_client.is_connected
        stats["symbol"] = symbol
        return stats
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._ws_client.is_connected
    
    def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """
        Wait for WebSocket connection to be established.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if connected, False if timeout
        """
        return self._ws_client._connected.wait(timeout=timeout)
    
    def get_subscribed_symbols(self) -> List[str]:
        """Get list of subscribed symbols."""
        return [s.upper() for s in self._ws_client._subscribed_symbols]
    
    def clear_buffer(self, symbol: str):
        """Clear the buffer for a symbol."""
        symbol = symbol.upper()
        if symbol in self._buffers:
            self._buffers[symbol].clear()
    
    def stop(self):
        """Stop the buffer manager and WebSocket client."""
        self._ws_client.stop()
        logger.info("WSTradeBufferManager stopped")


# Global singleton instance
_manager: Optional[WSTradeBufferManager] = None
_manager_lock = threading.Lock()


def get_ws_trade_buffer_manager() -> WSTradeBufferManager:
    """
    Get the global WSTradeBufferManager instance.
    
    Returns:
        WSTradeBufferManager singleton instance
    """
    global _manager
    
    with _manager_lock:
        if _manager is None:
            _manager = WSTradeBufferManager()
        return _manager


def start_ws_buffer_for_symbols(symbols: List[str], wait_connected: bool = True) -> WSTradeBufferManager:
    """
    Convenience function to start WebSocket buffer for multiple symbols.
    
    Args:
        symbols: List of symbols to subscribe to
        wait_connected: Whether to wait for connection
        
    Returns:
        WSTradeBufferManager instance
    """
    manager = get_ws_trade_buffer_manager()
    
    for symbol in symbols:
        manager.subscribe(symbol)
    
    if wait_connected:
        manager.wait_for_connection(timeout=30.0)
    
    return manager
