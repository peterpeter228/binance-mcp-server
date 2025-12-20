"""
Unit tests for Limit Order Analysis Tools.

Tests queue_fill_estimator and volume_profile_levels with mock data.
"""

import pytest
import time
from unittest.mock import patch, MagicMock
from typing import List, Tuple

# Import the tools and data structures
from binance_mcp_server.tools.futures.queue_fill_estimator import (
    queue_fill_estimator,
    calculate_obi,
    calculate_consumption_rate,
    estimate_queue_position,
    calculate_fill_probability,
    calculate_eta,
    calculate_adverse_selection_score,
    calculate_micro_health_score,
    detect_walls,
    QueueMetrics,
    GlobalMetrics,
)
from binance_mcp_server.tools.futures.volume_profile_levels import (
    volume_profile_levels,
    build_volume_profile,
    find_vpoc,
    find_value_area,
    find_hvn,
    find_lvn,
    find_single_print_zones,
    find_magnet_levels,
    calculate_dynamic_bin_size,
    VolumeProfileBin,
)
from binance_mcp_server.tools.futures.market_data_collector import (
    OrderBookSnapshot,
    TradeRecord,
    MarketDataCollector,
)


# ============================================================================
# TEST FIXTURES
# ============================================================================


@pytest.fixture
def mock_orderbook():
    """Create a realistic mock orderbook snapshot."""
    return OrderBookSnapshot(
        symbol="BTCUSDT",
        timestamp_ms=int(time.time() * 1000),
        last_update_id=123456789,
        bids=[
            (42000.0, 5.0),   # Best bid
            (41999.0, 3.0),
            (41998.0, 8.0),
            (41997.0, 2.0),
            (41996.0, 10.0),
            (41995.0, 4.0),
            (41990.0, 15.0),  # Larger wall
            (41985.0, 6.0),
            (41980.0, 7.0),
            (41975.0, 5.0),
        ],
        asks=[
            (42001.0, 4.0),   # Best ask
            (42002.0, 6.0),
            (42003.0, 2.0),
            (42004.0, 5.0),
            (42005.0, 8.0),
            (42006.0, 3.0),
            (42010.0, 20.0),  # Large wall
            (42015.0, 7.0),
            (42020.0, 4.0),
            (42025.0, 6.0),
        ]
    )


@pytest.fixture
def mock_trades():
    """Create realistic mock trade records."""
    base_time = int(time.time() * 1000) - 30000  # 30s ago
    trades = []
    
    # Simulate mixed buy/sell trades
    trade_data = [
        # (price, qty, is_buyer_maker, time_offset_ms)
        (42000.5, 0.5, True, 0),      # Sell aggressor
        (42001.0, 0.3, False, 1000),  # Buy aggressor
        (42000.8, 0.8, True, 2000),   # Sell aggressor
        (42001.5, 0.4, False, 3000),  # Buy aggressor
        (42000.2, 1.2, True, 4000),   # Large sell
        (42001.8, 0.6, False, 5000),  # Buy aggressor
        (42000.0, 0.9, True, 6000),   # Sell aggressor
        (42002.0, 0.5, False, 7000),  # Buy aggressor
        (42001.2, 0.7, True, 8000),   # Sell aggressor
        (42002.5, 1.0, False, 9000),  # Buy aggressor
        (42000.5, 0.4, True, 10000),  # Sell aggressor
        (42001.0, 0.3, False, 11000), # Buy aggressor
        (41999.5, 2.0, True, 12000),  # Large sell (price drops)
        (42000.0, 0.5, False, 13000), # Buy aggressor
        (42001.5, 0.6, False, 14000), # Buy aggressor
    ]
    
    for i, (price, qty, is_buyer_maker, offset) in enumerate(trade_data):
        trades.append(TradeRecord(
            agg_trade_id=1000 + i,
            price=price,
            qty=qty,
            first_trade_id=2000 + i * 10,
            last_trade_id=2000 + i * 10 + 9,
            timestamp_ms=base_time + offset,
            is_buyer_maker=is_buyer_maker
        ))
    
    return trades


