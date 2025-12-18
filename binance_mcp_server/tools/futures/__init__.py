"""
Binance USDⓈ-M Futures MCP Tools.

This package provides MCP tools for interacting with Binance USDⓈ-M Futures API,
supporting BTCUSDT and ETHUSDT perpetual contracts.
"""

from binance_mcp_server.tools.futures.exchange_info import get_exchange_info_futures
from binance_mcp_server.tools.futures.commission_rate import get_commission_rate_futures
from binance_mcp_server.tools.futures.position_risk import get_position_risk
from binance_mcp_server.tools.futures.leverage_brackets import get_leverage_brackets
from binance_mcp_server.tools.futures.set_leverage import set_leverage
from binance_mcp_server.tools.futures.set_margin_type import set_margin_type
from binance_mcp_server.tools.futures.place_order import place_order_futures
from binance_mcp_server.tools.futures.amend_order import amend_order_futures
from binance_mcp_server.tools.futures.get_order_status import get_order_status_futures
from binance_mcp_server.tools.futures.cancel_order import cancel_order_futures
from binance_mcp_server.tools.futures.cancel_multiple_orders import cancel_multiple_orders_futures
from binance_mcp_server.tools.futures.validate_order_plan import validate_order_plan_futures
from binance_mcp_server.tools.futures.bracket_orders import (
    place_bracket_orders_futures,
    get_bracket_job_status,
    cancel_bracket_job,
)
from binance_mcp_server.tools.futures.cancel_on_ttl import (
    cancel_on_ttl,
    get_ttl_job_status,
    cancel_ttl_job,
)

__all__ = [
    # P0 - Core Tools
    "get_exchange_info_futures",
    "get_commission_rate_futures",
    "get_position_risk",
    "get_leverage_brackets",
    "set_leverage",
    "set_margin_type",
    "place_order_futures",
    "amend_order_futures",
    "get_order_status_futures",
    "cancel_order_futures",
    "cancel_multiple_orders_futures",
    # P1 - Advanced Tools
    "validate_order_plan_futures",
    "place_bracket_orders_futures",
    "get_bracket_job_status",
    "cancel_bracket_job",
    "cancel_on_ttl",
    "get_ttl_job_status",
    "cancel_ttl_job",
]
