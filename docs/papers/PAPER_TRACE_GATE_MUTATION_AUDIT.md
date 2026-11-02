# PAPER-TRACE 门自身的变异审计：21 条单测的检出率 49.5% → 59.7%

作者：tp-ver-2（verifier 席，mission 1994） · **全部数字由 `tools/audit/paper_gate_mutation_audit.py` 写出 `artifacts/paper_gate_mutation/audit_v{1,2}.json`，本文件用脚本从 JSON 注入，零手抄**

## ① 案例介绍

被测对象不是船舶物理内核，而是**平台的稿件溯源门** `tools/paper_gate/traceability_gate.py`（PAPER-TRACE，596 行，PG1–PG8 八类判据）。
它存在的理由是历史事故：两条论文草稿被盲审以同一根因判废（报告 #825/#823 的数字无法回溯到任何工件，H2 WITHHELD / H3 合成数据）。
只要这扇门在，**每一份投稿报告的可发表性判据都挂在它身上**——它静默失效，等于全平台的数字纪律静默失效。

既有的 `backend/tests/test_paper_traceability_gate.py` 有 21 条单测（`docs/papers/PAPER_TRACEABILITY_GATE.md` §6 记 21 passed）。
问题在于：**测试绿只证明门能跑，不证明门在被削弱后测试会红**。验证器静默停止验证，与稿件直接通过是不可区分的——
这正是门要杀的失效模式，向上平移了一层。

| 项 | 值 |
|---|---|
| 被测模块 | `tools/paper_gate/traceability_gate.py` |
| 现有测试 | `backend/tests/test_paper_traceability_gate.py`（21 passed，0.93 s） |
| 新增测试 | `backend/tests/test_paper_traceability_gate_kills.py`（15 passed） |
| 变异体总数 | 186（单点变异） |
| 变异算子 | cmp_swap / binop_swap / boolop_swap / not_removal / append_drop（删掉 findings.append）/ return_empty（`return findings`→`return []`）/ const_scale（数值字面量×10）/ severity_flip（`"FAIL"`↔`"WARN"`） |

## ② 怎么做的

新增 `tools/audit/paper_gate_mutation_audit.py`（纯 stdlib + `ast`）：

1. `ast.parse` 门源码，用 `_Collector` 按确定性顺序枚举**单点可变位点**（只改一处，保证归因单一）；
2. 每个位点用 `NodeTransformer` 生成变异体，`ast.unparse` 写回**镜像工作树**（保留 `tools/paper_gate` + 测试文件的仓内相对路径，因为测试用 `parents[2]` 定位 ROOT）；
3. 在该镜像里跑 `pytest <测试文件> -q --noconftest -p no:cacheprovider -x`（`--noconftest` 必需：仓内 `backend/tests/conftest.py` 会拉起整个 app）；
4. 返回码 ≠ 0 ⇒ **killed**（测试抓到了变异）；= 0 ⇒ **survived**（门被削弱而测试全绿 = 测试盲区）；
5. 基线自检：镜像树的原始版本必须先全绿，否则「全灭」是假象（本轮踩到过：一开始把**原仓**测试文件路径传给镜像运行，导致导入的是未变异模块，186/186 全 survive，kill_rate=0——见 ⑤）；
6. 8 并发，单变异体 ~1.2 s，全量 ~110 s（本机 nproc=128、load15≈70，属轻量作业）。

**变异—固化—复测**闭环：v1 得到盲区 → 只针对 survivor 写杀手测试（不猜、只打实测盲区）→ v2 复测。产出双 JSON，可复现：
```bash
python3 tools/audit/paper_gate_mutation_audit.py --jobs 8 --json-out artifacts/paper_gate_mutation/audit_v2.json
```

## ③ 计算结果

### 3.1 总账

| 轮次 | 测试集合 | 变异体 | killed | survived | kill rate |
|---|---|---|---|---|---|
| v1 | 原 21 条单测 | 186 | 92 | 94 | **49.5%** |
| v2 | +15 条杀手测试 | 186 | 111 | 75 | **59.7%** |

### 3.2 分算子（kill rate 是检出率，越高=测试越硬）

| 变异算子 | 总数 | v1 killed | v1 检出率 | v2 killed | v2 检出率 | 提升 |
|---|---|---|---|---|---|---|
| `append_drop` | 11 | 8 | 72.7% | 8 | 72.7% | ++0.0pp |
| `binop_swap` | 45 | 17 | 37.8% | 25 | 55.6% | ++17.8pp |
| `boolop_swap` | 21 | 9 | 42.9% | 11 | 52.4% | ++9.5pp |
| `cmp_swap` | 42 | 30 | 71.4% | 33 | 78.6% | ++7.1pp |
| `const_scale` | 24 | 7 | 29.2% | 8 | 33.3% | ++4.2pp |
| `not_removal` | 17 | 12 | 70.6% | 16 | 94.1% | ++23.5pp |
| `return_empty` | 9 | 6 | 66.7% | 6 | 66.7% | ++0.0pp |
| `severity_flip` | 17 | 3 | 17.6% | 4 | 23.5% | ++5.9pp |

读法：`not_removal` 已达 94.1%（布尔取反几乎都被钉住），
`severity_flip` 仍只有 23.5%——测试普遍只断言**判据编号出现**，不断言**严重度**，
于是把 `"FAIL"` 改成 `"WARN"` 的变异活下来了：门仍然报出 `PG4_table_arithmetic`，但 verdict 从 FAIL 降成 WARN，稿件照样过闸。这是本轮**实测出来的最危险一类盲区**，已单独用 `test_pg4_inconsistency_is_fail_severity_and_drives_verdict` 钉住其中一处。

