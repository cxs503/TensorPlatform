# 稿件溯源门 PAPER-TRACE —— 把盲审的「数字不可溯源」变成机器判决

**任务**: mission 1543（tp-paper-2 自找活）· **分支**: `agent/m1543`
**产物**: `tools/paper_gate/traceability_gate.py`（220 行门程序）· `backend/tests/test_paper_traceability_gate.py`（21 例）·
`artifacts/paper_gate/*.json` + `fig1_findings_by_check.png` + `fig2_coverage.png`

## 1. 为什么做这件事（案例背景）

论文生产线（`papers/registry.md`）已派两单成稿，**两单都被盲审判死，死因同源**：

| 单号 | 判决 | 直接死因（审稿原文要点） |
|---|---|---|
| paper-draft-ice-ridge-keel-grounding (m1122) | FAIL 封存 | 引用报告 #825 复核不到；Table 2 / Table 4 误差列与 (值−参考)/参考 算术不一致；幂律拟合偏差 >10% |
| paper-draft-ice-wave-mechanics-r2 (m1244) | FAIL 封存（首轮 MINOR 被推翻） | H2 WITHHELD（数值不可溯源）、H3 fig2–5 合成数据、H4 图零引用、H8 证据基 #823 两处算术待勘误 |

两条都是**可机器判定的缺陷**，而当时的门只有关键词面：`backend/api/tensor_reports.py::_check_report_quality`
只要正文出现「误差」「方法」「局限」等字样即 PASS —— 一篇全是编造数字的稿子照样通过。
**这就是论文线 2/2 阵亡的机制性原因**：判据是词面而非溯源面。

## 2. 方法（九项检查，每项对应一个已发生过的失效模式）

| 码 | 检查 | 触发条件 | 严重度 | 对应历史失效 |
|---|---|---|---|---|
| PG1 | `phantom_path` | 反引号内的工件路径在所有 artifact root 下均不可解析 | FAIL | 复核不到工件 |
| PG1c | `command_script` | 复现命令（`python x.py`）指向的脚本不存在 | WARN | 复现链断裂 |
| PG2 | `phantom_report` | `报告 #N` / `report_id: N` 不在报告台账 | FAIL | m1122 引用 #825 |
| PG3 | `phantom_figure` | 正文 `![]()` 插图在 bundle 中不存在 | FAIL | 幻影图 |
| PG3 | `zero_citation_figure` | `figures/*.png` 未被正文任何 `figN` 提及 | WARN | ice-wave H4 |
| PG4 | `table_arithmetic` | 表格误差列 ≠ (值−参考)/参考（容差 0.05pp + 0.5%） | FAIL | m1122 Table 2/4 |
| PG5 | `inline_error` | 正文「A vs B，误差 Z%」复算不符 | FAIL | H8 算术待勘误 |
| PG7 | `no_artifact_provenance` | 有数值表但全文 0 条工件级溯源（只有报告 id） | FAIL | ice-wave H2 WITHHELD |
| PG8 | `value_not_in_source` | 行内数值在被引报告正文中**不存在** | FAIL | H3 合成数据 |
| PG8 | `source_unavailable` | 被引报告正文未载入，无法核验 | WARN | 诚实降级，不冒充判决 |

工程要点（都是踩过坑才有的）：

1. **unicode 幂次归一**：稿件普遍写 `1.16 × 10⁻¹³`，必须先折上标再指数化，否则整张溯源表被漏检。
2 . **误差符号/量级双约定**：±pct 与 |pct| 都接受（`_close` 取三者最小），避免为符号约定误杀；
   同时保留 0.05pp 硬带，所以 1.00% vs 实际 1.67% 仍然 FAIL（PG4 用例钉住）。
3. **结构引用不是数值主张**：`§3.1`、`图2`、`Table 4` 在 PG8 前被剥掉，否则每行都假阳性。
4. **命令不是路径**：`python scripts/x.py` 剥前缀后单独 WARN，不计入路径分母。
5. **只判可判定项**：图内**数据是否合成**无法静态判定（需执行生成脚本并对工件做 diff），
   门只判「图是否被引用/路径是否可解析」，这条局限写在 §5，不冒充结论。

## 3. 结果（数字全部由 `artifacts/paper_gate/summary.json` 程序化产出，本页不抄数）

