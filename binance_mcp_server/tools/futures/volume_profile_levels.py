"""
Volume Profile Levels Tool for Market Structure Analysis.

Calculates key volume profile levels from aggregated trade data:
- VPOC (Volume Point of Control)
- VAH/VAL (Value Area High/Low)
- HVN (High Volume Nodes)
- LVN (Low Volume Nodes)
- Single Print Zones
- Magnet Levels (key support/resistance)
- Avoid Zones (thin liquidity areas)

Uses aggTrades data to build volume-by-price distribution.
"""

import time
import logging
import statistics
from typing import Dict, Any, Optional, List, Tuple
from collections import defaultdict
from dataclasses import dataclass
from binance_mcp_server.futures_config import ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.tools.futures.market_data_collector import (
    get_market_data_collector,
    TradeRecord,
)

logger = logging.getLogger(__name__)


@dataclass
class VolumeProfileBin:
    """Represents a single price bin in the volume profile."""
    price_low: float
    price_high: float
    price_mid: float
    volume: float
    buy_volume: float
    sell_volume: float
    trade_count: int
    
    @property
    def delta(self) -> float:
        """Buy volume minus sell volume."""
        return self.buy_volume - self.sell_volume
    
    @property
    def delta_pct(self) -> float:
        """Delta as percentage of total volume."""
        if self.volume == 0:
            return 0.0
        return self.delta / self.volume * 100


def calculate_dynamic_bin_size(
    price_range: float,
    target_bins: int = 50
) -> float:
    """
    Calculate appropriate bin size based on price range.
    
    Args:
        price_range: High - Low price range
        target_bins: Target number of bins
        
    Returns:
        Bin size in price units
    """
    if price_range <= 0:
        return 5.0
    
    raw_size = price_range / target_bins
    
    # Round to nice numbers
    if raw_size >= 100:
        return round(raw_size / 50) * 50
    elif raw_size >= 10:
        return round(raw_size / 5) * 5
    elif raw_size >= 1:
        return round(raw_size)
    else:
        return round(raw_size, 1)


