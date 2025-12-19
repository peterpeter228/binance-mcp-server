"""
Place Order for USDⓈ-M Futures.

Corresponds to Binance API: POST /fapi/v1/order
"""

import logging
import time
from typing import Dict, Any, Optional

from binance_mcp_server.futures_config import get_futures_client
from binance_mcp_server.futures_utils import (
    validate_futures_symbol,
    get_order_validator,
    get_exchange_info_cache,
)
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


# Valid order types for futures
VALID_ORDER_TYPES = [
    "LIMIT", "MARKET", "STOP", "STOP_MARKET",
    "TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"
]

# Valid time in force values
VALID_TIF = ["GTC", "IOC", "FOK", "GTX"]  # GTX = Post-Only

# Valid position sides
VALID_POSITION_SIDES = ["BOTH", "LONG", "SHORT"]

# Valid working types for stop orders
VALID_WORKING_TYPES = ["MARK_PRICE", "CONTRACT_PRICE"]


@rate_limited(binance_rate_limiter)
def place_order_futures(
    symbol: str,
    side: str,
    order_type: str,
    quantity: Optional[float] = None,
    price: Optional[float] = None,
    stop_price: Optional[float] = None,
    time_in_force: Optional[str] = None,
    reduce_only: Optional[bool] = None,
    close_position: Optional[bool] = None,
    position_side: Optional[str] = None,
    working_type: Optional[str] = None,
    post_only: Optional[bool] = None,
    client_order_id: Optional[str] = None,
    callback_rate: Optional[float] = None,
    activation_price: Optional[float] = None,
    price_protect: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Place a new order for USDⓈ-M Futures.
    
    This tool creates a new futures order with comprehensive validation against
    exchange filters (tickSize, stepSize, minNotional, etc.) before submission.
    
    Corresponds to: POST /fapi/v1/order (TRADE, signed)
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        side: "BUY" or "SELL"
        order_type: Order type - LIMIT, MARKET, STOP, STOP_MARKET,
                   TAKE_PROFIT, TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET
        quantity: Order quantity (required unless closePosition=true)
        price: Limit price (required for LIMIT, STOP, TAKE_PROFIT)
        stop_price: Stop/trigger price (required for STOP/TAKE_PROFIT types)
        time_in_force: GTC, IOC, FOK, or GTX (GTX = post-only)
        reduce_only: If true, can only reduce position
        close_position: If true, close entire position
        position_side: BOTH, LONG, or SHORT (for hedge mode)
        working_type: MARK_PRICE or CONTRACT_PRICE (for stop orders)
        post_only: If true, sets timeInForce=GTX (order must be maker)
        client_order_id: Custom order ID
        callback_rate: Callback rate for trailing stop (1-5%)
        activation_price: Activation price for trailing stop
        price_protect: Enable price protection for stop orders
        
    Returns:
        Dict containing:
        - success (bool): Whether the order was placed successfully
        - data (dict): Order information with normalized fields
        - validation (dict): Validation and rounding information
        - raw_response (dict): Raw API response
        - timestamp (int): Unix timestamp of the response
        
    Example Response:
        {
            "success": true,
            "data": {
                "orderId": 1234567890,
                "clientOrderId": "x-...",
                "symbol": "BTCUSDT",
                "status": "NEW",
                "side": "BUY",
                "type": "LIMIT",
                "price": "50000.00",
                "origQty": "0.001",
                "avgPrice": "0",
                "updateTime": 1234567890123
            },
            "validation": {
                "price_rounded": true,
                "quantity_rounded": true,
                "original_price": 50000.15,
                "rounded_price": "50000.10"
            },
            "raw_response": {...},
            "timestamp": 1234567890123
        }
    """
    logger.info(f"Placing futures order: {symbol} {side} {order_type} qty={quantity} price={price}")
    
    try:
        # Validate symbol
        is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
        if not is_valid:
            return create_error_response("validation_error", error)
        
        # Validate side
        side = side.upper().strip()
        if side not in ("BUY", "SELL"):
            return create_error_response(
                "validation_error",
                f"Invalid side: {side}. Must be BUY or SELL"
            )
        
        # Validate order type
        order_type = order_type.upper().strip()
        if order_type not in VALID_ORDER_TYPES:
            return create_error_response(
                "validation_error",
                f"Invalid order type: {order_type}. Valid types: {', '.join(VALID_ORDER_TYPES)}"
            )
        
        # Handle postOnly -> GTX mapping
        if post_only:
            if order_type != "LIMIT":
                return create_error_response(
                    "validation_error",
                    "postOnly=true requires order type LIMIT"
                )
            time_in_force = "GTX"
        
        # Validate time_in_force
        if time_in_force:
            time_in_force = time_in_force.upper().strip()
            if time_in_force not in VALID_TIF:
                return create_error_response(
                    "validation_error",
                    f"Invalid timeInForce: {time_in_force}. Valid: {', '.join(VALID_TIF)}"
                )
        
        # Validate position_side
        if position_side:
            position_side = position_side.upper().strip()
            if position_side not in VALID_POSITION_SIDES:
                return create_error_response(
                    "validation_error",
                    f"Invalid positionSide: {position_side}. Valid: {', '.join(VALID_POSITION_SIDES)}"
                )
        
        # Validate working_type
        if working_type:
            working_type = working_type.upper().strip()
            if working_type not in VALID_WORKING_TYPES:
                return create_error_response(
                    "validation_error",
                    f"Invalid workingType: {working_type}. Valid: {', '.join(VALID_WORKING_TYPES)}"
                )
        
        # Get order validator for price/qty rounding
        validator = get_order_validator(normalized_symbol)
        if validator is None:
            return create_error_response(
                "validation_error",
                f"Could not fetch exchange info for {normalized_symbol}"
            )
        
        # Validate quantity
        validation_info = {}
        is_market = order_type in ("MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET")
        
        if quantity is not None:
            qty_valid, qty_rounded, qty_error = validator.validate_and_round_quantity(
                quantity, is_market=is_market
            )
            if not qty_valid:
                return create_error_response("validation_error", qty_error)
            
            if str(qty_rounded) != str(quantity):
                validation_info["quantity_rounded"] = True
                validation_info["original_quantity"] = quantity
                validation_info["rounded_quantity"] = str(qty_rounded)
            
            quantity = float(qty_rounded)
        elif not close_position:
            return create_error_response(
                "validation_error",
                "Quantity is required unless closePosition=true"
            )
        
        # Validate price for limit orders
        if order_type in ("LIMIT", "STOP", "TAKE_PROFIT"):
            if price is None:
                return create_error_response(
                    "validation_error",
                    f"Price is required for {order_type} orders"
                )
            
            price_valid, price_rounded, price_error = validator.validate_and_round_price(price)
            if not price_valid:
                return create_error_response("validation_error", price_error)
            
            if str(price_rounded) != str(price):
                validation_info["price_rounded"] = True
                validation_info["original_price"] = price
                validation_info["rounded_price"] = str(price_rounded)
            
            price = float(price_rounded)
            
            # Validate notional for limit orders
            if quantity is not None:
                notional_valid, notional, notional_error = validator.validate_notional(price, quantity)
                if not notional_valid:
                    return create_error_response("validation_error", notional_error)
                validation_info["notional"] = str(notional)
        
        # Validate stop price
        if stop_price is not None:
            stop_valid, stop_rounded, stop_error = validator.validate_and_round_price(stop_price)
            if not stop_valid:
                return create_error_response("validation_error", f"stopPrice: {stop_error}")
            
            if str(stop_rounded) != str(stop_price):
                validation_info["stop_price_rounded"] = True
                validation_info["original_stop_price"] = stop_price
                validation_info["rounded_stop_price"] = str(stop_rounded)
            
            stop_price = float(stop_rounded)
        elif order_type in ("STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"):
            return create_error_response(
                "validation_error",
                f"stopPrice is required for {order_type} orders"
            )
        
        # Validate trailing stop parameters
        if order_type == "TRAILING_STOP_MARKET":
            if callback_rate is None:
                return create_error_response(
                    "validation_error",
                    "callbackRate is required for TRAILING_STOP_MARKET orders"
                )
            if callback_rate < 0.1 or callback_rate > 5:
                return create_error_response(
                    "validation_error",
                    f"callbackRate must be between 0.1 and 5, got: {callback_rate}"
                )
        
        # Build order parameters
        client = get_futures_client()
        params = {
            "symbol": normalized_symbol,
            "side": side,
            "type": order_type,
        }
        
        if quantity is not None:
            params["quantity"] = quantity
        
        if price is not None:
            params["price"] = price
        
        if stop_price is not None:
            params["stopPrice"] = stop_price
        
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        elif order_type == "LIMIT":
            params["timeInForce"] = "GTC"  # Default for LIMIT
        
        if reduce_only is not None:
            params["reduceOnly"] = str(reduce_only).lower()
        
        if close_position is not None:
            params["closePosition"] = str(close_position).lower()
        
        if position_side is not None:
            params["positionSide"] = position_side
        
        if working_type is not None:
            params["workingType"] = working_type
        
        if client_order_id is not None:
            params["newClientOrderId"] = client_order_id
        
        if callback_rate is not None:
            params["callbackRate"] = callback_rate
        
        if activation_price is not None:
            act_valid, act_rounded, _ = validator.validate_and_round_price(activation_price)
            if act_valid:
                params["activationPrice"] = float(act_rounded)
        
        if price_protect is not None:
            params["priceProtect"] = str(price_protect).upper()
        
        # Place order
        success, data = client.post("/fapi/v1/order", params)
        
        if not success:
            error_code = data.get("code")
            error_msg = data.get("message", "Failed to place order")
            logger.error(f"Place order error: {error_msg} (code: {error_code})")
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
            "stopPrice": data.get("stopPrice"),
            "timeInForce": data.get("timeInForce"),
            "reduceOnly": data.get("reduceOnly"),
            "closePosition": data.get("closePosition"),
            "positionSide": data.get("positionSide"),
            "workingType": data.get("workingType"),
            "priceProtect": data.get("priceProtect"),
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
        logger.error(f"Unexpected error in place_order_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
