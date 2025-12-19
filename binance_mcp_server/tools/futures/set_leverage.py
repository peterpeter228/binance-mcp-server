"""
Set Leverage for USDⓈ-M Futures.

Corresponds to Binance API: POST /fapi/v1/leverage
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


@rate_limited(binance_rate_limiter)
def set_leverage(symbol: str, leverage: int) -> Dict[str, Any]:
    """
    Set leverage for a USDⓈ-M Futures symbol.
    
    This tool changes the leverage multiplier for a symbol. The operation
    is idempotent - if the leverage is already set to the requested value,
    it returns success with already_set=true.
    
    Corresponds to: POST /fapi/v1/leverage (TRADE, signed)
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        leverage: Target leverage (1-125 for BTC, 1-100 for ETH typically)
        
    Returns:
        Dict containing:
        - success (bool): Whether the operation was successful
        - data (dict): Leverage information
        - already_set (bool): True if leverage was already at requested value
        - raw_response (dict): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "symbol": "BTCUSDT",
                "leverage": 10,
                "maxNotionalValue": "100000000"
            },
            "already_set": false,
            "raw_response": {...},
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Setting leverage for {symbol} to {leverage}x")
    
    try:
        # Validate symbol
        is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
        if not is_valid:
            return create_error_response("validation_error", error)
        
        # Validate leverage
        if not isinstance(leverage, int) or leverage < 1:
            return create_error_response(
                "validation_error", 
                f"Leverage must be a positive integer, got: {leverage}"
            )
        
        if leverage > 125:
            return create_error_response(
                "validation_error",
                f"Leverage {leverage} exceeds maximum allowed (125)"
            )
        
        client = get_futures_client()
        
        # First, check current leverage via position risk
        already_set = False
        current_leverage = None
        
        check_success, check_data = client.get(
            "/fapi/v2/positionRisk",
            {"symbol": normalized_symbol},
            signed=True
        )
        
        if check_success and isinstance(check_data, list) and check_data:
            for pos in check_data:
                if pos.get("symbol") == normalized_symbol:
                    current_leverage = int(pos.get("leverage", 0))
                    if current_leverage == leverage:
                        already_set = True
                    break
        
        # Set leverage
        success, data = client.post(
            "/fapi/v1/leverage",
            {
                "symbol": normalized_symbol,
                "leverage": leverage
            }
        )
        
        if not success:
            error_code = data.get("code")
            error_msg = data.get("message", "Failed to set leverage")
            
            # Handle "no need to change" error as success
            if error_code == -4046 or "No need to change" in str(error_msg):
                return {
                    "success": True,
                    "data": {
                        "symbol": normalized_symbol,
                        "leverage": leverage,
                        "maxNotionalValue": None  # Unknown in this case
                    },
                    "already_set": True,
                    "message": "Leverage already set to requested value",
                    "raw_response": data,
                    "timestamp": int(time.time() * 1000)
                }
            
            logger.error(f"Set leverage error: {error_msg}")
            return create_error_response("api_error", error_msg, {"code": error_code})
        
        # Build response
        return {
            "success": True,
            "data": {
                "symbol": data.get("symbol", normalized_symbol),
                "leverage": data.get("leverage", leverage),
                "maxNotionalValue": data.get("maxNotionalValue")
            },
            "already_set": already_set,
            "previous_leverage": current_leverage,
            "raw_response": data,
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in set_leverage: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
