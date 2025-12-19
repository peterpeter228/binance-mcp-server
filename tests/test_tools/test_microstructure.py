"""
Unit tests for microstructure analysis calculations.

Tests core calculation functions for OBI, spread, persistence, slippage,
and health scoring.
"""

import pytest
import math
from binance_mcp_server.tools.microstructure.calculations import (
    calculate_spread_points,
    calculate_spread_bps,
    calculate_obi,
    calculate_obi_stats,
    calculate_depth_at_bps,
    calculate_depth_summary,
    identify_walls,
    calculate_persistence_score,
    calculate_taker_imbalance,
    estimate_slippage,
    calculate_micro_health_score,
    calculate_wall_risk_level,
    calculate_realized_volatility
)


class TestSpreadCalculations:
    """Tests for spread calculations."""
    
    def test_spread_points_normal(self):
        """Test spread calculation in price points."""
        best_bid = 100000.0
        best_ask = 100010.0
        
        spread = calculate_spread_points(best_bid, best_ask)
        
        assert spread == 10.0
    
    def test_spread_points_zero_spread(self):
        """Test spread when bid equals ask (crossed book)."""
        best_bid = 50000.0
        best_ask = 50000.0
        
        spread = calculate_spread_points(best_bid, best_ask)
        
        assert spread == 0.0
    
    def test_spread_bps_normal(self):
        """Test spread calculation in basis points."""
        # 1 basis point = 0.01%
        # If mid = 50000, spread = 5 => bps = (5/50000)*10000 = 1 bps
        best_bid = 49997.5
        best_ask = 50002.5
        
        spread_bps = calculate_spread_bps(best_bid, best_ask)
        
        # mid = 50000, spread = 5, bps = 5/50000 * 10000 = 1.0
        assert spread_bps == 1.0
    
    def test_spread_bps_wide_spread(self):
        """Test spread calculation with wider spread."""
        best_bid = 49975.0
        best_ask = 50025.0
        
        spread_bps = calculate_spread_bps(best_bid, best_ask)
        
        # mid = 50000, spread = 50, bps = 50/50000 * 10000 = 10.0
        assert spread_bps == 10.0
    
    def test_spread_bps_zero_bid(self):
        """Test spread with zero bid price."""
        spread_bps = calculate_spread_bps(0.0, 100.0)
        assert spread_bps == 0.0


class TestOBICalculations:
    """Tests for Order Book Imbalance calculations."""
    
    def test_obi_balanced(self):
        """Test OBI with balanced orderbook."""
        bids = [
            {"price": 100.0, "quantity": 10.0},
            {"price": 99.0, "quantity": 10.0}
        ]
        asks = [
            {"price": 101.0, "quantity": 10.0},
            {"price": 102.0, "quantity": 10.0}
        ]
        
        obi = calculate_obi(bids, asks, levels=2)
        
        # bid_qty = 20, ask_qty = 20, OBI = 0
        assert obi == 0.0
    
    def test_obi_bid_heavy(self):
        """Test OBI with bid-heavy orderbook."""
        bids = [
            {"price": 100.0, "quantity": 30.0},
            {"price": 99.0, "quantity": 30.0}
        ]
        asks = [
            {"price": 101.0, "quantity": 10.0},
            {"price": 102.0, "quantity": 10.0}
        ]
        
        obi = calculate_obi(bids, asks, levels=2)
        
        # bid_qty = 60, ask_qty = 20
        # OBI = (60 - 20) / (60 + 20) = 40/80 = 0.5
        assert obi == 0.5
    
    def test_obi_ask_heavy(self):
        """Test OBI with ask-heavy orderbook."""
        bids = [
            {"price": 100.0, "quantity": 10.0}
        ]
        asks = [
            {"price": 101.0, "quantity": 30.0}
        ]
        
        obi = calculate_obi(bids, asks, levels=1)
        
        # OBI = (10 - 30) / (10 + 30) = -20/40 = -0.5
        assert obi == -0.5
    
    def test_obi_empty_book(self):
        """Test OBI with empty orderbook."""
        obi = calculate_obi([], [], levels=10)
        assert obi == 0.0
    
    def test_obi_respects_levels(self):
        """Test OBI only considers specified number of levels."""
        bids = [
            {"price": 100.0, "quantity": 10.0},
            {"price": 99.0, "quantity": 100.0}  # Should be ignored with levels=1
        ]
        asks = [
            {"price": 101.0, "quantity": 10.0},
            {"price": 102.0, "quantity": 100.0}  # Should be ignored with levels=1
        ]
        
        obi = calculate_obi(bids, asks, levels=1)
        
        # Only first level: bid_qty = 10, ask_qty = 10
        assert obi == 0.0
    
    def test_obi_stats_multiple_snapshots(self):
        """Test OBI statistics across multiple snapshots."""
        obi_values = [0.1, 0.2, 0.3]
        
        stats = calculate_obi_stats(obi_values)
        
        assert stats["snapshots"] == [0.1, 0.2, 0.3]
        assert stats["mean"] == 0.2
        assert stats["stdev"] == pytest.approx(0.1, abs=0.01)
    
    def test_obi_stats_single_snapshot(self):
        """Test OBI statistics with single snapshot."""
        obi_values = [0.5]
        
        stats = calculate_obi_stats(obi_values)
        
        assert stats["mean"] == 0.5
        assert stats["stdev"] == 0.0
    
    def test_obi_stats_empty(self):
        """Test OBI statistics with empty input."""
        stats = calculate_obi_stats([])
        
        assert stats["mean"] == 0.0
        assert stats["stdev"] == 0.0