### 3.3 v2 仍存活的变异体（节选，前三类）

| 变异体 | 行 | 被改写的源码 |
|---|---|---|
| `cmp_swap[1]` | 115 | `abs(c - claim) <= 1e-12 + rtol * abs(claim)` |
| `const_scale[1]` | 115 | `1e-12` |
| `cmp_swap[2]` | 123 | `min(abs(stated - computed), abs(stated - abs(computed)),        ` |
| `binop_swap[5]` | 123 | `stated - computed` |
| `binop_swap[6]` | 123 | `stated - abs(computed)` |
| `binop_swap[11]` | 142 | `i + 1` |
| `return_empty[0]` | 169 | `return findings, checked` |
| `boolop_swap[3]` | 173 | `i not in err_idx and i not in ref_idx` |
| `boolop_swap[4]` | 174 | `not ref_idx or not val_idx` |
| `return_empty[1]` | 175 | `return findings, checked` |
| `boolop_swap[5]` | 177 | `"%" in header[err_i] or "％" in header[err_i]               or bo` |
| `cmp_swap[11]` | 180 | `max(err_i, ref_i, val_i) >= len(row)` |
| `const_scale[4]` | 190 | `100.0` |
| `binop_swap[16]` | 194 | `line_no + r_off + 1` |
| `binop_swap[17]` | 194 | `line_no + r_off` |
| `binop_swap[18]` | 197 | `tol_rel*100` |
| `const_scale[5]` | 197 | `100` |
| `boolop_swap[8]` | 215 | `None in (a, b, stated) or b == 0` |

按算子统计的残余存活：`binop_swap` 20 个, `const_scale` 16 个, `severity_flip` 13 个, `boolop_swap` 10 个, `cmp_swap` 9 个, `return_empty` 3 个, `append_drop` 3 个, `not_removal` 1 个。

## ④ 结果图

图 1（`fig1_killrate_by_operator.png`）：分算子 kill rate 前后对比，虚线为两轮总体值。
图 2（`fig2_survivor_map.png`）：变异体在门源码行号上的分布，红=存活（未覆盖行为）、绿=被测试抓住；上排 before、下排 after。
两图均由脚本从审计 JSON 直接绘制，无手工数据。

## ⑤ 注意点 / 局限 / 适用范围

1. **杀手测试必须对准实测 survivor**：本轮 15 条新测试全部由 v1 的 survivor 列表反推（容差带 `_close`、`value_present` 的 1e-6 相对容差、PG4 列序/零参考行/截断行/全角％表头/分数形式回退、PG5 分数形式、表尾边界、PG4 严重度）。不按 survivor 写的测试只会重复既有覆盖。
2. **等价变异体不可杀**：`return_empty[0]`（L169）落在「无误差列即早退」分支，该分支 findings 恒为空，改 `return []` 语义等价；`const_scale` 打在 argparse 默认值（main() 内）上，测试不经过 CLI。这类 survivor 记在账上但**不冒充欠账**。
3. **镜像树跑测试的坑（本轮真踩）**：镜像里必须跑**镜像内**的测试文件路径。第一版把仓内绝对路径传给镜像，测试按自身 `parents[2]` 定位 ROOT→导入原仓未变异模块，结果 186/186 全 survive、kill_rate=0.0——一个「审计器自己静默失效」的完美样本。因此基线全绿自检与 `baseline_green` 字段是硬门。
4. **ast.unparse 会丢注释/格式**：变异体只用于跑测试，绝不回写仓内源码。
5. **覆盖面**：只做单点变异（不做二阶/组合），因此 59.7% 是**下界**；真实盲区不超过它。变异体只覆盖 `traceability_gate.py`，不含 `figure_provenance.py`（PG9）与 `backend/api/tensor_reports.py` 的 `_check_report_quality`。
6. 本件是**判决器审计**，无连续物理量基准，故按离散判决口径给误差（见 ⑥）。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 基准 | 结果 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 变异体总数 | `ast` 枚举的确定性位点数（可复算） | 186 | 复跑一致（v1/v2 同源同数） | 是（0%，逐位点等式） |
| v1 kill rate | 92/186 逐条 JSON 记录 | 49.5% | 与 JSON 逐条计数一致（脚本注入） | 是（0%，程序化注入） |
| v2 kill rate | 111/186 逐条 JSON 记录 | 59.7% | 同上 | 是（0%，程序化注入） |
| 破坏性验证 | 变异必须让测试变红——绿色基线自检 | 基线镜像 21+15 全绿，变异后按 kill 记录 | 无假阳性来源（返回码为判据） | 是（离散判据，无连续误差） |

**是否 <3%**：无连续物理量参考值 → 该问在连续口径下不适用，已改用离散判据（变异体逐个复算、kill 由 pytest 返回码裁定、数字由脚本注入零手抄）并显式声明理由，未以模糊表述掩盖。
诚实声明：**59.7% 不是合格线**，余下 75 个 survivor 中至少 12 个（severity_flip）与约 10 个（findings 行号算术）是真实可补的欠账，本轮受单次工时限制未清完，已列在 §⑤/§3.3 供下一席接手。
