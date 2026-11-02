# PG9 · 图内数据溯源门（figure-data provenance gate）

> 补的是 `tools/paper_gate/traceability_gate.py`（PG1–PG8）与本文档 §5.2 亲自标注的缺口：
> 「图内数据合成不可静态判定；下一步应做 `generate_figures.py` 沙箱执行 + 与工件数值 diff」。
> 本件把那句话落成可执行判决器：`tools/paper_gate/figure_provenance.py`。

## 1. 问题（为什么文字门抓不到）

论文线两次废稿的根因不是文笔，而是**图**：

* `paper-draft-ice-wave-mechanics-r2`（m1244）：H3「figs 2-5 synthesised」，图内曲线无任何工件支撑；
* `paper-draft-ice-ridge-keel-grounding`（m1122）：表 2/表 4 与图不一致。

PG1–PG8 判的是**正文**：路径能否解析、报告 id 是否存在、表格算术、行内误差百分数。
生成器把数据写在 `ax.plot(...)` 的实参里——正文完全干净，`报告 #N` 一应俱全，门照样 PASS。
区别只在：那条曲线的数字，究竟来自工件树，还是来自脚本里一行 `np.linspace(...)`。

## 2. 方法（执行 + 数值对账，不判文风）

1. **沙箱执行**：子进程以 `runpy` 跑生成器，`MPLBACKEND=Agg`，cwd 取就近 `.git` 根（生成器普遍用仓内相对路径）。超时/崩溃一律记为「未核验」，绝不记 PASS。
2. **钩子录制**：在 `Axes.plot/semilogx/semilogy/loglog/step/stem/scatter/bar/barh/errorbar/fill_between/contour/contourf/pcolormesh/imshow/matshow` 上挂钩，捕获每个美术对象的数值实参与关键字实参（`yerr`/`xerr`/`levels` 等），连同**生成器源码调用行号与原文**（`traceback` 定位到 `__file__`）。`Figure.savefig` 再把美术对象绑定到落盘图名。
3. **主/辅序列判定**：`plot/scatter/errorbar/bar` 的主序列取第二个大数组（y / heights / 误差主体），`contour*/pcolormesh` 取最后一个（场量 Z），`imshow` 取第一个（像素阵）。
4. **工件语料**：递归收 `json/csv/npy/npz` 的数值序列——JSON 支持 `$.cf_curve` 与记录数组列投影 `$.runs[*].Cf`，CSV 按列。去重按数值摘要，容量上限（4M 值）显式写在策略里。
5. **对账**：`np.allclose(rtol=1e-6, atol=1e-12)`，长度不等时做滑窗子序列匹配（子区间引用也算溯源，标 `window@k`）。命中即输出**可直接粘贴的引用** `path#pointer`，并可被 `citation_values()` 反解回数值。
6. **解析声明**：合法解析曲线（ITTC-1957 摩擦线、O&O B 系列回归）本就不来自工件。`figprov.json` 允许声明 `{"kind":"analytic","citation":…,"expr":…}`，门记 `ANALYTIC_DECLARED`；**citation 或 expr 为空直接 FAIL**——「声明过」不等于「可复核」。

### 判决码

| 码 | 级别 | 含义 |
|---|---|---|
| `PG9_synthesised_figure_data` | FAIL | 主数据序列在工件树中无对应值，也无解析声明（H3 失效模式） |
| `PG9_declaration_missing_citation` | FAIL | 有声明但缺 citation/expr |
| `PG9_partial_provenance` | WARN | 主序列可溯源，但 x / yerr / 网格等辅助序列无对应值（坐标轴或误差棒可能是编的） |
| `PG9_figure_without_recorded_data` | WARN | 图已落盘但零绘图数据（示意图/子进程绘图/非 matplotlib） |
| `PG9_generator_crash` | WARN | 生成器抛异常 → 未核验，如实记未核验 |
| `PG9_hook_missed` | WARN | 输出目录出现图像但零 savefig → 钩子可能未生效 |

