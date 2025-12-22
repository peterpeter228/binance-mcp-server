"""
Volume Profile Fallback from Trades Tool.

Provides volume profile analysis from raw trade data when the primary
volume_profile_levels tool hits rate limits or is unavailable.

Calculates:
- vPOC (Volume Point of Control)
- VAH/VAL (Value Area High/Low at 70%)
- HVN (High Volume Nodes)
- LVN (Low Volume Nodes)
- Magnet levels
- Avoid zones

Key Features:
- Uses aggTrades as data source
- Simplified but reliable VP calculation
- 45-second cache for identical parameters
- Exponential backoff on rate limits
"""

import time
import logging
import statistics
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from binance_mcp_server.futures_config import ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.tools.futures.market_data_collector import (
    get_market_data_collector,
    TradeRecord,
)
from binance_mcp_server.tools.futures.rate_limit_utils import (
    get_tool_cache,
    ParameterCache,
    make_api_call_with_backoff,
    RetryConfig,
)

logger = logging.getLogger(__name__)

# Cache with 45-second TTL (between the 30s and 60s range)
_vp_fallback_cache = get_tool_cache("volume_profile_fallback_from_trades", default_ttl=45.0)


@dataclass
class VPBin:
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


def calculate_bin_size(price_range: float, target_bins: int = 50, user_bin_size: Optional[float] = None) -> float:
    """
    Calculate appropriate bin size.
    
    Args:
        price_range: High - Low price range
        target_bins: Target number of bins
        user_bin_size: User-specified bin size (takes precedence)
        
    Returns:
        Bin size in price units
    """
    if user_bin_size and user_bin_size > 0:
        return user_bin_size
    
    if price_range <= 0:
        return 10.0  # Default fallback
    
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


def build_volume_profile_from_trades(
    trades: List[TradeRecord],
    bin_size: float
) -> List[VPBin]:
    """
    Build volume profile from trade data.
    
    Args:
        trades: List of trade records
        bin_size: Size of each price bin
        
    Returns:
        List of VPBin objects sorted by price
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
    bins: Dict[float, VPBin] = {}
    current = price_min
    while current < price_max:
        bins[current] = VPBin(
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


def find_vpoc(profile: List[VPBin]) -> Optional[float]:
    """Find Volume Point of Control (highest volume price)."""
    if not profile:
        return None
    
    max_bin = max(profile, key=lambda x: x.volume)
    return round(max_bin.price_mid, 2)


def find_value_area(profile: List[VPBin], percentage: float = 0.70) -> Tuple[Optional[float], Optional[float]]:
    """
    Find Value Area High and Low containing X% of volume.
    
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


def find_hvn_levels(profile: List[VPBin], max_levels: int = 3) -> List[float]:
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


def find_lvn_levels(profile: List[VPBin], max_levels: int = 3) -> List[float]:
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


