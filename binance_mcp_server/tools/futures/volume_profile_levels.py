"""
Volume Profile Levels Analysis for Limit Order Optimization.

This tool calculates volume profile metrics to identify key price levels:
- VPOC (Volume Point of Control)
- VAH (Value Area High) / VAL (Value Area Low)
- HVN (High Volume Nodes) / LVN (Low Volume Nodes)
- Single Print Zones
- Magnet Levels and Avoid Zones

Output: JSON <= 2KB with key levels only (no histogram data).
"""

import time
import logging
import statistics
from typing import Dict, Any, Optional, List, Tuple
from decimal import Decimal
from collections import defaultdict

from binance_mcp_server.futures_config import ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import create_error_response, rate_limited, binance_rate_limiter
from binance_mcp_server.tools.futures.microstructure_data import (
    MicrostructureDataFetcher,
)

logger = logging.getLogger(__name__)


def _fetch_historical_trades(
    symbol: str,
    window_minutes: int = 240
) -> Dict[str, Any]:
    """
    Fetch historical aggregated trades for volume profile analysis.
    
    Uses multiple API calls if needed to cover the full window.
    
    Args:
        symbol: Trading symbol
        window_minutes: Window size in minutes (max 240 = 4 hours)
        
    Returns:
        Dict with trades list and metadata
    """
    from binance_mcp_server.futures_config import get_futures_client
    
    client = get_futures_client()
    
    end_time = int(time.time() * 1000)
    start_time = end_time - (window_minutes * 60 * 1000)
    
    all_trades = []
    current_end = end_time
    max_iterations = 10  # Safety limit
    iteration = 0
    
    while current_end > start_time and iteration < max_iterations:
        success, data = client.get(
            "/fapi/v1/aggTrades",
            {
                "symbol": symbol,
                "startTime": start_time,
                "endTime": current_end,
                "limit": 1000
            }
        )
        
        if not success or not data:
            break
        
        if not data:
            break
        
        # Add trades
        for t in data:
            trade_time = t.get("T", 0)
            if trade_time >= start_time:
                all_trades.append({
                    "price": Decimal(str(t.get("p", "0"))),
                    "qty": Decimal(str(t.get("q", "0"))),
                    "time": trade_time,
                    "is_buyer_maker": t.get("m", False)
                })
        
        # Move window for next iteration
        if data:
            oldest_time = min(t.get("T", current_end) for t in data)
            if oldest_time >= current_end:
                break  # No progress
            current_end = oldest_time - 1
        else:
            break
        
        iteration += 1
    
    # Sort by time
    all_trades.sort(key=lambda x: x["time"])
    
    return {
        "success": True,
        "trades": all_trades,
        "count": len(all_trades),
        "window_start": start_time,
        "window_end": end_time,
        "window_minutes": window_minutes
    }


def _calculate_optimal_bin_size(
    trades: List[Dict[str, Any]],
    default_bin: float = 5.0
) -> float:
    """
    Calculate optimal bin size based on price range and trade distribution.
    
    Args:
        trades: List of trades
        default_bin: Default bin size in USD
        
    Returns:
        Optimal bin size
    """
    if not trades:
        return default_bin
    
    prices = [float(t["price"]) for t in trades]
    
    if not prices:
        return default_bin
    
    price_range = max(prices) - min(prices)
    avg_price = statistics.mean(prices)
    
    # Target around 50-100 bins
    target_bins = 75
    
    # Calculate bin size
    if price_range > 0:
        calculated_bin = price_range / target_bins
        
        # Round to nice numbers
        if avg_price > 10000:  # BTC-like
            # Round to 5, 10, 25, 50, 100
            nice_bins = [5, 10, 25, 50, 100]
        else:  # ETH-like
            nice_bins = [1, 2, 5, 10, 25]
        
        # Find closest nice bin
        optimal = min(nice_bins, key=lambda x: abs(x - calculated_bin))
        return float(optimal)
    
    return default_bin


def _build_volume_profile(
    trades: List[Dict[str, Any]],
    bin_size: float
) -> Dict[str, Any]:
    """
    Build volume profile histogram from trades.
    
    Args:
        trades: List of trades with price and qty
        bin_size: Price bin size
        
    Returns:
        Dict with bins, total volume, and price range
    """
    if not trades:
        return {
            "bins": {},
            "total_volume": 0,
            "price_min": 0,
            "price_max": 0
        }
    
    volume_bins: Dict[float, Dict[str, float]] = defaultdict(
        lambda: {"volume": 0.0, "buy_volume": 0.0, "sell_volume": 0.0, "trade_count": 0}
    )
    
    total_volume = Decimal("0")
    prices = []
    
    for t in trades:
        price = float(t["price"])
        qty = float(t["qty"])
        
        # Calculate bin (floor to nearest bin_size)
        bin_price = (int(price / bin_size) * bin_size)
        
        volume_bins[bin_price]["volume"] += qty
        volume_bins[bin_price]["trade_count"] += 1
        
        if t.get("is_buyer_maker"):
            volume_bins[bin_price]["sell_volume"] += qty
        else:
            volume_bins[bin_price]["buy_volume"] += qty
        
        total_volume += t["qty"]
        prices.append(price)
    
    return {
        "bins": dict(volume_bins),
        "total_volume": float(total_volume),
        "price_min": min(prices) if prices else 0,
        "price_max": max(prices) if prices else 0,
        "bin_count": len(volume_bins)
    }


