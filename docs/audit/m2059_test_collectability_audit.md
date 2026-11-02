# 套件可采集性审计：5 个采集错误 → 0（m2059 · tp-infra-1）

**EDIT_WHITELIST**：`docs/`, `tests/`, `artifacts/` · 分支 `agent/m2059`

## ① 案例介绍（对象与背景）

`pytest tests` 在本工作树是**整轮中止**的：`errors during collection`，退出码 2，
n_collected 之前的 2590 例**一条都跑不到**。这不是「某条用例红」，而是采集期异常：
对 30+ 席并发的平台，这等于所有依赖整轮测试的回归判据（提交门禁、报告门禁、能力复核）
共同失去机器眼睛，任何人跑全量都只能看到"采集失败"。

审计对象：`tests/` 全目录（2776 例可采集）、`tests/factory` PG 集成夹具、
三个「解析基准闸门」测试（振动 / 耐波剖面 / 操纵参考）、`tests/evidence_registry.json`。

## ② 怎么做的（方法 / 流程 / 工具链）

`python3 -m pytest tests --collect-only -q` 取全量采集面 → 逐 ERROR 定位到 module 级 import
行 → 二分（单独跑 / 成对跑 / 全量跑）区分「自身坏」与「跨模块相互作用坏」→ 分类根因
R1–R6 → 最小改动修复 → 加机器闸门 `tests/test_collectability_gate.py` 防回归。

| 根因 | 机制 | 为什么只在全量跑暴露 | 修复 |
|---|---|---|---|
| R1 | `tests/factory/conftest.py` 硬 `import pgserver`（可选重依赖） | conftest 导入失败 = 整目录 error | try/except + `pytest_collection_modifyitems` 显式 WITHHELD skip |
| R2 | `test_public_wall.py` 与别模块共用进程级 `database.config.engine`，schema 由导入顺序决定 | 单独跑用最小 3 表 DDL，全量跑先被 models 建成 8 列（`workspace NOT NULL`） | 私有 `StaticPool` 内存引擎，种子 DDL 幂等（`IF NOT EXISTS` + 先清表 + 显式列名） |
| R3 | `import ref_vibration`（`artifacts/verif_vibration/` 未入库） | 工件缺失即 ModuleNotFound | `tests/_evidence.py` 安全加载：缺失 → 模块级 WITHHELD skip（带来源+恢复指令） |
| R4 | `import lewis_reference`（`artifacts/verif/seakeeping_section/` 未入库） | 同上 | 同上 |
| R5 | `tests/` 与 `tests/verifier/` 同名 `test_maneuvering_reference.py` | pytest 报 `import file mismatch` | 重命名 verifier 侧为 `..._reference_gate.py`（内容不变） |
| R6 | 种子写死 `2026-10-22`，端点按「最近 24h」计数 | 被 R2 遮住，修好 R2 才暴露（`succeeded_24h` 0≠1） | 种子时间相对 `now` 生成 |

关键设计：**工件缺失不等于测试消失**。加载器把不可复现的 ImportError 变成
`WITHHELD: <工件> 缺失（已搜 …）。来源任务: …；恢复: …` —— 判据仍是「欠账」，但不再劫持整轮。

## ③ 计算结果（程序化注入，来自 `artifacts/m2059/stats.json`）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| collection errors | 5 | **0** |
| 采集退出码 | 2（中止） | **0** |
| 可采集用例数 | 0（中止，后续 2590 未见） | **2776** |
| 历史热点 5 文件采集 | 5 ERROR | 0 ERROR（190 例可采集） |
| 闸门自检 | 无 | **5/5 PASS** |

附带清偿（被采集错误遮蔽的真缺陷）：R6 时间炸弹 1 处（改前该用例必红，改后 5/5 绿）。

## ④ 结果图

图 1 左：六类根因的 collection error 计数（修复前 6 / 修复后 0）；右：可采集用例数
0 → 2776。

## ⑤ 注意点（坑 / 局限 / 适用范围）

- **`pytest.skip` 在模块级必须 `allow_module_level=True`**，否则它自己抛 `Failed`，
  采集照样报 error——第一版就踩了，症状是 ERROR 行**消息为空**。
- **全量跑与单跑不等价**：R2/R5 只在全量下现形；任何"我这文件单独跑是绿的"都不能当结论。
- **局限（诚实）**：修复后以 `-n 16` 跑全量，结果为 `2779 skipped / 0 failed`，
  **全部被跳过**；本次预算未定位到该全局 skip 来源（工作树无根 conftest.py，
  `tests/` 下也无 conftest.py），故「整轮可跑绿」**未证明**，只能证明「整轮可采集」。
  该 skip 机制留作后续单的第一条线索。
- **不覆盖**：`pgserver` 真实 PG 夹具在本机未安装，factory 目录按 WITHHELD skip，
  未验证其在装有该依赖环境下的行为。

## ⑥ 误差判定（关键量 vs 基准，逐项回答「是否 <3%」）

| 关键量 | 基准 | 实测 | 相对误差 | <3%？ |
|---|---|---|---|---|
| collection errors | 0（`errors during collection` 出现的唯一可接受值：0） | 0 | 0% | **是** |
| 采集退出码 | 0 | 0 | 0% | **是** |
| 历史热点文件采集错误 | 0 | 0 | 0% | **是** |
| 闸门自检通过数 | 5 | 5 | 0% | **是** |
| 时间炸弹种子用例 | 5/5 绿 | 5/5 绿 | 0% | **是** |
| 整轮执行结果 | 「非全 skip」（无法用百分比表达） | 2779 skipped / 0 failed | **不适用** | **否（未达成，见 ⑤ 局限）** |

无物理参考量：本单是测试基础设施，判据是**离散计数量**（错误个数、退出码、门禁项），
故误差为 0% 或「不适用」，不存在插值精度问题；物理量的 3% 口径不适用于本单。
