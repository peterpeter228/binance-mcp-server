"""
Validate Order Plan for USDⓈ-M Futures.

This tool validates an order plan against exchange filters before execution,
allowing LLMs to verify their trading plans won't be rejected.
"""

import logging
import time
from typing import Dict, Any, Optional, List
from decimal import Decimal

from binance_mcp_server.futures_config import get_futures_client, ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import (
    validate_futures_symbol,
    get_order_validator,
    get_exchange_info_cache,
    round_to_tick_size,
    round_to_step_size,
)
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


@rate_limited(binance_rate_limiter)
def validate_order_plan_futures(
    symbol: str,
    side: str,
    entry_price: float,
    quantity: float,
    stop_loss: Optional[float] = None,
    take_profits: Optional[List[Dict[str, float]]] = None,
    post_only: bool = False,
    leverage: Optional[int] = None,
    margin_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate an order plan against exchange filters before execution.
    
    This tool pre-validates a trading plan (entry + SL + TPs) against exchange
    rules, returning rounded values and potential issues before attempting
    to place orders.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        side: Order side (BUY or SELL)
        entry_price: Intended entry price
        quantity: Order quantity
        stop_loss: Optional stop loss price
        take_profits: Optional list of take profit specs, each with:
                     {"price": float, "quantity": float (or "percentage": float)}
        post_only: Whether entry should be post-only (GTX)
        leverage: Intended leverage (for validation against max)
        margin_type: Intended margin type (ISOLATED/CROSSED)
        
    Returns:
        Dict containing:
        - success (bool): Always true if validation completes
        - valid (bool): Whether the plan passes all validations
        - data (dict): Validated and rounded plan
        - reasons (list): List of validation issues or warnings
        - suggested_fixes (list): Suggestions to fix issues
        
    Example Response:
        {
            "success": true,
            "valid": true,
            "data": {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entry": {
                    "original_price": 50000.15,
                    "rounded_price": "50000.10",
                    "original_quantity": 0.00105,
                    "rounded_quantity": "0.001",
                    "notional": "50.00",
                    "post_only": true
                },
                "stop_loss": {
                    "original_price": 49000.00,
                    "rounded_price": "49000.00"
                },
                "take_profits": [
                    {"price": "51000.00", "quantity": "0.001"}
                ]
            },
            "reasons": [
                "quantity_step_rounding_applied"
            ],
            "suggested_fixes": [],
            "exchange_info": {
                "tickSize": "0.10",
                "stepSize": "0.001",
                "minNotional": "5"
            }
        }
    """
    logger.info(f"Validating order plan for {symbol}")
    
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
        
        # Get validator
        validator = get_order_validator(normalized_symbol)
        if validator is None:
            return create_error_response(
                "validation_error",
                f"Could not fetch exchange info for {normalized_symbol}"
            )
        
        reasons = []
        suggested_fixes = []
        is_plan_valid = True
        
        # Validate entry price
        entry_data = {
            "original_price": entry_price,
            "original_quantity": quantity,
            "post_only": post_only,
        }
        
        price_valid, price_rounded, price_error = validator.validate_and_round_price(entry_price)
        if not price_valid:
            is_plan_valid = False
            reasons.append(f"entry_price_invalid: {price_error}")
            suggested_fixes.append("Use a valid entry price")
        else:
            entry_data["rounded_price"] = str(price_rounded)
            if price_rounded != Decimal(str(entry_price)):
                reasons.append("entry_price_rounded")
        
        # Validate quantity
        qty_valid, qty_rounded, qty_error = validator.validate_and_round_quantity(quantity)
        if not qty_valid:
            is_plan_valid = False
            reasons.append(f"quantity_invalid: {qty_error}")
            suggested_fixes.append(f"Minimum quantity is {validator.min_qty}")
        else:
            entry_data["rounded_quantity"] = str(qty_rounded)
            if qty_rounded != Decimal(str(quantity)):
                reasons.append("quantity_step_rounding_applied")
        
        # Validate notional
        if price_valid and qty_valid:
            notional_valid, notional, notional_error = validator.validate_notional(
                float(price_rounded), float(qty_rounded)
            )
            if not notional_valid:
                is_plan_valid = False
                reasons.append(f"min_notional_fail: {notional_error}")
                # Calculate minimum quantity for this price
                min_qty_for_price = float(validator.min_notional) / float(price_rounded)
                rounded_min_qty = round_to_step_size(min_qty_for_price * 1.01, validator.step_size)
                suggested_fixes.append(f"Increase quantity to at least {rounded_min_qty}")
            else:
                entry_data["notional"] = str(notional)
        
        result_data = {
            "symbol": normalized_symbol,
            "side": side,
            "entry": entry_data,
        }
        
        # Validate stop loss
        if stop_loss is not None:
            sl_data = {"original_price": stop_loss}
            sl_valid, sl_rounded, sl_error = validator.validate_and_round_price(stop_loss)
            
            if not sl_valid:
                is_plan_valid = False
                reasons.append(f"stop_loss_invalid: {sl_error}")
            else:
                sl_data["rounded_price"] = str(sl_rounded)
                if sl_rounded != Decimal(str(stop_loss)):
                    reasons.append("stop_loss_price_rounded")
                
                # Validate SL direction
                if price_valid:
                    if side == "BUY" and sl_rounded >= price_rounded:
                        is_plan_valid = False
                        reasons.append("stop_loss_must_be_below_entry_for_long")
                        suggested_fixes.append(f"Set stop loss below {price_rounded}")
                    elif side == "SELL" and sl_rounded <= price_rounded:
                        is_plan_valid = False
                        reasons.append("stop_loss_must_be_above_entry_for_short")
                        suggested_fixes.append(f"Set stop loss above {price_rounded}")
            
            result_data["stop_loss"] = sl_data
        
        # Validate take profits
        if take_profits is not None and len(take_profits) > 0:
            tp_data_list = []
            total_tp_qty = Decimal("0")
            
            for i, tp in enumerate(take_profits):
                tp_price = tp.get("price")
                tp_qty = tp.get("quantity")
                tp_pct = tp.get("percentage")
                
                tp_data = {"original_price": tp_price}
                
                # Validate TP price
                if tp_price is None:
                    is_plan_valid = False
                    reasons.append(f"tp_{i+1}_price_missing")
                    continue
                
                tp_price_valid, tp_price_rounded, tp_price_error = validator.validate_and_round_price(tp_price)
                if not tp_price_valid:
                    is_plan_valid = False
                    reasons.append(f"tp_{i+1}_price_invalid: {tp_price_error}")
                else:
                    tp_data["rounded_price"] = str(tp_price_rounded)
                    if tp_price_rounded != Decimal(str(tp_price)):
                        reasons.append(f"tp_{i+1}_price_rounded")
                    
                    # Validate TP direction
                    if price_valid:
                        if side == "BUY" and tp_price_rounded <= price_rounded:
                            is_plan_valid = False
                            reasons.append(f"tp_{i+1}_must_be_above_entry_for_long")
                        elif side == "SELL" and tp_price_rounded >= price_rounded:
                            is_plan_valid = False
                            reasons.append(f"tp_{i+1}_must_be_below_entry_for_short")
                
                # Calculate TP quantity
                if tp_qty is not None:
                    tp_qty_valid, tp_qty_rounded, _ = validator.validate_and_round_quantity(tp_qty)
                    if tp_qty_valid:
                        tp_data["quantity"] = str(tp_qty_rounded)
                        total_tp_qty += tp_qty_rounded
                elif tp_pct is not None and qty_valid:
                    # Calculate from percentage
                    tp_qty_calc = float(qty_rounded) * (tp_pct / 100)
                    tp_qty_valid, tp_qty_rounded, _ = validator.validate_and_round_quantity(tp_qty_calc)
                    if tp_qty_valid:
                        tp_data["quantity"] = str(tp_qty_rounded)
                        tp_data["percentage"] = tp_pct
                        total_tp_qty += tp_qty_rounded
                
                tp_data_list.append(tp_data)
            
            # Validate total TP quantity doesn't exceed entry
            if qty_valid and total_tp_qty > qty_rounded:
                reasons.append("tp_total_quantity_exceeds_entry")
                suggested_fixes.append("Reduce take profit quantities or use percentages totaling ≤100%")
            
            result_data["take_profits"] = tp_data_list
        
        # Validate leverage if provided
        if leverage is not None:
            cache = get_exchange_info_cache()
            client = get_futures_client()
            
            # Fetch leverage brackets
            bracket_success, bracket_data = client.get(
                "/fapi/v1/leverageBracket",
                {"symbol": normalized_symbol},
                signed=True
            )
            
            if bracket_success and bracket_data:
                for item in bracket_data:
                    if item.get("symbol") == normalized_symbol:
                        brackets = item.get("brackets", [])
                        if brackets:
                            max_leverage = brackets[0].get("initialLeverage", 125)
                            if leverage > max_leverage:
                                is_plan_valid = False
                                reasons.append(f"leverage_exceeds_max: max is {max_leverage}")
                                suggested_fixes.append(f"Reduce leverage to {max_leverage} or less")
                            result_data["leverage"] = {
                                "requested": leverage,
                                "max_allowed": max_leverage,
                                "valid": leverage <= max_leverage
                            }
                        break
        
        # Validate margin type
        if margin_type is not None:
            margin_type = margin_type.upper()
            if margin_type not in ("ISOLATED", "CROSSED"):
                is_plan_valid = False
                reasons.append(f"invalid_margin_type: {margin_type}")
                suggested_fixes.append("Use ISOLATED or CROSSED")
            else:
                result_data["margin_type"] = margin_type
        
        return {
            "success": True,
            "valid": is_plan_valid,
            "data": result_data,
            "reasons": reasons,
            "suggested_fixes": suggested_fixes,
            "exchange_info": {
                "symbol": normalized_symbol,
                "tickSize": validator.tick_size,
                "stepSize": validator.step_size,
                "minQty": str(validator.min_qty),
                "maxQty": str(validator.max_qty),
                "minNotional": str(validator.min_notional),
            },
            "timestamp": int(time.time() * 1000)
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in validate_order_plan_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")
