"""
Queue Fill Estimator Tool for Limit Order Analysis.

Estimates queue position, fill probability, and ETA for limit orders
based on orderbook depth and trade flow analysis.

Key Features:
- Queue position estimation using orderbook + trade consumption
- Fill probability calculation (30s, 60s windows)
- Consumption rate analysis by aggressor side
- Adverse selection score for risk assessment
- Microstructure health scoring
"""

import time
import logging
import statistics
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from binance_mcp_server.futures_config import ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.tools.futures.market_data_collector import (
    get_market_data_collector,
    OrderBookSnapshot,
    TradeRecord,
)

logger = logging.getLogger(__name__)


@dataclass
class QueueMetrics:
    """Metrics for a single price level queue."""
    price: float
    queue_qty_est: float
    queue_value_usd: float
    consumption_rate_qty_per_s: float
    eta_p50_s: Optional[float]
    eta_p95_s: Optional[float]
    fill_prob_30s: float
    fill_prob_60s: float
    adverse_selection_score: float
    notes: List[str]


@dataclass  
class GlobalMetrics:
    """Global market microstructure metrics."""
    micro_health_score: float  # 0-100 score
    spread_bps: float
    obi_mean: float  # Order Book Imbalance mean
    obi_stdev: float  # OBI standard deviation
    wall_risk_level: str  # "low", "medium", "high"
    best_price: Optional[float]
    recommendation_why: str


def calculate_obi(bids: List[Tuple[float, float]], asks: List[Tuple[float, float]], levels: int = 5) -> float:
    """
    Calculate Order Book Imbalance (OBI).
    
    OBI = (bid_volume - ask_volume) / (bid_volume + ask_volume)
    Positive = more bids (bullish), Negative = more asks (bearish)
    
    Args:
        bids: List of (price, qty) tuples
        asks: List of (price, qty) tuples
        levels: Number of levels to consider
        
    Returns:
        OBI value between -1 and 1
    """
    bid_vol = sum(qty for _, qty in bids[:levels])
    ask_vol = sum(qty for _, qty in asks[:levels])
    total = bid_vol + ask_vol
    
    if total == 0:
        return 0.0
    
    return (bid_vol - ask_vol) / total


def calculate_consumption_rate(
    trades: List[TradeRecord],
    side: str,
    lookback_seconds: float
) -> Tuple[float, float, int]:
    """
    Calculate consumption rate for a given side.
    
    Args:
        trades: List of recent trades
        side: "buy" or "sell" - the aggressor side
        lookback_seconds: Time window
        
    Returns:
        Tuple of (rate_per_second, total_qty, trade_count)
    """
    if not trades or lookback_seconds <= 0:
        return 0.0, 0.0, 0
    
    cutoff_ms = int((time.time() - lookback_seconds) * 1000)
    relevant_trades = [t for t in trades if t.timestamp_ms >= cutoff_ms and t.side == side]
    
    if not relevant_trades:
        return 0.0, 0.0, 0
    
    total_qty = sum(t.qty for t in relevant_trades)
    rate = total_qty / lookback_seconds
    
    return rate, total_qty, len(relevant_trades)


def estimate_queue_position(
    orderbook: OrderBookSnapshot,
    side: str,
    price: float
) -> Tuple[float, float]:
    """
    Estimate queue position at a given price level.
    
    For a BUY limit order, we look at bids at or above our price.
    For a SELL limit order, we look at asks at or below our price.
    
    Args:
        orderbook: Current orderbook snapshot
        side: "BUY" or "SELL"
        price: Target price level
        
    Returns:
        Tuple of (queue_qty_ahead, price_level_qty)
    """
    if side.upper() == "BUY":
        # For BUY, queue consists of bids >= our price
        queue_ahead = sum(qty for p, qty in orderbook.bids if p >= price)
        level_qty = sum(qty for p, qty in orderbook.bids if abs(p - price) < 0.01)
    else:
        # For SELL, queue consists of asks <= our price
        queue_ahead = sum(qty for p, qty in orderbook.asks if p <= price)
        level_qty = sum(qty for p, qty in orderbook.asks if abs(p - price) < 0.01)
    
    return queue_ahead, level_qty


