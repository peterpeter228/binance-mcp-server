"""
Core calculations for microstructure analysis.

Provides all the computational logic for OBI, walls, spreads, slippage,
and health scores in a clean, testable manner.
"""

import math
import statistics
from typing import List, Dict, Any, Optional, Tuple
from decimal import Decimal


def calculate_spread_points(best_bid: float, best_ask: float) -> float:
    """
    Calculate spread in price points.
    
    Args:
        best_bid: Best bid price
        best_ask: Best ask price
        
    Returns:
        Spread in price points
    """
    return round(best_ask - best_bid, 8)


def calculate_spread_bps(best_bid: float, best_ask: float) -> float:
    """
    Calculate spread in basis points (bps).
    
    Args:
        best_bid: Best bid price
        best_ask: Best ask price
        
    Returns:
        Spread in basis points
    """
    if best_bid <= 0:
        return 0.0
    mid = (best_bid + best_ask) / 2
    spread = best_ask - best_bid
    return round((spread / mid) * 10000, 2)


def calculate_obi(bids: List[Dict], asks: List[Dict], levels: int = 20) -> float:
    """
    Calculate Order Book Imbalance (OBI).
    
    OBI = (bid_qty - ask_qty) / (bid_qty + ask_qty)
    Range: [-1, 1] where positive = bid pressure, negative = ask pressure
    
    Args:
        bids: List of bid orders with 'quantity' field
        asks: List of ask orders with 'quantity' field
        levels: Number of levels to consider
        
    Returns:
        OBI value between -1 and 1
    """
    bid_qty = sum(float(b.get('quantity', 0)) for b in bids[:levels])
    ask_qty = sum(float(a.get('quantity', 0)) for a in asks[:levels])
    
    total = bid_qty + ask_qty
    if total == 0:
        return 0.0
    
    return round((bid_qty - ask_qty) / total, 4)


def calculate_obi_stats(obi_values: List[float]) -> Dict[str, Any]:
    """
    Calculate OBI statistics across multiple snapshots.
    
    Args:
        obi_values: List of OBI values from multiple snapshots
        
    Returns:
        Dict with snapshots, mean, and stdev
    """
    if not obi_values:
        return {"snapshots": [], "mean": 0.0, "stdev": 0.0}
    
    mean_val = statistics.mean(obi_values)
    stdev_val = statistics.stdev(obi_values) if len(obi_values) > 1 else 0.0
    
    return {
        "snapshots": [round(v, 4) for v in obi_values],
        "mean": round(mean_val, 4),
        "stdev": round(stdev_val, 4)
    }


def calculate_depth_at_bps(
    bids: List[Dict], 
    asks: List[Dict], 
    mid_price: float, 
    bps: int
) -> Dict[str, float]:
    """
    Calculate cumulative depth within X basis points of mid.
    
    Args:
        bids: List of bid orders with 'price' and 'quantity'
        asks: List of ask orders with 'price' and 'quantity'  
        mid_price: Mid price
        bps: Basis points from mid
        
    Returns:
        Dict with bid_depth and ask_depth at the specified bps
    """
    price_range = mid_price * (bps / 10000)
    
    bid_depth = sum(
        float(b.get('quantity', 0)) 
        for b in bids 
        if float(b.get('price', 0)) >= mid_price - price_range
    )
    
    ask_depth = sum(
        float(a.get('quantity', 0))
        for a in asks
        if float(a.get('price', 0)) <= mid_price + price_range
    )
    
    return {
        "bid_depth": round(bid_depth, 4),
        "ask_depth": round(ask_depth, 4),
        "total": round(bid_depth + ask_depth, 4)
    }


def calculate_depth_summary(
    bids: List[Dict],
    asks: List[Dict],
    mid_price: float,
    top_n: int = 20
) -> Dict[str, Any]:
    """
    Calculate comprehensive depth summary.
    
    Args:
        bids: List of bid orders
        asks: List of ask orders
        mid_price: Mid price
        top_n: Number of top levels to sum
        
    Returns:
        Dict with depth metrics
    """
    bid_qty_sum = sum(float(b.get('quantity', 0)) for b in bids[:top_n])
    ask_qty_sum = sum(float(a.get('quantity', 0)) for a in asks[:top_n])
    
    depth_10bps = calculate_depth_at_bps(bids, asks, mid_price, 10)
    depth_20bps = calculate_depth_at_bps(bids, asks, mid_price, 20)
    
    return {
        "bid_qty_sum_topN": round(bid_qty_sum, 4),
        "ask_qty_sum_topN": round(ask_qty_sum, 4),
        "depth_10bps": depth_10bps["total"],
        "depth_20bps": depth_20bps["total"]
    }


