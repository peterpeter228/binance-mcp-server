# Ubuntu Deployment Guide (Source Code Installation)

This guide covers deploying the Binance MCP Server on Ubuntu as a systemd service using source code installation (`pip install -e .`).

## Quick Deployment (Automated)

```bash
# Clone the repository
git clone https://github.com/AnalyticAce/binance-mcp-server.git
cd binance-mcp-server

# Run the deployment script
sudo bash scripts/deploy_ubuntu.sh

# Edit configuration with your API keys
sudo nano /etc/binance-mcp/binance-mcp.env

# Start the service
sudo systemctl start binance-mcp
sudo systemctl enable binance-mcp
```

## Manual Deployment

### Step 1: System Requirements

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Python 3.10+ and Git
sudo apt-get install -y python3 python3-pip python3-venv git curl
```

### Step 2: Create Service User

```bash
# Create a dedicated user for the service
sudo useradd --system --shell /bin/false --home-dir /opt/binance-mcp-server binance-mcp
```

### Step 3: Setup Directories

```bash
# Create installation directories
sudo mkdir -p /opt/binance-mcp-server/src
sudo mkdir -p /opt/binance-mcp-server/venv
sudo mkdir -p /etc/binance-mcp
sudo mkdir -p /var/log/binance-mcp
```

### Step 4: Clone Source Code

```bash
# Clone the repository
sudo git clone https://github.com/AnalyticAce/binance-mcp-server.git /opt/binance-mcp-server/src

# Set ownership
sudo chown -R binance-mcp:binance-mcp /opt/binance-mcp-server
sudo chown -R binance-mcp:binance-mcp /var/log/binance-mcp
```

### Step 5: Create Virtual Environment and Install

```bash
# Create virtual environment
sudo python3 -m venv /opt/binance-mcp-server/venv

# Install from source (editable mode)
sudo /opt/binance-mcp-server/venv/bin/pip install --upgrade pip
sudo /opt/binance-mcp-server/venv/bin/pip install -e /opt/binance-mcp-server/src
sudo /opt/binance-mcp-server/venv/bin/pip install websockets
```

### Step 6: Create Configuration File

Create `/etc/binance-mcp/binance-mcp.env`:

```bash
sudo nano /etc/binance-mcp/binance-mcp.env
```

Add the following content:

```ini
# Binance MCP Server Configuration

# Required: Binance API credentials
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Optional: Use testnet (true/false)
BINANCE_TESTNET=false

# Optional: Receive window for API requests (milliseconds)
BINANCE_RECV_WINDOW=5000

# Server settings
MCP_HOST=0.0.0.0
MCP_PORT=8000
MCP_TRANSPORT=sse
```

Secure the configuration file:

```bash
sudo chmod 600 /etc/binance-mcp/binance-mcp.env
sudo chown binance-mcp:binance-mcp /etc/binance-mcp/binance-mcp.env
```

### Step 7: Create Systemd Service File

Create `/etc/systemd/system/binance-mcp.service`:

```bash
sudo nano /etc/systemd/system/binance-mcp.service
```

Add the following content:

```ini
[Unit]
Description=Binance MCP Server (SSE Transport)
Documentation=https://github.com/AnalyticAce/binance-mcp-server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=binance-mcp
Group=binance-mcp
WorkingDirectory=/opt/binance-mcp-server/src

# Load environment variables
EnvironmentFile=/etc/binance-mcp/binance-mcp.env

# Start command using python -m (source installation)
ExecStart=/opt/binance-mcp-server/venv/bin/python -m binance_mcp_server.cli \
    --transport ${MCP_TRANSPORT} \
    --host ${MCP_HOST} \
    --port ${MCP_PORT}

# Restart policy
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictRealtime=true
RestrictSUIDSGID=true

# Allow write to source directory for editable install
ReadWritePaths=/opt/binance-mcp-server/src

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=binance-mcp

[Install]
WantedBy=multi-user.target
```

### Step 8: Enable and Start the Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable binance-mcp

# Start the service
sudo systemctl start binance-mcp

# Check status
sudo systemctl status binance-mcp
```

## Service Management

### Basic Commands

```bash
# Start service
sudo systemctl start binance-mcp

# Stop service
sudo systemctl stop binance-mcp

# Restart service
sudo systemctl restart binance-mcp

# Check status
sudo systemctl status binance-mcp

# Enable auto-start on boot
sudo systemctl enable binance-mcp

# Disable auto-start
sudo systemctl disable binance-mcp
```

### Viewing Logs

```bash
# View recent logs
sudo journalctl -u binance-mcp -n 50

# Follow logs in real-time
sudo journalctl -u binance-mcp -f

# View logs since last boot
sudo journalctl -u binance-mcp -b

# View logs for specific time range
sudo journalctl -u binance-mcp --since "2024-01-01 00:00:00" --until "2024-01-01 23:59:59"

# View only error logs
sudo journalctl -u binance-mcp -p err
```

### Changing Configuration

```bash
# Edit configuration
sudo nano /etc/binance-mcp/binance-mcp.env

# Restart to apply changes
sudo systemctl restart binance-mcp
```

## Updating from Source

### Manual Update

