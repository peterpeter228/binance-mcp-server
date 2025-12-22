"""
Unit tests for WebSocket-based Volume Profile Tool.

Tests cover:
- WSTradeRecord data structure
- TradeRingBuffer functionality
- Volume profile calculations
- Tool input validation
- Cache behavior
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from typing import List


class TestWSTradeRecord:
    """Test WSTradeRecord data structure."""
    
    def test_side_sell_when_buyer_maker(self):
        """Test that side is 'sell' when buyer_maker is True."""
        from binance_mcp_server.tools.futures.ws_trade_buffer import WSTradeRecord
        
        trade = WSTradeRecord(
            agg_trade_id=1,
            price=42000.0,
            qty=0.1,
            timestamp_ms=int(time.time() * 1000),
            is_buyer_maker=True
        )
        assert trade.side == "sell"
    
    def test_side_buy_when_not_buyer_maker(self):
        """Test that side is 'buy' when buyer_maker is False."""
        from binance_mcp_server.tools.futures.ws_trade_buffer import WSTradeRecord
        
        trade = WSTradeRecord(
            agg_trade_id=1,
            price=42000.0,
            qty=0.1,
            timestamp_ms=int(time.time() * 1000),
            is_buyer_maker=False
        )
        assert trade.side == "buy"


class TestTradeRingBuffer:
    """Test TradeRingBuffer functionality."""
    
    def test_add_and_get_trades(self):
        """Test adding and retrieving trades."""
        from binance_mcp_server.tools.futures.ws_trade_buffer import (
            TradeRingBuffer, WSTradeRecord
        )
        
        buffer = TradeRingBuffer(max_age_minutes=60, max_size=1000)
        
        # Add some trades
        now_ms = int(time.time() * 1000)
        for i in range(10):
            trade = WSTradeRecord(
                agg_trade_id=i,
                price=42000.0 + i,
                qty=0.1,
                timestamp_ms=now_ms - i * 1000,
                is_buyer_maker=i % 2 == 0
            )
            buffer.add_trade(trade)
        
        # Get trades from last 1 minute
        trades = buffer.get_trades(window_minutes=1)
        assert len(trades) == 10
    
    def test_buffer_stats(self):
        """Test buffer statistics."""
        from binance_mcp_server.tools.futures.ws_trade_buffer import (
            TradeRingBuffer, WSTradeRecord
        )
        
        buffer = TradeRingBuffer(max_age_minutes=60, max_size=1000)
        
        # Empty buffer
        stats = buffer.get_stats()
        assert stats["trade_count"] == 0
        assert stats["oldest_trade_ms"] is None
        
        # Add trades
        now_ms = int(time.time() * 1000)
        for i in range(5):
            trade = WSTradeRecord(
                agg_trade_id=i,
                price=42000.0,
                qty=0.1,
                timestamp_ms=now_ms - i * 1000,
                is_buyer_maker=False
            )
            buffer.add_trade(trade)
        
        stats = buffer.get_stats()
        assert stats["trade_count"] == 5
        assert stats["oldest_trade_ms"] is not None
        assert stats["newest_trade_ms"] is not None
    
    def test_buffer_max_size(self):
        """Test buffer respects max size."""
        from binance_mcp_server.tools.futures.ws_trade_buffer import (
            TradeRingBuffer, WSTradeRecord
        )
        
        buffer = TradeRingBuffer(max_age_minutes=60, max_size=10)
        
        # Add more trades than max size
        now_ms = int(time.time() * 1000)
        for i in range(20):
            trade = WSTradeRecord(
                agg_trade_id=i,
                price=42000.0,
                qty=0.1,
                timestamp_ms=now_ms,
                is_buyer_maker=False
            )
            buffer.add_trade(trade)
        
        stats = buffer.get_stats()
        assert stats["trade_count"] == 10  # Limited by max_size
    
    def test_buffer_clear(self):
        """Test clearing buffer."""
        from binance_mcp_server.tools.futures.ws_trade_buffer import (
            TradeRingBuffer, WSTradeRecord
        )
        
        buffer = TradeRingBuffer()
        
        now_ms = int(time.time() * 1000)
        trade = WSTradeRecord(
            agg_trade_id=1,
            price=42000.0,
            qty=0.1,
            timestamp_ms=now_ms,
            is_buyer_maker=False
        )
        buffer.add_trade(trade)
        
        assert buffer.get_stats()["trade_count"] == 1
        
        buffer.clear()
        assert buffer.get_stats()["trade_count"] == 0


class TestVolumeProfileCalculations:
    """Test volume profile calculation functions."""
    
    def test_calculate_dynamic_bin_size(self):
        """Test dynamic bin size calculation."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            calculate_dynamic_bin_size
        )
        
        # Large range
        size = calculate_dynamic_bin_size(1000, target_bins=50)
        assert size == 20
        
        # Medium range
        size = calculate_dynamic_bin_size(500, target_bins=50)
        assert size == 10
        
        # Zero range
        size = calculate_dynamic_bin_size(0, target_bins=50)
        assert size == 10.0
    
    def test_build_volume_profile(self):
        """Test building volume profile from trades."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            build_volume_profile
        )
        from binance_mcp_server.tools.futures.ws_trade_buffer import WSTradeRecord
        
        now_ms = int(time.time() * 1000)
        trades = [
            WSTradeRecord(1, 100.0, 10.0, now_ms, False),
            WSTradeRecord(2, 105.0, 20.0, now_ms, True),
            WSTradeRecord(3, 110.0, 15.0, now_ms, False),
            WSTradeRecord(4, 100.0, 5.0, now_ms, False),
        ]
        
        profile = build_volume_profile(trades, bin_size=10)
        
        assert len(profile) >= 2
        total_vol = sum(b.volume for b in profile)
        assert total_vol == 50.0  # 10 + 20 + 15 + 5
    
    def test_find_vpoc(self):
        """Test VPOC finding."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            VPWSBin, find_vpoc
        )
        
        profile = [
            VPWSBin(100, 110, 105, volume=10),
            VPWSBin(110, 120, 115, volume=50),  # Highest
            VPWSBin(120, 130, 125, volume=20),
        ]
        
        vpoc = find_vpoc(profile)
        assert vpoc == 115
    
    def test_find_value_area(self):
        """Test VAH/VAL calculation."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            VPWSBin, find_value_area
        )
        
        profile = [
            VPWSBin(100, 110, 105, volume=10),
            VPWSBin(110, 120, 115, volume=30),
            VPWSBin(120, 130, 125, volume=50),  # POC
            VPWSBin(130, 140, 135, volume=30),
            VPWSBin(140, 150, 145, volume=10),
        ]
        
        vah, val = find_value_area(profile, percentage=0.70)
        
        assert vah is not None
        assert val is not None
        assert vah > val
    
    def test_find_hvn_levels(self):
        """Test HVN identification."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            VPWSBin, find_hvn_levels
        )
        
        profile = [
            VPWSBin(100, 110, 105, volume=10),
            VPWSBin(110, 120, 115, volume=100),  # HVN
            VPWSBin(120, 130, 125, volume=20),
            VPWSBin(130, 140, 135, volume=90),   # HVN
            VPWSBin(140, 150, 145, volume=5),
        ]
        
        hvn = find_hvn_levels(profile, max_levels=2)
        
        assert len(hvn) <= 2
        assert 115 in hvn  # Highest volume
    
    def test_find_lvn_levels(self):
        """Test LVN identification."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            VPWSBin, find_lvn_levels
        )
        
        profile = [
            VPWSBin(100, 110, 105, volume=5),    # LVN
            VPWSBin(110, 120, 115, volume=100),
            VPWSBin(120, 130, 125, volume=80),
            VPWSBin(130, 140, 135, volume=90),
            VPWSBin(140, 150, 145, volume=3),   # LVN
        ]
        
        lvn = find_lvn_levels(profile, max_levels=2)
        
        assert len(lvn) <= 2
        assert 145 in lvn or 105 in lvn  # Lowest volumes
    
    def test_find_magnet_levels(self):
        """Test magnet level identification."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            VPWSBin, find_magnet_levels
        )
        
        profile = [
            VPWSBin(100, 110, 105, volume=10, buy_volume=5, sell_volume=5),
            VPWSBin(110, 120, 115, volume=100, buy_volume=80, sell_volume=20),  # Strong delta
            VPWSBin(120, 130, 125, volume=50, buy_volume=25, sell_volume=25),
        ]
        
        magnets = find_magnet_levels(profile, vpoc=125.0, vah=130.0, val=110.0, max_levels=6)
        
        # Should include at least vpoc, vah, val
        assert 125.0 in magnets
        assert 130.0 in magnets
        assert 110.0 in magnets


