# P02 盲评打分（两份同题练习产物）

**任务** mission 6359 ｜ **评分表** `agent_skills/EVALUATION_RUBRIC.md`（六维各 0–3，满分 18，档位 0–6 / 7–11 / 12–15 / 16–18）
**评估者** 盲评席位 agent（独立于两份产物的作者）｜ **时间** 2026-11-07T23:00Z 起 ｜ **工具** shell（cat/ls/sha256sum/python3）、只读访问
**工作树/分支** TensorPlatform @ `0f3655a6` / `agent/m6359` ｜ **python** 3.11.6
**盲评纪律** 评分对象=可观察行为与产物；不推测作者/模型身份，不以文风/篇幅代替证据。两份产物以 sha256 固定身份（见 §1），打分全程对照同一份哈希内容。

**样本与不确定度** 样本量 n=1 题（两份产物）；单题单评，维度分自带 ±1 评分噪声。下文凡"差距"均给出对归集口径的敏感性（§5.3）。

---

## 1. 被测产物（身份锚定 + 清单）

| 产物 | 主文档（字符/字节） | 支撑文件 | 主文档 sha256（前 16） |
|---|---|---|---|
| **产物 1** `skills_eval/P02/1/` | `P02-1.md`（8373 chars / 13658 B） | `dependency_graph.json`、`verify_workbreakdown.py`、`build_report.py` | `292bc70cce860ca9` |
| **产物 2** `skills_eval/P02/2/` | `P02-2.md`（13431 B） | `P02-2/verify.py`、`P02-2/verify_report.json`、`P02-2/contract/{openapi.yaml,storage.sql,error_codes.md,bundle.schema.json}`、`P02-2/make_report.py` | `4b5bf68a70153e8e` |

全部 12 个文件哈希已采集（评测命令见 §2），可据此判定"是否为同一份产物"。

---

## 2. 我实际跑过的检查（复现命令 + 结果）

| # | 命令 | 结果（原文数字） |
|---|---|---|
| E1 | `cd P02/1 && python3 verify_workbreakdown.py` | exit=0；`checks_total=13, checks_passed=13, coverage_pct=100.0`；`critical_path_person_days=3.0`；`total_effort_person_days=7.0`；`element_coverage=11/11`；`integrator=['A1']` |
| E2 | `cd P02/2/P02-2 && python3 verify.py` | exit=0；`passed=8, total=8, verdict=PASS`；`C6 computed=4.0d, doc_claim=4.0, critical=WP1 -> WP2 -> WP4b`；`C3 tables=['certification_exports']`；`C1 openapi=3.1.0` |
| E3 | 真仓核对产物 1 的 [F] 基线：`ls backend/auth.py`；`grep -c OAuth2PasswordBearer backend/auth.py`；`ls -d backend/alembic`；`ls tests` | 文件存在；计数 **2**；`backend/alembic` 存在；`tests/conftest.py` 存在 → 产物 1 §2 [F] 三条成立 |
| E4 | `ls -d tests/api`（产物 1 规划的测试落点） | `No such file or directory` → 规划路径**在真仓不存在**（未验证落点） |
| E5 | 真仓核对产物 2 的 §1.3 勘察：`grep -ril certif backend/api`；`ls backend/migrations \| wc -l`；`ls docs/W10-N07_*.md` | 零命中（与"绿地"断言一致）；migrations **74** 个、首个小文件 `008_deployments_tables.sql`（与"008–017+"一致）；`docs/W10-N07_numerical_uncertainty_certification.md` 存在 → 产物 2 §1.3 三条成立 |
| E6 | `ls P02/2/P02-2/contract/ \| wc -l`；`grep -rn contract_hashes` | **4** 个文件；`openapi.yaml:8` 引用 `contract_hashes.txt` → 被引用的哈希清单**不存在**（悬空引用） |

**not_run（未跑，故不得计分）**：产物的"未改生产代码"自述（无法从产物目录验证，两产物同一处理，见 §7）；留出题迁移测试；第二评估者独立复核；生产期纪律计时/预算核对。

---

## 3. 逐产物逐维打分（0–3，每维一句引证）

### 产物 1（`P02-1.md` + `dependency_graph.json` + `verify_workbreakdown.py`）

