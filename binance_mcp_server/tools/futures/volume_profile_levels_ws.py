"""
WebSocket-based Volume Profile Levels Tool.

Calculates volume profile levels from real-time WebSocket trade data
without making any REST API calls. Uses local ring buffers populated
by the WebSocket aggTrade stream.

Key Features:
- Zero REST API calls - all data from local buffer
- Compatible output with volume_profile_levels_futures
- 30-second cache for identical parameters
- Auto-subscribe to WebSocket stream if not already subscribed
- Returns quality_flags if buffer data is insufficient

Output Structure (compressed):
- VAH, VAL, vPOC (tPOC)
- HVN_levels (max 3)
- LVN_levels (max 3)
- single_print_zones (max 3)
- magnet_levels (max 6)
- avoid_zones (max 3)
"""

import time
import logging
import statistics
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from collections import defaultdict

from binance_mcp_server.futures_config import ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.tools.futures.ws_trade_buffer import (
    get_ws_trade_buffer_manager,
    WSTradeRecord,
)
from binance_mcp_server.tools.futures.rate_limit_utils import (
    get_tool_cache,
    ParameterCache,
)

logger = logging.getLogger(__name__)

# Cache with 30-second TTL
_vp_ws_cache = get_tool_cache("volume_profile_levels_futures_ws", default_ttl=30.0)


@dataclass
class VPWSBin:
    """A single bin in the volume profile."""
    price_low: float
    price_high: float
    price_mid: float
    volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    trade_count: int = 0
    
    @property
    def delta(self) -> float:
        """Buy minus sell volume."""
        return self.buy_volume - self.sell_volume
    
    @property
    def delta_pct(self) -> float:
        """Delta as percentage of total."""
        if self.volume == 0:
            return 0.0
        return (self.delta / self.volume) * 100


def calculate_dynamic_bin_size(price_range: float, target_bins: int = 50) -> float:
    """
    Calculate appropriate bin size based on price range.
    
    Args:
        price_range: High - Low price range
        target_bins: Target number of bins
        
    Returns:
        Bin size in price units
    """
    if price_range <= 0:
        return 10.0
    
    raw_size = price_range / target_bins
    
    # Round to nice numbers
    if raw_size >= 100:
        return round(raw_size / 50) * 50
    elif raw_size >= 10:
        return round(raw_size / 5) * 5
    elif raw_size >= 1:
        return round(raw_size)
    elif raw_size >= 0.1:
        return round(raw_size, 1)
    else:
        return round(raw_size, 2)


