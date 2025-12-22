"""
Binance MCP Server implementation using FastMCP.

This module provides a Model Context Protocol (MCP) server for interacting with 
the Binance cryptocurrency exchange API. It exposes Binance functionality as 
tools that can be called by LLM clients.
"""

import sys
import logging
import argparse
from typing import Dict, Any, Optional
from fastmcp import FastMCP
from dotenv import load_dotenv
from binance_mcp_server.security import SecurityConfig, validate_api_credentials, security_audit_log


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)


logger = logging.getLogger(__name__)


mcp = FastMCP(
    name="binance-mcp-server",
    version="1.3.0",  # Updated for futures tools
    instructions="""
    This server provides secure access to Binance cryptocurrency exchange functionality following MCP best practices.
    
    SECURITY FEATURES:
    - Rate limiting to prevent abuse
    - Input validation and sanitization
    - Secure error handling without information leakage
    - Comprehensive audit logging
    - Credential protection
    
    AVAILABLE TOOLS:
    
    == SPOT TRADING ==
    
    Market Data:
    - get_ticker_price: Get current price for a trading symbol
    - get_ticker: Get 24-hour price statistics for a symbol  
    - get_order_book: Get current order book (bids/asks) for a trading symbol
    - get_available_assets: Get exchange trading rules and symbol information
    - get_fee_info: Get trading fee rates (maker/taker commissions) for symbols
    
    Account Management:
    - get_balance: Get account balances for all assets
    - get_account_snapshot: Get account snapshot data
    
    Trading Operations:
    - create_order: Create new trading orders (with enhanced validation)
    - get_orders: Get order history for a specific symbol
    
    Portfolio & Analytics:
    - get_position_info: Get current futures position information
    - get_pnl: Get profit and loss information
    
    Wallet Operations:
    - get_deposit_address: Get deposit address for a specific coin
    - get_deposit_history: Get deposit history for a specific coin
    - get_withdraw_history: Get withdrawal history for a specific coin
    
    Risk Management:
    - get_liquidation_history: Get liquidation history for futures trading
    
    == USDⓈ-M FUTURES TRADING (BTCUSDT/ETHUSDT) ==
    
    Exchange Info & Validation:
    - get_exchange_info_futures: Get trading rules, tickSize, stepSize, minNotional
    - get_commission_rate_futures: Get maker/taker commission rates
    - validate_order_plan_futures: Pre-validate order plans before execution
    
    Position & Risk:
    - get_position_risk_futures: Get position info, liquidation price, unrealized PnL
    - get_leverage_brackets_futures: Get leverage tiers and maintenance margin ratios
    
    Account Settings:
    - set_leverage_futures: Set leverage (idempotent)
    - set_margin_type_futures: Set ISOLATED or CROSSED margin (idempotent)
    
    Order Management:
    - place_order_futures: Place orders with auto price/qty rounding
    - amend_order_futures: Modify LIMIT orders
    - get_order_status_futures: Get order status
    - cancel_order_futures: Cancel single order
    - cancel_multiple_orders_futures: Batch cancel up to 10 orders
    
    Advanced Order Tools:
    - place_bracket_orders_futures: Entry + SL + TPs with OCO-like coordination
    - get_bracket_job_status: Track bracket order jobs
    - cancel_bracket_job: Cancel bracket and all associated orders
    - cancel_on_ttl_futures: Auto-cancel unfilled orders after TTL
    - get_ttl_job_status: Track TTL cancellation jobs
    - cancel_ttl_job: Cancel pending TTL job
    
    Limit Order Analysis Tools (for maker strategy optimization):
    - queue_fill_estimator_futures: Estimate queue position, fill probability, and ETA
    - volume_profile_levels_futures: Calculate VPOC, VAH/VAL, HVN/LVN levels
    
    Advanced Limit Order Analysis Tools (with caching & rate limit handling):
    - liquidity_wall_persistence_futures: Track order book walls, detect spoofing, find magnet levels
    - queue_fill_probability_multi_horizon_futures: Multi-horizon fill probability (60s/300s/900s)
    - volume_profile_fallback_from_trades_futures: VP fallback when main tool is rate-limited
    
    WebSocket-based Tools (NO REST API calls):
    - volume_profile_levels_futures_ws: Real-time VP from WebSocket trade buffer
    - get_ws_buffer_status_futures: Check WebSocket connection and buffer status
    
    All futures tools:
    - Auto-validate against exchange filters (tickSize, stepSize, minNotional)
    - Round prices/quantities to valid precision
    - Handle server time sync and -1021 errors
    - Return both raw_response and normalized_fields
    - Support testnet via BINANCE_TESTNET=true
    
    Tools are implemented in dedicated modules following security best practices.
    """
)


