#!/bin/bash
# Binance MCP Server - Ubuntu 安装脚本
# 支持 Ubuntu 22.04+ (PEP 668 兼容)

set -e

echo "=========================================="
echo "Binance MCP Server 安装脚本"
echo "=========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${GREEN}项目目录: $PROJECT_DIR${NC}"

# 1. 安装系统依赖
echo -e "\n${YELLOW}[1/5] 安装系统依赖...${NC}"
sudo apt update
sudo apt install -y python3-venv python3-full python3-pip

# 2. 创建虚拟环境
echo -e "\n${YELLOW}[2/5] 创建虚拟环境...${NC}"
cd "$PROJECT_DIR"

if [ -d "venv" ]; then
    echo "虚拟环境已存在，跳过创建"
else
    python3 -m venv venv
    echo "虚拟环境创建成功"
fi

# 3. 激活虚拟环境并安装项目
echo -e "\n${YELLOW}[3/5] 安装项目依赖...${NC}"
source venv/bin/activate
pip install --upgrade pip
pip install -e .

# 4. 验证安装
echo -e "\n${YELLOW}[4/5] 验证安装...${NC}"
if command -v binance-mcp-server &> /dev/null; then
    echo -e "${GREEN}✓ binance-mcp-server 命令可用${NC}"
    binance-mcp-server --help | head -5
else
    echo -e "${RED}✗ 安装验证失败${NC}"
    exit 1
fi

# 5. 创建启动脚本
echo -e "\n${YELLOW}[5/5] 创建启动脚本...${NC}"

cat > "$PROJECT_DIR/start-server.sh" << 'EOF'
#!/bin/bash
# Binance MCP Server 启动脚本

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/venv/bin/activate"

# 默认参数
TRANSPORT="${TRANSPORT:-sse}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "启动 Binance MCP Server..."
echo "传输方式: $TRANSPORT"
echo "监听地址: $HOST:$PORT"
echo "日志级别: $LOG_LEVEL"
echo ""

exec binance-mcp-server \
    --transport "$TRANSPORT" \
    --host "$HOST" \
    --port "$PORT" \
    --log-level "$LOG_LEVEL"
EOF

chmod +x "$PROJECT_DIR/start-server.sh"

echo -e "\n${GREEN}=========================================="
echo "安装完成!"
echo "==========================================${NC}"
echo ""
echo "使用方法:"
echo ""
echo "1. 设置环境变量:"
echo "   export BINANCE_API_KEY=\"你的API密钥\""
echo "   export BINANCE_API_SECRET=\"你的API Secret\""
echo "   export BINANCE_TESTNET=\"true\"  # 可选，使用测试网"
echo ""
echo "2. 启动服务器:"
echo "   cd $PROJECT_DIR"
echo "   ./start-server.sh"
echo ""
echo "   或指定参数:"
echo "   TRANSPORT=sse PORT=8000 ./start-server.sh"
echo ""
echo "3. 手动启动 (需先激活虚拟环境):"
echo "   source $PROJECT_DIR/venv/bin/activate"
echo "   binance-mcp-server --transport sse --port 8000 --host 0.0.0.0"
echo ""
echo "4. 设置为系统服务:"
echo "   sudo cp $PROJECT_DIR/scripts/binance-mcp.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable binance-mcp"
echo "   sudo systemctl start binance-mcp"
echo ""
