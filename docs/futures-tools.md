# Binance USDⓈ-M Futures MCP Tools

This document provides comprehensive documentation for the Binance USDⓈ-M Futures MCP tools, including API reference, usage examples, and deployment guide.

## Overview

The Futures tools enable LLM agents to execute trades on Binance USDⓈ-M Futures (BTCUSDT/ETHUSDT perpetual contracts) with:

- **Automatic validation** against exchange filters (tickSize, stepSize, minNotional)
- **Price/quantity rounding** to valid precision
- **Server time synchronization** with automatic retry on timestamp errors
- **Structured JSON responses** with `raw_response` and `normalized_fields`
- **Testnet support** via `BINANCE_TESTNET=true`

## Supported Symbols

Currently supported (hardcoded allowlist):
- **BTCUSDT** - Bitcoin/USDT Perpetual
- **ETHUSDT** - Ethereum/USDT Perpetual

## Environment Configuration

```bash
# Required
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"

# Optional
export BINANCE_TESTNET="true"           # Use testnet (default: false)
export BINANCE_RECV_WINDOW="5000"       # Receive window in ms (default: 5000)
```

### Testnet vs Production URLs

| Environment | Base URL |
|-------------|----------|
| Production | `https://fapi.binance.com` |
| Testnet | `https://testnet.binancefuture.com` |

---

## P0 Tools (Core)

### 1. get_exchange_info_futures

Get trading rules, filters, and precision settings for a futures symbol.

**Endpoint:** `GET /fapi/v1/exchangeInfo`

**Input:**
```json
{
    "name": "get_exchange_info_futures",
    "arguments": {
        "symbol": "BTCUSDT"
    }
}
```

**Output:**
```json
{
    "success": true,
    "data": {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "tickSize": "0.10",
        "stepSize": "0.001",
        "minQty": "0.001",
        "maxQty": "1000",
        "minNotional": "5",
        "pricePrecision": 2,
        "quantityPrecision": 3,
        "maxLeverage": 125,
        "filters": {...}
    },
    "raw_response": {...},
    "serverTime": 1234567890123
}
```

---

### 2. get_commission_rate_futures

Get maker/taker commission rates for accurate fee calculation.

**Endpoint:** `GET /fapi/v1/commissionRate` (signed)

**Input:**
```json
{
    "name": "get_commission_rate_futures",
    "arguments": {
        "symbol": "BTCUSDT"
    }
}
```

**Output:**
```json
{
    "success": true,
    "data": {
        "symbol": "BTCUSDT",
        "makerCommissionRate": "0.0002",
        "makerCommissionRate_float": 0.0002,
        "makerCommissionRate_percent": "0.0200%",
        "takerCommissionRate": "0.0004",
        "takerCommissionRate_float": 0.0004,
        "takerCommissionRate_percent": "0.0400%"
    },
    "serverTime": 1234567890123
}
```

---

### 3. get_position_risk_futures

Get position information including entry price, mark price, liquidation price, and unrealized PnL.

**Endpoint:** `GET /fapi/v2/positionRisk` (signed)

**Input:**
```json
{
    "name": "get_position_risk_futures",
    "arguments": {
        "symbol": "BTCUSDT"  // Optional, omit for all allowed symbols
    }
}
```

**Output:**
```json
{
    "success": true,
    "data": [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.100",
            "entryPrice": "50000.0",
            "markPrice": "51000.0",
            "unRealizedProfit": "100.0",
            "liquidationPrice": "45000.0",
            "leverage": "10",
            "marginType": "cross",
            "isolatedMargin": "0",
            "positionSide": "BOTH"
        }
    ],
    "normalized_fields": {
        "BTCUSDT": {
            "positionAmt_float": 0.1,
            "entryPrice_float": 50000.0,
            "markPrice_float": 51000.0,
            "liquidationPrice_float": 45000.0,
            "leverage_int": 10,
            "unrealizedPnl_float": 100.0,
            "hasPosition": true,
            "isLong": true
        }
    }
}
```

---

### 4. get_leverage_brackets_futures

Get leverage brackets with maintenance margin ratios for calculating liquidation price.