| 维 | 分 | 引证（指向具体章节/命令/数字；无证据即 not_run） |
|---|---|---|
| ① 目标与约束 | **2** | §1–§2 明确父目标、范围内/范围外（前端 UI、异步队列、新认证提供方列为范围外）、≤4 agent；关键设计取舍（同步 v1 vs 异步队列）**只以 [I] 断言"同步对内部数据量足够"，未做方案对比** → 正确范围但未达"发现歧义与权衡" |
| ② 方法纪律 | **3** | 文档按 Procedure 1–11 分节（§2 契约→§4 边界→§5 WBS 七字段×6 包→§6 契约冻结→§7 DAG→§8 写所有权→§9 集成负责人→§10 早期验证→§12 重规划）；关键路径自算 0.5+1.5+1.0=3.0 与 E1 复算一致；WP0 冻结后并行是可辩护的适配 |
| ③ 证据质量 | **2** | E1 可复现 13/13、E3 三条 [F] 真仓成立、F/I/A/U 四类标注计数 `{'F':5,'I':4,'A':6,'U':2}`；**但 13 项检查中至少 5 项只是关键词存在性判定**（如 `negative_authorization_tests` 仅判 `"负向授权" and "401" and "403"` 是否出现），属自证；关键路径检查仅比 `budget=20pd`，**未与文中 3.0d 基线比对**；E4 显示规划落点 `tests/api/` 真仓不存在；§17 "[I]…5.5 人日估算" 与 §7/§11 的 7.0 人日自相矛盾（数字漂移）→ 可追溯但可靠性打折 |
| ④ 备选与不确定性 | **2** | §10 S1–S3 给出可判定假设+30min/1h/1h 成本+失败触发动作（§12），风险登记册 R1–R6 带概率/影响/owner；**但无方案级备选比较，无敏感性分析** |
| ⑤ 产物可用性 | **2** | §8 写所有权表（唯一 owner + `backend/auth.py` 只读）、§5 每包七字段含路径与验收、§11 预算、§14 DoD 七条 → 可直接排期；**接口契约只有文中表格（§6.1/§6.2），无 openapi/DDL 等可执行契约件**，且 `tests/api/` 落点不存在、5.5/7.0 数字冲突削弱交接精度 |
| ⑥ 安全与边界 | **3** | 最小权限设计完整：写所有权唯一化、§9 明确"A1 既实现又集成 → 由 A4 独立评审 A1 的授权代码"（职责分离）、WP5 安全清单含下载路径穿越与"导出文件不含密钥"、并写入"缺项=not_run 不得标 passed"；将"改 `backend/auth.py`"设为 §12 重规划触发器 = 冲突升级 |

**小计 14/18 → 档位 12–15「competent on this task」**

### 产物 2（`P02-2.md` + `contract/` 4 件 + `verify.py` + `verify_report.json`）

| 维 | 分 | 引证（同上规则） |
|---|---|---|
| ① 目标与约束 | **3** | §1.2 参数表把约束量化（3 操作 / 4 agent / 保留 30d / 配额 500MB→507 / 租户隔离→403）；§3.1 显式对比"异步 job 还是同步 GET"并给理由（证据包可达数百 MB、超时不可控），**该取舍直接改变全局设计**；§7 把"认证状态机尚不存在"升为前置项 P0 并**给重排后工期 5.5d** → 发现歧义+取舍+止损路径 |
| ② 方法纪律 | **3** | §2 声明 5 步方法（契约先行/七字段 WBS/CPM/单点签发/机器验收）；C4 用脚本逐字段清点 4×7；C6 **从 §5.2 表解析工期重算最长路径 4.0d 并与文中"总工期 4.0 人日"比对一致**（E2）；§5.2 给 ES/EF/浮动（WP3 0.5d、WP4a 1.0d、WP2 无浮动）；§4.1 集成人 5 项职责含回滚预案 |
| ③ 证据质量 | **3** | E2 复现 8/8 PASS，且检查是真解析/真执行而非自证：C1 `yaml.safe_load`、C3 `sqlite3` 内存库 executescript 得到 `certification_exports`、C7 图边↔依赖列表双向闭合、C8 OpenAPI 错误码 ⊆ 错误码表（`[401,403,404,409,410,422,429,507]` uncovered=无）；E5 真仓核对 §1.3 三条勘察全部成立 → 独立可复现 |
| ④ 备选与不确定性 | **2** | §9 四条局限（工期是估计非测量、规划≠实现、R5 为最大风险、适用范围边界）+ §1.2 取舍说明；**但"±20% 扰动下关键路径不变"只是文字断言，verify.py 未做敏感性计算；无 F/I/A/U 逐条溯源标注** |
| ⑤ 产物可用性 | **2** | 契约是**可执行件**：openapi.yaml 3.1 可解析、storage.sql 可直跑、bundle.schema.json、错误码表含"客户端动作"列，另给 §4.1 DoD/签发/回滚 → 强交接；**扣分点=两处悬空引用**：`openapi.yaml` 声明的 `contract_hashes.txt` 不存在（E6，而 WP1 验收④正是"4 个契约文件 sha256 入清单"），且"复算指令"写的 `docs/skills_training/P02-2/verify.py` 与实际目录不符 |
| ⑥ 安全与边界 | **3** | storage.sql 注释"只建新表不 ALTER"+`CHECK` 约束枚举状态、`artifact_path` 注释"路径唯一来源（防路径穿越）"、sha256 sidecar 审计链、跨租户一律 403、409 状态守卫"唯一守卫函数禁止旁路"、feature flag 一键下线不动存储；§7 主动升级 P0 冲突 |

