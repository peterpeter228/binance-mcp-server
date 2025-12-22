"""
Multi-Horizon Queue Fill Probability Tool.

Estimates limit order fill probability across multiple time horizons:
- Uses historical trade flow to estimate arrival rates
- Calculates queue depth at each price level
- Provides fill probability for each horizon (e.g., 60s, 300s, 900s)
- Estimates adverse selection risk

Key Features:
- Multiple time horizons in single call
- Queue position assumptions (best/mid/worst case)
- Adverse selection scoring
- 30-second cache for identical parameters
- Exponential backoff on rate limits
"""

import time
import math
import logging
import statistics
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from collections import defaultdict

from binance_mcp_server.futures_config import ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.tools.futures.market_data_collector import (
    get_market_data_collector,
    OrderBookSnapshot,
    TradeRecord,
)
from binance_mcp_server.tools.futures.rate_limit_utils import (
    get_tool_cache,
    ParameterCache,
    make_api_call_with_backoff,
    RetryConfig,
)

logger = logging.getLogger(__name__)

# Cache with 30-second TTL
_fill_prob_cache = get_tool_cache("queue_fill_probability_multi_horizon", default_ttl=30.0)


@dataclass
class TradeFlowStats:
    """Statistics about trade flow at price levels."""
    total_qty: float
    total_trades: int
    buy_aggressor_qty: float
    sell_aggressor_qty: float
    avg_trade_size: float
    arrival_rate_per_sec: float


def analyze_trade_flow(
    trades: List[TradeRecord],
    price_level: float,
    side: str,
    lookback_seconds: float,
    price_tolerance_pct: float = 0.1
) -> TradeFlowStats:
    """
    Analyze trade flow relevant to a specific price level.
    
    For BUY limit orders at price P:
    - Relevant trades are sells (that would consume our bid)
    - Focus on trades at or below price P
    
    For SELL limit orders at price P:
    - Relevant trades are buys (that would consume our ask)
    - Focus on trades at or above price P
    
    Args:
        trades: List of trade records
        price_level: Our target price level
        side: "LONG" for buy or "SHORT" for sell
        lookback_seconds: Time window
        price_tolerance_pct: Price range to consider (as %)
        
    Returns:
        TradeFlowStats with relevant flow analysis
    """
    if not trades or lookback_seconds <= 0:
        return TradeFlowStats(
            total_qty=0, total_trades=0, buy_aggressor_qty=0,
            sell_aggressor_qty=0, avg_trade_size=0, arrival_rate_per_sec=0
        )
    
    cutoff_ms = int((time.time() - lookback_seconds) * 1000)
    price_range = price_level * (price_tolerance_pct / 100)
    
    # Filter relevant trades
    relevant_trades = []
    for t in trades:
        if t.timestamp_ms < cutoff_ms:
            continue
        
        if side.upper() == "LONG":
            # For LONG (buy limit), count sells at or below our price
            if t.price <= price_level + price_range and t.side == "sell":
                relevant_trades.append(t)
        else:
            # For SHORT (sell limit), count buys at or above our price
            if t.price >= price_level - price_range and t.side == "buy":
                relevant_trades.append(t)
    
    if not relevant_trades:
        return TradeFlowStats(
            total_qty=0, total_trades=0, buy_aggressor_qty=0,
            sell_aggressor_qty=0, avg_trade_size=0, arrival_rate_per_sec=0
        )
    
    total_qty = sum(t.qty for t in relevant_trades)
    buy_qty = sum(t.qty for t in relevant_trades if t.side == "buy")
    sell_qty = sum(t.qty for t in relevant_trades if t.side == "sell")
    avg_size = total_qty / len(relevant_trades) if relevant_trades else 0
    
    # Calculate arrival rate
    time_span = (relevant_trades[-1].timestamp_ms - relevant_trades[0].timestamp_ms) / 1000.0
    arrival_rate = total_qty / max(time_span, 1.0)
    
    return TradeFlowStats(
        total_qty=total_qty,
        total_trades=len(relevant_trades),
        buy_aggressor_qty=buy_qty,
        sell_aggressor_qty=sell_qty,
        avg_trade_size=avg_size,
        arrival_rate_per_sec=arrival_rate
    )