def identify_walls(
    orders: List[Dict],
    top_n: int = 3
) -> List[Dict[str, Any]]:
    """
    Identify the top N largest orders (walls) by quantity.
    
    Args:
        orders: List of orders with 'price' and 'quantity'
        top_n: Number of top walls to return
        
    Returns:
        List of wall dicts with price, qty, and size_ratio
    """
    if not orders:
        return []
    
    # Sort by quantity descending
    sorted_orders = sorted(
        orders, 
        key=lambda x: float(x.get('quantity', 0)), 
        reverse=True
    )
    
    # Calculate median quantity for ratio
    quantities = [float(o.get('quantity', 0)) for o in orders if float(o.get('quantity', 0)) > 0]
    median_qty = statistics.median(quantities) if quantities else 1.0
    
    walls = []
    for order in sorted_orders[:top_n]:
        qty = float(order.get('quantity', 0))
        walls.append({
            "price": float(order.get('price', 0)),
            "qty": round(qty, 4),
            "size_ratio_vs_median": round(qty / median_qty, 2) if median_qty > 0 else 0
        })
    
    return walls


def calculate_persistence_score(
    current_walls: List[Dict],
    previous_walls: List[List[Dict]],
    tolerance_pct: float = 0.5
) -> List[Dict[str, Any]]:
    """
    Calculate wall persistence across multiple snapshots.
    
    Persistence = fraction of previous snapshots where a wall existed at similar price.
    
    Args:
        current_walls: Current snapshot walls
        previous_walls: List of wall lists from previous snapshots
        tolerance_pct: Price tolerance percentage for matching walls
        
    Returns:
        Current walls with persistence_score added
    """
    if not previous_walls:
        # No previous data, assume all persistent (1.0)
        for wall in current_walls:
            wall["persistence_score"] = 1.0
        return current_walls
    
    for wall in current_walls:
        price = wall["price"]
        tolerance = price * (tolerance_pct / 100)
        
        appearances = 0
        for snapshot_walls in previous_walls:
            for prev_wall in snapshot_walls:
                prev_price = prev_wall.get("price", 0)
                if abs(prev_price - price) <= tolerance:
                    appearances += 1
                    break
        
        wall["persistence_score"] = round(appearances / len(previous_walls), 2)
    
    return current_walls


def calculate_taker_imbalance(trades: List[Dict]) -> Dict[str, Any]:
    """
    Calculate taker buy/sell imbalance from recent trades.
    
    Args:
        trades: List of trades with 'qty' and 'isBuyerMaker' fields
        
    Returns:
        Dict with buy_qty_sum, sell_qty_sum, taker_imbalance
    """
    buy_qty = 0.0
    sell_qty = 0.0
    
    for trade in trades:
        qty = float(trade.get('qty', 0))
        # isBuyerMaker=true means the buyer was the maker, so the taker was a seller
        is_buyer_maker = trade.get('isBuyerMaker', False)
        
        if is_buyer_maker:
            # Taker was selling (aggressive sell)
            sell_qty += qty
        else:
            # Taker was buying (aggressive buy)
            buy_qty += qty
    
    total = buy_qty + sell_qty
    imbalance = (buy_qty - sell_qty) / total if total > 0 else 0.0
    
    return {
        "buy_qty_sum": round(buy_qty, 4),
        "sell_qty_sum": round(sell_qty, 4),
        "taker_imbalance": round(imbalance, 4)
    }