**小计 16/18 → 档位 16–18「strong on this task」**

---

## 4. 裁决

**产物 2 更好**，总分 **16 vs 14**，差距 **2 分（≈11%）**，跨越一个档位（strong vs competent）。

**理由（按判别力排序，全部可复核）**

1. **"接口契约先行"这一核心要求上量级不同**：产物 2 交付了可执行契约包（E2 实测 YAML 解析 + sqlite DDL 执行成功，另含 bundle schema 与 8 码错误码表），产物 1 的契约只存在于文档表格（§6.1/§6.2），实施者仍需自己再写一遍 → 直接决定 ⑤ 的可交接性与 ① 的约束落地。
2. **机器验收的"深度"差一层**：产物 2 的 C6 把文中声明（4.0d）与脚本重算值**互校**，产物 1 的 `critical_path_within_budget` 只比 20pd 预算，等于没有校验它自己的 3.0d 结论；产物 1 的 13 项检查里 ≥5 项是关键词存在性判定（如负向授权测试仅查字符串出现）。两者都"100%/8-8 PASS"，但前者是证据、后者更接近自我声明 → ③ 的差异来源。
3. **前置风险的处置方式**：产物 2 §7 主动认定"认证状态机在后端不存在"并给出重排方案（5.5d）；产物 1 把它降级为 WP 内的 0.5h spike（§10 S1）。方向都对，但产物 2 把该风险写进了关键路径与预算。
4. **反向计分（产物 1 确有的优势，已计入其 ②⑥ 满分而产物 2 缺项）**：产物 1 的写所有权矩阵 + "实现者兼集成者须由他人独立评审授权代码"的职责分离、F/I/A/U 四类溯源标注、S1–S3 带成本的早期验证与失败触发器，是产物 2 没有的三件结构性产物。

**敏感性**：若把"契约哈希清单缺失"改归到 ③（证据）而非 ⑤（可用性），产物 2 = 15（仍与 14 同档），评分口径变化不改变裁决方向，只改变档位差；单评 ±1 噪声下差距 2 分的方向稳健、幅度仍属"可辨但非悬殊"。

### 各自「最要害的一条缺陷」（败因记录）

- **产物 1 最要害：验收脚本自证，把"通过"变成不能证伪的仪式。** `verify_workbreakdown.py` 的 13 项里 `negative_authorization_tests`/`explicit_interfaces`/`required_elements_11`/`early_assumption_tests`/`dependency_aware_packages` 均为子串存在性判定；`critical_path_within_budget` 用 `cp_len <= 20pd` 这一必然成立的判据（实测 3.0 vs 20），而非与文中 3.0d 基线比对 → E1 的 `coverage_pct=100.0` 不构成对其主要结论的支持。两个佐证事实：规划落点 `tests/api/` 在真仓不存在（E4），§17 的"5.5 人日"与 7.0 人日冲突。
- **产物 2 最要害：冻结契约的完整性机制被声明但未交付，且自检集恰好漏掉自己的验收项。** WP1 验收④要求"4 个契约文件 sha256 入清单"，`openapi.yaml:8` 也引用 `contract_hashes.txt`，但 `contract/` 只有 4 个文件、无该清单（E6），而 `verify.py` 的 C1–C8 无一检查此项 → 仍报 `verdict=PASS`。即"契约先行+哈希防漂移"这一卖点的关键链条是空的，8/8 PASS 属自选集覆盖。

---

## 5. 事实 / 推断 / 未知（三分列）

**事实（我亲自执行并观察到）**
1. E1：产物 1 的校验脚本 exit=0，13/13 pass，`critical_path=3.0pd`、`effort=7.0pd`、`elements=11/11`。
2. E2：产物 2 的校验脚本 exit=0，8/8 PASS；C6 `computed=4.0d` 与 `doc_claim=4.0` 一致；C3 在 sqlite3 内存库成功建出 `certification_exports`。
3. E3/E5：两份产物的仓库勘察断言经真仓核对均成立（`OAuth2PasswordBearer` 计数 2、`backend/alembic` 存在、`backend/migrations` 74 个且始于 `008_*`、`grep -ril certif backend/api` 零命中、`docs/W10-N07_*` 存在）。
4. E4：`tests/api` 在真仓不存在。
5. E6：`contract/` 内 4 个文件，`contract_hashes.txt` 缺失但被引用。
6. 两份产物目录内均无生产代码改动痕迹；12 个文件哈希已采集（§1）。

