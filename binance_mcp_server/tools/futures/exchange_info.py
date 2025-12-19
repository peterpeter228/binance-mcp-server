"""
Get Exchange Info for USDⓈ-M Futures.

Corresponds to Binance API: GET /fapi/v1/exchangeInfo
"""

import logging
import time
from typing import Dict, Any, Optional

from binance_mcp_server.futures_config import get_futures_client, ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import (
    validate_futures_symbol,
    get_exchange_info_cache,
)
from binance_mcp_server.utils import (
    create_error_response,
    create_success_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


@rate_limited(binance_rate_limiter)
def get_exchange_info_futures(symbol: str) -> Dict[str, Any]:
    """
    Get exchange information for a USDⓈ-M Futures symbol.
    
    This tool retrieves trading rules, filters, and precision settings for
    a specific futures trading pair. Essential for order validation and
    price/quantity rounding.
    
    Corresponds to: GET /fapi/v1/exchangeInfo
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        
    Returns:
        Dict containing:
        - success (bool): Whether the request was successful
        - data (dict): Symbol information with normalized fields
        - raw_response (dict): Raw API response data
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "symbol": "BTCUSDT",
                "tickSize": "0.10",
                "stepSize": "0.001",
                "minQty": "0.001",
                "minNotional": "5",
                "pricePrecision": 2,
                "qtyPrecision": 3,
                "maxLeverage": 125,
                "status": "TRADING",
                "filters": {...}
            },
            "raw_response": {...},
            "serverTime": 1234567890123
        }
    """
    logger.info(f"Getting exchange info for futures symbol: {symbol}")
    
    try:
        # Validate symbol
        is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
        if not is_valid:
            return create_error_response("validation_error", error)
        
        client = get_futures_client()
        
        # Fetch exchange info
        success, data = client.get("/fapi/v1/exchangeInfo")
        
        if not success:
            error_msg = data.get("message", "Failed to fetch exchange info")
            logger.error(f"Exchange info error: {error_msg}")
            return create_error_response("api_error", error_msg, {"code": data.get("code")})
        
        # Find symbol info
        symbol_info = None
        for sym in data.get("symbols", []):
            if sym.get("symbol") == normalized_symbol:
                symbol_info = sym
                break
        
        if symbol_info is None:
            return create_error_response(
                "not_found", 
                f"Symbol {normalized_symbol} not found in exchange info"
            )
        
        # Parse filters
        filters = {}
        tick_size = "0.01"
        step_size = "0.001"
        min_qty = "0.001"
        max_qty = "9999999"
        min_notional = "5"
        max_leverage = None
        
        for f in symbol_info.get("filters", []):
            filter_type = f.get("filterType")
            filters[filter_type] = f
            
            if filter_type == "PRICE_FILTER":
                tick_size = f.get("tickSize", tick_size)
            elif filter_type == "LOT_SIZE":
                step_size = f.get("stepSize", step_size)
                min_qty = f.get("minQty", min_qty)
                max_qty = f.get("maxQty", max_qty)
            elif filter_type == "MIN_NOTIONAL":
                min_notional = f.get("notional", min_notional)
            elif filter_type == "NOTIONAL":
                min_notional = f.get("minNotional", min_notional)
        
        # Try to get max leverage from leverage brackets
        try:
            bracket_success, bracket_data = client.get(
                "/fapi/v1/leverageBracket", 
                {"symbol": normalized_symbol},
                signed=True
            )
            if bracket_success and bracket_data:
                # Find max leverage from first bracket
                for item in bracket_data:
                    if item.get("symbol") == normalized_symbol:
                        brackets = item.get("brackets", [])
                        if brackets:
                            max_leverage = brackets[0].get("initialLeverage")
                        break
        except Exception as e:
            logger.warning(f"Failed to fetch leverage brackets: {e}")
        
        # Build normalized response
        normalized = {
            "symbol": normalized_symbol,
            "status": symbol_info.get("status"),
            "baseAsset": symbol_info.get("baseAsset"),
            "quoteAsset": symbol_info.get("quoteAsset"),
            "marginAsset": symbol_info.get("marginAsset"),
            "pricePrecision": symbol_info.get("pricePrecision"),
            "quantityPrecision": symbol_info.get("quantityPrecision", symbol_info.get("baseAssetPrecision")),
            "baseAssetPrecision": symbol_info.get("baseAssetPrecision"),
            "quotePrecision": symbol_info.get("quotePrecision"),
            "tickSize": tick_size,
            "stepSize": step_size,
            "minQty": min_qty,
            "maxQty": max_qty,
            "minNotional": min_notional,
            "contractType": symbol_info.get("contractType"),
            "deliveryDate": symbol_info.get("deliveryDate"),
            "onboardDate": symbol_info.get("onboardDate"),
            "underlyingType": symbol_info.get("underlyingType"),
            "filters": filters,
        }
        
        if max_leverage is not None:
            normalized["maxLeverage"] = max_leverage
        
        return {
            "success": True,
            "data": normalized,
            "raw_response": symbol_info,
            "serverTime": data.get("serverTime"),
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in get_exchange_info_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