## 3. 在真实生成器上的判决（`artifacts/figure_provenance/audit_results.json`，程序化生成）

| 生成器 | 判决 | 主序列 | 可溯源 | 关键发现 |
|---|---|---|---|---|
| `tools/gen_benchmark_integrity_figures.py` | WARN | 8 | 8（100%） | 数据取自 artifacts/，2 处辅助序列（网格/标注线）无对应值 |
| `scripts/gen_provenance_figures.py` | **FAIL** | 2 | 0 | 2 条主序列为脚本内构造，工件树无此值；另有 1 张图零绘图数据 |
| `docs/audit/gen_figures.py` | **FAIL** | 3 | 0 | 3 条主序列为脚本内构造 |
| **合计** | 2 FAIL / 1 WARN | **13** | **8（61.5%）** | 现有平台图里近四成曲线无法从工件复算 |

判决由图 `artifacts/figure_provenance/pg9_verdict_mix.png`（判决构成）与 `pg9_coverage.png`（逐生成器可溯源率）给出；两图均由 `artifacts/figure_provenance/audit_results.json` 直接计算，零手抄。

> 平台报告：**#1526**（论文级，含判决构成图与可溯源率图）。交付提交：PG9 门 + 13 例回归 + 真实语料审计。

## 4. 工具链与复现

```bash
# 单生成器
python3 tools/paper_gate/figure_provenance.py \
  --generator scripts/gen_provenance_figures.py \
  --artifact-root artifacts --out-fig-dir artifacts \
  --json-out /tmp/figprov.json        # 退出码 0 无 FAIL ｜ 1 有 FAIL ｜ 2 用法/超时
# 回归
python3 -m pytest --noconftest backend/tests/test_figure_provenance_gate.py -q   # 13 passed
```

`--noconftest` 与 PG1–PG8 同位理由：本门是独立工具，不应被 `backend/tests/conftest.py` 拉起整个 app 拖死。

## 5. 注意点 / 局限 / 适用范围

1. **判值不判流向**：与工件数值**恰好相等**的合成曲线会被判 RESOLVED。
   `test_synthesised_fails_only_when_no_artifact_holds_it` 把这个保守方向钉成行为契约——
   宁可漏杀（要求作者自查来源），不误杀（honest FAIL 不得覆盖）。
2. 少于 3 点的序列不判（计入 `skipped_short_series`，不静默）。
3. 非 matplotlib 出图（PIL/gnuplot/LaTeX）不在钩子范围，出现图像但零 savefig 时记 `PG9_hook_missed`。
4. 子进程里绘图（`subprocess` 调另一个脚本）会退化成「零绘图数据」WARN，需手工降级复核。
5. 平行滑动窗口匹配对超长序列有探针上限（200k 次），超限即返回未匹配——偏保守。
6. 门不判物理：公式对不对、网格收不收敛、基准是否恰当，仍归审稿人 / verifier 席。

## 6. 误差判定（离散判决器，逐项）

本件是判决器，无连续量解析基准，故按离散口径回答「是否 <3%」并声明原因：

| 关键量 | 基准 | 实测 | 是否达标 |
|---|---|---|---|
| 合成数据检出 | 3 个真实生成器 + 构造用例中的 5 条已知合成主序列 | 5/5 命中 | 漏检 0 |
| 工件支撑曲线误杀 | `gen_benchmark_integrity_figures.py` 的 8 条 artifacts 支撑序列 | 8/8 RESOLVED | 误杀 0 |
| 声明缺 citation 的放行 | 构造用例（无 citation 声明） | FAIL | 未放行 |
| 崩溃生成器的判定 | RuntimeError 用例 | WARN（非 PASS） | 未粉饰 |
| 单元回归 | 13 例（含窗口匹配、CSV/NPZ 引用反解、CLI 退出码） | 13 passed | — |

**是否 <3%**：无连续量基准 → 该问在本件不适用；改用离散指标（检出 5/5、误杀 0/8、13 例绿）。
真实语料上的整体可溯源率 61.5% 是**现状测量**，不是本门的精度指标。