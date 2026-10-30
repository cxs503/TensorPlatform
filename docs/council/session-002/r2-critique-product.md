author-model: qwen3.7-plus

# R2 质询 · 产品与基础设施视角

> **质询人定位**：功能是否真用户可用（不是脚本演示）；triton 无 SDAA 后端 / 卡位 / 环境 / git 断连等硬约束是否被低估。
> 所有质疑附实测证据（API 调用 / 代码 grep / git 状态），可查证。允许的缺陷标注「非阻断」。

---

## 0. 质询前实测基线（证据快照）

以下为本质询人在撰写前对平台的实测，作为质询依据：

| 检查项 | 方法 | 结果 |
|---|---|---|
| 船舶计算 API 归属 | `call_ship_api` GET/POST `calculations/resistance` | 返回 port=8002，**与 TensorPlatform worktree 后端（无 ship 代码）分离** |
| worktree 内 ship 代码 | `grep -rn "resistance\|holtrop\|ship_type" backend/api/` | **零命中**；`backend/api/domain/` 无任何 `ship_*.py` 文件 |
| `tensor/executions` 端点 | GET `tensor/executions` | **404 Not Found** |
| `propeller/open_water` 端点 | GET `propeller/open_water` | **404 Not Found**（audit 标 REAL，实际不可达） |
| `samples/ship` 端点 | GET `samples/ship` | **404 Not Found** |
| `calculations/benchmark_validate` | GET | **404 Not Found** |
| `calculations/timing_baseline` | GET | **404 Not Found** |
| resistance 实算 SUBOFF V=5 m/s | POST `calculations/resistance` `{ship_type:SUBOFF, speed:5, length:4.356, beam:0.508, draft:0.508, cb:0.7, displacement:1}` | Ct=0.015768, Cr=0.012336, method="ITTC-57摩擦+Holtrop剩余" |
| SUBOFF 实验 Ct（Roddy 1990） | 文献值 | ~0.0035–0.004（bare hull, Fr≈0.39） |
| 误差 | API vs 实验 | **+294%~+350%**（远超 15% 不可接受线） |
| git remote | `git remote -v` | `origin https://github.com/cxs503/TensorPlatform.git`（agent 禁推送） |
| 前端 ship 页面 | `grep -li "ship\|resistance\|vessel\|hull" frontend-vue/src/` | **零命中**；无任何船舶相关 Vue 组件 |
| scheduler_service.py | `ls backend/services/scheduler*` | 存在（`SchedulerService` 类），但**未暴露为 API 端点** |
| lineage_sync_service.py | `ls backend/services/lineage*` | 存在，但 `tensor/executions` 404 说明未挂载 |
| Alembic/SQLAlchemy | `ls backend/alembic*` + grep requirements | 存在，DB 迁移基础设施可用 |

---

## 1. 对提案 guide（r1-proposal-guide.md）的质询

### ① 致命伤

**G-FATAL-1：船舶计算 API（port 8002）不在 TensorPlatform worktree 内——全部 4 个任务的 "repo=TensorPlatform" 前提不成立。**

实测证据：worktree 的 `backend/api/domain/` 目录包含 `data.py / deployment.py / iam.py / model.py / platform.py / tensor.py / tools.py` 等 12 个文件，**无任何 ship 相关文件**。`grep -rn "resistance\|holtrop\|ship_type" backend/api/` 零命中。船舶计算端点（resistance/stability）运行在 port 8002 的独立服务上，其代码不在本 worktree。

后果：任务 A 说"新增 `backend/api/domain/ship_batch.py`"、任务 B 说"新增 `backend/api/domain/ship_benchmark.py`"、任务 C 说"在 resistance/stability 端点注入计时装饰器"、任务 D 说"新增 `backend/models/ship_sample.py`"——**这些文件要加到哪里？** 如果加到 TensorPlatform worktree 的 backend，它们无法调用 port 8002 的船舶计算逻辑（除非走 HTTP 代理）；如果加到 port 8002 的独立服务，那不是本 worktree 能改的。提案未回答这个根本问题。

**G-FATAL-2：Holtrop 方法对 SUBOFF 误差 +300%——任务 B 的 ≤5% 精度判据必然 FAIL，且触发 >15% 不可接受线。**

