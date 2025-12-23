#!/usr/bin/env python3
"""
Demo script for Advanced Limit Order Analysis MCP Tools.

This script demonstrates the three new tools:
1. liquidity_wall_persistence - Track order book walls and detect spoofing
2. queue_fill_probability_multi_horizon - Multi-horizon fill probability estimation
3. volume_profile_fallback_from_trades - VP analysis from trade data

Usage:
    # Set environment variables first
    export BINANCE_API_KEY="your_api_key"
    export BINANCE_API_SECRET="your_api_secret"
    export BINANCE_TESTNET="true"  # Recommended for testing
    
    # Run the demo
    python scripts/demo_advanced_tools.py
    
    # Or run specific demo
    python scripts/demo_advanced_tools.py --tool wall_persistence
    python scripts/demo_advanced_tools.py --tool fill_probability
    python scripts/demo_advanced_tools.py --tool vp_fallback
    python scripts/demo_advanced_tools.py --tool all
"""

import os
import sys
import json
import argparse
import time
from typing import Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pretty_print(title: str, data: Dict[str, Any]) -> None:
    """Pretty print JSON result."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)
    print(json.dumps(data, indent=2, default=str))
    print()


def demo_liquidity_wall_persistence() -> Dict[str, Any]:
    """
    Demo: Liquidity Wall Persistence / Spoof Filter
    
    This tool samples the orderbook over time to:
    - Track persistent bid/ask walls (true liquidity)
    - Detect spoofing patterns (walls that appear/disappear)
    - Identify magnet levels (high persistence walls)
    - Flag avoid zones (areas with high spoof risk)
    
    Features:
    - 60-second cache for identical parameters
    - Exponential backoff on rate limits
    """
    print("\n" + "="*60)
    print("  DEMO: Liquidity Wall Persistence")
    print("="*60)
    
    from binance_mcp_server.tools.futures import liquidity_wall_persistence
    
    print("\nCalling liquidity_wall_persistence with:")
    print("  symbol: BTCUSDT")
    print("  depth_limit: 50")
    print("  window_seconds: 15 (short for demo)")
    print("  sample_interval_ms: 2000")
    print("  top_n: 3")
    print("  wall_threshold_usd: 500,000")
    print("\nSampling orderbook... (this takes ~15 seconds)")
    
    start = time.time()
    result = liquidity_wall_persistence(
        symbol="BTCUSDT",
        depth_limit=50,
        window_seconds=15,  # Short window for demo
        sample_interval_ms=2000,
        top_n=3,
        wall_threshold_usd=500000  # $500K threshold for demo
    )
    elapsed = time.time() - start
    
    print(f"\nCompleted in {elapsed:.1f} seconds")
    
    if result.get("success"):
        print("\n✅ SUCCESS")
        print(f"\n📊 Results:")
        print(f"  - Samples taken: {result.get('sampling', {}).get('samples_taken', 0)}")
        print(f"  - Bid walls found: {len(result.get('bid_walls', []))}")
        print(f"  - Ask walls found: {len(result.get('ask_walls', []))}")
        print(f"  - Spoof risk score: {result.get('spoof_risk_score_0_100', 0)}/100")
        print(f"  - Magnet levels: {result.get('magnet_levels', [])}")
        print(f"  - Avoid zones: {len(result.get('avoid_zones', []))}")
        print(f"  - Cache hit: {result.get('_cache_hit', False)}")
        
        if result.get("bid_walls"):
            print(f"\n📈 Top Bid Wall:")
            wall = result["bid_walls"][0]
            print(f"    Price: ${wall['price']:,.2f}")
            print(f"    Notional: ${wall['notional_usd']:,.0f}")
            print(f"    Persistence: {wall['persistence_score_0_100']}/100")
        
        if result.get("notes"):
            print(f"\n📝 Notes: {', '.join(result['notes'])}")
    else:
        print("\n❌ FAILED")
        print(f"  Error: {result.get('error', {}).get('message', 'Unknown')}")
    
    return result


def demo_queue_fill_probability() -> Dict[str, Any]:
    """
    Demo: Queue Fill Probability Multi-Horizon
    
    This tool estimates fill probability across multiple time horizons:
    - Uses Poisson process model for fill estimation
    - Calculates adverse selection risk
    - Supports multiple price levels
    - Configurable queue position assumptions
    
    Features:
    - 30-second cache for identical parameters
    - Exponential backoff on rate limits
    """
    print("\n" + "="*60)
    print("  DEMO: Queue Fill Probability Multi-Horizon")
    print("="*60)
    
    from binance_mcp_server.tools.futures import queue_fill_probability_multi_horizon
    
    # Get current price for realistic levels
    from binance_mcp_server.tools.futures.market_data_collector import get_market_data_collector
    collector = get_market_data_collector()
    _, mark_data, _ = collector.fetch_mark_price("BTCUSDT")
    
    if mark_data:
        current_price = mark_data.mark_price
        # Set price levels around current price
        price_levels = [
            round(current_price * 0.998, 1),  # 0.2% below
            round(current_price * 0.996, 1),  # 0.4% below
            round(current_price * 0.994, 1),  # 0.6% below
        ]
    else:
        price_levels = [42000.0, 41900.0, 41800.0]
    
    print("\nCalling queue_fill_probability_multi_horizon with:")
    print("  symbol: BTCUSDT")
    print("  side: LONG (buy limit order)")
    print(f"  price_levels: {price_levels}")
    print("  qty: 0.01 BTC")
    print("  horizons_sec: [60, 300, 900] (1min, 5min, 15min)")
    print("  lookback_sec: 120")
    print("  assume_queue_position: mid")
    
    start = time.time()
    result = queue_fill_probability_multi_horizon(
        symbol="BTCUSDT",
        side="LONG",
        price_levels=price_levels,
        qty=0.01,
        horizons_sec=[60, 300, 900],
        lookback_sec=120,
        assume_queue_position="mid"
    )
    elapsed = time.time() - start
    
    print(f"\nCompleted in {elapsed:.1f} seconds")
    
    if result.get("success"):
        print("\n✅ SUCCESS")
        print(f"\n📊 Results:")
        print(f"  - Best level: ${result.get('overall_best_level', 0):,.2f}")
        print(f"  - Confidence: {result.get('confidence_0_1', 0):.0%}")
        print(f"  - Cache hit: {result.get('_cache_hit', False)}")
        
        if result.get("per_level"):
            print("\n📈 Fill Probabilities by Level:")
            for level in result["per_level"]:
                print(f"\n  Price: ${level['price']:,.2f}")
                fill_probs = level.get('fill_prob', {})
                for horizon, prob in fill_probs.items():
                    print(f"    {horizon}s horizon: {prob:.1%}")
                print(f"    ETA (P50): {level.get('eta_sec_p50', 'N/A')}s")
                print(f"    Adverse selection: {level.get('adverse_selection_score_0_100', 0)}/100")
        
        if result.get("quality_flags"):
            print(f"\n⚠️ Quality flags: {result['quality_flags']}")
    else:
        print("\n❌ FAILED")
        print(f"  Error: {result.get('error', {}).get('message', 'Unknown')}")
    
    return result


def demo_volume_profile_fallback() -> Dict[str, Any]:
    """
    Demo: Volume Profile Fallback from Trades
    
    This tool calculates VP levels from raw trade data when the main
    volume_profile_levels tool is rate-limited or unavailable:
    - vPOC (Volume Point of Control)
    - VAH/VAL (Value Area at 70%)
    - HVN/LVN nodes
    - Magnet levels and avoid zones
    
    Features:
    - 45-second cache for identical parameters
    - Exponential backoff on rate limits
    """
    print("\n" + "="*60)
    print("  DEMO: Volume Profile Fallback from Trades")
    print("="*60)
    
    from binance_mcp_server.tools.futures import volume_profile_fallback_from_trades
    
    print("\nCalling volume_profile_fallback_from_trades with:")
    print("  symbol: BTCUSDT")
    print("  lookback_minutes: 60 (1 hour)")
    print("  bin_size: 25 (auto)")
    print("  max_trades: 2000")
    
    start = time.time()
    result = volume_profile_fallback_from_trades(
        symbol="BTCUSDT",
        lookback_minutes=60,  # 1 hour
        bin_size=None,  # Auto-calculate
        max_trades=2000
    )
    elapsed = time.time() - start
    
    print(f"\nCompleted in {elapsed:.1f} seconds")
    
    if result.get("success"):
        print("\n✅ SUCCESS")
        
        data_quality = result.get("data_quality", {})
        levels = result.get("levels", {})
        
        print(f"\n📊 Data Quality:")
        print(f"  - Trade count: {data_quality.get('trade_count', 0):,}")
        print(f"  - Actual window: {data_quality.get('actual_minutes', 0):.1f} min")
        print(f"  - Bin count: {data_quality.get('bin_count', 0)}")
        price_range = data_quality.get("price_range", {})
        print(f"  - Price range: ${price_range.get('low', 0):,.2f} - ${price_range.get('high', 0):,.2f}")
        
        print(f"\n📈 Key Levels:")
        print(f"  - vPOC: ${levels.get('vPOC', 0):,.2f}")
        print(f"  - VAH: ${levels.get('VAH', 0):,.2f}")
        print(f"  - VAL: ${levels.get('VAL', 0):,.2f}")
        print(f"  - HVN levels: {levels.get('HVN_levels', [])}")
        print(f"  - LVN levels: {levels.get('LVN_levels', [])}")
        print(f"  - Magnet levels: {levels.get('magnet_levels', [])}")
        
        print(f"\n  Confidence: {result.get('confidence_0_1', 0):.0%}")
        print(f"  Cache hit: {result.get('_cache_hit', False)}")
        
        if result.get("notes"):
            print(f"\n📝 Notes: {', '.join(result['notes'])}")
        
        if result.get("quality_flags"):
            print(f"\n⚠️ Quality flags: {result['quality_flags']}")
    else:
        print("\n❌ FAILED")
        print(f"  Error: {result.get('error', {}).get('message', 'Unknown')}")
    
    return result


def demo_cache_behavior() -> None:
    """Demo cache behavior by calling same tool twice."""
    print("\n" + "="*60)
    print("  DEMO: Cache Behavior")
    print("="*60)
    
    from binance_mcp_server.tools.futures import volume_profile_fallback_from_trades
    
    print("\nFirst call (should miss cache)...")
    start1 = time.time()
    result1 = volume_profile_fallback_from_trades(
        symbol="BTCUSDT",
        lookback_minutes=30,
        max_trades=500
    )
    elapsed1 = time.time() - start1
    
    print(f"  Time: {elapsed1:.2f}s")
    print(f"  Cache hit: {result1.get('_cache_hit', False)}")
    
    print("\nSecond call (should hit cache)...")
    start2 = time.time()
    result2 = volume_profile_fallback_from_trades(
        symbol="BTCUSDT",
        lookback_minutes=30,
        max_trades=500
    )
    elapsed2 = time.time() - start2
    
    print(f"  Time: {elapsed2:.2f}s")
    print(f"  Cache hit: {result2.get('_cache_hit', False)}")
    
    if elapsed2 < elapsed1 * 0.1:  # Cache should be 10x faster
        print("\n✅ Cache working correctly!")
    else:
        print("\n⚠️ Cache may not be working as expected")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Demo Advanced Limit Order Tools")
    parser.add_argument(
        "--tool",
        choices=["wall_persistence", "fill_probability", "vp_fallback", "cache", "all"],
        default="all",
        help="Which tool(s) to demo"
    )
    args = parser.parse_args()
    
    # Check environment
    if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
        print("❌ Error: BINANCE_API_KEY and BINANCE_API_SECRET environment variables required")
        print("\nUsage:")
        print("  export BINANCE_API_KEY='your_api_key'")
        print("  export BINANCE_API_SECRET='your_api_secret'")
        print("  export BINANCE_TESTNET='true'  # Recommended for testing")
        print("  python scripts/demo_advanced_tools.py")
        sys.exit(1)
    
    print("\n" + "="*60)
    print("  BINANCE MCP SERVER - ADVANCED LIMIT ORDER TOOLS DEMO")
    print("="*60)
    print(f"\nTestnet mode: {os.getenv('BINANCE_TESTNET', 'false')}")
    print(f"Tool: {args.tool}")
    
    results = {}
    
    try:
        if args.tool in ("wall_persistence", "all"):
            results["wall_persistence"] = demo_liquidity_wall_persistence()
        
        if args.tool in ("fill_probability", "all"):
            results["fill_probability"] = demo_queue_fill_probability()
        
        if args.tool in ("vp_fallback", "all"):
            results["vp_fallback"] = demo_volume_profile_fallback()
        
        if args.tool in ("cache", "all"):
            demo_cache_behavior()
        
        print("\n" + "="*60)
        print("  DEMO COMPLETE")
        print("="*60)
        
        # Summary
        success_count = sum(1 for r in results.values() if r.get("success"))
        print(f"\nSuccessful: {success_count}/{len(results)}")
        
    except Exception as e:
        print(f"\n❌ Demo failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
