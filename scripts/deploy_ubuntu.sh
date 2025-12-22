#!/bin/bash
#
# Binance MCP Server - Ubuntu Deployment Script
# 
# Usage:
#   sudo bash scripts/deploy_ubuntu.sh
#
# This script will:
#   1. Install Python 3.10+ and dependencies
#   2. Create a dedicated user for the service
#   3. Install the binance-mcp-server package
#   4. Create systemd service files (SSE and HTTP transports)
#   5. Configure environment variables
#   6. Start and enable the service
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SERVICE_USER="binance-mcp"
SERVICE_GROUP="binance-mcp"
INSTALL_DIR="/opt/binance-mcp-server"
CONFIG_DIR="/etc/binance-mcp"
LOG_DIR="/var/log/binance-mcp"
VENV_DIR="${INSTALL_DIR}/venv"

# Default settings
DEFAULT_PORT=8000
DEFAULT_HOST="0.0.0.0"
DEFAULT_TRANSPORT="sse"

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}  Binance MCP Server - Ubuntu Deployment${NC}"
echo -e "${BLUE}================================================${NC}"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root (use sudo)${NC}"
   exit 1
fi

# Function to print status
print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[i]${NC} $1"
}

# Step 1: Install system dependencies
echo ""
echo -e "${BLUE}Step 1: Installing system dependencies...${NC}"

apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv curl git

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
print_status "Python ${PYTHON_VERSION} installed"

# Step 2: Create service user
echo ""
echo -e "${BLUE}Step 2: Creating service user...${NC}"

if id "${SERVICE_USER}" &>/dev/null; then
    print_warning "User ${SERVICE_USER} already exists"
else
    useradd --system --shell /bin/false --home-dir ${INSTALL_DIR} ${SERVICE_USER}
    print_status "Created user ${SERVICE_USER}"
fi

# Step 3: Create directories
echo ""
echo -e "${BLUE}Step 3: Creating directories...${NC}"

mkdir -p ${INSTALL_DIR}
mkdir -p ${CONFIG_DIR}
mkdir -p ${LOG_DIR}

chown -R ${SERVICE_USER}:${SERVICE_GROUP} ${INSTALL_DIR}
chown -R ${SERVICE_USER}:${SERVICE_GROUP} ${LOG_DIR}

print_status "Created ${INSTALL_DIR}"
print_status "Created ${CONFIG_DIR}"
print_status "Created ${LOG_DIR}"

# Step 4: Create Python virtual environment
echo ""
echo -e "${BLUE}Step 4: Setting up Python virtual environment...${NC}"

python3 -m venv ${VENV_DIR}
source ${VENV_DIR}/bin/activate

# Upgrade pip
pip install --upgrade pip -q

# Install the package
pip install binance-mcp-server websockets -q

print_status "Virtual environment created at ${VENV_DIR}"
print_status "binance-mcp-server installed"

deactivate

# Step 5: Create environment configuration file
echo ""
echo -e "${BLUE}Step 5: Creating configuration file...${NC}"

if [[ ! -f "${CONFIG_DIR}/binance-mcp.env" ]]; then
    cat > ${CONFIG_DIR}/binance-mcp.env << 'EOF'
# Binance MCP Server Configuration
# Edit this file with your API credentials

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
MCP_LOG_LEVEL=INFO
EOF
    chmod 600 ${CONFIG_DIR}/binance-mcp.env
    chown ${SERVICE_USER}:${SERVICE_GROUP} ${CONFIG_DIR}/binance-mcp.env
    print_status "Created ${CONFIG_DIR}/binance-mcp.env"
    print_warning "Please edit ${CONFIG_DIR}/binance-mcp.env with your API credentials!"
else
    print_warning "Configuration file already exists, skipping"
fi

# Step 6: Create systemd service file for SSE transport
echo ""
echo -e "${BLUE}Step 6: Creating systemd service files...${NC}"

cat > /etc/systemd/system/binance-mcp.service << EOF
[Unit]
Description=Binance MCP Server (SSE Transport)
Documentation=https://github.com/AnalyticAce/binance-mcp-server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}

# Load environment variables
EnvironmentFile=${CONFIG_DIR}/binance-mcp.env

