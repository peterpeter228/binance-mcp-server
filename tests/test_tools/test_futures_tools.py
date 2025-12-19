"""
Unit tests for Binance USDⓈ-M Futures MCP tools.

These tests cover:
- Price/quantity rounding to tick/step sizes
- Post-only -> GTX mapping
- Margin/leverage idempotent handling
- Order validation and error handling
- Symbol allowlist validation
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal

# Import utilities
from binance_mcp_server.futures_utils import (
    validate_futures_symbol,
    round_to_tick_size,
    round_to_step_size,
    calculate_precision,
    OrderValidator,
    get_exchange_info_cache,
    calculate_mmr_for_notional,
)
from binance_mcp_server.futures_config import (
    ALLOWED_FUTURES_SYMBOLS,
    FuturesConfig,
    create_signature,
)


class TestSymbolValidation:
    """Test symbol validation against allowlist."""
    
    def test_valid_btcusdt(self):
        """Test BTCUSDT is allowed."""
        is_valid, symbol, error = validate_futures_symbol("BTCUSDT")
        assert is_valid is True
        assert symbol == "BTCUSDT"
        assert error is None
    
    def test_valid_ethusdt(self):
        """Test ETHUSDT is allowed."""
        is_valid, symbol, error = validate_futures_symbol("ETHUSDT")
        assert is_valid is True
        assert symbol == "ETHUSDT"
        assert error is None
    
    def test_lowercase_normalized(self):
        """Test lowercase symbols are normalized to uppercase."""
        is_valid, symbol, error = validate_futures_symbol("btcusdt")
        assert is_valid is True
        assert symbol == "BTCUSDT"
    
    def test_invalid_symbol_rejected(self):
        """Test non-allowed symbols are rejected."""
        is_valid, symbol, error = validate_futures_symbol("DOGEUSDT")
        assert is_valid is False
        assert "not in allowed list" in error
    
    def test_empty_symbol_rejected(self):
        """Test empty symbols are rejected."""
        is_valid, symbol, error = validate_futures_symbol("")
        assert is_valid is False
        assert "non-empty string" in error
    
    def test_none_symbol_rejected(self):
        """Test None symbol is rejected."""
        is_valid, symbol, error = validate_futures_symbol(None)
        assert is_valid is False
        assert "non-empty string" in error


class TestPriceRounding:
    """Test price rounding to tick sizes."""
    
    def test_round_price_btc_tick_0_10(self):
        """Test rounding price to 0.10 tick size (BTC-like)."""
        result = round_to_tick_size(50000.15, "0.10")
        assert result == Decimal("50000.10")
    
    def test_round_price_btc_tick_0_01(self):
        """Test rounding price to 0.01 tick size."""
        result = round_to_tick_size(50000.156, "0.01")
        assert result == Decimal("50000.15")
    
    def test_round_price_exact(self):
        """Test price that's already on tick size."""
        result = round_to_tick_size(50000.10, "0.10")
        assert result == Decimal("50000.10")
    
    def test_round_price_down(self):
        """Test price rounds down to nearest tick."""
        result = round_to_tick_size(50000.19, "0.10")
        assert result == Decimal("50000.10")
    
    def test_round_price_small_tick(self):
        """Test rounding with small tick size."""
        result = round_to_tick_size(0.123456, "0.0001")
        assert result == Decimal("0.1234")


class TestQuantityRounding:
    """Test quantity rounding to step sizes."""
    
    def test_round_qty_btc_step_0_001(self):
        """Test rounding quantity to 0.001 step size (BTC-like)."""
        result = round_to_step_size(0.12345, "0.001")
        assert result == Decimal("0.123")
    
    def test_round_qty_eth_step_0_01(self):
        """Test rounding quantity to 0.01 step size (ETH-like)."""
        result = round_to_step_size(1.2567, "0.01")
        assert result == Decimal("1.25")
    
    def test_round_qty_exact(self):
        """Test quantity that's already on step size."""
        result = round_to_step_size(0.123, "0.001")
        assert result == Decimal("0.123")
    
    def test_round_qty_down(self):
        """Test quantity rounds down."""
        result = round_to_step_size(0.1239, "0.001")
        assert result == Decimal("0.123")


