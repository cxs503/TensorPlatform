# P96-N8 报告载荷归属门成熟化：服务端结构性阻断 + 客户端 fail-fast（mission 5617）

作者：tp-agent · 战役 DAG 节点 P96-N8 · 分支 `agent/m5617` · 本文所有数字由
`artifacts/m5617/collect_evidence.py` 实测后写入 `artifacts/m5617/criteria.json`，
再由 `artifacts/m5617/make_report_payload.py` 程序化注入（**零手抄**）。

## ① 案例介绍（被测对象、背景与参数）

被测对象不是船舶水动力内核，而是**平台报告链路的载荷归属机制**——生态里每一份
带数字的成果都经它落库，它决定了「一份报告能不能被追责到正确的单」。四条历史失效线：

1. **写错单（串号）**：`POST /tensor/reports` 曾经只有角色门（「你能不能发报告」），
   没有归属门（「这份报告是不是你的单」）。任何持 ENGINEER token 的智能体都能把
   A 单成果写进 B 单号下，HTTP 200、静默落库；战役 DAG 按 `mission_id` 归并战绩，
   于是甲的绿灯记到乙头上，且数据层无法分辨。
2. **配图门形同虚设**：旧实现只判 `startswith(b"\x89PNG")` 四位前缀 → 截断 PNG、
   WebP/JPEG 改后缀、只剩文件头的空壳都能落盘，报告页图裂、图像证据失效。
3. **客户端路径不独占**：派单脚本统一写 `/tmp/report.json` 这个**全局固定文件名**，
   并发跑多单时后写覆盖先写 → 单号对、内容却是别人的（归属门拦不住这一类）。
4. **门「存在」但没接线 / 没被执行**：本单实测到的最严重一条——P34-N7 交付的
   `backend/services/report_ownership.py` 在合并时被门禁滤除，而
   `backend/api/tensor_reports.py` 仍然 `import` 它：**整个报告 API 在 main 上 import 即崩**；
   同时它的测试放在 `tests/`（不在 `pyproject.toml` 的 `testpaths=["backend/tests"]` 内），
   于是「测试全绿」是一句空话。

| 项 | 值 |
|---|---|
| 端点 | `POST /tensor/reports`（`backend/api/tensor_reports.py`） |
| 判据模块 | `backend/services/report_ownership.py`（恢复自 `2fe7f597`，sha256 `ae0fa6421174111b…`，11709 B） |
| 客户端工装 | `tools/report_payload.py`（独占文件名 + `os.link` 独占写 + 自证 sha256 + 提交前复算） |
| 路径约定 lint | `tools/audit/report_path_convention_lint.py` + `config/report_path_baseline.json` |
| 新增 pytest | `backend/tests/report_gate_support.py`、`test_report_ownership_gate.py`、`test_report_ownership_concurrency.py`、`test_report_payload_tool.py` |
| 取证脚本 | `artifacts/m5617/collect_evidence.py` → `criteria.json` / `junit.xml` |
| 错误码契约 | `REPORT_MISSION_NOT_OWNED`(403)、`REPORT_MISSION_NOT_FOUND`(404)、`REPORT_FIGURE_NOT_PNG`(422)、`REPORT_FIGURE_TRUNCATED`(422) |

## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）

**(a) 归属门（服务端结构性阻断）**。判据抽成纯函数 `ownership_verdict(user, mission, *, mission_id, org_id)`，
端点只做适配 → 生产与测试 import 同一份实现。身份解析 `claimed_user_id`（0090 迁移后的
认证账号）优先，回退 `claimed_by`（历史字符串）。判定表：无 `mission_id` → 放行（历史路径不回归）；
mission 不存在/跨组织 → 404；领取者 ≠ 提交者 → **403 + `X-Error-Code` 头**；
**无领取记录 → 也拒**（没有「谁该交这份报告」的证据，落库即不可归属数据）；
ADMIN 补录放行但响应带 `ownership.bypass="role:admin"`（可审计明口，不是暗门）。

**(b) 配图门（魔数 + IEND，先验后落）**。`validate_png_payload`：base64 严格解码 →
首八字节必须等于 `89 50 4E 47 0D 0A 1A 0A` → 必须以带 CRC 的 IEND 块收尾。JPEG/RIFF-WebP/GIF/BMP/TIFF
命中容器表 → 422 且错误体给**期望魔数**与实测魔数；缺 IEND → 422 TRUNCATED。
旧实现「先 INSERT 再写盘、失败再 DELETE」的窗口已消除：**先验后落**，异常路径不再出现
指向缺失图片的报告行（本单实测三类拒绝的 `rows_added` 全为 0、磁盘 0 文件）。