class TestPersistenceScore:
    """Tests for wall persistence scoring."""
    
    def test_persistence_full(self):
        """Test full persistence when wall exists in all snapshots."""
        current_walls = [{"price": 50000.0, "qty": 100.0, "size_ratio_vs_median": 5.0}]
        previous_walls = [
            [{"price": 50000.0, "qty": 100.0}],
            [{"price": 49999.0, "qty": 100.0}],  # Within tolerance
            [{"price": 50001.0, "qty": 100.0}]   # Within tolerance
        ]
        
        result = calculate_persistence_score(current_walls, previous_walls, tolerance_pct=0.5)
        
        assert result[0]["persistence_score"] == 1.0
    
    def test_persistence_partial(self):
        """Test partial persistence when wall exists in some snapshots."""
        current_walls = [{"price": 50000.0, "qty": 100.0, "size_ratio_vs_median": 5.0}]
        previous_walls = [
            [{"price": 50000.0, "qty": 100.0}],  # Match
            [{"price": 45000.0, "qty": 100.0}],  # No match
        ]
        
        result = calculate_persistence_score(current_walls, previous_walls, tolerance_pct=0.5)
        
        assert result[0]["persistence_score"] == 0.5
    
    def test_persistence_zero(self):
        """Test zero persistence when wall is new."""
        current_walls = [{"price": 50000.0, "qty": 100.0, "size_ratio_vs_median": 5.0}]
        previous_walls = [
            [{"price": 40000.0, "qty": 100.0}],
            [{"price": 60000.0, "qty": 100.0}],
        ]
        
        result = calculate_persistence_score(current_walls, previous_walls, tolerance_pct=0.5)
        
        assert result[0]["persistence_score"] == 0.0
    
    def test_persistence_no_history(self):
        """Test persistence with no previous data (default to 1.0)."""
        current_walls = [{"price": 50000.0, "qty": 100.0}]
        
        result = calculate_persistence_score(current_walls, [], tolerance_pct=0.5)
        
        assert result[0]["persistence_score"] == 1.0


class TestWallIdentification:
    """Tests for wall identification."""
    
    def test_identify_walls_top3(self):
        """Test identifying top 3 walls by quantity."""
        orders = [
            {"price": 100.0, "quantity": 50.0},
            {"price": 99.0, "quantity": 100.0},
            {"price": 98.0, "quantity": 30.0},
            {"price": 97.0, "quantity": 200.0},
            {"price": 96.0, "quantity": 10.0}
        ]
        
        walls = identify_walls(orders, top_n=3)
        
        assert len(walls) == 3
        assert walls[0]["qty"] == 200.0  # Largest
        assert walls[1]["qty"] == 100.0
        assert walls[2]["qty"] == 50.0
    
    def test_identify_walls_size_ratio(self):
        """Test wall size ratio vs median calculation."""
        # Quantities: 10, 20, 30 -> median = 20
        orders = [
            {"price": 100.0, "quantity": 10.0},
            {"price": 99.0, "quantity": 20.0},
            {"price": 98.0, "quantity": 30.0}
        ]
        
        walls = identify_walls(orders, top_n=1)
        
        # Top wall qty = 30, median = 20, ratio = 30/20 = 1.5
        assert walls[0]["size_ratio_vs_median"] == 1.5
    
    def test_identify_walls_empty(self):
        """Test wall identification with empty orders."""
        walls = identify_walls([], top_n=3)
        assert walls == []