@pytest.fixture
def mock_trades_for_volume_profile():
    """Create mock trades with clear volume distribution patterns."""
    base_time = int(time.time() * 1000) - 3600000  # 1 hour ago
    trades = []
    
    # Create trades with distinct volume clustering
    # High volume around 42000-42100 (VPOC area)
    for i in range(50):
        trades.append(TradeRecord(
            agg_trade_id=i,
            price=42000 + (i % 10) * 10,  # 42000-42090
            qty=2.0,  # High volume
            first_trade_id=i * 10,
            last_trade_id=i * 10 + 9,
            timestamp_ms=base_time + i * 10000,
            is_buyer_maker=i % 2 == 0
        ))
    
    # Medium volume at 41900-42000 and 42100-42200
    for i in range(30):
        trades.append(TradeRecord(
            agg_trade_id=100 + i,
            price=41900 + (i % 10) * 10,
            qty=1.0,
            first_trade_id=(100 + i) * 10,
            last_trade_id=(100 + i) * 10 + 9,
            timestamp_ms=base_time + (50 + i) * 10000,
            is_buyer_maker=i % 2 == 0
        ))
    
    for i in range(30):
        trades.append(TradeRecord(
            agg_trade_id=200 + i,
            price=42100 + (i % 10) * 10,
            qty=1.0,
            first_trade_id=(200 + i) * 10,
            last_trade_id=(200 + i) * 10 + 9,
            timestamp_ms=base_time + (80 + i) * 10000,
            is_buyer_maker=i % 2 == 0
        ))
    
    # Low volume (LVN) at 42200-42300
    for i in range(5):
        trades.append(TradeRecord(
            agg_trade_id=300 + i,
            price=42200 + i * 20,
            qty=0.1,
            first_trade_id=(300 + i) * 10,
            last_trade_id=(300 + i) * 10 + 9,
            timestamp_ms=base_time + (110 + i) * 10000,
            is_buyer_maker=i % 2 == 0
        ))
    
    return trades


# ============================================================================
# QUEUE FILL ESTIMATOR TESTS
# ============================================================================