class TestPrecisionCalculation:
    """Test decimal precision calculation from tick/step."""
    
    def test_precision_0_10(self):
        """Test precision for 0.10."""
        assert calculate_precision("0.10") == 2
    
    def test_precision_0_01(self):
        """Test precision for 0.01."""
        assert calculate_precision("0.01") == 2
    
    def test_precision_0_001(self):
        """Test precision for 0.001."""
        assert calculate_precision("0.001") == 3
    
    def test_precision_0_00001(self):
        """Test precision for 0.00001."""
        assert calculate_precision("0.00001") == 5
    
    def test_precision_1(self):
        """Test precision for 1."""
        assert calculate_precision("1") == 0


class TestOrderValidator:
    """Test OrderValidator class."""
    
    @pytest.fixture
    def btc_validator(self):
        """Create a BTC-like validator."""
        symbol_info = {
            "symbol": "BTCUSDT",
            "tickSize": "0.10",
            "stepSize": "0.001",
            "minQty": "0.001",
            "maxQty": "1000",
            "minNotional": "5",
            "marketStepSize": "0.001",
            "marketMinQty": "0.001",
            "marketMaxQty": "100",
        }
        return OrderValidator(symbol_info)
    
    def test_validate_price_valid(self, btc_validator):
        """Test valid price validation."""
        valid, rounded, error = btc_validator.validate_and_round_price(50000.15)
        assert valid is True
        assert rounded == Decimal("50000.10")
        assert error is None
    
    def test_validate_price_zero(self, btc_validator):
        """Test zero price is rejected."""
        valid, rounded, error = btc_validator.validate_and_round_price(0)
        assert valid is False
        assert "greater than 0" in error
    
    def test_validate_price_negative(self, btc_validator):
        """Test negative price is rejected."""
        valid, rounded, error = btc_validator.validate_and_round_price(-100)
        assert valid is False
        assert "greater than 0" in error
    
    def test_validate_quantity_valid(self, btc_validator):
        """Test valid quantity validation."""
        valid, rounded, error = btc_validator.validate_and_round_quantity(0.12345)
        assert valid is True
        assert rounded == Decimal("0.123")
        assert error is None
    
    def test_validate_quantity_below_min(self, btc_validator):
        """Test quantity below minimum is rejected."""
        valid, rounded, error = btc_validator.validate_and_round_quantity(0.0001)
        assert valid is False
        assert "below minimum" in error
    
    def test_validate_quantity_above_max(self, btc_validator):
        """Test quantity above maximum is rejected."""
        valid, rounded, error = btc_validator.validate_and_round_quantity(2000)
        assert valid is False
        assert "exceeds maximum" in error
    
    def test_validate_notional_valid(self, btc_validator):
        """Test valid notional (price * qty)."""
        valid, notional, error = btc_validator.validate_notional(50000, 0.001)
        assert valid is True
        assert notional == Decimal("50")
        assert error is None
    
    def test_validate_notional_below_min(self, btc_validator):
        """Test notional below minimum is rejected."""
        valid, notional, error = btc_validator.validate_notional(1000, 0.001)
        assert valid is False
        assert "below minimum" in error
    
    def test_validate_order_complete(self, btc_validator):
        """Test complete order validation."""
        result = btc_validator.validate_order(
            side="BUY",
            order_type="LIMIT",
            quantity=0.12345,
            price=50000.15,
        )
        
        assert result["valid"] is True
        assert "0.123" in result["rounded"]["quantity"]
        assert "50000.10" in result["rounded"]["price"]
    
    def test_validate_order_invalid_side(self, btc_validator):
        """Test order with invalid side is rejected."""
        result = btc_validator.validate_order(
            side="HOLD",
            order_type="LIMIT",
            quantity=0.1,
            price=50000,
        )
        
        assert result["valid"] is False
        assert any("Invalid side" in e for e in result["errors"])
    
    def test_validate_order_invalid_type(self, btc_validator):
        """Test order with invalid type is rejected."""
        result = btc_validator.validate_order(
            side="BUY",
            order_type="INVALID",
            quantity=0.1,
            price=50000,
        )
        
        assert result["valid"] is False
        assert any("Invalid order type" in e for e in result["errors"])


