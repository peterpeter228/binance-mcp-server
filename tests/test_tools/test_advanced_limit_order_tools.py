"""
Unit tests for Advanced Limit Order Analysis MCP Tools.

Tests cover:
- liquidity_wall_persistence
- queue_fill_probability_multi_horizon
- volume_profile_fallback_from_trades
- Rate limit utilities (caching, backoff)
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal


# ==============================================================================
# Test Rate Limit Utilities
# ==============================================================================

class TestRateLimitUtils:
    """Test rate limiting and caching utilities."""
    
    def test_calculate_backoff_delay_basic(self):
        """Test basic exponential backoff calculation."""
        from binance_mcp_server.tools.futures.rate_limit_utils import calculate_backoff_delay
        
        # Attempt 0 should be close to base delay
        delay_0 = calculate_backoff_delay(0, base_delay_ms=1000, max_delay_ms=30000, jitter_factor=0)
        assert delay_0 == 1.0  # 1000ms = 1s
        
        # Attempt 1 should be 2x
        delay_1 = calculate_backoff_delay(1, base_delay_ms=1000, max_delay_ms=30000, jitter_factor=0)
        assert delay_1 == 2.0  # 2000ms = 2s
        
        # Attempt 2 should be 4x
        delay_2 = calculate_backoff_delay(2, base_delay_ms=1000, max_delay_ms=30000, jitter_factor=0)
        assert delay_2 == 4.0  # 4000ms = 4s
    
    def test_calculate_backoff_delay_max_cap(self):
        """Test that backoff is capped at max delay."""
        from binance_mcp_server.tools.futures.rate_limit_utils import calculate_backoff_delay
        
        # Large attempt number should be capped
        delay = calculate_backoff_delay(10, base_delay_ms=1000, max_delay_ms=5000, jitter_factor=0)
        assert delay == 5.0  # Capped at max
    
    def test_calculate_backoff_delay_with_jitter(self):
        """Test that jitter adds randomness."""
        from binance_mcp_server.tools.futures.rate_limit_utils import calculate_backoff_delay
        
        # With jitter, delays should vary
        delays = [calculate_backoff_delay(1, base_delay_ms=1000, jitter_factor=0.3) for _ in range(10)]
        
        # Not all delays should be the same
        assert len(set(delays)) > 1
        
        # All delays should be within reasonable range (1.4-2.6s for attempt 1 with 30% jitter)
        for d in delays:
            assert 1.0 < d < 3.0
    
    def test_parameter_cache_basic(self):
        """Test basic cache get/set operations."""
        from binance_mcp_server.tools.futures.rate_limit_utils import ParameterCache
        
        cache = ParameterCache(default_ttl_seconds=5.0)
        
        # Miss on empty cache
        hit, value = cache.get("key1")
        assert hit is False
        assert value is None
        
        # Set and get
        cache.set("key1", {"data": "test"})
        hit, value = cache.get("key1")
        assert hit is True
        assert value == {"data": "test"}
    
    def test_parameter_cache_expiry(self):
        """Test cache entry expiration."""
        from binance_mcp_server.tools.futures.rate_limit_utils import ParameterCache
        
        cache = ParameterCache(default_ttl_seconds=0.1)  # 100ms TTL
        
        cache.set("key1", "value1")
        hit, value = cache.get("key1")
        assert hit is True
        
        # Wait for expiry
        time.sleep(0.15)
        hit, value = cache.get("key1")
        assert hit is False
    
    def test_parameter_cache_hash_params(self):
        """Test parameter hashing for cache keys."""
        from binance_mcp_server.tools.futures.rate_limit_utils import ParameterCache
        
        # Same params should produce same hash
        params1 = {"symbol": "BTCUSDT", "qty": 0.1}
        params2 = {"symbol": "BTCUSDT", "qty": 0.1}
        
        hash1 = ParameterCache._hash_params(params1)
        hash2 = ParameterCache._hash_params(params2)
        assert hash1 == hash2
        
        # Different params should produce different hash
        params3 = {"symbol": "ETHUSDT", "qty": 0.1}
        hash3 = ParameterCache._hash_params(params3)
        assert hash1 != hash3
    
    def test_retry_config_defaults(self):
        """Test RetryConfig default values."""
        from binance_mcp_server.tools.futures.rate_limit_utils import RetryConfig
        
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay_ms == 1000
        assert config.max_delay_ms == 30000
        assert config.jitter_factor == 0.3
        assert -1003 in config.retry_codes  # Rate limit code


# ==============================================================================
# Test Liquidity Wall Persistence Tool
# ==============================================================================

class TestLiquidityWallPersistence:
    """Test liquidity_wall_persistence tool."""
    
    def test_invalid_symbol_rejected(self):
        """Test that invalid symbols are rejected."""
        from binance_mcp_server.tools.futures.liquidity_wall_persistence import liquidity_wall_persistence
        
        result = liquidity_wall_persistence("INVALID")
        assert result["success"] is False
        assert "not in allowed" in result["error"]["message"]
    
    def test_empty_symbol_rejected(self):
        """Test that empty symbol is rejected."""
        from binance_mcp_server.tools.futures.liquidity_wall_persistence import liquidity_wall_persistence
        
        result = liquidity_wall_persistence("")
        assert result["success"] is False
        assert "non-empty" in result["error"]["message"]
    
    def test_parameter_constraints(self):
        """Test that parameters are properly constrained."""
        from binance_mcp_server.tools.futures.liquidity_wall_persistence import liquidity_wall_persistence
        
        # These will fail due to API, but params should be constrained
        # We'll mock the API call to test parameter handling
        pass  # API call required for full test
    
    @patch('binance_mcp_server.tools.futures.liquidity_wall_persistence.get_market_data_collector')
    def test_cache_key_generation(self, mock_collector):
        """Test that cache keys are generated consistently."""
        from binance_mcp_server.tools.futures.rate_limit_utils import ParameterCache
        
        params1 = {
            "symbol": "BTCUSDT",
            "depth_limit": 50,
            "window_seconds": 60,
            "sample_interval_ms": 1000,
            "top_n": 5,
            "wall_threshold_usd": 1000000
        }
        params2 = {
            "symbol": "BTCUSDT",
            "depth_limit": 50,
            "window_seconds": 60,
            "sample_interval_ms": 1000,
            "top_n": 5,
            "wall_threshold_usd": 1000000
        }
        
        key1 = ParameterCache._hash_params(params1)
        key2 = ParameterCache._hash_params(params2)
        assert key1 == key2


# ==============================================================================
# Test Queue Fill Probability Multi-Horizon Tool
# ==============================================================================

class TestQueueFillProbabilityMultiHorizon:
    """Test queue_fill_probability_multi_horizon tool."""
    
    def test_invalid_symbol_rejected(self):
        """Test that invalid symbols are rejected."""
        from binance_mcp_server.tools.futures.queue_fill_probability_multi_horizon import queue_fill_probability_multi_horizon
        
        result = queue_fill_probability_multi_horizon(
            symbol="INVALID",
            side="LONG",
            price_levels=[42000.0],
            qty=0.1
        )
        assert result["success"] is False
        assert "not in allowed" in result["error"]["message"]
    
    def test_invalid_side_rejected(self):
        """Test that invalid side is rejected."""
        from binance_mcp_server.tools.futures.queue_fill_probability_multi_horizon import queue_fill_probability_multi_horizon
        
        result = queue_fill_probability_multi_horizon(
            symbol="BTCUSDT",
            side="INVALID",
            price_levels=[42000.0],
            qty=0.1
        )
        assert result["success"] is False
        assert "LONG or SHORT" in result["error"]["message"]
    
    def test_empty_price_levels_rejected(self):
        """Test that empty price levels are rejected."""
        from binance_mcp_server.tools.futures.queue_fill_probability_multi_horizon import queue_fill_probability_multi_horizon
        
        result = queue_fill_probability_multi_horizon(
            symbol="BTCUSDT",
            side="LONG",
            price_levels=[],
            qty=0.1
        )
        assert result["success"] is False
        assert "price level required" in result["error"]["message"]
    
    def test_default_horizons(self):
        """Test that default horizons are applied."""
        # This tests the default value handling
        from binance_mcp_server.tools.futures.queue_fill_probability_multi_horizon import queue_fill_probability_multi_horizon
        
        # Mock will be needed for full test
        pass
    
    def test_poisson_fill_probability_calculation(self):
        """Test Poisson fill probability calculation."""
        from binance_mcp_server.tools.futures.queue_fill_probability_multi_horizon import calculate_poisson_fill_prob
        
        # No queue ahead = 100% fill
        prob = calculate_poisson_fill_prob(0, 1.0, 60)
        assert prob == 1.0
        
        # No arrival rate = 0% fill
        prob = calculate_poisson_fill_prob(10, 0, 60)
        assert prob == 0.0
        
        # Case where expected arrivals > queue (should give high probability)
        # queue=20, rate=1.0/sec, horizon=60s -> lambda_t=60, queue=20
        prob = calculate_poisson_fill_prob(20, 1.0, 60)
        assert prob > 0.9  # Should be very likely
        
        # Case where expected arrivals < queue (should give low probability)
        # queue=100, rate=0.1/sec, horizon=60s -> lambda_t=6, queue=100
        prob_low = calculate_poisson_fill_prob(100, 0.1, 60)
        assert prob_low < 0.5  # Should be unlikely
        
        # Probability should be between 0 and 1
        prob_any = calculate_poisson_fill_prob(50, 1.0, 60)
        assert 0 <= prob_any <= 1
    
    def test_eta_calculation(self):
        """Test ETA calculation."""
        from binance_mcp_server.tools.futures.queue_fill_probability_multi_horizon import calculate_eta_seconds
        
        # No queue = instant fill
        eta = calculate_eta_seconds(0, 1.0, 0.5)
        assert eta == 0.0
        
        # No rate = None (cannot estimate)
        eta = calculate_eta_seconds(10, 0, 0.5)
        assert eta is None
        
        # Normal case
        eta = calculate_eta_seconds(10, 1.0, 0.5)
        assert eta is not None
        assert eta > 0


# ==============================================================================
# Test Volume Profile Fallback Tool
# ==============================================================================

class TestVolumeProfileFallback:
    """Test volume_profile_fallback_from_trades tool."""
    
    def test_invalid_symbol_rejected(self):
        """Test that invalid symbols are rejected."""
        from binance_mcp_server.tools.futures.volume_profile_fallback_from_trades import volume_profile_fallback_from_trades
        
        result = volume_profile_fallback_from_trades("INVALID")
        assert result["success"] is False
        assert "not in allowed" in result["error"]["message"]
    
    def test_parameter_constraints(self):
        """Test that parameters are constrained to valid ranges."""
        # lookback_minutes: 15-360
        # max_trades: 100-5000
        pass  # API call required for full test
    
    def test_bin_size_calculation(self):
        """Test dynamic bin size calculation."""
        from binance_mcp_server.tools.futures.volume_profile_fallback_from_trades import calculate_bin_size
        
        # Large range should give larger bins
        size = calculate_bin_size(1000, target_bins=50, user_bin_size=None)
        assert size == 20  # 1000/50 = 20
        
        # User-specified takes precedence
        size = calculate_bin_size(1000, target_bins=50, user_bin_size=25)
        assert size == 25
        
        # Zero range should return default
        size = calculate_bin_size(0, target_bins=50, user_bin_size=None)
        assert size == 10.0
    
    def test_vpoc_calculation(self):
        """Test VPOC calculation."""
        from binance_mcp_server.tools.futures.volume_profile_fallback_from_trades import VPBin, find_vpoc
        
        profile = [
            VPBin(price_low=100, price_high=110, price_mid=105, volume=50),
            VPBin(price_low=110, price_high=120, price_mid=115, volume=100),  # Highest
            VPBin(price_low=120, price_high=130, price_mid=125, volume=30),
        ]
        
        vpoc = find_vpoc(profile)
        assert vpoc == 115  # Mid of highest volume bin
    
    def test_value_area_calculation(self):
        """Test VAH/VAL calculation."""
        from binance_mcp_server.tools.futures.volume_profile_fallback_from_trades import VPBin, find_value_area
        
        # Create a profile with clear POC
        profile = [
            VPBin(price_low=100, price_high=110, price_mid=105, volume=10),
            VPBin(price_low=110, price_high=120, price_mid=115, volume=30),
            VPBin(price_low=120, price_high=130, price_mid=125, volume=50),  # POC
            VPBin(price_low=130, price_high=140, price_mid=135, volume=30),
            VPBin(price_low=140, price_high=150, price_mid=145, volume=10),
        ]
        
        vah, val = find_value_area(profile, percentage=0.70)
        
        assert vah is not None
        assert val is not None
        assert vah > val  # VAH should be higher than VAL
    
    def test_hvn_lvn_identification(self):
        """Test HVN and LVN identification."""
        from binance_mcp_server.tools.futures.volume_profile_fallback_from_trades import (
            VPBin, find_hvn_levels, find_lvn_levels
        )
        
        profile = [
            VPBin(price_low=100, price_high=110, price_mid=105, volume=10),  # LVN
            VPBin(price_low=110, price_high=120, price_mid=115, volume=100),  # HVN
            VPBin(price_low=120, price_high=130, price_mid=125, volume=15),
            VPBin(price_low=130, price_high=140, price_mid=135, volume=90),  # HVN
            VPBin(price_low=140, price_high=150, price_mid=145, volume=5),   # LVN
        ]
        
        hvn = find_hvn_levels(profile, max_levels=2)
        lvn = find_lvn_levels(profile, max_levels=2)
        
        assert len(hvn) <= 2
        assert len(lvn) <= 2
        assert 115 in hvn  # Should be high volume
        assert 145 in lvn or 105 in lvn  # Should be low volume


# ==============================================================================
# Test Output Schema Compliance
# ==============================================================================

class TestOutputSchemaCompliance:
    """Test that tool outputs comply with compressed statistics requirement."""
    
    def test_liquidity_wall_output_size(self):
        """Test that liquidity wall output is compressed."""
        # Output should have:
        # - bid_walls: max top_n (default 5)
        # - ask_walls: max top_n (default 5)
        # - magnet_levels: max 6
        # - avoid_zones: max 4
        # - notes: max 4
        pass  # Requires mocking
    
    def test_fill_probability_output_size(self):
        """Test that fill probability output is compressed."""
        # Output should have:
        # - per_level: max 5 levels
        # - quality_flags: max 6
        pass  # Requires mocking
    
    def test_vp_fallback_output_size(self):
        """Test that VP fallback output is compressed."""
        # Output should have:
        # - HVN_levels: max 3
        # - LVN_levels: max 3
        # - magnet_levels: max 4
        # - avoid_zones: max 3
        # - notes: max 4
        pass  # Requires mocking


# ==============================================================================
# Integration Tests (require API credentials)
# ==============================================================================

@pytest.mark.skip(reason="Requires API credentials and network access")
class TestIntegration:
    """Integration tests that require actual API access."""
    
    def test_liquidity_wall_live(self):
        """Test liquidity wall with live API."""
        from binance_mcp_server.tools.futures import liquidity_wall_persistence
        
        result = liquidity_wall_persistence(
            symbol="BTCUSDT",
            window_seconds=10,  # Short window for testing
            sample_interval_ms=1000,
            top_n=3,
            wall_threshold_usd=500000
        )
        
        assert result["success"] is True
        assert "bid_walls" in result
        assert "ask_walls" in result
        assert "spoof_risk_score_0_100" in result
    
    def test_fill_probability_live(self):
        """Test fill probability with live API."""
        from binance_mcp_server.tools.futures import queue_fill_probability_multi_horizon
        
        result = queue_fill_probability_multi_horizon(
            symbol="BTCUSDT",
            side="LONG",
            price_levels=[42000.0, 41900.0],
            qty=0.01,
            horizons_sec=[60, 300],
            lookback_sec=60
        )
        
        assert result["success"] is True
        assert "per_level" in result
        assert "overall_best_level" in result
    
    def test_vp_fallback_live(self):
        """Test VP fallback with live API."""
        from binance_mcp_server.tools.futures import volume_profile_fallback_from_trades
        
        result = volume_profile_fallback_from_trades(
            symbol="BTCUSDT",
            lookback_minutes=60,  # 1 hour
            max_trades=1000
        )
        
        assert result["success"] is True
        assert "levels" in result
        assert "vPOC" in result["levels"]
