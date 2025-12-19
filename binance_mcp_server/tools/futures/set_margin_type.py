"""
Set Margin Type for USDⓈ-M Futures.

Corresponds to Binance API: POST /fapi/v1/marginType
"""

import logging
import time
from typing import Dict, Any

from binance_mcp_server.futures_config import get_futures_client
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


VALID_MARGIN_TYPES = ["ISOLATED", "CROSSED"]


@rate_limited(binance_rate_limiter)
def set_margin_type(symbol: str, margin_type: str) -> Dict[str, Any]:
    """
    Set margin type for a USDⓈ-M Futures symbol.
    
    This tool changes between isolated and cross margin modes. The operation
    is idempotent - if margin type is already set to the requested value,
    it returns success with already_set=true.
    
    Note: You cannot change margin type when you have open positions or
    open orders on that symbol.
    
    Corresponds to: POST /fapi/v1/marginType (TRADE, signed)
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        margin_type: "ISOLATED" or "CROSSED"
        
    Returns:
        Dict containing:
        - success (bool): Whether the operation was successful
        - data (dict): Margin type information
        - already_set (bool): True if margin type was already at requested value
        - raw_response (dict): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "symbol": "BTCUSDT",
                "marginType": "ISOLATED"
            },
            "already_set": false,
            "raw_response": {...},
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Setting margin type for {symbol} to {margin_type}")
    
    try:
        # Validate symbol
        is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
        if not is_valid:
            return create_error_response("validation_error", error)
        
        # Validate margin type
        margin_type = margin_type.upper().strip()
        if margin_type not in VALID_MARGIN_TYPES:
            return create_error_response(
                "validation_error",
                f"Invalid margin type: {margin_type}. Must be one of: {', '.join(VALID_MARGIN_TYPES)}"
            )
        
        client = get_futures_client()
        
        # Check current margin type via position risk
        current_margin_type = None
        check_success, check_data = client.get(
            "/fapi/v2/positionRisk",
            {"symbol": normalized_symbol},
            signed=True
        )
        
        if check_success and isinstance(check_data, list) and check_data:
            for pos in check_data:
                if pos.get("symbol") == normalized_symbol:
                    current_margin_type = pos.get("marginType", "").upper()
                    # Normalize: API returns "cross" but we send "CROSSED"
                    if current_margin_type == "CROSS":
                        current_margin_type = "CROSSED"
                    elif current_margin_type == "ISOLATED":
                        current_margin_type = "ISOLATED"
                    break
        
        # Set margin type
        success, data = client.post(
            "/fapi/v1/marginType",
            {
                "symbol": normalized_symbol,
                "marginType": margin_type
            }
        )
        
        if not success:
            error_code = data.get("code")
            error_msg = data.get("message", "Failed to set margin type")
            
            # Handle "no need to change" error as success
            # Error -4046: No need to change margin type
            if error_code == -4046 or "No need to change" in str(error_msg):
                return {
                    "success": True,
                    "data": {
                        "symbol": normalized_symbol,
                        "marginType": margin_type
                    },
                    "already_set": True,
                    "message": "Margin type already set to requested value",
                    "raw_response": data,
                    "timestamp": int(time.time() * 1000)
                }
            
            # Handle position exists error
            if error_code == -4048 or "position" in str(error_msg).lower():
                return create_error_response(
                    "position_exists",
                    "Cannot change margin type with open position. Close position first.",
                    {"code": error_code, "raw_message": error_msg}
                )
            
            logger.error(f"Set margin type error: {error_msg}")
            return create_error_response("api_error", error_msg, {"code": error_code})
        
        # Determine if it was already set
        already_set = False
        if current_margin_type:
            # Normalize for comparison
            current_normalized = current_margin_type
            if current_normalized == "CROSS":
                current_normalized = "CROSSED"
            already_set = current_normalized == margin_type
        
        # Build response
        return {
            "success": True,
            "data": {
                "symbol": normalized_symbol,
                "marginType": margin_type
            },
            "already_set": already_set,
            "previous_marginType": current_margin_type,
            "raw_response": data,
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in set_margin_type: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
