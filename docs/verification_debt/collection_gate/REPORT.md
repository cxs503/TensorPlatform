# 采集闸门清偿：5 个 collection error → 0，2732 例可采集（m2066）

**EDIT_WHITELIST**: `docs/`, `tests/`, `tools/`, `artifacts/`
**证据等级**: REAL（机器可复算：`pytest --collect-only` 前后计数）

## ① 案例介绍（对象与背景）
平台全部「pytest 全绿」声明都隐含一个前提：测试能被 pytest **采集**。pytest 在 collection
阶段即中断时，一整轮 2500+ 例**一条都不执行**，而报告里仍可能写着「测试绿」。
2026-11-02 实测本仓工作树：`pytest tests/ --collect-only` → **5 个 collection error，
成功态用例 2590 例被整体阻塞**。本单把该类缺陷清零，并给出防止复发的机器闸门。

## ② 怎么做的（方法 / 关键设置）
逐条定位 collection error 的**机理**（不是删测试、不是加 skip 换假绿）：

| # | 失败机理（实测） | 修复（最小改动） |
|---|---|---|
| 1–2 | `tests/test_vibration_analytic_bench.py` / `tests/verif/test_seakeeping_section_analytic.py` 从 `artifacts/` 导入参考核；该目录不入提交（合并门禁滤除），干净副本必然 `ModuleNotFoundError` | 恢复 4 份资产到**已提交**的 `tests/data/verifier_assets/`（含 sha256 manifest 登记），导入顺序改为「版本化资产 → artifacts → **显式 WITHHELD skip**」 |
| 3 | `tests/verifier/test_maneuvering_reference.py` 与 `tests/test_maneuvering_reference.py` **同名不同目录**，pytest 默认 import-mode 判 `import file mismatch` | 重命名为 `test_mmg_reference_audit.py`（唯一 basename） |
| 4 | `tests/test_public_wall.py` 模块级 DDL 直接打到 `database.config` 的**共享 engine**；同批采集里别的模块先绑了同一 sqlite → `table organizations already exists`；且夹具时间戳写死 `2026-10-22`，`succeeded_24h` 断言过期即必然失败 | 专用 `sqlite+pysqlite://` + `StaticPool` 内存库（不与其他模块复用）；`CREATE TABLE IF NOT EXISTS`；夹具时钟按 now−1h 平移 |
| 5 | `tests/factory/conftest.py` 顶层 `import pgserver`（未安装）→ 整目录 collection ERROR | `ModuleNotFoundError` → `pytest.skip(allow_module_level=True)`（WITHHELD，不伪装通过） |

**新增机器闸门** `tests/test_clean_checkout_collection.py`：用 `git ls-files` 把**全部 tracked
文件**复制到临时目录（**不含**任何未跟踪的 `artifacts/` 探针产物），在那里跑 `--collect-only`，
断言「无 collection error 且收集数 ≥ 2500」。它判的不是"工作树能不能跑"，而是
**提交进仓库的东西能不能跑** —— 即证据可复现性。

## ③ 计算结果（程序化注入，零手抄）
| 指标 | 修复前 | 修复后 |
|---|---|---|
| `pytest tests/ --collect-only` | **5 errors**（Interrupted） | **0 errors** |
| 可采集用例数 | 2590（且整体被中断） | **2732** |
| 受影响模块定向回归 | 2 error + 2 fail | **141 passed / 4 skipped / 1 xfailed** |
| 资产 manifest | 11 条 | **15 条**（新增 4 条 sha256 登记） |

## ④ 结果图
`fig_collection_gate.png`：左=按机理分解的 collection error 数（5→0）；右=可采集用例数（2590→2732）。
数字由本单 `pytest --collect-only` 输出程序化注入。

## ⑤ 注意点（踩过的坑 / 局限）
1. **先试错记录**：第一版给 `tests/verifier/` 加 `__init__.py` 解 basename 冲突 → 反而新增 4 个错误
   （包化改变模块名，内部兄弟导入失效）。已回退，改用重命名（唯一 basename），1 行改动。
2. **闸门时序**：闸门读 `git ls-files`，未提交的修复不算证据 —— 必须 commit 之后再跑（本单按要求
   在提交后复跑）。
3. **skip ≠ 通过**：`pgserver` 缺依赖走 WITHHELD skip，4 个 skipped 用例属此类，不计入"绿"。
4. 本单不动船舶求解代码（`backend/api/domain/*`），只动测试/资产/文档，避免与在飞开发单抢文件。
5. 其余 `artifacts/` 依赖（如 `artifacts/verif/*`）仍是**同形风险**，本单只清偿了会让采集断裂的
   共 5 处；未被采集触发的同类引用未逐一迁移。

## ⑥ 误差判定
本单为测试基础设施任务，关键量是**计数/布尔判据**（collection error 数、收集用例数），
无物理基准可对标，故不适用 "vs 实验/解析基准 <3%" 判据；替代判据为逐项机器复算：

| 关键量 | 基准 | 结果 | 是否达标 |
|---|---|---|---|
| collection error 数 | 0 | 0 | ✅ 是（布尔，精确） |
| 收集用例数 | ≥ 2500（闸门下限） | 2732 | ✅ 是 |
| 定向回归 | 全绿 | 141 passed / 4 skipped(WITHHELD) / 1 xfailed | ✅ 是 |
| 资产 digest 一致性 | manifest sha256 | 15/15 匹配 | ✅ 是（`test_verifier_assets_integrity` 通过） |