def estimate_slippage(
    bids: List[Dict],
    asks: List[Dict],
    trades: List[Dict],
    typical_order_size: Optional[float] = None
) -> Dict[str, float]:
    """
    Estimate slippage based on orderbook depth and recent trades.
    
    Uses a simplified model based on:
    1. Average trade size from recent trades
    2. Depth at different price levels
    
    Args:
        bids: Bid orders
        asks: Ask orders
        trades: Recent trades
        typical_order_size: Optional typical order size to estimate for
        
    Returns:
        Dict with p50_points and p95_points slippage estimates
    """
    if not bids or not asks:
        return {"p50_points": 0.0, "p95_points": 0.0}
    
    best_bid = float(bids[0].get('price', 0))
    best_ask = float(asks[0].get('price', 0))
    mid = (best_bid + best_ask) / 2
    
    if mid == 0:
        return {"p50_points": 0.0, "p95_points": 0.0}
    
    # Calculate trade size distribution for estimation
    if trades:
        trade_sizes = [float(t.get('qty', 0)) for t in trades]
        p50_size = statistics.median(trade_sizes) if trade_sizes else 0.1
        p95_size = sorted(trade_sizes)[int(len(trade_sizes) * 0.95)] if len(trade_sizes) > 10 else p50_size * 3
    else:
        p50_size = typical_order_size or 0.1
        p95_size = p50_size * 3
    
    def estimate_slippage_for_size(size: float, side: str) -> float:
        """Estimate slippage for a given order size."""
        orders = asks if side == 'buy' else bids
        reference_price = best_ask if side == 'buy' else best_bid
        
        remaining = size
        total_cost = 0.0
        
        for order in orders:
            order_qty = float(order.get('quantity', 0))
            order_price = float(order.get('price', 0))
            
            filled = min(remaining, order_qty)
            total_cost += filled * order_price
            remaining -= filled
            
            if remaining <= 0:
                break
        
        if size <= 0:
            return 0.0
            
        avg_price = total_cost / size if size > 0 else reference_price
        slippage = abs(avg_price - reference_price)
        
        return slippage
    
    # Calculate for both buy and sell, take average
    p50_buy_slip = estimate_slippage_for_size(p50_size, 'buy')
    p50_sell_slip = estimate_slippage_for_size(p50_size, 'sell')
    p50_points = (p50_buy_slip + p50_sell_slip) / 2
    
    p95_buy_slip = estimate_slippage_for_size(p95_size, 'buy')
    p95_sell_slip = estimate_slippage_for_size(p95_size, 'sell')
    p95_points = (p95_buy_slip + p95_sell_slip) / 2
    
    return {
        "p50_points": round(p50_points, 4),
        "p95_points": round(p95_points, 4)
    }


def calculate_micro_health_score(
    spread_bps: float,
    obi_stdev: float,
    depth_10bps: float,
    taker_imbalance: float,
    wall_persistence_avg: float,
    mid_price: float
) -> Tuple[int, List[str]]:
    """
    Calculate overall microstructure health score (0-100).
    
    Uses explainable weighted rules:
    - Spread quality (30%): Lower spread = higher score
    - OBI stability (20%): Lower stdev = higher score
    - Depth quality (25%): Higher depth relative to price = higher score
    - Trade flow balance (15%): Lower absolute imbalance = higher score
    - Wall persistence (10%): Higher persistence = more reliable walls
    
    Args:
        spread_bps: Spread in basis points
        obi_stdev: Standard deviation of OBI across snapshots
        depth_10bps: Total depth within 10bps of mid
        taker_imbalance: Taker buy/sell imbalance [-1, 1]
        wall_persistence_avg: Average wall persistence score
        mid_price: Mid price for depth normalization
        
    Returns:
        Tuple of (score 0-100, explanations list)
    """
    explanations = []
    scores = {}
    
    # 1. Spread quality (30%)
    # Excellent: < 1bps, Good: < 3bps, Fair: < 5bps, Poor: >= 5bps
    if spread_bps < 1:
        scores['spread'] = 100
        explanations.append(f"spread={spread_bps}bps(excellent)")
    elif spread_bps < 3:
        scores['spread'] = 80
        explanations.append(f"spread={spread_bps}bps(good)")
    elif spread_bps < 5:
        scores['spread'] = 60
        explanations.append(f"spread={spread_bps}bps(fair)")
    elif spread_bps < 10:
        scores['spread'] = 40
        explanations.append(f"spread={spread_bps}bps(wide)")
    else:
        scores['spread'] = 20
        explanations.append(f"spread={spread_bps}bps(poor)")
    
    # 2. OBI stability (20%)
    # Low stdev indicates stable orderbook
    if obi_stdev < 0.05:
        scores['obi_stability'] = 100
    elif obi_stdev < 0.1:
        scores['obi_stability'] = 80
    elif obi_stdev < 0.2:
        scores['obi_stability'] = 60
    else:
        scores['obi_stability'] = 40
        explanations.append(f"obi_volatile(stdev={obi_stdev})")
    
    # 3. Depth quality (25%)
    # Normalize depth by price to compare across symbols
    notional_depth = depth_10bps * mid_price if mid_price > 0 else 0
    if notional_depth > 1_000_000:  # $1M+ depth
        scores['depth'] = 100
    elif notional_depth > 500_000:
        scores['depth'] = 80
    elif notional_depth > 100_000:
        scores['depth'] = 60
    elif notional_depth > 50_000:
        scores['depth'] = 40
    else:
        scores['depth'] = 20
        explanations.append("thin_depth")
    
    # 4. Trade flow balance (15%)
    abs_imbalance = abs(taker_imbalance)
    if abs_imbalance < 0.1:
        scores['flow'] = 100
    elif abs_imbalance < 0.3:
        scores['flow'] = 70
    elif abs_imbalance < 0.5:
        scores['flow'] = 50
    else:
        scores['flow'] = 30
        explanations.append(f"flow_imbalanced({round(taker_imbalance, 2)})")
    
    # 5. Wall persistence (10%)
    scores['persistence'] = int(wall_persistence_avg * 100)
    if wall_persistence_avg < 0.5:
        explanations.append("walls_unstable")
    
    # Calculate weighted score
    total_score = (
        scores['spread'] * 0.30 +
        scores['obi_stability'] * 0.20 +
        scores['depth'] * 0.25 +
        scores['flow'] * 0.15 +
        scores['persistence'] * 0.10
    )
    
    return int(round(total_score)), explanations