def find_magnet_levels(
    profile: List[VPBin],
    vpoc: Optional[float],
    vah: Optional[float],
    val: Optional[float],
    max_levels: int = 4
) -> List[float]:
    """
    Find magnet levels (prices that attract price action).
    
    Combines HVN analysis with delta analysis.
    
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
    profile: List[VPBin],
    lvn_levels: List[float],
    max_zones: int = 3
) -> List[Dict[str, Any]]:
    """
    Find zones to avoid (low liquidity, fast price movement).
    
    Args:
        profile: Volume profile
        lvn_levels: LVN price levels
        max_zones: Maximum zones to return
        
    Returns:
        List of avoid zone dictionaries
    """
    zones = []
    
    # LVN areas are avoid zones
    for lvn in lvn_levels[:max_zones]:
        zones.append({
            "price": lvn,
            "reason": "LVN - quick price movement"
        })
    
    # Find single-print style zones (very low volume stretches)
    if profile:
        avg_vol = statistics.mean(b.volume for b in profile) if profile else 0
        
        current_zone = None
        for b in profile:
            is_thin = b.volume < avg_vol * 0.1
            
            if is_thin:
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
                            "high": round(current_zone["high"], 2),
                            "reason": "Thin liquidity zone"
                        })
                    current_zone = None
        
        if current_zone:
            zones.append({
                "low": round(current_zone["low"], 2),
                "high": round(current_zone["high"], 2),
                "reason": "Thin liquidity zone"
            })
    
    return zones[:max_zones]


def volume_profile_fallback_from_trades(
    symbol: str,
    lookback_minutes: int = 240,
    bin_size: Optional[float] = None,
    max_trades: int = 5000
) -> Dict[str, Any]:
    """
    Calculate volume profile from aggregated trades (fallback method).
    
    Use when volume_profile_levels_futures hits rate limits or is unavailable.
    Provides simplified but reliable VP levels.
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        lookback_minutes: Time window in minutes (max 360)
        bin_size: Price bin size (auto-calculated if None, default ~25)
        max_trades: Maximum trades to process (max 5000)
        
    Returns:
        Dictionary containing:
        - vPOC: Volume Point of Control
        - VAH/VAL: Value Area High/Low (70% volume)
        - HVN_levels: High Volume Nodes (<=3)
        - LVN_levels: Low Volume Nodes (<=3)
        - magnet_levels: Price magnets (<=4)
        - avoid_zones: Zones to avoid (<=3)
        - confidence_0_1: Confidence in results
        - notes: Summary notes
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
    lookback_minutes = min(max(lookback_minutes, 15), 360)
    max_trades = min(max(max_trades, 100), 5000)
    
    # Check cache
    cache_params = {
        "symbol": normalized_symbol,
        "lookback_minutes": lookback_minutes,
        "bin_size": bin_size,
        "max_trades": max_trades
    }
    cache_key = ParameterCache._hash_params(cache_params)
    hit, cached = _vp_fallback_cache.get(cache_key)
    if hit:
        cached["_cache_hit"] = True
        return cached
    
    # Get market data collector
    collector = get_market_data_collector()
    retry_config = RetryConfig(max_retries=3, base_delay_ms=1000)
    
    # Calculate time window
    lookback_seconds = lookback_minutes * 60
    start_time_ms = ts_ms - (lookback_seconds * 1000)
    
    # Fetch trades with retry
    success, trades_result, trades_error = make_api_call_with_backoff(
        lambda: collector.fetch_historical_trades(
            symbol=normalized_symbol,
            start_time_ms=start_time_ms,
            limit=min(max_trades, 1000)  # API limit per call
        ),
        retry_config,
        "fetch_historical_trades"
    )
    
    if not success:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "data_error", "message": trades_error or "Failed to fetch trades"}
        }
    
    trades: List[TradeRecord] = trades_result if trades_result else []
    
    quality_flags = []
    notes = []
    
    if len(trades) < 100:
        quality_flags.append("low_trade_count")
        notes.append("Limited trade data available")
    
    if not trades:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "data_error", "message": "No trades found in window"}
        }
    
    # Calculate price range
    prices = [t.price for t in trades]
    price_min = min(prices)
    price_max = max(prices)
    price_range = price_max - price_min
    
    # Calculate bin size
    effective_bin_size = calculate_bin_size(price_range, target_bins=50, user_bin_size=bin_size)
    
    # Build volume profile
    profile = build_volume_profile_from_trades(trades, effective_bin_size)
    
    if len(profile) < 5:
        quality_flags.append("insufficient_bins")
        notes.append("Limited price range for VP analysis")
    
    # Calculate key levels
    vpoc = find_vpoc(profile)
    vah, val = find_value_area(profile, percentage=0.70)
    hvn_levels = find_hvn_levels(profile, max_levels=3)
    lvn_levels = find_lvn_levels(profile, max_levels=3)
    magnet_levels = find_magnet_levels(profile, vpoc, vah, val, max_levels=4)
    avoid_zones = find_avoid_zones(profile, lvn_levels, max_zones=3)
    
    # Calculate actual time coverage
    if trades:
        actual_start = min(t.timestamp_ms for t in trades)
        actual_end = max(t.timestamp_ms for t in trades)
        actual_minutes = (actual_end - actual_start) / 60000
    else:
        actual_minutes = 0
    
    # Calculate confidence based on data quality
    confidence = 0.5
    if len(trades) >= 1000:
        confidence += 0.25
    elif len(trades) >= 500:
        confidence += 0.15
    elif len(trades) >= 200:
        confidence += 0.05
    
    if actual_minutes >= lookback_minutes * 0.8:
        confidence += 0.15
    elif actual_minutes >= lookback_minutes * 0.5:
        confidence += 0.05
    
    if len(profile) >= 20:
        confidence += 0.1
    
    confidence = round(min(1.0, confidence), 2)
    
    # Generate notes
    if vpoc:
        notes.append(f"POC at {vpoc}")
    if vah and val:
        notes.append(f"Value Area: {val}-{vah}")
    if len(hvn_levels) > 0:
        notes.append(f"{len(hvn_levels)} HVN level(s) identified")
    
    # Build response (compressed output - no large arrays)
    response = {
        "success": True,
        "ts_ms": ts_ms,
        "inputs": {
            "symbol": normalized_symbol,
            "lookback_minutes": lookback_minutes,
            "bin_size": effective_bin_size,
            "max_trades": max_trades
        },
        "data_quality": {
            "trade_count": len(trades),
            "actual_minutes": round(actual_minutes, 1),
            "bin_count": len(profile),
            "price_range": {
                "low": round(price_min, 2),
                "high": round(price_max, 2)
            }
        },
        "levels": {
            "vPOC": vpoc,
            "VAH": vah,
            "VAL": val,
            "HVN_levels": hvn_levels,
            "LVN_levels": lvn_levels,
            "magnet_levels": magnet_levels,
            "avoid_zones": avoid_zones
        },
        "confidence_0_1": confidence,
        "notes": notes[:4],
        "_cache_hit": False
    }
    
    if quality_flags:
        response["quality_flags"] = quality_flags
    
    # Cache the result
    _vp_fallback_cache.set(cache_key, response)
    
    return response