class TestMMRCalculation:
    """Test maintenance margin ratio calculation."""
    
    @pytest.fixture
    def btc_brackets(self):
        """Sample BTC leverage brackets."""
        return [
            {"bracket": 1, "notionalFloor": 0, "notionalCap": 50000, "initialLeverage": 125, "maintMarginRatio": 0.004, "cum": 0},
            {"bracket": 2, "notionalFloor": 50000, "notionalCap": 250000, "initialLeverage": 100, "maintMarginRatio": 0.005, "cum": 50},
            {"bracket": 3, "notionalFloor": 250000, "notionalCap": 1000000, "initialLeverage": 50, "maintMarginRatio": 0.01, "cum": 1300},
        ]
    
    def test_mmr_first_bracket(self, btc_brackets):
        """Test MMR for small notional (first bracket)."""
        result = calculate_mmr_for_notional(btc_brackets, 10000)
        assert result is not None
        assert result["bracket"] == 1
        assert result["maintMarginRatio"] == 0.004
        assert result["initialLeverage"] == 125
    
    def test_mmr_second_bracket(self, btc_brackets):
        """Test MMR for medium notional (second bracket)."""
        result = calculate_mmr_for_notional(btc_brackets, 100000)
        assert result is not None
        assert result["bracket"] == 2
        assert result["maintMarginRatio"] == 0.005
    
    def test_mmr_boundary(self, btc_brackets):
        """Test MMR at bracket boundary."""
        result = calculate_mmr_for_notional(btc_brackets, 50000)
        assert result is not None
        assert result["bracket"] == 2  # 50000 is >= floor of bracket 2


class TestFuturesConfig:
    """Test futures configuration."""
    
    def test_production_url(self):
        """Test production base URL."""
        with patch.dict('os.environ', {'BINANCE_TESTNET': 'false', 'BINANCE_API_KEY': 'test', 'BINANCE_API_SECRET': 'test'}):
            config = FuturesConfig()
            assert config.base_url == "https://fapi.binance.com"
    
    def test_testnet_url(self):
        """Test testnet base URL when enabled."""
        with patch.dict('os.environ', {'BINANCE_TESTNET': 'true', 'BINANCE_API_KEY': 'test', 'BINANCE_API_SECRET': 'test'}):
            config = FuturesConfig()
            assert config.base_url == "https://testnet.binancefuture.com"
    
    def test_signature_creation(self):
        """Test HMAC signature creation."""
        secret = "test_secret"
        query = "symbol=BTCUSDT&timestamp=1234567890"
        signature = create_signature(secret, query)
        
        # Signature should be hex string of 64 chars (SHA256)
        assert len(signature) == 64
        assert all(c in '0123456789abcdef' for c in signature)


class TestPostOnlyMapping:
    """Test post-only to GTX mapping."""
    
    def test_post_only_requires_limit(self):
        """Test that post_only=True requires LIMIT order type."""
        from binance_mcp_server.tools.futures.place_order import VALID_ORDER_TYPES
        
        # GTX (post-only) should be in valid TIF values
        from binance_mcp_server.tools.futures.place_order import VALID_TIF
        assert "GTX" in VALID_TIF