class TestQueueFillEstimator:
    """Tests for queue_fill_estimator tool."""
    
    def test_calculate_obi_balanced(self):
        """Test OBI calculation with balanced orderbook."""
        bids = [(100, 10), (99, 10), (98, 10)]
        asks = [(101, 10), (102, 10), (103, 10)]
        
        obi = calculate_obi(bids, asks, levels=3)
        assert obi == 0.0  # Perfectly balanced
    
    def test_calculate_obi_bullish(self):
        """Test OBI calculation with bullish imbalance."""
        bids = [(100, 20), (99, 20), (98, 20)]  # More bids: total 60
        asks = [(101, 10), (102, 10), (103, 10)]  # Less asks: total 30
        
        obi = calculate_obi(bids, asks, levels=3)
        assert obi > 0  # Bullish (more bids)
        # OBI = (60 - 30) / (60 + 30) = 30/90 = 0.333...
        assert abs(obi - 0.333) < 0.01
    
    def test_calculate_obi_bearish(self):
        """Test OBI calculation with bearish imbalance."""
        bids = [(100, 10), (99, 10), (98, 10)]
        asks = [(101, 20), (102, 20), (103, 20)]  # More asks
        
        obi = calculate_obi(bids, asks, levels=3)
        assert obi < 0  # Bearish (more asks)
    
    def test_calculate_consumption_rate(self, mock_trades):
        """Test consumption rate calculation."""
        rate, total, count = calculate_consumption_rate(mock_trades, "sell", 30.0)
        
        assert rate > 0
        assert total > 0
        assert count > 0
    
    def test_estimate_queue_position_buy(self, mock_orderbook):
        """Test queue position estimation for BUY orders."""
        queue_ahead, level_qty = estimate_queue_position(
            mock_orderbook, "BUY", 42000.0  # At best bid
        )
        
        assert queue_ahead >= 5.0  # At least the best bid quantity
    
    def test_estimate_queue_position_sell(self, mock_orderbook):
        """Test queue position estimation for SELL orders."""
        queue_ahead, level_qty = estimate_queue_position(
            mock_orderbook, "SELL", 42001.0  # At best ask
        )
        
        assert queue_ahead >= 4.0  # At least the best ask quantity
    
    def test_calculate_fill_probability_high_consumption(self):
        """Test fill probability with high consumption rate."""
        prob = calculate_fill_probability(
            queue_ahead=10.0,
            consumption_rate=2.0,  # 2 units/second
            time_window_seconds=30
        )
        
        assert 0 < prob < 1
        assert prob > 0.9  # High probability with high consumption
    
    def test_calculate_fill_probability_zero_queue(self):
        """Test fill probability when at front of queue."""
        prob = calculate_fill_probability(
            queue_ahead=0.0,
            consumption_rate=1.0,
            time_window_seconds=30
        )
        
        assert prob == 1.0  # Already at front
    
    def test_calculate_fill_probability_zero_consumption(self):
        """Test fill probability with no consumption."""
        prob = calculate_fill_probability(
            queue_ahead=10.0,
            consumption_rate=0.0,
            time_window_seconds=30
        )
        
        assert prob == 0.0  # No consumption = no fill
    
    def test_calculate_eta_median(self):
        """Test ETA calculation for median (p50)."""
        eta = calculate_eta(
            queue_ahead=10.0,
            consumption_rate=2.0,
            percentile=0.5
        )
        
        assert eta is not None
        assert eta > 0
        assert eta < 10  # Should be reasonable
    
    def test_calculate_eta_95th_percentile(self):
        """Test ETA calculation for 95th percentile."""
        eta_p50 = calculate_eta(10.0, 2.0, 0.5)
        eta_p95 = calculate_eta(10.0, 2.0, 0.95)
        
        assert eta_p50 is not None
        assert eta_p95 is not None
        assert eta_p95 > eta_p50  # 95th percentile should be higher
    
    def test_calculate_adverse_selection_buy(self, mock_trades, mock_orderbook):
        """Test adverse selection for BUY orders."""
        score, notes = calculate_adverse_selection_score(
            mock_trades, mock_orderbook, "BUY", 42000.0, lookback_seconds=15.0
        )
        
        assert 0 <= score <= 100
        assert isinstance(notes, list)
        assert len(notes) <= 2
    
    def test_calculate_micro_health_score(self, mock_orderbook, mock_trades):
        """Test microstructure health score calculation."""
        score = calculate_micro_health_score(mock_orderbook, mock_trades, 30.0)
        
        assert 0 <= score <= 100
    
    def test_detect_walls_low(self, mock_orderbook):
        """Test wall detection - low risk."""
        # With normal orderbook, should be low or medium
        risk = detect_walls(mock_orderbook, "BUY")
        assert risk in ("low", "medium", "high")
    
    @patch('binance_mcp_server.tools.futures.queue_fill_estimator.get_market_data_collector')
    def test_queue_fill_estimator_success(self, mock_get_collector, mock_orderbook, mock_trades):
        """Test full queue_fill_estimator with mocked data."""
        # Setup mock collector
        mock_collector = MagicMock()
        mock_collector.fetch_orderbook.return_value = (True, mock_orderbook, None)
        mock_collector.ensure_trade_history.return_value = (True, None)
        mock_collector.get_buffered_trades.return_value = mock_trades
        mock_collector.fetch_mark_price.return_value = (True, MagicMock(mark_price=42000.0), None)
        mock_get_collector.return_value = mock_collector
        
        result = queue_fill_estimator(
            symbol="BTCUSDT",
            side="BUY",
            price_levels=[42000.0, 41999.0, 41998.0],
            qty=1.0,
            lookback_seconds=30.0
        )
        
        assert result["success"] is True
        assert "ts_ms" in result
        assert "inputs" in result
        assert "per_level" in result
        assert "global" in result
        assert len(result["per_level"]) == 3
        
        # Check per_level structure
        for level in result["per_level"]:
            assert "price" in level
            assert "queue_qty_est" in level
            assert "fill_prob_30s" in level
            assert "fill_prob_60s" in level
            assert "adverse_selection_score" in level
        
        # Check global structure
        assert "micro_health_score" in result["global"]
        assert "spread_bps" in result["global"]
        assert "recommendation" in result["global"]
    
    def test_queue_fill_estimator_invalid_symbol(self):
        """Test with invalid symbol."""
        result = queue_fill_estimator(
            symbol="INVALID",
            side="BUY",
            price_levels=[42000.0],
            qty=1.0
        )
        
        assert result["success"] is False
        assert "error" in result
    
    def test_queue_fill_estimator_invalid_side(self):
        """Test with invalid side."""
        result = queue_fill_estimator(
            symbol="BTCUSDT",
            side="INVALID",
            price_levels=[42000.0],
            qty=1.0
        )
        
        assert result["success"] is False


# ============================================================================
# VOLUME PROFILE LEVELS TESTS
# ============================================================================