def build_volume_profile(
    trades: List[TradeRecord],
    bin_size: float,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None
) -> List[VolumeProfileBin]:
    """
    Build volume profile from trades.
    
    Args:
        trades: List of trade records
        bin_size: Size of each price bin
        price_min: Optional minimum price (auto-detect if None)
        price_max: Optional maximum price (auto-detect if None)
        
    Returns:
        List of VolumeProfileBin objects sorted by price
    """
    if not trades:
        return []
    
    # Determine price range
    prices = [t.price for t in trades]
    if price_min is None:
        price_min = min(prices)
    if price_max is None:
        price_max = max(prices)
    
    # Snap to bin boundaries
    price_min = (price_min // bin_size) * bin_size
    price_max = ((price_max // bin_size) + 1) * bin_size
    
    # Initialize bins
    bins: Dict[float, Dict] = {}
    
    current_price = price_min
    while current_price < price_max:
        bin_key = current_price
        bins[bin_key] = {
            "price_low": current_price,
            "price_high": current_price + bin_size,
            "price_mid": current_price + bin_size / 2,
            "volume": 0.0,
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "trade_count": 0
        }
        current_price += bin_size
    
    # Aggregate trades into bins
    for trade in trades:
        bin_key = (trade.price // bin_size) * bin_size
        
        if bin_key not in bins:
            continue
        
        bins[bin_key]["volume"] += trade.qty
        bins[bin_key]["trade_count"] += 1
        
        if trade.side == "buy":
            bins[bin_key]["buy_volume"] += trade.qty
        else:
            bins[bin_key]["sell_volume"] += trade.qty
    
    # Convert to VolumeProfileBin objects
    profile = [
        VolumeProfileBin(
            price_low=data["price_low"],
            price_high=data["price_high"],
            price_mid=data["price_mid"],
            volume=data["volume"],
            buy_volume=data["buy_volume"],
            sell_volume=data["sell_volume"],
            trade_count=data["trade_count"]
        )
        for data in bins.values()
    ]
    
    # Sort by price
    profile.sort(key=lambda x: x.price_mid)
    
    return profile


def find_vpoc(profile: List[VolumeProfileBin]) -> Optional[float]:
    """
    Find Volume Point of Control (highest volume price).
    
    Args:
        profile: Volume profile bins
        
    Returns:
        VPOC price or None
    """
    if not profile:
        return None
    
    max_bin = max(profile, key=lambda x: x.volume)
    return max_bin.price_mid


def find_value_area(
    profile: List[VolumeProfileBin],
    percentage: float = 0.70
) -> Tuple[Optional[float], Optional[float]]:
    """
    Find Value Area High and Low (containing X% of volume).
    
    Uses the TPO method: start from VPOC and expand outward.
    
    Args:
        profile: Volume profile bins
        percentage: Percentage of volume to include (default 70%)
        
    Returns:
        Tuple of (VAH, VAL) or (None, None)
    """
    if not profile:
        return None, None
    
    total_volume = sum(b.volume for b in profile)
    if total_volume == 0:
        return None, None
    
    target_volume = total_volume * percentage
    
    # Find VPOC index
    vpoc_idx = max(range(len(profile)), key=lambda i: profile[i].volume)
    
    # Expand from VPOC
    low_idx = vpoc_idx
    high_idx = vpoc_idx
    current_volume = profile[vpoc_idx].volume
    
    while current_volume < target_volume:
        # Check which side to expand
        expand_low = low_idx > 0
        expand_high = high_idx < len(profile) - 1
        
        if not expand_low and not expand_high:
            break
        
        # Get volumes for next expansion
        low_vol = profile[low_idx - 1].volume if expand_low else 0
        high_vol = profile[high_idx + 1].volume if expand_high else 0
        
        # Expand toward higher volume
        if low_vol >= high_vol and expand_low:
            low_idx -= 1
            current_volume += profile[low_idx].volume
        elif expand_high:
            high_idx += 1
            current_volume += profile[high_idx].volume
        elif expand_low:
            low_idx -= 1
            current_volume += profile[low_idx].volume
    
    val = profile[low_idx].price_low
    vah = profile[high_idx].price_high
    
    return vah, val


def find_hvn(
    profile: List[VolumeProfileBin],
    top_n: int = 3,
    min_percentile: float = 75
) -> List[float]:
    """
    Find High Volume Nodes.
    
    HVNs are price levels with significantly above-average volume.
    
    Args:
        profile: Volume profile bins
        top_n: Maximum number of HVNs to return
        min_percentile: Minimum volume percentile threshold
        
    Returns:
        List of HVN price levels
    """
    if not profile or len(profile) < 3:
        return []
    
    volumes = [b.volume for b in profile if b.volume > 0]
    if not volumes:
        return []
    
    # Calculate threshold
    sorted_volumes = sorted(volumes)
    threshold_idx = int(len(sorted_volumes) * min_percentile / 100)
    threshold = sorted_volumes[min(threshold_idx, len(sorted_volumes) - 1)]
    
    # Find bins above threshold
    hvn_bins = [b for b in profile if b.volume >= threshold]
    
    # Sort by volume descending
    hvn_bins.sort(key=lambda x: x.volume, reverse=True)
    
    # Return top N prices
    return [round(b.price_mid, 2) for b in hvn_bins[:top_n]]


def find_lvn(
    profile: List[VolumeProfileBin],
    top_n: int = 3,
    max_percentile: float = 25
) -> List[float]:
    """
    Find Low Volume Nodes.
    
    LVNs are price levels with significantly below-average volume.
    These often act as resistance/support levels that price moves through quickly.
    
    Args:
        profile: Volume profile bins
        top_n: Maximum number of LVNs to return
        max_percentile: Maximum volume percentile threshold
        
    Returns:
        List of LVN price levels
    """
    if not profile or len(profile) < 3:
        return []
    
    # Filter out zero-volume bins for percentile calculation
    volumes = [b.volume for b in profile if b.volume > 0]
    if not volumes:
        return []
    
    # Calculate threshold
    sorted_volumes = sorted(volumes)
    threshold_idx = int(len(sorted_volumes) * max_percentile / 100)
    threshold = sorted_volumes[threshold_idx]
    
    # Find bins below threshold (excluding zero volume)
    lvn_bins = [b for b in profile if 0 < b.volume <= threshold]
    
    # Sort by volume ascending
    lvn_bins.sort(key=lambda x: x.volume)
    
    return [round(b.price_mid, 2) for b in lvn_bins[:top_n]]


def find_single_print_zones(
    profile: List[VolumeProfileBin],
    max_zones: int = 2
) -> List[Dict[str, float]]:
    """
    Find single print zones (gaps in the profile).
    
    Single prints are areas with minimal trading activity,
    often representing fast moves that may be revisited.
    
    Args:
        profile: Volume profile bins
        max_zones: Maximum zones to return
        
    Returns:
        List of zone dictionaries with 'low' and 'high' prices
    """
    if not profile or len(profile) < 5:
        return []
    
    avg_volume = statistics.mean(b.volume for b in profile)
    
    # Find consecutive low-volume bins
    zones = []
    current_zone_start = None
    
    for i, bin_data in enumerate(profile):
        is_single_print = bin_data.volume < avg_volume * 0.1  # Less than 10% of average
        
        if is_single_print:
            if current_zone_start is None:
                current_zone_start = i
        else:
            if current_zone_start is not None:
                # End of zone
                zone_length = i - current_zone_start
                if zone_length >= 2:  # At least 2 consecutive bins
                    zones.append({
                        "low": round(profile[current_zone_start].price_low, 2),
                        "high": round(profile[i - 1].price_high, 2)
                    })
                current_zone_start = None
    
    # Check if we ended in a zone
    if current_zone_start is not None:
        zone_length = len(profile) - current_zone_start
        if zone_length >= 2:
            zones.append({
                "low": round(profile[current_zone_start].price_low, 2),
                "high": round(profile[-1].price_high, 2)
            })
    
    # Sort by zone size (larger first)
    zones.sort(key=lambda z: z["high"] - z["low"], reverse=True)
    
    return zones[:max_zones]


def find_magnet_levels(
    profile: List[VolumeProfileBin],
    vpoc: Optional[float],
    vah: Optional[float],
    val: Optional[float],
    max_levels: int = 3
) -> List[float]:
    """
    Find magnet levels (strong support/resistance).
    
    Combines HVN analysis with delta analysis to find levels
    where price is likely to be attracted.
    
    Args:
        profile: Volume profile bins
        vpoc: Volume POC
        vah: Value Area High
        val: Value Area Low
        max_levels: Maximum levels to return
        
    Returns:
        List of magnet level prices
    """
    if not profile:
        return []
    
    magnets = set()
    
    # VPOC is always a magnet
    if vpoc:
        magnets.add(round(vpoc, 2))
    
    # VAH/VAL are magnets
    if vah:
        magnets.add(round(vah, 2))
    if val:
        magnets.add(round(val, 2))
    
    # Find bins with strong delta (institutional activity)
    avg_volume = statistics.mean(b.volume for b in profile if b.volume > 0)
    
    for b in profile:
        if b.volume >= avg_volume * 1.5:  # High volume
            if abs(b.delta_pct) > 30:  # Strong directional imbalance
                magnets.add(round(b.price_mid, 2))
    
    return sorted(list(magnets))[:max_levels]


def find_avoid_zones(
    profile: List[VolumeProfileBin],
    lvn_list: List[float],
    single_prints: List[Dict[str, float]],
    max_zones: int = 2
) -> List[Dict[str, float]]:
    """
    Find zones to avoid placing limit orders.
    
    These are areas where fills are unlikely or risky:
    - LVN areas (price moves through quickly)
    - Single print zones
    - Areas with strong adverse delta
    
    Args:
        profile: Volume profile bins
        lvn_list: LVN price levels
        single_prints: Single print zones
        max_zones: Maximum zones to return
        
    Returns:
        List of avoid zone dictionaries
    """
    avoid_zones = []
    
    # Convert LVNs to zones
    for lvn in lvn_list[:2]:
        avoid_zones.append({
            "price": round(lvn, 2),
            "reason": "LVN - quick price movement"
        })
    
    # Add single print zones
    for sp in single_prints[:1]:
        avoid_zones.append({
            "low": sp["low"],
            "high": sp["high"],
            "reason": "Single print - thin liquidity"
        })
    
    return avoid_zones[:max_zones]


def volume_profile_levels(
    symbol: str,
    window_minutes: int = 240,
    bin_size: Optional[float] = None
) -> Dict[str, Any]:
    """
    Calculate volume profile levels for market structure analysis.
    
    Args:
        symbol: Trading symbol (BTCUSDT, ETHUSDT)
        window_minutes: Time window in minutes (default 240 = 4 hours)
        bin_size: Price bin size in USD (auto-calculated if None)
        
    Returns:
        Dictionary with volume profile levels and quality flags.
        Output is guaranteed to be <= 2KB JSON.
    """
    ts_ms = int(time.time() * 1000)
    
    # Validate inputs
    is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
    if not is_valid:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "validation_error", "message": error}
        }
    
    # Validate window
    window_minutes = min(max(window_minutes, 15), 1440)  # 15min to 24h
    lookback_seconds = window_minutes * 60
    
    # Get market data
    collector = get_market_data_collector()
    
    # Fetch trade history
    start_time_ms = ts_ms - (lookback_seconds * 1000)
    success, trades, error = collector.fetch_historical_trades(
        symbol=normalized_symbol,
        start_time_ms=start_time_ms,
        limit=1000
    )
    
    if not success:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "data_error", "message": error or "Failed to fetch trades"},
            "quality_flags": ["trades_unavailable"]
        }
    
    # Quality flags
    quality_flags = []
    
    if len(trades) < 100:
        quality_flags.append("low_trade_count")
    
    if not trades:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "data_error", "message": "No trades in window"},
            "quality_flags": ["no_trades"]
        }
    
    # Calculate price range
    prices = [t.price for t in trades]
    price_min = min(prices)
    price_max = max(prices)
    price_range = price_max - price_min
    
    # Auto-calculate bin size if not provided
    if bin_size is None:
        bin_size = calculate_dynamic_bin_size(price_range)
    else:
        bin_size = max(0.1, min(bin_size, price_range / 5))  # Reasonable bounds
    
    # Build volume profile
    profile = build_volume_profile(trades, bin_size, price_min, price_max)
    
    if len(profile) < 5:
        quality_flags.append("insufficient_bins")
    
    # Calculate levels
    vpoc = find_vpoc(profile)
    vah, val = find_value_area(profile, percentage=0.70)
    hvn_list = find_hvn(profile, top_n=3)
    lvn_list = find_lvn(profile, top_n=3)
    single_prints = find_single_print_zones(profile, max_zones=2)
    magnet_levels = find_magnet_levels(profile, vpoc, vah, val, max_levels=3)
    avoid_zones = find_avoid_zones(profile, lvn_list, single_prints, max_zones=2)
    
    # Calculate actual time window covered by trades
    if trades:
        actual_start_ms = min(t.timestamp_ms for t in trades)
        actual_end_ms = max(t.timestamp_ms for t in trades)
        actual_window_minutes = (actual_end_ms - actual_start_ms) / 60000
    else:
        actual_window_minutes = 0
    
    # Build response
    response = {
        "success": True,
        "ts_ms": ts_ms,
        "window": {
            "requested_minutes": window_minutes,
            "actual_minutes": round(actual_window_minutes, 1),
            "trade_count": len(trades),
            "bin_size": bin_size,
            "price_range": {
                "low": round(price_min, 2),
                "high": round(price_max, 2)
            }
        },
        "levels": {
            "vpoc": round(vpoc, 2) if vpoc else None,
            "vah": round(vah, 2) if vah else None,
            "val": round(val, 2) if val else None,
            "hvn": hvn_list,
            "lvn": lvn_list,
            "single_print_zones": single_prints,
            "magnet_levels": magnet_levels,
            "avoid_zones": avoid_zones
        }
    }
    
    if quality_flags:
        response["quality_flags"] = quality_flags
    
    return response