```bash
# Stop the service
sudo systemctl stop binance-mcp

# Update source code
cd /opt/binance-mcp-server/src
sudo git fetch origin
sudo git reset --hard origin/main

# Reinstall package
sudo /opt/binance-mcp-server/venv/bin/pip install -e .

# Start the service
sudo systemctl start binance-mcp
```

### Using Update Script

If you used the automated deployment script, a helper script is available:

```bash
sudo binance-mcp-update
```

## HTTP Transport Configuration

If you prefer HTTP transport over SSE, create an alternative service file:

```bash
sudo nano /etc/systemd/system/binance-mcp-http.service
```

```ini
[Unit]
Description=Binance MCP Server (HTTP Transport)
Documentation=https://github.com/AnalyticAce/binance-mcp-server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=binance-mcp
Group=binance-mcp
WorkingDirectory=/opt/binance-mcp-server/src

EnvironmentFile=/etc/binance-mcp/binance-mcp.env

ExecStart=/opt/binance-mcp-server/venv/bin/python -m binance_mcp_server.cli \
    --transport streamable-http \
    --host ${MCP_HOST} \
    --port ${MCP_PORT}

Restart=always
RestartSec=10

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/binance-mcp-server/src

StandardOutput=journal
StandardError=journal
SyslogIdentifier=binance-mcp-http

[Install]
WantedBy=multi-user.target
```

Then use:

```bash
sudo systemctl daemon-reload
sudo systemctl start binance-mcp-http
```

## Directory Structure

After deployment, the directory structure will be:

```
/opt/binance-mcp-server/
├── src/                          # Source code (git repository)
│   ├── binance_mcp_server/       # Python package
│   ├── pyproject.toml
│   └── ...
└── venv/                         # Python virtual environment
    ├── bin/
    │   ├── python
    │   └── pip
    └── lib/

/etc/binance-mcp/
└── binance-mcp.env              # Configuration file (API keys)

/var/log/binance-mcp/            # Log directory (if needed)
```

## Nginx Reverse Proxy (Optional)

For production deployments, it's recommended to use Nginx as a reverse proxy.

### Install Nginx

```bash
sudo apt-get install -y nginx
```

### Create Nginx Configuration

```bash
sudo nano /etc/nginx/sites-available/binance-mcp
```

```nginx
upstream binance_mcp {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name your-domain.com;  # Replace with your domain

    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;  # Replace with your domain

    # SSL certificates (use Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;

    # SSE endpoint
    location /sse {
        proxy_pass http://binance_mcp/sse;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # SSE-specific settings
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        chunked_transfer_encoding off;
    }

    # HTTP endpoint
    location /mcp {
        proxy_pass http://binance_mcp/mcp;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Health check endpoint
    location /health {
        proxy_pass http://binance_mcp/health;
        proxy_http_version 1.1;
    }
}
```

### Enable the Site

```bash
# Enable the site
sudo ln -s /etc/nginx/sites-available/binance-mcp /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

### SSL with Let's Encrypt

```bash
# Install Certbot
sudo apt-get install -y certbot python3-certbot-nginx

# Get SSL certificate
sudo certbot --nginx -d your-domain.com

# Auto-renewal is configured automatically
```

## Firewall Configuration

```bash
# Allow HTTP and HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# If not using Nginx, allow direct access to MCP server
sudo ufw allow 8000/tcp

# Enable firewall
sudo ufw enable
```

## Troubleshooting

### Service Won't Start

```bash
# Check service status
sudo systemctl status binance-mcp -l

# Check logs for errors
sudo journalctl -u binance-mcp -n 100 --no-pager

# Verify configuration file
sudo cat /etc/binance-mcp/binance-mcp.env

# Test manual start
sudo -u binance-mcp /opt/binance-mcp-server/venv/bin/python -m binance_mcp_server.cli \
    --transport sse --host 0.0.0.0 --port 8000
```

### Module Not Found Error

If you see `ModuleNotFoundError`, reinstall the package:

```bash
sudo /opt/binance-mcp-server/venv/bin/pip install -e /opt/binance-mcp-server/src
```

### Permission Errors

```bash
# Fix ownership
sudo chown -R binance-mcp:binance-mcp /opt/binance-mcp-server
sudo chown -R binance-mcp:binance-mcp /var/log/binance-mcp
sudo chown binance-mcp:binance-mcp /etc/binance-mcp/binance-mcp.env
sudo chmod 600 /etc/binance-mcp/binance-mcp.env
```

### Connection Refused

```bash
# Check if service is listening
sudo ss -tlnp | grep 8000

# Check firewall
sudo ufw status

# Test local connection
curl http://localhost:8000/health
```

## Uninstallation

```bash
# Stop and disable service
sudo systemctl stop binance-mcp
sudo systemctl disable binance-mcp

# Remove service files
sudo rm /etc/systemd/system/binance-mcp.service
sudo rm /etc/systemd/system/binance-mcp-http.service
sudo systemctl daemon-reload

# Remove installation
sudo rm -rf /opt/binance-mcp-server
sudo rm -rf /etc/binance-mcp
sudo rm -rf /var/log/binance-mcp

# Remove user (optional)
sudo userdel binance-mcp

# Remove helper scripts
sudo rm -f /usr/local/bin/binance-mcp-status
sudo rm -f /usr/local/bin/binance-mcp-logs
sudo rm -f /usr/local/bin/binance-mcp-update
```
