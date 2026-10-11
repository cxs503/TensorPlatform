#!/bin/bash
# 开放数字化底座 - Docker 快速部署脚本
# 用法: ./deploy.sh [command]

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查依赖
check_dependencies() {
    log_info "检查依赖..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi

    log_success "依赖检查通过"
}

# 初始化环境
init_env() {
    log_info "初始化环境..."

    if [ ! -f .env ]; then
        log_info "创建 .env 文件..."
        cp .env.docker.example .env

        # 生成随机密钥
        JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
        REFRESH_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")

        # 更新 .env 文件
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|your-jwt-secret-key-change-in-production-use-64chars|$JWT_SECRET|g" .env
            sed -i '' "s|your-refresh-secret-key-change-in-production-use-64chars|$REFRESH_SECRET|g" .env
        else
            sed -i "s|your-jwt-secret-key-change-in-production-use-64chars|$JWT_SECRET|g" .env
            sed -i "s|your-refresh-secret-key-change-in-production-use-64chars|$REFRESH_SECRET|g" .env
        fi

        log_success "JWT 密钥已自动生成"
    else
        log_warning ".env 文件已存在，跳过初始化"
    fi
}

# 构建镜像
build() {
    log_info "构建 Docker 镜像..."
    docker-compose build --parallel
    log_success "镜像构建完成"
}

# 启动服务
start() {
    log_info "启动服务..."
    docker-compose up -d

    log_info "等待服务启动..."
    sleep 10

    # 检查服务状态
    if docker-compose ps | grep -q "Up"; then
        log_success "服务启动成功！"
        echo ""
        echo "访问地址:"
        echo "  前端:     http://localhost:3000"
        echo "  后端API:  http://localhost:8001"
        echo "  API文档:  http://localhost:8001/docs"
        echo "  MinIO:    http://localhost:9001"
    else
        log_error "服务启动失败，请检查日志"
        docker-compose logs
        exit 1
    fi
}

# 停止服务
stop() {
    log_info "停止服务..."
    docker-compose down
    log_success "服务已停止"
}

# 重启服务
restart() {
    stop
    start
}

# 查看状态
status() {
    echo ""
    echo "服务状态:"
    docker-compose ps
    echo ""

    # 健康检查
    log_info "健康检查..."

    BACKEND_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/api/v1/health 2>/dev/null || echo "000")
    FRONTEND_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/health 2>/dev/null || echo "000")

    if [ "$BACKEND_HEALTH" == "200" ]; then
        log_success "后端服务: 健康 ($BACKEND_HEALTH)"
    else
        log_warning "后端服务: 异常 ($BACKEND_HEALTH)"
    fi

    if [ "$FRONTEND_HEALTH" == "200" ]; then
        log_success "前端服务: 健康 ($FRONTEND_HEALTH)"
    else
        log_warning "前端服务: 异常 ($FRONTEND_HEALTH)"
    fi
}

# 查看日志
logs() {
    docker-compose logs -f --tail=100 "$@"
}

# 清理
clean() {
    log_warning "这将删除所有未使用的 Docker 资源"
    read -p "确定要继续吗？(y/N): " confirm
    if [ "$confirm" == "y" ] || [ "$confirm" == "Y" ]; then
        docker system prune -f
        docker image prune -f
        log_success "清理完成"
    else
        log_info "已取消"
    fi
}

# 完全清理（包括数据）
clean_all() {
    log_error "警告: 这将删除所有容器、镜像和数据卷！"
    read -p "确定要继续吗？(yes/N): " confirm
    if [ "$confirm" == "yes" ]; then
        docker-compose down -v
        docker system prune -a -f --volumes
        log_success "完全清理完成"
    else
        log_info "已取消"
    fi
}

# 备份数据库
backup() {
    log_info "备份数据库..."
    mkdir -p backups

    BACKUP_FILE="backups/backup_$(date +%Y%m%d_%H%M%S).sql"
    docker-compose exec -T postgres pg_dump -U foundation open_digital_foundation > "$BACKUP_FILE"

    log_success "备份完成: $BACKUP_FILE"
}

# 恢复数据库
restore() {
    if [ -z "$1" ]; then
        log_error "请指定备份文件"
        echo "用法: $0 restore <backup_file.sql>"
        echo ""
        echo "可用备份:"
        ls -la backups/*.sql 2>/dev/null || echo "  无备份文件"
        exit 1
    fi

    if [ ! -f "$1" ]; then
        log_error "文件不存在: $1"
        exit 1
    fi

    log_info "恢复数据库..."
    cat "$1" | docker-compose exec -T postgres psql -U foundation open_digital_foundation
    log_success "恢复完成"
}

# 进入容器
shell() {
    SERVICE=${1:-backend}
    docker-compose exec "$SERVICE" bash
}

# 帮助信息
help() {
    echo "开放数字化底座 Docker 部署脚本"
    echo ""
    echo "用法: $0 [command]"
    echo ""
    echo "命令:"
    echo "  init        初始化环境（创建 .env 文件）"
    echo "  build       构建 Docker 镜像"
    echo "  start       启动所有服务"
    echo "  stop        停止所有服务"
    echo "  restart     重启所有服务"
    echo "  status      查看服务状态"
    echo "  logs        查看日志 (可选: 服务名)"
    echo "  shell       进入容器 (可选: 服务名，默认 backend)"
    echo "  backup      备份数据库"
    echo "  restore     恢复数据库 (需要: 备份文件路径)"
    echo "  clean       清理未使用的 Docker 资源"
    echo "  clean-all   完全清理（危险！删除数据）"
    echo "  help        显示帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 init               # 首次部署初始化"
    echo "  $0 start              # 启动服务"
    echo "  $0 logs backend       # 查看后端日志"
    echo "  $0 shell postgres     # 进入数据库容器"
    echo "  $0 backup             # 备份数据库"
}

# 主入口
case "$1" in
    init)
        check_dependencies
        init_env
        ;;
    build)
        check_dependencies
        build
        ;;
    start)
        check_dependencies
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    logs)
        shift
        logs "$@"
        ;;
    shell)
        shift
        shell "$@"
        ;;
    backup)
        backup
        ;;
    restore)
        restore "$2"
        ;;
    clean)
        clean
        ;;
    clean-all)
        clean_all
        ;;
    help|--help|-h|"")
        help
        ;;
    *)
        log_error "未知命令: $1"
        help
        exit 1
        ;;
esac