实测：API 对 SUBOFF（V=5 m/s, Fr=0.39）返回 Ct=0.015768，其中 Cr=0.012336（Holtrop 剩余阻力）。SUBOFF 实验值 Ct≈0.0035–0.004（Roddy 1990, bare hull）。误差 **+294%~+350%**。

根因：Holtrop 是**水面船舶**经验方法，SUBOFF 是**水下轴对称体**。Holtrop 的剩余阻力系数公式基于水面兴波阻力，对水下体完全不适用——它把不存在的兴波阻力算进去了（Cr=0.012336 占 Ct 的 78%）。

后果：任务 B 的 B4"端到端验证"对 SUBOFF ≥4 点必然全部 FAIL（误差 >>15%）。提案自己在风险中写了"Holtrop 对高速细长体 Fr>0.4 误差可能 >5%"，但**低估了两个数量级**——不是 >5%，是 >300%。DTMB-5415（水面舰）可能稍好，但 KVLCC2（肥大船，低 Fr）Holtrop 也未必 ≤5%。这意味着"10 对标算例精度实测"KPI 在当前算法下**不可达**。

### ② 被低估的风险

**G-RISK-1：任务 D 的 10000 条样本写入——provenance_hash 去重 + 参数空间不足。**

提案说 SUBOFF×31 速度点 + DTMB×50 速度点 + KVLCC2×20 速度点×多排水量变体。但 resistance API 只接受 `ship_type/speed/length/beam/draft/cb/displacement` 7 个参数。SUBOFF 31 点 + DTMB 50 点 + KVLCC2 20 点 = 101 点。要达到 10000 条，需要 ~100 组排水量/几何变体。提案说"多排水量变体"但未给出具体变体矩阵。如果 provenance_hash 去重，实际入库可能远 <10000。

**G-RISK-2：任务 A 的 batch_sweep 端点——如果走 HTTP 代理调 port 8002，1000 工况串行调用耗时不可控。**

实测单次 resistance 调用返回正常（~200ms 级），但 1000 次串行 = ~200s 仅网络开销。如果 port 8002 服务有并发限制或 rate limiting，批量调用可能超时。提案未讨论并发策略。

**G-RISK-3：propeller/open_water 404——推进能力不可用，但 KPI 要求"工具接入 ≥5 类"。**

audit 标 `propeller.open_water` 为 REAL，但实测 404。这意味着推进类计算无法纳入批量调度或对标验证。提案诚实标注了这一点（非阻断），但 KPI"工具接入 ≥5 类（现 4/5）"的第 5 类如果指望推进，则无法闭合。

### ③ 判据是否机器可裁

| 任务 | 判据 | 机器可裁？ | 问题 |
|---|---|---|---|
| A | "batch_sweep 返回 ≥1000 条" | ✅ 可 | 但需先解决 G-FATAL-1（端点加到哪里） |
| B | "error_pct ≤5% pass, >5% fail" | ✅ 可 | 但 SUBOFF 必然全 FAIL（G-FATAL-2），判据本身没问题，结果不可接受 |
| C | "p50>0 且 p99≥p90≥p50" | ✅ 可 | 纯逻辑判据，合理 |
| D | "样本库 ≥10000 条" | ✅ 可 | 但依赖 A 完成 + 参数空间足够（G-RISK-1） |

### ④ 预算/墙钟是否现实

总预算 215 调用 / 10800s（3 worker 并行）。单 worker 最大 65 调用 / 3300s。

- 任务 A（60 调用 / 2700s）：实现新端点 + 测试 + demo——如果端点加在 TensorPlatform worktree 内（HTTP 代理模式），2700s 可能够。但如果需要改 port 8002 服务，则不可行（代码不在 worktree）。
- 任务 B（45 调用 / 2400s）：录入参考数据 + 实现端点 + 测试 + 验证——参考数据录入本身就需要查文献，2400s 紧张但可能。
- 任务 D（65 调用 / 3300s）：模型 + 迁移 + CRUD + 填充 10000 条——3300s 内完成 DB 迁移 + 3 个端点 + 测试 + 10000 条写入，**不现实**。仅 10000 条 HTTP 调用（即使批量）就需要可观时间。

### ⑤ 缺失项

