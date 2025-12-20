# Ubuntu 部署指南 - Binance MCP Server

本指南详细说明如何在 Ubuntu 服务器上部署 Binance MCP Server，包括新增的限价单分析工具（queue_fill_estimator 和 volume_profile_levels）。

## 目录

1. [系统要求](#系统要求)
2. [快速部署](#快速部署)
3. [详细部署步骤](#详细部署步骤)
4. [Systemd 服务配置](#systemd-服务配置)
5. [Nginx 反向代理（可选）](#nginx-反向代理可选)
6. [Docker 部署（可选）](#docker-部署可选)
7. [测试验证](#测试验证)
8. [故障排除](#故障排除)

---

## 系统要求

- **操作系统**: Ubuntu 20.04 / 22.04 / 24.04 LTS
- **Python**: 3.10+
- **内存**: 最低 512MB，推荐 1GB+
- **网络**: 需要访问 Binance API (fapi.binance.com)

---

## 快速部署

```bash
# 一键安装脚本
curl -fsSL https://raw.githubusercontent.com/AnalyticAce/binance-mcp-server/main/scripts/install.sh | bash

# 或手动安装
pip3 install binance-mcp-server

# 配置环境变量
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"

# 启动服务器（SSE 模式）
binance-mcp-server --transport sse --host 0.0.0.0 --port 8000
```

---

## 详细部署步骤

### 步骤 1: 系统准备

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装 Python 和依赖
sudo apt install -y python3 python3-pip python3-venv git curl

# 验证 Python 版本 (需要 3.10+)
python3 --version
```

### 步骤 2: 创建专用用户（推荐）

```bash
# 创建服务用户
sudo useradd -r -s /bin/false -m -d /opt/binance-mcp binance-mcp

# 切换到该用户目录
sudo -u binance-mcp mkdir -p /opt/binance-mcp
```

### 步骤 3: 安装 Binance MCP Server

**方式 A: 从 PyPI 安装（推荐）**

```bash
# 创建虚拟环境
python3 -m venv /opt/binance-mcp/venv

# 激活虚拟环境
source /opt/binance-mcp/venv/bin/activate

# 安装
pip install binance-mcp-server

# 验证安装
binance-mcp-server --help
```

**方式 B: 从源码安装**

```bash
# 克隆仓库
cd /opt/binance-mcp
git clone https://github.com/AnalyticAce/binance-mcp-server.git
cd binance-mcp-server

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -e .
```

### 步骤 4: 配置环境变量

```bash
# 创建环境配置文件
sudo nano /opt/binance-mcp/.env
```

添加以下内容：

```bash
# Binance API 配置
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# 可选：使用测试网（开发时推荐）
BINANCE_TESTNET=false

# 可选：接收窗口（毫秒）
BINANCE_RECV_WINDOW=5000
```

设置权限：

```bash
sudo chmod 600 /opt/binance-mcp/.env
sudo chown binance-mcp:binance-mcp /opt/binance-mcp/.env
```

### 步骤 5: 测试运行

```bash
# 加载环境变量
source /opt/binance-mcp/.env

# 测试 SSE 模式
/opt/binance-mcp/venv/bin/binance-mcp-server --transport sse --host 127.0.0.1 --port 8000

# 在另一个终端测试
curl http://127.0.0.1:8000/health
```

---

## Systemd 服务配置

### 创建服务文件

```bash
sudo nano /etc/systemd/system/binance-mcp.service
```

添加以下内容：

```ini
[Unit]
Description=Binance MCP Server - Cryptocurrency Trading AI Agent
Documentation=https://github.com/AnalyticAce/binance-mcp-server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=binance-mcp
Group=binance-mcp
WorkingDirectory=/opt/binance-mcp

# 环境变量
EnvironmentFile=/opt/binance-mcp/.env

# 启动命令
ExecStart=/opt/binance-mcp/venv/bin/binance-mcp-server \
    --transport sse \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level INFO

# 重启策略
Restart=always
RestartSec=5
StartLimitBurst=3
StartLimitIntervalSec=60

# 安全配置
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/binance-mcp

# 资源限制
MemoryMax=1G
CPUQuota=80%

# 日志
StandardOutput=journal
StandardError=journal
SyslogIdentifier=binance-mcp

[Install]
WantedBy=multi-user.target
```

### 启用和启动服务

```bash
# 重载 systemd
sudo systemctl daemon-reload

# 启用开机自启
sudo systemctl enable binance-mcp

# 启动服务
sudo systemctl start binance-mcp

# 查看状态
sudo systemctl status binance-mcp

# 查看日志
sudo journalctl -u binance-mcp -f
```

### 常用管理命令

```bash
# 停止服务
sudo systemctl stop binance-mcp

# 重启服务
sudo systemctl restart binance-mcp

# 查看最近日志
sudo journalctl -u binance-mcp -n 100

# 查看今日日志
sudo journalctl -u binance-mcp --since today
```

---

## Nginx 反向代理（可选）

如果需要 HTTPS 或域名访问，配置 Nginx 反向代理。

### 安装 Nginx

```bash
sudo apt install -y nginx
```

### 创建配置文件

```bash
sudo nano /etc/nginx/sites-available/binance-mcp
```

添加以下内容：

```nginx
upstream binance_mcp {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name your-domain.com;  # 替换为你的域名

    # 重定向到 HTTPS（如果配置了 SSL）
    # return 301 https://$server_name$request_uri;

    location / {
        proxy_pass http://binance_mcp;
        proxy_http_version 1.1;
        
        # SSE 支持
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding off;
        
        # 标准代理头
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # 超时设置（SSE 需要长连接）
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 3600s;  # 1小时
    }
    
    # 健康检查端点
    location /health {
        proxy_pass http://binance_mcp/health;
    }
}

# HTTPS 配置（使用 Let's Encrypt）
server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    # SSL 安全配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    
    location / {
        proxy_pass http://binance_mcp;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
    }
}
```

### 启用配置

```bash
# 创建符号链接
sudo ln -s /etc/nginx/sites-available/binance-mcp /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 重载 Nginx
sudo systemctl reload nginx
```

### 配置 SSL（Let's Encrypt）

```bash
# 安装 Certbot
sudo apt install -y certbot python3-certbot-nginx

# 获取证书
sudo certbot --nginx -d your-domain.com

# 自动续期（已默认配置）
sudo certbot renew --dry-run
```

---

## Docker 部署（可选）

### 创建 Dockerfile

```bash
nano /opt/binance-mcp/Dockerfile
```

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
RUN pip install --no-cache-dir binance-mcp-server

# 非 root 用户
RUN useradd -r -s /bin/false appuser
USER appuser

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["binance-mcp-server", "--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose

```bash
nano /opt/binance-mcp/docker-compose.yml
```

```yaml
version: '3.8'

services:
  binance-mcp:
    build: .
    container_name: binance-mcp-server
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - BINANCE_API_KEY=${BINANCE_API_KEY}
      - BINANCE_API_SECRET=${BINANCE_API_SECRET}
      - BINANCE_TESTNET=${BINANCE_TESTNET:-false}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          cpus: '0.8'
          memory: 1G
```

### 运行 Docker

```bash
cd /opt/binance-mcp

# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

---

## 测试验证

### 1. 测试服务健康状态

```bash
curl http://localhost:8000/health
```

### 2. 测试限价单分析工具

**测试 queue_fill_estimator:**

```bash
curl -X POST http://localhost:8000/sse \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "queue_fill_estimator_futures",
      "arguments": {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "price_levels": [42000.0, 41990.0, 41980.0],
        "qty": 0.1,
        "lookback_seconds": 30
      }
    }
  }'
```

**测试 volume_profile_levels:**

```bash
curl -X POST http://localhost:8000/sse \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "volume_profile_levels_futures",
      "arguments": {
        "symbol": "BTCUSDT",
        "window_minutes": 240
      }
    }
  }'
```

### 3. MCP 客户端连接测试

在你的 MCP 客户端（如 Claude Desktop）配置：

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

---

## 故障排除

### 常见问题

**1. 端口被占用**

```bash
# 查看端口占用
sudo lsof -i :8000

# 杀死占用进程
sudo kill -9 <PID>
```

**2. API 连接失败**

```bash
# 测试 Binance API 连接
curl https://fapi.binance.com/fapi/v1/time

# 检查防火墙
sudo ufw status
sudo ufw allow 8000/tcp
```

**3. 权限问题**

```bash
# 修复权限
sudo chown -R binance-mcp:binance-mcp /opt/binance-mcp
sudo chmod -R 755 /opt/binance-mcp
sudo chmod 600 /opt/binance-mcp/.env
```

**4. 内存不足**

```bash
# 检查内存
free -h

# 添加 swap（如果需要）
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

**5. 查看详细日志**

```bash
# 服务日志
sudo journalctl -u binance-mcp -f --no-pager

# 带调试级别启动
binance-mcp-server --transport sse --host 0.0.0.0 --port 8000 --log-level DEBUG
```

### 日志分析

```bash
# 查看错误日志
sudo journalctl -u binance-mcp -p err

# 查看最近 1 小时日志
sudo journalctl -u binance-mcp --since "1 hour ago"

# 导出日志
sudo journalctl -u binance-mcp > binance-mcp.log
```

---

## 安全建议

1. **使用防火墙限制访问**
   ```bash
   sudo ufw allow from your-ip to any port 8000
   ```

2. **定期更新**
   ```bash
   pip install --upgrade binance-mcp-server
   ```

3. **监控服务**
   ```bash
   # 安装监控工具
   sudo apt install -y htop iotop
   ```

4. **备份配置**
   ```bash
   cp /opt/binance-mcp/.env /opt/binance-mcp/.env.backup
   ```

---

## 联系支持

- **GitHub Issues**: https://github.com/AnalyticAce/binance-mcp-server/issues
- **文档**: https://analyticace.github.io/binance-mcp-server/

---

*最后更新: 2024-12*
