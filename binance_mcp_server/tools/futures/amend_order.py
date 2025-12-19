"""
Amend Order for USDⓈ-M Futures.

Corresponds to Binance API: PUT /fapi/v1/order
"""

import logging
import time
from typing import Dict, Any, Optional

from binance_mcp_server.futures_config import get_futures_client
from binance_mcp_server.futures_utils import (
    validate_futures_symbol,
    get_order_validator,
)
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


@rate_limited(binance_rate_limiter)
def amend_order_futures(
    symbol: str,
    order_id: Optional[int] = None,
    orig_client_order_id: Optional[str] = None,
    price: Optional[float] = None,
    quantity: Optional[float] = None,
    side: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Amend/modify an existing LIMIT order for USDⓈ-M Futures.
    
    This tool modifies the price and/or quantity of an existing LIMIT order.
    Note: Binance only supports modifying LIMIT orders - other order types
    must be cancelled and re-placed.
    
    Corresponds to: PUT /fapi/v1/order (TRADE, signed)
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id: Order ID to modify (either this or orig_client_order_id required)
        orig_client_order_id: Client order ID to modify
        price: New price (optional)
        quantity: New quantity (optional)
        side: Order side BUY/SELL (required for modification)
        
    Returns:
        Dict containing:
        - success (bool): Whether the modification was successful
        - data (dict): Modified order information
        - validation (dict): Validation and rounding information
        - raw_response (dict): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "orderId": 1234567890,
                "symbol": "BTCUSDT",
                "status": "NEW",
                "price": "51000.00",
                "origQty": "0.002"
            },
            "validation": {
                "price_rounded": true,
                "original_price": 51000.15,
                "rounded_price": "51000.10"
            },
            "raw_response": {...},
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Amending futures order: {symbol} orderId={order_id} clientOrderId={orig_client_order_id}")
    
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
        
        # Validate that at least one modification parameter is provided
        if price is None and quantity is None:
            return create_error_response(
                "validation_error",
                "At least one of price or quantity must be provided for modification"
            )
        
        # Validate side if provided
        if side:
            side = side.upper().strip()
            if side not in ("BUY", "SELL"):
                return create_error_response(
                    "validation_error",
                    f"Invalid side: {side}. Must be BUY or SELL"
                )
        else:
            return create_error_response(
                "validation_error",
                "side is required for order modification"
            )
        
        # Get order validator
        validator = get_order_validator(normalized_symbol)
        if validator is None:
            return create_error_response(
                "validation_error",
                f"Could not fetch exchange info for {normalized_symbol}"
            )
        
        # Build modification parameters
        params = {
            "symbol": normalized_symbol,
            "side": side,
        }
        
        if order_id is not None:
            params["orderId"] = order_id
        
        if orig_client_order_id is not None:
            params["origClientOrderId"] = orig_client_order_id
        
        validation_info = {}
        
        # Validate and round price
        if price is not None:
            price_valid, price_rounded, price_error = validator.validate_and_round_price(price)
            if not price_valid:
                return create_error_response("validation_error", price_error)
            
            if str(price_rounded) != str(price):
                validation_info["price_rounded"] = True
                validation_info["original_price"] = price
                validation_info["rounded_price"] = str(price_rounded)
            
            params["price"] = float(price_rounded)
        
        # Validate and round quantity
        if quantity is not None:
            qty_valid, qty_rounded, qty_error = validator.validate_and_round_quantity(quantity)
            if not qty_valid:
                return create_error_response("validation_error", qty_error)
            
            if str(qty_rounded) != str(quantity):
                validation_info["quantity_rounded"] = True
                validation_info["original_quantity"] = quantity
                validation_info["rounded_quantity"] = str(qty_rounded)
            
            params["quantity"] = float(qty_rounded)
        
        # Validate notional if both price and quantity provided
        if price is not None and quantity is not None:
            notional_valid, notional, notional_error = validator.validate_notional(
                float(params["price"]), float(params["quantity"])
            )
            if not notional_valid:
                return create_error_response("validation_error", notional_error)
            validation_info["notional"] = str(notional)
        
        # Send modification request
        client = get_futures_client()
        success, data = client.put("/fapi/v1/order", params)
        
        if not success:
            error_code = data.get("code")
            error_msg = data.get("message", "Failed to amend order")
            
            # Handle common errors
            if error_code == -2011:
                return create_error_response(
                    "order_not_found",
                    "Order not found or already cancelled/filled",
                    {"code": error_code}
                )
            
            if error_code == -4141:
                return create_error_response(
                    "invalid_order_type",
                    "Only LIMIT orders can be modified. Cancel and re-place for other order types.",
                    {"code": error_code}
                )
            
            logger.error(f"Amend order error: {error_msg} (code: {error_code})")
            return create_error_response(
                "api_error",
                error_msg,
                {
                    "code": error_code,
                    "params_sent": {k: v for k, v in params.items() if k != "signature"},
                    "validation": validation_info
                }
            )
        
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
            "timeInForce": data.get("timeInForce"),
            "updateTime": data.get("updateTime"),
        }
        
        return {
            "success": True,
            "data": normalized_data,
            "validation": validation_info if validation_info else {"all_valid": True},
            "raw_response": data,
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in amend_order_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