**(c) 客户端工装（fail-fast 前置）**。`write_report_payload(path, mission_id, payload)`：
文件名必须含 mission 令牌（`report_m5617.json`）→ 否则 `PayloadNameNotExclusive`；
payload 内嵌 `mission_id` 与入参不符 → `PayloadMissionMismatch`（写错单在写盘前拦下）；
`临时文件 + os.link` 实现**原子且独占**（目标已存在 → `PayloadExists`，绝不覆盖他人成果）；
返回自证 `{path, mission_id, sha256, bytes, verified}` 并**立刻回读复算**；
提交前 `preflight(path, mission_id, proof)` 现场重算，漂移 → `PayloadDrift`。
客户端配图判据直接复用服务端模块（`FigureRejected` 是同一个类对象）→ 「本地绿/服务端红」不可能。

**(d) 路径约定 lint（防复发）**。扫 `/tmp/**/report.json`（basename 无 mission 令牌）；
存量欠账登记在 `config/report_path_baseline.json`（84 处 / 39 个文件）：
**超基线即红、新增文件即红、欠账只减不增**。本单不批量改写 30+ 脚本（改动面 >> 收益），
但把「新增」这条路封死，并给出迁移目标（`write_report_payload`）。

**(e) 判决工具链**。`collect_evidence.py` 用 sqlite 文件库 + FastAPI `TestClient`
（真 HTTP、真 SQL、真线程）跑 C1–C4；用 `git show 2fe7f597^` 取**修改前的端点原文**
按路径加载，跑出「修复前 200 且落库」的红证据 —— 红对照不是照着想象写的假旧实现。
sqlite 与 Postgres 的差异只在夹具层补齐（仅剥 `CAST(... AS jsonb)` 与回读时解 JSON），
业务与判据代码零改动。

## ③ 计算结果（机器实测数字表）

| 判据 | 定义 | 实测 | 结论 |
|---|---|---|---|
| C1 归属红→绿 | B 单 token 提交 A 单 mission_id | 修复前 HTTP **200** 且落库 1 行（`accepted=True`）；修复后 HTTP **403** `REPORT_MISSION_NOT_OWNED`，落库 +0 行 | PASS |
| C2 并发串号率 | 8 线程 × 8 个不同 mission | 落库 8/8，串号 **0/8**，正常路径 8/8 全 200，越权提交被挡 8/8，并发写盘字节一致 8/8 | PASS |
| C2b 归属矩阵 | 8×8 = 64 次真实提交 | 对角线 200 = 8/8；非对角 403 = 56/56 | PASS |
| C3 配图门 | JPEG / WebP / 截断 PNG | JPEG 422 `REPORT_FIGURE_NOT_PNG`；WebP 422 `REPORT_FIGURE_NOT_PNG`；截断 422 `REPORT_FIGURE_TRUNCATED`；三者 `rows_added`=0、磁盘图 0 | PASS |
| C4 正常路径 | 领取者提交自己的单 | HTTP 200，`ownership.figures_checked`=1，落盘字节一致=True，回读 200 | PASS |
| C5 既有测试 | 报告链路 7 个套件 | 124 passed / 0 failed / 1 skipped / 0 errors（exit 0；JUnit `artifacts/m5617/junit.xml`） | PASS |
| C6 零领域语义 | AST + 词表扫描 7 个文件 | 命中 **0** 条（37 域词条） | PASS |
| C7 陷阱蒸馏 | KNOWN_TRAPS append | `docs/m5617/KNOWN_TRAPS_append.md`：6 条新条目 + 修复机制 5 步 | PASS |
| 附 路径约定 lint | 裸 `/tmp/report.json` | 扫 1916 文件，命中 84 处 = 基线 84 处，新增 0 处 | PASS |

判据总体：**PASS**（
`all_ok=True`，失败项：无）。

## ④ 结果图

图 1（`fig1_gate_matrix.png`）：**归属矩阵热图**。行 = 提交者 user id，列 = 目标 mission_id，
绿格 200、红格 403。全部 64 个格子都是真实 HTTP 请求的返回值
（不是示意）：只有对角线（自己的单）是 200，非对角 56/56 全 403。

图 2（`fig2_before_after.png`）：**修复前后对照**。左：同一条跨单请求，修复前 200 且写 1 行、
修复后 403 且写 0 行（红→绿同一份请求、同一份库）；右：配图门三态全部 422 且不留行不留图。

图 3（`fig3_magic_fingerprint.png`）：**PNG 魔数指纹**。虚线是期望魔数 `89 50 4E 47 0D 0A 1A 0A`；
JPEG（SOI `FF D8 FF`）与 WebP（`RIFF…WEBP`）在第 1–4 字节即偏离，
截断 PNG 则是「头对、尾缺 IEND」——两类拒绝分别对应 `REPORT_FIGURE_NOT_PNG`
与 `REPORT_FIGURE_TRUNCATED`，判据可机器复算。

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **合并门禁会静默滤除新文件**（本单实测）：`services/report_ownership.py` 与它的测试
   在 P34-N7 合并时消失，而 `api/tensor_reports.py` 的 `import` 保留 → 报告 API 在 main 上
   直接 import 失败。修复不只是补文件，而是补一条**结构性断言**
   （`test_wiring_shared_module_exists`）：缺模块即红，不再依赖人工发现。