class TestTakerImbalance:
    """Tests for taker imbalance calculations."""
    
    def test_taker_imbalance_balanced(self):
        """Test balanced taker flow."""
        trades = [
            {"qty": "10", "isBuyerMaker": True},   # Seller taker
            {"qty": "10", "isBuyerMaker": False},  # Buyer taker
        ]
        
        result = calculate_taker_imbalance(trades)
        
        assert result["buy_qty_sum"] == 10.0
        assert result["sell_qty_sum"] == 10.0
        assert result["taker_imbalance"] == 0.0
    
    def test_taker_imbalance_buy_heavy(self):
        """Test buy-heavy taker flow."""
        trades = [
            {"qty": "30", "isBuyerMaker": False},  # Buyer taker
            {"qty": "10", "isBuyerMaker": True},   # Seller taker
        ]
        
        result = calculate_taker_imbalance(trades)
        
        # imbalance = (30 - 10) / (30 + 10) = 0.5
        assert result["taker_imbalance"] == 0.5
    
    def test_taker_imbalance_empty(self):
        """Test taker imbalance with no trades."""
        result = calculate_taker_imbalance([])
        
        assert result["taker_imbalance"] == 0.0


class TestSlippageEstimation:
    """Tests for slippage estimation."""
    
    def test_slippage_deep_book(self):
        """Test slippage estimation with deep orderbook."""
        bids = [
            {"price": 99.9, "quantity": 100.0},
            {"price": 99.8, "quantity": 100.0},
            {"price": 99.7, "quantity": 100.0}
        ]
        asks = [
            {"price": 100.1, "quantity": 100.0},
            {"price": 100.2, "quantity": 100.0},
            {"price": 100.3, "quantity": 100.0}
        ]
        trades = [{"qty": "1"}]  # Small trades -> low slippage expected
        
        result = estimate_slippage(bids, asks, trades)
        
        # With deep book and small order size, slippage should be minimal
        assert result["p50_points"] < 1.0
        assert result["p95_points"] >= result["p50_points"]
    
    def test_slippage_empty_book(self):
        """Test slippage with empty orderbook."""
        result = estimate_slippage([], [], [])
        
        assert result["p50_points"] == 0.0
        assert result["p95_points"] == 0.0


class TestMicroHealthScore:
    """Tests for micro health score calculation."""
    
    def test_health_excellent(self):
        """Test health score for excellent market conditions."""
        score, notes = calculate_micro_health_score(
            spread_bps=0.5,        # Excellent spread
            obi_stdev=0.02,        # Very stable
            depth_10bps=1000.0,    # Deep book (will be * mid_price)
            taker_imbalance=0.05,  # Balanced flow
            wall_persistence_avg=0.9,  # Stable walls
            mid_price=50000.0      # For notional calculation
        )
        
        # Should be a high score
        assert score >= 80
    
    def test_health_poor(self):
        """Test health score for poor market conditions."""
        score, notes = calculate_micro_health_score(
            spread_bps=15.0,       # Wide spread
            obi_stdev=0.3,         # Volatile OBI
            depth_10bps=0.5,       # Thin depth
            taker_imbalance=0.7,   # Imbalanced flow
            wall_persistence_avg=0.2,  # Unstable walls
            mid_price=50000.0
        )
        
        # Should be a low score
        assert score < 50
        assert len(notes) > 0  # Should have explanatory notes
    
    def test_health_notes_content(self):
        """Test that health notes contain meaningful information."""
        score, notes = calculate_micro_health_score(
            spread_bps=8.0,        # Wide spread
            obi_stdev=0.25,        # Volatile
            depth_10bps=0.1,       # Thin
            taker_imbalance=0.6,   # Imbalanced
            wall_persistence_avg=0.3,
            mid_price=50000.0
        )
        
        # Should mention issues
        notes_text = " ".join(notes)
        assert "spread" in notes_text.lower() or "depth" in notes_text.lower() or "flow" in notes_text.lower()