**Endpoint:** `GET /fapi/v1/leverageBracket` (signed)

**Input:**
```json
{
    "name": "get_leverage_brackets_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "notional_for_mmr": 100000  // Optional: calculate MMR for this notional
    }
}
```

**Output:**
```json
{
    "success": true,
    "data": {
        "BTCUSDT": {
            "brackets": [
                {
                    "bracket": 1,
                    "initialLeverage": 125,
                    "notionalCap": 50000,
                    "notionalFloor": 0,
                    "maintMarginRatio": 0.004,
                    "cum": 0
                },
                {
                    "bracket": 2,
                    "initialLeverage": 100,
                    "notionalCap": 250000,
                    "notionalFloor": 50000,
                    "maintMarginRatio": 0.005,
                    "cum": 50
                }
            ],
            "maxLeverage": 125,
            "minMaintMarginRatio": 0.004
        }
    },
    "mmr_for_notional": {
        "BTCUSDT": {
            "notional": "100000",
            "bracket": 2,
            "maintMarginRatio": 0.005,
            "initialLeverage": 100
        }
    }
}
```

---

### 5. set_leverage_futures

Set leverage for a symbol. **Idempotent** - returns `already_set=true` if unchanged.

**Endpoint:** `POST /fapi/v1/leverage` (signed)

**Input:**
```json
{
    "name": "set_leverage_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "leverage": 10
    }
}
```

**Output:**
```json
{
    "success": true,
    "data": {
        "symbol": "BTCUSDT",
        "leverage": 10,
        "maxNotionalValue": "100000000"
    },
    "already_set": false,
    "previous_leverage": 20
}
```

---

### 6. set_margin_type_futures

Set margin type (ISOLATED/CROSSED). **Idempotent** - cannot change with open positions.

**Endpoint:** `POST /fapi/v1/marginType` (signed)

**Input:**
```json
{
    "name": "set_margin_type_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "margin_type": "ISOLATED"
    }
}
```

**Output:**
```json
{
    "success": true,
    "data": {
        "symbol": "BTCUSDT",
        "marginType": "ISOLATED"
    },
    "already_set": false
}
```

---

### 7. place_order_futures

Place a futures order with automatic validation and rounding.

**Endpoint:** `POST /fapi/v1/order` (signed)

**Input:**
```json
{
    "name": "place_order_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 0.001,
        "price": 50000.15,
        "time_in_force": "GTC",
        "post_only": true,          // Maps to timeInForce=GTX
        "reduce_only": false,
        "position_side": "BOTH",
        "client_order_id": "my_order_123"
    }
}
```

**Order Types:**
- `LIMIT` - Requires price, timeInForce
- `MARKET` - Market execution
- `STOP` - Stop limit (requires price, stopPrice)
- `STOP_MARKET` - Stop market (requires stopPrice)
- `TAKE_PROFIT` - Take profit limit (requires price, stopPrice)
- `TAKE_PROFIT_MARKET` - Take profit market (requires stopPrice)
- `TRAILING_STOP_MARKET` - Trailing stop (requires callbackRate)

**Output:**
```json
{
    "success": true,
    "data": {
        "orderId": 1234567890,
        "clientOrderId": "my_order_123",
        "symbol": "BTCUSDT",
        "status": "NEW",
        "side": "BUY",
        "type": "LIMIT",
        "price": "50000.10",
        "origQty": "0.001",
        "avgPrice": "0",
        "updateTime": 1234567890123
    },
    "validation": {
        "price_rounded": true,
        "original_price": 50000.15,
        "rounded_price": "50000.10",
        "notional": "50.00"
    }
}
```

---

### 8. amend_order_futures

Modify an existing LIMIT order's price/quantity.

**Endpoint:** `PUT /fapi/v1/order` (signed)

**Input:**
```json
{
    "name": "amend_order_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "order_id": 1234567890,
        "side": "BUY",
        "price": 51000,
        "quantity": 0.002
    }
}
```

---

### 9. get_order_status_futures

Get detailed order status including fill percentage.

