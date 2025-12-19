"""
Place Bracket Orders for USDⓈ-M Futures.

This tool places a complete bracket order: entry + stop loss + take profits,
with proper handling of reduceOnly requirements and order coordination.
"""

import logging
import time
import threading
import uuid
from typing import Dict, Any, Optional, List
from decimal import Decimal

from binance_mcp_server.futures_config import get_futures_client, ALLOWED_FUTURES_SYMBOLS
from binance_mcp_server.futures_utils import (
    validate_futures_symbol,
    get_order_validator,
    round_to_tick_size,
    round_to_step_size,
)
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


# Bracket job storage
_bracket_jobs: Dict[str, Dict[str, Any]] = {}
_bracket_jobs_lock = threading.Lock()


def _place_single_order(client, params: Dict) -> Dict[str, Any]:
    """Place a single order."""
    success, data = client.post("/fapi/v1/order", params)
    return {"success": success, "data": data}


def _cancel_order_silent(client, symbol: str, order_id: int) -> bool:
    """Cancel an order silently, returning success status."""
    try:
        success, _ = client.delete(
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id}
        )
        return success
    except Exception:
        return False


def _get_order_status_data(client, symbol: str, order_id: int) -> Optional[Dict]:
    """Get order status, returning data or None."""
    try:
        success, data = client.get(
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
            signed=True
        )
        return data if success else None
    except Exception:
        return None


def _monitor_bracket(job_id: str, job_data: Dict[str, Any]):
    """
    Background worker to monitor bracket orders.
    
    Responsibilities:
    1. Wait for entry to fill before placing exit orders (if not already placed)
    2. Monitor exits - if one triggers, cancel the others
    """
    logger.info(f"Bracket monitor started for job {job_id}")
    
    client = get_futures_client()
    symbol = job_data["symbol"]
    entry_order_id = job_data.get("entry_order_id")
    exit_orders_placed = job_data.get("exit_orders_placed", False)
    sl_order_id = job_data.get("sl_order_id")
    tp_order_ids = job_data.get("tp_order_ids", [])
    
    max_wait = 3600  # 1 hour max monitoring
    start_time = time.time()
    poll_interval = 2  # seconds
    
    try:
        # Phase 1: Wait for entry to fill (if exits not already placed)
        if not exit_orders_placed and entry_order_id:
            logger.info(f"Bracket {job_id}: Waiting for entry {entry_order_id} to fill")
            
            while time.time() - start_time < max_wait:
                with _bracket_jobs_lock:
                    job = _bracket_jobs.get(job_id)
                    if job and job.get("cancelled"):
                        logger.info(f"Bracket {job_id}: Job cancelled")
                        return
                
                order_data = _get_order_status_data(client, symbol, entry_order_id)
                if order_data:
                    status = order_data.get("status")
                    executed_qty = float(order_data.get("executedQty", 0))
                    
                    if status == "FILLED" or (status == "PARTIALLY_FILLED" and executed_qty > 0):
                        # Entry filled - now place exit orders
                        logger.info(f"Bracket {job_id}: Entry filled, placing exits")
                        
                        with _bracket_jobs_lock:
                            _bracket_jobs[job_id]["entry_filled"] = True
                            _bracket_jobs[job_id]["filled_qty"] = executed_qty
                        
                        # Place exit orders based on stored spec
                        exits_result = _place_exit_orders(
                            client, job_id, symbol, job_data, executed_qty
                        )
                        
                        with _bracket_jobs_lock:
                            _bracket_jobs[job_id].update(exits_result)
                            sl_order_id = exits_result.get("sl_order_id")
                            tp_order_ids = exits_result.get("tp_order_ids", [])
                            exit_orders_placed = True
                        
                        break
                    
                    elif status in ("CANCELED", "CANCELLED", "EXPIRED", "REJECTED"):
                        # Entry failed
                        logger.info(f"Bracket {job_id}: Entry {status}")
                        with _bracket_jobs_lock:
                            _bracket_jobs[job_id]["status"] = "entry_failed"
                            _bracket_jobs[job_id]["entry_status"] = status
                        return
                
                time.sleep(poll_interval)
        
        # Phase 2: Monitor exit orders for OCO-like behavior
        all_exit_ids = ([sl_order_id] if sl_order_id else []) + tp_order_ids
        
        if not all_exit_ids:
            logger.info(f"Bracket {job_id}: No exit orders to monitor")
            with _bracket_jobs_lock:
                _bracket_jobs[job_id]["status"] = "completed"
            return
        
        logger.info(f"Bracket {job_id}: Monitoring {len(all_exit_ids)} exit orders")
        
        while time.time() - start_time < max_wait:
            with _bracket_jobs_lock:
                job = _bracket_jobs.get(job_id)
                if job and job.get("cancelled"):
                    logger.info(f"Bracket {job_id}: Job cancelled during monitoring")
                    return
            
            triggered_order = None
            triggered_status = None
            
            # Check all exit orders
            for exit_id in all_exit_ids:
                order_data = _get_order_status_data(client, symbol, exit_id)
                if order_data:
                    status = order_data.get("status")
                    if status == "FILLED":
                        triggered_order = exit_id
                        triggered_status = status
                        break
            
            if triggered_order:
                # One exit triggered - cancel the others
                logger.info(f"Bracket {job_id}: Exit {triggered_order} filled, cancelling others")
                
                cancelled_orders = []
                for exit_id in all_exit_ids:
                    if exit_id != triggered_order:
                        if _cancel_order_silent(client, symbol, exit_id):
                            cancelled_orders.append(exit_id)
                
                with _bracket_jobs_lock:
                    _bracket_jobs[job_id]["status"] = "completed"
                    _bracket_jobs[job_id]["triggered_exit"] = triggered_order
                    _bracket_jobs[job_id]["cancelled_exits"] = cancelled_orders
                    _bracket_jobs[job_id]["trigger_type"] = "sl" if triggered_order == sl_order_id else "tp"
                
                logger.info(f"Bracket {job_id}: Completed. Triggered: {triggered_order}, Cancelled: {cancelled_orders}")
                return
            
            time.sleep(poll_interval)
        
        # Timeout - mark as monitoring stopped
        with _bracket_jobs_lock:
            _bracket_jobs[job_id]["status"] = "monitoring_timeout"
            _bracket_jobs[job_id]["message"] = "Monitoring stopped after 1 hour, exit orders still active"
        
    except Exception as e:
        logger.error(f"Bracket {job_id}: Monitor error: {e}")
        with _bracket_jobs_lock:
            _bracket_jobs[job_id]["status"] = "error"
            _bracket_jobs[job_id]["error"] = str(e)