class TestVolumeProfileLevels:
    """Tests for volume_profile_levels tool."""
    
    def test_calculate_dynamic_bin_size_btc(self):
        """Test dynamic bin size for BTC price range."""
        bin_size = calculate_dynamic_bin_size(1000.0)  # $1000 range
        
        assert bin_size > 0
        assert bin_size <= 100  # Should be reasonable for BTC
    
    def test_calculate_dynamic_bin_size_small_range(self):
        """Test dynamic bin size for small range."""
        bin_size = calculate_dynamic_bin_size(50.0)  # $50 range
        
        assert bin_size > 0
        assert bin_size <= 10
    
    def test_build_volume_profile(self, mock_trades_for_volume_profile):
        """Test volume profile building."""
        profile = build_volume_profile(mock_trades_for_volume_profile, bin_size=25.0)
        
        assert len(profile) > 0
        assert all(isinstance(b, VolumeProfileBin) for b in profile)
        
        # Check volume is non-negative
        for bin_data in profile:
            assert bin_data.volume >= 0
            assert bin_data.buy_volume >= 0
            assert bin_data.sell_volume >= 0
    
    def test_find_vpoc(self, mock_trades_for_volume_profile):
        """Test VPOC finding."""
        profile = build_volume_profile(mock_trades_for_volume_profile, bin_size=25.0)
        vpoc = find_vpoc(profile)
        
        assert vpoc is not None
        assert 42000 <= vpoc <= 42100  # Should be in high volume area
    
    def test_find_value_area(self, mock_trades_for_volume_profile):
        """Test Value Area (VAH/VAL) finding."""
        profile = build_volume_profile(mock_trades_for_volume_profile, bin_size=25.0)
        vah, val = find_value_area(profile, percentage=0.70)
        
        assert vah is not None
        assert val is not None
        assert vah > val  # VAH should be higher than VAL
    
    def test_find_hvn(self, mock_trades_for_volume_profile):
        """Test High Volume Node finding."""
        profile = build_volume_profile(mock_trades_for_volume_profile, bin_size=25.0)
        hvn_list = find_hvn(profile, top_n=3)
        
        assert isinstance(hvn_list, list)
        assert len(hvn_list) <= 3
    
    def test_find_lvn(self, mock_trades_for_volume_profile):
        """Test Low Volume Node finding."""
        profile = build_volume_profile(mock_trades_for_volume_profile, bin_size=25.0)
        lvn_list = find_lvn(profile, top_n=3)
        
        assert isinstance(lvn_list, list)
        assert len(lvn_list) <= 3
    
    def test_find_single_print_zones(self, mock_trades_for_volume_profile):
        """Test single print zone detection."""
        profile = build_volume_profile(mock_trades_for_volume_profile, bin_size=25.0)
        zones = find_single_print_zones(profile, max_zones=2)
        
        assert isinstance(zones, list)
        for zone in zones:
            assert "low" in zone
            assert "high" in zone
            assert zone["high"] > zone["low"]
    
    def test_find_magnet_levels(self, mock_trades_for_volume_profile):
        """Test magnet level finding."""
        profile = build_volume_profile(mock_trades_for_volume_profile, bin_size=25.0)
        vpoc = find_vpoc(profile)
        vah, val = find_value_area(profile)
        
        magnets = find_magnet_levels(profile, vpoc, vah, val, max_levels=3)
        
        assert isinstance(magnets, list)
        assert len(magnets) <= 3
        if vpoc:
            assert round(vpoc, 2) in magnets  # VPOC should be a magnet
    
    @patch('binance_mcp_server.tools.futures.volume_profile_levels.get_market_data_collector')
    def test_volume_profile_levels_success(self, mock_get_collector, mock_trades_for_volume_profile):
        """Test full volume_profile_levels with mocked data."""
        # Setup mock collector
        mock_collector = MagicMock()
        mock_collector.fetch_historical_trades.return_value = (True, mock_trades_for_volume_profile, None)
        mock_get_collector.return_value = mock_collector
        
        result = volume_profile_levels(
            symbol="BTCUSDT",
            window_minutes=240,
            bin_size=25.0
        )
        
        assert result["success"] is True
        assert "ts_ms" in result
        assert "window" in result
        assert "levels" in result
        
        # Check levels structure
        levels = result["levels"]
        assert "vpoc" in levels
        assert "vah" in levels
        assert "val" in levels
        assert "hvn" in levels
        assert "lvn" in levels
        assert "magnet_levels" in levels
        assert "avoid_zones" in levels
    
    def test_volume_profile_levels_invalid_symbol(self):
        """Test with invalid symbol."""
        result = volume_profile_levels(
            symbol="INVALID",
            window_minutes=240
        )
        
        assert result["success"] is False
        assert "error" in result


# ============================================================================
# ORDERBOOK SNAPSHOT TESTS
# ============================================================================