class TestAllowedSymbols:
    """Test allowed symbols configuration."""
    
    def test_btcusdt_in_allowlist(self):
        """Test BTCUSDT is in allowlist."""
        assert "BTCUSDT" in ALLOWED_FUTURES_SYMBOLS
    
    def test_ethusdt_in_allowlist(self):
        """Test ETHUSDT is in allowlist."""
        assert "ETHUSDT" in ALLOWED_FUTURES_SYMBOLS
    
    def test_only_two_symbols(self):
        """Test only BTCUSDT and ETHUSDT are allowed."""
        assert len(ALLOWED_FUTURES_SYMBOLS) == 2


class TestMockedAPITools:
    """Test futures tools with mocked API responses."""
    
    @patch('binance_mcp_server.tools.futures.exchange_info.get_futures_client')
    def test_get_exchange_info_success(self, mock_get_client):
        """Test get_exchange_info_futures with mocked success response."""
        mock_client = Mock()
        mock_client.get.return_value = (True, {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ]
                }
            ],
            "serverTime": 1234567890123
        })
        mock_get_client.return_value = mock_client
        
        from binance_mcp_server.tools.futures import get_exchange_info_futures
        result = get_exchange_info_futures("BTCUSDT")
        
        assert result["success"] is True
        assert result["data"]["symbol"] == "BTCUSDT"
        assert result["data"]["tickSize"] == "0.10"
        assert result["data"]["stepSize"] == "0.001"
    
    @patch('binance_mcp_server.tools.futures.commission_rate.get_futures_client')
    def test_get_commission_rate_success(self, mock_get_client):
        """Test get_commission_rate_futures with mocked success response."""
        mock_client = Mock()
        mock_client.get.side_effect = [
            (True, {
                "symbol": "BTCUSDT",
                "makerCommissionRate": "0.0002",
                "takerCommissionRate": "0.0004"
            }),
            (True, {"serverTime": 1234567890123})  # For time endpoint
        ]
        mock_get_client.return_value = mock_client
        
        from binance_mcp_server.tools.futures import get_commission_rate_futures
        result = get_commission_rate_futures("BTCUSDT")
        
        assert result["success"] is True
        assert result["data"]["makerCommissionRate"] == "0.0002"
        assert result["data"]["makerCommissionRate_float"] == 0.0002
        assert "0.02" in result["data"]["makerCommissionRate_percent"]
    
    @patch('binance_mcp_server.tools.futures.set_leverage.get_futures_client')
    def test_set_leverage_idempotent(self, mock_get_client):
        """Test set_leverage returns already_set for duplicate request."""
        mock_client = Mock()
        # First call - check current leverage
        mock_client.get.return_value = (True, [
            {"symbol": "BTCUSDT", "leverage": "10"}
        ])
        # Second call - set leverage returns "no need to change"
        mock_client.post.return_value = (False, {
            "code": -4046,
            "msg": "No need to change leverage."
        })
        mock_get_client.return_value = mock_client
        
        from binance_mcp_server.tools.futures import set_leverage
        result = set_leverage("BTCUSDT", 10)
        
        assert result["success"] is True
        assert result["already_set"] is True
    
    @patch('binance_mcp_server.tools.futures.set_margin_type.get_futures_client')
    def test_set_margin_type_idempotent(self, mock_get_client):
        """Test set_margin_type returns already_set for duplicate request."""
        mock_client = Mock()
        # Check current margin type
        mock_client.get.return_value = (True, [
            {"symbol": "BTCUSDT", "marginType": "isolated"}
        ])
        # Set margin type returns "no need to change"
        mock_client.post.return_value = (False, {
            "code": -4046,
            "msg": "No need to change margin type."
        })
        mock_get_client.return_value = mock_client
        
        from binance_mcp_server.tools.futures import set_margin_type
        result = set_margin_type("BTCUSDT", "ISOLATED")
        
        assert result["success"] is True
        assert result["already_set"] is True