def _place_exit_orders(
    client,
    job_id: str,
    symbol: str,
    job_data: Dict[str, Any],
    filled_qty: float
) -> Dict[str, Any]:
    """Place SL and TP orders after entry fills."""
    
    result = {
        "sl_order_id": None,
        "tp_order_ids": [],
        "exit_orders_placed": True,
    }
    
    side = job_data["side"]
    exit_side = "SELL" if side == "BUY" else "BUY"
    sl_price = job_data.get("sl_price")
    tp_specs = job_data.get("tp_specs", [])
    working_type = job_data.get("working_type", "CONTRACT_PRICE")
    
    # Place stop loss
    if sl_price:
        sl_params = {
            "symbol": symbol,
            "side": exit_side,
            "type": "STOP_MARKET",
            "quantity": filled_qty,
            "stopPrice": sl_price,
            "workingType": working_type,
            "reduceOnly": "true",
        }
        
        sl_result = _place_single_order(client, sl_params)
        if sl_result["success"]:
            result["sl_order_id"] = sl_result["data"].get("orderId")
            logger.info(f"Bracket {job_id}: SL order placed: {result['sl_order_id']}")
        else:
            result["sl_error"] = sl_result["data"].get("message", "SL placement failed")
            logger.warning(f"Bracket {job_id}: SL failed: {result['sl_error']}")
    
    # Place take profits
    remaining_qty = filled_qty
    for i, tp in enumerate(tp_specs):
        tp_qty = tp.get("quantity", remaining_qty)
        
        # Don't exceed remaining
        if tp_qty > remaining_qty:
            tp_qty = remaining_qty
        
        if tp_qty <= 0:
            continue
        
        tp_price = tp.get("price")
        if not tp_price:
            continue
        
        tp_params = {
            "symbol": symbol,
            "side": exit_side,
            "type": "TAKE_PROFIT_MARKET",
            "quantity": tp_qty,
            "stopPrice": tp_price,
            "workingType": working_type,
            "reduceOnly": "true",
        }
        
        tp_result = _place_single_order(client, tp_params)
        if tp_result["success"]:
            tp_order_id = tp_result["data"].get("orderId")
            result["tp_order_ids"].append(tp_order_id)
            remaining_qty -= tp_qty
            logger.info(f"Bracket {job_id}: TP {i+1} order placed: {tp_order_id}")
        else:
            if "tp_errors" not in result:
                result["tp_errors"] = []
            result["tp_errors"].append({
                "index": i,
                "error": tp_result["data"].get("message", "TP placement failed")
            })
            logger.warning(f"Bracket {job_id}: TP {i+1} failed")
    
    return result