def estimate_queue_depth(
    orderbook: OrderBookSnapshot,
    side: str,
    price_level: float,
    queue_position: str = "mid"
) -> Tuple[float, float]:
    """
    Estimate queue depth and our position at a price level.
    
    Args:
        orderbook: Current orderbook snapshot
        side: "LONG" (buy) or "SHORT" (sell)
        price_level: Target price
        queue_position: "best_case", "mid", or "worst_case"
        
    Returns:
        Tuple of (queue_qty_ahead, level_qty)
    """
    if side.upper() == "LONG":
        # For buy, look at bids at or above our price
        levels = [(p, q) for p, q in orderbook.bids if p >= price_level]
        level_qty = sum(q for p, q in orderbook.bids if abs(p - price_level) < 0.01)
    else:
        # For sell, look at asks at or below our price
        levels = [(p, q) for p, q in orderbook.asks if p <= price_level]
        level_qty = sum(q for p, q in orderbook.asks if abs(p - price_level) < 0.01)
    
    total_ahead = sum(q for _, q in levels) if levels else 0
    
    # Adjust based on queue position assumption
    if queue_position == "best_case":
        # Assume we're at front of queue
        queue_ahead = 0
    elif queue_position == "worst_case":
        # Assume we're at back of queue
        queue_ahead = total_ahead
    else:  # mid
        # Assume middle position
        queue_ahead = total_ahead / 2
    
    return queue_ahead, level_qty


def calculate_poisson_fill_prob(
    queue_ahead: float,
    arrival_rate: float,
    horizon_seconds: float
) -> float:
    """
    Calculate fill probability using Poisson process model.
    
    P(fill) = 1 - P(arrivals < queue_ahead)
            = 1 - sum(e^(-λt) * (λt)^k / k!) for k < queue_ahead
    
    Args:
        queue_ahead: Quantity ahead in queue
        arrival_rate: Trade arrival rate (qty/sec)
        horizon_seconds: Time horizon
        
    Returns:
        Fill probability (0-1)
    """
    if queue_ahead <= 0:
        return 1.0
    if arrival_rate <= 0:
        return 0.0
    
    # Expected arrivals in the horizon
    lambda_t = arrival_rate * horizon_seconds
    
    # Approximate using CDF of Poisson distribution
    # For large lambda_t, use normal approximation
    if lambda_t > 50:
        # Normal approximation: N(lambda_t, sqrt(lambda_t))
        z = (queue_ahead - lambda_t) / max(math.sqrt(lambda_t), 0.001)
        # Standard normal CDF approximation
        prob_not_filled = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return round(1 - prob_not_filled, 4)
    
    # Direct Poisson calculation for smaller lambda
    prob_not_filled = 0.0
    for k in range(int(queue_ahead)):
        try:
            term = math.exp(-lambda_t) * (lambda_t ** k) / math.factorial(k)
            prob_not_filled += term
        except (OverflowError, ValueError):
            break
    
    return round(min(1.0, max(0.0, 1 - prob_not_filled)), 4)


def calculate_eta_seconds(
    queue_ahead: float,
    arrival_rate: float,
    percentile: float = 0.5
) -> Optional[float]:
    """
    Calculate estimated time to fill at given percentile.
    
    Uses inverse of exponential CDF.
    
    Args:
        queue_ahead: Quantity ahead in queue
        arrival_rate: Trade arrival rate (qty/sec)
        percentile: Target percentile (0.5 for median)
        
    Returns:
        ETA in seconds, or None if cannot estimate
    """
    if queue_ahead <= 0:
        return 0.0
    if arrival_rate <= 0:
        return None
    
    # For Poisson process, time to k arrivals follows Gamma distribution
    # Mean = k / lambda, so P50 ≈ k / lambda (approximately)
    mean_time = queue_ahead / arrival_rate
    
    # Adjust for percentile (rough approximation)
    if percentile <= 0.5:
        eta = mean_time * (percentile * 2)  # Faster than mean
    else:
        eta = mean_time * (1 + (percentile - 0.5) * 2)  # Slower than mean
    
    return round(max(0, eta), 1)