class TestWallRiskLevel:
    """Tests for wall risk level assessment."""
    
    def test_risk_low(self):
        """Test low risk with normal conditions."""
        walls_bid = [{"price": 49000.0, "size_ratio_vs_median": 2.0, "persistence_score": 0.8}]
        walls_ask = [{"price": 51000.0, "size_ratio_vs_median": 2.0, "persistence_score": 0.8}]
        
        risk = calculate_wall_risk_level(walls_bid, walls_ask, mid_price=50000.0, obi_stdev=0.05)
        
        assert risk == "low"
    
    def test_risk_high_large_walls(self):
        """Test high risk with very large walls."""
        walls_bid = [{"price": 49990.0, "size_ratio_vs_median": 15.0, "persistence_score": 0.2}]
        walls_ask = [{"price": 50010.0, "size_ratio_vs_median": 15.0, "persistence_score": 0.2}]
        
        risk = calculate_wall_risk_level(walls_bid, walls_ask, mid_price=50000.0, obi_stdev=0.25)
        
        assert risk == "high"
    
    def test_risk_empty_walls(self):
        """Test risk with no walls."""
        risk = calculate_wall_risk_level([], [], mid_price=50000.0, obi_stdev=0.1)
        assert risk == "low"


class TestRealizedVolatility:
    """Tests for realized volatility calculations."""
    
    def test_rv_stable_prices(self):
        """Test RV with stable prices."""
        # Very small oscillations around 100
        closes = [100.0, 100.001, 99.999, 100.0, 100.001, 99.999] * 20
        
        result = calculate_realized_volatility(closes, interval_minutes=1)
        
        # Should be relatively low volatility (but annualization makes it larger)
        assert result["rv"] < 200  # Reasonable upper bound for "stable"
        assert result["confidence"] > 0.5
    
    def test_rv_volatile_prices(self):
        """Test RV with volatile prices."""
        # Alternating 1% moves -> high volatility
        closes = []
        price = 100.0
        for i in range(60):
            closes.append(price)
            price *= 1.01 if i % 2 == 0 else 0.99
        
        result = calculate_realized_volatility(closes, interval_minutes=1)
        
        # Should have meaningful volatility
        assert result["rv"] > 0
        assert result["expected_move_points"] > 0
    
    def test_rv_insufficient_data(self):
        """Test RV with insufficient data."""
        result = calculate_realized_volatility([100.0], interval_minutes=1)
        
        assert result["rv"] == 0.0
        assert result["confidence"] == 0.0
    
    def test_rv_expected_move_scaling(self):
        """Test that expected move is reasonably scaled."""
        closes = [100.0 + i * 0.1 for i in range(100)]
        
        result = calculate_realized_volatility(closes, interval_minutes=1)
        
        # Expected move should be in reasonable range relative to price
        assert result["expected_move_points"] >= 0
        assert result["expected_move_bps"] >= 0


class TestDepthCalculations:
    """Tests for depth-related calculations."""
    
    def test_depth_at_bps(self):
        """Test depth calculation within basis points."""
        mid_price = 50000.0
        # 10 bps = 0.1% = 50 points from mid
        
        bids = [
            {"price": 49980.0, "quantity": 10.0},  # Within 10 bps (40 points)
            {"price": 49940.0, "quantity": 20.0},  # Outside 10 bps
        ]
        asks = [
            {"price": 50020.0, "quantity": 15.0},  # Within 10 bps
            {"price": 50060.0, "quantity": 25.0},  # Outside 10 bps
        ]
        
        result = calculate_depth_at_bps(bids, asks, mid_price, bps=10)
        
        assert result["bid_depth"] == 10.0
        assert result["ask_depth"] == 15.0
        assert result["total"] == 25.0
    
    def test_depth_summary(self):
        """Test comprehensive depth summary."""
        bids = [
            {"price": 99.0, "quantity": 50.0},
            {"price": 98.0, "quantity": 60.0}
        ]
        asks = [
            {"price": 101.0, "quantity": 40.0},
            {"price": 102.0, "quantity": 70.0}
        ]
        
        result = calculate_depth_summary(bids, asks, mid_price=100.0, top_n=2)
        
        assert result["bid_qty_sum_topN"] == 110.0
        assert result["ask_qty_sum_topN"] == 110.0
        assert "depth_10bps" in result
        assert "depth_20bps" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