@rate_limited(binance_rate_limiter)
def place_bracket_orders_futures(
    symbol: str,
    side: str,
    entry_price: float,
    quantity: float,
    stop_loss_price: Optional[float] = None,
    take_profits: Optional[List[Dict[str, float]]] = None,
    entry_type: str = "LIMIT",
    time_in_force: str = "GTC",
    post_only: bool = False,
    reduce_only: bool = False,
    working_type: str = "CONTRACT_PRICE",
    wait_for_entry: bool = True,
) -> Dict[str, Any]:
    """
    Place a complete bracket order: entry + stop loss + take profits.
    
    This tool orchestrates the placement of:
    1. Entry order (LIMIT or MARKET)
    2. Stop loss order (placed after entry fills if wait_for_entry=True)
    3. Multiple take profit orders (placed after entry fills)
    
    The tool handles the coordination problem where reduceOnly exits can't be
    placed before the entry fills. It also implements OCO-like behavior where
    when one exit triggers, the others are cancelled.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        side: Entry side (BUY for long, SELL for short)
        entry_price: Entry limit price (ignored for MARKET)
        quantity: Total position quantity
        stop_loss_price: Stop loss trigger price
        take_profits: List of TP specs, each with:
                     {"price": float, "quantity": float or "percentage": float}
        entry_type: "LIMIT" or "MARKET"
        time_in_force: "GTC", "IOC", "FOK", or "GTX" (GTX for post-only)
        post_only: If true, uses GTX (post-only) for entry
        reduce_only: If true, entry is also reduceOnly (for scaling out)
        working_type: "MARK_PRICE" or "CONTRACT_PRICE" for exit triggers
        wait_for_entry: If true, waits for entry to fill before placing exits
        
    Returns:
        Dict containing:
        - success (bool): Whether bracket was initiated
        - job_id (str): ID for tracking the bracket
        - entry_order (dict): Entry order details
        - exit_orders_pending (bool): Whether exits are pending entry fill
        - sl_order (dict): SL order if placed immediately
        - tp_orders (list): TP orders if placed immediately
        
    Example Response:
        {
            "success": true,
            "job_id": "bracket_abc123",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry_order": {
                "orderId": 12345,
                "status": "NEW",
                "price": "50000.00",
                "quantity": "0.001"
            },
            "exit_orders_pending": true,
            "monitoring": true,
            "message": "Entry placed, exits will be placed when entry fills"
        }
    """
    logger.info(f"Placing bracket order: {symbol} {side} qty={quantity} entry={entry_price}")
    
    try:
        # Validate symbol
        is_valid, normalized_symbol, error = validate_futures_symbol(symbol)
        if not is_valid:
            return create_error_response("validation_error", error)
        
        # Validate side
        side = side.upper().strip()
        if side not in ("BUY", "SELL"):
            return create_error_response("validation_error", f"Invalid side: {side}")
        
        # Validate entry type
        entry_type = entry_type.upper().strip()
        if entry_type not in ("LIMIT", "MARKET"):
            return create_error_response("validation_error", f"Entry type must be LIMIT or MARKET")
        
        # Get validator for rounding
        validator = get_order_validator(normalized_symbol)
        if validator is None:
            return create_error_response(
                "validation_error",
                f"Could not fetch exchange info for {normalized_symbol}"
            )
        
        # Validate and round quantity
        qty_valid, qty_rounded, qty_error = validator.validate_and_round_quantity(quantity)
        if not qty_valid:
            return create_error_response("validation_error", qty_error)
        
        # Validate and round entry price
        entry_price_rounded = None
        if entry_type == "LIMIT":
            price_valid, price_rounded, price_error = validator.validate_and_round_price(entry_price)
            if not price_valid:
                return create_error_response("validation_error", price_error)
            entry_price_rounded = float(price_rounded)
        
        # Validate and round SL price
        sl_price_rounded = None
        if stop_loss_price is not None:
            sl_valid, sl_rounded, sl_error = validator.validate_and_round_price(stop_loss_price)
            if not sl_valid:
                return create_error_response("validation_error", f"Stop loss: {sl_error}")
            sl_price_rounded = float(sl_rounded)
            
            # Validate SL direction
            if entry_price_rounded:
                if side == "BUY" and sl_price_rounded >= entry_price_rounded:
                    return create_error_response(
                        "validation_error",
                        "Stop loss must be below entry for long position"
                    )
                elif side == "SELL" and sl_price_rounded <= entry_price_rounded:
                    return create_error_response(
                        "validation_error",
                        "Stop loss must be above entry for short position"
                    )
        
        # Process and validate take profits
        tp_specs = []
        if take_profits:
            total_tp_qty = Decimal("0")
            
            for i, tp in enumerate(take_profits):
                tp_price = tp.get("price")
                if tp_price is None:
                    return create_error_response(
                        "validation_error",
                        f"Take profit {i+1} missing price"
                    )
                
                tp_valid, tp_rounded, _ = validator.validate_and_round_price(tp_price)
                if not tp_valid:
                    return create_error_response("validation_error", f"TP {i+1} price invalid")
                
                # Validate TP direction
                if entry_price_rounded:
                    if side == "BUY" and float(tp_rounded) <= entry_price_rounded:
                        return create_error_response(
                            "validation_error",
                            f"Take profit {i+1} must be above entry for long"
                        )
                    elif side == "SELL" and float(tp_rounded) >= entry_price_rounded:
                        return create_error_response(
                            "validation_error",
                            f"Take profit {i+1} must be below entry for short"
                        )
                
                # Calculate TP quantity
                tp_qty = tp.get("quantity")
                tp_pct = tp.get("percentage")
                
                if tp_qty is not None:
                    tq_valid, tq_rounded, _ = validator.validate_and_round_quantity(tp_qty)
                    if tq_valid:
                        tp_specs.append({
                            "price": float(tp_rounded),
                            "quantity": float(tq_rounded)
                        })
                        total_tp_qty += tq_rounded
                elif tp_pct is not None:
                    tp_qty_calc = float(qty_rounded) * (tp_pct / 100)
                    tq_valid, tq_rounded, _ = validator.validate_and_round_quantity(tp_qty_calc)
                    if tq_valid:
                        tp_specs.append({
                            "price": float(tp_rounded),
                            "quantity": float(tq_rounded)
                        })
                        total_tp_qty += tq_rounded
                else:
                    # Use remaining quantity for last TP
                    remaining = qty_rounded - total_tp_qty
                    if remaining > 0:
                        tp_specs.append({
                            "price": float(tp_rounded),
                            "quantity": float(remaining)
                        })
                        total_tp_qty = qty_rounded
        
        client = get_futures_client()
        
        # Handle post_only
        if post_only:
            if entry_type != "LIMIT":
                return create_error_response("validation_error", "post_only requires LIMIT entry")
            time_in_force = "GTX"
        
        # Build entry order params
        entry_params = {
            "symbol": normalized_symbol,
            "side": side,
            "type": entry_type,
            "quantity": float(qty_rounded),
        }
        
        if entry_type == "LIMIT":
            entry_params["price"] = entry_price_rounded
            entry_params["timeInForce"] = time_in_force
        
        if reduce_only:
            entry_params["reduceOnly"] = "true"
        
        # Place entry order
        entry_result = _place_single_order(client, entry_params)
        
        if not entry_result["success"]:
            error_msg = entry_result["data"].get("message", "Entry order failed")
            return create_error_response(
                "entry_failed",
                error_msg,
                {"code": entry_result["data"].get("code")}
            )
        
        entry_data = entry_result["data"]
        entry_order_id = entry_data.get("orderId")
        entry_status = entry_data.get("status")
        
        # Create job for tracking
        job_id = f"bracket_{uuid.uuid4().hex[:8]}"
        
        job_data = {
            "status": "active",
            "symbol": normalized_symbol,
            "side": side,
            "entry_order_id": entry_order_id,
            "entry_status": entry_status,
            "entry_filled": entry_status == "FILLED",
            "filled_qty": float(entry_data.get("executedQty", 0)),
            "sl_price": sl_price_rounded,
            "tp_specs": tp_specs,
            "working_type": working_type,
            "sl_order_id": None,
            "tp_order_ids": [],
            "exit_orders_placed": False,
            "created_at": int(time.time() * 1000),
            "cancelled": False,
        }
        
        result = {
            "success": True,
            "job_id": job_id,
            "symbol": normalized_symbol,
            "side": side,
            "entry_order": {
                "orderId": entry_order_id,
                "status": entry_status,
                "price": entry_data.get("price"),
                "quantity": entry_data.get("origQty"),
                "executedQty": entry_data.get("executedQty"),
            },
            "timestamp": int(time.time() * 1000)
        }
        
        # If entry is already filled or we don't want to wait, place exits now
        if entry_status == "FILLED" or not wait_for_entry:
            filled_qty = float(entry_data.get("executedQty", qty_rounded))
            if filled_qty <= 0:
                filled_qty = float(qty_rounded)
            
            exits_result = _place_exit_orders(
                client, job_id, normalized_symbol, job_data, filled_qty
            )
            
            job_data.update(exits_result)
            job_data["exit_orders_placed"] = True
            
            result["sl_order"] = {"orderId": exits_result.get("sl_order_id")} if exits_result.get("sl_order_id") else None
            result["tp_orders"] = [{"orderId": oid} for oid in exits_result.get("tp_order_ids", [])]
            result["exit_orders_pending"] = False
            
            if exits_result.get("sl_error"):
                result["sl_error"] = exits_result["sl_error"]
            if exits_result.get("tp_errors"):
                result["tp_errors"] = exits_result["tp_errors"]
        else:
            result["exit_orders_pending"] = True
            result["message"] = "Entry placed, exits will be placed when entry fills"
        
        # Store job
        with _bracket_jobs_lock:
            _bracket_jobs[job_id] = job_data
        
        # Start background monitor if needed
        if entry_status != "FILLED" and wait_for_entry:
            result["monitoring"] = True
            thread = threading.Thread(
                target=_monitor_bracket,
                args=(job_id, job_data),
                daemon=True
            )
            thread.start()
        elif job_data.get("sl_order_id") or job_data.get("tp_order_ids"):
            # Start monitor for OCO-like behavior
            result["monitoring"] = True
            thread = threading.Thread(
                target=_monitor_bracket,
                args=(job_id, job_data),
                daemon=True
            )
            thread.start()
        
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in place_bracket_orders_futures: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")


