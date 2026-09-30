#!/bin/bash

# 示例组织AI赋能研发平台 - 启动脚本

set -e

echo "========================================"
echo "  示例组织AI赋能研发平台启动脚本"
echo "========================================"
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 检查Python版本
check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        echo -e "${GREEN}✓${NC} Python版本: $PYTHON_VERSION"
    else
        echo -e "${RED}✗${NC} 未找到Python3，请先安装Python 3.9+"
        exit 1
    fi
}

# 检查Node.js版本
check_node() {
    if command -v node &> /dev/null; then
        NODE_VERSION=$(node --version)
        echo -e "${GREEN}✓${NC} Node.js版本: $NODE_VERSION"
    else
        echo -e "${YELLOW}!${NC} 未找到Node.js，前端服务可能无法启动"
    fi
}

# 安装后端依赖
install_backend_deps() {
    echo ""
    echo -e "${BLUE}[1/4] 安装后端依赖...${NC}"
    cd backend

    if [ -f "requirements.txt" ]; then
        pip3 install -r requirements.txt -q
        echo -e "${GREEN}✓${NC} 后端依赖安装完成"
    else
        echo -e "${YELLOW}!${NC} 未找到requirements.txt"
    fi

    cd ..
}

# 安装前端依赖
install_frontend_deps() {
    echo ""
    echo -e "${BLUE}[2/4] 安装前端依赖...${NC}"
    cd frontend

    if [ -f "package.json" ]; then
        npm install -q
        echo -e "${GREEN}✓${NC} 前端依赖安装完成"
    else
        echo -e "${YELLOW}!${NC} 未找到package.json，跳过前端依赖安装"
    fi

    cd ..
}

# 启动后端服务
start_backend() {
    echo ""
    echo -e "${BLUE}[3/4] 启动后端服务...${NC}"
    cd backend

    # 检查端口是否被占用
    if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "${YELLOW}!${NC} 端口8000已被占用，尝试停止现有服务..."
        lsof -Pi :8000 -sTCP:LISTEN -t | xargs kill -9 2>/dev/null || true
        sleep 2
    fi

    # 启动服务
    nohup uvicorn main:app --host 0.0.0.0 --port 8000 --reload > ../logs/backend.log 2>&1 &
    BACKEND_PID=$!
    echo $BACKEND_PID > ../logs/backend.pid

    # 等待服务启动
    sleep 3

    if curl -s http://localhost:8000/api/v1/health > /dev/null; then
        echo -e "${GREEN}✓${NC} 后端服务已启动 (PID: $BACKEND_PID)"
        echo -e "  - API文档: http://localhost:8000/docs"
        echo -e "  - 健康检查: http://localhost:8000/api/v1/health"
    else
        echo -e "${RED}✗${NC} 后端服务启动失败，请检查日志"
        exit 1
    fi

    cd ..
}

# 启动前端服务
start_frontend() {
    echo ""
    echo -e "${BLUE}[4/4] 启动前端服务...${NC}"
    cd frontend

    # 检查端口是否被占用
    if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "${YELLOW}!${NC} 端口3000已被占用，尝试停止现有服务..."
        lsof -Pi :3000 -sTCP:LISTEN -t | xargs kill -9 2>/dev/null || true
        sleep 2
    fi

    # 启动服务
    nohup python3 -m http.server 3000 > ../logs/frontend.log 2>&1 &
    FRONTEND_PID=$!
    echo $FRONTEND_PID > ../logs/frontend.pid

    sleep 2

    echo -e "${GREEN}✓${NC} 前端服务已启动 (PID: $FRONTEND_PID)"
    echo -e "  - 访问地址: http://localhost:3000"

    cd ..
}

# 生成虚拟数据
generate_virtual_data() {
    echo ""
    echo -e "${BLUE}[可选] 生成虚拟数据...${NC}"
    read -p "是否生成虚拟测试数据? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd virtual-data
        python3 data_generator.py
        cd ..
    fi
}

# 创建日志目录
mkdir -p logs

# 主流程
echo ""
echo "检查环境..."
check_python
check_node

echo ""
echo "安装依赖..."
install_backend_deps
install_frontend_deps

echo ""
echo "启动服务..."
start_backend
start_frontend

echo ""
generate_virtual_data

echo ""
echo "========================================"
echo -e "${GREEN}  所有服务已启动成功！${NC}"
echo "========================================"
echo ""
echo "服务访问地址:"
echo "  - 前端界面: http://localhost:3000"
echo "  - API文档: http://localhost:8000/docs"
echo "  - API端点: http://localhost:8000/api/v1"
echo ""
echo "日志文件:"
echo "  - 后端日志: logs/backend.log"
echo "  - 前端日志: logs/frontend.log"
echo ""
echo "停止服务:"
echo "  ./stop.sh"
echo ""
