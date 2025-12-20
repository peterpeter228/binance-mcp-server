# Limit Order Optimization Tools

This document describes the limit order optimization tools designed to improve maker limit order success rates and reduce adverse selection in BTC/ETH perpetual futures trading.

## Overview

These tools provide microstructure analysis for LLM-based trading decisions:

1. **`queue_fill_estimator_futures`** - Estimates queue position, fill probability, and adverse selection risk
2. **`volume_profile_levels_futures`** - Identifies key support/resistance levels from volume distribution

Both tools output JSON ≤ 2KB with statistical summaries only (no raw tick data or full orderbook).

## Installation & Configuration

### Environment Variables

```bash
# Required
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"

# Optional: Use testnet
export BINANCE_TESTNET="true"
```

### Running the Server

#### STDIO Mode (Default - for MCP Clients)

```bash
# Install dependencies
pip install -e .

# Run with STDIO transport
binance-mcp-server
# or
python -m binance_mcp_server.server
```

#### SSE Mode (Server-Sent Events)

```bash
# Run with SSE transport on port 8000
binance-mcp-server --transport sse --port 8000 --host localhost
```

#### HTTP Mode (Streamable HTTP)

```bash
# Run with streamable-http transport
binance-mcp-server --transport streamable-http --port 8000 --host localhost
```

