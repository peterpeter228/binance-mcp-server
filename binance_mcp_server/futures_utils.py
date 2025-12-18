"""
Futures utilities for validation, rounding, and exchange info handling.

This module provides utilities for:
- Symbol validation against allowlist
- Price/quantity rounding based on exchange filters
- Min notional validation
- Exchange info caching
"""

import time
import math
import logging
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Dict, Any, Optional, List, Tuple
from binance_mcp_server.futures_config import (
    get_futures_client, 
    ALLOWED_FUTURES_SYMBOLS,
    FuturesClient
)

logger = logging.getLogger(__name__)


# Cache for exchange info
_exchange_info_cache: Dict[str, Any] = {}
_cache_timestamp: float = 0
CACHE_TTL_SECONDS = 300  # 5 minutes


class ExchangeInfoCache:
    """
    Cache for exchange info with automatic refresh.
    """
    
    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._last_update: float = 0
        self._raw_exchange_info: Optional[Dict] = None
    
    def _fetch_exchange_info(self, client: FuturesClient) -> Tuple[bool, Any]:
        """Fetch exchange info from API."""
        return client.get("/fapi/v1/exchangeInfo")
    
    def get_symbol_info(self, symbol: str, client: Optional[FuturesClient] = None) -> Optional[Dict[str, Any]]:
        """
        Get exchange info for a specific symbol.
        
        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            client: Optional FuturesClient instance
            
        Returns:
            Dict with symbol filters and info, or None if not found
        """
        symbol = symbol.upper()
        current_time = time.time()
        
        # Check if cache needs refresh
        if current_time - self._last_update > self.ttl or symbol not in self._cache:
            client = client or get_futures_client()
            success, data = self._fetch_exchange_info(client)
            
            if success:
                self._raw_exchange_info = data
                self._last_update = current_time
                
                # Parse and cache symbol info
                for sym_info in data.get("symbols", []):
                    sym = sym_info.get("symbol", "")
                    self._cache[sym] = self._parse_symbol_info(sym_info)
        
        return self._cache.get(symbol)
    
    def _parse_symbol_info(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse symbol info and extract relevant filters.
        
        Args:
            info: Raw symbol info from exchange
            
        Returns:
            Dict with parsed filters and precision info
        """
        filters = {}
        for f in info.get("filters", []):
            filter_type = f.get("filterType")
            if filter_type:
                filters[filter_type] = f
        
        # Extract tick size from PRICE_FILTER
        tick_size = "0.01"
        if "PRICE_FILTER" in filters:
            tick_size = filters["PRICE_FILTER"].get("tickSize", tick_size)
        
        # Extract step size and min qty from LOT_SIZE
        step_size = "0.001"
        min_qty = "0.001"
        max_qty = "9999999"
        if "LOT_SIZE" in filters:
            step_size = filters["LOT_SIZE"].get("stepSize", step_size)
            min_qty = filters["LOT_SIZE"].get("minQty", min_qty)
            max_qty = filters["LOT_SIZE"].get("maxQty", max_qty)
        
        # Extract min notional from MIN_NOTIONAL or NOTIONAL
        min_notional = "5"
        if "MIN_NOTIONAL" in filters:
            min_notional = filters["MIN_NOTIONAL"].get("notional", min_notional)
        elif "NOTIONAL" in filters:
            min_notional = filters["NOTIONAL"].get("minNotional", min_notional)
        
        # Extract market lot size if different
        market_step_size = step_size
        market_min_qty = min_qty
        market_max_qty = max_qty
        if "MARKET_LOT_SIZE" in filters:
            market_step_size = filters["MARKET_LOT_SIZE"].get("stepSize", market_step_size)
            market_min_qty = filters["MARKET_LOT_SIZE"].get("minQty", market_min_qty)
            market_max_qty = filters["MARKET_LOT_SIZE"].get("maxQty", market_max_qty)
        
        return {
            "symbol": info.get("symbol"),
            "status": info.get("status"),
            "baseAsset": info.get("baseAsset"),
            "quoteAsset": info.get("quoteAsset"),
            "pricePrecision": info.get("pricePrecision", 2),
            "quantityPrecision": info.get("quantityPrecision", 3),
            "baseAssetPrecision": info.get("baseAssetPrecision", 8),
            "quotePrecision": info.get("quotePrecision", 8),
            "tickSize": tick_size,
            "stepSize": step_size,
            "minQty": min_qty,
            "maxQty": max_qty,
            "minNotional": min_notional,
            "marketStepSize": market_step_size,
            "marketMinQty": market_min_qty,
            "marketMaxQty": market_max_qty,
            "contractType": info.get("contractType"),
            "marginAsset": info.get("marginAsset"),
            "filters_raw": filters
        }
    
    def get_raw_exchange_info(self) -> Optional[Dict]:
        """Get raw exchange info."""
        return self._raw_exchange_info


# Global exchange info cache
_exchange_cache = ExchangeInfoCache()


def get_exchange_info_cache() -> ExchangeInfoCache:
    """Get the global exchange info cache."""
    return _exchange_cache


def validate_futures_symbol(symbol: str) -> Tuple[bool, str, Optional[str]]:
    """
    Validate symbol against allowlist and exchange info.
    
    Args:
        symbol: Trading symbol to validate
        
    Returns:
        Tuple of (is_valid, normalized_symbol, error_message)
    """
    if not symbol or not isinstance(symbol, str):
        return False, "", "Symbol must be a non-empty string"
    
    symbol = symbol.upper().strip()
    
    # Check against allowlist
    if symbol not in ALLOWED_FUTURES_SYMBOLS:
        return False, symbol, f"Symbol '{symbol}' is not in allowed list. Allowed: {', '.join(ALLOWED_FUTURES_SYMBOLS)}"
    
    return True, symbol, None


def round_to_tick_size(value: float, tick_size: str) -> Decimal:
    """
    Round a value to the nearest tick size.
    
    Args:
        value: Value to round
        tick_size: Tick size string (e.g., "0.01")
        
    Returns:
        Decimal rounded to tick size
    """
    tick = Decimal(str(tick_size))
    val = Decimal(str(value))
    
    # Calculate precision from tick size
    precision = abs(tick.as_tuple().exponent)
    
    # Round down to tick size
    rounded = (val // tick) * tick
    
    return rounded.quantize(Decimal(10) ** -precision)


def round_to_step_size(value: float, step_size: str) -> Decimal:
    """
    Round a quantity to the nearest step size.
    
    Args:
        value: Quantity to round
        step_size: Step size string (e.g., "0.001")
        
    Returns:
        Decimal rounded to step size
    """
    step = Decimal(str(step_size))
    val = Decimal(str(value))
    
    # Calculate precision from step size
    precision = abs(step.as_tuple().exponent)
    
    # Round down to step size
    rounded = (val // step) * step
    
    return rounded.quantize(Decimal(10) ** -precision)


def calculate_precision(tick_or_step: str) -> int:
    """
    Calculate decimal precision from tick/step size.
    
    Args:
        tick_or_step: Tick or step size string
        
    Returns:
        Number of decimal places
    """
    d = Decimal(str(tick_or_step))
    return abs(d.as_tuple().exponent)


class OrderValidator:
    """
    Validator for futures order parameters.
    """
    
    def __init__(self, symbol_info: Dict[str, Any]):
        self.info = symbol_info
        self.tick_size = symbol_info.get("tickSize", "0.01")
        self.step_size = symbol_info.get("stepSize", "0.001")
        self.min_qty = Decimal(str(symbol_info.get("minQty", "0.001")))
        self.max_qty = Decimal(str(symbol_info.get("maxQty", "9999999")))
        self.min_notional = Decimal(str(symbol_info.get("minNotional", "5")))
    
    def validate_and_round_price(self, price: float) -> Tuple[bool, Decimal, Optional[str]]:
        """
        Validate and round price to tick size.
        
        Args:
            price: Order price
            
        Returns:
            Tuple of (is_valid, rounded_price, error_message)
        """
        if price <= 0:
            return False, Decimal("0"), "Price must be greater than 0"
        
        rounded = round_to_tick_size(price, self.tick_size)
        
        if rounded <= 0:
            return False, Decimal("0"), f"Price {price} rounds to 0 with tick size {self.tick_size}"
        
        return True, rounded, None
    
    def validate_and_round_quantity(self, quantity: float, is_market: bool = False) -> Tuple[bool, Decimal, Optional[str]]:
        """
        Validate and round quantity to step size.
        
        Args:
            quantity: Order quantity
            is_market: Whether this is a market order
            
        Returns:
            Tuple of (is_valid, rounded_quantity, error_message)
        """
        if quantity <= 0:
            return False, Decimal("0"), "Quantity must be greater than 0"
        
        step = self.info.get("marketStepSize", self.step_size) if is_market else self.step_size
        min_q = Decimal(str(self.info.get("marketMinQty", str(self.min_qty)))) if is_market else self.min_qty
        max_q = Decimal(str(self.info.get("marketMaxQty", str(self.max_qty)))) if is_market else self.max_qty
        
        rounded = round_to_step_size(quantity, step)
        
        if rounded < min_q:
            return False, rounded, f"Quantity {quantity} (rounded: {rounded}) is below minimum {min_q}"
        
        if rounded > max_q:
            return False, rounded, f"Quantity {quantity} (rounded: {rounded}) exceeds maximum {max_q}"
        
        return True, rounded, None
    
    def validate_notional(self, price: float, quantity: float) -> Tuple[bool, Decimal, Optional[str]]:
        """
        Validate that order meets minimum notional requirement.
        
        Args:
            price: Order price
            quantity: Order quantity
            
        Returns:
            Tuple of (is_valid, notional, error_message)
        """
        notional = Decimal(str(price)) * Decimal(str(quantity))
        
        if notional < self.min_notional:
            return False, notional, f"Notional value {notional} is below minimum {self.min_notional}"
        
        return True, notional, None
    
    def validate_order(
        self,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Comprehensive order validation.
        
        Args:
            side: Order side (BUY/SELL)
            order_type: Order type (LIMIT/MARKET/etc.)
            quantity: Order quantity
            price: Limit price (required for LIMIT orders)
            stop_price: Stop price (for STOP orders)
            
        Returns:
            Dict with validation results and rounded values
        """
        errors = []
        warnings = []
        rounded = {}
        
        # Validate side
        side = side.upper()
        if side not in ("BUY", "SELL"):
            errors.append(f"Invalid side: {side}. Must be BUY or SELL")
        
        # Validate order type
        valid_types = [
            "LIMIT", "MARKET", "STOP", "STOP_MARKET", 
            "TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"
        ]
        order_type = order_type.upper()
        if order_type not in valid_types:
            errors.append(f"Invalid order type: {order_type}. Valid types: {', '.join(valid_types)}")
        
        is_market = order_type in ("MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET")
        
        # Validate and round quantity
        qty_valid, qty_rounded, qty_error = self.validate_and_round_quantity(quantity, is_market)
        if not qty_valid:
            errors.append(qty_error)
        else:
            rounded["quantity"] = str(qty_rounded)
            if qty_rounded != Decimal(str(quantity)):
                warnings.append(f"Quantity rounded from {quantity} to {qty_rounded}")
        
        # Validate price for limit orders
        if order_type in ("LIMIT", "STOP", "TAKE_PROFIT"):
            if price is None:
                errors.append(f"Price is required for {order_type} orders")
            else:
                price_valid, price_rounded, price_error = self.validate_and_round_price(price)
                if not price_valid:
                    errors.append(price_error)
                else:
                    rounded["price"] = str(price_rounded)
                    if price_rounded != Decimal(str(price)):
                        warnings.append(f"Price rounded from {price} to {price_rounded}")
                    
                    # Validate notional
                    notional_valid, notional, notional_error = self.validate_notional(
                        float(price_rounded), float(qty_rounded) if qty_valid else quantity
                    )
                    if not notional_valid:
                        errors.append(notional_error)
                    else:
                        rounded["notional"] = str(notional)
        
        # Validate stop price
        if stop_price is not None:
            stop_valid, stop_rounded, stop_error = self.validate_and_round_price(stop_price)
            if not stop_valid:
                errors.append(f"Stop price error: {stop_error}")
            else:
                rounded["stopPrice"] = str(stop_rounded)
                if stop_rounded != Decimal(str(stop_price)):
                    warnings.append(f"Stop price rounded from {stop_price} to {stop_rounded}")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "rounded": rounded,
            "symbol_info": {
                "tickSize": self.tick_size,
                "stepSize": self.step_size,
                "minQty": str(self.min_qty),
                "minNotional": str(self.min_notional)
            }
        }