**Endpoint:** `GET /fapi/v1/order` (signed)

**Input:**
```json
{
    "name": "get_order_status_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "order_id": 1234567890
    }
}
```

**Output:**
```json
{
    "success": true,
    "data": {
        "orderId": 1234567890,
        "status": "PARTIALLY_FILLED",
        "executedQty": "0.0005",
        "origQty": "0.001",
        "isFilled": false,
        "isPartiallyFilled": true,
        "isActive": true,
        "fillPercentage": 50.0
    }
}
```

---

### 10. cancel_order_futures

Cancel a single order.

**Endpoint:** `DELETE /fapi/v1/order` (signed)

**Input:**
```json
{
    "name": "cancel_order_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "order_id": 1234567890
    }
}
```

---

### 11. cancel_multiple_orders_futures

Cancel up to 10 orders in a batch.

**Endpoint:** `DELETE /fapi/v1/batchOrders` (signed)

**Input:**
```json
{
    "name": "cancel_multiple_orders_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "order_id_list": [1234567890, 1234567891, 1234567892]
    }
}
```

**Output:**
```json
{
    "success": true,
    "data": {
        "totalRequested": 3,
        "successCount": 2,
        "failedCount": 1,
        "allSucceeded": false
    },
    "cancelled_orders": [...],
    "failed_orders": [...]
}
```

---

## P1 Tools (Advanced)

### 12. validate_order_plan_futures

Pre-validate an order plan before execution.

**Input:**
```json
{
    "name": "validate_order_plan_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "entry_price": 50000.15,
        "quantity": 0.001,
        "stop_loss": 49000,
        "take_profits": [
            {"price": 51000, "percentage": 50},
            {"price": 52000, "percentage": 50}
        ],
        "post_only": true,
        "leverage": 10,
        "margin_type": "ISOLATED"
    }
}
```

**Output:**
```json
{
    "success": true,
    "valid": true,
    "data": {
        "entry": {
            "original_price": 50000.15,
            "rounded_price": "50000.10",
            "original_quantity": 0.001,
            "rounded_quantity": "0.001",
            "notional": "50.00"
        },
        "stop_loss": {
            "original_price": 49000,
            "rounded_price": "49000.00"
        },
        "take_profits": [...]
    },
    "reasons": ["entry_price_rounded"],
    "suggested_fixes": [],
    "exchange_info": {
        "tickSize": "0.10",
        "stepSize": "0.001",
        "minNotional": "5"
    }
}
```

---

### 13. place_bracket_orders_futures

Place a complete bracket: entry + stop loss + take profits with OCO-like coordination.

**Input:**
```json
{
    "name": "place_bracket_orders_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "entry_price": 50000,
        "quantity": 0.01,
        "stop_loss_price": 49000,
        "take_profits": [
            {"price": 51000, "percentage": 50},
            {"price": 52000, "percentage": 50}
        ],
        "entry_type": "LIMIT",
        "post_only": true,
        "working_type": "CONTRACT_PRICE",
        "wait_for_entry": true
    }
}
```

**Output:**
```json
{
    "success": true,
    "job_id": "bracket_abc123",
    "entry_order": {
        "orderId": 12345,
        "status": "NEW"
    },
    "exit_orders_pending": true,
    "monitoring": true,
    "message": "Entry placed, exits will be placed when entry fills"
}
```

**Track with:** `get_bracket_job_status(job_id="bracket_abc123")`

**Cancel with:** `cancel_bracket_job(job_id="bracket_abc123")`

---

### 14. cancel_on_ttl_futures

Auto-cancel an order after TTL expires (max 600 seconds).

**Input:**
```json
{
    "name": "cancel_on_ttl_futures",
    "arguments": {
        "symbol": "BTCUSDT",
        "order_id": 12345,
        "ttl_seconds": 60,
        "blocking": false
    }
}
```

**Non-blocking Output:**
```json
{
    "success": true,
    "job_id": "ttl_abc123",
    "order_id": 12345,
    "ttl_seconds": 60,
    "scheduled_cancel_at": 1234567950000,
    "check_with": "get_ttl_job_status(job_id='ttl_abc123')"
}
```

