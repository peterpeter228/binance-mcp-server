"""
Cancel Multiple Orders for USDⓈ-M Futures.

Corresponds to Binance API: DELETE /fapi/v1/batchOrders
"""

import logging
import time
import json
from typing import Dict, Any, Optional, List

from binance_mcp_server.futures_config import get_futures_client
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


@rate_limited(binance_rate_limiter)
def cancel_multiple_orders_futures(
    symbol: str,
    order_id_list: Optional[List[int]] = None,
    orig_client_order_id_list: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Cancel multiple open orders for USDⓈ-M Futures in a single request.
    
    This tool cancels up to 10 orders in a batch. Either orderIdList or
    origClientOrderIdList must be provided (not both).
    
    Corresponds to: DELETE /fapi/v1/batchOrders (TRADE, signed)
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id_list: List of order IDs to cancel (max 10)
        orig_client_order_id_list: List of client order IDs to cancel (max 10)
        
    Returns:
        Dict containing:
        - success (bool): Whether the request completed (individual orders may fail)
        - data (dict): Summary of cancellation results
        - cancelled_orders (list): Successfully cancelled orders
        - failed_orders (list): Orders that failed to cancel
        - raw_response (list): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "totalRequested": 3,
                "successCount": 2,
                "failedCount": 1
            },
            "cancelled_orders": [
                {"orderId": 123, "status": "CANCELED"},
                {"orderId": 456, "status": "CANCELED"}
            ],
            "failed_orders": [
                {"orderId": 789, "error": "Order not found"}
            ],
            "raw_response": [...],
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Cancelling multiple futures orders for {symbol}")
    
    try:
        # Validate symbol
        is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
        if not is_valid:
            return create_error_response("validation_error", error)
        
        # Validate that one list is provided
        if order_id_list is None and orig_client_order_id_list is None:
            return create_error_response(
                "validation_error",
                "Either orderIdList or origClientOrderIdList is required"
            )
        
        if order_id_list is not None and orig_client_order_id_list is not None:
            return create_error_response(
                "validation_error",
                "Provide only one of orderIdList or origClientOrderIdList, not both"
            )
        
        # Validate list length
        id_list = order_id_list or orig_client_order_id_list
        if len(id_list) == 0:
            return create_error_response(
                "validation_error",
                "Order ID list cannot be empty"
            )
        
        if len(id_list) > 10:
            return create_error_response(
                "validation_error",
                f"Maximum 10 orders per batch, got {len(id_list)}"
            )
        
        # Build parameters
        params = {"symbol": normalized_symbol}
        
        if order_id_list is not None:
            # Binance expects JSON array as string
            params["orderIdList"] = json.dumps(order_id_list)
        else:
            params["origClientOrderIdList"] = json.dumps(orig_client_order_id_list)
        
        # Cancel orders
        client = get_futures_client()
        success, data = client.delete("/fapi/v1/batchOrders", params)
        
        if not success:
            error_code = data.get("code")
            error_msg = data.get("message", "Failed to cancel orders")
            logger.error(f"Batch cancel error: {error_msg} (code: {error_code})")
            return create_error_response("api_error", error_msg, {"code": error_code})
        
        # Process results - data should be a list of individual results
        if not isinstance(data, list):
            data = [data]
        
        cancelled_orders = []
        failed_orders = []
        
        for i, result in enumerate(data):
            order_ref = id_list[i] if i < len(id_list) else f"index_{i}"
            
            if isinstance(result, dict):
                if result.get("code"):
                    # This is an error
                    failed_orders.append({
                        "orderRef": order_ref,
                        "code": result.get("code"),
                        "error": result.get("msg", "Unknown error")
                    })
                else:
                    # Success
                    cancelled_orders.append({
                        "orderId": result.get("orderId"),
                        "clientOrderId": result.get("clientOrderId"),
                        "symbol": result.get("symbol"),
                        "status": result.get("status"),
                        "side": result.get("side"),
                        "type": result.get("type"),
                        "origQty": result.get("origQty"),
                        "executedQty": result.get("executedQty"),
                        "updateTime": result.get("updateTime"),
                    })
        
        # Build summary
        summary = {
            "totalRequested": len(id_list),
            "successCount": len(cancelled_orders),
            "failedCount": len(failed_orders),
            "allSucceeded": len(failed_orders) == 0,
        }
        
        return {
            "success": True,  # Request completed, check data for individual results
            "data": summary,
            "cancelled_orders": cancelled_orders,
            "failed_orders": failed_orders,
            "raw_response": data,
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_multiple_orders_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