def _find_vpoc(bins: Dict[float, Dict[str, float]]) -> Optional[float]:
    """Find Volume Point of Control (highest volume bin)."""
    if not bins:
        return None
    
    return max(bins.keys(), key=lambda x: bins[x]["volume"])


def _find_value_area(
    bins: Dict[float, Dict[str, float]],
    total_volume: float,
    value_area_percent: float = 70.0
) -> Tuple[Optional[float], Optional[float]]:
    """
    Find Value Area High and Low (price range containing X% of volume).
    
    Standard value area is 70% of volume centered around VPOC.
    
    Args:
        bins: Volume bins
        total_volume: Total volume
        value_area_percent: Percentage for value area (default 70%)
        
    Returns:
        Tuple of (VAL, VAH)
    """
    if not bins or total_volume <= 0:
        return None, None
    
    vpoc = _find_vpoc(bins)
    if vpoc is None:
        return None, None
    
    target_volume = total_volume * (value_area_percent / 100.0)
    
    # Sort bins by price
    sorted_bins = sorted(bins.keys())
    vpoc_idx = sorted_bins.index(vpoc) if vpoc in sorted_bins else len(sorted_bins) // 2
    
    # Expand from VPOC
    val_idx = vpoc_idx
    vah_idx = vpoc_idx
    current_volume = bins[vpoc]["volume"]
    
    while current_volume < target_volume:
        # Try expanding lower
        expand_low = val_idx > 0
        expand_high = vah_idx < len(sorted_bins) - 1
        
        if not expand_low and not expand_high:
            break
        
        low_vol = bins[sorted_bins[val_idx - 1]]["volume"] if expand_low else 0
        high_vol = bins[sorted_bins[vah_idx + 1]]["volume"] if expand_high else 0
        
        # Expand in direction with more volume
        if expand_low and (not expand_high or low_vol >= high_vol):
            val_idx -= 1
            current_volume += low_vol
        elif expand_high:
            vah_idx += 1
            current_volume += high_vol
        else:
            break
    
    return sorted_bins[val_idx], sorted_bins[vah_idx]