1. **未回答端点代码归属问题**（G-FATAL-1）——这是最关键的缺失。
2. **未评估 Holtrop 对三型船的实际误差**——提案假设"可能 >5%"但未实测，实际 SUBOFF 误差 >300%。
3. **未考虑 port 8002 服务的可用性/并发限制**——批量调用可能打挂独立服务。
4. **冰区 ≤10% KPI 完全未覆盖**——提案诚实标注了（非阻断），但 KPI 闭合度声称"100%"是误导。

---

## 2. 对提案 software（r1-proposal-software.md）的质询

### ① 致命伤

**S-FATAL-1：`tensor/executions` 端点 404——方向 A/B/C/D 的核心基础设施不存在。**

实测：GET `tensor/executions` 返回 404。提案方向 A（批量调度）的 S0 说"扩展 `/tensor/executions/batch`"，方向 B（谱系）的 S0 说"完善 `backend/api/tensor_executions.py`"，方向 C（复现）的 S0 说"复现 API 设计"——**这些全部基于一个不存在的端点**。

`backend/services/scheduler_service.py` 和 `lineage_sync_service.py` 确实存在（代码级），但它们**未被挂载为 HTTP 路由**。提案说"扩展"和"完善"，暗示这些端点已存在——这是事实性错误。

**S-FATAL-2：预算用人日（"1.5天/2天/1天"），与议事会预算帽（140 调用 / 5400s = 90 分钟）完全不匹配。**

议事会纪律明确"预算帽 140 调用 / 5400s"。提案的预算单位是"人日"和"卡"，没有转换为调用数和秒数。一个"2 天"的任务在 5400s（90 分钟）内不可能完成。这不是估算偏差，是**单位错误**——提案未遵守议事会预算口径。

### ② 被低估的风险

**S-RISK-1：前端页面——frontend-vue 无任何船舶相关组件，"看板页面"从零开始。**

实测：`grep -li "ship\|resistance\|vessel\|hull" frontend-vue/src/` 零命中。frontend-vue 是数据平台前端（DataCatalog/DataLineage/DataAnalysis 等），不是船舶平台前端。方向 A 的 S2（调度监控看板）、方向 B 的 S1（谱系图可视化）、方向 C 的 S2（复现 UI）、方向 D 的 S2（工作流可视化）全部需要新建 Vue 页面——但前端框架、路由、API 对接层都不存在。提案说"0 卡"好像前端是免费的，实际上前端开发量可能比后端更大。

**S-RISK-2：WebSocket 实时更新——基础设施不存在。**

方向 A S2 说"实时更新（WebSocket）"，缓解方案是"先用轮询"。但轮询也需要后端有状态查询 API（`tensor/executions` 404），且轮询频率/性能未评估。WebSocket 更是需要后端 ASGI 支持、连接管理、消息推送——这些在当前平台中未见任何基础设施。

**S-RISK-3：方向 D（优化工作流编排）——依赖调度服务（方向 A）和执行引擎，但两者都不存在。**

方向 D 的 S1 说"工作流阶段调度（依赖满足后自动触发）"，依赖 scheduler_service。但 scheduler_service 未暴露为 API，且方向 A 的调度功能本身还未实现。方向 D 的前置依赖被标为"无"，这是错误的。

### ③ 判据是否机器可裁

| 方向 | 判据 | 机器可裁？ | 问题 |
|---|---|---|---|
| A | "批量提交 100 工况成功" | ✅ 可 | 但端点不存在（S-FATAL-1） |
| A | "看板页面可展示队列状态" | ❌ 不可 | "可展示"是主观判断，无自动化测试 |
| A | "实时更新延迟 <5s" | ✅ 可 | 但需 WebSocket/轮询基础设施 |
| B | "谱系数据模型完整" | ❌ 不可 | "完整"无客观定义 |
| B | "谱系图可展示" | ❌ 不可 | 主观 |
| C | "复现执行成功；结果与原工况一致" | ⚠️ 半可 | "一致"需定义容差 |
| D | "工作流数据模型完整" | ❌ 不可 | "完整"无客观定义 |

**4 个方向中仅方向 A 的部分判据机器可裁，其余大量主观判据。**

### ④ 预算/墙钟是否现实