def calculate_fill_probability(
    queue_ahead: float,
    consumption_rate: float,
    time_window_seconds: float
) -> float:
    """
    Estimate probability of fill within time window.
    
    Uses exponential decay model: P(fill) = 1 - exp(-lambda * t)
    where lambda = consumption_rate / queue_ahead
    
    Args:
        queue_ahead: Quantity ahead in queue
        consumption_rate: Rate of queue consumption (qty/s)
        time_window_seconds: Time window to consider
        
    Returns:
        Probability between 0 and 1
    """
    if queue_ahead <= 0:
        return 1.0  # Already at front of queue
    
    if consumption_rate <= 0:
        return 0.0  # No consumption happening
    
    import math
    
    # Lambda = rate at which our position advances
    lambda_rate = consumption_rate / queue_ahead
    
    # Exponential CDF: P(T <= t) = 1 - exp(-lambda * t)
    prob = 1 - math.exp(-lambda_rate * time_window_seconds)
    
    return min(max(prob, 0.0), 1.0)


def calculate_eta(
    queue_ahead: float,
    consumption_rate: float,
    percentile: float
) -> Optional[float]:
    """
    Calculate estimated time to fill at given percentile.
    
    Uses inverse of exponential CDF.
    
    Args:
        queue_ahead: Quantity ahead in queue
        consumption_rate: Rate of queue consumption (qty/s)
        percentile: Percentile (0.5 for median, 0.95 for 95th)
        
    Returns:
        Time in seconds, or None if cannot be estimated
    """
    if queue_ahead <= 0:
        return 0.0
    
    if consumption_rate <= 0:
        return None  # Cannot estimate
    
    import math
    
    lambda_rate = consumption_rate / queue_ahead
    
    # Inverse CDF: t = -ln(1-p) / lambda
    try:
        eta = -math.log(1 - percentile) / lambda_rate
        return round(eta, 1)
    except (ValueError, ZeroDivisionError):
        return None


def calculate_adverse_selection_score(
    trades: List[TradeRecord],
    orderbook: OrderBookSnapshot,
    side: str,
    price: float,
    lookback_seconds: float = 5.0
) -> Tuple[float, List[str]]:
    """
    Calculate adverse selection risk score.
    
    High score indicates higher risk of adverse selection:
    - Orderflow moving against our position
    - OBI shifts suggesting informed trading
    - Large trades at nearby levels
    
    Args:
        trades: Recent trades
        orderbook: Current orderbook
        side: "BUY" or "SELL" - our order side
        price: Our price level
        lookback_seconds: Short-term window for flow analysis
        
    Returns:
        Tuple of (score 0-100, list of notes)
    """
    notes = []
    score = 0.0
    
    if not trades:
        return 50.0, ["Insufficient trade data"]
    
    cutoff_ms = int((time.time() - lookback_seconds) * 1000)
    recent_trades = [t for t in trades if t.timestamp_ms >= cutoff_ms]
    
    if len(recent_trades) < 5:
        return 50.0, ["Low trade activity"]
    
    # 1. Orderflow imbalance (last N trades)
    buy_vol = sum(t.qty for t in recent_trades if t.side == "buy")
    sell_vol = sum(t.qty for t in recent_trades if t.side == "sell")
    total_vol = buy_vol + sell_vol
    
    if total_vol > 0:
        if side.upper() == "BUY":
            # For buy orders, adverse if more sell flow
            flow_ratio = sell_vol / total_vol
            if flow_ratio > 0.7:
                score += 30
                notes.append("Strong sell flow")
            elif flow_ratio > 0.55:
                score += 15
        else:
            # For sell orders, adverse if more buy flow
            flow_ratio = buy_vol / total_vol
            if flow_ratio > 0.7:
                score += 30
                notes.append("Strong buy flow")
            elif flow_ratio > 0.55:
                score += 15
    
    # 2. OBI analysis
    obi = calculate_obi(orderbook.bids, orderbook.asks, levels=10)
    
    if side.upper() == "BUY":
        # For buy, negative OBI (more asks) is adverse
        if obi < -0.3:
            score += 25
            notes.append("OBI strongly bearish")
        elif obi < -0.1:
            score += 10
    else:
        # For sell, positive OBI (more bids) is adverse
        if obi > 0.3:
            score += 25
            notes.append("OBI strongly bullish")
        elif obi > 0.1:
            score += 10
    
    # 3. Price momentum
    if len(recent_trades) >= 3:
        first_price = recent_trades[0].price
        last_price = recent_trades[-1].price
        price_change_pct = (last_price - first_price) / first_price * 100
        
        if side.upper() == "BUY":
            # For buy, falling prices are adverse (we're buying into weakness)
            if price_change_pct < -0.05:
                score += 20
                notes.append("Price declining")
        else:
            # For sell, rising prices are adverse (we're selling into strength)
            if price_change_pct > 0.05:
                score += 20
                notes.append("Price rising")
    
    # 4. Large trade detection
    avg_size = statistics.mean(t.qty for t in recent_trades) if recent_trades else 0
    large_trades = [t for t in recent_trades if t.qty > avg_size * 3]
    
    if large_trades:
        # Check if large trades are adverse
        adverse_large = sum(1 for t in large_trades 
                          if (side.upper() == "BUY" and t.side == "sell") or
                             (side.upper() == "SELL" and t.side == "buy"))
        if adverse_large > 0:
            score += 15
            notes.append(f"{adverse_large} large adverse trade(s)")
    
    # Cap at 100
    score = min(score, 100.0)
    
    if not notes:
        notes.append("Normal conditions")
    
    return round(score, 1), notes[:2]  # Limit to 2 notes


