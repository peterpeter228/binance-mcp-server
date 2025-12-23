#!/usr/bin/env python3
"""
Demo script for WebSocket-based Volume Profile Tool.

This script demonstrates:
1. Starting WebSocket connection to Binance Futures aggTrade stream
2. Collecting trades into a ring buffer
3. Calling the volume_profile_levels_futures_ws tool
4. Printing the volume profile results

Usage:
    # Set environment variables (optional, uses defaults)
    export BINANCE_TESTNET="false"  # Use production by default
    
    # Run the demo (waits 60 seconds for data collection)
    python scripts/demo_ws_volume_profile.py
    
    # Or specify wait time and symbol
    python scripts/demo_ws_volume_profile.py --symbol BTCUSDT --wait 120
    
    # Quick demo (30 seconds)
    python scripts/demo_ws_volume_profile.py --wait 30

Note:
    - The WebSocket needs time to collect trades before VP calculation works
    - For 240-minute window, you need buffer data spanning that time
    - Initial calls may return "insufficient_trade_data" error
"""

import os
import sys
import json
import argparse
import time
import signal

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully."""
    print("\n\n🛑 Demo interrupted by user")
    sys.exit(0)


def print_separator(title: str = ""):
    """Print a visual separator."""
    if title:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print('='*60)
    else:
        print('-'*60)


def print_json(data: dict, indent: int = 2):
    """Pretty print JSON data."""
    print(json.dumps(data, indent=indent, default=str))


def demo_ws_volume_profile(symbol: str = "BTCUSDT", wait_seconds: int = 60):
    """
    Demo the WebSocket-based Volume Profile tool.
    
    Args:
        symbol: Symbol to subscribe to
        wait_seconds: Seconds to wait for data collection
    """
    print_separator("BINANCE FUTURES WEBSOCKET VOLUME PROFILE DEMO")
    print(f"\nSymbol: {symbol}")
    print(f"Wait time: {wait_seconds} seconds")
    print(f"Testnet: {os.getenv('BINANCE_TESTNET', 'false')}")
    
    # Import the required modules
    from binance_mcp_server.tools.futures import (
        get_ws_trade_buffer_manager,
        volume_profile_levels_futures_ws,
        get_ws_buffer_status,
    )
    
    # Step 1: Start WebSocket and subscribe
    print_separator("Step 1: Starting WebSocket Connection")
    print(f"Subscribing to {symbol} aggTrade stream...")
    
    manager = get_ws_trade_buffer_manager()
    manager.subscribe(symbol)
    
    print("Waiting for WebSocket connection...")
    if manager.wait_for_connection(timeout=30.0):
        print("✅ WebSocket connected!")
    else:
        print("⚠️ WebSocket connection timeout, continuing anyway...")
    
    # Step 2: Wait for data collection
    print_separator(f"Step 2: Collecting Trade Data ({wait_seconds}s)")
    print("Trade data is being buffered in real-time...")
    print()
    
    start_time = time.time()
    last_count = 0
    
    while time.time() - start_time < wait_seconds:
        elapsed = time.time() - start_time
        remaining = wait_seconds - elapsed
        
        stats = manager.get_buffer_stats(symbol)
        trade_count = stats.get("trade_count", 0)
        trades_per_sec = (trade_count - last_count) if last_count > 0 else 0
        last_count = trade_count
        
        print(f"\r  ⏱️ {remaining:.0f}s remaining | "
              f"📊 Trades: {trade_count:,} | "
              f"📈 Rate: ~{trades_per_sec}/s | "
              f"🔗 Connected: {'✓' if stats.get('is_connected') else '✗'}",
              end='', flush=True)
        
        time.sleep(1)
    
    print("\n\n✅ Data collection complete!")
    
    # Step 3: Show buffer status
    print_separator("Step 3: Buffer Status")
    status = get_ws_buffer_status(symbol)
    print(f"WebSocket connected: {status.get('is_connected', False)}")
    print(f"Subscribed symbols: {status.get('subscribed_symbols', [])}")
    
    if "symbol_stats" in status:
        stats = status["symbol_stats"]
        print(f"\n{symbol} Buffer Stats:")
        print(f"  Trade count: {stats.get('trade_count', 0):,}")
        print(f"  Buffer duration: {stats.get('buffer_duration_minutes', 0):.1f} minutes")
        if stats.get("oldest_trade_ms"):
            oldest = time.strftime('%H:%M:%S', time.localtime(stats["oldest_trade_ms"]/1000))
            newest = time.strftime('%H:%M:%S', time.localtime(stats["newest_trade_ms"]/1000))
            print(f"  Time range: {oldest} - {newest}")
    
    # Step 4: Calculate Volume Profile
    print_separator("Step 4: Calculate Volume Profile")
    print(f"Calling volume_profile_levels_futures_ws...")
    print(f"  symbol: {symbol}")
    print(f"  window_minutes: 30 (using available data)")
    print(f"  bin_size: auto")
    
    start = time.time()
    result = volume_profile_levels_futures_ws(
        symbol=symbol,
        window_minutes=30,  # Use 30 minutes for demo
        bin_size=None
    )
    elapsed = time.time() - start
    
    print(f"\n⏱️ Calculation took {elapsed:.3f} seconds")
    
    # Step 5: Display results
    print_separator("Step 5: Results")
    
    if result.get("success"):
        print("✅ SUCCESS\n")
        
        # Window info
        window = result.get("window", {})
        print("📊 Window Statistics:")
        print(f"  Requested: {window.get('requested_minutes', 0)} minutes")
        print(f"  Actual: {window.get('actual_minutes', 0):.1f} minutes")
        print(f"  Trade count: {window.get('trade_count', 0):,}")
        print(f"  Bin size: ${window.get('bin_size', 0)}")
        
        price_range = window.get("price_range", {})
        if price_range:
            print(f"  Price range: ${price_range.get('low', 0):,.2f} - ${price_range.get('high', 0):,.2f}")
        
        # Key levels
        levels = result.get("levels", {})
        print("\n🎯 Key Levels:")
        print(f"  vPOC (tPOC): ${levels.get('vpoc', 0):,.2f}")
        print(f"  VAH: ${levels.get('vah', 0):,.2f}")
        print(f"  VAL: ${levels.get('val', 0):,.2f}")
        
        if levels.get("hvn"):
            print(f"\n📈 HVN (High Volume Nodes):")
            for price in levels["hvn"]:
                print(f"    ${price:,.2f}")
        
        if levels.get("lvn"):
            print(f"\n📉 LVN (Low Volume Nodes):")
            for price in levels["lvn"]:
                print(f"    ${price:,.2f}")
        
        if levels.get("magnet_levels"):
            print(f"\n🧲 Magnet Levels:")
            for price in levels["magnet_levels"]:
                print(f"    ${price:,.2f}")
        
        if levels.get("avoid_zones"):
            print(f"\n⚠️ Avoid Zones:")
            for zone in levels["avoid_zones"]:
                if "low" in zone:
                    print(f"    ${zone['low']:,.2f} - ${zone['high']:,.2f}: {zone.get('reason', '')}")
                else:
                    print(f"    ${zone.get('price', 0):,.2f}: {zone.get('reason', '')}")
        
        # Metadata
        print(f"\n📈 Confidence: {result.get('confidence_0_1', 0):.0%}")
        print(f"📦 Cache hit: {result.get('_cache_hit', False)}")
        
        ws_stats = result.get("ws_stats", {})
        print(f"\n🔌 WebSocket Stats:")
        print(f"  Connected: {ws_stats.get('is_connected', False)}")
        print(f"  Buffer trades: {ws_stats.get('buffer_trade_count', 0):,}")
        print(f"  Buffer duration: {ws_stats.get('buffer_duration_minutes', 0):.1f} min")
        
        if result.get("notes"):
            print(f"\n📝 Notes: {', '.join(result['notes'])}")
        
        if result.get("quality_flags"):
            print(f"\n⚠️ Quality flags: {result['quality_flags']}")
    
    else:
        print("❌ FAILED\n")
        error = result.get("error", {})
        print(f"Error type: {error.get('type', 'unknown')}")
        print(f"Message: {error.get('message', 'No message')}")
        
        if result.get("quality_flags"):
            print(f"\n⚠️ Quality flags: {result['quality_flags']}")
        
        if result.get("ws_stats"):
            ws_stats = result["ws_stats"]
            print(f"\n🔌 WebSocket Stats:")
            print(f"  Connected: {ws_stats.get('is_connected', False)}")
            print(f"  Buffer trades: {ws_stats.get('buffer_trade_count', 0):,}")
        
        if result.get("notes"):
            print(f"\n💡 Suggestions: {', '.join(result['notes'])}")
    
    # Step 6: Test cache
    print_separator("Step 6: Test Cache (Second Call)")
    print("Calling same tool again to verify cache...")
    
    start = time.time()
    result2 = volume_profile_levels_futures_ws(
        symbol=symbol,
        window_minutes=30,
        bin_size=None
    )
    elapsed2 = time.time() - start
    
    print(f"Second call took: {elapsed2:.3f} seconds")
    print(f"Cache hit: {result2.get('_cache_hit', False)}")
    
    if result2.get("_cache_hit"):
        print("✅ Cache working correctly!")
    
    print_separator("DEMO COMPLETE")
    
    # Show full JSON output
    print("\n📋 Full JSON Output:")
    print_json(result)
    
    return result


def main():
    """Main entry point."""
    signal.signal(signal.SIGINT, signal_handler)
    
    parser = argparse.ArgumentParser(
        description="Demo WebSocket-based Volume Profile Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/demo_ws_volume_profile.py --symbol BTCUSDT --wait 60
    python scripts/demo_ws_volume_profile.py --symbol ETHUSDT --wait 120
    python scripts/demo_ws_volume_profile.py --wait 30  # Quick test
        """
    )
    parser.add_argument(
        "--symbol",
        choices=["BTCUSDT", "ETHUSDT"],
        default="BTCUSDT",
        help="Symbol to analyze (default: BTCUSDT)"
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=60,
        help="Seconds to wait for data collection (default: 60)"
    )
    
    args = parser.parse_args()
    
    try:
        demo_ws_volume_profile(
            symbol=args.symbol,
            wait_seconds=args.wait
        )
    except KeyboardInterrupt:
        print("\n\n🛑 Demo interrupted")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