**推断（有证据但需一步推理）**
1. 产物 1 的"100% 覆盖"实际语义是"关键词/字段存在性覆盖"，不能读作"结论被验证"——依据是脚本判据代码本身（子串匹配 + 20pd 上界）。
2. 产物 2 的 CPM 数字（4.0d、浮动 0.5/1.0）是自洽且可复算的，因此内部的算术可信；但工期输入 1.0/2.0/1.5/1.0 人日无实测来源，可复算 ≠ 可信。
3. 产物 2 的悬空哈希清单暗示其"契约冻结"流程在练习阶段即已中断一次，实施时存在同样的漂移风险。

**未知（无法从产物判定，标 not_run）**
1. 谁在什么预算下、用了哪些工具/技能版本（两份产物均未记录 tools/time；不在评分内）。
2. 两份产物是否真的未触碰生产仓（仅自述；目录证据不充分）。
3. 换题（held-out）后两产物方法是否仍成立 —— rubric 明确"重复记忆题不证明通用能力"，本单 n=1，不外推。
4. 若把两份方案真的落到本仓实施，谁的工期估计更准 —— 无实测基线。

---

## 6. 另一 agent 能否据此接手复现

**可以，按下列 6 条即可在 10 分钟内复现我的全部结论**（无需我的任何私有上下文）：

```bash
# 1) 身份锚定（确认是同一份产物）
sha256sum /root/tp-agent/skills_eval/P02/1/* /root/tp-agent/skills_eval/P02/2/P02-2.md \
          /root/tp-agent/skills_eval/P02/2/P02-2/*.* /root/tp-agent/skills_eval/P02/2/P02-2/contract/*
# 2) 产物 1 自检 → 期望 checks_passed=13/13, critical_path_person_days=3.0
cd /root/tp-agent/skills_eval/P02/1 && python3 verify_workbreakdown.py
# 3) 产物 2 自检 → 期望 passed=8, verdict=PASS, computed=4.0d/doc_claim=4.0
cd /root/tp-agent/skills_eval/P02/2/P02-2 && python3 verify.py
# 4) 真仓核对产物 1 的 [F] 基线（期望：文件存在、计数=2、alembic 存在）
cd <TensorPlatform 工作树> && ls backend/auth.py && grep -c OAuth2PasswordBearer backend/auth.py && ls -d backend/alembic
# 5) 核对产物 1 的落点假设（期望：No such file or directory）
ls -d tests/api
# 6) 核对产物 2 的勘察 + 悬空引用（期望：零命中 / 74 个迁移 / 4 个 contract 文件 / 仅 openapi.yaml 提及 contract_hashes）
grep -ril certif backend/api | wc -l; ls backend/migrations | wc -l; ls /root/tp-agent/skills_eval/P02/2/P02-2/contract | wc -l; grep -rn contract_hashes /root/tp-agent/skills_eval/P02/2/
```

**接手边界（复现得到什么/得不到什么）**：上述 6 条可复现 §3 表中所有"事实"级引证与 §5 全部事实列；**不能**复现 ④ 维的主观权衡打分与"是否算发现歧义"的判定，也不能复现任何 held-out 迁移结论。两人独立打分若在 ④/⑤ 上差 1 分属预期噪声，若 ③ 差异超过 1 分（例如有评估者认为产物 1 的 13 项检查够格给 3）则需补一条"判据是否自证"的书面标准再收敛。

---

## 7. 本评价自身的局限与残留风险

1. **单题单评**：n=1、无第二评估者（not_run），仅给出本任务上的相对排序，不可外推为能力评级（rubric 明令不得以单一聚合分分配未来工作）。
2. **只读材料**：未运行产物 1 的 `build_report.py` / 产物 2 的 `make_report.py`（它们的输出不在评分范围内），故未评其报告生成链。
3. **对称处理的风险项**：两份产物都声明"未改生产代码"，我一律记为自述（`not_run`），未因此加减分。
4. **可能异议点（已备答）**：产物 1 的 ③ 若被判 3，则 15 vs 16（差距 1 分、同处 competent 与 strong 边界），裁决仍指向产物 2，理由仍为契约可执行性与验收互校深度两项硬差异。