@mcp.tool()
def get_ticker_price(symbol: str) -> Dict[str, Any]:
    """
    Get the current price for a trading symbol on Binance.
    
    This tool fetches real-time price data for any valid trading pair available
    on Binance using the configured environment (production or testnet).
    
    Args:
        symbol: Trading pair symbol in format BASEQUOTE (e.g., 'BTCUSDT', 'ETHBTC')
        
    Returns:
        Dictionary containing success status, price data, and metadata
    """
    logger.info(f"Tool called: get_ticker_price with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.get_ticker_price import get_ticker_price as _get_ticker_price
        result = _get_ticker_price(symbol)
        
        if result.get("success"):
            logger.info(f"Successfully fetched price for {symbol}")
        else:
            logger.warning(f"Failed to fetch price for {symbol}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_ticker_price tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_ticker(symbol: str) -> Dict[str, Any]:
    """
    Get 24-hour ticker price change statistics for a symbol.
    
    Args:
        symbol: Trading pair symbol (e.g., 'BTCUSDT', 'ETHUSDT')
        
    Returns:
        Dictionary containing 24-hour price statistics and metadata.
    """
    logger.info(f"Tool called: get_ticker with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.get_ticker import get_ticker as _get_ticker
        result = _get_ticker(symbol)
        
        if result.get("success"):
            logger.info(f"Successfully fetched ticker stats for {symbol}")
        else:
            logger.warning(f"Failed to fetch ticker stats for {symbol}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_ticker tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_available_assets() -> Dict[str, Any]:
    """
    Get a list of all available assets and trading pairs on Binance.
    
    Returns:
        Dictionary containing comprehensive exchange information and available assets.
    """
    logger.info("Tool called: get_available_assets")
    
    try:
        from binance_mcp_server.tools.get_available_assets import get_available_assets as _get_available_assets
        result = _get_available_assets()
        
        if result.get("success"):
            logger.info("Successfully fetched available assets")
        else:
            logger.warning(f"Failed to fetch available assets: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_available_assets tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_balance() -> Dict[str, Any]:
    """
    Get the current account balance for all assets on Binance.
    
    This tool retrieves the balances of all assets in the user's Binance account,
    including available and locked amounts.
    
    Returns:
        Dictionary containing success status, asset balances, and metadata.
    """
    logger.info("Tool called: get_balance")
    
    try:
        from binance_mcp_server.tools.get_balance import get_balance as _get_balance
        result = _get_balance()
        
        if result.get("success"):
            logger.info("Successfully fetched account balances")
        else:
            logger.warning(f"Failed to fetch account balances: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_balance tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_orders(symbol: str, start_time: Optional[int] = None, end_time: Optional[int] = None) -> Dict[str, Any]:
    """
    Get all orders for a specific trading symbol on Binance.
    
    Args:
        symbol: Trading pair symbol (e.g., 'BTCUSDT', 'ETHUSDT')
        start_time: Optional start time for filtering orders (Unix timestamp)
        end_time: Optional end time for filtering orders (Unix timestamp)
        
    Returns:
        Dictionary containing success status, order data, and metadata.
    """
    logger.info(f"Tool called: get_orders with symbol={symbol}, start_time={start_time}, end_time={end_time}")
    
    try:
        from binance_mcp_server.tools.get_orders import get_orders as _get_orders
        result = _get_orders(symbol, start_time=start_time, end_time=end_time)

        if result.get("success"):
            logger.info(f"Successfully fetched orders for {symbol}")
        else:
            logger.warning(f"Failed to fetch orders for {symbol}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_orders tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_position_info() -> Dict[str, Any]:
    """
    Get the current position information for the user on Binance.
    
    This tool retrieves the user's current positions in futures trading.
    
    Returns:
        Dictionary containing success status, position data, and metadata.
    """
    logger.info("Tool called: get_position_info")
    
    try:
        from binance_mcp_server.tools.get_position_info import get_position_info as _get_position_info
        result = _get_position_info()
        
        if result.get("success"):
            logger.info("Successfully fetched position info")
        else:
            logger.warning(f"Failed to fetch position info: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_position_info tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_pnl() -> Dict[str, Any]:
    """
    Get the current profit and loss (PnL) information for the user on Binance.
    
    This tool retrieves the user's PnL data for futures trading.
    
    Returns:
        Dictionary containing success status, PnL data, and metadata.
    """
    logger.info("Tool called: get_pnl")
    
    try:
        from binance_mcp_server.tools.get_pnl import get_pnl as _get_pnl
        result = _get_pnl()
        
        if result.get("success"):
            logger.info("Successfully fetched PnL info")
        else:
            logger.warning(f"Failed to fetch PnL info: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_pnl tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def create_order(
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Create a new order on Binance.
    
    Args:
        symbol: Trading pair symbol (e.g., 'BTCUSDT').
        side: Order side ('BUY' or 'SELL').
        order_type: Type of order ('LIMIT', 'MARKET', etc.).
        quantity: Quantity of the asset to buy/sell.
        price: Price for limit orders (optional).
        
    Returns:
        Dictionary containing success status and order data.
    """
    logger.info(f"Tool called: create_order with symbol={symbol}, side={side}, type={order_type}, quantity={quantity}, price={price}")
    
    try:
        from binance_mcp_server.tools.create_order import create_order as _create_order
        result = _create_order(symbol, side, order_type, quantity, price)
        
        if result.get("success"):
            logger.info(f"Successfully created order for {symbol}")
        else:
            logger.warning(f"Failed to create order for {symbol}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in create_order tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_liquidation_history() -> Dict[str, Any]:
    """
    Get the liquidation history on Binance account.
    
    This tool retrieves the user's liquidation orders in futures trading.
    
    Returns:
        Dictionary containing success status and liquidation history data.
    """
    logger.info("Tool called: get_liquidation_history")
    
    try:
        from binance_mcp_server.tools.get_liquidation_history import get_liquidation_history as _get_liquidation_history
        result = _get_liquidation_history()
        
        if result.get("success"):
            logger.info("Successfully fetched liquidation history")
        else:
            logger.warning(f"Failed to fetch liquidation history: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_liquidation_history tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }

@mcp.tool()
def get_deposit_address(coin: str) -> Dict[str, Any]:
    """
    Get the deposit address for a specific coin on the user's Binance account.
    
    Args:
        coin (str): The coin for which to fetch the deposit address.
        
    Returns:
        Dictionary containing success status and deposit address data.
    """
    logger.info(f"Tool called: get_deposit_address with coin={coin}")
    
    try:
        from binance_mcp_server.tools.get_deposit_address import get_deposit_address as _get_deposit_address
        result = _get_deposit_address(coin)
        
        if result.get("success"):
            logger.info(f"Successfully fetched deposit address for {coin}")
        else:
            logger.warning(f"Failed to fetch deposit address for {coin}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_deposit_address tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_deposit_history(coin: str) -> Dict[str, Any]:
    """
    Get the deposit history for a specific coin on the user's Binance account.
    
    Args:
        coin (str): The coin for which to fetch the deposit history.
        
    Returns:
        Dictionary containing success status and deposit history data.
    """
    logger.info(f"Tool called: get_deposit_history with coin={coin}")
    
    try:
        from binance_mcp_server.tools.get_deposit_history import get_deposit_history as _get_deposit_history
        result = _get_deposit_history(coin)
        
        if result.get("success"):
            logger.info(f"Successfully fetched deposit history for {coin}")
        else:
            logger.warning(f"Failed to fetch deposit history for {coin}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_deposit_history tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_withdraw_history(coin: str) -> Dict[str, Any]:
    """
    Get the withdrawal history for the user's Binance account.
    
    Args:
        coin (Optional[str]): The coin for which to fetch the withdrawal history. Defaults to 'BTC'.
        
    Returns:
        Dictionary containing success status and withdrawal history data.
    """
    logger.info(f"Tool called: get_withdraw_history with coin={coin}")
    
    try:
        from binance_mcp_server.tools.get_withdraw_history import get_withdraw_history as _get_withdraw_history
        result = _get_withdraw_history(coin)
        
        if result.get("success"):
            logger.info(f"Successfully fetched withdrawal history for {coin}")
        else:
            logger.warning(f"Failed to fetch withdrawal history for {coin}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_withdraw_history tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_account_snapshot(account_type: str = "SPOT") -> Dict[str, Any]:
    """
    Get the account snapshot for the user's Binance account.
    
    Args:
        account_type (str): The account type to filter the snapshot. Defaults to "SPOT".
        
    Returns:
        Dictionary containing success status and account snapshot data.
    """
    logger.info(f"Tool called: get_account_snapshot with account_type={account_type}")
    
    try:
        from binance_mcp_server.tools.get_account_snapshot import get_account_snapshot as _get_account_snapshot
        result = _get_account_snapshot(account_type)
        
        if result.get("success"):
            logger.info(f"Successfully fetched account snapshot for {account_type} account")
        else:
            logger.warning(f"Failed to fetch account snapshot for {account_type}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_account_snapshot tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_fee_info(symbol: Optional[str] = None) -> Dict[str, Any]:
    """
    Get trading fee information for symbols on Binance.
    
    This tool retrieves trading fee rates including maker and taker commissions
    for spot trading. Fee information is essential for calculating trading costs
    and optimizing trading strategies.
    
    Args:
        symbol (Optional[str]): Specific trading pair symbol to get fees for.
                               If not provided, returns fees for all symbols.
                               Format: 'BTCUSDT', 'ETHUSDT', etc.
        
    Returns:
        Dictionary containing success status, fee data, and metadata.
    """
    logger.info(f"Tool called: get_fee_info with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.get_fee_info import get_fee_info as _get_fee_info
        result = _get_fee_info(symbol)
        
        if result.get("success"):
            fee_count = len(result.get("data", []))
            logger.info(f"Successfully fetched fee information for {fee_count} symbol(s)")
        else:
            logger.warning(f"Failed to fetch fee information: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_fee_info tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


@mcp.tool()
def get_order_book(symbol: str, limit: Optional[int] = None) -> Dict[str, Any]:
    """
    Get the current order book (bids/asks) for a trading symbol on Binance.
    
    This tool fetches real-time order book data for any valid trading pair available
    on Binance. The order book contains arrays of bid and ask orders with their prices
    and quantities, essential for trading/finance operations.
    
    Args:
        symbol: Trading pair symbol in format BASEQUOTE (e.g., 'BTCUSDT', 'ETHBTC')
        limit: Optional limit for number of orders per side (default: 100, max: 5000)
        
    Returns:
        Dictionary containing success status, order book data, and metadata.
    """
    logger.info(f"Tool called: get_order_book with symbol={symbol}, limit={limit}")
    
    try:
        from binance_mcp_server.tools.get_order_book import get_order_book as _get_order_book
        result = _get_order_book(symbol, limit)
        
        if result.get("success"):
            data = result.get("data", {})
            bid_count = data.get("bidCount", 0)
            ask_count = data.get("askCount", 0)
            logger.info(f"Successfully fetched order book for {symbol}: {bid_count} bids, {ask_count} asks")
        else:
            logger.warning(f"Failed to fetch order book for {symbol}: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_order_book tool: {str(e)}")
        return {
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"Tool execution failed: {str(e)}"
            }
        }


# ============================================================================
# USDⓈ-M FUTURES TOOLS
# These tools provide comprehensive access to Binance USDⓈ-M Futures trading
# for BTCUSDT and ETHUSDT perpetual contracts.
# ============================================================================


@mcp.tool()
def get_exchange_info_futures(symbol: str) -> Dict[str, Any]:
    """
    Get exchange information for a USDⓈ-M Futures symbol.
    
    Retrieves trading rules, filters, and precision settings essential for
    order validation and price/quantity rounding.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        
    Returns:
        Dictionary containing tickSize, stepSize, minQty, minNotional, 
        pricePrecision, qtyPrecision, maxLeverage, filters, and serverTime.
    """
    logger.info(f"Tool called: get_exchange_info_futures with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.futures import get_exchange_info_futures as _get_exchange_info_futures
        result = _get_exchange_info_futures(symbol)
        
        if result.get("success"):
            logger.info(f"Successfully fetched futures exchange info for {symbol}")
        else:
            logger.warning(f"Failed to fetch futures exchange info: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_exchange_info_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def get_commission_rate_futures(symbol: str) -> Dict[str, Any]:
    """
    Get user's commission rate for a USDⓈ-M Futures symbol.
    
    Retrieves maker and taker commission rates for accurate trading cost calculation.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        
    Returns:
        Dictionary containing makerCommissionRate, takerCommissionRate with
        both string and float values, plus percentage representations.
    """
    logger.info(f"Tool called: get_commission_rate_futures with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.futures import get_commission_rate_futures as _get_commission_rate_futures
        result = _get_commission_rate_futures(symbol)
        
        if result.get("success"):
            logger.info(f"Successfully fetched commission rate for {symbol}")
        else:
            logger.warning(f"Failed to fetch commission rate: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_commission_rate_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def get_position_risk_futures(symbol: Optional[str] = None) -> Dict[str, Any]:
    """
    Get position risk information for USDⓈ-M Futures.
    
    Retrieves comprehensive position information including position size,
    entry price, mark price, liquidation price, leverage, margin, and unrealized PnL.
    
    Args:
        symbol: Optional trading pair symbol (BTCUSDT or ETHUSDT).
                If not provided, returns positions for all allowed symbols.
        
    Returns:
        Dictionary containing position data with normalized fields for easy access.
    """
    logger.info(f"Tool called: get_position_risk_futures with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.futures import get_position_risk as _get_position_risk
        result = _get_position_risk(symbol)
        
        if result.get("success"):
            logger.info(f"Successfully fetched position risk for {symbol if symbol else 'all symbols'}")
        else:
            logger.warning(f"Failed to fetch position risk: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_position_risk_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def get_leverage_brackets_futures(
    symbol: Optional[str] = None,
    notional_for_mmr: Optional[float] = None
) -> Dict[str, Any]:
    """
    Get leverage brackets for USDⓈ-M Futures symbol(s).
    
    Retrieves notional value tiers with max leverage and maintenance margin ratios.
    Essential for calculating liquidation price and margin requirements.
    
    Args:
        symbol: Optional trading pair symbol (BTCUSDT or ETHUSDT).
        notional_for_mmr: Optional notional value to calculate specific MMR for.
        
    Returns:
        Dictionary containing brackets organized by symbol, with maxLeverage,
        maintMarginRatio, and optional mmr_for_notional calculation.
    """
    logger.info(f"Tool called: get_leverage_brackets_futures with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.futures import get_leverage_brackets as _get_leverage_brackets
        result = _get_leverage_brackets(symbol, notional_for_mmr)
        
        if result.get("success"):
            logger.info(f"Successfully fetched leverage brackets for {symbol if symbol else 'all symbols'}")
        else:
            logger.warning(f"Failed to fetch leverage brackets: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_leverage_brackets_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def set_leverage_futures(symbol: str, leverage: int) -> Dict[str, Any]:
    """
    Set leverage for a USDⓈ-M Futures symbol.
    
    Changes the leverage multiplier. Idempotent - returns success with
    already_set=true if leverage is already at requested value.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        leverage: Target leverage (1-125 for BTC, varies by symbol)
        
    Returns:
        Dictionary containing leverage setting result and maxNotionalValue.
    """
    logger.info(f"Tool called: set_leverage_futures with symbol={symbol}, leverage={leverage}")
    
    try:
        from binance_mcp_server.tools.futures import set_leverage as _set_leverage
        result = _set_leverage(symbol, leverage)
        
        if result.get("success"):
            logger.info(f"Successfully set leverage for {symbol} to {leverage}x")
        else:
            logger.warning(f"Failed to set leverage: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in set_leverage_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def set_margin_type_futures(symbol: str, margin_type: str) -> Dict[str, Any]:
    """
    Set margin type for a USDⓈ-M Futures symbol.
    
    Changes between isolated and cross margin modes. Idempotent - returns
    success with already_set=true if margin type is already set.
    Note: Cannot change with open positions.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        margin_type: "ISOLATED" or "CROSSED"
        
    Returns:
        Dictionary containing margin type setting result.
    """
    logger.info(f"Tool called: set_margin_type_futures with symbol={symbol}, margin_type={margin_type}")
    
    try:
        from binance_mcp_server.tools.futures import set_margin_type as _set_margin_type
        result = _set_margin_type(symbol, margin_type)
        
        if result.get("success"):
            logger.info(f"Successfully set margin type for {symbol} to {margin_type}")
        else:
            logger.warning(f"Failed to set margin type: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in set_margin_type_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
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
    
    Creates a futures order with automatic validation against exchange filters
    (tickSize, stepSize, minNotional) before submission.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        side: "BUY" or "SELL"
        order_type: LIMIT, MARKET, STOP, STOP_MARKET, TAKE_PROFIT, 
                   TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET
        quantity: Order quantity (required unless closePosition=true)
        price: Limit price (required for LIMIT, STOP, TAKE_PROFIT)
        stop_price: Stop/trigger price (for STOP/TAKE_PROFIT types)
        time_in_force: GTC, IOC, FOK, or GTX (GTX = post-only)
        reduce_only: If true, can only reduce position
        close_position: If true, close entire position
        position_side: BOTH, LONG, or SHORT (for hedge mode)
        working_type: MARK_PRICE or CONTRACT_PRICE (for stop orders)
        post_only: If true, sets timeInForce=GTX
        client_order_id: Custom order ID
        callback_rate: Callback rate for trailing stop (1-5%)
        activation_price: Activation price for trailing stop
        price_protect: Enable price protection for stop orders
        
    Returns:
        Dictionary containing order details and validation information.
    """
    logger.info(f"Tool called: place_order_futures with symbol={symbol}, side={side}, type={order_type}")
    
    try:
        from binance_mcp_server.tools.futures import place_order_futures as _place_order_futures
        result = _place_order_futures(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            close_position=close_position,
            position_side=position_side,
            working_type=working_type,
            post_only=post_only,
            client_order_id=client_order_id,
            callback_rate=callback_rate,
            activation_price=activation_price,
            price_protect=price_protect,
        )
        
        if result.get("success"):
            logger.info(f"Successfully placed futures order for {symbol}")
        else:
            logger.warning(f"Failed to place futures order: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in place_order_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
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
    
    Modifies price and/or quantity of an existing LIMIT order.
    Note: Only LIMIT orders can be modified.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id: Order ID to modify
        orig_client_order_id: Client order ID to modify
        price: New price (optional)
        quantity: New quantity (optional)
        side: Order side BUY/SELL (required)
        
    Returns:
        Dictionary containing modified order information.
    """
    logger.info(f"Tool called: amend_order_futures with symbol={symbol}, orderId={order_id}")
    
    try:
        from binance_mcp_server.tools.futures import amend_order_futures as _amend_order_futures
        result = _amend_order_futures(
            symbol=symbol,
            order_id=order_id,
            orig_client_order_id=orig_client_order_id,
            price=price,
            quantity=quantity,
            side=side,
        )
        
        if result.get("success"):
            logger.info(f"Successfully amended futures order for {symbol}")
        else:
            logger.warning(f"Failed to amend futures order: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in amend_order_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def get_order_status_futures(
    symbol: str,
    order_id: Optional[int] = None,
    orig_client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get the status of an order for USDⓈ-M Futures.
    
    Retrieves detailed information about a specific order including status,
    filled quantity, and execution details.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id: Order ID (either this or orig_client_order_id required)
        orig_client_order_id: Client order ID
        
    Returns:
        Dictionary containing order status with computed fields like
        isFilled, isActive, fillPercentage.
    """
    logger.info(f"Tool called: get_order_status_futures with symbol={symbol}, orderId={order_id}")
    
    try:
        from binance_mcp_server.tools.futures import get_order_status_futures as _get_order_status_futures
        result = _get_order_status_futures(
            symbol=symbol,
            order_id=order_id,
            orig_client_order_id=orig_client_order_id,
        )
        
        if result.get("success"):
            logger.info(f"Successfully fetched order status for {symbol}")
        else:
            logger.warning(f"Failed to fetch order status: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in get_order_status_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def cancel_order_futures(
    symbol: str,
    order_id: Optional[int] = None,
    orig_client_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cancel an open order for USDⓈ-M Futures.
    
    Cancels a single open order by orderId or clientOrderId.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id: Order ID to cancel
        orig_client_order_id: Client order ID to cancel
        
    Returns:
        Dictionary containing cancelled order information.
    """
    logger.info(f"Tool called: cancel_order_futures with symbol={symbol}, orderId={order_id}")
    
    try:
        from binance_mcp_server.tools.futures import cancel_order_futures as _cancel_order_futures
        result = _cancel_order_futures(
            symbol=symbol,
            order_id=order_id,
            orig_client_order_id=orig_client_order_id,
        )
        
        if result.get("success"):
            logger.info(f"Successfully cancelled futures order for {symbol}")
        else:
            logger.warning(f"Failed to cancel futures order: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_order_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def cancel_multiple_orders_futures(
    symbol: str,
    order_id_list: Optional[list] = None,
    orig_client_order_id_list: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Cancel multiple open orders for USDⓈ-M Futures in a single request.
    
    Cancels up to 10 orders in a batch. Provide either orderIdList or
    origClientOrderIdList (not both).
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id_list: List of order IDs to cancel (max 10)
        orig_client_order_id_list: List of client order IDs to cancel (max 10)
        
    Returns:
        Dictionary containing summary and details of cancelled/failed orders.
    """
    logger.info(f"Tool called: cancel_multiple_orders_futures with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.futures import cancel_multiple_orders_futures as _cancel_multiple_orders_futures
        result = _cancel_multiple_orders_futures(
            symbol=symbol,
            order_id_list=order_id_list,
            orig_client_order_id_list=orig_client_order_id_list,
        )
        
        if result.get("success"):
            data = result.get("data", {})
            logger.info(f"Batch cancel: {data.get('successCount', 0)} succeeded, {data.get('failedCount', 0)} failed")
        else:
            logger.warning(f"Failed batch cancel: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_multiple_orders_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def validate_order_plan_futures(
    symbol: str,
    side: str,
    entry_price: float,
    quantity: float,
    stop_loss: Optional[float] = None,
    take_profits: Optional[list] = None,
    post_only: bool = False,
    leverage: Optional[int] = None,
    margin_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate an order plan against exchange filters before execution.
    
    Pre-validates a trading plan (entry + SL + TPs) against exchange rules,
    returning rounded values and potential issues.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        side: Order side (BUY or SELL)
        entry_price: Intended entry price
        quantity: Order quantity
        stop_loss: Optional stop loss price
        take_profits: Optional list of TP specs [{price, quantity or percentage}]
        post_only: Whether entry should be post-only
        leverage: Intended leverage (for validation)
        margin_type: Intended margin type (ISOLATED/CROSSED)
        
    Returns:
        Dictionary containing validation result, rounded values, issues, and fixes.
    """
    logger.info(f"Tool called: validate_order_plan_futures with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.futures import validate_order_plan_futures as _validate_order_plan_futures
        result = _validate_order_plan_futures(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profits=take_profits,
            post_only=post_only,
            leverage=leverage,
            margin_type=margin_type,
        )
        
        valid = result.get("valid", False)
        logger.info(f"Order plan validation for {symbol}: {'valid' if valid else 'invalid'}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in validate_order_plan_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def place_bracket_orders_futures(
    symbol: str,
    side: str,
    entry_price: float,
    quantity: float,
    stop_loss_price: Optional[float] = None,
    take_profits: Optional[list] = None,
    entry_type: str = "LIMIT",
    time_in_force: str = "GTC",
    post_only: bool = False,
    reduce_only: bool = False,
    working_type: str = "CONTRACT_PRICE",
    wait_for_entry: bool = True,
) -> Dict[str, Any]:
    """
    Place a complete bracket order: entry + stop loss + take profits.
    
    Orchestrates placement of entry with coordinated exit orders.
    Handles reduceOnly timing and implements OCO-like behavior.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        side: Entry side (BUY for long, SELL for short)
        entry_price: Entry limit price
        quantity: Total position quantity
        stop_loss_price: Stop loss trigger price
        take_profits: List of TP specs [{price, quantity or percentage}]
        entry_type: "LIMIT" or "MARKET"
        time_in_force: "GTC", "IOC", "FOK", or "GTX"
        post_only: If true, uses GTX for entry
        reduce_only: If true, entry is reduceOnly
        working_type: "MARK_PRICE" or "CONTRACT_PRICE" for exits
        wait_for_entry: If true, waits for entry fill before placing exits
        
    Returns:
        Dictionary containing job_id for tracking and order details.
    """
    logger.info(f"Tool called: place_bracket_orders_futures with symbol={symbol}, side={side}")
    
    try:
        from binance_mcp_server.tools.futures import place_bracket_orders_futures as _place_bracket_orders_futures
        result = _place_bracket_orders_futures(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss_price=stop_loss_price,
            take_profits=take_profits,
            entry_type=entry_type,
            time_in_force=time_in_force,
            post_only=post_only,
            reduce_only=reduce_only,
            working_type=working_type,
            wait_for_entry=wait_for_entry,
        )
        
        if result.get("success"):
            logger.info(f"Successfully initiated bracket order for {symbol}")
        else:
            logger.warning(f"Failed bracket order: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in place_bracket_orders_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def get_bracket_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get the status of a bracket order job.
    
    Args:
        job_id: The job ID returned from place_bracket_orders_futures
        
    Returns:
        Dictionary containing job status and order details.
    """
    logger.info(f"Tool called: get_bracket_job_status with job_id={job_id}")
    
    try:
        from binance_mcp_server.tools.futures.bracket_orders import get_bracket_job_status as _get_bracket_job_status
        return _get_bracket_job_status(job_id)
        
    except Exception as e:
        logger.error(f"Unexpected error in get_bracket_job_status tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def cancel_bracket_job(job_id: str) -> Dict[str, Any]:
    """
    Cancel a bracket order job and its associated orders.
    
    Args:
        job_id: The job ID to cancel
        
    Returns:
        Dictionary with cancellation results.
    """
    logger.info(f"Tool called: cancel_bracket_job with job_id={job_id}")
    
    try:
        from binance_mcp_server.tools.futures.bracket_orders import cancel_bracket_job as _cancel_bracket_job
        return _cancel_bracket_job(job_id)
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_bracket_job tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def cancel_on_ttl_futures(
    symbol: str,
    order_id: Optional[int] = None,
    orig_client_order_id: Optional[str] = None,
    ttl_seconds: float = 60,
    blocking: bool = False,
) -> Dict[str, Any]:
    """
    Cancel an order after a time-to-live period expires.
    
    Schedules order cancellation after TTL. If order fills before TTL,
    no cancellation is attempted.
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT only)
        order_id: Order ID to cancel
        orig_client_order_id: Client order ID to cancel
        ttl_seconds: Time to wait before cancelling (max: 600)
        blocking: If true, waits for TTL and returns final result
        
    Returns:
        For non-blocking: job_id to track cancellation
        For blocking: final order status after TTL
    """
    logger.info(f"Tool called: cancel_on_ttl_futures with symbol={symbol}, ttl={ttl_seconds}s")
    
    try:
        from binance_mcp_server.tools.futures import cancel_on_ttl as _cancel_on_ttl
        result = _cancel_on_ttl(
            symbol=symbol,
            order_id=order_id,
            orig_client_order_id=orig_client_order_id,
            ttl_seconds=ttl_seconds,
            blocking=blocking,
        )
        
        if result.get("success"):
            logger.info(f"TTL cancellation initiated for {symbol}")
        else:
            logger.warning(f"TTL cancellation failed: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_on_ttl_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def get_ttl_job_status(job_id: str) -> Dict[str, Any]:
    """
    Get the status of a TTL cancellation job.
    
    Args:
        job_id: The job ID returned from cancel_on_ttl_futures
        
    Returns:
        Dictionary containing job status and result if completed.
    """
    logger.info(f"Tool called: get_ttl_job_status with job_id={job_id}")
    
    try:
        from binance_mcp_server.tools.futures.cancel_on_ttl import get_ttl_job_status as _get_ttl_job_status
        return _get_ttl_job_status(job_id)
        
    except Exception as e:
        logger.error(f"Unexpected error in get_ttl_job_status tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def cancel_ttl_job(job_id: str) -> Dict[str, Any]:
    """
    Cancel a pending TTL job before it executes.
    
    Args:
        job_id: The job ID to cancel
        
    Returns:
        Dictionary confirming cancellation.
    """
    logger.info(f"Tool called: cancel_ttl_job with job_id={job_id}")
    
    try:
        from binance_mcp_server.tools.futures.cancel_on_ttl import cancel_ttl_job as _cancel_ttl_job
        return _cancel_ttl_job(job_id)
        
    except Exception as e:
        logger.error(f"Unexpected error in cancel_ttl_job tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


# ============================================================================
# LIMIT ORDER ANALYSIS TOOLS
# These tools provide advanced limit order placement analysis for maker strategies.
# ============================================================================


@mcp.tool()
def queue_fill_estimator_futures(
    symbol: str,
    side: str,
    price_levels: list,
    qty: float,
    lookback_seconds: float = 30.0
) -> Dict[str, Any]:
    """
    Estimate queue fill probability and ETA for limit orders.
    
    Analyzes orderbook depth and trade flow to estimate:
    - Queue position at each price level
    - Fill probability within 30s/60s windows
    - Estimated time to fill (median and 95th percentile)
    - Adverse selection risk score
    - Market microstructure health
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT)
        side: Order side ("BUY" or "SELL")
        price_levels: List of price levels to analyze (max 5)
        qty: Order quantity
        lookback_seconds: Lookback window for trade analysis (5-300s, default 30s)
        
    Returns:
        Dictionary containing:
        - per_level: Analysis for each price level
        - global: Market microstructure metrics and recommendation
        - quality_flags: Data quality indicators
        
    Example response structure:
        {
            "ts_ms": 1703123456789,
            "inputs": {...},
            "per_level": [
                {
                    "price": 42000.0,
                    "queue_qty_est": 12.5,
                    "queue_value_usd": 525000,
                    "consumption_rate_qty_per_s": 0.85,
                    "eta_p50_s": 7.3,
                    "eta_p95_s": 22.1,
                    "fill_prob_30s": 0.82,
                    "fill_prob_60s": 0.97,
                    "adverse_selection_score": 25.5,
                    "notes_max2": ["Normal conditions"]
                }
            ],
            "global": {
                "micro_health_score": 85.2,
                "spread_bps": 0.5,
                "obi_mean": 0.12,
                "obi_stdev": 0.08,
                "wall_risk_level": "low",
                "recommendation": {"best_price": 42000.0, "why": "High fill probability"}
            }
        }
    """
    logger.info(f"Tool called: queue_fill_estimator_futures with symbol={symbol}, side={side}")
    
    try:
        from binance_mcp_server.tools.futures import queue_fill_estimator as _queue_fill_estimator
        result = _queue_fill_estimator(
            symbol=symbol,
            side=side,
            price_levels=price_levels,
            qty=qty,
            lookback_seconds=lookback_seconds
        )
        
        if result.get("success"):
            logger.info(f"Successfully analyzed queue for {symbol}")
        else:
            logger.warning(f"Queue analysis failed: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in queue_fill_estimator_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def volume_profile_levels_futures(
    symbol: str,
    window_minutes: int = 240,
    bin_size: Optional[float] = None
) -> Dict[str, Any]:
    """
    Calculate volume profile levels for market structure analysis.
    
    Analyzes trade data to identify key structural levels:
    - VPOC (Volume Point of Control): Highest volume price
    - VAH/VAL (Value Area High/Low): 70% volume range
    - HVN (High Volume Nodes): Strong support/resistance
    - LVN (Low Volume Nodes): Price moves quickly through
    - Single Print Zones: Gaps in volume distribution
    - Magnet Levels: Prices that attract price action
    - Avoid Zones: Risky areas for limit orders
    
    Args:
        symbol: Trading pair symbol (BTCUSDT or ETHUSDT)
        window_minutes: Time window in minutes (15-1440, default 240 = 4 hours)
        bin_size: Price bin size in USD (auto-calculated if None)
        
    Returns:
        Dictionary containing:
        - window: Time window and trade statistics
        - levels: All calculated structural levels
        - quality_flags: Data quality indicators
        
    Example response structure:
        {
            "ts_ms": 1703123456789,
            "window": {
                "requested_minutes": 240,
                "actual_minutes": 238.5,
                "trade_count": 15234,
                "bin_size": 25.0,
                "price_range": {"low": 41500.0, "high": 43200.0}
            },
            "levels": {
                "vpoc": 42350.0,
                "vah": 42800.0,
                "val": 41900.0,
                "hvn": [42350.0, 42100.0, 42600.0],
                "lvn": [42475.0, 41975.0, 42725.0],
                "single_print_zones": [{"low": 42900.0, "high": 43000.0}],
                "magnet_levels": [42350.0, 42800.0, 41900.0],
                "avoid_zones": [
                    {"price": 42475.0, "reason": "LVN - quick price movement"}
                ]
            }
        }
    """
    logger.info(f"Tool called: volume_profile_levels_futures with symbol={symbol}, window={window_minutes}min")
    
    try:
        from binance_mcp_server.tools.futures import volume_profile_levels as _volume_profile_levels
        result = _volume_profile_levels(
            symbol=symbol,
            window_minutes=window_minutes,
            bin_size=bin_size
        )
        
        if result.get("success"):
            levels = result.get("levels", {})
            logger.info(f"Successfully calculated volume profile for {symbol}: VPOC={levels.get('vpoc')}")
        else:
            logger.warning(f"Volume profile failed: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in volume_profile_levels_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


# ============================================================================
# ADVANCED LIMIT ORDER ANALYSIS TOOLS
# These tools provide advanced market microstructure analysis with caching
# and exponential backoff for rate limit handling.
# ============================================================================


@mcp.tool()
def liquidity_wall_persistence_futures(
    symbol: str,
    depth_limit: int = 50,
    window_seconds: int = 60,
    sample_interval_ms: int = 1000,
    top_n: int = 5,
    wall_threshold_usd: float = 1000000
) -> Dict[str, Any]:
    """
    Track order book walls and detect spoofing patterns.
    
    Samples the orderbook over a time window to identify:
    - Persistent bid/ask walls (true liquidity)
    - Spoof patterns (appearing/disappearing, unstable notionals)
    - Magnet levels (strong attraction points)
    - Avoid zones (high spoof risk areas)
    
    Features:
    - 60-second cache for identical parameters
    - Exponential backoff with jitter on rate limits
    - Compressed statistics output (no large arrays)
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        depth_limit: Orderbook depth to fetch (default 50, max 100)
        window_seconds: Sampling window duration (default 60, max 300)
        sample_interval_ms: Time between samples (default 1000ms, min 500ms)
        top_n: Number of top walls per side (default 5, max 10)
        wall_threshold_usd: Minimum notional to consider a wall (default 1M USD)
        
    Returns:
        Dictionary containing:
        - bid_walls: List of bid walls with persistence scores (<=top_n)
        - ask_walls: List of ask walls with persistence scores (<=top_n)
        - spoof_risk_score_0_100: Overall spoofing risk assessment
        - magnet_levels: High-persistence price levels (<=6)
        - avoid_zones: Zones with high spoof risk (<=4)
        - notes: Summary notes
        
    Example response:
        {
            "success": true,
            "ts_ms": 1703123456789,
            "bid_walls": [
                {"price": 42000.0, "notional_usd": 2500000, "persistence_score_0_100": 85.5, "avg_life_sec": 45.2}
            ],
            "ask_walls": [...],
            "spoof_risk_score_0_100": 25.0,
            "magnet_levels": [42000.0, 42500.0],
            "avoid_zones": [{"low": 42100, "high": 42150, "reason": "ask_spoof_risk"}],
            "notes": ["Low spoof activity", "2 strong magnet levels identified"]
        }
    """
    logger.info(f"Tool called: liquidity_wall_persistence_futures with symbol={symbol}, window={window_seconds}s")
    
    try:
        from binance_mcp_server.tools.futures import liquidity_wall_persistence as _liquidity_wall_persistence
        result = _liquidity_wall_persistence(
            symbol=symbol,
            depth_limit=depth_limit,
            window_seconds=window_seconds,
            sample_interval_ms=sample_interval_ms,
            top_n=top_n,
            wall_threshold_usd=wall_threshold_usd
        )
        
        if result.get("success"):
            spoof_score = result.get("spoof_risk_score_0_100", 0)
            logger.info(f"Wall persistence analysis complete for {symbol}: spoof_risk={spoof_score}")
        else:
            logger.warning(f"Wall persistence failed: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in liquidity_wall_persistence_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def queue_fill_probability_multi_horizon_futures(
    symbol: str,
    side: str,
    price_levels: list,
    qty: float,
    horizons_sec: Optional[list] = None,
    lookback_sec: int = 120,
    assume_queue_position: str = "mid"
) -> Dict[str, Any]:
    """
    Estimate fill probability across multiple time horizons.
    
    Uses historical trade flow and orderbook depth to estimate:
    - Fill probability at each horizon (e.g., 60s, 300s, 900s)
    - Estimated time to fill (P50)
    - Adverse selection risk score
    
    Features:
    - 30-second cache for identical parameters
    - Exponential backoff with jitter on rate limits
    - Compressed statistics output (no large arrays)
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        side: Position side ("LONG" for buy, "SHORT" for sell)
        price_levels: Price levels to analyze (max 5)
        qty: Order quantity
        horizons_sec: Time horizons in seconds (default [60, 300, 900], max 5 horizons)
        lookback_sec: Lookback for trade flow analysis (default 120, max 600)
        assume_queue_position: Queue assumption ("best_case", "mid", "worst_case")
        
    Returns:
        Dictionary containing:
        - per_level: Analysis for each price level (<=5)
            - price: Price level
            - fill_prob: Dict of horizon -> probability (0-1)
            - eta_sec_p50: Estimated time to fill (median)
            - adverse_selection_score_0_100: Risk score
        - overall_best_level: Recommended price level
        - quality_flags: Data quality indicators (<=6)
        - confidence_0_1: Confidence in estimates
        
    Example response:
        {
            "success": true,
            "ts_ms": 1703123456789,
            "per_level": [
                {
                    "price": 42000.0,
                    "fill_prob": {60: 0.45, 300: 0.82, 900: 0.96},
                    "eta_sec_p50": 180.5,
                    "adverse_selection_score_0_100": 30.0
                }
            ],
            "overall_best_level": 42000.0,
            "confidence_0_1": 0.75
        }
    """
    logger.info(f"Tool called: queue_fill_probability_multi_horizon_futures with symbol={symbol}, side={side}")
    
    try:
        from binance_mcp_server.tools.futures import queue_fill_probability_multi_horizon as _queue_fill_prob
        result = _queue_fill_prob(
            symbol=symbol,
            side=side,
            price_levels=price_levels,
            qty=qty,
            horizons_sec=horizons_sec,
            lookback_sec=lookback_sec,
            assume_queue_position=assume_queue_position
        )
        
        if result.get("success"):
            best = result.get("overall_best_level")
            logger.info(f"Fill probability analysis complete for {symbol}: best_level={best}")
        else:
            logger.warning(f"Fill probability failed: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in queue_fill_probability_multi_horizon_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def volume_profile_fallback_from_trades_futures(
    symbol: str,
    lookback_minutes: int = 240,
    bin_size: Optional[float] = None,
    max_trades: int = 5000
) -> Dict[str, Any]:
    """
    Calculate volume profile from trades (fallback when main VP tool is rate-limited).
    
    Provides simplified but reliable VP key structure levels:
    - vPOC (Volume Point of Control)
    - VAH/VAL (Value Area at 70%)
    - HVN/LVN levels
    - Magnet levels
    - Avoid zones
    
    Use this when volume_profile_levels_futures hits rate limits or is unavailable.
    
    Features:
    - 45-second cache for identical parameters
    - Exponential backoff with jitter on rate limits
    - Uses aggTrades as data source
    - Compressed statistics output (<=12 levels total)
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        lookback_minutes: Time window in minutes (default 240, max 360)
        bin_size: Price bin size (default 25, auto-calculated if None)
        max_trades: Maximum trades to process (default 5000, max 5000)
        
    Returns:
        Dictionary containing:
        - vPOC: Volume Point of Control price
        - VAH/VAL: Value Area High/Low (70% volume)
        - HVN_levels: High Volume Nodes (<=3)
        - LVN_levels: Low Volume Nodes (<=3)
        - magnet_levels: Price magnets (<=4)
        - avoid_zones: Zones to avoid (<=3)
        - confidence_0_1: Confidence in results
        - notes: Summary notes
        
    Example response:
        {
            "success": true,
            "ts_ms": 1703123456789,
            "levels": {
                "vPOC": 42350.0,
                "VAH": 42800.0,
                "VAL": 41900.0,
                "HVN_levels": [42350.0, 42100.0, 42600.0],
                "LVN_levels": [42475.0, 41975.0],
                "magnet_levels": [42350.0, 42800.0, 41900.0],
                "avoid_zones": [{"price": 42475.0, "reason": "LVN - quick price movement"}]
            },
            "confidence_0_1": 0.75,
            "notes": ["POC at 42350.0", "Value Area: 41900.0-42800.0"]
        }
    """
    logger.info(f"Tool called: volume_profile_fallback_from_trades_futures with symbol={symbol}, lookback={lookback_minutes}min")
    
    try:
        from binance_mcp_server.tools.futures import volume_profile_fallback_from_trades as _vp_fallback
        result = _vp_fallback(
            symbol=symbol,
            lookback_minutes=lookback_minutes,
            bin_size=bin_size,
            max_trades=max_trades
        )
        
        if result.get("success"):
            levels = result.get("levels", {})
            logger.info(f"VP fallback analysis complete for {symbol}: vPOC={levels.get('vPOC')}")
        else:
            logger.warning(f"VP fallback failed: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in volume_profile_fallback_from_trades_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


# ============================================================================
# WEBSOCKET-BASED TOOLS
# These tools use WebSocket streams for real-time data without REST API calls.
# ============================================================================


@mcp.tool()
def volume_profile_levels_futures_ws(
    symbol: str,
    window_minutes: int = 240,
    bin_size: Optional[float] = None
) -> Dict[str, Any]:
    """
    Calculate volume profile from WebSocket trade buffer (NO REST API calls).
    
    Uses locally buffered aggTrade data from WebSocket stream for real-time
    volume profile analysis. This tool does NOT make any REST API calls.
    
    Prerequisites:
    - WebSocket connection is auto-established when tool is first called
    - Buffer needs time to collect trades (wait ~60 seconds for initial data)
    - For best results, keep server running to maintain continuous buffer
    
    Features:
    - Zero REST API calls - all data from local WebSocket buffer
    - 30-second cache for identical parameters
    - Auto-subscribes to symbol's aggTrade stream
    - Auto-reconnect on WebSocket disconnection
    - Compatible output with volume_profile_levels_futures
    
    Args:
        symbol: Trading symbol (BTCUSDT or ETHUSDT)
        window_minutes: Time window in minutes (default 240, max 360)
        bin_size: Price bin size (auto-calculated if None)
        
    Returns:
        Dictionary containing:
        - vpoc: Volume Point of Control (tPOC from trades)
        - vah/val: Value Area High/Low (70% volume)
        - hvn: High Volume Nodes (<=3)
        - lvn: Low Volume Nodes (<=3)
        - single_print_zones: Gaps in volume (<=3)
        - magnet_levels: Price magnets (<=6)
        - avoid_zones: Zones to avoid (<=3)
        - ws_stats: WebSocket buffer statistics
        - quality_flags: ["insufficient_trade_data"] if buffer is empty
        
    Example response:
        {
            "success": true,
            "ts_ms": 1703123456789,
            "window": {
                "requested_minutes": 240,
                "actual_minutes": 180.5,
                "trade_count": 15234,
                "bin_size": 25.0
            },
            "levels": {
                "vpoc": 42350.0,
                "vah": 42800.0,
                "val": 41900.0,
                "hvn": [42350.0, 42100.0, 42600.0],
                "lvn": [42475.0, 41975.0],
                "magnet_levels": [42350.0, 42800.0, 41900.0],
                "avoid_zones": [{"price": 42475.0, "reason": "LVN"}]
            },
            "ws_stats": {
                "is_connected": true,
                "buffer_trade_count": 50000,
                "buffer_duration_minutes": 240.5
            },
            "confidence_0_1": 0.85
        }
        
    Error response (insufficient data):
        {
            "success": false,
            "error": {"type": "data_error", "message": "Insufficient trade data"},
            "quality_flags": ["insufficient_trade_data"],
            "ws_stats": {"is_connected": true, "buffer_trade_count": 50}
        }
    """
    logger.info(f"Tool called: volume_profile_levels_futures_ws with symbol={symbol}, window={window_minutes}min")
    
    try:
        from binance_mcp_server.tools.futures import volume_profile_levels_futures_ws as _vp_ws
        result = _vp_ws(
            symbol=symbol,
            window_minutes=window_minutes,
            bin_size=bin_size
        )
        
        if result.get("success"):
            levels = result.get("levels", {})
            ws_stats = result.get("ws_stats", {})
            logger.info(
                f"VP WS analysis complete for {symbol}: "
                f"vPOC={levels.get('vpoc')}, "
                f"trades={ws_stats.get('buffer_trade_count', 0)}"
            )
        else:
            logger.warning(f"VP WS failed: {result.get('error', {}).get('message')}")
            
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in volume_profile_levels_futures_ws tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


@mcp.tool()
def get_ws_buffer_status_futures(symbol: Optional[str] = None) -> Dict[str, Any]:
    """
    Get WebSocket buffer status and statistics.
    
    Utility tool to check WebSocket connection status and buffer contents.
    Useful for debugging and monitoring the WebSocket-based tools.
    
    Args:
        symbol: Optional specific symbol to get stats for
        
    Returns:
        Dictionary containing:
        - is_connected: Whether WebSocket is connected
        - subscribed_symbols: List of subscribed symbols
        - symbol_stats: Stats for specific symbol (if provided)
            - trade_count: Number of trades in buffer
            - oldest_trade_ms: Timestamp of oldest trade
            - newest_trade_ms: Timestamp of newest trade
            - buffer_duration_minutes: Time span of buffered data
            
    Example response:
        {
            "is_connected": true,
            "subscribed_symbols": ["BTCUSDT", "ETHUSDT"],
            "symbol_stats": {
                "symbol": "BTCUSDT",
                "trade_count": 45000,
                "buffer_duration_minutes": 180.5,
                "is_connected": true
            }
        }
    """
    logger.info(f"Tool called: get_ws_buffer_status_futures with symbol={symbol}")
    
    try:
        from binance_mcp_server.tools.futures import get_ws_buffer_status as _get_status
        result = _get_status(symbol=symbol)
        
        logger.info(f"WS buffer status: connected={result.get('is_connected')}, symbols={result.get('subscribed_symbols')}")
        return {"success": True, **result}
        
    except Exception as e:
        logger.error(f"Unexpected error in get_ws_buffer_status_futures tool: {str(e)}")
        return {"success": False, "error": {"type": "tool_error", "message": f"Tool execution failed: {str(e)}"}}


def validate_configuration() -> bool:
    """
    Validate server configuration and dependencies with security checks.
    
    Returns:
        bool: True if configuration is valid and secure, False otherwise
    """
    try:
        from binance_mcp_server.config import BinanceConfig
        from binance_mcp_server.security import SecurityConfig, validate_api_credentials
        
        # Validate basic configuration
        config = BinanceConfig()
        if not config.is_valid():
            logger.error("Invalid Binance configuration:")
            for error in config.get_validation_errors():
                logger.error(f"  • {error}")
            return False
        
        # Validate API credentials security
        if not validate_api_credentials():
            logger.error("API credentials validation failed")
            return False
        
        # Validate security configuration
        security_config = SecurityConfig()
        if not security_config.is_secure():
            logger.warning("Security configuration warnings:")
            for warning in security_config.get_security_warnings():
                logger.warning(f"  • {warning}")
            # Don't fail on security warnings, just log them
        
        # Log successful validation with security audit
        security_audit_log(
            "configuration_validated",
            {
                "testnet": config.testnet,
                "security_enabled": security_config.is_secure()
            }
        )
        
        logger.info(f"Configuration validated successfully (testnet: {config.testnet})")
        logger.info(f"Security features enabled: {security_config.is_secure()}")
        return True
        
    except ImportError as e:
        logger.error(f"Failed to import configuration module: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Configuration validation failed: {str(e)}")
        security_audit_log(
            "configuration_validation_failed",
            {"error": str(e)},
            level="ERROR"
        )
        return False


def main() -> None:
    """
    Main entry point for the Binance MCP Server.
    
    Handles argument parsing, configuration validation, and server startup
    with proper error handling and exit codes.
    
    Exit Codes:
        0: Successful execution or user interruption
        1: Configuration error or validation failure
        84: Server startup or runtime error
    """
    load_dotenv()
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Binance MCP Server - Model Context Protocol server for Binance API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Examples:
            %(prog)s                           # Start with STDIO transport (default)
            %(prog)s --transport streamable-http          # Start with streamable-http transport for testing
            %(prog)s --transport sse --port 8080 --host 0.0.0.0  # Custom SSE configuration
        """
    )
    
    parser.add_argument(
        "--transport", 
        choices=["stdio", "streamable-http", "sse"], 
        default="stdio",
        help="Transport method to use (stdio for MCP clients, streamable-http/sse for testing)"
    )
    parser.add_argument(
        "--port", 
        type=int, 
        default=8000,
        help="Port for HTTP transport (default: 8000)"
    )
    parser.add_argument(
        "--host", 
        type=str, 
        default="localhost",
        help="Host for HTTP transport (default: localhost)"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level (default: INFO)"
    )
    
    args = parser.parse_args()
    
    # Configure logging level based on argument
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    
    
    logger.info(f"Starting Binance MCP Server with {args.transport} transport")
    logger.info(f"Log level set to: {args.log_level}")
    
    
    # Validate configuration before starting server
    if not validate_configuration():
        logger.error("Configuration validation failed. Please check your environment variables.")
        logger.error("Required: BINANCE_API_KEY, BINANCE_API_SECRET")
        logger.error("Optional: BINANCE_TESTNET (true/false)")
        sys.exit(84)
    
    
    if args.transport in ["streamable-http", "sse"]:
        logger.info(f"HTTP server will start on {args.host}:{args.port}")
        logger.info("HTTP mode is primarily for testing. Use STDIO for MCP clients.")
    else:
        logger.info("STDIO mode: Ready for MCP client connections")
    
    
    try:
        if args.transport == "stdio":
            logger.info("Initializing STDIO transport...")
            mcp.run(transport="stdio")
        else:
            logger.info(f"Initializing {args.transport} transport on {args.host}:{args.port}")
            mcp.run(transport=args.transport, port=args.port, host=args.host)

    except KeyboardInterrupt:
        logger.info("Server shutdown requested by user (Ctrl+C)")
        sys.exit(0)

    except ImportError as e:
        logger.error(f"Missing required dependencies: {str(e)}")
        logger.error("Please ensure all required packages are installed")
        sys.exit(84)

    except OSError as e:
        if "Address already in use" in str(e):
            logger.error(f"Port {args.port} is already in use. Please choose a different port.")
            sys.exit(84)
        else:
            logger.error(f"Network error during server startup: {str(e)}")
            sys.exit(84)

    except Exception as e:
        logger.error(f"Server startup failed with unexpected error: {str(e)}")
        logger.error("This is likely a configuration or environment issue")
        sys.exit(84)


if __name__ == "__main__":
    main()