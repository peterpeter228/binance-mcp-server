"""
Get Leverage Brackets for USDⓈ-M Futures.

Corresponds to Binance API: GET /fapi/v1/leverageBracket
"""

import logging
import time
from typing import Dict, Any, Optional, List
from decimal import Decimal

from binance_mcp_server.futures_config import get_futures_client, ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import (
    validate_futures_symbol, 
    calculate_mmr_for_notional
)
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


@rate_limited(binance_rate_limiter)
def get_leverage_brackets(
    symbol: Optional[str] = None,
    notional_for_mmr: Optional[float] = None
) -> Dict[str, Any]:
    """
    Get leverage brackets for USDⓈ-M Futures symbol(s).
    
    This tool retrieves notional value tiers with their associated max leverage
    and maintenance margin ratios. Essential for calculating liquidation price
    and maintenance margin requirements.
    
    Corresponds to: GET /fapi/v1/leverageBracket (USER_DATA, signed)
    
    Args:
        symbol: Optional trading pair symbol (BTCUSDT or ETHUSDT).
                If not provided, returns brackets for all allowed symbols.
        notional_for_mmr: Optional notional value to calculate specific MMR for.
                         When provided, returns a helper field with MMR calculation.
        
    Returns:
        Dict containing:
        - success (bool): Whether the request was successful
        - data (dict): Brackets organized by symbol
        - mmr_for_notional (dict): If notional provided, MMR calculation result
        - raw_response (list): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "BTCUSDT": {
                    "brackets": [
                        {
                            "bracket": 1,
                            "initialLeverage": 125,
                            "notionalCap": 50000,
                            "notionalFloor": 0,
                            "maintMarginRatio": 0.004,
                            "cum": 0.0
                        },
                        ...
                    ],
                    "maxLeverage": 125,
                    "minMaintMarginRatio": 0.004
                }
            },
            "mmr_for_notional": {
                "BTCUSDT": {
                    "notional": "10000",
                    "bracket": 1,
                    "maintMarginRatio": 0.004,
                    "initialLeverage": 125,
                    "cum": 0.0
                }
            },
            "raw_response": [...],
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Getting leverage brackets for: {symbol if symbol else 'all allowed'}")
    
    try:
        # Validate symbol if provided
        params = {}
        normalized_symbol = None
        if symbol:
            is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
            if not is_valid:
                return create_error_response("validation_error", error)
            params["symbol"] = normalized_symbol
        
        client = get_futures_client()
        
        # Fetch leverage brackets (signed endpoint)
        success, data = client.get("/fapi/v1/leverageBracket", params, signed=True)
        
        if not success:
            error_msg = data.get("message", "Failed to fetch leverage brackets")
            logger.error(f"Leverage brackets error: {error_msg}")
            return create_error_response("api_error", error_msg, {"code": data.get("code")})
        
        # Ensure data is a list
        if not isinstance(data, list):
            data = [data]
        
        # Process and organize brackets by symbol
        brackets_by_symbol = {}
        for item in data:
            sym = item.get("symbol")
            
            # Filter to allowed symbols
            if sym not in ALLOWED_FUTURES_SYMBOLS:
                continue
            
            # If specific symbol requested, filter to that only
            if normalized_symbol and sym != normalized_symbol:
                continue
            
            raw_brackets = item.get("brackets", [])
            
            # Parse brackets with proper types
            parsed_brackets = []
            max_leverage = 0
            min_mmr = 1.0
            
            for b in raw_brackets:
                bracket = {
                    "bracket": b.get("bracket"),
                    "initialLeverage": b.get("initialLeverage"),
                    "notionalCap": float(b.get("notionalCap", 0)),
                    "notionalFloor": float(b.get("notionalFloor", 0)),
                    "maintMarginRatio": float(b.get("maintMarginRatio", 0)),
                    "cum": float(b.get("cum", 0)),
                }
                parsed_brackets.append(bracket)
                
                # Track max leverage and min MMR
                if bracket["initialLeverage"] > max_leverage:
                    max_leverage = bracket["initialLeverage"]
                if bracket["maintMarginRatio"] < min_mmr:
                    min_mmr = bracket["maintMarginRatio"]
            
            brackets_by_symbol[sym] = {
                "brackets": parsed_brackets,
                "maxLeverage": max_leverage,
                "minMaintMarginRatio": min_mmr,
                "bracketCount": len(parsed_brackets)
            }
        
        # Calculate MMR for specific notional if requested
        mmr_for_notional = {}
        if notional_for_mmr is not None and notional_for_mmr > 0:
            for sym, sym_data in brackets_by_symbol.items():
                mmr_result = calculate_mmr_for_notional(
                    sym_data["brackets"], 
                    notional_for_mmr
                )
                if mmr_result:
                    mmr_for_notional[sym] = mmr_result
        
        result = {
            "success": True,
            "data": brackets_by_symbol,
            "raw_response": data,
            "timestamp": int(time.time() * 1000),
            "allowed_symbols": ALLOWED_FUTURES_SYMBOLS
        }
        
        if mmr_for_notional:
            result["mmr_for_notional"] = mmr_for_notional
        
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_leverage_brackets: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
