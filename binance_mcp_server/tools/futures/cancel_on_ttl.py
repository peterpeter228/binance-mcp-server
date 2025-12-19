"""
Cancel Order on TTL for USDⓈ-M Futures.

This tool implements time-to-live cancellation for orders, cancelling
unfilled orders after a specified duration.
"""

import logging
import time
import threading
import uuid
from typing import Dict, Any, Optional

from binance_mcp_server.futures_config import get_futures_client
from binance_mcp_server.futures_utils import validate_futures_symbol
from binance_mcp_server.utils import (
    create_error_response,
    rate_limited,
    binance_rate_limiter,
)

logger = logging.getLogger(__name__)


# Job storage for background TTL jobs
_ttl_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()

# Maximum TTL in seconds (10 minutes)
MAX_TTL_SECONDS = 600


def _get_order_status(client, symbol: str, order_id: int) -> Dict[str, Any]:
    """Get order status helper."""
    success, data = client.get(
        "/fapi/v1/order",
        {"symbol": symbol, "orderId": order_id},
        signed=True
    )
    return {"success": success, "data": data}


def _cancel_order(client, symbol: str, order_id: int) -> Dict[str, Any]:
    """Cancel order helper."""
    success, data = client.delete(
        "/fapi/v1/order",
        {"symbol": symbol, "orderId": order_id}
    )
    return {"success": success, "data": data}


def _ttl_worker(job_id: str, symbol: str, order_id: int, ttl_seconds: float):
    """Background worker for TTL cancellation."""
    logger.info(f"TTL job {job_id} started: will cancel order {order_id} in {ttl_seconds}s")
    
    with _jobs_lock:
        _ttl_jobs[job_id]["status"] = "waiting"
    
    # Wait for TTL
    time.sleep(ttl_seconds)
    
    with _jobs_lock:
        job = _ttl_jobs.get(job_id)
        if job and job.get("cancelled"):
            logger.info(f"TTL job {job_id} was cancelled before execution")
            _ttl_jobs[job_id]["status"] = "cancelled"
            return
        _ttl_jobs[job_id]["status"] = "executing"
    
    try:
        client = get_futures_client()
        
        # Check if order is still active
        status_result = _get_order_status(client, symbol, order_id)
        
        if not status_result["success"]:
            with _jobs_lock:
                _ttl_jobs[job_id]["status"] = "error"
                _ttl_jobs[job_id]["error"] = status_result["data"].get("message", "Failed to check order")
            return
        
        order_status = status_result["data"].get("status")
        
        # Only cancel if order is still active
        if order_status in ("NEW", "PARTIALLY_FILLED"):
            cancel_result = _cancel_order(client, symbol, order_id)
            
            with _jobs_lock:
                if cancel_result["success"]:
                    _ttl_jobs[job_id]["status"] = "completed"
                    _ttl_jobs[job_id]["result"] = {
                        "action": "cancelled",
                        "order_id": order_id,
                        "final_status": cancel_result["data"].get("status"),
                        "executed_qty": cancel_result["data"].get("executedQty"),
                    }
                else:
                    _ttl_jobs[job_id]["status"] = "error"
                    _ttl_jobs[job_id]["error"] = cancel_result["data"].get("message")
        else:
            # Order already filled or cancelled
            with _jobs_lock:
                _ttl_jobs[job_id]["status"] = "completed"
                _ttl_jobs[job_id]["result"] = {
                    "action": "no_action",
                    "reason": f"Order already {order_status}",
                    "order_id": order_id,
                    "final_status": order_status,
                    "executed_qty": status_result["data"].get("executedQty"),
                }
        
        logger.info(f"TTL job {job_id} completed: {_ttl_jobs.get(job_id, {}).get('status')}")
        
    except Exception as e:
        logger.error(f"TTL job {job_id} failed: {e}")
        with _jobs_lock:
            _ttl_jobs[job_id]["status"] = "error"
            _ttl_jobs[job_id]["error"] = str(e)