def calculate_micro_health_score(
    orderbook: OrderBookSnapshot,
    trades: List[TradeRecord],
    lookback_seconds: float
) -> float:
    """
    Calculate market microstructure health score.
    
    High score = healthy market (tight spread, balanced flow, good depth)
    Low score = stressed market (wide spread, imbalanced, thin)
    
    Returns score from 0-100.
    """
    score = 100.0
    
    # 1. Spread analysis (30 points)
    spread_bps = orderbook.spread_bps or 10.0
    if spread_bps > 5.0:
        score -= min(30, spread_bps * 3)
    elif spread_bps > 2.0:
        score -= (spread_bps - 2.0) * 5
    
    # 2. Depth analysis (30 points)
    bid_depth = sum(qty for _, qty in orderbook.bids[:10])
    ask_depth = sum(qty for _, qty in orderbook.asks[:10])
    
    # Rough thresholds for BTC/ETH
    min_healthy_depth = 50.0  # BTC contracts
    
    depth_ratio = min(bid_depth, ask_depth) / min_healthy_depth
    if depth_ratio < 1.0:
        score -= 30 * (1 - depth_ratio)
    
    # 3. Trade flow balance (20 points)
    if trades:
        cutoff_ms = int((time.time() - lookback_seconds) * 1000)
        recent = [t for t in trades if t.timestamp_ms >= cutoff_ms]
        
        if recent:
            buy_vol = sum(t.qty for t in recent if t.side == "buy")
            sell_vol = sum(t.qty for t in recent if t.side == "sell")
            total = buy_vol + sell_vol
            
            if total > 0:
                imbalance = abs(buy_vol - sell_vol) / total
                if imbalance > 0.5:
                    score -= 20 * (imbalance - 0.5) * 2
    
    # 4. OBI stability (20 points)
    obi = abs(calculate_obi(orderbook.bids, orderbook.asks, levels=10))
    if obi > 0.5:
        score -= 20 * (obi - 0.5) * 2
    
    return max(0, min(100, round(score, 1)))


def detect_walls(
    orderbook: OrderBookSnapshot,
    side: str,
    threshold_multiplier: float = 5.0
) -> str:
    """
    Detect large walls that might impact fills.
    
    Returns risk level: "low", "medium", "high"
    """
    if side.upper() == "BUY":
        levels = orderbook.asks[:20]
    else:
        levels = orderbook.bids[:20]
    
    if len(levels) < 5:
        return "low"
    
    avg_size = statistics.mean(qty for _, qty in levels)
    max_size = max(qty for _, qty in levels)
    
    ratio = max_size / avg_size if avg_size > 0 else 1.0
    
    if ratio > threshold_multiplier * 2:
        return "high"
    elif ratio > threshold_multiplier:
        return "medium"
    return "low"