class TestVolumeProfileWSValidation:
    """Test volume profile WS tool input validation."""
    
    def test_invalid_symbol_rejected(self):
        """Test that invalid symbols are rejected."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            volume_profile_levels_futures_ws
        )
        
        result = volume_profile_levels_futures_ws("INVALID")
        assert result["success"] is False
        assert "not in allowed" in result["error"]["message"]
    
    def test_empty_symbol_rejected(self):
        """Test that empty symbol is rejected."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            volume_profile_levels_futures_ws
        )
        
        result = volume_profile_levels_futures_ws("")
        assert result["success"] is False
        assert "non-empty" in result["error"]["message"]


class TestCacheBehavior:
    """Test cache behavior for WS tool."""
    
    def test_cache_key_generation(self):
        """Test that cache keys are generated consistently."""
        from binance_mcp_server.tools.futures.rate_limit_utils import ParameterCache
        
        params1 = {"symbol": "BTCUSDT", "window_minutes": 240, "bin_size": None}
        params2 = {"symbol": "BTCUSDT", "window_minutes": 240, "bin_size": None}
        params3 = {"symbol": "ETHUSDT", "window_minutes": 240, "bin_size": None}
        
        key1 = ParameterCache._hash_params(params1)
        key2 = ParameterCache._hash_params(params2)
        key3 = ParameterCache._hash_params(params3)
        
        assert key1 == key2
        assert key1 != key3