@rate_limited(binance_rate_limiter)
def cancel_on_ttl(
    symbol: str,
    order_id: Optional[int] = None,
    orig_client_order_id: Optional[str] = None,
    ttl_seconds: float = 60,
    blocking: bool = False,
) -> Dict[str, Any]:
    """
    Cancel an order after a time-to-live period expires.
    
    This tool schedules an order for cancellation after the specified TTL.
    If the order fills before the TTL expires, no cancellation is attempted.
    
    Can operate in two modes:
    - Non-blocking (default): Returns immediately with a job_id for status tracking
    - Blocking: Waits for TTL to expire and returns final result
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id: Order ID to cancel (either this or orig_client_order_id required)
        orig_client_order_id: Client order ID to cancel
        ttl_seconds: Time to wait before cancelling (default: 60, max: 600)
        blocking: If true, waits for TTL and returns final result
        
    Returns:
        Dict containing:
        For non-blocking:
        - success (bool): Whether the job was scheduled
        - job_id (str): ID to track the cancellation job
        - check_with (str): Instruction to check job status
        
        For blocking:
        - success (bool): Whether the operation completed
        - data (dict): Final order status
        - action (str): What happened (cancelled/no_action/error)
        
    Example Non-blocking Response:
        {
            "success": true,
            "job_id": "ttl_abc123",
            "ttl_seconds": 60,
            "order_id": 12345,
            "scheduled_cancel_at": 1234567950000,
            "check_with": "get_ttl_job_status(job_id='ttl_abc123')"
        }
        
    Example Blocking Response:
        {
            "success": true,
            "action": "cancelled",
            "data": {
                "order_id": 12345,
                "final_status": "CANCELED",
                "executed_qty": "0.000"
            },
            "waited_seconds": 60
        }
    """
    logger.info(f"Cancel on TTL: {symbol} orderId={order_id} ttl={ttl_seconds}s blocking={blocking}")
    
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
        
        # Validate TTL
        if ttl_seconds <= 0:
            return create_error_response(
                "validation_error",
                "ttl_seconds must be positive"
            )
        
        if ttl_seconds > MAX_TTL_SECONDS:
            return create_error_response(
                "validation_error",
                f"ttl_seconds cannot exceed {MAX_TTL_SECONDS} (10 minutes)"
            )
        
        client = get_futures_client()
        
        # If we have client order id, resolve to order id
        actual_order_id = order_id
        if actual_order_id is None and orig_client_order_id is not None:
            status_success, status_data = client.get(
                "/fapi/v1/order",
                {"symbol": normalized_symbol, "origClientOrderId": orig_client_order_id},
                signed=True
            )
            if status_success:
                actual_order_id = status_data.get("orderId")
            else:
                return create_error_response(
                    "order_not_found",
                    f"Could not find order with clientOrderId: {orig_client_order_id}"
                )
        
        # Verify order exists and is active
        verify_success, verify_data = client.get(
            "/fapi/v1/order",
            {"symbol": normalized_symbol, "orderId": actual_order_id},
            signed=True
        )
        
        if not verify_success:
            return create_error_response(
                "order_not_found",
                verify_data.get("message", "Order not found")
            )
        
        current_status = verify_data.get("status")
        if current_status not in ("NEW", "PARTIALLY_FILLED"):
            return {
                "success": True,
                "action": "no_action",
                "reason": f"Order is already {current_status}, no TTL cancellation needed",
                "data": {
                    "order_id": actual_order_id,
                    "final_status": current_status,
                    "executed_qty": verify_data.get("executedQty"),
                },
                "timestamp": int(time.time() * 1000)
            }
        
        if blocking:
            # Blocking mode - wait and cancel
            start_time = time.time()
            time.sleep(ttl_seconds)
            
            # Check status again
            status_result = _get_order_status(client, normalized_symbol, actual_order_id)
            
            if not status_result["success"]:
                return create_error_response(
                    "api_error",
                    status_result["data"].get("message", "Failed to check order status")
                )
            
            order_status = status_result["data"].get("status")
            
            if order_status in ("NEW", "PARTIALLY_FILLED"):
                # Cancel the order
                cancel_result = _cancel_order(client, normalized_symbol, actual_order_id)
                
                if cancel_result["success"]:
                    return {
                        "success": True,
                        "action": "cancelled",
                        "data": {
                            "order_id": actual_order_id,
                            "final_status": cancel_result["data"].get("status"),
                            "executed_qty": cancel_result["data"].get("executedQty"),
                            "avg_price": cancel_result["data"].get("avgPrice"),
                        },
                        "waited_seconds": round(time.time() - start_time, 2),
                        "timestamp": int(time.time() * 1000)
                    }
                else:
                    return create_error_response(
                        "cancel_failed",
                        cancel_result["data"].get("message", "Failed to cancel order")
                    )
            else:
                return {
                    "success": True,
                    "action": "no_action",
                    "reason": f"Order filled/cancelled during TTL wait: {order_status}",
                    "data": {
                        "order_id": actual_order_id,
                        "final_status": order_status,
                        "executed_qty": status_result["data"].get("executedQty"),
                    },
                    "waited_seconds": round(time.time() - start_time, 2),
                    "timestamp": int(time.time() * 1000)
                }
        else:
            # Non-blocking mode - schedule background job
            job_id = f"ttl_{uuid.uuid4().hex[:8]}"
            scheduled_time = int((time.time() + ttl_seconds) * 1000)
            
            with _jobs_lock:
                _ttl_jobs[job_id] = {
                    "status": "scheduled",
                    "symbol": normalized_symbol,
                    "order_id": actual_order_id,
                    "ttl_seconds": ttl_seconds,
                    "created_at": int(time.time() * 1000),
                    "scheduled_cancel_at": scheduled_time,
                    "cancelled": False,
                    "result": None,
                    "error": None,
                }
            
            # Start background thread
            thread = threading.Thread(
                target=_ttl_worker,
                args=(job_id, normalized_symbol, actual_order_id, ttl_seconds),
                daemon=True
            )
            thread.start()
            
            return {
                "success": True,
                "job_id": job_id,
                "order_id": actual_order_id,
                "symbol": normalized_symbol,
                "ttl_seconds": ttl_seconds,
                "scheduled_cancel_at": scheduled_time,
                "check_with": f"get_ttl_job_status(job_id='{job_id}')",
                "timestamp": int(time.time() * 1000)
            }
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_on_ttl: {e}")
        return create_error_response("tool_error", f"Tool execution failed: {str(e)}")


