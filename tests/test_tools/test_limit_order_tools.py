"""
Unit tests for Limit Order Optimization MCP tools.

These tests cover:
- Queue fill estimation with ETA and probability calculations
- Volume profile analysis with HVN/LVN detection
- Microstructure data caching and fetching
- Adverse selection scoring
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal
import time


class TestMicrostructureDataFetcher:
    """Test MicrostructureDataFetcher class."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock futures client."""
        mock = Mock()
        return mock
    
    @patch('binance_mcp_server.tools.futures.microstructure_data.get_futures_client')
    def test_fetch_orderbook_success(self, mock_get_client):
        """Test successful orderbook fetch."""
        mock_client = Mock()
        mock_client.get.return_value = (True, {
            "lastUpdateId": 123456,
            "bids": [["50000.00", "1.5"], ["49999.00", "2.0"]],
            "asks": [["50001.00", "1.0"], ["50002.00", "1.8"]]
        })
        mock_get_client.return_value = mock_client
        
        from binance_mcp_server.tools.futures.microstructure_data import MicrostructureDataFetcher
        
        fetcher = MicrostructureDataFetcher("BTCUSDT")
        fetcher.cache.clear()  # Clear cache for clean test
        result = fetcher.fetch_orderbook(limit=100)
        
        assert result["success"] is True
        assert len(result["bids"]) == 2
        assert len(result["asks"]) == 2
        assert result["bids"][0][0] == Decimal("50000.00")
    
    @patch('binance_mcp_server.tools.futures.microstructure_data.get_futures_client')
    def test_fetch_orderbook_cached(self, mock_get_client):
        """Test orderbook caching prevents duplicate API calls."""
        mock_client = Mock()
        mock_client.get.return_value = (True, {
            "lastUpdateId": 123456,
            "bids": [["50000.00", "1.5"]],
            "asks": [["50001.00", "1.0"]]
        })
        mock_get_client.return_value = mock_client
        
        from binance_mcp_server.tools.futures.microstructure_data import MicrostructureDataFetcher
        
        fetcher = MicrostructureDataFetcher("BTCUSDT")
        fetcher.cache.clear()
        
        # First call
        result1 = fetcher.fetch_orderbook(limit=100)
        # Second call should use cache
        result2 = fetcher.fetch_orderbook(limit=100)
        
        # Should only call API once
        assert mock_client.get.call_count == 1
        assert result1["success"] is True
        assert result2["success"] is True
    
    @patch('binance_mcp_server.tools.futures.microstructure_data.get_futures_client')
    def test_fetch_agg_trades_success(self, mock_get_client):
        """Test successful aggTrades fetch."""
        mock_client = Mock()
        mock_client.get.return_value = (True, [
            {"a": 1, "p": "50000.00", "q": "0.5", "T": int(time.time() * 1000) - 1000, "m": False},
            {"a": 2, "p": "49999.00", "q": "0.3", "T": int(time.time() * 1000) - 500, "m": True}
        ])
        mock_get_client.return_value = mock_client
        
        from binance_mcp_server.tools.futures.microstructure_data import MicrostructureDataFetcher
        
        fetcher = MicrostructureDataFetcher("BTCUSDT")
        fetcher.cache.clear()
        result = fetcher.fetch_agg_trades(lookback_ms=30000)
        
        assert result["success"] is True
        assert len(result["trades"]) == 2
        assert result["trades"][0]["price"] == Decimal("50000.00")


