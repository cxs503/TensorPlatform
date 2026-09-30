# 开放数字底座 - Docker & Python 管理脚本
# Makefile for Docker and Python operations

.PHONY: help build up down restart logs ps clean backup restore test coverage lint type-check format

# 默认目标
.DEFAULT_GOAL := help

# 帮助信息
help:
	@echo "开放数字底座 Docker & Python 管理命令"
	@echo ""
	@echo "服务管理:"
	@echo "  make build       构建所有镜像"
	@echo "  make up          启动所有服务"
	@echo "  make down        停止所有服务"
	@echo "  make restart     重启所有服务"
	@echo "  make ps          查看服务状态"
	@echo "  make logs        查看日志 (Ctrl+C 退出)"
	@echo ""
	@echo "生产环境:"
	@echo "  make prod-up     启动生产环境"
	@echo "  make prod-down   停止生产环境"
	@echo ""
	@echo "数据库:"
	@echo "  make backup      备份数据库"
	@echo "  make restore     恢复数据库 (需要 BACKUP_FILE=xxx.sql)"
	@echo "  make migrate     运行数据库迁移"
	@echo ""
	@echo "代码质量:"
	@echo "  make test        运行测试"
	@echo "  make coverage    运行覆盖率测试"
	@echo "  make lint        运行代码检查 (flake8)"
	@echo "  make type-check  运行类型检查 (mypy)"
	@echo "  make format      格式化代码 (black)"
	@echo ""
	@echo "清理:"
	@echo "  make clean       清理未使用的镜像和容器"
	@echo "  make clean-all   清理所有（包括数据卷，危险！）"
	@echo ""
	@echo "开发:"
	@echo "  make shell       进入后端容器"

# 构建镜像
build:
	@echo "构建 Docker 镜像..."
	docker-compose build --parallel

# 启动服务
up:
	@echo "启动服务..."
	docker-compose up -d
	@echo "等待服务就绪..."
	@sleep 10
	@docker-compose ps
	@echo ""
	@echo "服务已启动！"
	@echo "前端: http://localhost:3000"
	@echo "后端API: http://localhost:8001"
	@echo "API文档: http://localhost:8001/docs"
	@echo "MinIO控制台: http://localhost:9001"

# 停止服务
down:
	@echo "停止服务..."
	docker-compose down

# 重启服务
restart:
	@echo "重启服务..."
	docker-compose restart
	@echo "服务已重启"

# 查看状态
ps:
	@docker-compose ps

# 查看日志
logs:
	docker-compose logs -f --tail=100

# 生产环境启动
prod-up:
	@echo "启动生产环境..."
	docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
	@echo "生产环境已启动"

# 生产环境停止
prod-down:
	@echo "停止生产环境..."
	docker-compose -f docker-compose.yml -f docker-compose.prod.yml down

# 备份数据库
backup:
	@echo "备份数据库..."
	@mkdir -p backups
	docker-compose exec -T postgres pg_dump -U foundation open-digital-foundation > backups/backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "备份完成！"
	@ls -la backups/

# 恢复数据库
restore:
ifndef BACKUP_FILE
	@echo "错误: 请指定备份文件"
	@echo "用法: make restore BACKUP_FILE=backups/backup_xxx.sql"
	@exit 1
endif
	@echo "恢复数据库..."
	cat $(BACKUP_FILE) | docker-compose exec -T postgres psql -U foundation open-digital-foundation
	@echo "恢复完成！"

# 数据库迁移
migrate:
	@echo "运行数据库迁移..."
	docker-compose exec backend alembic upgrade head
	@echo "迁移完成！"

# 进入后端容器
shell:
	docker-compose exec backend bash

# 运行测试
test:
	@echo "运行测试..."
	@cd backend && pytest

# 测试覆盖率
coverage:
	@echo "运行覆盖率测试..."
	@cd backend && pytest --cov=. --cov-report=html --cov-report=term -v
	@echo "覆盖率报告: htmlcov/index.html"

# 代码检查 (flake8)
lint:
	@echo "运行代码检查..."
	@cd backend && flake8 --max-line-length=120 --ignore=E501,W503

# 类型检查 (mypy)
type-check:
	@echo "运行类型检查..."
	@cd backend && mypy --ignore-missing-imports --no-strict-optional .

# 代码格式化 (black)
format:
	@echo "格式化代码..."
	@cd backend && black --line-length=120 .

# 格式检查
format-check:
	@echo "检查代码格式..."
	@cd backend && black --check --line-length=120 .

# 运行所有质量检查
quality:
	@echo "运行所有质量检查..."
	@make lint
	@make type-check
	@make format-check

# 清理
clean:
	@echo "清理未使用的资源..."
	docker system prune -f
	docker image prune -f
	@echo "清理完成"

# 完全清理（危险）
clean-all:
	@echo "警告: 这将删除所有数据卷！"
	@echo "按 Ctrl+C 取消，按回车继续..."
	@read confirm
	docker-compose down -v
	docker system prune -a -f
	@echo "完全清理完成"

# 重建并启动
rebuild:
	@echo "重新构建并启动..."
	docker-compose down
	docker-compose build --no-cache
	docker-compose up -d