def get_ttl_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get the status of a TTL cancellation job.
    
    Args:
        job_id: The job ID returned from cancel_on_ttl
        
    Returns:
        Dict containing job status and result if completed
    """
    with _jobs_lock:
        job = _ttl_jobs.get(job_id)
        
        if job is None:
            return create_error_response("not_found", f"Job {job_id} not found")
        
        return {
            "success": True,
            "job_id": job_id,
            "status": job["status"],
            "symbol": job["symbol"],
            "order_id": job["order_id"],
            "ttl_seconds": job["ttl_seconds"],
            "scheduled_cancel_at": job["scheduled_cancel_at"],
            "result": job.get("result"),
            "error": job.get("error"),
            "timestamp": int(time.time() * 1000)
        }


def cancel_ttl_job(job_id: str) -> Dict[str, Any]:
    """
    Cancel a pending TTL job before it executes.
    
    Args:
        job_id: The job ID to cancel
        
    Returns:
        Dict confirming cancellation
    """
    with _jobs_lock:
        job = _ttl_jobs.get(job_id)
        
        if job is None:
            return create_error_response("not_found", f"Job {job_id} not found")
        
        if job["status"] not in ("scheduled", "waiting"):
            return create_error_response(
                "cannot_cancel",
                f"Job is already {job['status']}, cannot cancel"
            )
        
        job["cancelled"] = True
        job["status"] = "cancelled"
        
        return {
            "success": True,
            "job_id": job_id,
            "message": "TTL job cancelled, order will not be automatically cancelled",
            "timestamp": int(time.time() * 1000)
        }