class TestOrderBookSnapshot:
    """Tests for OrderBookSnapshot dataclass."""
    
    def test_best_bid(self, mock_orderbook):
        """Test best bid property."""
        best_bid = mock_orderbook.best_bid
        assert best_bid is not None
        assert best_bid[0] == 42000.0
        assert best_bid[1] == 5.0
    
    def test_best_ask(self, mock_orderbook):
        """Test best ask property."""
        best_ask = mock_orderbook.best_ask
        assert best_ask is not None
        assert best_ask[0] == 42001.0
        assert best_ask[1] == 4.0
    
    def test_mid_price(self, mock_orderbook):
        """Test mid price calculation."""
        mid = mock_orderbook.mid_price
        assert mid == 42000.5
    
    def test_spread(self, mock_orderbook):
        """Test spread calculation."""
        spread = mock_orderbook.spread
        assert spread == 1.0  # 42001 - 42000
    
    def test_spread_bps(self, mock_orderbook):
        """Test spread in basis points."""
        spread_bps = mock_orderbook.spread_bps
        assert spread_bps is not None
        assert spread_bps > 0
        assert spread_bps < 10  # Should be small for tight market


# ============================================================================
# TRADE RECORD TESTS
# ============================================================================


class TestTradeRecord:
    """Tests for TradeRecord dataclass."""
    
    def test_side_sell(self):
        """Test side property for seller aggressor."""
        trade = TradeRecord(
            agg_trade_id=1,
            price=42000.0,
            qty=1.0,
            first_trade_id=100,
            last_trade_id=109,
            timestamp_ms=int(time.time() * 1000),
            is_buyer_maker=True  # Buyer was maker -> seller was aggressor
        )
        
        assert trade.side == "sell"
    
    def test_side_buy(self):
        """Test side property for buyer aggressor."""
        trade = TradeRecord(
            agg_trade_id=1,
            price=42000.0,
            qty=1.0,
            first_trade_id=100,
            last_trade_id=109,
            timestamp_ms=int(time.time() * 1000),
            is_buyer_maker=False  # Buyer was taker -> buyer was aggressor
        )
        
        assert trade.side == "buy"


# ============================================================================
# VOLUME PROFILE BIN TESTS
# ============================================================================


class TestVolumeProfileBin:
    """Tests for VolumeProfileBin dataclass."""
    
    def test_delta_positive(self):
        """Test positive delta (more buying)."""
        bin_data = VolumeProfileBin(
            price_low=42000,
            price_high=42010,
            price_mid=42005,
            volume=100,
            buy_volume=70,
            sell_volume=30,
            trade_count=50
        )
        
        assert bin_data.delta == 40  # 70 - 30
        assert bin_data.delta_pct == 40.0  # 40 / 100 * 100
    
    def test_delta_negative(self):
        """Test negative delta (more selling)."""
        bin_data = VolumeProfileBin(
            price_low=42000,
            price_high=42010,
            price_mid=42005,
            volume=100,
            buy_volume=30,
            sell_volume=70,
            trade_count=50
        )
        
        assert bin_data.delta == -40
        assert bin_data.delta_pct == -40.0


# ============================================================================
# OUTPUT SIZE TESTS
# ============================================================================


class TestOutputSize:
    """Tests to verify output JSON size <= 2KB."""
    
    @patch('binance_mcp_server.tools.futures.queue_fill_estimator.get_market_data_collector')
    def test_queue_fill_estimator_output_size(self, mock_get_collector, mock_orderbook, mock_trades):
        """Verify queue_fill_estimator output is <= 2KB."""
        import json
        
        mock_collector = MagicMock()
        mock_collector.fetch_orderbook.return_value = (True, mock_orderbook, None)
        mock_collector.ensure_trade_history.return_value = (True, None)
        mock_collector.get_buffered_trades.return_value = mock_trades
        mock_collector.fetch_mark_price.return_value = (True, MagicMock(mark_price=42000.0), None)
        mock_get_collector.return_value = mock_collector
        
        result = queue_fill_estimator(
            symbol="BTCUSDT",
            side="BUY",
            price_levels=[42000.0, 41999.0, 41998.0, 41997.0, 41996.0],  # Max 5 levels
            qty=1.0,
            lookback_seconds=30.0
        )
        
        json_str = json.dumps(result)
        assert len(json_str) <= 2048, f"Output size {len(json_str)} exceeds 2KB limit"
    
    @patch('binance_mcp_server.tools.futures.volume_profile_levels.get_market_data_collector')
    def test_volume_profile_output_size(self, mock_get_collector, mock_trades_for_volume_profile):
        """Verify volume_profile_levels output is <= 2KB."""
        import json
        
        mock_collector = MagicMock()
        mock_collector.fetch_historical_trades.return_value = (True, mock_trades_for_volume_profile, None)
        mock_get_collector.return_value = mock_collector
        
        result = volume_profile_levels(
            symbol="BTCUSDT",
            window_minutes=240,
            bin_size=25.0
        )
        
        json_str = json.dumps(result)
        assert len(json_str) <= 2048, f"Output size {len(json_str)} exceeds 2KB limit"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
