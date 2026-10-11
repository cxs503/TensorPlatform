#!/bin/bash

# 示例组织AI赋能研发平台 - 停止脚本

echo "========================================"
echo "  示例组织AI赋能研发平台停止脚本"
echo "========================================"
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 停止后端服务
stop_backend() {
    if [ -f "logs/backend.pid" ]; then
        PID=$(cat logs/backend.pid)
        if ps -p $PID > /dev/null 2>&1; then
            kill $PID 2>/dev/null
            echo -e "${GREEN}✓${NC} 后端服务已停止 (PID: $PID)"
        else
            echo -e "${YELLOW}!${NC} 后端服务未运行"
        fi
        rm -f logs/backend.pid
    else
        # 尝试通过端口查找并停止
        if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
            lsof -Pi :8000 -sTCP:LISTEN -t | xargs kill -9 2>/dev/null
            echo -e "${GREEN}✓${NC} 后端服务已停止"
        else
            echo -e "${YELLOW}!${NC} 后端服务未运行"
        fi
    fi
}

# 停止前端服务
stop_frontend() {
    if [ -f "logs/frontend.pid" ]; then
        PID=$(cat logs/frontend.pid)
        if ps -p $PID > /dev/null 2>&1; then
            kill $PID 2>/dev/null
            echo -e "${GREEN}✓${NC} 前端服务已停止 (PID: $PID)"
        else
            echo -e "${YELLOW}!${NC} 前端服务未运行"
        fi
        rm -f logs/frontend.pid
    else
        # 尝试通过端口查找并停止
        if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
            lsof -Pi :3000 -sTCP:LISTEN -t | xargs kill -9 2>/dev/null
            echo -e "${GREEN}✓${NC} 前端服务已停止"
        else
            echo -e "${YELLOW}!${NC} 前端服务未运行"
        fi
    fi
}

# 主流程
echo "正在停止服务..."
echo ""

stop_backend
stop_frontend

echo ""
echo "========================================"
echo -e "${GREEN}  所有服务已停止${NC}"
echo "========================================"
