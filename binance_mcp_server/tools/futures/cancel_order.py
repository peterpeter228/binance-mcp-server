"""
Cancel Order for USDⓈ-M Futures.

Corresponds to Binance API: DELETE /fapi/v1/order
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
def cancel_order_futures(
    symbol: str,
    order_id: Optional[int] = None,
    orig_client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cancel an open order for USDⓈ-M Futures.
    
    This tool cancels a single open order. Either orderId or
    origClientOrderId must be provided.
    
    Corresponds to: DELETE /fapi/v1/order (TRADE, signed)
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id: Order ID to cancel (either this or orig_client_order_id required)
        orig_client_order_id: Client order ID to cancel
        
    Returns:
        Dict containing:
        - success (bool): Whether the cancellation was successful
        - data (dict): Cancelled order information
        - raw_response (dict): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "orderId": 1234567890,
                "clientOrderId": "x-...",
                "symbol": "BTCUSDT",
                "status": "CANCELED",
                "side": "BUY",
                "type": "LIMIT",
                "origQty": "0.001",
                "executedQty": "0.000"
            },
            "raw_response": {...},
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Cancelling futures order: {symbol} orderId={order_id} clientOrderId={orig_client_order_id}")
    
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
        
        # Cancel order
        client = get_futures_client()
        success, data = client.delete("/fapi/v1/order", params)
        
        if not success:
            error_code = data.get("code")
            error_msg = data.get("message", "Failed to cancel order")
            
            # Handle common errors
            if error_code == -2011:
                return create_error_response(
                    "order_not_found",
                    "Order not found or already cancelled/filled",
                    {"code": error_code}
                )
            
            logger.error(f"Cancel order error: {error_msg} (code: {error_code})")
            return create_error_response("api_error", error_msg, {"code": error_code})
        
        # Build normalized response
        normalized_data = {
            "orderId": data.get("orderId"),
            "clientOrderId": data.get("clientOrderId"),
            "symbol": data.get("symbol"),
            "status": data.get("status"),
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
            "positionSide": data.get("positionSide"),
            "updateTime": data.get("updateTime"),
        }
        
        return {
            "success": True,
            "data": normalized_data,
            "raw_response": data,
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_order_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