def get_order_validator(symbol: str) -> Optional[OrderValidator]:
    """
    Get an order validator for a symbol.
    
    Args:
        symbol: Trading symbol
        
    Returns:
        OrderValidator instance or None if symbol not found
    """
    cache = get_exchange_info_cache()
    symbol_info = cache.get_symbol_info(symbol)
    
    if symbol_info is None:
        return None
    
    return OrderValidator(symbol_info)


def calculate_mmr_for_notional(brackets: List[Dict], notional: float) -> Optional[Dict[str, Any]]:
    """
    Calculate maintenance margin rate for a given notional value.
    
    Args:
        brackets: List of leverage bracket dicts
        notional: Position notional value
        
    Returns:
        Dict with bracket info and MMR, or None if no matching bracket
    """
    notional = Decimal(str(notional))
    
    for bracket in brackets:
        floor = Decimal(str(bracket.get("notionalFloor", 0)))
        cap = Decimal(str(bracket.get("notionalCap", float("inf"))))
        
        if floor <= notional < cap:
            return {
                "bracket": bracket.get("bracket"),
                "notionalFloor": str(floor),
                "notionalCap": str(cap),
                "initialLeverage": bracket.get("initialLeverage"),
                "maintMarginRatio": bracket.get("maintMarginRatio"),
                "cum": bracket.get("cum"),
                "notional": str(notional)
            }
    
    return None