提案用"人日"和"卡"为单位，未转换为调用/秒。按议事会口径（140 调用 / 5400s）：

- 方向 A（S0+S1+S2 = 6 impl + 3 ver + 3 doc = 12 工作单元，"5.5 天"）：在 5400s 内不可能。
- 方向 B（S0+S1+S2 = 5 impl + 3 ver + 3 doc = 11 工作单元，"4.5 天"）：不可能。
- 方向 C（同上量级）：不可能。
- 方向 D（同上量级）：不可能。

**提案的预算口径与议事会纪律完全不兼容。** 如果按单 mission 拆（每个 S0/S1/S2 为一个 mission），则每个 mission 需在 140 调用 / 5400s 内完成一个"2 天"的任务——仍然不现实。

### ⑤ 缺失项

1. **未验证 `tensor/executions` 端点是否存在**（S-FATAL-1）。
2. **预算未转换为调用/秒**（S-FATAL-2）。
3. **前端开发量被严重低估**（"0 卡"暗示无需资源）。
4. **方向 D 的前置依赖标错**（标"无"，实际依赖方向 A）。
5. **无放弃条件的量化触发器**——"2 天内无法完成"在 5400s 预算下无意义。
6. **"卡"的含义未定义**——是 GPU 卡？是卡位（slot）？软件开发任务为何需要卡？

---

## 3. 对提案 solver（r1-proposal-solver.md）的质询

### ① 致命伤

**无致命伤。** solver 提案在硬约束认知上最为诚实——明确标注了 triton 无 SDAA 后端、D6 墙钟超帽、两库 0 处 sdaa 引用等关键限制，且放弃条件具体可触发。这是三份提案中对基础设施约束理解最深的。

### ② 被低估的风险

**V-RISK-1：triton 无 SDAA 后端——B 线（第二个库上卡）和 C 线（3D/自由面/相场上卡）的根本前提可能不成立。**

owner 指令明确"triton 无 SDAA 后端（只能 CPU 解释，绕行须如实记录）"。提案 B0 普查三库是否"有可用张量后端"，但如果 triton 本身不支持 SDAA，那么即使库代码用了 torch 张量，编译/执行路径仍然走 CPU 解释模式。提案在 §6 提到"M-T1 triton 审计（5 模块纯 torch 等价）"但不重复——这意味着 triton 问题已被识别但**未被本提案解决**。B1–B3 的"胜者单点 PASS"如果走 CPU 解释模式，性能数据无意义（不是真正的 SDAA 加速）。

提案应明确：B0 普查的判据中是否包含"triton 编译路径是否走 SDAA"？如果只查"代码是否用 torch 张量"，会得到假阳性。

**V-RISK-2：卡位可用性——"峰值 6 卡，远低于 12 卡待释放额度"的假设可能过于乐观。**

上下文说"利用率 12/32，在飞 24"。如果 12 卡在用 + 24 个 mission 在飞，实际空闲卡可能 <6。提案的卡位表假设可以同时占 6 卡（A3 独占 1 + B0 并行 3 + B/C/D 错峰 2），但如果其他 mission 也在抢卡，实际可用可能只有 2–4 卡。提案未讨论卡位竞争场景。

**V-RISK-3：git 断连——A1 的"版本锁定 CPU 参考值"需要 git_sha，但 agent 禁推送 GitHub。**

提案 A1 要求每条记录带 `torch/torch_sdaa/git_sha/device_name` 四元组。worktree 的 git remote 是 `https://github.com/cxs503/TensorPlatform.git`，agent 禁推送。如果 git_sha 只读不写（记录当前 commit），则可行。但如果需要 clone/pull 其他仓库（TensorLBM/TensorFEM/TensorDEM）来获取参考值，网络可达性未验证。提案未讨论 git 网络约束。

**V-RISK-4：DEM-FEM 耦合——TensorDEM 邻域搜索依赖 scipy.spatial.cKDTree（CPU-only），上卡后"数据在卡上、搜索在 CPU"的假加速。**

提案自己在 R-D1 中标注了这一点（非阻断，诚实），但 D4 的判据"报每步接触搜索占比"如果显示 >70%，则整个 D 线的结论是"耦合瓶颈在 DEM 搜索而非 FEM 求解"——这意味着 D3 上卡的价值存疑。提案应明确：如果 D4 证明搜索占比 >70%，D3 的 SDAA 加速比是否仍有意义？

