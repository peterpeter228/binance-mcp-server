"""
Microstructure snapshot tool for compact market structure analysis.

Provides a token-efficient summary of orderbook, trades, and market health
for LLM-based trading systems.
"""

import time
import asyncio
import logging
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor

from binance_mcp_server.futures_config import (
    get_futures_client, 
    ALLOWED_FUTURES_SYMBOLS,
    FuturesClient
)
from binance_mcp_server.futures_utils import (
    validate_futures_symbol,
    get_exchange_info_cache
)
from binance_mcp_server.utils import (
    create_error_response,
    create_success_response
)
from binance_mcp_server.tools.microstructure.calculations import (
    calculate_spread_points,
    calculate_spread_bps,
    calculate_obi,
    calculate_obi_stats,
    calculate_depth_summary,
    identify_walls,
    calculate_persistence_score,
    calculate_taker_imbalance,
    estimate_slippage,
    calculate_micro_health_score,
    calculate_wall_risk_level
)

logger = logging.getLogger(__name__)


# Rate limit tracking
_last_request_time: float = 0
_request_count: int = 0
_REQUEST_LIMIT = 20  # Max requests per window
_REQUEST_WINDOW = 1.0  # Window in seconds


def _rate_limit_check() -> bool:
    """Check and update rate limit state."""
    global _last_request_time, _request_count
    
    current_time = time.time()
    
    # Reset counter if window expired
    if current_time - _last_request_time > _REQUEST_WINDOW:
        _request_count = 0
        _last_request_time = current_time
    
    _request_count += 1
    return _request_count <= _REQUEST_LIMIT


def _fetch_orderbook(
    client: FuturesClient,
    symbol: str,
    limit: int = 100
) -> tuple[bool, Any, Optional[str]]:
    """
    Fetch orderbook from Binance Futures API.
    
    Args:
        client: FuturesClient instance
        symbol: Trading symbol
        limit: Depth limit (5, 10, 20, 50, 100, 500, 1000)
        
    Returns:
        Tuple of (success, data, error_note)
    """
    try:
        success, data = client.get(
            "/fapi/v1/depth",
            params={"symbol": symbol, "limit": limit}
        )
        
        if not success:
            return False, None, f"orderbook_fetch_failed:{data.get('message', 'unknown')}"
        
        # Parse bids and asks
        bids = [
            {"price": float(b[0]), "quantity": float(b[1])}
            for b in data.get("bids", [])
        ]
        asks = [
            {"price": float(a[0]), "quantity": float(a[1])}
            for a in data.get("asks", [])
        ]
        
        return True, {"bids": bids, "asks": asks}, None
        
    except Exception as e:
        logger.error(f"Error fetching orderbook: {e}")
        return False, None, f"orderbook_error:{str(e)[:50]}"


def _fetch_recent_trades(
    client: FuturesClient,
    symbol: str,
    limit: int = 300
) -> tuple[bool, Any, Optional[str]]:
    """
    Fetch recent trades from Binance Futures API.
    
    Args:
        client: FuturesClient instance
        symbol: Trading symbol
        limit: Number of trades to fetch
        
    Returns:
        Tuple of (success, data, error_note)
    """
    try:
        success, data = client.get(
            "/fapi/v1/trades",
            params={"symbol": symbol, "limit": limit}
        )
        
        if not success:
            return False, None, f"trades_fetch_failed:{data.get('message', 'unknown')}"
        
        return True, data, None
        
    except Exception as e:
        logger.error(f"Error fetching trades: {e}")
        return False, None, f"trades_error:{str(e)[:50]}"


def _fetch_ticker(
    client: FuturesClient,
    symbol: str
) -> tuple[bool, Any, Optional[str]]:
    """
    Fetch 24h ticker for current price info.
    
    Args:
        client: FuturesClient instance
        symbol: Trading symbol
        
    Returns:
        Tuple of (success, data, error_note)
    """
    try:
        success, data = client.get(
            "/fapi/v1/ticker/bookTicker",
            params={"symbol": symbol}
        )
        
        if not success:
            return False, None, f"ticker_fetch_failed:{data.get('message', 'unknown')}"
        
        return True, data, None
        
    except Exception as e:
        logger.error(f"Error fetching ticker: {e}")
        return False, None, f"ticker_error:{str(e)[:50]}"


