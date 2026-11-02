# TensorPlatform Project Charter

生效：2026-11-03 · 依据：owner 2026-11-02 终裁（两层分工）与[生态总纲](architecture/TENSOR_ECOSYSTEM_AI_ERA_VISION.md)

## 本仓是什么

TensorPlatform 是**领域无关的智能体运行与协同基础设施（harness）**。它为任何领域的智能体
生态提供运行底盘；科学计算只是当前租户，不是本仓的边界（人文如视频制作、游戏制作同理）。

## 职责（保留面）

- 用户 / 权限 / 审计
- 通用任务机制（tensor_dev_missions：领取 / 心跳 / 预算 / 验收流）
- 模型元数据与运行登记（RunRegistration）
- 通用执行接口与证据存储
- 数据与制品引用（引用，不是领域内容本身）

## 非职责（迁移面）

- **科学计算语义**（基准判读、物理常数、阻力链、科学验收规则）→ TensorPhysics 与领域库
- **船舶域载荷** → ship-performance-platform（P2 迁移线）
- **论文生产** → TensorPapers

## 分层裁决（owner 2026-11-02 终裁）

| 层 | 职责 |
|---|---|
| TensorPlatform | harness：用户/权限/审计/数据/模型元数据/通用执行接口/证据存储 |
| TensorPhysics | 科学流程组织：科研循环状态机/求解器调度/耦合/数值诊断/科学验收/下轮决策 |
| 领域库（TensorLBM/SPH/DG/…） | 求解器与领域本体 |

## 过渡安排

- `api/domain/` = **域载荷过渡区**：R2 物理缝（physics provider 注册面）落地于此；
  其中的领域语义最终下沉领域库，不是永久归属。
- 存量域代码的逐项去向（保留 / 抽取 / 兼容包装 / 废弃）见迁移处置清单：
  `docs/architecture/migration_disposition_2026-11.md`（编制中）。

## 机器门（边界由测试强制，不由本文承诺）

- `tests/test_boundary_gates.py`（G1-G3 边界门）
- `tests/test_physics_seam_guards.py`（R4 物理缝守卫）

门必须可失败；当前为 non-blocking baseline，将按棘轮收紧为阻断。

## 本章程的修改

章程变更与分层裁决同权：须 owner 批准，随 PR 评审入库。