### ③ 判据是否机器可裁

| 任务 | 判据 | 机器可裁？ | 问题 |
|---|---|---|---|
| A1 | "88 工件 sha256 快照 + 布尔判定字段" | ✅ 可 | 合理 |
| A3 | "CV ≤2%，5 次重复" | ✅ 可 | 合理 |
| B0 | "sdaa_lib_survey.json 含 compile_route" | ✅ 可 | 但需确认 compile_route 是否能检测 triton→SDAA 路径 |
| B1–B3 | "Cd/误差 ≤3%" | ✅ 可 | 合理 |
| C1 | "evidence 可点回工件字段路径" | ✅ 可 | 合理 |
| C2 | "Cd ≤3%，显存 ≤14.94 GiB" | ✅ 可 | 合理 |
| C3 | "L2 ≤1%，质量漂移 ≤CPU 基线" | ✅ 可 | 合理 |
| C4 | "Δp·R/σ ≤3%" | ✅ 可 | 合理 |
| D2 | "F∝δ^1.5 指数 1.5±0.05，刚度误差 ≤3%" | ✅ 可 | 合理 |
| D3 | "sdaa vs CPU L2 <1e-6" | ✅ 可 | 合理 |

**solver 提案的判据机器可裁性最好**——全部有量化阈值，无主观判断。

### ④ 预算/墙钟是否现实

提案每个 mission 标 140 调用 / 5400s，与议事会帽一致。但：

- **A3（单卡性能基线）**：5 次重复 × 单次运行（D6 中位数 36.19s）= ~180s 计算 + 环境准备/数据处理。5400s 够。
- **B0（三库普查）**：3 卡并行 × 2h = 各 7200s——**超过 5400s 帽**。提案说"3 卡 2 小时"但单 mission 帽是 5400s（90 分钟）。需要拆为更小粒度。
- **C2/C3/C4**：各 140 调用 / 5400s，但 D6 显示有历史样本墙钟 7351.5s > 5400s。提案说"按网格分档拆单"——合理，但实际拆多少档未量化。
- **D2（Hertz 标杆 CPU）**：0 卡，纯 CPU 计算。140 调用 / 5400s 应该够，但 FEM 网格收敛测试（"单元尺寸减半时变化 <5%"）可能需要多轮计算。

**B0 的 2 小时 > 5400s（90 分钟）帽是一个具体超帽问题。** 其余基本合理。

### ⑤ 缺失项

1. **B0 普查判据未明确包含 triton→SDAA 编译路径检测**（V-RISK-1）。
2. **卡位竞争场景未讨论**（V-RISK-2）。
3. **B0 的 2 小时预算超过 5400s 单 mission 帽**（需拆单）。
4. **D 线上卡价值的前提条件未量化**——如果搜索占比 >70%，D3 的加速比预期是多少？未给出"如果加速比 <2x 则不上卡"的放弃条件。
5. **TensorFEM 是否支持接触单元未验证**（提案 §7 honest 标注了，非阻断）。

---

## 4. 修正建议：第四方向——Ship API 代理编排层

三份提案各有盲区：guide 假设 ship 代码在 worktree 内（不在）；software 假设 tensor/executions 存在（不存在）；solver 聚焦 SDAA 上卡但与船舶 KPI 关联弱。

**建议第四方向：在 TensorPlatform worktree 内构建 Ship API 代理编排层（Ship Proxy Orchestration Layer）。**

### 核心思路

port 8002 的船舶计算服务是**外部黑盒**——代码不在 worktree 内，agent 无法修改。与其假装能改它，不如在 TensorPlatform worktree 内建一层代理编排：

```
用户/Agent → TensorPlatform backend (worktree 内) → HTTP 代理 → port 8002 (船舶计算服务)
                    ↓
            批量调度 / 计时 / 缓存 / 样本存储 / 对标验证
```

### 具体任务