**Blocking Output:**
```json
{
    "success": true,
    "action": "cancelled",
    "data": {
        "order_id": 12345,
        "final_status": "CANCELED",
        "executed_qty": "0.000"
    },
    "waited_seconds": 60
}
```

---

## Error Handling

All errors return structured JSON with `success=false`:

```json
{
    "success": false,
    "error": {
        "type": "validation_error",
        "message": "Quantity 0.0001 (rounded: 0.000) is below minimum 0.001",
        "details": {
            "code": -1111,
            "params_sent": {...}
        }
    }
}
```

**Error Types:**
- `validation_error` - Input validation failed
- `api_error` - Binance API error
- `order_not_found` - Order doesn't exist
- `position_exists` - Cannot change margin type with open position
- `tool_error` - Unexpected tool execution error

---

## Deployment Guide

### Option 1: SSE/HTTP Server (Recommended for Remote Access)

Start the server with SSE or HTTP transport for remote connections:

```bash
# Install the package
pip install binance-mcp-server

# Set environment variables
export BINANCE_API_KEY="your_key"
export BINANCE_API_SECRET="your_secret"
export BINANCE_TESTNET="true"  # Use testnet for testing

# Start with SSE transport (for CherryStudio)
binance-mcp-server --transport sse --host 0.0.0.0 --port 8000

# Or streamable-http
binance-mcp-server --transport streamable-http --host 0.0.0.0 --port 8000
```

**Important:** For production, always run behind a reverse proxy (nginx) with HTTPS.

### Option 2: Systemd Service (Ubuntu Server)

Create `/etc/systemd/system/binance-mcp.service`:

```ini
[Unit]
Description=Binance MCP Server
After=network.target

[Service]
Type=simple
User=ubuntu
Environment="BINANCE_API_KEY=your_key"
Environment="BINANCE_API_SECRET=your_secret"
Environment="BINANCE_TESTNET=false"
ExecStart=/home/ubuntu/.local/bin/binance-mcp-server --transport sse --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable binance-mcp
sudo systemctl start binance-mcp
sudo systemctl status binance-mcp
```

### Option 3: Docker Deployment

Create `Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install package
RUN pip install binance-mcp-server

# Expose port
EXPOSE 8000

# Run server
CMD ["binance-mcp-server", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:

```bash
docker build -t binance-mcp .
docker run -d \
  -p 8000:8000 \
  -e BINANCE_API_KEY=your_key \
  -e BINANCE_API_SECRET=your_secret \
  -e BINANCE_TESTNET=true \
  --name binance-mcp \
  binance-mcp
```

### Nginx Reverse Proxy (HTTPS)

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/mcp.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;  # For SSE connections
    }
}
```

---

## CherryStudio Connection

After deploying to your Ubuntu server, configure CherryStudio:

### SSE Connection

```json
{
    "mcpServers": {
        "binance-futures": {
            "url": "http://your-server-ip:8000/sse",
            "transport": "sse"
        }
    }
}
```

### HTTP Connection

```json
{
    "mcpServers": {
        "binance-futures": {
            "url": "http://your-server-ip:8000",
            "transport": "streamable-http"
        }
    }
}
```

---

## Security Recommendations

1. **Never expose API keys** - Use environment variables
2. **Use HTTPS** in production with nginx reverse proxy
3. **Firewall rules** - Only allow necessary IPs
4. **Test on testnet first** - Set `BINANCE_TESTNET=true`
5. **Rate limiting** - Built-in at 1200 requests/minute
6. **Audit logging** - All operations are logged

---

## Testing

Run the test suite:

```bash
# Install dev dependencies
pip install pytest

# Run all futures tests
pytest tests/test_tools/test_futures_tools.py -v

# Run specific test class
pytest tests/test_tools/test_futures_tools.py::TestOrderValidator -v
```

**Test coverage includes:**
- Symbol validation (allowlist)
- Price rounding (tick size)
- Quantity rounding (step size)
- Notional validation (min notional)
- Post-only → GTX mapping
- Leverage/margin idempotent handling
- Structured error responses