def queue_fill_estimator(
    symbol: str,
    side: str,
    price_levels: List[float],
    qty: float,
    lookback_seconds: float = 30.0
) -> Dict[str, Any]:
    """
    Estimate queue fill probability and ETA for limit orders.
    
    Args:
        symbol: Trading symbol (BTCUSDT, ETHUSDT)
        side: Order side ("BUY" or "SELL")
        price_levels: List of price levels to analyze (max 5)
        qty: Order quantity
        lookback_seconds: Lookback window for trade analysis (default 30s)
        
    Returns:
        Dictionary with per-level metrics and global recommendations.
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
    
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "validation_error", "message": "Side must be BUY or SELL"}
        }
    
    # Limit price levels to 5
    price_levels = price_levels[:5]
    
    if not price_levels:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "validation_error", "message": "At least one price level required"}
        }
    
    # Validate lookback
    lookback_seconds = min(max(lookback_seconds, 5), 300)  # 5s to 5min
    
    # Get market data
    collector = get_market_data_collector()
    
    # Fetch orderbook
    success, orderbook, error = collector.fetch_orderbook(normalized_symbol, limit=100)
    if not success or orderbook is None:
        return {
            "success": False,
            "ts_ms": ts_ms,
            "error": {"type": "data_error", "message": error or "Failed to fetch orderbook"},
            "quality_flags": ["orderbook_unavailable"]
        }
    
    # Ensure trade history
    success, error = collector.ensure_trade_history(normalized_symbol, lookback_seconds)
    if not success:
        logger.warning(f"Could not fetch full trade history: {error}")
    
    # Get trades from buffer
    trades = collector.get_buffered_trades(normalized_symbol, lookback_seconds)
    
    # Quality flags
    quality_flags = []
    if len(trades) < 10:
        quality_flags.append("low_trade_volume")
    
    # Calculate consumption rate for the relevant side
    # For BUY orders, bids get consumed by SELL aggressors
    # For SELL orders, asks get consumed by BUY aggressors
    aggressor_side = "sell" if side == "BUY" else "buy"
    consumption_rate, total_consumed, trade_count = calculate_consumption_rate(
        trades, aggressor_side, lookback_seconds
    )
    
    if consumption_rate <= 0:
        quality_flags.append("zero_consumption")
    
    # Calculate per-level metrics
    per_level = []
    
    for price in price_levels:
        # Estimate queue position
        queue_ahead, level_qty = estimate_queue_position(orderbook, side, price)
        
        # Add our quantity to queue estimate
        total_queue = queue_ahead + qty
        
        # Get mark price for USD value calculation
        mark_success, mark_info, _ = collector.fetch_mark_price(normalized_symbol)
        mark_price = mark_info.mark_price if mark_success and mark_info else price
        
        queue_value_usd = round(total_queue * mark_price, 2)
        
        # Calculate fill probabilities
        fill_prob_30s = calculate_fill_probability(total_queue, consumption_rate, 30)
        fill_prob_60s = calculate_fill_probability(total_queue, consumption_rate, 60)
        
        # Calculate ETAs
        eta_p50 = calculate_eta(total_queue, consumption_rate, 0.5)
        eta_p95 = calculate_eta(total_queue, consumption_rate, 0.95)
        
        # Calculate adverse selection score
        adverse_score, adverse_notes = calculate_adverse_selection_score(
            trades, orderbook, side, price, lookback_seconds=5.0
        )
        
        per_level.append({
            "price": price,
            "queue_qty_est": round(total_queue, 4),
            "queue_value_usd": queue_value_usd,
            "consumption_rate_qty_per_s": round(consumption_rate, 4),
            "eta_p50_s": eta_p50,
            "eta_p95_s": eta_p95,
            "fill_prob_30s": round(fill_prob_30s, 3),
            "fill_prob_60s": round(fill_prob_60s, 3),
            "adverse_selection_score": adverse_score,
            "notes_max2": adverse_notes
        })
    
    # Calculate global metrics
    spread_bps = round(orderbook.spread_bps or 0, 2)
    
    # OBI calculations
    obi_values = []
    for i in range(min(5, len(orderbook.bids))):
        obi_values.append(calculate_obi(
            orderbook.bids[i:i+5], 
            orderbook.asks[i:i+5], 
            levels=5
        ))
    
    obi_mean = round(statistics.mean(obi_values), 3) if obi_values else 0
    obi_stdev = round(statistics.stdev(obi_values), 3) if len(obi_values) > 1 else 0
    
    # Micro health score
    micro_health = calculate_micro_health_score(orderbook, trades, lookback_seconds)
    
    # Wall risk
    wall_risk = detect_walls(orderbook, side)
    
    # Recommendation
    best_level = min(per_level, key=lambda x: (
        -x["fill_prob_60s"] * 0.4 +
        x["adverse_selection_score"] * 0.006
    ))
    best_price = best_level["price"]
    
    # Generate recommendation reason
    if best_level["fill_prob_60s"] > 0.8:
        why = "High fill probability"
    elif best_level["adverse_selection_score"] < 30:
        why = "Low adverse selection risk"
    elif best_level["queue_qty_est"] < per_level[0]["queue_qty_est"] * 0.5:
        why = "Shorter queue"
    else:
        why = "Best risk-adjusted position"
    
    # Build response
    response = {
        "success": True,
        "ts_ms": ts_ms,
        "inputs": {
            "symbol": normalized_symbol,
            "side": side,
            "price_levels": price_levels,
            "qty": qty,
            "lookback_seconds": lookback_seconds
        },
        "per_level": per_level,
        "global": {
            "micro_health_score": micro_health,
            "spread_bps": spread_bps,
            "obi_mean": obi_mean,
            "obi_stdev": obi_stdev,
            "wall_risk_level": wall_risk,
            "recommendation": {
                "best_price": best_price,
                "why": why
            }
        }
    }
    
    if quality_flags:
        response["quality_flags"] = quality_flags
    
    return response
