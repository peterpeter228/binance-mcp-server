"""
Get Position Risk for USDⓈ-M Futures.

Corresponds to Binance API: GET /fapi/v2/positionRisk or /fapi/v3/positionRisk
"""

import logging
import time
from typing import Dict, Any, Optional, List

from binance_mcp_server.futures_config import get_futures_client, ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


@rate_limited(binance_rate_limiter)
def get_position_risk(symbol: Optional[str] = None) -> Dict[str, Any]:
    """
    Get position risk information for USDⓈ-M Futures.
    
    This tool retrieves comprehensive position information including position size,
    entry price, mark price, liquidation price, leverage, margin, and unrealized PnL.
    
    Corresponds to: GET /fapi/v2/positionRisk (USER_DATA, signed)
    
    Args:
        symbol: Optional trading pair symbol (BTCUSDT or ETHUSDT).
                If not provided, returns positions for all allowed symbols.
        
    Returns:
        Dict containing:
        - success (bool): Whether the request was successful
        - data (list): Position information for requested symbol(s)
        - normalized_fields (dict): Easy-access summary of key fields
        - raw_response (list): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.100",
                    "entryPrice": "50000.0",
                    "markPrice": "51000.0",
                    "unRealizedProfit": "100.0",
                    "liquidationPrice": "45000.0",
                    "leverage": "10",
                    "maxNotionalValue": "1000000",
                    "marginType": "cross",
                    "isolatedMargin": "0",
                    "isAutoAddMargin": "false",
                    "positionSide": "BOTH",
                    "notional": "5100.0",
                    "isolatedWallet": "0",
                    "updateTime": 1234567890123
                }
            ],
            "normalized_fields": {
                "BTCUSDT": {
                    "positionAmt_float": 0.1,
                    "entryPrice_float": 50000.0,
                    "markPrice_float": 51000.0,
                    "liquidationPrice_float": 45000.0,
                    "leverage_int": 10,
                    "unrealizedPnl_float": 100.0,
                    "marginType": "cross",
                    "positionSide": "BOTH",
                    "isLong": true,
                    "hasPosition": true
                }
            },
            "raw_response": [...],
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Getting position risk for futures symbol: {symbol if symbol else 'all allowed'}")
    
    try:
        # Validate symbol if provided
        params = {}
        if symbol:
            is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
            if not is_valid:
                return create_error_response("validation_error", error)
            params["symbol"] = normalized_symbol
        
        client = get_futures_client()
        
        # Try v2 first, fallback to v3 if needed
        success, data = client.get("/fapi/v2/positionRisk", params, signed=True)
        
        if not success:
            # Try v3
            success, data = client.get("/fapi/v3/positionRisk", params, signed=True)
            
            if not success:
                error_msg = data.get("message", "Failed to fetch position risk")
                logger.error(f"Position risk error: {error_msg}")
                return create_error_response("api_error", error_msg, {"code": data.get("code")})
        
        # Ensure data is a list
        if not isinstance(data, list):
            data = [data]
        
        # Filter to allowed symbols only
        filtered_data = [
            pos for pos in data 
            if pos.get("symbol") in ALLOWED_FUTURES_SYMBOLS
        ]
        
        # If specific symbol requested but not in filtered data
        if symbol and normalized_symbol:
            filtered_data = [
                pos for pos in filtered_data 
                if pos.get("symbol") == normalized_symbol
            ]
        
        # Build normalized fields for easy access
        normalized_fields = {}
        for pos in filtered_data:
            sym = pos.get("symbol")
            position_amt = float(pos.get("positionAmt", 0))
            
            normalized_fields[sym] = {
                "positionAmt_float": position_amt,
                "entryPrice_float": float(pos.get("entryPrice", 0)),
                "markPrice_float": float(pos.get("markPrice", 0)),
                "liquidationPrice_float": float(pos.get("liquidationPrice", 0)),
                "leverage_int": int(pos.get("leverage", 1)),
                "unrealizedPnl_float": float(pos.get("unRealizedProfit", 0)),
                "marginType": pos.get("marginType", "cross"),
                "positionSide": pos.get("positionSide", "BOTH"),
                "isLong": position_amt > 0,
                "isShort": position_amt < 0,
                "hasPosition": position_amt != 0,
                "isolatedMargin_float": float(pos.get("isolatedMargin", 0)),
                "notional_float": float(pos.get("notional", 0)),
                "maxNotionalValue_float": float(pos.get("maxNotionalValue", 0)),
                "maintenanceMargin_float": float(pos.get("maintMargin", 0)) if pos.get("maintMargin") else None,
            }
        
        return {
            "success": True,
            "data": filtered_data,
            "normalized_fields": normalized_fields,
            "raw_response": filtered_data,
            "timestamp": int(time.time() * 1000),
            "allowed_symbols": ALLOWED_FUTURES_SYMBOLS
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in get_position_risk: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
