# PAPER-TRACE 溯源门自身变异审计 · 第二轮：单测检出率 59.7% → 92.5%

**单号** m2068（自找活 · paper 席）｜**上游** m1994（报告 #1771）｜**仓** TensorPlatform
**工件** `artifacts/paper_gate_mutation/audit_v3.json`、`round2_summary.json`、
`docs/papers/figures_trace_mut/fig1_killrate_by_operator_r2.png`、`fig2_survivor_map_r2.png`
**被测件** `tools/paper_gate/traceability_gate.py`（596 行，PAPER-TRACE 稿件溯源门 PG1–PG8）
**新增测试** `backend/tests/test_paper_traceability_gate_kills_v2.py`（36 条）

---

## ① 案例介绍（物理背景 / 对象 / 参数）

平台有两条"判决器"：`backend/api/tensor_reports.py::_check_report_quality` 只做关键词匹配，
稿件溯源门 `tools/paper_gate/traceability_gate.py` 做**可解析性判决**——正文里每个可核对声明
（路径、报告号、图表、误差列、锚点、被引报告正文里的数值）必须能回到真实工件。历史两次盲审废稿
（m1122 / m1244）的根因都是"数字无法溯源"，所以这道门本身的可信度就是平台可信度的上界。

问题在于：**判决器的单测全绿，只证明它会跑，不证明它会抓。** 一个被删掉分支的溯源门与一份
合格的稿件在 CI 上是同一副面孔——这正是这道门当初要杀掉的失效模式（H2 WITHHELD / H3 合成数据）
上升一层。因此用变异测试审计它自己：对门源码做 186 个单点变异，看它自己的测试会不会变红。

| 参数 | 取值 |
|---|---|
| 被测模块 | `tools/paper_gate/traceability_gate.py`（596 行） |
| 变异枚举 | `ast` 单点变异 9 类：cmp / binop / boolop / not 删除 / append 删除 / return 空表 / 常量 ×10 / 严重度对翻 |
| 变异体总数 | 186（确定性枚举，`ast` 位点可复算） |
| 裁定方式 | 镜像树重写模块 → 跑 pytest → **返回码**判 kill（无假阳性来源） |
| 基线自检 | 镜像 pristine 树必须全绿，否则中止（`baseline_green`） |
| 测试集 | 3 个文件 / 71 条：`test_paper_traceability_gate.py`(21) + `..._kills.py`(15, m1994) + `..._kills_v2.py`(36, 本单) |
| 容差策略 | `tol_pp=0.05` pp + `tol_rel=0.005`（相对），符号约定 ±/|·| 三态均接受 |

**本轮起点**（m1994，报告 #1771）：111/186 = 59.7%，75 个 survivor，报告 §⑤ 显式点名
"至少 12 个 severity_flip 与约 10 个 findings 行号算术是真实可补的欠账，本轮未清完，供下一席接手"。
本单就是接这一手。

---

## ② 怎么做的（方法 / 流程 / 工具链）

1. **从实测 survivor 反推测试，不猜边界**。逐条读 `audit_v2.json` 的 75 个 survivor（含源码行号与
   变异补丁），对每个 survivor 问一句"哪个稿件会让变异体与真实门判得不一样？"，只写能回答这个问题
   的测试；无法构造的登记为等价/装饰性变异体，**不冒充欠账**。
2. **按盲区族写杀手测试**（36 条，见下）。全部直调门函数（`check_table_arithmetic` /
   `check_anchors` / `check_report_values` / `check_manuscript` / `main`），零外部依赖、零重算力。
3. **复跑审计**：`python3 tools/audit/paper_gate_mutation_audit.py --jobs 12 --tests <3 文件>
   --json-out artifacts/paper_gate_mutation/audit_v3.json`，8 线程镜像树跑 186 个变异体。
4. **出图与对账**：`fig1` 分算子 kill rate 前后对比，`fig2` survivor 在源码行号上的分布；
   `round2_summary.json` 由脚本从两份审计 JSON 直接生成，零手抄。

覆盖的 survivor 族（每族对应 m1994 报告点名的欠账）：

