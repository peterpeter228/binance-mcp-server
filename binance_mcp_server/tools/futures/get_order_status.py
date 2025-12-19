"""
Get Order Status for USDⓈ-M Futures.

Corresponds to Binance API: GET /fapi/v1/order
"""

import logging
import time
from typing import Dict, Any, Optional

from binance_mcp_server.futures_config import get_futures_client
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


@rate_limited(binance_rate_limiter)
def get_order_status_futures(
    symbol: str,
    order_id: Optional[int] = None,
    orig_client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get the status of an order for USDⓈ-M Futures.
    
    This tool retrieves detailed information about a specific order,
    including its current status, filled quantity, and execution details.
    
    Corresponds to: GET /fapi/v1/order (USER_DATA, signed)
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id: Order ID (either this or orig_client_order_id required)
        orig_client_order_id: Client order ID
        
    Returns:
        Dict containing:
        - success (bool): Whether the request was successful
        - data (dict): Order information with normalized fields
        - raw_response (dict): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "orderId": 1234567890,
                "clientOrderId": "x-...",
                "symbol": "BTCUSDT",
                "status": "FILLED",
                "side": "BUY",
                "type": "LIMIT",
                "price": "50000.00",
                "origQty": "0.001",
                "executedQty": "0.001",
                "avgPrice": "49999.50",
                "updateTime": 1234567890123,
                "isFilled": true,
                "fillPercentage": 100.0
            },
            "raw_response": {...},
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Getting order status: {symbol} orderId={order_id} clientOrderId={orig_client_order_id}")
    
    try:
        # Validate symbol
        is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
        if not is_valid:
            return create_error_response("validation_error", error)
        
        # Validate order identifier
        if order_id is None and orig_client_order_id is None:
            return create_error_response(
                "validation_error",
                "Either orderId or origClientOrderId is required"
            )
        
        # Build parameters
        params = {"symbol": normalized_symbol}
        
        if order_id is not None:
            params["orderId"] = order_id
        
        if orig_client_order_id is not None:
            params["origClientOrderId"] = orig_client_order_id
        
        # Fetch order status
        client = get_futures_client()
        success, data = client.get("/fapi/v1/order", params, signed=True)
        
        if not success:
            error_code = data.get("code")
            error_msg = data.get("message", "Failed to get order status")
            
            if error_code == -2013:
                return create_error_response(
                    "order_not_found",
                    "Order does not exist",
                    {"code": error_code}
                )
            
            logger.error(f"Get order status error: {error_msg} (code: {error_code})")
            return create_error_response("api_error", error_msg, {"code": error_code})
        
        # Calculate fill percentage
        orig_qty = float(data.get("origQty", 0))
        executed_qty = float(data.get("executedQty", 0))
        fill_percentage = (executed_qty / orig_qty * 100) if orig_qty > 0 else 0
        
        # Determine status flags
        status = data.get("status", "")
        is_filled = status == "FILLED"
        is_partially_filled = status == "PARTIALLY_FILLED"
        is_cancelled = status in ("CANCELED", "CANCELLED")
        is_expired = status == "EXPIRED"
        is_active = status in ("NEW", "PARTIALLY_FILLED")
        
        # Build normalized response
        normalized_data = {
            "orderId": data.get("orderId"),
            "clientOrderId": data.get("clientOrderId"),
            "symbol": data.get("symbol"),
            "status": status,
            "side": data.get("side"),
            "type": data.get("type"),
            "price": data.get("price"),
            "origQty": data.get("origQty"),
            "executedQty": data.get("executedQty"),
            "cumQuote": data.get("cumQuote"),
            "avgPrice": data.get("avgPrice"),
            "stopPrice": data.get("stopPrice"),
            "timeInForce": data.get("timeInForce"),
            "reduceOnly": data.get("reduceOnly"),
            "closePosition": data.get("closePosition"),
            "positionSide": data.get("positionSide"),
            "workingType": data.get("workingType"),
            "priceProtect": data.get("priceProtect"),
            "origType": data.get("origType"),
            "time": data.get("time"),
            "updateTime": data.get("updateTime"),
            # Computed fields
            "isFilled": is_filled,
            "isPartiallyFilled": is_partially_filled,
            "isCancelled": is_cancelled,
            "isExpired": is_expired,
            "isActive": is_active,
            "fillPercentage": round(fill_percentage, 2),
        }
        
        return {
            "success": True,
            "data": normalized_data,
            "raw_response": data,
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in get_order_status_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