def microstructure_snapshot(
    symbol: str,
    depth_levels: int = 20,
    snapshots: int = 3,
    spacing_ms: int = 2000,
    trades_limit: int = 300
) -> Dict[str, Any]:
    """
    Get a compact microstructure snapshot for a futures symbol.
    
    This tool provides a token-efficient summary of market microstructure
    including orderbook imbalance, wall detection, trade flow, and health metrics.
    
    Output is designed to be <= 2KB for LLM context efficiency.
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        depth_levels: Number of orderbook levels per side (default: 20)
        snapshots: Number of orderbook snapshots for OBI calculation (default: 3)
        spacing_ms: Milliseconds between snapshots (default: 2000)
        trades_limit: Number of recent trades to analyze (default: 300)
        
    Returns:
        Compact dict with microstructure metrics:
        - ts: Timestamp
        - symbol: Trading symbol
        - best_bid, best_ask, mid, tick_size
        - spread_points, spread_bps
        - depth: bid/ask depth summary
        - obi: Order book imbalance stats
        - walls: Top bid/ask walls with persistence
        - trade_flow: Taker buy/sell imbalance
        - slippage_est: Estimated slippage at p50/p95
        - micro_health_score: 0-100 health rating
        - wall_risk_level: low/medium/high
        - notes: Degradation/warning notes
    """
    logger.info(f"microstructure_snapshot called: symbol={symbol}, depth={depth_levels}, snapshots={snapshots}")
    
    notes = []
    
    # Validate symbol
    is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
    if not is_valid:
        return create_error_response("validation_error", error)
    
    symbol = normalized_symbol
    
    # Validate parameters
    if depth_levels < 5 or depth_levels > 100:
        depth_levels = max(5, min(100, depth_levels))
        notes.append(f"depth_clamped_to_{depth_levels}")
    
    if snapshots < 1 or snapshots > 10:
        snapshots = max(1, min(10, snapshots))
        notes.append(f"snapshots_clamped_to_{snapshots}")
    
    if spacing_ms < 100 or spacing_ms > 10000:
        spacing_ms = max(100, min(10000, spacing_ms))
        notes.append(f"spacing_clamped_to_{spacing_ms}ms")
    
    if trades_limit < 50 or trades_limit > 1000:
        trades_limit = max(50, min(1000, trades_limit))
        notes.append(f"trades_limit_clamped_to_{trades_limit}")
    
    # Rate limit check
    if not _rate_limit_check():
        notes.append("rate_limited_burst_mode")
        snapshots = 1  # Reduce to single snapshot in burst mode
    
    try:
        client = get_futures_client()
    except Exception as e:
        return create_error_response("client_error", f"Failed to initialize client: {str(e)}")
    
    # Get exchange info for tick_size
    cache = get_exchange_info_cache()
    symbol_info = cache.get_symbol_info(symbol, client)
    tick_size = float(symbol_info.get("tickSize", 0.01)) if symbol_info else 0.01
    
    # Collect multiple orderbook snapshots
    orderbook_snapshots = []
    obi_values = []
    wall_history_bid = []
    wall_history_ask = []
    
    for i in range(snapshots):
        success, ob_data, ob_note = _fetch_orderbook(client, symbol, min(depth_levels * 2, 100))
        
        if success and ob_data:
            bids = ob_data["bids"]
            asks = ob_data["asks"]
            
            orderbook_snapshots.append(ob_data)
            
            # Calculate OBI for this snapshot
            obi = calculate_obi(bids, asks, depth_levels)
            obi_values.append(obi)
            
            # Identify walls for persistence tracking
            bid_walls = identify_walls(bids[:depth_levels], top_n=3)
            ask_walls = identify_walls(asks[:depth_levels], top_n=3)
            wall_history_bid.append(bid_walls)
            wall_history_ask.append(ask_walls)
            
        elif ob_note:
            notes.append(ob_note)
            if i == 0:
                # First snapshot failed - critical error
                return create_error_response("api_error", f"Failed to fetch orderbook: {ob_note}")
        
        # Wait between snapshots (except last)
        if i < snapshots - 1 and spacing_ms > 0:
            time.sleep(spacing_ms / 1000)
    
    if not orderbook_snapshots:
        return create_error_response("api_error", "No orderbook data available")
    
    # Use last snapshot as current state
    latest_ob = orderbook_snapshots[-1]
    bids = latest_ob["bids"]
    asks = latest_ob["asks"]
    
    # Get current ticker for precise best bid/ask
    success, ticker_data, ticker_note = _fetch_ticker(client, symbol)
    if success and ticker_data:
        best_bid = float(ticker_data.get("bidPrice", bids[0]["price"] if bids else 0))
        best_ask = float(ticker_data.get("askPrice", asks[0]["price"] if asks else 0))
    else:
        best_bid = bids[0]["price"] if bids else 0
        best_ask = asks[0]["price"] if asks else 0
        if ticker_note:
            notes.append(ticker_note)
    
    mid = (best_bid + best_ask) / 2
    
    # Fetch recent trades
    success, trades_data, trades_note = _fetch_recent_trades(client, symbol, trades_limit)
    trades = trades_data if success and trades_data else []
    if trades_note:
        notes.append(trades_note)
    
    # Calculate metrics
    spread_points = calculate_spread_points(best_bid, best_ask)
    spread_bps = calculate_spread_bps(best_bid, best_ask)
    
    # Depth summary
    depth = calculate_depth_summary(bids, asks, mid, depth_levels)
    
    # OBI stats
    obi_stats = calculate_obi_stats(obi_values)
    
    # Walls with persistence
    current_bid_walls = wall_history_bid[-1] if wall_history_bid else []
    current_ask_walls = wall_history_ask[-1] if wall_history_ask else []
    
    # Calculate persistence (using previous snapshots, excluding current)
    if len(wall_history_bid) > 1:
        current_bid_walls = calculate_persistence_score(
            current_bid_walls, 
            wall_history_bid[:-1]
        )
        current_ask_walls = calculate_persistence_score(
            current_ask_walls,
            wall_history_ask[:-1]
        )
    else:
        # Single snapshot - assume full persistence
        for w in current_bid_walls:
            w["persistence_score"] = 1.0
        for w in current_ask_walls:
            w["persistence_score"] = 1.0
    
    # Trade flow
    trade_flow = calculate_taker_imbalance(trades)
    
    # Slippage estimate
    slippage_est = estimate_slippage(bids, asks, trades)
    
    # Calculate average wall persistence for health score
    all_walls = current_bid_walls + current_ask_walls
    wall_persistence_avg = (
        sum(w.get("persistence_score", 1.0) for w in all_walls) / len(all_walls)
        if all_walls else 1.0
    )
    
    # Micro health score
    health_score, health_notes = calculate_micro_health_score(
        spread_bps=spread_bps,
        obi_stdev=obi_stats["stdev"],
        depth_10bps=depth["depth_10bps"],
        taker_imbalance=trade_flow["taker_imbalance"],
        wall_persistence_avg=wall_persistence_avg,
        mid_price=mid
    )
    notes.extend(health_notes)
    
    # Wall risk level
    wall_risk = calculate_wall_risk_level(
        current_bid_walls,
        current_ask_walls,
        mid,
        obi_stats["stdev"]
    )
    
    # Limit notes to 6
    if len(notes) > 6:
        notes = notes[:5] + [f"+{len(notes)-5}_more_notes"]
    
    # Build compact response
    result = {
        "ts": int(time.time() * 1000),
        "symbol": symbol,
        "best_bid": round(best_bid, 8),
        "best_ask": round(best_ask, 8),
        "mid": round(mid, 8),
        "tick_size": tick_size,
        "spread_points": spread_points,
        "spread_bps": spread_bps,
        "depth": depth,
        "obi": obi_stats,
        "walls": {
            "bid": current_bid_walls,
            "ask": current_ask_walls
        },
        "trade_flow": trade_flow,
        "slippage_est": slippage_est,
        "micro_health_score": health_score,
        "wall_risk_level": wall_risk,
        "notes": notes if notes else []
    }
    
    return create_success_response(
        data=result,
        metadata={
            "source": "binance_futures",
            "snapshots_taken": len(orderbook_snapshots),
            "trades_analyzed": len(trades)
        }
    )
