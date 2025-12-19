# Microstructure Analysis Tools

Token-efficient market microstructure analysis for LLM-based trading systems.

## Overview

These tools provide **compact market microstructure summaries** designed for LLM context efficiency (< 2KB output per call). They eliminate the need for LLMs to:

- Fetch raw orderbook data
- Fetch raw trade history
- Fetch raw OHLCV klines
- Write calculation code for metrics like OBI, volatility, slippage

All analysis is done server-side, returning only actionable metrics.

## Tools

### `microstructure_snapshot`

Get a comprehensive market microstructure snapshot in a single call.

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | str | required | Trading symbol (BTCUSDT or ETHUSDT) |
| `depth_levels` | int | 20 | Orderbook levels per side (5-100) |
| `snapshots` | int | 3 | Number of OB snapshots for OBI calculation (1-10) |
| `spacing_ms` | int | 2000 | Milliseconds between snapshots (100-10000) |
| `trades_limit` | int | 300 | Recent trades to analyze (50-1000) |

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `ts` | int | Unix timestamp in milliseconds |
| `symbol` | str | Trading symbol |
| `best_bid` | float | Best bid price |
| `best_ask` | float | Best ask price |
| `mid` | float | Mid price |
| `tick_size` | float | Minimum price increment |
| `spread_points` | float | Spread in price points |
| `spread_bps` | float | Spread in basis points |
| `depth` | object | Depth metrics (see below) |
| `obi` | object | Order Book Imbalance stats (see below) |
| `walls` | object | Wall detection (see below) |
| `trade_flow` | object | Trade flow analysis (see below) |
| `slippage_est` | object | Slippage estimates (see below) |
| `micro_health_score` | int | Overall health score 0-100 |
| `wall_risk_level` | str | "low", "medium", or "high" |
| `notes` | array | Warning/degradation notes (max 6) |

##### Depth Object

```json
{
  "bid_qty_sum_topN": 125.5,
  "ask_qty_sum_topN": 118.2,
  "depth_10bps": 85.3,
  "depth_20bps": 210.7
}
```

##### OBI (Order Book Imbalance) Object

```json
{
  "snapshots": [0.12, 0.08, 0.15],
  "mean": 0.1167,
  "stdev": 0.0351
}
```

- Range: [-1, 1]
- Positive = bid pressure (more buy interest)
- Negative = ask pressure (more sell interest)
- Lower stdev = more stable orderbook

##### Walls Object

```json
{
  "bid": [
    {
      "price": 104200.0,
      "qty": 15.5,
      "size_ratio_vs_median": 4.2,
      "persistence_score": 1.0
    }
  ],
  "ask": [
    {
      "price": 104300.0,
      "qty": 18.2,
      "size_ratio_vs_median": 4.9,
      "persistence_score": 0.67
    }
  ]
}
```

- `size_ratio_vs_median`: How large the wall is compared to median order size
- `persistence_score`: 0.0 = new wall, 1.0 = existed in all previous snapshots

##### Trade Flow Object

```json
{
  "buy_qty_sum": 45.2,
  "sell_qty_sum": 38.7,
  "taker_imbalance": 0.077
}
```

- `taker_imbalance`: [-1, 1] where positive = more aggressive buying

##### Slippage Estimate Object

```json
{
  "p50_points": 0.3,
  "p95_points": 1.2
}
```

Based on recent trade sizes and orderbook depth.

#### Micro Health Score

The health score (0-100) is calculated using weighted factors:

| Factor | Weight | Description |
|--------|--------|-------------|
| Spread Quality | 30% | Lower spread = higher score |
| OBI Stability | 20% | Lower stdev = higher score |
| Depth Quality | 25% | Higher depth = higher score |
| Trade Flow Balance | 15% | Lower absolute imbalance = higher score |
| Wall Persistence | 10% | Higher persistence = higher score |

#### Wall Risk Level

Assesses spoofing/manipulation risk:

