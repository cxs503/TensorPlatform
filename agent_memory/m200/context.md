# m200 交接笔记（Tensor Soul Runtime 第一阶段）

## 当前状态（2026-10-23）
- 分支 `agent/m200`，基线 main `6a3f63d`，未推送（收割线代推）。
- 三段全交付：审计 `docs/architecture/TENSOR_SOUL_AUDIT.md`、实现（migration 094 + services/api soul_runtime + soul_migrations）、
  报告 `docs/architecture/SOUL_PHASE1_REPORT.md`、机读判据 `soul_phase1_validation.json`（13/13）。
- 测试全绿：tests/factory 95 passed / soul 专项 11 passed / 非 factory 23 passed，三组退出码均 0。

## 关键路径与坑
- 依赖不在系统 site-packages，须 `PYTHONPATH=/srv/tensor/shared/tensor_eco/agent_work/m200/.pydeps`，
  Python 一律 `/srv/tensor/shared/venvs/tensor_eco/bin/python`。
- 取证脚本 `scripts/verify_soul_phase1.py` 会跑三组 pytest（约 90 秒）并重写 soul_phase1_validation.json；
  它把跑测时的 HEAD 记进 head_commit，所以每次跑完都要再提交一次工件（报告里已注明该口径）。
- 094 迁移的 sha256 已硬编码在 `backend/services/soul_migrations.py` 的登记表里（跑库时登记），
  **改动 094 文件内容会让已建库报 MigrationChecksumMismatch**——要改就改新迁移文件（095+）并加进 SOUL_MIGRATIONS。
- `backend/services/factory_bootstrap.py:32-34` 是 Soul 迁移入口；工厂测试都走它，改这里会一次性影响 tests/factory 全量。

## 未做（后续）
- 神经记忆实验、生产部署验证、resident_harness 接 recall/prepare、community knowledge 收敛到 memory_events 视图。
- 详见报告 §4 NOT RUN 与 §5 缺口。