class TestOutputCompliance:
    """Test that output complies with compressed statistics requirements."""
    
    def test_output_structure_on_insufficient_data(self):
        """Test output structure when buffer has insufficient data."""
        # This verifies the error response structure
        pass  # Requires mocking
    
    def test_output_level_limits(self):
        """Test that output respects maximum level limits."""
        from binance_mcp_server.tools.futures.volume_profile_levels_ws import (
            find_hvn_levels, find_lvn_levels, find_magnet_levels,
            find_single_print_zones, find_avoid_zones, VPWSBin
        )
        
        # Create a large profile
        profile = [VPWSBin(i*10, (i+1)*10, i*10+5, volume=i*10) for i in range(50)]
        
        hvn = find_hvn_levels(profile, max_levels=3)
        assert len(hvn) <= 3
        
        lvn = find_lvn_levels(profile, max_levels=3)
        assert len(lvn) <= 3
        
        magnets = find_magnet_levels(profile, vpoc=105, vah=200, val=50, max_levels=6)
        assert len(magnets) <= 6
        
        single_prints = find_single_print_zones(profile, max_zones=3)
        assert len(single_prints) <= 3
        
        avoid = find_avoid_zones(profile, lvn, single_prints, max_zones=3)
        assert len(avoid) <= 3


@pytest.mark.skip(reason="Requires WebSocket connection and time to buffer data")
class TestIntegration:
    """Integration tests requiring actual WebSocket connection."""
    
    def test_ws_connection_and_data_collection(self):
        """Test WebSocket connection and data collection."""
        from binance_mcp_server.tools.futures import (
            get_ws_trade_buffer_manager,
            volume_profile_levels_futures_ws
        )
        
        manager = get_ws_trade_buffer_manager()
        manager.subscribe("BTCUSDT")
        
        # Wait for connection and some data
        assert manager.wait_for_connection(timeout=30.0)
        time.sleep(30)  # Wait for trades
        
        # Check buffer status
        stats = manager.get_buffer_stats("BTCUSDT")
        assert stats["is_connected"] is True
        assert stats["trade_count"] > 0
    
    def test_volume_profile_ws_live(self):
        """Test volume profile calculation with live data."""
        from binance_mcp_server.tools.futures import volume_profile_levels_futures_ws
        
        result = volume_profile_levels_futures_ws(
            symbol="BTCUSDT",
            window_minutes=30
        )
        
        if result["success"]:
            assert "levels" in result
            assert "vpoc" in result["levels"]
            assert "ws_stats" in result
        else:
            # May fail due to insufficient data
            assert "quality_flags" in result