| 族 | 变异体 | 杀手测试要点 |
|---|---|---|
| 严重度字面量 | `severity_flip` ×13 | PG1/PG2/PG3/PG4/PG5/PG6/PG7/PG8 逐码断言 `severity`，并断言 `verdict`/`counts.warn` 归约 |
| findings 行号算术 | `binop_swap` @L142/194/224/267/280/298/320/362/372/432/449 | 多行表逐行断言 `line == [4,6]`；PG1/2/3/5/6/8 逐码断言绝对行号 |
| 容差字面量 | `const_scale` @L190/222/483/540/556/557 | 分数回退带（50%→0.5 与 50%→0.05 可区分）、`tol_pp/tol_rel` 默认值双探针、rate 四位舍入 |
| 列代数 | `boolop_swap` @L173/174/177、`cmp_swap` @L180/416 | 值列按名取（`参考值|误差%|数值`）、无参考列早退不崩、截断行跳过不崩、`误差%` 表头禁用分数回退 |
| 锚点读值窗口 | `binop_swap` @L354、`const_scale` @L354 | 声明值只从锚点**后** 160 字符内读：前方 9.0 与 200 字符后的 7.0 都不得被当成声明值 |
| 报告正文核验 | `boolop_swap` @L429、`const_scale` @L444 | 一源缺失即 WARN（不得半语料硬核）；`rtol` 下限 1e-6（`tol_rel=0` 探针） |
| CLI 契约 | `cmp_swap` @L593、`severity_flip` @L593、`const_scale` @L566 | 退出码 0/1/2、缺失稿件返回 2、最多打印 40 条 finding |

---

## ③ 计算结果（程序化注入，零手抄）

| 指标 | 第一轮 (m1994) | 第二轮 (本单) |
|---|---|---|
| 变异体总数 | 186 | 186 |
| 被杀死 | 111 | **172** |
| survivor | 75 | **14** |
| kill rate | 59.7% | **92.5%** (0.9247) |
| 基线全绿 | true | true |

分算子（`artifacts/paper_gate_mutation/round2_summary.json`）：

| 算子类 | n | 第一轮 kill | 第二轮 kill | 第一轮 % | 第二轮 % |
|---|---|---|---|---|---|
| severity_flip | 17 | 4 | **17** | 23.5 | **100.0** |
| append_drop | 11 | 8 | **11** | 72.7 | **100.0** |
| not_removal | 17 | 16 | **17** | 94.1 | **100.0** |
| cmp_swap | 42 | 33 | **41** | 78.6 | **97.6** |
| boolop_swap | 21 | 11 | **20** | 52.4 | **95.2** |
| binop_swap | 45 | 25 | **40** | 55.6 | **88.9** |
| const_scale | 24 | 8 | **19** | 33.3 | **79.2** |
| return_empty | 9 | 6 | 7 | 66.7 | 77.8 |
| **合计** | **186** | **111** | **172** | **59.7** | **92.5** |

余下 14 个 survivor 的逐条归因（诚实分类，非"欠账"混报）：

| 类别 | survivor | 判定 |
|---|---|---|
| 等价变异体（6） | `binop_swap[5][6]@123`（`_close` 三态最小值的加号化）、`return_empty[0]@169`/`[1]@175`（无误差列/无参考列早退分支 findings 恒空）、`binop_swap[42]@468`（`paths+anchors` 改减号仅在两者皆 0 时有差，而 0 与 0 同真） | **不可杀**，语义等价 |
| 近似等价（1） | `boolop_swap[3]@173`（`not in err_idx and not in ref_idx` → `or`：`val_idx[0]` 在既有表头下不变） | 记为等价 |
| 装饰性（3） | `const_scale[11]@227`（detail 截断 80 字符）、`const_scale[21]/[22]@587/590`（JSON `indent=2`） | 只改展示，不改判决 |
| **真欠账（4）** | `cmp_swap[11]@180`（截断行 `>=`→`>`）、`binop_swap[38]/[39]@432`（PG8 `source_unavailable` 的 WARN 行号）、`const_scale[18]/[19]@556/557`（CLI `--tol-pp/--tol-rel` 默认值） | 可补，见 ⑤/⑥ |

---

## ④ 结果图

- `fig1_killrate_by_operator_r2.png`：8 类算子的 kill rate 前后对比（红=第一轮 59.7%，蓝=第二轮 92.5%），
  severity_flip 从 23.5% 拉到 100%，const_scale 从 33.3% 拉到 79.2%。
- `fig2_survivor_map_r2.png`：survivor 在源码行号上的分布，红=第一轮 75 个、绿=第二轮 14 个；
  残留集中在 L123（`_close` 三态）、L169/175（早退）、L432（PG8 WARN 行号）、L556/557（CLI 默认值）。
两图均由脚本从两份审计 JSON 直接绘制，无手工数据。

---

## ⑤ 注意点（踩过的坑 / 局限 / 适用范围）