class TestOrderbookAnalysis:
    """Test orderbook analysis functions."""
    
    def test_analyze_orderbook_imbalance(self):
        """Test OBI calculation."""
        from binance_mcp_server.tools.futures.microstructure_data import analyze_orderbook_imbalance
        
        bids = [(Decimal("50000"), Decimal("10")), (Decimal("49999"), Decimal("5"))]
        asks = [(Decimal("50001"), Decimal("5")), (Decimal("50002"), Decimal("5"))]
        
        result = analyze_orderbook_imbalance(bids, asks, depth_levels=2)
        
        # Bid volume = 15, Ask volume = 10
        # OBI = (15 - 10) / (15 + 10) = 0.2
        assert result["obi_mean"] == 0.2
        assert result["bid_volume"] == 15.0
        assert result["ask_volume"] == 10.0
        assert "spread_bps" in result
        assert result["best_bid"] == 50000.0
        assert result["best_ask"] == 50001.0
    
    def test_analyze_orderbook_imbalance_balanced(self):
        """Test OBI for balanced orderbook."""
        from binance_mcp_server.tools.futures.microstructure_data import analyze_orderbook_imbalance
        
        bids = [(Decimal("50000"), Decimal("10"))]
        asks = [(Decimal("50001"), Decimal("10"))]
        
        result = analyze_orderbook_imbalance(bids, asks, depth_levels=1)
        
        assert result["obi_mean"] == 0.0
    
    def test_analyze_trade_flow(self):
        """Test trade flow analysis."""
        from binance_mcp_server.tools.futures.microstructure_data import analyze_trade_flow
        
        now = int(time.time() * 1000)
        trades = [
            {"price": Decimal("50000"), "qty": Decimal("1.0"), "time": now - 1000, "is_buyer_maker": False},
            {"price": Decimal("49999"), "qty": Decimal("0.5"), "time": now - 500, "is_buyer_maker": True},
        ]
        
        result = analyze_trade_flow(trades, time_window_ms=30000)
        
        assert result["buy_aggressor_volume"] == 1.0  # is_buyer_maker=False = buy aggressor
        assert result["sell_aggressor_volume"] == 0.5  # is_buyer_maker=True = sell aggressor
        assert result["trade_count"] == 2
        assert result["flow_imbalance"] > 0  # More buy aggression
    
    def test_detect_walls(self):
        """Test wall detection in orderbook."""
        from binance_mcp_server.tools.futures.microstructure_data import detect_walls
        
        # Create orderbook with a large bid at L2
        bids = [
            (Decimal("50000"), Decimal("1")),
            (Decimal("49999"), Decimal("10")),  # Wall: 10x average
            (Decimal("49998"), Decimal("1")),
            (Decimal("49997"), Decimal("1")),
            (Decimal("49996"), Decimal("1")),
        ]
        asks = [
            (Decimal("50001"), Decimal("1")),
            (Decimal("50002"), Decimal("1")),
            (Decimal("50003"), Decimal("1")),
            (Decimal("50004"), Decimal("1")),
            (Decimal("50005"), Decimal("1")),
        ]
        
        result = detect_walls(bids, asks, depth_levels=5, wall_threshold_multiplier=3.0)
        
        assert len(result["bid_walls"]) >= 1
        assert result["bid_walls"][0]["price"] == 49999.0
        assert result["wall_risk_level"] >= 1


class TestQueuePositionCalculation:
    """Test queue position calculations."""
    
    def test_calculate_queue_position_buy(self):
        """Test queue calculation for buy limit order."""
        from binance_mcp_server.tools.futures.microstructure_data import calculate_queue_position
        
        bids = [
            (Decimal("50000"), Decimal("2")),
            (Decimal("49999"), Decimal("3")),
            (Decimal("49998"), Decimal("5")),
        ]
        asks = [(Decimal("50001"), Decimal("1"))]
        
        # Place buy limit at 49999 - should have 2 + 3 = 5 qty ahead
        result = calculate_queue_position(bids, asks, Decimal("49999"), "BUY")
        
        assert result["queue_qty"] == 5.0  # 2 at 50000 + 3 at 49999
        assert result["levels_ahead"] == 2
    
    def test_calculate_queue_position_sell(self):
        """Test queue calculation for sell limit order."""
        from binance_mcp_server.tools.futures.microstructure_data import calculate_queue_position
        
        bids = [(Decimal("50000"), Decimal("1"))]
        asks = [
            (Decimal("50001"), Decimal("2")),
            (Decimal("50002"), Decimal("3")),
            (Decimal("50003"), Decimal("5")),
        ]
        
        # Place sell limit at 50002 - should have 2 + 3 = 5 qty ahead
        result = calculate_queue_position(bids, asks, Decimal("50002"), "SELL")
        
        assert result["queue_qty"] == 5.0  # 2 at 50001 + 3 at 50002
        assert result["levels_ahead"] == 2