2. **测试位置 = 是否被执行**：pytest `testpaths=["backend/tests"]`，原测试放在 `tests/`
   → 从未运行。本单把用例搬进 `backend/tests/`，并把「套件真跑了 N 条」写进证据
   （C5 从 JUnit 解析，不信文本）。
3. **纯函数单测对「没接线」免疫**：所以 C1/C3/C4 全部走真 HTTP 端点；
   红证据来自 `git show 2fe7f597^` 的历史端点原文，而不是复述式假实现。
4. **并发才暴露共享状态**：全局 USER、进程级 `FIGURE_DIR`、固定文件名 `/tmp/report.json`
   在顺序测试里永远绿。C2/C2b 用真线程 + 真库，并额外校验「并发写盘字节一致」。
5. **sqlite 夹具的诚实边界**：`CAST(:x AS jsonb)` 在 sqlite 下变成 NUMERIC（JSON→0），
   夹具层只做两处等价变换（剥 jsonb CAST、读回解 JSON），业务与判据代码零改动；
   真实 Postgres 上的 jsonb 行为不在本单覆盖范围（无 psycopg2 运行环境）。
6. **适用范围**：改名/迁移路径的存量欠账（84 处 / 39 文件）只做
   「只增即红」的护栏，未批量改写；跨组织隔离、幂等重放、报告删除审计不在本单范围。
7. **`FIGURE_DIR` 是进程级常量**：单实例单目录没问题，多实例共享 NFS 时依赖文件名与
   报告 id 唯一性（服务端已用 `report_id/name.png`）。

## ⑥ 误差判定（逐项对照基准，显式回答是否 <3%）

本单是**平台机制成熟化**，无物理实验/解析解基准，故不能做「阻力/功率」式的 <3% 相对误差判定；
基准取**机器可裁的判据对照**（修复前 vs 修复后、期望魔数 vs 实测魔数、播种归属表 vs 落库行）：

| 关键量 | 基准 | 实测 | 相对偏差 | 是否 <3% |
|---|---|---|---|---|
| 跨单提交的 HTTP 状态 | 修复前 200 → 修复后 403 | 200 → 403 | 0（完全一致） | 是 |
| 被拒请求的落库行数 | 0 | 0 | 0 | 是 |
| 并发串号率 | 0/8 | 0/8 | 0 | 是 |
| 归属矩阵非对角阻断率 | 100%（56/56） | 56/56 | 0 | 是 |
| 配图门期望魔数 | `89 50 4E 47 0D 0A 1A 0A` | `89 50 4E 47 0D 0A 1A 0A` | 0 | 是 |
| 领域词命中 | 0 | 0 | 0 | 是 |
| 载荷 sha256 自证 | 磁盘回读值 | 与内存值一致（`verified=true`） | 0 | 是 |

**结论**：全部判据与基准零偏差（<3% 满足，且是 0）。
**未做**（honest）：Postgres 真库端到端（环境无 psycopg2，用 sqlite 等价夹具）、
多实例 NFS 共享目录下的写盘竞态、`/tmp/report.json` 存量 84 处的批量迁移。

## 审计链

`criteria.json`（2026-11-07T14:18:26Z，git `f0c5b582dd7a29451667db90455d41deda905dd5`）→ `junit.xml`（C5 原始用例结果）
→ `report_path_lint.json`（路径约定）→ `domain_scan.json`（域词）→ 本 md
（`make_report_payload.py` 注入）→ `report_payload_proof.json`（写盘自证 sha256）。


---

## 运行时状态（发布后补记，诚实披露）

本单报告已通过 `/tensor/reports` 落库（report id **6611**，mission 5617，3 图）。
该响应体的 `ownership` 字段为 `null`：说明**当前运行在 :8010 的服务实例仍是 P34-N7
合并之前的构建**，本单的归属门/配图门尚未部署到运行时。仓库侧判据（C1–C7）是在
分支代码上用真 HTTP 端点验证的（TestClient 打的就是同一份 `api/tensor_reports.py`），
**部署生效需重建后端镜像**；部署后同一响应应出现
`ownership: {mission_id, owner, bypass, figures_checked}`。
反过来说，这也是本单要修的那条事故的旁证：main 上 `services/report_ownership.py` 缺失，
一旦有人从 main 重建镜像，报告 API 会 import 失败；本单已恢复该模块并加了结构性断言。
