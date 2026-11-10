# -*- coding: utf-8 -*-
"""
 CHANGELOG.md - 示例组织 AI 平台改进记录
"""

## [Unreleased] - 2026-11-10 · IN50-W4-N09 WITHHELD 三态清偿闭环

- 新增 `tools/vv/withheld_gates.py`（零领域语义）：把 WITHHELD 做成可清偿债务——
  门工件 `requires_reference` 声明 dataset/tol/k/实测值 → `tensor_reference_datasets`
  登记（DOI + 原始文件 sha256 + 数字化方法 + `u_dig`）→ 自动重判
  `|E| ≤ max(tol, k·sqrt(u_exp²+u_dig²))`（三态保留）→ 哈希链审计（append-only）。
- 反作弊三重防线：契约字段规范化 sha256 基线（重判时指纹不符 ⇒ FAIL + `TAMPER_BLOCKED`）、
  生效容差取 `min(基线, 现行)`（只可收紧）、库层触发器禁止 UPDATE 契约列 / UPDATE-DELETE 审计行。
- 新增 `backend/migrations/113_withheld_gates.sql`（表 + 触发器 + `v_withheld_gates_open` 视图 + KPI 索引）。
- KPI 落 `tensor_guide_tracker`：`withheld_gate_share`、`withheld_mean_age_days`（小时桶幂等）；
  未清偿门按 `(repo, dataset_id, age)` 合并派单，工单号幂等（`WO-sha256(repo|dataset_id)[:12]`）。
- 新增 `tests/test_withheld_gate_clearing.py`（26 用例，含迁移列 parity / CLI 端到端）与
  `tools/vv/withheld_gates_demo.py`（12 天合成战役 → `artifacts/withheld_gates/` 工件 + 3 图 + 报告）。
- 修：KPI 账龄时钟回拨/未来声明不再产出负账龄（会把均值拉低成"假清偿"）。

## [Unreleased] - 2026-11-10 · p228w1-N10 自主续航运维矩阵

- 新增 `tools/sustainment/`（零领域语义，四模块独立可调用）：工件血缘 DAG（sha256 + 证据分级边 +
  证据强度环消解 + 冻结/篡改检测）、报告证据链校验（图 sha256 / 数字溯源 / 判据重算 / 诚实 FAIL 披露）、
  备份恢复演练（隔离命名空间 + 三重对拍 + 三类坏备份负控 + RPO/RTO 账本）、工作树六类治理
  （tar+zstd 归档 + 磁盘压力门 85/90% + flock 互斥 + 未注册目录盲区测量）。
- 修：GC 指标语义（`reclaimed_bytes` 曾误记归档压缩体积，现为被移除树体积，另设 `archive_bytes`）。
- 修：血缘审计自噬（输出目录现自动排除出扫描域）与 verifier 未重放环消解导致的伪漂移。
- 新增 `config/sustain_core_tables.json`（5 张平台元数据表）与 `scripts/sustain/p228w1_n10_report.py`
  （报告数字程序化注入）与 `tests/test_sustain_*.py`（65 用例）。

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
