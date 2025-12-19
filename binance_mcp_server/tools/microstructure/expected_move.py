"""
Expected move calculation tool based on realized volatility.

Provides compact volatility estimates for LLM-based trading systems
without returning raw OHLCV data.
"""

import time
import logging
from typing import Dict, Any, Optional

from binance_mcp_server.futures_config import (
    get_futures_client,
    FuturesClient
)
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import (
    create_error_response,
    create_success_response
)
from binance_mcp_server.tools.microstructure.calculations import (
    calculate_realized_volatility
)

logger = logging.getLogger(__name__)


# Valid intervals mapping to minutes
VALID_INTERVALS = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240
}


def _fetch_klines(
    client: FuturesClient,
    symbol: str,
    interval: str,
    limit: int
) -> tuple[bool, Any, Optional[str]]:
    """
    Fetch OHLCV klines from Binance Futures API.
    
    Args:
        client: FuturesClient instance
        symbol: Trading symbol
        interval: Kline interval (1m, 5m, 15m, 1h, etc.)
        limit: Number of klines to fetch
        
    Returns:
        Tuple of (success, data, error_note)
    """
    try:
        success, data = client.get(
            "/fapi/v1/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
        )
        
        if not success:
            return False, None, f"klines_fetch_failed:{data.get('message', 'unknown')}"
        
        return True, data, None
        
    except Exception as e:
        logger.error(f"Error fetching klines: {e}")
        return False, None, f"klines_error:{str(e)[:50]}"


def expected_move(
    symbol: str,
    horizon_minutes: int = 60,
    interval: str = "1m",
    lookback: int = 240
) -> Dict[str, Any]:
    """
    Calculate expected price move based on realized volatility.
    
    Uses historical OHLCV data to compute realized volatility and
    expected move in points and basis points. Does NOT return raw kline data.
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        horizon_minutes: Time horizon in minutes for expected move (default: 60)
        interval: Kline interval for calculation (default: "1m")
            Valid: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h
        lookback: Number of klines to use for calculation (default: 240)
        
    Returns:
        Dict with:
        - rv: Annualized realized volatility (%)
        - expected_move_points: Expected move in price points
        - expected_move_bps: Expected move in basis points
        - confidence: Confidence score based on sample size
        - current_price: Latest price
        - horizon_minutes: Time horizon used
        - notes: Any warnings or adjustments
    """
    logger.info(f"expected_move called: symbol={symbol}, horizon={horizon_minutes}m, interval={interval}")
    
    notes = []
    
    # Validate symbol
    is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
    if not is_valid:
        return create_error_response("validation_error", error)
    
    symbol = normalized_symbol
    
    # Validate interval
    if interval not in VALID_INTERVALS:
        valid = ", ".join(VALID_INTERVALS.keys())
        return create_error_response(
            "validation_error", 
            f"Invalid interval '{interval}'. Valid: {valid}"
        )
    
    interval_minutes = VALID_INTERVALS[interval]
    
    # Validate lookback
    if lookback < 30:
        lookback = 30
        notes.append("lookback_min_30")
    elif lookback > 1000:
        lookback = 1000
        notes.append("lookback_max_1000")
    
    # Validate horizon
    if horizon_minutes < 1:
        horizon_minutes = 1
        notes.append("horizon_min_1m")
    elif horizon_minutes > 1440:  # 24 hours
        horizon_minutes = 1440
        notes.append("horizon_max_1440m")
    
    try:
        client = get_futures_client()
    except Exception as e:
        return create_error_response("client_error", f"Failed to initialize client: {str(e)}")
    
    # Fetch klines
    success, klines_data, klines_note = _fetch_klines(client, symbol, interval, lookback)
    
    if not success or not klines_data:
        return create_error_response(
            "api_error",
            klines_note or "Failed to fetch klines data"
        )
    
    # Extract closing prices
    # Kline format: [open_time, open, high, low, close, volume, close_time, ...]
    closes = []
    for kline in klines_data:
        try:
            close_price = float(kline[4])
            closes.append(close_price)
        except (IndexError, ValueError):
            continue
    
    if len(closes) < 30:
        notes.append(f"limited_data_points_{len(closes)}")
        if len(closes) < 10:
            return create_error_response(
                "data_error",
                f"Insufficient data: only {len(closes)} valid candles"
            )
    
    # Calculate base volatility
    vol_stats = calculate_realized_volatility(closes, interval_minutes)
    
    # Scale expected move to requested horizon
    # Vol scales with sqrt of time
    current_price = closes[-1]
    
    # Calculate hourly volatility from the base interval
    base_hourly_vol_decimal = vol_stats["expected_move_bps"] / 10000  # Convert bps to decimal
    
    # Scale to requested horizon
    horizon_scale = (horizon_minutes / 60) ** 0.5
    horizon_vol_decimal = base_hourly_vol_decimal * horizon_scale
    
    expected_move_points = current_price * horizon_vol_decimal
    expected_move_bps = horizon_vol_decimal * 10000
    
    # Calculate confidence based on data quality
    data_confidence = min(1.0, len(closes) / lookback)
    time_confidence = 1.0 if horizon_minutes <= 240 else 0.8  # Less confident for longer horizons
    overall_confidence = round(data_confidence * time_confidence * vol_stats["confidence"], 2)
    
    # Add notes based on conditions
    rv = vol_stats["rv"]
    if rv > 100:
        notes.append("high_volatility_regime")
    elif rv < 10:
        notes.append("low_volatility_regime")
    
    if abs(closes[-1] - closes[0]) / closes[0] > 0.02:
        notes.append("trending_market")
    
    # Build response
    result = {
        "ts": int(time.time() * 1000),
        "symbol": symbol,
        "rv": rv,  # Annualized realized volatility %
        "expected_move_points": round(expected_move_points, 4),
        "expected_move_bps": round(expected_move_bps, 2),
        "confidence": overall_confidence,
        "current_price": current_price,
        "horizon_minutes": horizon_minutes,
        "interval_used": interval,
        "candles_analyzed": len(closes),
        "notes": notes if notes else []
    }
    
    return create_success_response(
        data=result,
        metadata={
            "source": "binance_futures",
            "calculation_method": "log_returns_std",
            "annualization": "525960_minutes_per_year"
        }
    )