class TestQueueFillEstimator:
    """Test queue_fill_estimator tool."""
    
    @patch('binance_mcp_server.tools.futures.queue_fill_estimator.MicrostructureDataFetcher')
    def test_queue_fill_estimator_success(self, mock_fetcher_class):
        """Test successful queue fill estimation."""
        mock_fetcher = Mock()
        
        # Mock orderbook
        mock_fetcher.fetch_orderbook.return_value = {
            "success": True,
            "bids": [(Decimal("50000"), Decimal("10")), (Decimal("49999"), Decimal("5"))],
            "asks": [(Decimal("50001"), Decimal("5")), (Decimal("50002"), Decimal("5"))],
            "lastUpdateId": 123456,
            "timestamp": int(time.time() * 1000)
        }
        
        # Mock trades
        now = int(time.time() * 1000)
        mock_fetcher.fetch_agg_trades.return_value = {
            "success": True,
            "trades": [
                {"price": Decimal("50000"), "qty": Decimal("0.5"), "time": now - 1000, "is_buyer_maker": True},
                {"price": Decimal("50001"), "qty": Decimal("0.5"), "time": now - 500, "is_buyer_maker": False},
            ],
            "count": 2
        }
        
        # Mock mark price
        mock_fetcher.fetch_mark_price.return_value = {
            "success": True,
            "markPrice": Decimal("50000.50"),
            "timestamp": int(time.time() * 1000)
        }
        
        mock_fetcher_class.return_value = mock_fetcher
        
        from binance_mcp_server.tools.futures.queue_fill_estimator import queue_fill_estimator
        
        result = queue_fill_estimator(
            symbol="BTCUSDT",
            side="BUY",
            price_levels=[50000.0, 49999.0],
            qty=0.1,
            lookback_seconds=30
        )
        
        assert "per_level" in result
        assert len(result["per_level"]) == 2
        assert "global" in result
        assert "micro_health_score" in result["global"]
        assert "recommendation" in result["global"]
    
    def test_queue_fill_estimator_invalid_symbol(self):
        """Test queue fill estimator with invalid symbol."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import queue_fill_estimator
        
        result = queue_fill_estimator(
            symbol="INVALID",
            side="BUY",
            price_levels=[50000.0],
            qty=0.1
        )
        
        assert result["success"] is False
        assert "not in allowed list" in result["error"]["message"]
    
    def test_queue_fill_estimator_invalid_side(self):
        """Test queue fill estimator with invalid side."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import queue_fill_estimator
        
        result = queue_fill_estimator(
            symbol="BTCUSDT",
            side="HOLD",
            price_levels=[50000.0],
            qty=0.1
        )
        
        assert result["success"] is False
        assert "BUY or SELL" in result["error"]["message"]
    
    def test_queue_fill_estimator_too_many_levels(self):
        """Test queue fill estimator with too many price levels."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import queue_fill_estimator
        
        result = queue_fill_estimator(
            symbol="BTCUSDT",
            side="BUY",
            price_levels=[50000, 49999, 49998, 49997, 49996, 49995],  # 6 levels
            qty=0.1
        )
        
        assert result["success"] is False
        assert "1-5" in result["error"]["message"]


class TestFillProbabilityCalculation:
    """Test fill probability and ETA calculations."""
    
    def test_calculate_fill_probability_instant(self):
        """Test fill probability when no queue."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import _calculate_fill_probability
        
        prob = _calculate_fill_probability(queue_qty=0, consumption_rate=0.5, time_horizon_s=30)
        assert prob == 1.0
    
    def test_calculate_fill_probability_with_queue(self):
        """Test fill probability with queue ahead."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import _calculate_fill_probability
        
        # Queue = 10, Rate = 0.5/s
        # Expected time = 20s
        # P(fill in 30s) should be > 0.5
        prob = _calculate_fill_probability(queue_qty=10, consumption_rate=0.5, time_horizon_s=30)
        assert 0.5 < prob < 1.0
    
    def test_calculate_fill_probability_no_consumption(self):
        """Test fill probability when no consumption."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import _calculate_fill_probability
        
        prob = _calculate_fill_probability(queue_qty=10, consumption_rate=0, time_horizon_s=30)
        assert prob == 0.0
    
    def test_calculate_eta_percentiles(self):
        """Test ETA percentile calculations."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import _calculate_eta_percentiles
        
        # Queue = 10, Rate = 1/s -> expected time = 10s
        eta = _calculate_eta_percentiles(queue_qty=10, consumption_rate=1.0)
        
        # p50 should be around 6.9s (median of exponential)
        # p95 should be around 30s
        assert 5 < eta["eta_p50_s"] < 10
        assert eta["eta_p95_s"] > eta["eta_p50_s"]


class TestAdverseSelectionScore:
    """Test adverse selection scoring."""
    
    def test_adverse_selection_neutral(self):
        """Test adverse selection with neutral conditions."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import _calculate_adverse_selection_score
        
        result = _calculate_adverse_selection_score(
            side="BUY",
            obi_mean=0.0,
            flow_imbalance=0.0,
            price=50000,
            mid_price=50000
        )
        
        # Should be around neutral (50)
        assert 40 <= result["score"] <= 60
    
    def test_adverse_selection_buy_unfavorable(self):
        """Test adverse selection for buy with sell pressure."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import _calculate_adverse_selection_score
        
        result = _calculate_adverse_selection_score(
            side="BUY",
            obi_mean=-0.5,  # Sell pressure
            flow_imbalance=-0.5,  # Sell aggression
            price=50100,  # Above mid
            mid_price=50000
        )
        
        # Should be high risk (>60)
        assert result["score"] > 60
    
    def test_adverse_selection_sell_unfavorable(self):
        """Test adverse selection for sell with buy pressure."""
        from binance_mcp_server.tools.futures.queue_fill_estimator import _calculate_adverse_selection_score
        
        result = _calculate_adverse_selection_score(
            side="SELL",
            obi_mean=0.5,  # Buy pressure
            flow_imbalance=0.5,  # Buy aggression
            price=49900,  # Below mid
            mid_price=50000
        )
        
        # Should be high risk (>60)
        assert result["score"] > 60


class TestVolumeProfileLevels:
    """Test volume_profile_levels tool."""
    
    @patch('binance_mcp_server.tools.futures.volume_profile_levels._fetch_historical_trades')
    @patch('binance_mcp_server.tools.futures.volume_profile_levels.MicrostructureDataFetcher')
    def test_volume_profile_success(self, mock_fetcher_class, mock_fetch_trades):
        """Test successful volume profile analysis."""
        # Create trades clustered around certain prices
        trades = []
        for i in range(100):
            if i < 40:
                price = 50000 + (i % 3)  # Cluster around 50000
            elif i < 70:
                price = 50100 + (i % 3)  # Cluster around 50100
            else:
                price = 49900 + (i % 10)  # Spread around 49900
            
            trades.append({
                "price": Decimal(str(price)),
                "qty": Decimal("0.1"),
                "time": int(time.time() * 1000) - i * 1000,
                "is_buyer_maker": i % 2 == 0
            })
        
        mock_fetch_trades.return_value = {
            "success": True,
            "trades": trades,
            "count": len(trades),
            "window_start": int(time.time() * 1000) - 240 * 60 * 1000,
            "window_end": int(time.time() * 1000),
            "window_minutes": 240
        }
        
        mock_fetcher = Mock()
        mock_fetcher.fetch_mark_price.return_value = {
            "success": True,
            "markPrice": Decimal("50050"),
            "timestamp": int(time.time() * 1000)
        }
        mock_fetcher_class.return_value = mock_fetcher
        
        from binance_mcp_server.tools.futures.volume_profile_levels import volume_profile_levels
        
        result = volume_profile_levels(
            symbol="BTCUSDT",
            window_minutes=240
        )
        
        assert "levels" in result
        assert result["levels"]["vpoc"] is not None
        assert "hvn" in result["levels"]
        assert "lvn" in result["levels"]
        assert "magnet_levels" in result["levels"]
    
    def test_volume_profile_invalid_symbol(self):
        """Test volume profile with invalid symbol."""
        from binance_mcp_server.tools.futures.volume_profile_levels import volume_profile_levels
        
        result = volume_profile_levels(
            symbol="INVALID",
            window_minutes=240
        )
        
        assert result["success"] is False
        assert "not in allowed list" in result["error"]["message"]


class TestVolumeProfileCalculations:
    """Test volume profile calculation functions."""
    
    def test_find_vpoc(self):
        """Test VPOC detection."""
        from binance_mcp_server.tools.futures.volume_profile_levels import _find_vpoc
        
        bins = {
            50000.0: {"volume": 100, "trade_count": 50},
            50050.0: {"volume": 200, "trade_count": 80},  # Highest volume
            50100.0: {"volume": 80, "trade_count": 30},
        }
        
        vpoc = _find_vpoc(bins)
        assert vpoc == 50050.0
    
    def test_find_value_area(self):
        """Test value area calculation."""
        from binance_mcp_server.tools.futures.volume_profile_levels import _find_value_area
        
        # Create bins where 70% of volume is in middle bins
        bins = {
            49900.0: {"volume": 10},
            49950.0: {"volume": 30},
            50000.0: {"volume": 100},  # VPOC
            50050.0: {"volume": 30},
            50100.0: {"volume": 10},
        }
        total = 180
        
        val, vah = _find_value_area(bins, total, 70.0)
        
        assert val is not None
        assert vah is not None
        assert val <= 50000.0 <= vah
    
    def test_find_volume_nodes(self):
        """Test HVN/LVN detection."""
        from binance_mcp_server.tools.futures.volume_profile_levels import _find_volume_nodes
        
        bins = {
            49900.0: {"volume": 5},    # LVN
            49950.0: {"volume": 50},
            50000.0: {"volume": 150},  # HVN
            50050.0: {"volume": 50},
            50100.0: {"volume": 3},    # LVN
        }
        
        hvn, lvn = _find_volume_nodes(bins, threshold_multiplier=1.5)
        
        assert len(hvn) >= 1
        assert hvn[0]["price"] == 50000.0
        assert len(lvn) >= 1


class TestMicrostructureCache:
    """Test MicrostructureCache class."""
    
    def test_cache_set_get(self):
        """Test basic cache set and get."""
        from binance_mcp_server.tools.futures.microstructure_data import MicrostructureCache
        
        cache = MicrostructureCache(default_ttl=1.0)
        cache.set("test_key", {"data": 123})
        
        result = cache.get("test_key")
        assert result is not None
        assert result["data"] == 123
    
    def test_cache_expiry(self):
        """Test cache expiry."""
        from binance_mcp_server.tools.futures.microstructure_data import MicrostructureCache
        
        cache = MicrostructureCache(default_ttl=0.1)  # 100ms TTL
        cache.set("test_key", {"data": 123})
        
        # Wait for expiry
        time.sleep(0.15)
        
        result = cache.get("test_key")
        assert result is None
    
    def test_cache_custom_ttl(self):
        """Test cache with custom TTL."""
        from binance_mcp_server.tools.futures.microstructure_data import MicrostructureCache
        
        cache = MicrostructureCache(default_ttl=1.0)
        cache.set("short_key", {"data": 1}, ttl=0.1)
        cache.set("long_key", {"data": 2}, ttl=2.0)
        
        time.sleep(0.15)
        
        assert cache.get("short_key") is None
        assert cache.get("long_key") is not None


class TestOutputSizeConstraint:
    """Test that outputs stay within 2KB limit."""
    
    @patch('binance_mcp_server.tools.futures.queue_fill_estimator.MicrostructureDataFetcher')
    def test_queue_fill_output_size(self, mock_fetcher_class):
        """Test that queue fill estimator output is <= 2KB."""
        import json
        
        mock_fetcher = Mock()
        mock_fetcher.fetch_orderbook.return_value = {
            "success": True,
            "bids": [(Decimal(str(50000 - i)), Decimal("1")) for i in range(100)],
            "asks": [(Decimal(str(50001 + i)), Decimal("1")) for i in range(100)],
            "lastUpdateId": 123456,
            "timestamp": int(time.time() * 1000)
        }
        
        now = int(time.time() * 1000)
        mock_fetcher.fetch_agg_trades.return_value = {
            "success": True,
            "trades": [
                {"price": Decimal("50000"), "qty": Decimal("0.1"), "time": now - i, "is_buyer_maker": i % 2 == 0}
                for i in range(100)
            ],
            "count": 100
        }
        
        mock_fetcher.fetch_mark_price.return_value = {
            "success": True,
            "markPrice": Decimal("50000.50"),
            "timestamp": int(time.time() * 1000)
        }
        
        mock_fetcher_class.return_value = mock_fetcher
        
        from binance_mcp_server.tools.futures.queue_fill_estimator import queue_fill_estimator
        
        result = queue_fill_estimator(
            symbol="BTCUSDT",
            side="BUY",
            price_levels=[50000, 49999, 49998, 49997, 49996],  # Max 5 levels
            qty=0.1
        )
        
        # Convert Decimals to strings for JSON serialization
        result_json = json.dumps(result, default=str)
        size_bytes = len(result_json.encode('utf-8'))
        
        assert size_bytes <= 2048, f"Output size {size_bytes} exceeds 2KB limit"
    
    @patch('binance_mcp_server.tools.futures.volume_profile_levels._fetch_historical_trades')
    @patch('binance_mcp_server.tools.futures.volume_profile_levels.MicrostructureDataFetcher')
    def test_volume_profile_output_size(self, mock_fetcher_class, mock_fetch_trades):
        """Test that volume profile output is <= 2KB."""
        import json
        
        trades = [
            {
                "price": Decimal(str(50000 + (i % 100))),
                "qty": Decimal("0.1"),
                "time": int(time.time() * 1000) - i * 100,
                "is_buyer_maker": i % 2 == 0
            }
            for i in range(500)
        ]
        
        mock_fetch_trades.return_value = {
            "success": True,
            "trades": trades,
            "count": len(trades),
            "window_start": int(time.time() * 1000) - 240 * 60 * 1000,
            "window_end": int(time.time() * 1000),
            "window_minutes": 240
        }
        
        mock_fetcher = Mock()
        mock_fetcher.fetch_mark_price.return_value = {
            "success": True,
            "markPrice": Decimal("50050"),
            "timestamp": int(time.time() * 1000)
        }
        mock_fetcher_class.return_value = mock_fetcher
        
        from binance_mcp_server.tools.futures.volume_profile_levels import volume_profile_levels
        
        result = volume_profile_levels(
            symbol="BTCUSDT",
            window_minutes=240
        )
        
        # Convert Decimals to strings for JSON serialization
        result_json = json.dumps(result, default=str)
        size_bytes = len(result_json.encode('utf-8'))
        
        assert size_bytes <= 2048, f"Output size {size_bytes} exceeds 2KB limit"