def calculate_adverse_selection_score(
    trades: List[TradeRecord],
    orderbook: OrderBookSnapshot,
    side: str,
    price_level: float,
    lookback_seconds: float = 10.0
) -> Tuple[float, List[str]]:
    """
    Calculate adverse selection risk.
    
    High score = fills are more likely to be followed by unfavorable price moves.
    
    Factors:
    - Order flow imbalance against our position
    - Recent price momentum against our position
    - Large trades indicating informed flow
    
    Args:
        trades: Recent trades
        orderbook: Current orderbook
        side: "LONG" or "SHORT"
        price_level: Our target price
        lookback_seconds: Short lookback for recent flow
        
    Returns:
        Tuple of (score 0-100, notes)
    """
    notes = []
    score = 0.0
    
    if not trades:
        return 50.0, ["Insufficient trade data"]
    
    cutoff_ms = int((time.time() - lookback_seconds) * 1000)
    recent = [t for t in trades if t.timestamp_ms >= cutoff_ms]
    
    if len(recent) < 5:
        return 50.0, ["Low trade activity"]
    
    # 1. Order flow imbalance (40 points max)
    buy_vol = sum(t.qty for t in recent if t.side == "buy")
    sell_vol = sum(t.qty for t in recent if t.side == "sell")
    total_vol = buy_vol + sell_vol
    
    if total_vol > 0:
        if side.upper() == "LONG":
            # For LONG, adverse if heavy sell flow (price likely to drop)
            sell_ratio = sell_vol / total_vol
            if sell_ratio > 0.7:
                score += 40
                notes.append("Heavy sell flow")
            elif sell_ratio > 0.6:
                score += 25
        else:
            # For SHORT, adverse if heavy buy flow (price likely to rise)
            buy_ratio = buy_vol / total_vol
            if buy_ratio > 0.7:
                score += 40
                notes.append("Heavy buy flow")
            elif buy_ratio > 0.6:
                score += 25
    
    # 2. Price momentum (30 points max)
    if len(recent) >= 3:
        first_price = recent[0].price
        last_price = recent[-1].price
        pct_change = (last_price - first_price) / first_price * 100
        
        if side.upper() == "LONG":
            # For LONG, adverse if price falling (will fill into weakness)
            if pct_change < -0.1:
                score += 30
                notes.append("Price declining")
            elif pct_change < -0.05:
                score += 15
        else:
            # For SHORT, adverse if price rising (will fill into strength)
            if pct_change > 0.1:
                score += 30
                notes.append("Price rising")
            elif pct_change > 0.05:
                score += 15
    
    # 3. OBI analysis (30 points max)
    if orderbook.bids and orderbook.asks:
        bid_vol = sum(q for _, q in orderbook.bids[:10])
        ask_vol = sum(q for _, q in orderbook.asks[:10])
        total = bid_vol + ask_vol
        
        if total > 0:
            obi = (bid_vol - ask_vol) / total
            
            if side.upper() == "LONG":
                # For LONG, adverse if OBI negative (more asks)
                if obi < -0.3:
                    score += 30
                    notes.append("OBI bearish")
                elif obi < -0.1:
                    score += 15
            else:
                # For SHORT, adverse if OBI positive (more bids)
                if obi > 0.3:
                    score += 30
                    notes.append("OBI bullish")
                elif obi > 0.1:
                    score += 15
    
    return round(min(100, score), 1), notes[:2]


