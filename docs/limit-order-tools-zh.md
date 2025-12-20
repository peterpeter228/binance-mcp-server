# 限价单优化工具 - 安装使用说明

本文档详细介绍如何安装、配置和使用限价单优化工具（Queue Fill Estimator 和 Volume Profile Levels）。

---

## 目录

1. [环境要求](#环境要求)
2. [安装方法](#安装方法)
3. [配置说明](#配置说明)
4. [启动服务器](#启动服务器)
5. [MCP客户端配置](#mcp客户端配置)
6. [工具使用详解](#工具使用详解)
7. [LLM集成示例](#llm集成示例)
8. [常见问题](#常见问题)

---

## 环境要求

| 要求 | 版本/说明 |
|------|----------|
| Python | 3.10 或更高版本 |
| 操作系统 | Linux、macOS、Windows |
| Binance账户 | 需要开通API访问权限 |
| 网络 | 能够访问 Binance API |

---

## 安装方法

### 方法一：从 PyPI 安装（推荐）

```bash
# 使用 pip 安装
pip install binance-mcp-server

# 或使用 uv（更快）
uv add binance-mcp-server
```

### 方法二：从源码安装

```bash
# 1. 克隆仓库
git clone https://github.com/AnalyticAce/binance-mcp-server.git
cd binance-mcp-server

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 安装依赖
pip install -e .

# 4. 验证安装
python -c "from binance_mcp_server.tools.futures import queue_fill_estimator, volume_profile_levels; print('安装成功!')"
```

### 依赖包列表

安装时会自动安装以下依赖：

```
fastmcp>=2.5.1
python-binance>=1.0.29
typer>=0.16.0
requests>=2.31.0
```

---

## 配置说明

### 环境变量配置

创建 `.env` 文件或设置环境变量：

```bash
# 必需：Binance API 凭证
export BINANCE_API_KEY="你的API密钥"
export BINANCE_API_SECRET="你的API密钥Secret"

# 可选：使用测试网（强烈建议开发时使用）
export BINANCE_TESTNET="true"

# 可选：接收窗口（毫秒）
export BINANCE_RECV_WINDOW="5000"
```

### 获取 Binance API 密钥

1. 登录 [Binance](https://www.binance.com)
2. 进入 **用户中心** → **API管理**
3. 点击 **创建API**
4. 设置API权限：
   - ✅ 读取信息（必需）
   - ✅ 现货和杠杆交易（如需交易）
   - ✅ 合约（如需期货交易）
5. 复制 API Key 和 Secret Key

### 测试网配置（推荐）

对于开发和测试，建议使用 Binance 测试网：

1. 访问 [Binance Futures 测试网](https://testnet.binancefuture.com)
2. 注册测试账户并获取测试API密钥
3. 设置 `BINANCE_TESTNET="true"`

---

## 启动服务器

### 方式一：STDIO 模式（默认，用于 MCP 客户端）

```bash
# 基本启动
binance-mcp-server

# 或使用模块方式
python -m binance_mcp_server.server

# 带调试日志
binance-mcp-server --log-level DEBUG
```

### 方式二：SSE 模式（Server-Sent Events）

适用于 CherryStudio、Web 客户端等：

```bash
# 本地启动
binance-mcp-server --transport sse --port 8000 --host localhost

# 远程服务器（允许外部访问）
binance-mcp-server --transport sse --port 8000 --host 0.0.0.0
```

启动后访问：`http://localhost:8000/sse`

### 方式三：HTTP 模式（Streamable HTTP）

```bash
# 启动 HTTP 服务
binance-mcp-server --transport streamable-http --port 8000 --host 0.0.0.0
```

### 命令行参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--transport` | 传输方式：`stdio`、`sse`、`streamable-http` | `stdio` |
| `--port` | HTTP/SSE 端口 | `8000` |
| `--host` | 监听地址 | `localhost` |
| `--log-level` | 日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR` | `INFO` |

---

## MCP客户端配置

### Claude Desktop 配置

编辑 `claude_desktop_config.json`：

**STDIO 模式：**

```json
{
  "mcpServers": {
    "binance": {
      "command": "binance-mcp-server",
      "env": {
        "BINANCE_API_KEY": "你的API密钥",
        "BINANCE_API_SECRET": "你的API密钥Secret",
        "BINANCE_TESTNET": "true"
      }
    }
  }
}
```

**SSE 模式：**

```json
{
  "mcpServers": {
    "binance": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### CherryStudio 配置

1. 打开 CherryStudio 设置
2. 添加 MCP 服务器
3. 选择 SSE 传输
4. 输入 URL：`http://你的服务器IP:8000/sse`

### 远程服务器部署（Ubuntu）

**使用 systemd 管理服务：**

```bash
# 创建服务文件
sudo nano /etc/systemd/system/binance-mcp.service
```

写入以下内容：

```ini
[Unit]
Description=Binance MCP Server - Limit Order Tools
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu
Environment="BINANCE_API_KEY=你的API密钥"
Environment="BINANCE_API_SECRET=你的API密钥Secret"
Environment="BINANCE_TESTNET=false"
ExecStart=/home/ubuntu/.local/bin/binance-mcp-server --transport sse --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable binance-mcp
sudo systemctl start binance-mcp

# 查看状态
sudo systemctl status binance-mcp

# 查看日志
journalctl -u binance-mcp -f
```

---

## 工具使用详解

### 工具 1：queue_fill_estimator_futures

**功能：** 估算限价单的队列位置、成交概率和逆向选择风险。

#### 参数说明

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `symbol` | string | ✅ | 交易对：`BTCUSDT` 或 `ETHUSDT` |
| `side` | string | ✅ | 订单方向：`BUY` 或 `SELL` |
| `price_levels` | list | ✅ | 价格列表（最多5个） |
| `qty` | float | ✅ | 订单数量 |
| `lookback_seconds` | int | ❌ | 回看时间（10-120秒，默认30） |

#### 调用示例

```json
{
  "name": "queue_fill_estimator_futures",
  "arguments": {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "price_levels": [97000, 96950, 96900],
    "qty": 0.1,
    "lookback_seconds": 30
  }
}
```

#### 返回结果说明

```json
{
  "ts_ms": 1703094000000,
  "inputs": {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "price_levels": [97000, 96950, 96900],
    "qty": 0.1,
    "lookback_seconds": 30
  },
  "per_level": [
    {
      "price": 97000.0,
      "queue_qty_est": 15.5,
      "queue_value_usd": 1503500,
      "consumption_rate_qty_per_s": 0.8,
      "eta_p50_s": 13.5,
      "eta_p95_s": 40.3,
      "fill_prob_30s": 0.72,
      "fill_prob_60s": 0.92,
      "adverse_selection_score": 38,
      "notes_max2": ["OBI favors"]
    }
  ],
  "global": {
    "micro_health_score": 75,
    "spread_bps": 1.8,
    "obi_mean": 0.12,
    "obi_stdev": 0.05,
    "wall_risk_level": 1,
    "recommendation": {
      "best_price": 97000.0,
      "why": "Best fill probability (92%) with acceptable adverse selection (38)"
    }
  },
  "quality_flags": []
}
```

#### 关键指标解读

| 指标 | 含义 | 建议 |
|------|------|------|
| `queue_qty_est` | 排在你前面的挂单量 | 越小越快成交 |
| `eta_p50_s` | 中位数成交时间（秒） | 参考值，实际可能偏差 |
| `eta_p95_s` | 95%概率成交时间 | 用于评估最坏情况 |
| `fill_prob_30s` | 30秒内成交概率 | >0.7 较好 |
| `fill_prob_60s` | 60秒内成交概率 | >0.9 很好 |
| `adverse_selection_score` | 逆向选择风险（0-100） | <50 较安全，>70 高风险 |
| `micro_health_score` | 市场微观结构健康度 | >60 正常，>80 很好 |
| `spread_bps` | 买卖价差（基点） | <5 紧密，>10 较宽 |
| `wall_risk_level` | 大单墙风险（0-3） | 0 无，3 严重 |

---

### 工具 2：volume_profile_levels_futures

**功能：** 分析成交量分布，识别关键支撑/阻力位。

#### 参数说明

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `symbol` | string | ✅ | 交易对：`BTCUSDT` 或 `ETHUSDT` |
| `window_minutes` | int | ❌ | 分析窗口（15-240分钟，默认240） |
| `bin_size` | float | ❌ | 价格分箱大小（USD，默认自动） |

#### 调用示例

```json
{
  "name": "volume_profile_levels_futures",
  "arguments": {
    "symbol": "BTCUSDT",
    "window_minutes": 240
  }
}
```

#### 返回结果说明

```json
{
  "ts_ms": 1703094000000,
  "window": {
    "start_ms": 1703080400000,
    "end_ms": 1703094000000,
    "minutes": 240,
    "trade_count": 18500,
    "bin_size": 25,
    "bin_count": 52
  },
  "levels": {
    "vpoc": 97125.0,
    "vah": 97450.0,
    "val": 96800.0,
    "hvn": [
      {"price": 97100.0, "volume": 145.5, "multiple": 2.5},
      {"price": 97200.0, "volume": 112.3, "multiple": 1.9}
    ],
    "lvn": [
      {"price": 96950.0, "volume": 12.2, "multiple": 0.21},
      {"price": 97350.0, "volume": 8.8, "multiple": 0.15}
    ],
    "single_print_zones": [
      {"low": 97400.0, "high": 97450.0, "bins": 3}
    ],
    "magnet_levels": [
      {"price": 97125.0, "type": "VPOC", "distance_bps": 0, "strength": "strong"},
      {"price": 97100.0, "type": "HVN", "distance_bps": 26, "strength": "strong"}
    ],
    "avoid_zones": [
      {
        "price_low": 96950.0,
        "price_high": 96950.0,
        "type": "LVN",
        "distance_bps": 180,
        "reason": "Low volume - rapid price movement area"
      }
    ]
  },
  "quality_flags": []
}
```

#### 关键水平解读

| 水平 | 含义 | 交易建议 |
|------|------|----------|
| **VPOC** | 成交量控制点 - 成交量最大的价格 | 最强支撑/阻力，适合挂单 |
| **VAH** | 价值区上沿 - 70%成交量的上边界 | 阻力位，可考虑卖出 |
| **VAL** | 价值区下沿 - 70%成交量的下边界 | 支撑位，可考虑买入 |
| **HVN** | 高成交量节点 - 成交密集区 | 好的限价单价位 |
| **LVN** | 低成交量节点 - 价格快速穿越区 | 避免在此挂单 |
| **Single Print** | 单打印区 - 快速移动形成的缺口 | 可能的突破区域 |
| **Magnet** | 磁吸位 - 价格倾向回归的位置 | 目标价参考 |
| **Avoid Zone** | 避开区 - 成交概率低的区域 | 不要在此挂限价单 |

---

## LLM集成示例

### 场景1：选择最佳买入价位

**用户问：** "我想买入0.1个BTC，应该挂在什么价位？"

**LLM执行流程：**

```
1. 先调用 volume_profile_levels_futures 获取关键价位
2. 根据VPOC、VAL、HVN确定候选价位
3. 调用 queue_fill_estimator_futures 评估这些价位
4. 综合成交概率和逆向选择风险给出建议
```

**示例对话：**

```
LLM: 让我分析一下当前的市场结构...

[调用 volume_profile_levels_futures]

从过去4小时的成交量分布来看：
- VPOC（成交量控制点）在 $97,125
- VAL（价值区下沿）在 $96,800
- 主要HVN在 $97,100 和 $97,200

让我评估这些价位的成交概率...

[调用 queue_fill_estimator_futures，价格: [97125, 97100, 96800]]

分析结果：
- $97,125: 60秒成交概率 85%，逆向选择风险 42
- $97,100: 60秒成交概率 78%，逆向选择风险 35
- $96,800: 60秒成交概率 45%，逆向选择风险 28

建议：在 $97,100 挂买单。理由：
1. 位于HVN（高成交量节点），有结构支撑
2. 78%的成交概率足够高
3. 逆向选择风险35处于可接受范围
4. 比VPOC多省约$25/BTC

避免：在 $96,950 附近挂单（LVN区域，容易被快速穿越）
```

### 场景2：评估现有挂单

**用户问：** "我在96500挂了一个买单，成交机会大吗？"

**LLM执行：**

```json
{
  "name": "queue_fill_estimator_futures",
  "arguments": {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "price_levels": [96500],
    "qty": 0.1,
    "lookback_seconds": 60
  }
}
```

**分析回复：**

```
根据当前市场数据：

您在 $96,500 的买单情况：
- 队列前面约有 45.2 BTC（约 $4.36M）
- 当前消耗速率：0.15 BTC/秒
- 预计成交时间：中位数 5 分钟，95%概率需要 15 分钟
- 30秒成交概率：仅 8%
- 60秒成交概率：15%
- 逆向选择风险：22（较低）

结论：
✅ 逆向选择风险很低（价格远离市价）
⚠️ 但成交概率较低，需要价格大幅下跌才能成交
💡 如果希望更快成交，建议将价格提高到 $96,900 附近（VAL）
```

### 场景3：日内交易策略

```
用户: 我想做BTC的日内交易，帮我找几个好的限价单价位

LLM:
1. [调用 volume_profile_levels_futures, window=60] 分析1小时结构
2. [调用 volume_profile_levels_futures, window=240] 分析4小时结构  
3. 找出重叠的关键位
4. [调用 queue_fill_estimator_futures] 评估这些位置

回复：
基于多时间框架分析，以下是今日关键价位：

买入区（支撑）：
1. $96,800-96,850 (VAL + HVN 重叠，强支撑)
   - 成交概率高，建议主力买入位
   
2. $96,500 (次级支撑)
   - 成交概率中等，可设止损买单

卖出区（阻力）：
1. $97,450-97,500 (VAH + HVN)
   - 强阻力，可考虑部分止盈

2. $97,650 (单打印区上沿)
   - 如果突破此位可能快速上涨

避开区域：
- $96,950 (LVN) - 不要在此挂单
- $97,350 (LVN) - 价格会快速穿越
```

---

## 常见问题

### Q1: 启动时报错 "Invalid Binance configuration"

**原因：** 未设置 API 密钥环境变量

**解决：**
```bash
export BINANCE_API_KEY="你的密钥"
export BINANCE_API_SECRET="你的Secret"
```

### Q2: 返回 "INSUFFICIENT_DATA" 质量标志

**原因：** 查询的时间窗口内交易数据不足

**解决：**
- 对于 `queue_fill_estimator`：增加 `lookback_seconds`
- 对于 `volume_profile_levels`：增加 `window_minutes`
- 确认交易对是否活跃（只支持 BTCUSDT、ETHUSDT）

### Q3: SSE 连接超时

**原因：** 网络问题或防火墙阻止

**解决：**
```bash
# 检查端口是否监听
netstat -tlnp | grep 8000

# 检查防火墙
sudo ufw status
sudo ufw allow 8000
```

### Q4: 逆向选择分数很高

**原因：** 市场条件不利于你的订单方向

**建议：**
- 如果是买单：等待卖压减少（OBI转正）
- 如果是卖单：等待买压减少（OBI转负）
- 或选择更保守的价位

### Q5: 测试网和主网的区别

| 项目 | 测试网 | 主网 |
|------|--------|------|
| 资金 | 模拟资金，免费 | 真实资金 |
| 数据 | 可能与主网有差异 | 真实市场数据 |
| 用途 | 开发、测试 | 实盘交易 |
| 设置 | `BINANCE_TESTNET=true` | `BINANCE_TESTNET=false` |

### Q6: 如何提高限价单成交率？

根据工具分析结果：

1. **选择 HVN（高成交量节点）价位**
   - 这些位置有更多的市场参与者
   - 价格更容易在此停留

2. **避免 LVN（低成交量节点）**
   - 价格会快速穿越这些区域
   - 挂单可能被跳过

3. **关注逆向选择分数**
   - 分数 > 60 时考虑等待更好时机
   - 分数 < 40 时是相对安全的

4. **使用多个价位分批挂单**
   - 不要把所有数量挂在一个价位
   - 在 VAL、VPOC、HVN 分散挂单

---

## 技术支持

- **GitHub Issues**: [报告问题](https://github.com/AnalyticAce/binance-mcp-server/issues)
- **文档**: [在线文档](https://analyticace.github.io/binance-mcp-server/)
- **邮件**: dossehdosseh14@gmail.com