### MCP Client Configuration (Claude Desktop)

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "binance": {
      "command": "binance-mcp-server",
      "env": {
        "BINANCE_API_KEY": "your_api_key",
        "BINANCE_API_SECRET": "your_api_secret"
      }
    }
  }
}
```

For SSE/HTTP mode:

```json
{
  "mcpServers": {
    "binance": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

---

## Tool 1: Queue Fill Estimator

### Purpose

Estimates the queue position, expected time to fill, and fill probability for limit orders at specified price levels. Helps LLMs make optimal price/fill-speed tradeoffs.

### Function Signature

```python
queue_fill_estimator_futures(
    symbol: str,           # "BTCUSDT" or "ETHUSDT"
    side: str,             # "BUY" or "SELL"
    price_levels: list,    # Up to 5 price levels to analyze
    qty: float,            # Order quantity
    lookback_seconds: int = 30  # Trade lookback (10-120)
)
```

### Output Structure

```json
{
  "ts_ms": 1703094000000,
  "inputs": {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "price_levels": [50000, 49950, 49900],
    "qty": 0.1,
    "lookback_seconds": 30
  },
  "per_level": [
    {
      "price": 50000.0,
      "queue_qty_est": 12.5,
      "queue_value_usd": 625000,
      "consumption_rate_qty_per_s": 0.5,
      "eta_p50_s": 12.5,
      "eta_p95_s": 37.4,
      "fill_prob_30s": 0.65,
      "fill_prob_60s": 0.88,
      "adverse_selection_score": 35,
      "notes_max2": ["OBI favors"]
    }
  ],
  "global": {
    "micro_health_score": 72,
    "spread_bps": 2.5,
    "obi_mean": 0.15,
    "obi_stdev": 0.08,
    "wall_risk_level": 1,
    "recommendation": {
      "best_price": 50000.0,
      "why": "Best fill probability (88%) with acceptable adverse selection (35)"
    }
  },
  "quality_flags": []
}
```

### Key Metrics Explained

| Metric | Description |
|--------|-------------|
| `queue_qty_est` | Estimated quantity ahead in queue at this price |
| `queue_value_usd` | USD value of queue ahead |
| `consumption_rate_qty_per_s` | Rate at which queue is consumed (qty/second) |
| `eta_p50_s` | Median expected time to fill (seconds) |
| `eta_p95_s` | 95th percentile time to fill (seconds) |
| `fill_prob_30s` | Probability of fill within 30 seconds |
| `fill_prob_60s` | Probability of fill within 60 seconds |
| `adverse_selection_score` | Risk score 0-100 (higher = more risk) |
| `micro_health_score` | Market health 0-100 (higher = healthier) |
| `spread_bps` | Current spread in basis points |
| `obi_mean` | Order Book Imbalance (-1 to 1) |
| `wall_risk_level` | Wall presence (0-3, higher = more walls) |

### Algorithm Details

**Queue Estimation:**
- Uses orderbook depth at and above target price
- Factors in recent trade consumption patterns

**Consumption Rate:**
- Calculated from aggTrades in lookback window
- Separated by aggressor side (buy vs sell)

**Fill Probability:**
- Exponential decay model: `P(fill) = 1 - exp(-rate * time / queue)`

**Adverse Selection Score:**
Increases when:
- OBI shows pressure against your side
- Trade flow imbalance against your side
- Price is unfavorable relative to mid

### LLM Usage Example

```
User: I want to place a 0.1 BTC buy limit order. Where should I place it?

LLM: Let me analyze the queue at different price levels...

[Calls queue_fill_estimator_futures with prices near current]

Based on the analysis:
- At $49,950: 88% fill probability in 60s, adverse selection score 35
- At $49,900: 72% fill probability, adverse selection score 28
- At $49,850: 45% fill probability, adverse selection score 22

Recommendation: Place at $49,950 for the best balance of fill speed 
and risk. The market health score is 72 (good) with tight spreads.
```

---

## Tool 2: Volume Profile Levels

### Purpose

Identifies key support/resistance levels based on volume distribution, helping LLMs select optimal limit order prices.

### Function Signature

```python
volume_profile_levels_futures(
    symbol: str,              # "BTCUSDT" or "ETHUSDT"
    window_minutes: int = 240, # Analysis window (15-240 min)
    bin_size: float = None    # Price bin size (auto if None)
)
```

### Output Structure

```json
{
  "ts_ms": 1703094000000,
  "window": {
    "start_ms": 1703080400000,
    "end_ms": 1703094000000,
    "minutes": 240,
    "trade_count": 15420,
    "bin_size": 25,
    "bin_count": 48
  },
  "levels": {
    "vpoc": 50125.0,
    "vah": 50450.0,
    "val": 49800.0,
    "hvn": [
      {"price": 50100.0, "volume": 125.5, "multiple": 2.3},
      {"price": 50200.0, "volume": 98.2, "multiple": 1.9}
    ],
    "lvn": [
      {"price": 49950.0, "volume": 8.2, "multiple": 0.15},
      {"price": 50350.0, "volume": 12.1, "multiple": 0.22}
    ],
    "single_print_zones": [
      {"low": 50400.0, "high": 50450.0, "bins": 3}
    ],
    "magnet_levels": [
      {"price": 50125.0, "type": "VPOC", "distance_bps": 25.0, "strength": "strong"},
      {"price": 50100.0, "type": "HVN", "distance_bps": 50.0, "strength": "strong"}
    ],
    "avoid_zones": [
      {"price_low": 49950.0, "price_high": 49950.0, "type": "LVN", 
       "distance_bps": 100.0, "reason": "Low volume - rapid price movement area"}
    ]
  },
  "quality_flags": []
}
```

### Key Levels Explained

| Level | Description | Trading Use |
|-------|-------------|-------------|
| **VPOC** | Volume Point of Control - price with most volume | Strongest S/R, good for limit orders |
| **VAH** | Value Area High - upper boundary of 70% volume | Resistance zone |
| **VAL** | Value Area Low - lower boundary of 70% volume | Support zone |
| **HVN** | High Volume Nodes - accumulation zones | Good limit order levels |
| **LVN** | Low Volume Nodes - price moved quickly through | Avoid for limit orders |
| **Single Prints** | Gap zones from rapid moves | Potential breakout zones |
| **Magnet Levels** | Where price gravitates | Target prices |
| **Avoid Zones** | Poor fill probability areas | Don't place limits here |

### LLM Usage Example

```
User: Where are the best levels to place a buy limit for BTC?

LLM: Let me analyze the volume profile...

[Calls volume_profile_levels_futures]

Key findings from 4-hour volume profile:
- VPOC at $50,125 (strongest support)
- Value Area: $49,800 - $50,450
- High volume node at $50,100 (good for limits)

Avoid zones:
- $49,950 (LVN - price moves fast through here)
- $50,400-50,450 (single print zone)

Recommendation: Place buy limits at $50,100 (HVN) or $49,800 (VAL)
for best fill probability at structural support.
```

---

## Best Practices for LLMs

### Combining Both Tools

1. **First**: Call `volume_profile_levels_futures` to identify structural levels
2. **Then**: Call `queue_fill_estimator_futures` with those levels to assess fill probability

### Decision Framework

```
IF micro_health_score > 70:
    # Market is healthy, can be more aggressive
    Prefer levels with fill_prob_60s > 0.7
    
IF adverse_selection_score > 60:
    # High risk of adverse fill
    Consider tighter levels or skip this entry
    
IF wall_risk_level >= 2:
    # Large walls present
    Be cautious about levels near walls
    
FOR buy limits:
    Prefer levels at HVN, VPOC, or VAL
    Avoid LVN and single print zones
    
FOR sell limits:
    Prefer levels at HVN, VPOC, or VAH
    Avoid LVN and single print zones
```

### Quality Flags

Handle these flags appropriately:

| Flag | Meaning | Action |
|------|---------|--------|
| `LOW_TRADE_ACTIVITY` | Few trades in lookback | Widen lookback or be cautious |
| `SHALLOW_ORDERBOOK` | Limited orderbook depth | Estimates less reliable |
| `TRADE_DATA_DEGRADED` | Trade fetch partial failure | Use with caution |
| `INSUFFICIENT_DATA` | Not enough data for analysis | Cannot provide reliable levels |
| `LOW_SAMPLE_SIZE` | Small trade sample | Results may be noisy |

---

## Data Sources

Both tools use Binance Futures API:
- **Orderbook**: `/fapi/v1/depth` (cached 0.5s)
- **Trades**: `/fapi/v1/aggTrades` (cached 0.5s)
- **Mark Price**: `/fapi/v1/premiumIndex` (cached 1s)

Rate limiting is enforced to stay within Binance limits (1200 req/min).

## Supported Symbols

Currently limited to:
- **BTCUSDT** - Bitcoin perpetual
- **ETHUSDT** - Ethereum perpetual

---

## Testing

Run the test suite:

```bash
# Run all limit order tool tests
pytest tests/test_tools/test_limit_order_tools.py -v

# Run specific test class
pytest tests/test_tools/test_limit_order_tools.py::TestQueueFillEstimator -v
```

## Changelog

### v1.4.0
- Added `queue_fill_estimator_futures` tool
- Added `volume_profile_levels_futures` tool
- New microstructure data module with caching
- Support for SSE and HTTP transport modes