def calculate_wall_risk_level(
    walls_bid: List[Dict],
    walls_ask: List[Dict],
    mid_price: float,
    obi_stdev: float
) -> str:
    """
    Calculate wall risk level based on multiple factors.
    
    Factors:
    - Size ratio vs median: Large walls may indicate manipulation
    - Persistence: Low persistence = possible spoofing
    - OBI volatility: High volatility = unstable conditions
    - Distance from mid: Close walls are more impactful
    
    Args:
        walls_bid: Bid wall data with persistence_score
        walls_ask: Ask wall data with persistence_score
        mid_price: Current mid price
        obi_stdev: OBI standard deviation
        
    Returns:
        "low", "medium", or "high"
    """
    risk_factors = 0
    
    all_walls = walls_bid + walls_ask
    
    if not all_walls:
        return "low"
    
    # Check for large size ratios (potential manipulation)
    max_size_ratio = max(w.get("size_ratio_vs_median", 0) for w in all_walls)
    if max_size_ratio > 10:
        risk_factors += 2
    elif max_size_ratio > 5:
        risk_factors += 1
    
    # Check persistence (low = possible spoofing)
    avg_persistence = statistics.mean([w.get("persistence_score", 1.0) for w in all_walls])
    if avg_persistence < 0.3:
        risk_factors += 2
    elif avg_persistence < 0.5:
        risk_factors += 1
    
    # Check OBI volatility
    if obi_stdev > 0.2:
        risk_factors += 2
    elif obi_stdev > 0.1:
        risk_factors += 1
    
    # Check wall proximity to mid
    for wall in all_walls:
        price = wall.get("price", 0)
        distance_pct = abs(price - mid_price) / mid_price * 100 if mid_price > 0 else 100
        if distance_pct < 0.1 and wall.get("size_ratio_vs_median", 0) > 3:
            risk_factors += 1
    
    # Determine risk level
    if risk_factors >= 4:
        return "high"
    elif risk_factors >= 2:
        return "medium"
    else:
        return "low"


def calculate_realized_volatility(
    closes: List[float],
    interval_minutes: int = 1
) -> Dict[str, float]:
    """
    Calculate realized volatility from closing prices.
    
    Uses log returns and annualized standard deviation.
    
    Args:
        closes: List of closing prices
        interval_minutes: Time interval in minutes
        
    Returns:
        Dict with rv, expected_move_points, expected_move_bps
    """
    if len(closes) < 2:
        return {
            "rv": 0.0,
            "expected_move_points": 0.0,
            "expected_move_bps": 0.0,
            "confidence": 0.0
        }
    
    # Calculate log returns
    returns = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            returns.append(math.log(closes[i] / closes[i-1]))
    
    if not returns:
        return {
            "rv": 0.0,
            "expected_move_points": 0.0,
            "expected_move_bps": 0.0,
            "confidence": 0.0
        }
    
    # Calculate standard deviation of returns
    return_stdev = statistics.stdev(returns) if len(returns) > 1 else abs(returns[0])
    
    # Annualization factor (assuming 24/7 trading)
    # Minutes per year = 365.25 * 24 * 60 = 525,960
    intervals_per_year = 525960 / interval_minutes
    annualized_vol = return_stdev * math.sqrt(intervals_per_year)
    
    # Expected move for the horizon (typically 1 hour)
    # Using 1-sigma move for the interval period
    current_price = closes[-1]
    hourly_vol = return_stdev * math.sqrt(60 / interval_minutes)
    expected_move_points = current_price * hourly_vol
    expected_move_bps = hourly_vol * 10000
    
    # Confidence based on sample size
    confidence = min(1.0, len(returns) / 100)
    
    return {
        "rv": round(annualized_vol * 100, 4),  # As percentage
        "expected_move_points": round(expected_move_points, 4),
        "expected_move_bps": round(expected_move_bps, 2),
        "confidence": round(confidence, 2)
    }