# Start command
ExecStart=${VENV_DIR}/bin/binance-mcp-server \
    --transport \${MCP_TRANSPORT} \
    --host \${MCP_HOST} \
    --port \${MCP_PORT} \
    --log-level \${MCP_LOG_LEVEL}

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

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=binance-mcp

[Install]
WantedBy=multi-user.target
EOF

print_status "Created /etc/systemd/system/binance-mcp.service"

# Create HTTP transport service (alternative)
cat > /etc/systemd/system/binance-mcp-http.service << EOF
[Unit]
Description=Binance MCP Server (HTTP Transport)
Documentation=https://github.com/AnalyticAce/binance-mcp-server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}

# Load environment variables
EnvironmentFile=${CONFIG_DIR}/binance-mcp.env

# Start command with HTTP transport
ExecStart=${VENV_DIR}/bin/binance-mcp-server \
    --transport streamable-http \
    --host \${MCP_HOST} \
    --port \${MCP_PORT} \
    --log-level \${MCP_LOG_LEVEL}

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

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=binance-mcp-http

[Install]
WantedBy=multi-user.target
EOF

print_status "Created /etc/systemd/system/binance-mcp-http.service"

# Step 7: Create helper scripts
echo ""
echo -e "${BLUE}Step 7: Creating helper scripts...${NC}"

# Create status check script
cat > /usr/local/bin/binance-mcp-status << 'EOF'
#!/bin/bash
echo "=== Binance MCP Server Status ==="
echo ""
echo "Service Status:"
systemctl status binance-mcp --no-pager -l 2>/dev/null || echo "SSE service not running"
echo ""
echo "Recent Logs:"
journalctl -u binance-mcp -n 20 --no-pager
EOF
chmod +x /usr/local/bin/binance-mcp-status

# Create log viewer script
cat > /usr/local/bin/binance-mcp-logs << 'EOF'
#!/bin/bash
journalctl -u binance-mcp -f
EOF
chmod +x /usr/local/bin/binance-mcp-logs

print_status "Created /usr/local/bin/binance-mcp-status"
print_status "Created /usr/local/bin/binance-mcp-logs"

# Step 8: Reload systemd
echo ""
echo -e "${BLUE}Step 8: Reloading systemd...${NC}"

systemctl daemon-reload
print_status "Systemd daemon reloaded"

# Print summary
echo ""
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo -e "${GREEN}================================================${NC}"
echo ""
echo -e "${YELLOW}IMPORTANT: Before starting the service, edit the configuration:${NC}"
echo ""
echo -e "  ${BLUE}sudo nano ${CONFIG_DIR}/binance-mcp.env${NC}"
echo ""
echo "  Set your Binance API credentials:"
echo "    BINANCE_API_KEY=your_actual_api_key"
echo "    BINANCE_API_SECRET=your_actual_api_secret"
echo ""
echo -e "${BLUE}Service Management Commands:${NC}"
echo ""
echo "  # Start the service (SSE transport)"
echo "  sudo systemctl start binance-mcp"
echo ""
echo "  # Enable auto-start on boot"
echo "  sudo systemctl enable binance-mcp"
echo ""
echo "  # Check status"
echo "  sudo systemctl status binance-mcp"
echo ""
echo "  # View logs"
echo "  sudo journalctl -u binance-mcp -f"
echo ""
echo "  # Restart service"
echo "  sudo systemctl restart binance-mcp"
echo ""
echo "  # Stop service"
echo "  sudo systemctl stop binance-mcp"
echo ""
echo -e "${BLUE}Alternative HTTP Transport:${NC}"
echo ""
echo "  # Use HTTP transport instead of SSE"
echo "  sudo systemctl start binance-mcp-http"
echo ""
echo -e "${BLUE}Helper Commands:${NC}"
echo ""
echo "  binance-mcp-status  # Quick status check"
echo "  binance-mcp-logs    # Follow logs"
echo ""
echo -e "${BLUE}Endpoints (after starting):${NC}"
echo ""
echo "  SSE:  http://your-server-ip:8000/sse"
echo "  HTTP: http://your-server-ip:8000/mcp"
echo ""
echo -e "${GREEN}Done!${NC}"