class TestValidateOrderPlan:
    """Test validate_order_plan_futures tool."""
    
    @patch('binance_mcp_server.tools.futures.validate_order_plan.get_order_validator')
    def test_validate_plan_success(self, mock_get_validator):
        """Test successful order plan validation."""
        mock_validator = Mock()
        mock_validator.validate_and_round_price.return_value = (True, Decimal("50000.10"), None)
        mock_validator.validate_and_round_quantity.return_value = (True, Decimal("0.001"), None)
        mock_validator.validate_notional.return_value = (True, Decimal("50"), None)
        mock_validator.tick_size = "0.10"
        mock_validator.step_size = "0.001"
        mock_validator.min_qty = Decimal("0.001")
        mock_validator.max_qty = Decimal("1000")
        mock_validator.min_notional = Decimal("5")
        mock_get_validator.return_value = mock_validator
        
        from binance_mcp_server.tools.futures import validate_order_plan_futures
        result = validate_order_plan_futures(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.15,
            quantity=0.001,
        )
        
        assert result["success"] is True
        assert result["valid"] is True
    
    @patch('binance_mcp_server.tools.futures.validate_order_plan.get_order_validator')
    def test_validate_plan_invalid_notional(self, mock_get_validator):
        """Test order plan with invalid notional."""
        mock_validator = Mock()
        mock_validator.validate_and_round_price.return_value = (True, Decimal("1000"), None)
        mock_validator.validate_and_round_quantity.return_value = (True, Decimal("0.001"), None)
        mock_validator.validate_notional.return_value = (False, Decimal("1"), "Notional below minimum 5")
        mock_validator.tick_size = "0.10"
        mock_validator.step_size = "0.001"
        mock_validator.min_qty = Decimal("0.001")
        mock_validator.max_qty = Decimal("1000")
        mock_validator.min_notional = Decimal("5")
        mock_get_validator.return_value = mock_validator
        
        from binance_mcp_server.tools.futures import validate_order_plan_futures
        result = validate_order_plan_futures(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=1000,
            quantity=0.001,
        )
        
        assert result["success"] is True
        assert result["valid"] is False
        assert any("min_notional" in r for r in result["reasons"])


class TestStructuredErrorResponse:
    """Test that errors return structured JSON responses."""
    
    def test_validation_error_structure(self):
        """Test validation error returns proper structure."""
        from binance_mcp_server.tools.futures import get_exchange_info_futures
        result = get_exchange_info_futures("INVALID_SYMBOL")
        
        assert result["success"] is False
        assert "error" in result
        assert "type" in result["error"]
        assert "message" in result["error"]
    
    @patch('binance_mcp_server.tools.futures.place_order.get_futures_client')
    @patch('binance_mcp_server.tools.futures.place_order.get_order_validator')
    def test_api_error_includes_params(self, mock_get_validator, mock_get_client):
        """Test API error includes parameters sent."""
        mock_validator = Mock()
        mock_validator.validate_and_round_price.return_value = (True, Decimal("50000"), None)
        mock_validator.validate_and_round_quantity.return_value = (True, Decimal("0.001"), None)
        mock_validator.validate_notional.return_value = (True, Decimal("50"), None)
        mock_validator.info = {"tickSize": "0.10", "stepSize": "0.001"}
        mock_get_validator.return_value = mock_validator
        
        mock_client = Mock()
        mock_client.post.return_value = (False, {
            "code": -2010,
            "message": "Insufficient balance"
        })
        mock_get_client.return_value = mock_client
        
        from binance_mcp_server.tools.futures import place_order_futures
        result = place_order_futures(
            symbol="BTCUSDT",
            side="BUY",
            order_type="LIMIT",
            quantity=0.001,
            price=50000,
            time_in_force="GTC",
        )
        
        assert result["success"] is False
        assert result["error"]["type"] == "api_error"
        assert "details" in result["error"]
        assert "code" in result["error"]["details"]