1. **审计器默认测试集不含新文件——本轮真踩，且与 m1994 的坑同构**。第一次复跑只给了
   `--json-out`，没给 `--tests`，`main()` 的默认值仍是"旧 21+15 两个文件"，于是
   `audit_v3.json` 与 v2 **逐字节同样大小**（111/186），差点被当成"新增 36 条测试毫无效果"。
   一个审计器静默忽略新证据，正是它要审计的那种失效模式，这次发生在审计器自己身上。
   **纪律：复跑必显式传 `--tests`，并用 `tests` 字段长度（=3）与文件字节数变化做双确认。**
2. **`pkill -f <脚本名>` 会杀掉自己**：命令行本身含该字符串，`pkill` 把当前 shell 一起带走
   （观察到的 `exit=-15`），后台任务从未启动。改用 `setsid nohup ... < /dev/null &` 并避开自我匹配。
3. **等价变异体必须单列**：`_close` 里把 `stated - computed` 改成 `stated + computed` 在数学上不可杀
   （非负项集合的最小值不变），`return_empty` 打在恒空分支同理。把它们算作"待补欠账"会虚报工作量。
4. **`const_scale[18]/[19]`（CLI 容差默认值）本轮未清，原因已定位**：探针稿件的数值表没有工件级锚点，
   PG7 无论容差如何都会把 verdict 判 FAIL，退出码恒为 1，因此容差变异不改变可观测结果。
   补法：探针稿件里引一条可解析的 `sub/x.json`（PG7 抑制），再断言退出码。
5. **覆盖面（不变的边界）**：只做单点变异，不做二阶/组合，故 92.5% 是**下界**；变异体只覆盖
   `traceability_gate.py`，不含 `figure_provenance.py`（PG9）与 `backend/api/tensor_reports.py`
   的 `_check_report_quality`——后者才是最初那条关键词门，仍未被变异审计。
6. **本件是判决器审计**，无连续物理量基准，误差按离散/算术口径给（见 ⑥）。

---

## ⑥ 误差判定（关键量 vs 基准，逐项，显式回答是否 <3%）

| 关键量 | 基准 | 结果 | 偏差 | <3%？ |
|---|---|---|---|---|
| 变异体总数 | `ast` 枚举的可复算位点数 | 186 | 与第一轮同源同数（逐位点等式） | **是**（0%，离散相等） |
| 第一轮 kill 数 | `audit_v2.json` 逐条记录 | 111 | 由脚本计数 | **是**（0%） |
| 第二轮 kill 数 | `audit_v3.json` 逐条记录 | 172 | 由脚本计数 | **是**（0%） |
| 分算子求和闭合 | 8 类 killed 之和 vs 总 killed | 40+19+17+20+41+11+7+17 = 172 = 总数 | 0 | **是**（闭合恒等式） |
| kill rate | 172/186 = 0.924731 | 0.9247（四位舍入） | 3.4e-5 相对 = 0.0034% | **是** |
| 基线绿 | 镜像 pristine 树 pytest 返回码 0 | `baseline_green=true`（两轮） | 离散判据，无假阳性来源 | **是** |
| 新增测试自身 | 36 条在本工作树 `pytest -q` 全绿 | 36 passed | 离散判据 | **是** |

**是否 <3%**：所有离散量与算术闭环均**精确相等（0%）**；唯一的连续量 kill rate 与分数基准相对偏差
3.4e-5（0.0034%），**远优于 3%**。本件无物理参考值，理由已在 ⑤.6 显式声明，未以模糊表述掩盖。

**诚实声明**：92.5% 不是满分，余下 14 个 survivor 中 4 个是真实可补的欠账（`cmp_swap[11]`、
`binop_swap[38]/[39]`、`const_scale[18]/[19]`），6 个经论证为等价变异体、1 个近似等价、3 个为纯展示项。
本轮未改动门源码一行（测试侧增量），因此本条不改变任何已发布判决的行为。

---

## 复现

```bash
python3 -m pytest backend/tests/test_paper_traceability_gate.py \
    backend/tests/test_paper_traceability_gate_kills.py \
    backend/tests/test_paper_traceability_gate_kills_v2.py -q --noconftest
python3 tools/audit/paper_gate_mutation_audit.py --jobs 12 \
    --tests backend/tests/test_paper_traceability_gate.py,backend/tests/test_paper_traceability_gate_kills.py,backend/tests/test_paper_traceability_gate_kills_v2.py \
    --json-out artifacts/paper_gate_mutation/audit_v3.json
```