# -*- coding: utf-8 -*-
"""
 CHANGELOG.md - 示例组织 AI 平台改进记录
"""

# 当前版本: v2.2.0 - 2026-06-21

## [v2.2.0] - 2026-06-21

### 工程化与仓库净化
- 新增 `.gitignore`,移除误提交的构建产物(node_modules / dist / mlruns / uploads),被跟踪文件 17752 → 465
- 版本号对齐:统一 backend main.py(app version / /health / 根路由)、镜像 tag、Dockerfile 至 2.2.0(此前 /health 自报 2.1.0)

### 当前运行状态(演示态)
- 后端认证临时关闭(middleware/auth_guard 注入默认 admin),前端路由守卫同步放行
- 默认仅运行核心 4 服务(backend / frontend / postgres / redis);minio、celery、kafka 等支撑服务按需启动

## [v1.1.0] - 2026-03-06

### 新增功能 - Testing Framework
- ✅ 创建完整的测试框架
- ✅ `backend/requirements-test.txt` - 完整测试依赖
- ✅ `backend/conftest.py` - pytest 配置（SQLite 测试支持）
- ✅ `backend/tests/test_database.py` - 数据库测试（8325行）
- ✅ `backend/tests/test_auth.py` - 认证测试（9497行）
- ✅ `backend/tests/test_api.py` - API 测试（1826行）
- ✅ `backend/tests/__init__.py` - 测试包初始化
- ✅ `backend/TESTING.md` - 测试文档

### 新增功能 - Code Quality
- ✅ `backend/mypy.ini` - 类型检查配置
- ✅ `backend/.flake8` - flake8 配置
- ✅ `backend/install-precommit.py` - pre-commit 安装脚本
- ✅ `pre-commit-config.yaml` - pre-commit hooks 配置
- ✅ `Makefile` 更新 - 添加质量检查命令

### 新增功能 - CI/CD
- ✅ `.github/workflows/tests.yml` - 单元测试 CI 流程
- ✅ `.github/workflows/ci.yml` - 完整 CI/CD 流程
- ✅ 支持覆盖率报告上传到 Codecov
- ✅ 支持 Docker 构建和测试
- ✅ 支持前端构建和测试

### 新增功能 - Metrics & Health
- ✅ `backend/utils/metrics.py` - Prometheus 指标收集
- ✅ `backend/utils/health_check.py` - 健康检查器
- ✅ `backend/utils/alerts.py` - 告警通知管理器
- ✅ `backend/utils/docker_logging.py` - Docker 日志轮转

### 新增功能 - Documentation
- ✅ `CODING_GUIDELINE.md` 完善 - 添加测试规范
- ✅ `CODING_GUIDELINE.md` 完善 - 添加类型检查规范
- ✅ `CODING_GUIDELINE.md` 完善 - 添加日志规范

### 新增功能 - Makefile
- ✅ 添加 `make test` - 运行测试
- ✅ 添加 `make coverage` - 覆盖率测试
- ✅ 添加 `make lint` - 代码检查（flake8）
- ✅ 添加 `make type-check` - 类型检查（mypy）
- ✅ 添加 `make format` - 代码格式化（black）
- ✅ 添加 `make format-check` - 格式检查
- ✅ 添加 `make quality` - 运行所有质量检查

### 加入依赖
```
# 测试依赖
pytest==7.4.4
pytest-asyncio==0.21.2
pytest-cov==4.1.0
httpx==0.25.1
faker==20.1.0
freezegun==1.2.2

# 代码质量
black==23.12.1
flake8==7.0.0
mypy==1.8.0
types-passlib==1.7.7

# 开发工具
pytest-mock==3.12.0
```

### 向后兼容
- ✅ 所有新增文件为 UTF-8 编码
- ✅ 不修改现有功能
- ✅ 新增命令非破坏性

### Known Issues
- None

### Upcoming Features (v1.2.0)
- Prometheus metrics 集成到 FastAPI
- Health check 端点添加到 API
- 告警通知配置完整测试
- 示例测试服务层

## [v1.0.0] - Initial Release

### Features
- FastAPI 后端
- Vue 前端
- LLM 模型集成
- 数据管理功能
- 项目管理功能
- 用户认证

### Documentation
- README.md
- DOCKER_DEPLOYMENT.md
- CODE_OF_CONDUCT.md