- **low**: Normal market conditions
- **medium**: Some warning signs (large walls, low persistence, or OBI volatility)
- **high**: Multiple risk factors present (very large walls + low persistence + high volatility)

### `expected_move`

Calculate expected price movement based on realized volatility.

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `symbol` | str | required | Trading symbol (BTCUSDT or ETHUSDT) |
| `horizon_minutes` | int | 60 | Time horizon in minutes (1-1440) |
| `interval` | str | "1m" | Kline interval (1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h) |
| `lookback` | int | 240 | Number of klines for calculation (30-1000) |

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `ts` | int | Unix timestamp in milliseconds |
| `symbol` | str | Trading symbol |
| `rv` | float | Annualized realized volatility (%) |
| `expected_move_points` | float | 1-sigma expected move in price points |
| `expected_move_bps` | float | Expected move in basis points |
| `confidence` | float | Confidence score 0-1 |
| `current_price` | float | Latest price |
| `horizon_minutes` | int | Time horizon used |
| `interval_used` | str | Kline interval used |
| `candles_analyzed` | int | Number of data points used |
| `notes` | array | Warnings (high/low vol regime, trending) |

## Usage Examples

### Python

```python
# Using the MCP client
result = mcp_client.call_tool("microstructure_snapshot", {
    "symbol": "BTCUSDT",
    "depth_levels": 20,
    "snapshots": 3
})

if result["success"]:
    data = result["data"]
    print(f"Health Score: {data['micro_health_score']}")
    print(f"OBI: {data['obi']['mean']} ± {data['obi']['stdev']}")
    print(f"Spread: {data['spread_bps']}bps")
```

### LLM Integration (Example Prompt)

```
Use the microstructure_snapshot tool to analyze BTCUSDT market structure.
Based on the health score and OBI, assess if this is a good time to place
a limit order.
```

### CherryStudio/Cursor Configuration

```json
{
  "mcpServers": {
    "binance": {
      "command": "binance-mcp-server",
      "args": ["--transport", "stdio"],
      "env": {
        "BINANCE_API_KEY": "your_key",
        "BINANCE_API_SECRET": "your_secret"
      }
    }
  }
}
```

For remote SSE connection:

```json
{
  "mcpServers": {
    "binance": {
      "url": "http://your-server:8000/sse",
      "transport": "sse"
    }
  }
}
```

## Deployment

### Start with STDIO (local)

```bash
binance-mcp-server --transport stdio
```

### Start with SSE (remote)

```bash
binance-mcp-server --transport sse --host 0.0.0.0 --port 8000
```

### Start with HTTP Streamable (remote)

```bash
binance-mcp-server --transport streamable-http --host 0.0.0.0 --port 8000
```

## Best Practices

### For LLM Token Efficiency

1. **Single Call Pattern**: Use `microstructure_snapshot` instead of multiple raw data calls
2. **Interpret, Don't Parse**: Use the `micro_health_score` and `wall_risk_level` directly
3. **Check Notes**: The `notes` array contains important warnings and degradation info

### For Trading Decisions

1. **Combine Metrics**: Use OBI + health score + wall risk together
2. **Monitor Persistence**: Low wall persistence may indicate spoofing
3. **Use Expected Move**: Compare your target profit to expected volatility

## Rate Limiting

The tools implement internal rate limiting:
- Max 20 requests per second in normal mode
- Automatic degradation to single snapshot in burst mode
- Notes will indicate `rate_limited_burst_mode` when triggered

## Error Handling

All responses follow this structure:

```json
{
  "success": true|false,
  "data": { ... },      // Only on success
  "error": {            // Only on failure
    "type": "validation_error|api_error|tool_error",
    "message": "Error description"
  },
  "timestamp": 1734567890123,
  "metadata": { ... }   // Additional info
}
```

The tools are designed to degrade gracefully:
- Missing data adds notes, doesn't fail
- Rate limiting reduces snapshots, doesn't fail
- API errors are captured with meaningful messages
