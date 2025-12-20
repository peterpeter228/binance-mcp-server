"""
Queue Fill Estimator for Limit Order Success Rate Improvement.

This tool estimates queue position, consumption rate, fill probability,
and adverse selection risk for limit orders at specified price levels.

Output: JSON <= 2KB with statistical summaries (no raw tick data).
"""

import time
import logging
import statistics
from typing import Dict, Any, Optional, List
from decimal import Decimal

from binance_mcp_server.futures_config import ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import create_error_response, rate_limited, binance_rate_limiter
from binance_mcp_server.tools.futures.microstructure_data import (
    MicrostructureDataFetcher,
    analyze_orderbook_imbalance,
    analyze_trade_flow,
    calculate_queue_position,
    detect_walls,
)

logger = logging.getLogger(__name__)


def _calculate_fill_probability(
    queue_qty: float,
    consumption_rate: float,
    time_horizon_s: float
) -> float:
    """
    Calculate probability of fill within time horizon.
    
    Uses exponential decay model based on consumption rate vs queue size.
    P(fill) = 1 - exp(-consumption_rate * time / queue_qty)
    
    Args:
        queue_qty: Estimated queue ahead of order
        consumption_rate: Rate of consumption (qty per second)
        time_horizon_s: Time horizon in seconds
        
    Returns:
        Probability between 0 and 1
    """
    if queue_qty <= 0:
        return 1.0  # No queue = immediate fill
    
    if consumption_rate <= 0:
        return 0.0  # No consumption = won't fill
    
    import math
    
    # Expected time to fill = queue_qty / consumption_rate
    # Using exponential distribution model
    lambda_rate = consumption_rate / queue_qty
    prob = 1 - math.exp(-lambda_rate * time_horizon_s)
    
    return min(1.0, max(0.0, prob))


def _calculate_eta_percentiles(
    queue_qty: float,
    consumption_rate: float
) -> Dict[str, float]:
    """
    Calculate ETA percentiles for queue fill.
    
    Using inverse of exponential CDF:
    t = -ln(1-p) * queue_qty / consumption_rate
    
    Args:
        queue_qty: Queue quantity ahead
        consumption_rate: Consumption rate (qty/s)
        
    Returns:
        Dict with eta_p50_s and eta_p95_s
    """
    import math
    
    if queue_qty <= 0:
        return {"eta_p50_s": 0.0, "eta_p95_s": 0.0}
    
    if consumption_rate <= 0:
        return {"eta_p50_s": float("inf"), "eta_p95_s": float("inf")}
    
    # ETA = -ln(1-p) * queue_qty / consumption_rate
    eta_p50 = -math.log(1 - 0.5) * queue_qty / consumption_rate
    eta_p95 = -math.log(1 - 0.95) * queue_qty / consumption_rate
    
    return {
        "eta_p50_s": round(min(eta_p50, 3600), 1),  # Cap at 1 hour
        "eta_p95_s": round(min(eta_p95, 3600), 1)
    }


def _calculate_adverse_selection_score(
    side: str,
    obi_mean: float,
    flow_imbalance: float,
    price: float,
    mid_price: float
) -> Dict[str, Any]:
    """
    Calculate adverse selection risk score.
    
    Adverse selection occurs when:
    - For BUY limit: orderflow turning negative (sell pressure increasing)
    - For SELL limit: orderflow turning positive (buy pressure increasing)
    
    Also considers how far price is from mid.
    
    Args:
        side: Order side (BUY/SELL)
        obi_mean: Order book imbalance (-1 to 1)
        flow_imbalance: Trade flow imbalance (-1 to 1)
        price: Order price
        mid_price: Current mid price
        
    Returns:
        Dict with score (0-100) and notes
    """
    score = 50  # Base score (neutral)
    notes = []
    
    side = side.upper()
    
    # Distance from mid price (basis points)
    if mid_price > 0:
        distance_bps = abs((price - mid_price) / mid_price) * 10000
    else:
        distance_bps = 0
    
    if side == "BUY":
        # For buy limit orders:
        # - Negative OBI (sell pressure) = adverse
        # - Negative flow imbalance (sell aggression) = adverse
        # - Price far above mid = more adverse
        
        if obi_mean < -0.2:
            score += 15
            notes.append("OBI shows sell pressure")
        elif obi_mean > 0.2:
            score -= 10
            notes.append("OBI favors")
        
        if flow_imbalance < -0.2:
            score += 20
            notes.append("Sell aggression high")
        elif flow_imbalance > 0.2:
            score -= 10
        
        if price > mid_price:
            score += min(distance_bps / 5, 20)  # Penalty for buying above mid
        else:
            score -= min(distance_bps / 10, 15)  # Bonus for buying below mid
            
    else:  # SELL
        # For sell limit orders:
        # - Positive OBI (buy pressure) = adverse
        # - Positive flow imbalance (buy aggression) = adverse
        # - Price far below mid = more adverse
        
        if obi_mean > 0.2:
            score += 15
            notes.append("OBI shows buy pressure")
        elif obi_mean < -0.2:
            score -= 10
            notes.append("OBI favors")
        
        if flow_imbalance > 0.2:
            score += 20
            notes.append("Buy aggression high")
        elif flow_imbalance < -0.2:
            score -= 10
        
        if price < mid_price:
            score += min(distance_bps / 5, 20)  # Penalty for selling below mid
        else:
            score -= min(distance_bps / 10, 15)  # Bonus for selling above mid
    
    # Normalize to 0-100
    score = max(0, min(100, score))
    
    return {
        "score": round(score),
        "notes": notes[:2] if notes else ["No significant adverse signals"]
    }


