"""
Get Commission Rate for USDⓈ-M Futures.

Corresponds to Binance API: GET /fapi/v1/commissionRate (USER_DATA)
"""

import logging
import time
from typing import Dict, Any

from binance_mcp_server.futures_config import get_futures_client
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import (
    create_error_response,
    create_success_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


@rate_limited(binance_rate_limiter)
def get_commission_rate_futures(symbol: str) -> Dict[str, Any]:
    """
    Get user's commission rate for a USDⓈ-M Futures symbol.
    
    This tool retrieves the maker and taker commission rates for the
    authenticated user. Essential for calculating trading costs accurately.
    
    Corresponds to: GET /fapi/v1/commissionRate (USER_DATA, signed)
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        
    Returns:
        Dict containing:
        - success (bool): Whether the request was successful
        - data (dict): Commission rate information with normalized fields
        - raw_response (dict): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "symbol": "BTCUSDT",
                "makerCommissionRate": "0.0002",
                "makerCommissionRate_float": 0.0002,
                "makerCommissionRate_percent": "0.02%",
                "takerCommissionRate": "0.0004",
                "takerCommissionRate_float": 0.0004,
                "takerCommissionRate_percent": "0.04%"
            },
            "raw_response": {...},
            "serverTime": 1234567890123
        }
    """
    logger.info(f"Getting commission rate for futures symbol: {symbol}")
    
    try:
        # Validate symbol
        is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
        if not is_valid:
            return create_error_response("validation_error", error)
        
        client = get_futures_client()
        
        # Fetch commission rate (signed endpoint)
        success, data = client.get(
            "/fapi/v1/commissionRate",
            {"symbol": normalized_symbol},
            signed=True
        )
        
        if not success:
            error_msg = data.get("message", "Failed to fetch commission rate")
            logger.error(f"Commission rate error: {error_msg}")
            return create_error_response("api_error", error_msg, {"code": data.get("code")})
        
        # Parse commission rates
        maker_rate = data.get("makerCommissionRate", "0")
        taker_rate = data.get("takerCommissionRate", "0")
        
        # Convert to float for convenience
        maker_float = float(maker_rate)
        taker_float = float(taker_rate)
        
        # Build normalized response
        normalized = {
            "symbol": data.get("symbol", normalized_symbol),
            "makerCommissionRate": maker_rate,
            "makerCommissionRate_float": maker_float,
            "makerCommissionRate_percent": f"{maker_float * 100:.4f}%",
            "takerCommissionRate": taker_rate,
            "takerCommissionRate_float": taker_float,
            "takerCommissionRate_percent": f"{taker_float * 100:.4f}%",
        }
        
        # Get server time for reference
        time_success, time_data = client.get("/fapi/v1/time")
        server_time = time_data.get("serverTime") if time_success else int(time.time() * 1000)
        
        return {
            "success": True,
            "data": normalized,
            "raw_response": data,
            "serverTime": server_time,
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in get_commission_rate_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