def _find_volume_nodes(
    bins: Dict[float, Dict[str, float]],
    threshold_multiplier: float = 1.5
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Find High Volume Nodes (HVN) and Low Volume Nodes (LVN).
    
    HVN: Bins with volume > threshold_multiplier * average
    LVN: Bins with volume < 1/threshold_multiplier * average
    
    Args:
        bins: Volume bins
        threshold_multiplier: Multiplier for threshold calculation
        
    Returns:
        Tuple of (HVN list, LVN list)
    """
    if not bins:
        return [], []
    
    volumes = [b["volume"] for b in bins.values()]
    avg_volume = statistics.mean(volumes)
    
    hvn_threshold = avg_volume * threshold_multiplier
    lvn_threshold = avg_volume / threshold_multiplier
    
    hvn = []
    lvn = []
    
    sorted_bins = sorted(bins.keys())
    
    for price in sorted_bins:
        vol = bins[price]["volume"]
        
        if vol >= hvn_threshold:
            hvn.append({
                "price": price,
                "volume": round(vol, 4),
                "multiple": round(vol / avg_volume, 2)
            })
        elif vol <= lvn_threshold:
            lvn.append({
                "price": price,
                "volume": round(vol, 4),
                "multiple": round(vol / avg_volume, 2)
            })
    
    # Sort by volume (descending for HVN, ascending for LVN)
    hvn.sort(key=lambda x: x["volume"], reverse=True)
    lvn.sort(key=lambda x: x["volume"])
    
    return hvn[:3], lvn[:3]  # Top 3 each


def _find_single_prints(
    bins: Dict[float, Dict[str, float]],
    min_gap_bins: int = 2
) -> List[Dict[str, Any]]:
    """
    Find single print zones (price areas with very low volume surrounded by higher volume).
    
    These represent rapid price moves and potential support/resistance.
    
    Args:
        bins: Volume bins
        min_gap_bins: Minimum consecutive low-volume bins
        
    Returns:
        List of single print zones
    """
    if not bins:
        return []
    
    volumes = [b["volume"] for b in bins.values()]
    avg_volume = statistics.mean(volumes)
    threshold = avg_volume * 0.3  # Very low volume threshold
    
    sorted_bins = sorted(bins.keys())
    
    single_prints = []
    current_zone_start = None
    current_zone_prices = []
    
    for i, price in enumerate(sorted_bins):
        vol = bins[price]["volume"]
        
        if vol <= threshold:
            if current_zone_start is None:
                current_zone_start = price
            current_zone_prices.append(price)
        else:
            if current_zone_prices and len(current_zone_prices) >= min_gap_bins:
                single_prints.append({
                    "low": current_zone_prices[0],
                    "high": current_zone_prices[-1],
                    "bins": len(current_zone_prices)
                })
            current_zone_start = None
            current_zone_prices = []
    
    # Check last zone
    if current_zone_prices and len(current_zone_prices) >= min_gap_bins:
        single_prints.append({
            "low": current_zone_prices[0],
            "high": current_zone_prices[-1],
            "bins": len(current_zone_prices)
        })
    
    return single_prints[:2]  # Top 2 only


def _identify_magnet_levels(
    bins: Dict[float, Dict[str, float]],
    hvn: List[Dict[str, Any]],
    vpoc: Optional[float],
    current_price: float
) -> List[Dict[str, Any]]:
    """
    Identify price levels that act as "magnets" (price tends to gravitate towards).
    
    Magnets are typically:
    - VPOC
    - Strong HVNs near current price
    - Previous session POCs
    
    Args:
        bins: Volume bins
        hvn: High volume nodes
        vpoc: Volume Point of Control
        current_price: Current market price
        
    Returns:
        List of magnet levels with distance from current price
    """
    magnets = []
    
    # VPOC is always a magnet
    if vpoc is not None:
        distance_bps = abs(vpoc - current_price) / current_price * 10000 if current_price > 0 else 0
        magnets.append({
            "price": vpoc,
            "type": "VPOC",
            "distance_bps": round(distance_bps, 1),
            "strength": "strong"
        })
    
    # Add HVNs as magnets
    for node in hvn[:2]:  # Top 2 HVNs
        price = node["price"]
        if price != vpoc:  # Avoid duplicate with VPOC
            distance_bps = abs(price - current_price) / current_price * 10000 if current_price > 0 else 0
            strength = "strong" if node["multiple"] > 2.0 else "moderate"
            magnets.append({
                "price": price,
                "type": "HVN",
                "distance_bps": round(distance_bps, 1),
                "strength": strength
            })
    
    # Sort by distance from current price
    magnets.sort(key=lambda x: x["distance_bps"])
    
    return magnets[:3]


def _identify_avoid_zones(
    lvn: List[Dict[str, Any]],
    single_prints: List[Dict[str, Any]],
    current_price: float
) -> List[Dict[str, Any]]:
    """
    Identify zones to avoid for limit orders (low fill probability areas).
    
    Avoid zones are:
    - LVNs (price moves quickly through these)
    - Single print zones
    
    Args:
        lvn: Low volume nodes
        single_prints: Single print zones
        current_price: Current market price
        
    Returns:
        List of avoid zones
    """
    avoid = []
    
    # Add LVNs
    for node in lvn[:2]:
        price = node["price"]
        distance_bps = abs(price - current_price) / current_price * 10000 if current_price > 0 else 0
        avoid.append({
            "price_low": price,
            "price_high": price,
            "type": "LVN",
            "distance_bps": round(distance_bps, 1),
            "reason": "Low volume - rapid price movement area"
        })
    
    # Add single prints
    for zone in single_prints:
        mid_price = (zone["low"] + zone["high"]) / 2
        distance_bps = abs(mid_price - current_price) / current_price * 10000 if current_price > 0 else 0
        avoid.append({
            "price_low": zone["low"],
            "price_high": zone["high"],
            "type": "SINGLE_PRINT",
            "distance_bps": round(distance_bps, 1),
            "reason": "Single print zone - unstable price area"
        })
    
    # Sort by distance
    avoid.sort(key=lambda x: x["distance_bps"])
    
    return avoid[:2]


@rate_limited(binance_rate_limiter)
def volume_profile_levels(
    symbol: str,
    window_minutes: int = 240,
    bin_size: Optional[float] = None
) -> Dict[str, Any]:
    """
    Calculate volume profile levels for limit order optimization.
    
    Analyzes trade distribution to identify:
    - VPOC: Price with most volume (strong support/resistance)
    - VAH/VAL: Value area boundaries (70% of volume)
    - HVN: High volume nodes (accumulation zones)
    - LVN: Low volume nodes (rapid movement zones)
    - Single prints: Gap zones from rapid moves
    - Magnet levels: Where price tends to gravitate
    - Avoid zones: Poor fill probability areas
    
    Args:
        symbol: Trading pair (BTCUSDT or ETHUSDT)
        window_minutes: Analysis window (15-240 minutes)
        bin_size: Price bin size in USD (auto if None)
        
    Returns:
        Dict with key volume profile levels
        
    Example Response:
        {
            "ts_ms": 1234567890123,
            "window": {"start_ms": ..., "end_ms": ..., "minutes": 240},
            "levels": {
                "vpoc": 50125.0,
                "vah": 50450.0,
                "val": 49800.0,
                "hvn": [{"price": 50100.0, "volume": 125.5, "multiple": 2.3}, ...],
                "lvn": [{"price": 49950.0, "volume": 8.2, "multiple": 0.15}, ...],
                "single_print_zones": [{"low": 50200.0, "high": 50250.0, "bins": 3}],
                "magnet_levels": [{"price": 50125.0, "type": "VPOC", "distance_bps": 25.0}],
                "avoid_zones": [{"price_low": 49950.0, "price_high": 49950.0, "type": "LVN"}]
            },
            "quality_flags": []
        }
    """
    ts_ms = int(time.time() * 1000)
    
    # Input validation
    is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
    if not is_valid:
        return create_error_response("validation_error", error)
    
    window_minutes = max(15, min(240, window_minutes))
    
    quality_flags = []
    
    try:
        # Fetch historical trades
        trades_data = _fetch_historical_trades(normalized_symbol, window_minutes)
        trades = trades_data.get("trades", [])
        
        if len(trades) < 100:
            quality_flags.append("LOW_SAMPLE_SIZE")
        
        if len(trades) < 10:
            return {
                "ts_ms": ts_ms,
                "window": {
                    "start_ms": trades_data.get("window_start"),
                    "end_ms": trades_data.get("window_end"),
                    "minutes": window_minutes
                },
                "levels": {
                    "vpoc": None,
                    "vah": None,
                    "val": None,
                    "hvn": [],
                    "lvn": [],
                    "single_print_zones": [],
                    "magnet_levels": [],
                    "avoid_zones": []
                },
                "quality_flags": ["INSUFFICIENT_DATA"]
            }
        
        # Calculate optimal bin size if not provided
        if bin_size is None:
            bin_size = _calculate_optimal_bin_size(trades)
        else:
            bin_size = max(0.1, float(bin_size))
        
        # Build volume profile
        profile = _build_volume_profile(trades, bin_size)
        bins = profile["bins"]
        total_volume = profile["total_volume"]
        
        if not bins:
            quality_flags.append("NO_PROFILE_DATA")
            return {
                "ts_ms": ts_ms,
                "window": {
                    "start_ms": trades_data.get("window_start"),
                    "end_ms": trades_data.get("window_end"),
                    "minutes": window_minutes
                },
                "levels": {
                    "vpoc": None, "vah": None, "val": None,
                    "hvn": [], "lvn": [], "single_print_zones": [],
                    "magnet_levels": [], "avoid_zones": []
                },
                "quality_flags": quality_flags
            }
        
        # Calculate key levels
        vpoc = _find_vpoc(bins)
        val, vah = _find_value_area(bins, total_volume, 70.0)
        hvn, lvn = _find_volume_nodes(bins, threshold_multiplier=1.5)
        single_prints = _find_single_prints(bins, min_gap_bins=2)
        
        # Get current price for magnet/avoid calculations
        fetcher = MicrostructureDataFetcher(normalized_symbol)
        mark_data = fetcher.fetch_mark_price()
        current_price = float(mark_data.get("markPrice", vpoc or 0))
        
        magnet_levels = _identify_magnet_levels(bins, hvn, vpoc, current_price)
        avoid_zones = _identify_avoid_zones(lvn, single_prints, current_price)
        
        return {
            "ts_ms": ts_ms,
            "window": {
                "start_ms": trades_data.get("window_start"),
                "end_ms": trades_data.get("window_end"),
                "minutes": window_minutes,
                "trade_count": len(trades),
                "bin_size": bin_size,
                "bin_count": profile["bin_count"]
            },
            "levels": {
                "vpoc": vpoc,
                "vah": vah,
                "val": val,
                "hvn": hvn,
                "lvn": lvn,
                "single_print_zones": single_prints,
                "magnet_levels": magnet_levels,
                "avoid_zones": avoid_zones
            },
            "quality_flags": quality_flags
        }
        
    except Exception as e:
        logger.error(f"Error in volume_profile_levels: {e}")
        return create_error_response("tool_error", f"Volume profile analysis failed: {str(e)}")
