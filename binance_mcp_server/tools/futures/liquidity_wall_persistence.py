"""
Liquidity Wall Persistence / Spoof Filter Tool.

Tracks order book walls over time to detect:
- True liquidity walls (persistent, stable)
- Spoof orders (appear/disappear quickly, jump prices)
- Magnet levels (high persistence walls)
- Avoid zones (frequently spoofed areas)

Key Features:
- Samples orderbook at configurable intervals
- Tracks wall persistence scores
- Detects spoofing patterns
- 60-second cache for identical parameters
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
    OrderBookSnapshot,
)
from binance_mcp_server.tools.futures.rate_limit_utils import (
    get_tool_cache,
    ParameterCache,
    make_api_call_with_backoff,
    RetryConfig,
)

logger = logging.getLogger(__name__)

# Cache with 60-second TTL
_wall_cache = get_tool_cache("liquidity_wall_persistence", default_ttl=60.0)


@dataclass
class WallObservation:
    """Single observation of a wall at a price level."""
    price: float
    notional_usd: float
    timestamp_ms: int
    side: str  # "bid" or "ask"


@dataclass
class WallTracker:
    """Tracks wall observations over time."""
    price: float
    side: str
    observations: List[WallObservation] = field(default_factory=list)
    
    @property
    def first_seen_ms(self) -> Optional[int]:
        return self.observations[0].timestamp_ms if self.observations else None
    
    @property
    def last_seen_ms(self) -> Optional[int]:
        return self.observations[-1].timestamp_ms if self.observations else None
    
    @property
    def avg_notional_usd(self) -> float:
        if not self.observations:
            return 0.0
        return statistics.mean(o.notional_usd for o in self.observations)
    
    @property
    def notional_variance(self) -> float:
        """Variance in notional - high variance indicates instability/spoofing."""
        if len(self.observations) < 2:
            return 0.0
        notionals = [o.notional_usd for o in self.observations]
        return statistics.variance(notionals) / (self.avg_notional_usd ** 2) if self.avg_notional_usd > 0 else 0
    
    def life_seconds(self) -> float:
        """Total time this wall has been observed."""
        if not self.first_seen_ms or not self.last_seen_ms:
            return 0.0
        return (self.last_seen_ms - self.first_seen_ms) / 1000.0
    
    def presence_ratio(self, window_ms: int) -> float:
        """Ratio of time wall was present vs window length."""
        if not self.observations or window_ms <= 0:
            return 0.0
        # Count gaps between observations
        total_present = len(self.observations)
        expected_samples = window_ms / 1000  # Roughly one per second
        return min(1.0, total_present / max(1, expected_samples))


def calculate_persistence_score(
    tracker: WallTracker,
    window_ms: int,
    total_samples: int
) -> float:
    """
    Calculate persistence score (0-100) for a wall.
    
    Factors:
    - Presence ratio (was wall present in most samples?)
    - Notional stability (consistent size?)
    - Life duration (how long has it existed?)
    
    Args:
        tracker: WallTracker with observations
        window_ms: Total window in milliseconds
        total_samples: Total number of samples taken
        
    Returns:
        Persistence score 0-100
    """
    if not tracker.observations or total_samples == 0:
        return 0.0
    
    score = 0.0
    
    # 1. Presence score (40 points max)
    presence = len(tracker.observations) / total_samples
    score += presence * 40
    
    # 2. Stability score (30 points max)
    # Low variance = high stability
    variance = tracker.notional_variance
    if variance < 0.05:
        score += 30
    elif variance < 0.2:
        score += 20
    elif variance < 0.5:
        score += 10
    
    # 3. Life duration score (30 points max)
    life_sec = tracker.life_seconds()
    window_sec = window_ms / 1000.0
    life_ratio = min(1.0, life_sec / window_sec)
    score += life_ratio * 30
    
    return round(min(100, max(0, score)), 1)


def calculate_spoof_indicators(
    trackers: Dict[float, WallTracker],
    side: str,
    window_ms: int,
    total_samples: int
) -> Tuple[float, List[str]]:
    """
    Calculate spoof risk score and notes.
    
    Spoof indicators:
    - Walls that appear/disappear frequently
    - Walls that jump price levels
    - High notional variance
    - Short-lived large walls
    
    Args:
        trackers: Dictionary of price -> WallTracker
        side: "bid" or "ask"
        window_ms: Window duration
        total_samples: Total samples taken
        
    Returns:
        Tuple of (spoof_score_0_100, notes)
    """
    notes = []
    score = 0.0
    
    if not trackers or total_samples < 3:
        return 0.0, ["Insufficient data"]
    
    # Count walls that appeared briefly
    brief_walls = 0
    unstable_walls = 0
    
    for price, tracker in trackers.items():
        presence = len(tracker.observations) / total_samples
        
        # Brief appearance (< 30% of window)
        if 0 < presence < 0.3:
            brief_walls += 1
        
        # High variance (spoofing signature)
        if tracker.notional_variance > 0.3:
            unstable_walls += 1
    
    total_walls = len(trackers)
    
    # Score based on ratio of suspicious walls
    if total_walls > 0:
        brief_ratio = brief_walls / total_walls
        unstable_ratio = unstable_walls / total_walls
        
        if brief_ratio > 0.5:
            score += 40
            notes.append(f"Many brief {side} walls ({brief_walls}/{total_walls})")
        elif brief_ratio > 0.3:
            score += 20
        
        if unstable_ratio > 0.5:
            score += 40
            notes.append(f"Unstable {side} notionals")
        elif unstable_ratio > 0.3:
            score += 20
    
    # Check for price jumping (walls moving between levels)
    # This is detected by having many unique prices with few observations each
    if total_walls > 5:
        avg_obs_per_wall = sum(len(t.observations) for t in trackers.values()) / total_walls
        if avg_obs_per_wall < 2:
            score += 20
            notes.append("Walls jumping between prices")
    
    return round(min(100, score), 1), notes[:2]


def identify_magnet_levels(
    bid_trackers: Dict[float, WallTracker],
    ask_trackers: Dict[float, WallTracker],
    window_ms: int,
    total_samples: int,
    max_levels: int = 6
) -> List[float]:
    """
    Identify magnet levels (high-persistence walls that attract price).
    
    Args:
        bid_trackers: Bid wall trackers
        ask_trackers: Ask wall trackers
        window_ms: Window duration
        total_samples: Total samples
        max_levels: Maximum levels to return
        
    Returns:
        List of magnet price levels
    """
    magnets = []
    
    # Combine all trackers with their persistence scores
    all_tracked = []
    for price, tracker in bid_trackers.items():
        score = calculate_persistence_score(tracker, window_ms, total_samples)
        if score >= 70:  # High persistence threshold
            all_tracked.append((price, score, tracker.avg_notional_usd))
    
    for price, tracker in ask_trackers.items():
        score = calculate_persistence_score(tracker, window_ms, total_samples)
        if score >= 70:
            all_tracked.append((price, score, tracker.avg_notional_usd))
    
    # Sort by score * notional (importance)
    all_tracked.sort(key=lambda x: x[1] * x[2], reverse=True)
    
    # Return unique prices
    seen = set()
    for price, _, _ in all_tracked:
        if price not in seen:
            magnets.append(round(price, 2))
            seen.add(price)
            if len(magnets) >= max_levels:
                break
    
    return magnets


def identify_avoid_zones(
    bid_trackers: Dict[float, WallTracker],
    ask_trackers: Dict[float, WallTracker],
    window_ms: int,
    total_samples: int,
    max_zones: int = 4
) -> List[Dict[str, Any]]:
    """
    Identify zones to avoid placing orders (high spoof risk).
    
    Args:
        bid_trackers: Bid wall trackers
        ask_trackers: Ask wall trackers  
        window_ms: Window duration
        total_samples: Total samples
        max_zones: Maximum zones to return
        
    Returns:
        List of avoid zone dictionaries
    """
    zones = []
    
    # Find high-variance, low-persistence price ranges
    suspicious_prices = []
    
    for price, tracker in bid_trackers.items():
        score = calculate_persistence_score(tracker, window_ms, total_samples)
        if tracker.notional_variance > 0.3 and score < 50:
            suspicious_prices.append((price, "bid_spoof_risk"))
    
    for price, tracker in ask_trackers.items():
        score = calculate_persistence_score(tracker, window_ms, total_samples)
        if tracker.notional_variance > 0.3 and score < 50:
            suspicious_prices.append((price, "ask_spoof_risk"))
    
    # Cluster nearby prices into zones
    if suspicious_prices:
        suspicious_prices.sort(key=lambda x: x[0])
        
        current_zone = None
        for price, reason in suspicious_prices:
            if current_zone is None:
                current_zone = {"low": price, "high": price, "reason": reason}
            elif price - current_zone["high"] < current_zone["high"] * 0.001:  # Within 0.1%
                current_zone["high"] = price
            else:
                zones.append(current_zone)
                current_zone = {"low": price, "high": price, "reason": reason}
        
        if current_zone:
            zones.append(current_zone)
    
    # Round and limit
    for zone in zones:
        zone["low"] = round(zone["low"], 2)
        zone["high"] = round(zone["high"], 2)
    
    return zones[:max_zones]


def liquidity_wall_persistence(
    symbol: str,
    depth_limit: int = 50,
    window_seconds: int = 60,
    sample_interval_ms: int = 1000,
    top_n: int = 5,
    wall_threshold_usd: float = 1_000_000
) -> Dict[str, Any]:
    """
    Track order book walls and detect spoofing patterns.
    
    Samples the orderbook over a time window to identify:
    - Persistent bid/ask walls (true liquidity)
    - Spoof patterns (appearing/disappearing, unstable notionals)
    - Magnet levels (strong attraction points)
    - Avoid zones (high spoof risk areas)
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        depth_limit: Orderbook depth to fetch (max 100)
        window_seconds: Sampling window duration (max 300)
        sample_interval_ms: Time between samples (min 500ms)
        top_n: Number of top walls to return per side (max 10)
        wall_threshold_usd: Minimum notional to consider a wall
        
    Returns:
        Dictionary containing:
        - bid_walls: List of bid walls with persistence scores
        - ask_walls: List of ask walls with persistence scores
        - spoof_risk_score_0_100: Overall spoofing risk assessment
        - magnet_levels: High-persistence price levels
        - avoid_zones: Zones with high spoof risk
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
    
    # Validate and constrain parameters
    depth_limit = min(max(depth_limit, 10), 100)
    window_seconds = min(max(window_seconds, 10), 300)
    sample_interval_ms = max(sample_interval_ms, 500)
    top_n = min(max(top_n, 1), 10)
    wall_threshold_usd = max(wall_threshold_usd, 10000)
    
    # Check cache
    cache_params = {
        "symbol": normalized_symbol,
        "depth_limit": depth_limit,
        "window_seconds": window_seconds,
        "sample_interval_ms": sample_interval_ms,
        "top_n": top_n,
        "wall_threshold_usd": wall_threshold_usd
    }
    cache_key = ParameterCache._hash_params(cache_params)
    hit, cached = _wall_cache.get(cache_key)
    if hit:
        cached["_cache_hit"] = True
        return cached
    
    # Get market data collector
    collector = get_market_data_collector()
    
    # Track walls over time
    bid_trackers: Dict[float, WallTracker] = defaultdict(lambda: WallTracker(price=0, side="bid"))
    ask_trackers: Dict[float, WallTracker] = defaultdict(lambda: WallTracker(price=0, side="ask"))
    
    # Get mark price for USD calculations
    retry_config = RetryConfig(max_retries=2, base_delay_ms=500)
    mark_success, mark_data, mark_error = make_api_call_with_backoff(
        lambda: collector.fetch_mark_price(normalized_symbol),
        retry_config,
        "fetch_mark_price"
    )
    
    if not mark_success or mark_data is None:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "data_error", "message": mark_error or "Failed to fetch mark price"}
        }
    
    mark_price = mark_data.mark_price
    
    # Sample orderbook over the window
    window_ms = window_seconds * 1000
    start_time = time.time()
    samples_taken = 0
    quality_flags = []
    
    while (time.time() - start_time) * 1000 < window_ms:
        sample_ts = int(time.time() * 1000)
        
        # Fetch orderbook with retry
        success, ob_data, ob_error = make_api_call_with_backoff(
            lambda: collector.fetch_orderbook(normalized_symbol, limit=depth_limit, use_cache=False),
            retry_config,
            "fetch_orderbook"
        )
        
        if not success or ob_data is None:
            logger.warning(f"Failed to fetch orderbook sample: {ob_error}")
            time.sleep(sample_interval_ms / 1000.0)
            continue
        
        orderbook: OrderBookSnapshot = ob_data
        samples_taken += 1
        
        # Process bids - look for walls
        for price, qty in orderbook.bids:
            notional_usd = qty * mark_price
            if notional_usd >= wall_threshold_usd:
                if price not in bid_trackers or bid_trackers[price].price == 0:
                    bid_trackers[price] = WallTracker(price=price, side="bid")
                bid_trackers[price].observations.append(
                    WallObservation(price=price, notional_usd=notional_usd, timestamp_ms=sample_ts, side="bid")
                )
        
        # Process asks - look for walls
        for price, qty in orderbook.asks:
            notional_usd = qty * mark_price
            if notional_usd >= wall_threshold_usd:
                if price not in ask_trackers or ask_trackers[price].price == 0:
                    ask_trackers[price] = WallTracker(price=price, side="ask")
                ask_trackers[price].observations.append(
                    WallObservation(price=price, notional_usd=notional_usd, timestamp_ms=sample_ts, side="ask")
                )
        
        # Wait for next sample
        elapsed = time.time() - start_time
        remaining = (window_ms / 1000.0) - elapsed
        if remaining > sample_interval_ms / 1000.0:
            time.sleep(sample_interval_ms / 1000.0)
    
    if samples_taken < 3:
        quality_flags.append("low_sample_count")
    
    # Calculate persistence scores and build output
    bid_walls = []
    for price, tracker in sorted(bid_trackers.items(), reverse=True)[:20]:  # Process top 20 by price
        persistence = calculate_persistence_score(tracker, window_ms, samples_taken)
        if persistence > 0:
            bid_walls.append({
                "price": round(price, 2),
                "notional_usd": round(tracker.avg_notional_usd, 0),
                "persistence_score_0_100": persistence,
                "avg_life_sec": round(tracker.life_seconds(), 1)
            })
    
    # Sort by persistence and take top_n
    bid_walls.sort(key=lambda x: x["persistence_score_0_100"], reverse=True)
    bid_walls = bid_walls[:top_n]
    
    ask_walls = []
    for price, tracker in sorted(ask_trackers.items())[:20]:  # Process top 20 by price
        persistence = calculate_persistence_score(tracker, window_ms, samples_taken)
        if persistence > 0:
            ask_walls.append({
                "price": round(price, 2),
                "notional_usd": round(tracker.avg_notional_usd, 0),
                "persistence_score_0_100": persistence,
                "avg_life_sec": round(tracker.life_seconds(), 1)
            })
    
    # Sort by persistence and take top_n
    ask_walls.sort(key=lambda x: x["persistence_score_0_100"], reverse=True)
    ask_walls = ask_walls[:top_n]
    
    # Calculate spoof risk
    bid_spoof_score, bid_spoof_notes = calculate_spoof_indicators(bid_trackers, "bid", window_ms, samples_taken)
    ask_spoof_score, ask_spoof_notes = calculate_spoof_indicators(ask_trackers, "ask", window_ms, samples_taken)
    spoof_risk_score = round((bid_spoof_score + ask_spoof_score) / 2, 1)
    
    # Identify magnet levels
    magnet_levels = identify_magnet_levels(bid_trackers, ask_trackers, window_ms, samples_taken)
    
    # Identify avoid zones
    avoid_zones = identify_avoid_zones(bid_trackers, ask_trackers, window_ms, samples_taken)
    
    # Generate notes
    notes = []
    if spoof_risk_score > 60:
        notes.append("High spoof activity detected")
    elif spoof_risk_score > 30:
        notes.append("Moderate spoof activity")
    else:
        notes.append("Low spoof activity")
    
    if magnet_levels:
        notes.append(f"{len(magnet_levels)} strong magnet levels identified")
    
    if len(bid_walls) == 0 and len(ask_walls) == 0:
        notes.append(f"No walls >= ${wall_threshold_usd:,.0f} found")
    
    notes.extend(bid_spoof_notes)
    notes.extend(ask_spoof_notes)
    
    # Build response
    response = {
        "success": True,
        "ts_ms": ts_ms,
        "inputs": {
            "symbol": normalized_symbol,
            "depth_limit": depth_limit,
            "window_seconds": window_seconds,
            "sample_interval_ms": sample_interval_ms,
            "top_n": top_n,
            "wall_threshold_usd": wall_threshold_usd
        },
        "sampling": {
            "samples_taken": samples_taken,
            "actual_window_sec": round(time.time() - start_time, 1)
        },
        "bid_walls": bid_walls,
        "ask_walls": ask_walls,
        "spoof_risk_score_0_100": spoof_risk_score,
        "magnet_levels": magnet_levels,
        "avoid_zones": avoid_zones,
        "notes": notes[:4],  # Limit to 4 notes
        "_cache_hit": False
    }
    
    if quality_flags:
        response["quality_flags"] = quality_flags
    
    # Cache the result
    _wall_cache.set(cache_key, response)
    
    return response
