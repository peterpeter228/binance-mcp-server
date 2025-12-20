"""
Microstructure Data Fetcher for Limit Order Analysis.

This module provides data fetching utilities for queue estimation and volume profile
analysis, using WebSocket with REST fallback for Binance Futures data.

Data sources:
- Order book depth (bids/asks)
- Aggregated trades (aggTrades)
- Mark price and funding rate
"""

import time
import logging
import threading
from typing import Dict, Any, Optional, List, Tuple
from collections import deque
from decimal import Decimal
import statistics

from binance_mcp_server.futures_config import get_futures_client, get_futures_config

logger = logging.getLogger(__name__)


class MicrostructureCache:
    """
    Thread-safe cache for microstructure data with short TTL.
    
    Caches orderbook, trades, and derived metrics with automatic expiry.
    """
    
    def __init__(self, default_ttl: float = 1.0):
        self.default_ttl = default_ttl
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        with self._lock:
            if key in self._cache:
                value, expires_at = self._cache[key]
                if time.time() < expires_at:
                    return value
                del self._cache[key]
        return None
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None):
        """Set cache value with optional custom TTL."""
        ttl = ttl if ttl is not None else self.default_ttl
        with self._lock:
            self._cache[key] = (value, time.time() + ttl)
    
    def clear(self):
        """Clear all cached values."""
        with self._lock:
            self._cache.clear()


# Global cache instance with 1 second TTL
_microstructure_cache = MicrostructureCache(default_ttl=1.0)


def get_microstructure_cache() -> MicrostructureCache:
    """Get the global microstructure cache."""
    return _microstructure_cache