def queue_fill_probability_multi_horizon(
    symbol: str,
    side: str,
    price_levels: List[float],
    qty: float,
    horizons_sec: List[int] = None,
    lookback_sec: int = 120,
    assume_queue_position: str = "mid"
) -> Dict[str, Any]:
    """
    Estimate fill probability across multiple time horizons.
    
    Uses historical trade flow and orderbook depth to estimate:
    - Fill probability at each horizon (60s, 300s, 900s, etc.)
    - Estimated time to fill (P50)
    - Adverse selection risk
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        side: Position side ("LONG" for buy, "SHORT" for sell)
        price_levels: Price levels to analyze (max 5)
        qty: Order quantity
        horizons_sec: Time horizons in seconds (default [60, 300, 900], max 5)
        lookback_sec: Lookback for trade flow analysis (max 600)
        assume_queue_position: Queue position assumption ("best_case", "mid", "worst_case")
        
    Returns:
        Dictionary containing:
        - per_level: Analysis for each price level
        - overall_best_level: Recommended price level
        - quality_flags: Data quality indicators
        - confidence_0_1: Confidence in estimates
    """
    ts_ms = int(time.time() * 1000)
    
    # Default horizons
    if horizons_sec is None:
        horizons_sec = [60, 300, 900]
    
    # Input validation
    is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
    if not is_valid:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "validation_error", "message": error}
        }
    
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "validation_error", "message": "Side must be LONG or SHORT"}
        }
    
    # Constrain parameters
    price_levels = price_levels[:5]
    horizons_sec = horizons_sec[:5]
    lookback_sec = min(max(lookback_sec, 30), 600)
    
    if assume_queue_position not in ("best_case", "mid", "worst_case"):
        assume_queue_position = "mid"
    
    if not price_levels:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "validation_error", "message": "At least one price level required"}
        }
    
    # Check cache
    cache_params = {
        "symbol": normalized_symbol,
        "side": side,
        "price_levels": sorted(price_levels),
        "qty": qty,
        "horizons_sec": sorted(horizons_sec),
        "lookback_sec": lookback_sec,
        "assume_queue_position": assume_queue_position
    }
    cache_key = ParameterCache._hash_params(cache_params)
    hit, cached = _fill_prob_cache.get(cache_key)
    if hit:
        cached["_cache_hit"] = True
        return cached
    
    # Get market data collector
    collector = get_market_data_collector()
    retry_config = RetryConfig(max_retries=2, base_delay_ms=500)
    
    # Fetch orderbook
    success, ob_result, ob_error = make_api_call_with_backoff(
        lambda: collector.fetch_orderbook(normalized_symbol, limit=100),
        retry_config,
        "fetch_orderbook"
    )
    
    if not success or ob_result is None:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "data_error", "message": ob_error or "Failed to fetch orderbook"}
        }
    
    orderbook: OrderBookSnapshot = ob_result
    
    # Ensure trade history
    success, th_error = collector.ensure_trade_history(normalized_symbol, lookback_sec)
    if not success:
        logger.warning(f"Could not fetch full trade history: {th_error}")
    
    # Get trades from buffer
    trades = collector.get_buffered_trades(normalized_symbol, lookback_sec)
    
    quality_flags = []
    if len(trades) < 50:
        quality_flags.append("low_trade_volume")
    
    # Get mark price for reference
    mark_success, mark_data, _ = make_api_call_with_backoff(
        lambda: collector.fetch_mark_price(normalized_symbol),
        retry_config,
        "fetch_mark_price"
    )
    mark_price = mark_data.mark_price if mark_success and mark_data else (
        orderbook.mid_price or price_levels[0]
    )
    
    # Analyze each price level
    per_level = []
    best_level_score = -float('inf')
    best_level_price = None
    
    for price in price_levels:
        # Estimate queue depth
        queue_ahead, level_qty = estimate_queue_depth(orderbook, side, price, assume_queue_position)
        total_queue = queue_ahead + qty
        
        # Analyze trade flow at this level
        flow_stats = analyze_trade_flow(trades, price, side, lookback_sec)
        
        # Calculate fill probabilities for each horizon
        fill_probs = {}
        for horizon in horizons_sec:
            prob = calculate_poisson_fill_prob(total_queue, flow_stats.arrival_rate_per_sec, horizon)
            fill_probs[horizon] = prob
        
        # Calculate ETA
        eta_p50 = calculate_eta_seconds(total_queue, flow_stats.arrival_rate_per_sec, 0.5)
        
        # Calculate adverse selection score
        adverse_score, adverse_notes = calculate_adverse_selection_score(
            trades, orderbook, side, price, lookback_seconds=10.0
        )
        
        level_result = {
            "price": round(price, 2),
            "fill_prob": fill_probs,
            "eta_sec_p50": eta_p50,
            "adverse_selection_score_0_100": adverse_score
        }
        per_level.append(level_result)
        
        # Score this level for recommendation
        # Higher fill prob at medium horizon (300s) + lower adverse selection
        medium_horizon = horizons_sec[len(horizons_sec) // 2] if horizons_sec else 300
        medium_prob = fill_probs.get(medium_horizon, 0)
        level_score = medium_prob * 100 - adverse_score * 0.5
        
        if level_score > best_level_score:
            best_level_score = level_score
            best_level_price = price
    
    # Calculate overall confidence
    confidence = 0.5
    if len(trades) >= 100:
        confidence += 0.2
    elif len(trades) >= 50:
        confidence += 0.1
    
    if orderbook.spread_bps and orderbook.spread_bps < 5:
        confidence += 0.2
    elif orderbook.spread_bps and orderbook.spread_bps < 10:
        confidence += 0.1
    
    confidence = round(min(1.0, confidence), 2)
    
    # Build response
    response = {
        "success": True,
        "ts_ms": ts_ms,
        "inputs": {
            "symbol": normalized_symbol,
            "side": side,
            "price_levels": price_levels,
            "qty": qty,
            "horizons_sec": horizons_sec,
            "lookback_sec": lookback_sec,
            "assume_queue_position": assume_queue_position
        },
        "per_level": per_level,
        "overall_best_level": round(best_level_price, 2) if best_level_price else None,
        "confidence_0_1": confidence,
        "_cache_hit": False
    }
    
    if quality_flags:
        response["quality_flags"] = quality_flags
    
    # Cache the result
    _fill_prob_cache.set(cache_key, response)
    
    return response
