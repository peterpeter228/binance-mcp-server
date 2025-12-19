#!/bin/bash
# Binance MCP Server 部署脚本
# 端口: 8003

set -e

echo "=========================================="
echo "Binance MCP Server 部署脚本"
echo "端口: 8003"
echo "=========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查是否以 root 运行 (部分命令需要 sudo)
check_sudo() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${YELLOW}提示: 某些命令可能需要 sudo 权限${NC}"
    fi
}

# 步骤 1: 安装依赖
install_dependencies() {
    echo -e "\n${GREEN}[1/6] 安装依赖...${NC}"
    pip3 install --user binance-mcp-server --upgrade
    echo -e "${GREEN}✓ 依赖安装完成${NC}"
}

# 步骤 2: 验证安装
verify_installation() {
    echo -e "\n${GREEN}[2/6] 验证安装...${NC}"
    if command -v binance-mcp-server &> /dev/null || [ -f ~/.local/bin/binance-mcp-server ]; then
        echo -e "${GREEN}✓ binance-mcp-server 已安装${NC}"
    else
        echo -e "${RED}✗ binance-mcp-server 未找到${NC}"
        echo "请确保 ~/.local/bin 在 PATH 中"
        exit 1
    fi
}

# 步骤 3: 配置环境变量
setup_env() {
    echo -e "\n${GREEN}[3/6] 配置环境变量...${NC}"
    
    # 检查是否已配置
    if [ -z "$BINANCE_API_KEY" ] || [ -z "$BINANCE_API_SECRET" ]; then
        echo -e "${YELLOW}请配置以下环境变量:${NC}"
        echo "export BINANCE_API_KEY='your_api_key'"
        echo "export BINANCE_API_SECRET='your_api_secret'"
        echo ""
        echo "可以添加到 ~/.bashrc 或 ~/.profile"
        echo -e "${YELLOW}或者在 systemd 服务文件中配置${NC}"
    else
        echo -e "${GREEN}✓ 环境变量已配置${NC}"
    fi
}

# 步骤 4: 安装 systemd 服务
install_service() {
    echo -e "\n${GREEN}[4/6] 安装 systemd 服务...${NC}"
    
    SERVICE_FILE="/etc/systemd/system/binance-mcp.service"
    
    if [ -f "./scripts/binance-mcp.service" ]; then
        echo "复制服务文件到 $SERVICE_FILE"
        sudo cp ./scripts/binance-mcp.service $SERVICE_FILE
        
        echo -e "${YELLOW}请编辑服务文件配置 API 密钥:${NC}"
        echo "sudo nano $SERVICE_FILE"
        echo ""
        
        sudo systemctl daemon-reload
        echo -e "${GREEN}✓ systemd 服务已安装${NC}"
    else
        echo -e "${YELLOW}服务文件不存在，跳过...${NC}"
    fi
}

# 步骤 5: 配置防火墙
setup_firewall() {
    echo -e "\n${GREEN}[5/6] 配置防火墙...${NC}"
    
    if command -v ufw &> /dev/null; then
        echo "开放端口 8003..."
        sudo ufw allow 8003/tcp
        echo -e "${GREEN}✓ UFW 防火墙已配置${NC}"
    elif command -v firewall-cmd &> /dev/null; then
        echo "开放端口 8003..."
        sudo firewall-cmd --permanent --add-port=8003/tcp
        sudo firewall-cmd --reload
        echo -e "${GREEN}✓ firewalld 防火墙已配置${NC}"
    else
        echo -e "${YELLOW}未检测到防火墙，请手动开放端口 8003${NC}"
    fi
}

# 步骤 6: 启动服务
start_service() {
    echo -e "\n${GREEN}[6/6] 启动服务...${NC}"
    
    echo "启用并启动服务..."
    sudo systemctl enable binance-mcp
    sudo systemctl start binance-mcp
    
    sleep 2
    
    if sudo systemctl is-active --quiet binance-mcp; then
        echo -e "${GREEN}✓ 服务已启动${NC}"
        echo ""
        echo "=========================================="
        echo -e "${GREEN}部署完成!${NC}"
        echo "=========================================="
        echo ""
        echo "服务地址: http://$(hostname -I | awk '{print $1}'):8003"
        echo "SSE 端点: http://$(hostname -I | awk '{print $1}'):8003/sse"
        echo ""
        echo "常用命令:"
        echo "  查看状态: sudo systemctl status binance-mcp"
        echo "  查看日志: sudo journalctl -u binance-mcp -f"
        echo "  重启服务: sudo systemctl restart binance-mcp"
        echo "  停止服务: sudo systemctl stop binance-mcp"
    else
        echo -e "${RED}✗ 服务启动失败${NC}"
        echo "查看日志: sudo journalctl -u binance-mcp -n 50"
    fi
}

# 显示手动启动说明
show_manual_start() {
    echo ""
    echo "=========================================="
    echo "手动启动说明 (无需 systemd)"
    echo "=========================================="
    echo ""
    echo "# 1. 设置环境变量"
    echo "export BINANCE_API_KEY='your_key'"
    echo "export BINANCE_API_SECRET='your_secret'"
    echo ""
    echo "# 2. 启动服务"
    echo "binance-mcp-server --transport sse --host 0.0.0.0 --port 8003"
    echo ""
    echo "# 或使用 nohup 后台运行"
    echo "nohup binance-mcp-server --transport sse --host 0.0.0.0 --port 8003 > /var/log/binance-mcp.log 2>&1 &"
}

# 主函数
main() {
    check_sudo
    
    case "${1:-full}" in
        "install")
            install_dependencies
            verify_installation
            ;;
        "service")
            install_service
            ;;
        "firewall")
            setup_firewall
            ;;
        "start")
            start_service
            ;;
        "manual")
            show_manual_start
            ;;
        "full"|*)
            install_dependencies
            verify_installation
            setup_env
            install_service
            setup_firewall
            start_service
            show_manual_start
            ;;
    esac
}

main "$@"