def build_volume_profile(
    trades: List[WSTradeRecord],
    bin_size: float
) -> List[VPWSBin]:
    """
    Build volume profile from WebSocket trade records.
    
    Args:
        trades: List of WSTradeRecord objects
        bin_size: Size of each price bin
        
    Returns:
        List of VPWSBin objects sorted by price
    """
    if not trades:
        return []
    
    # Determine price range
    prices = [t.price for t in trades]
    price_min = min(prices)
    price_max = max(prices)
    
    # Snap to bin boundaries
    price_min = (price_min // bin_size) * bin_size
    price_max = ((price_max // bin_size) + 1) * bin_size
    
    # Initialize bins
    bins: Dict[float, VPWSBin] = {}
    current = price_min
    while current < price_max:
        bins[current] = VPWSBin(
            price_low=current,
            price_high=current + bin_size,
            price_mid=current + bin_size / 2
        )
        current += bin_size
    
    # Aggregate trades into bins
    for trade in trades:
        bin_key = (trade.price // bin_size) * bin_size
        if bin_key in bins:
            bins[bin_key].volume += trade.qty
            bins[bin_key].trade_count += 1
            if trade.side == "buy":
                bins[bin_key].buy_volume += trade.qty
            else:
                bins[bin_key].sell_volume += trade.qty
    
    # Convert to sorted list
    profile = list(bins.values())
    profile.sort(key=lambda x: x.price_mid)
    
    return profile


def find_vpoc(profile: List[VPWSBin]) -> Optional[float]:
    """Find Volume Point of Control (highest volume price)."""
    if not profile:
        return None
    
    max_bin = max(profile, key=lambda x: x.volume)
    return round(max_bin.price_mid, 2)


def find_value_area(profile: List[VPWSBin], percentage: float = 0.70) -> Tuple[Optional[float], Optional[float]]:
    """
    Find Value Area High and Low containing specified percentage of volume.
    
    Uses TPO method: start from POC and expand outward.
    
    Args:
        profile: Volume profile bins
        percentage: Percentage of volume to include (0.70 = 70%)
        
    Returns:
        Tuple of (VAH, VAL)
    """
    if not profile:
        return None, None
    
    total_volume = sum(b.volume for b in profile)
    if total_volume == 0:
        return None, None
    
    target_volume = total_volume * percentage
    
    # Find POC index
    poc_idx = max(range(len(profile)), key=lambda i: profile[i].volume)
    
    # Expand from POC
    low_idx = poc_idx
    high_idx = poc_idx
    current_volume = profile[poc_idx].volume
    
    while current_volume < target_volume:
        can_expand_low = low_idx > 0
        can_expand_high = high_idx < len(profile) - 1
        
        if not can_expand_low and not can_expand_high:
            break
        
        # Get volumes for potential expansion
        low_vol = profile[low_idx - 1].volume if can_expand_low else 0
        high_vol = profile[high_idx + 1].volume if can_expand_high else 0
        
        # Expand toward higher volume
        if low_vol >= high_vol and can_expand_low:
            low_idx -= 1
            current_volume += profile[low_idx].volume
        elif can_expand_high:
            high_idx += 1
            current_volume += profile[high_idx].volume
        elif can_expand_low:
            low_idx -= 1
            current_volume += profile[low_idx].volume
    
    val = round(profile[low_idx].price_low, 2)
    vah = round(profile[high_idx].price_high, 2)
    
    return vah, val


def find_hvn_levels(profile: List[VPWSBin], max_levels: int = 3) -> List[float]:
    """
    Find High Volume Nodes (above 75th percentile).
    
    Args:
        profile: Volume profile bins
        max_levels: Maximum levels to return
        
    Returns:
        List of HVN prices
    """
    if not profile or len(profile) < 3:
        return []
    
    volumes = [b.volume for b in profile if b.volume > 0]
    if not volumes:
        return []
    
    # Find 75th percentile threshold
    sorted_vols = sorted(volumes)
    threshold_idx = int(len(sorted_vols) * 0.75)
    threshold = sorted_vols[min(threshold_idx, len(sorted_vols) - 1)]
    
    # Find bins above threshold
    hvn_bins = [b for b in profile if b.volume >= threshold]
    hvn_bins.sort(key=lambda x: x.volume, reverse=True)
    
    return [round(b.price_mid, 2) for b in hvn_bins[:max_levels]]


def find_lvn_levels(profile: List[VPWSBin], max_levels: int = 3) -> List[float]:
    """
    Find Low Volume Nodes (below 25th percentile).
    
    Args:
        profile: Volume profile bins
        max_levels: Maximum levels to return
        
    Returns:
        List of LVN prices
    """
    if not profile or len(profile) < 3:
        return []
    
    volumes = [b.volume for b in profile if b.volume > 0]
    if not volumes:
        return []
    
    # Find 25th percentile threshold
    sorted_vols = sorted(volumes)
    threshold_idx = int(len(sorted_vols) * 0.25)
    threshold = sorted_vols[threshold_idx]
    
    # Find bins below threshold (but with some volume)
    lvn_bins = [b for b in profile if 0 < b.volume <= threshold]
    lvn_bins.sort(key=lambda x: x.volume)
    
    return [round(b.price_mid, 2) for b in lvn_bins[:max_levels]]


def find_single_print_zones(profile: List[VPWSBin], max_zones: int = 3) -> List[Dict[str, float]]:
    """
    Find single print zones (gaps in volume distribution).
    
    Args:
        profile: Volume profile bins
        max_zones: Maximum zones to return
        
    Returns:
        List of zone dictionaries with 'low' and 'high' prices
    """
    if not profile or len(profile) < 5:
        return []
    
    avg_volume = statistics.mean(b.volume for b in profile)
    zones = []
    current_zone = None
    
    for i, b in enumerate(profile):
        is_single_print = b.volume < avg_volume * 0.1  # Less than 10% of average
        
        if is_single_print:
            if current_zone is None:
                current_zone = {"low": b.price_low, "high": b.price_high}
            else:
                current_zone["high"] = b.price_high
        else:
            if current_zone is not None:
                zone_size = current_zone["high"] - current_zone["low"]
                if zone_size > 0:
                    zones.append({
                        "low": round(current_zone["low"], 2),
                        "high": round(current_zone["high"], 2)
                    })
                current_zone = None
    
    # Handle zone at end
    if current_zone is not None:
        zones.append({
            "low": round(current_zone["low"], 2),
            "high": round(current_zone["high"], 2)
        })
    
    # Sort by zone size and return top N
    zones.sort(key=lambda z: z["high"] - z["low"], reverse=True)
    return zones[:max_zones]


def find_magnet_levels(
    profile: List[VPWSBin],
    vpoc: Optional[float],
    vah: Optional[float],
    val: Optional[float],
    max_levels: int = 6
) -> List[float]:
    """
    Find magnet levels (prices that attract price action).
    
    Args:
        profile: Volume profile
        vpoc: Volume POC
        vah: Value Area High
        val: Value Area Low
        max_levels: Maximum levels to return
        
    Returns:
        List of magnet level prices
    """
    magnets = set()
    
    # POC is always a magnet
    if vpoc:
        magnets.add(vpoc)
    
    # Value area boundaries
    if vah:
        magnets.add(vah)
    if val:
        magnets.add(val)
    
    # Find bins with strong delta (institutional activity)
    if profile:
        avg_vol = statistics.mean(b.volume for b in profile if b.volume > 0) if profile else 0
        for b in profile:
            if b.volume >= avg_vol * 1.5 and abs(b.delta_pct) > 25:
                magnets.add(round(b.price_mid, 2))
    
    return sorted(list(magnets))[:max_levels]


def find_avoid_zones(
    profile: List[VPWSBin],
    lvn_levels: List[float],
    single_prints: List[Dict[str, float]],
    max_zones: int = 3
) -> List[Dict[str, Any]]:
    """
    Find zones to avoid (low liquidity, fast price movement).
    
    Args:
        profile: Volume profile
        lvn_levels: LVN price levels
        single_prints: Single print zones
        max_zones: Maximum zones to return
        
    Returns:
        List of avoid zone dictionaries
    """
    zones = []
    
    # LVN areas are avoid zones
    for lvn in lvn_levels[:2]:
        zones.append({
            "price": lvn,
            "reason": "LVN - quick price movement"
        })
    
    # Single print zones
    for sp in single_prints[:1]:
        zones.append({
            "low": sp["low"],
            "high": sp["high"],
            "reason": "Single print - thin liquidity"
        })
    
    return zones[:max_zones]


def volume_profile_levels_futures_ws(
    symbol: str,
    window_minutes: int = 240,
    bin_size: Optional[float] = None
) -> Dict[str, Any]:
    """
    Calculate volume profile levels from WebSocket trade buffer.
    
    Uses locally buffered aggTrade data from WebSocket stream.
    NO REST API calls are made by this tool.
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        window_minutes: Time window in minutes (max 360, depends on buffer)
        bin_size: Price bin size (auto-calculated if None)
        
    Returns:
        Dictionary containing:
        - vPOC: Volume Point of Control (tPOC)
        - VAH/VAL: Value Area High/Low (70% volume)
        - HVN_levels: High Volume Nodes (<=3)
        - LVN_levels: Low Volume Nodes (<=3)
        - single_print_zones: Gaps in volume (<=3)
        - magnet_levels: Price magnets (<=6)
        - avoid_zones: Zones to avoid (<=3)
        - quality_flags: Data quality indicators
        - ws_stats: WebSocket buffer statistics
    """
    ts_ms = int(time.time() * 1000)
    
    # Input validation
    is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
    if not is_valid:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "validation_error", "message": error}
        }
    
    # Constrain parameters
    window_minutes = min(max(window_minutes, 5), 360)
    
    # Check cache first
    cache_params = {
        "symbol": normalized_symbol,
        "window_minutes": window_minutes,
        "bin_size": bin_size
    }
    cache_key = ParameterCache._hash_params(cache_params)
    hit, cached = _vp_ws_cache.get(cache_key)
    if hit:
        cached["_cache_hit"] = True
        return cached
    
    # Get WebSocket buffer manager
    manager = get_ws_trade_buffer_manager()
    
    # Ensure we're subscribed to this symbol
    if normalized_symbol not in manager.get_subscribed_symbols():
        manager.subscribe(normalized_symbol)
        # Give it a moment to connect if needed
        manager.wait_for_connection(timeout=5.0)
    
    # Get buffer statistics
    ws_stats = manager.get_buffer_stats(normalized_symbol)
    quality_flags = []
    notes = []
    
    # Check if WebSocket is connected
    if not ws_stats.get("is_connected", False):
        quality_flags.append("ws_disconnected")
        notes.append("WebSocket not connected - attempting reconnect")
    
    # Get trades from buffer
    trades = manager.get_trades(normalized_symbol, window_minutes)
    
    # Check for insufficient data
    if len(trades) < 100:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "data_error", "message": "Insufficient trade data in buffer"},
            "quality_flags": ["insufficient_trade_data"],
            "ws_stats": ws_stats,
            "notes": [
                f"Buffer has {len(trades)} trades, need at least 100",
                "WebSocket may need more time to collect data"
            ]
        }
    
    if len(trades) < 500:
        quality_flags.append("low_trade_count")
    
    # Calculate price range
    prices = [t.price for t in trades]
    price_min = min(prices)
    price_max = max(prices)
    price_range = price_max - price_min
    
    # Calculate bin size
    effective_bin_size = bin_size if bin_size else calculate_dynamic_bin_size(price_range)
    
    # Build volume profile
    profile = build_volume_profile(trades, effective_bin_size)
    
    if len(profile) < 5:
        quality_flags.append("insufficient_bins")
    
    # Calculate key levels
    vpoc = find_vpoc(profile)
    vah, val = find_value_area(profile, percentage=0.70)
    hvn_levels = find_hvn_levels(profile, max_levels=3)
    lvn_levels = find_lvn_levels(profile, max_levels=3)
    single_print_zones = find_single_print_zones(profile, max_zones=3)
    magnet_levels = find_magnet_levels(profile, vpoc, vah, val, max_levels=6)
    avoid_zones = find_avoid_zones(profile, lvn_levels, single_print_zones, max_zones=3)
    
    # Calculate actual time coverage
    if trades:
        actual_start = min(t.timestamp_ms for t in trades)
        actual_end = max(t.timestamp_ms for t in trades)
        actual_minutes = (actual_end - actual_start) / 60000
    else:
        actual_minutes = 0
    
    # Check if we have enough time coverage
    if actual_minutes < window_minutes * 0.5:
        quality_flags.append("incomplete_window")
        notes.append(f"Only {actual_minutes:.1f} min of {window_minutes} min requested")
    
    # Generate notes
    if vpoc:
        notes.append(f"tPOC at {vpoc}")
    if vah and val:
        notes.append(f"Value Area: {val}-{vah}")
    
    # Calculate confidence based on data quality
    confidence = 0.5
    if len(trades) >= 2000:
        confidence += 0.25
    elif len(trades) >= 1000:
        confidence += 0.15
    elif len(trades) >= 500:
        confidence += 0.05
    
    if actual_minutes >= window_minutes * 0.8:
        confidence += 0.15
    elif actual_minutes >= window_minutes * 0.5:
        confidence += 0.05
    
    if ws_stats.get("is_connected"):
        confidence += 0.1
    
    confidence = round(min(1.0, confidence), 2)
    
    # Build response (compatible with volume_profile_levels_futures)
    response = {
        "success": True,
        "ts_ms": ts_ms,
        "inputs": {
            "symbol": normalized_symbol,
            "window_minutes": window_minutes,
            "bin_size": effective_bin_size
        },
        "window": {
            "requested_minutes": window_minutes,
            "actual_minutes": round(actual_minutes, 1),
            "trade_count": len(trades),
            "bin_size": effective_bin_size,
            "price_range": {
                "low": round(price_min, 2),
                "high": round(price_max, 2)
            }
        },
        "levels": {
            "vpoc": vpoc,  # tPOC from trades
            "vah": vah,
            "val": val,
            "hvn": hvn_levels,
            "lvn": lvn_levels,
            "single_print_zones": single_print_zones,
            "magnet_levels": magnet_levels,
            "avoid_zones": avoid_zones
        },
        "confidence_0_1": confidence,
        "ws_stats": {
            "is_connected": ws_stats.get("is_connected", False),
            "buffer_trade_count": ws_stats.get("trade_count", 0),
            "buffer_duration_minutes": ws_stats.get("buffer_duration_minutes", 0)
        },
        "_cache_hit": False
    }
    
    if quality_flags:
        response["quality_flags"] = quality_flags
    
    if notes:
        response["notes"] = notes[:4]
    
    # Cache the result
    _vp_ws_cache.set(cache_key, response)
    
    return response


def get_ws_buffer_status(symbol: Optional[str] = None) -> Dict[str, Any]:
    """
    Get WebSocket buffer status (utility function).
    
    Args:
        symbol: Optional symbol to get specific stats for
        
    Returns:
        Buffer status information
    """
    manager = get_ws_trade_buffer_manager()
    
    result = {
        "is_connected": manager.is_connected(),
        "subscribed_symbols": manager.get_subscribed_symbols()
    }
    
    if symbol:
        symbol = symbol.upper()
        result["symbol_stats"] = manager.get_buffer_stats(symbol)
    
    return result