| ID | 内容 | 机器判据 | 预算 |
|---|---|---|---|
| P1 | 新增 `backend/services/ship_proxy.py`：封装对 port 8002 的 HTTP 调用（resistance/stability），含重试/超时/错误处理 | 单元测试：mock port 8002 响应，验证代理正确转发参数和解析结果 | 15 调用 / 600s |
| P2 | 新增 `POST /ship/batch_sweep`：接收参数矩阵（ship_type × speed[] × param_variants），串行/并发调用代理，返回结果数组 | 提交 50 工况，返回 50 条结果；耗时记录 | 20 调用 / 900s |
| P3 | 新增 `POST /ship/benchmark_validate`：调用代理获取计算值，与内置参考数据（JSON）逐点比较，返回 error_pct + pass | ≥5 个参考点验证；error_pct 计算正确 | 15 调用 / 600s |
| P4 | 新增 `backend/models/ship_sample.py` + Alembic 迁移 + `POST /ship/samples/bulk` + `GET /ship/samples`：批量存储和分页查询 | 写入 100 条，查询返回正确；迁移可执行 | 20 调用 / 900s |
| P5 | 计时装饰器注入代理层（非 port 8002 端点），`GET /ship/timing_baseline` 返回 p50/p90/p99 | 50 次调用后 p50>0 且 p99≥p90≥p50 | 10 调用 / 600s |

### 为什么比现有提案更好

1. **解决 G-FATAL-1**：不假装能改 port 8002，而是在 worktree 内建代理层——代码确实在 worktree 内，agent 可改可测。
2. **解决 S-FATAL-1**：不依赖不存在的 `tensor/executions`，新建独立的 `/ship/*` 路由。
3. **预算现实**：总计 80 调用 / 3600s，单 mission 均 ≤20 调用 / 900s，远在 140/5400s 帽内。
4. **判据全机器可裁**：每条都有量化阈值。
5. **诚实面对精度问题**：benchmark_validate 会如实暴露 Holtrop 对 SUBOFF 的 +300% 误差，触发 honest-FAIL，推动算法升级——而不是假装 ≤5% 可达。

### 关键诚实标注

- **精度 ≤5% 不可达**：当前 port 8002 用 Holtrop 方法，对 SUBOFF 误差 +300%。代理层只能如实报告误差，不能改善精度。精度达标需要 port 8002 侧算法升级（CFD/经验公式替换），这超出 worktree 可改范围。
- **冰区 ≤10% 不覆盖**：port 8002 无冰区计算能力。
- **propeller/open_water 404**：推进类计算不可用，代理层暂不纳入。

---

## 5. 总结对比

| 维度 | guide | software | solver | 第四方向(建议) |
|---|---|---|---|---|
| 端点代码在 worktree 内？ | ❌ 不在 | ❌ tensor/executions 404 | ✅ (TensorLBM 等独立仓) | ✅ 代理层在 worktree 内 |
| 判据机器可裁？ | ✅ 全部 | ❌ 大量主观 | ✅ 全部 | ✅ 全部 |
| 预算口径正确？ | ⚠️ 墙钟偏乐观 | ❌ 人日≠调用/秒 | ⚠️ B0 超帽 | ✅ 80调用/3600s |
| 硬约束认知？ | ❌ 未识别 ship API 分离 | ❌ 未识别端点不存在 | ✅ 最诚实 | ✅ 直面分离现实 |
| 精度 KPI 可达？ | ❌ Holtrop +300% | N/A | N/A | ❌ 如实 honest-FAIL |
| 用户真可用？ | ❌ 脚本演示级 | ❌ 前端不存在 | ⚠️ 研究级非产品 | ⚠️ API 级可用，无前端 |

**最终判断**：
- **solver 提案**质量最高（硬约束认知、判据可裁、诚实标注），B0 超帽需拆单（非阻断）。
- **guide 提案**有致命伤（G-FATAL-1 端点归属 + G-FATAL-2 精度差 300%），需根本性修正。
- **software 提案**有致命伤（S-FATAL-1 端点不存在 + S-FATAL-2 预算单位错误），需重写。
- **建议第四方向**解决 guide 和 software 的共同盲区（ship API 不在 worktree），但精度 KPI 仍不可达（需 port 8002 侧算法升级）。

---

*本文件为 session-002 R2 质询（产品与基础设施视角），仅代表质询人个人意见，不构成派发指令。所有实测证据可在平台复现。终审归 owner。*