class MicrostructureDataFetcher:
    """
    Fetcher for microstructure data from Binance Futures.
    
    Uses REST API with caching to minimize API calls while providing
    fresh data for queue estimation and volume profile analysis.
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.client = get_futures_client()
        self.cache = get_microstructure_cache()
    
    def fetch_orderbook(self, limit: int = 500) -> Dict[str, Any]:
        """
        Fetch current orderbook depth.
        
        Args:
            limit: Number of price levels per side (max 1000)
            
        Returns:
            Dict with bids, asks, lastUpdateId, and timestamp
        """
        cache_key = f"orderbook:{self.symbol}:{limit}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        
        success, data = self.client.get(
            "/fapi/v1/depth",
            {"symbol": self.symbol, "limit": min(limit, 1000)}
        )
        
        if not success:
            return {
                "success": False,
                "error": data.get("message", "Failed to fetch orderbook"),
                "timestamp": int(time.time() * 1000)
            }
        
        result = {
            "success": True,
            "bids": [(Decimal(p), Decimal(q)) for p, q in data.get("bids", [])],
            "asks": [(Decimal(p), Decimal(q)) for p, q in data.get("asks", [])],
            "lastUpdateId": data.get("lastUpdateId"),
            "timestamp": int(time.time() * 1000)
        }
        
        self.cache.set(cache_key, result, ttl=0.5)
        return result
    
    def fetch_agg_trades(self, lookback_ms: int = 30000) -> Dict[str, Any]:
        """
        Fetch aggregated trades for the lookback period.
        
        Args:
            lookback_ms: Lookback period in milliseconds
            
        Returns:
            Dict with trades array and metadata
        """
        cache_key = f"aggtrades:{self.symbol}:{lookback_ms}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        
        end_time = int(time.time() * 1000)
        start_time = end_time - lookback_ms
        
        success, data = self.client.get(
            "/fapi/v1/aggTrades",
            {
                "symbol": self.symbol,
                "startTime": start_time,
                "endTime": end_time,
                "limit": 1000  # Max per request
            }
        )
        
        if not success:
            return {
                "success": False,
                "error": data.get("message", "Failed to fetch aggTrades"),
                "trades": [],
                "timestamp": int(time.time() * 1000)
            }
        
        # Parse trades
        trades = []
        for t in data:
            trades.append({
                "id": t.get("a"),
                "price": Decimal(str(t.get("p", "0"))),
                "qty": Decimal(str(t.get("q", "0"))),
                "time": t.get("T"),
                "is_buyer_maker": t.get("m", False)  # True = sell aggressor, False = buy aggressor
            })
        
        result = {
            "success": True,
            "trades": trades,
            "count": len(trades),
            "start_time": start_time,
            "end_time": end_time,
            "timestamp": int(time.time() * 1000)
        }
        
        # Shorter TTL for trades since they're time-sensitive
        self.cache.set(cache_key, result, ttl=0.5)
        return result
    
    def fetch_mark_price(self) -> Dict[str, Any]:
        """
        Fetch current mark price and funding rate.
        
        Returns:
            Dict with markPrice, indexPrice, fundingRate
        """
        cache_key = f"markprice:{self.symbol}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        
        success, data = self.client.get(
            "/fapi/v1/premiumIndex",
            {"symbol": self.symbol}
        )
        
        if not success:
            return {
                "success": False,
                "error": data.get("message", "Failed to fetch mark price"),
                "timestamp": int(time.time() * 1000)
            }
        
        result = {
            "success": True,
            "markPrice": Decimal(str(data.get("markPrice", "0"))),
            "indexPrice": Decimal(str(data.get("indexPrice", "0"))),
            "lastFundingRate": Decimal(str(data.get("lastFundingRate", "0"))),
            "nextFundingTime": data.get("nextFundingTime"),
            "timestamp": int(time.time() * 1000)
        }
        
        self.cache.set(cache_key, result, ttl=1.0)
        return result
    
    def fetch_ticker_24h(self) -> Dict[str, Any]:
        """
        Fetch 24h ticker statistics.
        
        Returns:
            Dict with price change, volume, high/low prices
        """
        cache_key = f"ticker24h:{self.symbol}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        
        success, data = self.client.get(
            "/fapi/v1/ticker/24hr",
            {"symbol": self.symbol}
        )
        
        if not success:
            return {
                "success": False,
                "error": data.get("message", "Failed to fetch 24h ticker"),
                "timestamp": int(time.time() * 1000)
            }
        
        result = {
            "success": True,
            "lastPrice": Decimal(str(data.get("lastPrice", "0"))),
            "highPrice": Decimal(str(data.get("highPrice", "0"))),
            "lowPrice": Decimal(str(data.get("lowPrice", "0"))),
            "volume": Decimal(str(data.get("volume", "0"))),
            "quoteVolume": Decimal(str(data.get("quoteVolume", "0"))),
            "priceChangePercent": Decimal(str(data.get("priceChangePercent", "0"))),
            "timestamp": int(time.time() * 1000)
        }
        
        self.cache.set(cache_key, result, ttl=2.0)
        return result


def analyze_orderbook_imbalance(
    bids: List[Tuple[Decimal, Decimal]], 
    asks: List[Tuple[Decimal, Decimal]],
    depth_levels: int = 10
) -> Dict[str, Any]:
    """
    Calculate Order Book Imbalance (OBI) metrics.
    
    OBI = (bid_volume - ask_volume) / (bid_volume + ask_volume)
    Range: -1 (all sell pressure) to +1 (all buy pressure)
    
    Args:
        bids: List of (price, qty) tuples
        asks: List of (price, qty) tuples
        depth_levels: Number of levels to analyze
        
    Returns:
        Dict with OBI metrics at various depths
    """
    results = {}
    
    bid_vol_total = Decimal("0")
    ask_vol_total = Decimal("0")
    
    for i in range(min(depth_levels, len(bids), len(asks))):
        bid_vol_total += bids[i][1]
        ask_vol_total += asks[i][1]
        
        total_vol = bid_vol_total + ask_vol_total
        if total_vol > 0:
            obi = float((bid_vol_total - ask_vol_total) / total_vol)
        else:
            obi = 0.0
        
        results[f"obi_l{i+1}"] = round(obi, 4)
    
    # Summary metrics
    total = bid_vol_total + ask_vol_total
    results["bid_volume"] = float(bid_vol_total)
    results["ask_volume"] = float(ask_vol_total)
    results["obi_mean"] = round(float((bid_vol_total - ask_vol_total) / total) if total > 0 else 0, 4)
    
    # Calculate spread
    if bids and asks:
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_bps = float((spread / mid_price) * 10000) if mid_price > 0 else 0
        results["spread_bps"] = round(spread_bps, 2)
        results["mid_price"] = float(mid_price)
        results["best_bid"] = float(best_bid)
        results["best_ask"] = float(best_ask)
    
    return results


def analyze_trade_flow(
    trades: List[Dict[str, Any]],
    time_window_ms: int = 30000
) -> Dict[str, Any]:
    """
    Analyze trade flow to calculate consumption rates and imbalance.
    
    Args:
        trades: List of trade dicts with is_buyer_maker, price, qty, time
        time_window_ms: Time window in milliseconds
        
    Returns:
        Dict with buy/sell volumes and consumption rates
    """
    if not trades:
        return {
            "buy_aggressor_volume": 0.0,
            "sell_aggressor_volume": 0.0,
            "total_volume": 0.0,
            "buy_consumption_rate": 0.0,
            "sell_consumption_rate": 0.0,
            "trade_count": 0,
            "flow_imbalance": 0.0
        }
    
    now = int(time.time() * 1000)
    cutoff = now - time_window_ms
    
    buy_vol = Decimal("0")
    sell_vol = Decimal("0")
    trade_count = 0
    
    for t in trades:
        if t.get("time", 0) >= cutoff:
            qty = t.get("qty", Decimal("0"))
            # is_buyer_maker=True means sell aggressor (taker sells into bid)
            # is_buyer_maker=False means buy aggressor (taker buys from ask)
            if t.get("is_buyer_maker"):
                sell_vol += qty
            else:
                buy_vol += qty
            trade_count += 1
    
    total_vol = buy_vol + sell_vol
    time_secs = time_window_ms / 1000.0
    
    # Consumption rates (qty per second)
    buy_rate = float(buy_vol / Decimal(str(time_secs))) if time_secs > 0 else 0
    sell_rate = float(sell_vol / Decimal(str(time_secs))) if time_secs > 0 else 0
    
    # Flow imbalance: positive = more buy aggression, negative = more sell aggression
    flow_imbalance = 0.0
    if total_vol > 0:
        flow_imbalance = float((buy_vol - sell_vol) / total_vol)
    
    return {
        "buy_aggressor_volume": float(buy_vol),
        "sell_aggressor_volume": float(sell_vol),
        "total_volume": float(total_vol),
        "buy_consumption_rate": round(buy_rate, 6),
        "sell_consumption_rate": round(sell_rate, 6),
        "trade_count": trade_count,
        "flow_imbalance": round(flow_imbalance, 4)
    }


def calculate_queue_position(
    bids: List[Tuple[Decimal, Decimal]],
    asks: List[Tuple[Decimal, Decimal]],
    target_price: Decimal,
    side: str
) -> Dict[str, Any]:
    """
    Calculate estimated queue position for a limit order at target price.
    
    Args:
        bids: Orderbook bids
        asks: Orderbook asks
        target_price: Target limit order price
        side: Order side (BUY or SELL)
        
    Returns:
        Dict with queue position estimates
    """
    side = side.upper()
    
    if side == "BUY":
        # For buy limit order, look at bid side
        # Orders are in orderbook at prices >= target_price
        queue_qty = Decimal("0")
        levels_ahead = 0
        
        for price, qty in bids:
            if price >= target_price:
                queue_qty += qty
                levels_ahead += 1
            else:
                break
        
        return {
            "queue_qty": float(queue_qty),
            "levels_ahead": levels_ahead,
            "side": side,
            "target_price": float(target_price)
        }
    else:
        # For sell limit order, look at ask side
        # Orders are in orderbook at prices <= target_price
        queue_qty = Decimal("0")
        levels_ahead = 0
        
        for price, qty in asks:
            if price <= target_price:
                queue_qty += qty
                levels_ahead += 1
            else:
                break
        
        return {
            "queue_qty": float(queue_qty),
            "levels_ahead": levels_ahead,
            "side": side,
            "target_price": float(target_price)
        }


def detect_walls(
    bids: List[Tuple[Decimal, Decimal]],
    asks: List[Tuple[Decimal, Decimal]],
    depth_levels: int = 20,
    wall_threshold_multiplier: float = 3.0
) -> Dict[str, Any]:
    """
    Detect large walls in the orderbook.
    
    A wall is defined as a level with size > threshold_multiplier * average size.
    
    Args:
        bids: Orderbook bids
        asks: Orderbook asks
        depth_levels: Number of levels to analyze
        wall_threshold_multiplier: Multiplier for wall detection
        
    Returns:
        Dict with detected walls and risk assessment
    """
    bid_sizes = [float(q) for _, q in bids[:depth_levels]]
    ask_sizes = [float(q) for _, q in asks[:depth_levels]]
    
    avg_bid = statistics.mean(bid_sizes) if bid_sizes else 0
    avg_ask = statistics.mean(ask_sizes) if ask_sizes else 0
    
    bid_walls = []
    ask_walls = []
    
    threshold_bid = avg_bid * wall_threshold_multiplier
    threshold_ask = avg_ask * wall_threshold_multiplier
    
    for i, (price, qty) in enumerate(bids[:depth_levels]):
        if float(qty) > threshold_bid:
            bid_walls.append({
                "price": float(price),
                "qty": float(qty),
                "multiple": round(float(qty) / avg_bid if avg_bid > 0 else 0, 2),
                "level": i + 1
            })
    
    for i, (price, qty) in enumerate(asks[:depth_levels]):
        if float(qty) > threshold_ask:
            ask_walls.append({
                "price": float(price),
                "qty": float(qty),
                "multiple": round(float(qty) / avg_ask if avg_ask > 0 else 0, 2),
                "level": i + 1
            })
    
    # Wall risk level: 0-3
    # 0: No significant walls
    # 1: Some walls but not at critical levels
    # 2: Large walls at L1-L5
    # 3: Very large walls at L1-L3
    wall_risk = 0
    
    if bid_walls or ask_walls:
        wall_risk = 1
        
        # Check for walls at critical levels (L1-L5)
        critical_bid_walls = [w for w in bid_walls if w["level"] <= 5]
        critical_ask_walls = [w for w in ask_walls if w["level"] <= 5]
        
        if critical_bid_walls or critical_ask_walls:
            wall_risk = 2
            
            # Very large walls at L1-L3
            very_critical = [w for w in critical_bid_walls + critical_ask_walls 
                           if w["level"] <= 3 and w["multiple"] > 5]
            if very_critical:
                wall_risk = 3
    
    return {
        "bid_walls": bid_walls[:3],  # Top 3 only
        "ask_walls": ask_walls[:3],  # Top 3 only
        "wall_risk_level": wall_risk,
        "avg_bid_size": round(avg_bid, 4),
        "avg_ask_size": round(avg_ask, 4)
    }
