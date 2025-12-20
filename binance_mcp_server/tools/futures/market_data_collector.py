"""
Market Data Collector for Limit Order Tools.

This module provides real-time market data collection for:
- Order book depth snapshots
- Aggregated trades (aggTrades)
- Mark price and funding rate

Uses Binance Futures REST API with WebSocket fallback pattern and short-term caching.
"""

import time
import logging
import threading
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
from dataclasses import dataclass, field
from binance_mcp_server.futures_config import get_futures_client, FuturesClient, ALLOWED_FUTURES_SYMBOLS

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Represents a single aggregated trade."""
    agg_trade_id: int
    price: float
    qty: float
    first_trade_id: int
    last_trade_id: int
    timestamp_ms: int
    is_buyer_maker: bool  # True = seller is aggressor (sell), False = buyer is aggressor (buy)
    
    @property
    def side(self) -> str:
        """Return aggressor side: 'sell' if buyer_maker, else 'buy'."""
        return "sell" if self.is_buyer_maker else "buy"


@dataclass
class OrderBookSnapshot:
    """Represents an order book snapshot."""
    symbol: str
    timestamp_ms: int
    last_update_id: int
    bids: List[Tuple[float, float]]  # [(price, qty), ...]
    asks: List[Tuple[float, float]]  # [(price, qty), ...]
    
    @property
    def best_bid(self) -> Optional[Tuple[float, float]]:
        return self.bids[0] if self.bids else None
    
    @property
    def best_ask(self) -> Optional[Tuple[float, float]]:
        return self.asks[0] if self.asks else None
    
    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid[0] + self.best_ask[0]) / 2
        return None
    
    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask[0] - self.best_bid[0]
        return None
    
    @property
    def spread_bps(self) -> Optional[float]:
        """Spread in basis points."""
        if self.mid_price and self.spread:
            return (self.spread / self.mid_price) * 10000
        return None


@dataclass
class MarkPriceInfo:
    """Mark price and funding rate information."""
    symbol: str
    mark_price: float
    index_price: float
    estimated_settle_price: float
    last_funding_rate: float
    next_funding_time: int
    timestamp_ms: int


class MarketDataCache:
    """
    Thread-safe cache for market data with configurable TTL.
    """
    
    def __init__(self, default_ttl_seconds: float = 1.0):
        self._cache: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
        self._ttls: Dict[str, float] = {}
        self._lock = threading.RLock()
        self.default_ttl = default_ttl_seconds
    
    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        with self._lock:
            if key not in self._cache:
                return None
            
            ttl = self._ttls.get(key, self.default_ttl)
            if time.time() - self._timestamps.get(key, 0) > ttl:
                # Expired
                del self._cache[key]
                del self._timestamps[key]
                if key in self._ttls:
                    del self._ttls[key]
                return None
            
            return self._cache[key]
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None):
        """Set cached value with optional custom TTL."""
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = time.time()
            if ttl is not None:
                self._ttls[key] = ttl
    
    def invalidate(self, key: str):
        """Invalidate a cache entry."""
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
            self._ttls.pop(key, None)


class TradeBuffer:
    """
    Circular buffer for recent trades with time-based window.
    """
    
    def __init__(self, max_size: int = 10000, max_age_seconds: int = 300):
        self._trades: deque = deque(maxlen=max_size)
        self._lock = threading.RLock()
        self.max_age_seconds = max_age_seconds
    
    def add_trades(self, trades: List[TradeRecord]):
        """Add trades to buffer."""
        with self._lock:
            for trade in trades:
                self._trades.append(trade)
    
    def get_trades_in_window(self, lookback_seconds: float) -> List[TradeRecord]:
        """Get trades within lookback window."""
        with self._lock:
            cutoff_ms = int((time.time() - lookback_seconds) * 1000)
            return [t for t in self._trades if t.timestamp_ms >= cutoff_ms]
    
    def clear_old_trades(self):
        """Remove trades older than max_age_seconds."""
        with self._lock:
            cutoff_ms = int((time.time() - self.max_age_seconds) * 1000)
            while self._trades and self._trades[0].timestamp_ms < cutoff_ms:
                self._trades.popleft()
    
    @property
    def oldest_timestamp_ms(self) -> Optional[int]:
        with self._lock:
            return self._trades[0].timestamp_ms if self._trades else None
    
    @property
    def newest_timestamp_ms(self) -> Optional[int]:
        with self._lock:
            return self._trades[-1].timestamp_ms if self._trades else None
    
    def __len__(self):
        with self._lock:
            return len(self._trades)


class MarketDataCollector:
    """
    Collects and manages market data for limit order analysis.
    
    Features:
    - REST API fetching with automatic caching
    - Rate limiting awareness
    - Trade history buffering for consumption rate calculation
    """
    
    # Cache TTLs (in seconds)
    ORDERBOOK_CACHE_TTL = 0.5  # 500ms for orderbook
    TRADES_CACHE_TTL = 0.5     # 500ms for recent trades
    MARK_PRICE_CACHE_TTL = 1.0  # 1s for mark price
    
    def __init__(self, client: Optional[FuturesClient] = None):
        self.client = client or get_futures_client()
        self._cache = MarketDataCache(default_ttl_seconds=1.0)
        self._trade_buffers: Dict[str, TradeBuffer] = {}
        self._lock = threading.RLock()
    
    def _get_trade_buffer(self, symbol: str) -> TradeBuffer:
        """Get or create trade buffer for symbol."""
        with self._lock:
            if symbol not in self._trade_buffers:
                self._trade_buffers[symbol] = TradeBuffer()
            return self._trade_buffers[symbol]
    
    def fetch_orderbook(
        self, 
        symbol: str, 
        limit: int = 100,
        use_cache: bool = True
    ) -> Tuple[bool, Optional[OrderBookSnapshot], Optional[str]]:
        """
        Fetch order book snapshot.
        
        Args:
            symbol: Trading symbol (BTCUSDT, ETHUSDT)
            limit: Number of levels (5, 10, 20, 50, 100, 500, 1000)
            use_cache: Whether to use cached data
            
        Returns:
            Tuple of (success, snapshot, error_message)
        """
        cache_key = f"orderbook:{symbol}:{limit}"
        
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return True, cached, None
        
        try:
            success, data = self.client.get(
                "/fapi/v1/depth",
                params={"symbol": symbol.upper(), "limit": limit}
            )
            
            if not success:
                return False, None, data.get("message", "Failed to fetch orderbook")
            
            snapshot = OrderBookSnapshot(
                symbol=symbol.upper(),
                timestamp_ms=int(time.time() * 1000),
                last_update_id=data.get("lastUpdateId", 0),
                bids=[(float(b[0]), float(b[1])) for b in data.get("bids", [])],
                asks=[(float(a[0]), float(a[1])) for a in data.get("asks", [])]
            )
            
            self._cache.set(cache_key, snapshot, ttl=self.ORDERBOOK_CACHE_TTL)
            return True, snapshot, None
            
        except Exception as e:
            logger.error(f"Error fetching orderbook for {symbol}: {e}")
            return False, None, str(e)
    
    def fetch_recent_trades(
        self,
        symbol: str,
        limit: int = 1000,
        use_cache: bool = True
    ) -> Tuple[bool, List[TradeRecord], Optional[str]]:
        """
        Fetch recent aggregated trades.
        
        Args:
            symbol: Trading symbol
            limit: Number of trades to fetch (max 1000)
            use_cache: Whether to use cached data
            
        Returns:
            Tuple of (success, trades, error_message)
        """
        cache_key = f"aggtrades:{symbol}:{limit}"
        
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return True, cached, None
        
        try:
            success, data = self.client.get(
                "/fapi/v1/aggTrades",
                params={"symbol": symbol.upper(), "limit": limit}
            )
            
            if not success:
                return False, [], data.get("message", "Failed to fetch trades")
            
            trades = [
                TradeRecord(
                    agg_trade_id=t["a"],
                    price=float(t["p"]),
                    qty=float(t["q"]),
                    first_trade_id=t["f"],
                    last_trade_id=t["l"],
                    timestamp_ms=t["T"],
                    is_buyer_maker=t["m"]
                )
                for t in data
            ]
            
            # Add to buffer for historical analysis
            buffer = self._get_trade_buffer(symbol)
            buffer.add_trades(trades)
            buffer.clear_old_trades()
            
            self._cache.set(cache_key, trades, ttl=self.TRADES_CACHE_TTL)
            return True, trades, None
            
        except Exception as e:
            logger.error(f"Error fetching trades for {symbol}: {e}")
            return False, [], str(e)
    
    def fetch_historical_trades(
        self,
        symbol: str,
        start_time_ms: int,
        end_time_ms: Optional[int] = None,
        limit: int = 1000
    ) -> Tuple[bool, List[TradeRecord], Optional[str]]:
        """
        Fetch historical aggregated trades within time range.
        
        Args:
            symbol: Trading symbol
            start_time_ms: Start timestamp in milliseconds
            end_time_ms: End timestamp in milliseconds (default: now)
            limit: Max trades per request
            
        Returns:
            Tuple of (success, trades, error_message)
        """
        if end_time_ms is None:
            end_time_ms = int(time.time() * 1000)
        
        try:
            all_trades = []
            current_start = start_time_ms
            
            # Fetch in batches
            while current_start < end_time_ms:
                success, data = self.client.get(
                    "/fapi/v1/aggTrades",
                    params={
                        "symbol": symbol.upper(),
                        "startTime": current_start,
                        "endTime": end_time_ms,
                        "limit": limit
                    }
                )
                
                if not success:
                    return False, all_trades, data.get("message", "Failed to fetch historical trades")
                
                if not data:
                    break
                
                batch_trades = [
                    TradeRecord(
                        agg_trade_id=t["a"],
                        price=float(t["p"]),
                        qty=float(t["q"]),
                        first_trade_id=t["f"],
                        last_trade_id=t["l"],
                        timestamp_ms=t["T"],
                        is_buyer_maker=t["m"]
                    )
                    for t in data
                ]
                
                all_trades.extend(batch_trades)
                
                if len(batch_trades) < limit:
                    break
                
                # Move start time forward
                current_start = batch_trades[-1].timestamp_ms + 1
            
            # Add to buffer
            buffer = self._get_trade_buffer(symbol)
            buffer.add_trades(all_trades)
            buffer.clear_old_trades()
            
            return True, all_trades, None
            
        except Exception as e:
            logger.error(f"Error fetching historical trades for {symbol}: {e}")
            return False, [], str(e)
    
    def fetch_mark_price(
        self,
        symbol: str,
        use_cache: bool = True
    ) -> Tuple[bool, Optional[MarkPriceInfo], Optional[str]]:
        """
        Fetch mark price and funding rate.
        
        Args:
            symbol: Trading symbol
            use_cache: Whether to use cached data
            
        Returns:
            Tuple of (success, mark_price_info, error_message)
        """
        cache_key = f"markprice:{symbol}"
        
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return True, cached, None
        
        try:
            success, data = self.client.get(
                "/fapi/v1/premiumIndex",
                params={"symbol": symbol.upper()}
            )
            
            if not success:
                return False, None, data.get("message", "Failed to fetch mark price")
            
            info = MarkPriceInfo(
                symbol=data["symbol"],
                mark_price=float(data["markPrice"]),
                index_price=float(data["indexPrice"]),
                estimated_settle_price=float(data.get("estimatedSettlePrice", 0)),
                last_funding_rate=float(data["lastFundingRate"]),
                next_funding_time=int(data["nextFundingTime"]),
                timestamp_ms=int(data["time"])
            )
            
            self._cache.set(cache_key, info, ttl=self.MARK_PRICE_CACHE_TTL)
            return True, info, None
            
        except Exception as e:
            logger.error(f"Error fetching mark price for {symbol}: {e}")
            return False, None, str(e)
    
    def get_buffered_trades(
        self,
        symbol: str,
        lookback_seconds: float
    ) -> List[TradeRecord]:
        """
        Get trades from buffer within lookback window.
        
        Args:
            symbol: Trading symbol
            lookback_seconds: Time window in seconds
            
        Returns:
            List of trades in window
        """
        buffer = self._get_trade_buffer(symbol)
        return buffer.get_trades_in_window(lookback_seconds)
    
    def ensure_trade_history(
        self,
        symbol: str,
        lookback_seconds: float
    ) -> Tuple[bool, Optional[str]]:
        """
        Ensure we have trade history for the lookback window.
        
        Fetches historical trades if buffer doesn't cover the window.
        
        Args:
            symbol: Trading symbol
            lookback_seconds: Required lookback window
            
        Returns:
            Tuple of (success, error_message)
        """
        buffer = self._get_trade_buffer(symbol)
        required_start_ms = int((time.time() - lookback_seconds) * 1000)
        
        # Check if buffer covers the required window
        oldest = buffer.oldest_timestamp_ms
        if oldest is not None and oldest <= required_start_ms:
            return True, None
        
        # Need to fetch historical trades
        success, _, error = self.fetch_historical_trades(
            symbol=symbol,
            start_time_ms=required_start_ms,
            limit=1000
        )
        
        return success, error


# Global collector instance
_collector: Optional[MarketDataCollector] = None
_collector_lock = threading.Lock()


def get_market_data_collector() -> MarketDataCollector:
    """Get or create the global MarketDataCollector instance."""
    global _collector
    
    with _collector_lock:
        if _collector is None:
            _collector = MarketDataCollector()
        return _collector