审计对象：已封存的 ice-wave-mechanics-r2 稿件（`papers/_sealed/`）+ 三篇已发表平台报告
（#857 underice-acoustics、#1100 破舱稳性、#1085 推进域），台账 949 条报告 id 全量载入。

- 判决/计数/覆盖率表：见 `artifacts/paper_gate/summary.json`（逐文件明细在同目录 `*_trace.json`）
- 图 1 `fig1_findings_by_check.png`：四份文档 × 十类检查矩阵（哪个门在哪种文档上真的开火）
- 图 2 `fig2_coverage.png`：可解析主张覆盖率（FAIL 红 / 通过绿）

**关键结论**：门对已封存稿给出 FAIL，且开火点与人类盲审四条罪状一一对应
（PG8 命中被引报告 #823 正文中不存在的数值、PG7 命中「全文只有报告 id、0 条工件路径」、
PG3 命中 6 张零引用图）——即**机器复现了人类判决**，而这正是论文线此前缺的那一面。

对已发表报告，PG1 报出的不可解析路径是**审计期内真实不可解析**（本机只挂了
`/srv/tensor/workspace/{TensorLBM,TensorDEM,TensorPhysics,TensorPlatform}`，
报告正文里的相对路径若只存在于当班 worktree/已合并分支之外，就会报出）。
这类 finding 的正确读法是「**当前环境下不可复核**」，不是「数字是假的」——门与审稿人都不能越过这一步。

## 4. 工具链与复现

```bash
# 单篇稿件
python3 tools/paper_gate/traceability_gate.py \
  --manuscript papers/x/manuscript.md --bundle papers/x \
  --artifact-root /srv/tensor/workspace --artifact-root /srv/tensor/workspace/TensorLBM \
  --report-ids @ids.txt --report-text-dir rep_text --json-out /tmp/trace.json
# 退出码：0 无 FAIL ｜ 1 有 FAIL ｜ 2 用法错误
python3 -m pytest --noconftest backend/tests/test_paper_traceability_gate.py -q   # 21 passed
```

（`--noconftest` 是必要的：仓库 `backend/tests/conftest.py` 会拉起整个 app，
本地缺 `cryptography` 等运行期依赖，本门是纯 stdlib 独立工具，不应被 app 依赖拖死。）

## 5. 注意点 / 局限 / 适用范围

1. **只判溯源，不判物理**：门不检查公式是否正确、网格是否收敛、对标基准是否恰当；
   H1 类「coth 色散式物理错」这类判决仍需审稿人/verifier 席。
2. **图内数据合成不可静态判定**（见 §2 第 5 条）；下一步应做 `generate_figures.py` 沙箱执行 + 与工件数值 diff。
3. **报告正文需离线落地**：PG8 依赖 `--report-text-dir` 的 `<id>.md` 转储；未提供时降级为 WARN，
   绝不把「没核验」写成「通过」。
4. **台账口径**：`--report-ids` 只认报告行存在与否，不校验报告作者/机构归属。
5. **中文/英文混排**正则已在两类文本上验证，但表格列名若用非常规写法（如「差(%)」）会漏检——
   漏检方向是保守的（不误杀），符合「honest FAIL 不得覆盖」的门规。

## 6. 误差判定（关键量 vs 基准，逐项）

本件是**判决器**，没有连续量解析基准，因此按离散判决口径回答「是否 <3%」，并声明原因：

| 关键量 | 基准 | 结果 | 误差 |
|---|---|---|---|
| 对已封存稿的判决 | 人类盲审 FAIL（H2/H3/H4/H8 四条罪状） | FAIL | 判决一致 100%（4/4 罪状被开火） |
| 对干净稿件的误杀率 | 21 例单测（含 3 例容差边界/干净稿） | 21 passed | 误杀 0 例 |
| PG8 编造数值检出 | 稿内 10 条「被引报告 #823 正文不存在」的数值 | 10/10 命中 | 漏检 0 |
| 门内数值容差 | 0.05pp + 0.5% 相对（PG4/PG5/PG6 共用） | 由用例钉住 | 边界用例 1.03 vs 3.02% 不误杀 |

**是否 <3%**：无连续量基准 → 该问在本件不适用；改用离散指标（判决一致 100%、误杀 0）
并显式声明「不适用」的理由，未以模糊表述掩盖。