def _assess_micro_health(
    obi_metrics: Dict[str, Any],
    trade_flow: Dict[str, Any],
    wall_info: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Calculate overall microstructure health score.
    
    Considers:
    - Spread tightness
    - Order book balance
    - Trade flow consistency
    - Wall presence
    
    Returns:
        Dict with score (0-100) and components
    """
    score = 50  # Base score
    
    # Spread component (tighter = healthier)
    spread_bps = obi_metrics.get("spread_bps", 10)
    if spread_bps < 2:
        score += 15
    elif spread_bps < 5:
        score += 10
    elif spread_bps > 15:
        score -= 15
    elif spread_bps > 10:
        score -= 10
    
    # OBI component (more balanced = healthier)
    obi = abs(obi_metrics.get("obi_mean", 0))
    if obi < 0.1:
        score += 15  # Very balanced
    elif obi < 0.3:
        score += 5
    elif obi > 0.6:
        score -= 15  # Very imbalanced
    elif obi > 0.4:
        score -= 10
    
    # Trade flow component
    flow_imbalance = abs(trade_flow.get("flow_imbalance", 0))
    trade_count = trade_flow.get("trade_count", 0)
    
    if trade_count > 50 and flow_imbalance < 0.2:
        score += 10  # Active and balanced
    elif trade_count < 10:
        score -= 10  # Low activity
    
    # Wall component
    wall_risk = wall_info.get("wall_risk_level", 0)
    if wall_risk >= 3:
        score -= 15
    elif wall_risk == 2:
        score -= 10
    elif wall_risk == 0:
        score += 5
    
    return {
        "score": max(0, min(100, round(score))),
        "spread_health": "good" if spread_bps < 5 else ("fair" if spread_bps < 10 else "poor"),
        "balance_health": "good" if obi < 0.2 else ("fair" if obi < 0.4 else "poor"),
        "activity_health": "good" if trade_count > 50 else ("fair" if trade_count > 20 else "low")
    }


@rate_limited(binance_rate_limiter)
def queue_fill_estimator(
    symbol: str,
    side: str,
    price_levels: List[float],
    qty: float,
    lookback_seconds: int = 30
) -> Dict[str, Any]:
    """
    Estimate queue position and fill probability for limit orders.
    
    Analyzes orderbook depth and recent trade flow to estimate:
    - Queue position at each price level
    - Expected time to fill (ETA) at p50 and p95
    - Fill probability within 30s and 60s
    - Adverse selection risk score
    
    Args:
        symbol: Trading pair (BTCUSDT or ETHUSDT)
        side: Order side (BUY or SELL)
        price_levels: List of up to 5 price levels to analyze
        qty: Order quantity
        lookback_seconds: Lookback period for trade analysis (10-120)
        
    Returns:
        Dict with per-level analysis and global recommendations
        
    Example Response:
        {
            "ts_ms": 1234567890123,
            "inputs": {"symbol": "BTCUSDT", "side": "BUY", ...},
            "per_level": [
                {
                    "price": 50000.0,
                    "queue_qty_est": 12.5,
                    "queue_value_usd": 625000,
                    "consumption_rate_qty_per_s": 0.5,
                    "eta_p50_s": 12.5,
                    "eta_p95_s": 37.4,
                    "fill_prob_30s": 0.65,
                    "fill_prob_60s": 0.88,
                    "adverse_selection_score": 35,
                    "notes_max2": ["OBI favors"]
                }
            ],
            "global": {
                "micro_health_score": 72,
                "spread_bps": 2.5,
                "obi_mean": 0.15,
                "obi_stdev": 0.08,
                "wall_risk_level": 1,
                "recommendation": {"best_price": 50000.0, "why": "Best ETA/risk ratio"}
            },
            "quality_flags": []
        }
    """
    ts_ms = int(time.time() * 1000)
    
    # Input validation
    is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
    if not is_valid:
        return create_error_response("validation_error", error)
    
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return create_error_response("validation_error", "Side must be BUY or SELL")
    
    if not price_levels or len(price_levels) > 5:
        return create_error_response("validation_error", "price_levels must have 1-5 prices")
    
    if qty <= 0:
        return create_error_response("validation_error", "qty must be positive")
    
    lookback_seconds = max(10, min(120, lookback_seconds))
    lookback_ms = lookback_seconds * 1000
    
    quality_flags = []
    
    try:
        # Fetch data
        fetcher = MicrostructureDataFetcher(normalized_symbol)
        
        orderbook = fetcher.fetch_orderbook(limit=500)
        if not orderbook.get("success"):
            return create_error_response("data_error", 
                f"Failed to fetch orderbook: {orderbook.get('error', 'Unknown')}")
        
        trades_data = fetcher.fetch_agg_trades(lookback_ms)
        if not trades_data.get("success"):
            quality_flags.append("TRADE_DATA_DEGRADED")
            trades = []
        else:
            trades = trades_data.get("trades", [])
        
        mark_data = fetcher.fetch_mark_price()
        
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        
        if len(bids) < 10 or len(asks) < 10:
            quality_flags.append("SHALLOW_ORDERBOOK")
        
        if len(trades) < 10:
            quality_flags.append("LOW_TRADE_ACTIVITY")
        
        # Analyze orderbook
        obi_metrics = analyze_orderbook_imbalance(bids, asks, depth_levels=20)
        
        # Analyze trade flow
        trade_flow = analyze_trade_flow(trades, lookback_ms)
        
        # Detect walls
        wall_info = detect_walls(bids, asks, depth_levels=30, wall_threshold_multiplier=3.0)
        
        # Get mid price
        mid_price = obi_metrics.get("mid_price", 0)
        
        # Calculate consumption rate for the relevant side
        if side == "BUY":
            # Buy limit orders fill when sell aggressors hit the bid
            consumption_rate = trade_flow.get("sell_consumption_rate", 0)
        else:
            # Sell limit orders fill when buy aggressors lift the ask
            consumption_rate = trade_flow.get("buy_consumption_rate", 0)
        
        # Analyze each price level
        per_level = []
        for price in price_levels:
            price_dec = Decimal(str(price))
            
            # Calculate queue position
            queue_info = calculate_queue_position(bids, asks, price_dec, side)
            queue_qty = queue_info.get("queue_qty", 0)
            
            # Calculate queue value in USD
            queue_value_usd = queue_qty * float(price_dec)
            
            # Calculate ETAs
            eta_info = _calculate_eta_percentiles(queue_qty, consumption_rate)
            
            # Calculate fill probabilities
            fill_prob_30s = _calculate_fill_probability(queue_qty, consumption_rate, 30)
            fill_prob_60s = _calculate_fill_probability(queue_qty, consumption_rate, 60)
            
            # Calculate adverse selection
            adverse = _calculate_adverse_selection_score(
                side,
                obi_metrics.get("obi_mean", 0),
                trade_flow.get("flow_imbalance", 0),
                float(price_dec),
                mid_price
            )
            
            per_level.append({
                "price": float(price_dec),
                "queue_qty_est": round(queue_qty, 4),
                "queue_value_usd": round(queue_value_usd, 2),
                "consumption_rate_qty_per_s": round(consumption_rate, 6),
                "eta_p50_s": eta_info["eta_p50_s"],
                "eta_p95_s": eta_info["eta_p95_s"],
                "fill_prob_30s": round(fill_prob_30s, 3),
                "fill_prob_60s": round(fill_prob_60s, 3),
                "adverse_selection_score": adverse["score"],
                "notes_max2": adverse["notes"]
            })
        
        # Calculate micro health score
        micro_health = _assess_micro_health(obi_metrics, trade_flow, wall_info)
        
        # Calculate OBI stdev if we have multiple levels
        obi_values = [obi_metrics.get(f"obi_l{i}", 0) for i in range(1, 11) 
                      if obi_metrics.get(f"obi_l{i}") is not None]
        obi_stdev = round(statistics.stdev(obi_values), 4) if len(obi_values) > 1 else 0.0
        
        # Generate recommendation
        if per_level:
            # Find best price based on fill_prob_60s / adverse_selection_score ratio
            best_level = max(per_level, 
                            key=lambda x: x["fill_prob_60s"] / max(x["adverse_selection_score"], 1))
            recommendation = {
                "best_price": best_level["price"],
                "why": f"Best fill probability ({best_level['fill_prob_60s']:.0%}) with acceptable adverse selection ({best_level['adverse_selection_score']})"
            }
        else:
            recommendation = {"best_price": None, "why": "No valid levels analyzed"}
        
        return {
            "ts_ms": ts_ms,
            "inputs": {
                "symbol": normalized_symbol,
                "side": side,
                "price_levels": [float(p) for p in price_levels],
                "qty": qty,
                "lookback_seconds": lookback_seconds
            },
            "per_level": per_level,
            "global": {
                "micro_health_score": micro_health["score"],
                "spread_bps": obi_metrics.get("spread_bps", 0),
                "obi_mean": obi_metrics.get("obi_mean", 0),
                "obi_stdev": obi_stdev,
                "wall_risk_level": wall_info.get("wall_risk_level", 0),
                "recommendation": recommendation
            },
            "quality_flags": quality_flags
        }
        
    except Exception as e:
        logger.error(f"Error in queue_fill_estimator: {e}")
        return create_error_response("tool_error", f"Queue estimation failed: {str(e)}")