def get_bracket_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get the status of a bracket order job.
    
    Args:
        job_id: The job ID returned from place_bracket_orders_futures
        
    Returns:
        Dict containing job status and order details
    """
    with _bracket_jobs_lock:
        job = _bracket_jobs.get(job_id)
        
        if job is None:
            return create_error_response("not_found", f"Job {job_id} not found")
        
        return {
            "success": True,
            "job_id": job_id,
            "status": job["status"],
            "symbol": job["symbol"],
            "side": job["side"],
            "entry_order_id": job["entry_order_id"],
            "entry_status": job.get("entry_status"),
            "entry_filled": job.get("entry_filled", False),
            "filled_qty": job.get("filled_qty"),
            "sl_order_id": job.get("sl_order_id"),
            "tp_order_ids": job.get("tp_order_ids", []),
            "exit_orders_placed": job.get("exit_orders_placed", False),
            "triggered_exit": job.get("triggered_exit"),
            "trigger_type": job.get("trigger_type"),
            "cancelled_exits": job.get("cancelled_exits", []),
            "error": job.get("error"),
            "timestamp": int(time.time() * 1000)
        }


def cancel_bracket_job(job_id: str) -> Dict[str, Any]:
    """
    Cancel a bracket order job and its associated orders.
    
    Args:
        job_id: The job ID to cancel
        
    Returns:
        Dict with cancellation results
    """
    with _bracket_jobs_lock:
        job = _bracket_jobs.get(job_id)
        
        if job is None:
            return create_error_response("not_found", f"Job {job_id} not found")
        
        if job["status"] in ("completed", "error", "cancelled"):
            return create_error_response(
                "cannot_cancel",
                f"Job is already {job['status']}"
            )
        
        job["cancelled"] = True
    
    # Try to cancel all orders
    client = get_futures_client()
    symbol = job["symbol"]
    cancelled_orders = []
    failed_cancellations = []
    
    # Cancel entry if still active
    if job.get("entry_order_id") and not job.get("entry_filled"):
        if _cancel_order_silent(client, symbol, job["entry_order_id"]):
            cancelled_orders.append({"type": "entry", "orderId": job["entry_order_id"]})
        else:
            failed_cancellations.append({"type": "entry", "orderId": job["entry_order_id"]})
    
    # Cancel SL
    if job.get("sl_order_id"):
        if _cancel_order_silent(client, symbol, job["sl_order_id"]):
            cancelled_orders.append({"type": "sl", "orderId": job["sl_order_id"]})
        else:
            failed_cancellations.append({"type": "sl", "orderId": job["sl_order_id"]})
    
    # Cancel TPs
    for tp_id in job.get("tp_order_ids", []):
        if _cancel_order_silent(client, symbol, tp_id):
            cancelled_orders.append({"type": "tp", "orderId": tp_id})
        else:
            failed_cancellations.append({"type": "tp", "orderId": tp_id})
    
    with _bracket_jobs_lock:
        _bracket_jobs[job_id]["status"] = "cancelled"
    
    return {
        "success": True,
        "job_id": job_id,
        "cancelled_orders": cancelled_orders,
        "failed_cancellations": failed_cancellations,
        "timestamp": int(time.time() * 1000)
    }
