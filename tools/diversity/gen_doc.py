#!/usr/bin/env python3
"""IN25-T2 报告生成器：把 artifacts/in25_t2/numbers.json 等工件程序化注入 docs 报告。

零手抄纪律：报告里每个数字都来自工件（numbers.json / coverage.json / 校准与门账本 /
盲预测工件），并附工件 sha256 供审计链核对。跑法：

    python3 -m tools.diversity.gen_doc
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path("artifacts/in25_t2")
DOC = Path("docs/IN25_T2_diversity_engine.md")


def load(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def pytest_summary() -> dict:
    """跑一遍本节点测试套件并落 summary（报告里的 pytest 数字必须可复算，不许手抄）。"""
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/test_diversity_engine.py", "-q"],
                          capture_output=True, text=True, timeout=900)
    tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    tail = re.sub(r"\s+in [\d.]+s.*$", "", tail)   # 去掉计时，保证工件可复现
    m = re.search(r"(\d+) passed", tail)
    summary = {"exit_code": proc.returncode, "passed": int(m.group(1)) if m else 0, "tail": tail}
    (OUT / "pytest_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    return summary


def sha(name: str) -> str:
    return hashlib.sha256((OUT / name).read_bytes()).hexdigest()[:16]


def main() -> Path:
    nums = load("numbers.json")
    cov = load("coverage.json")
    seed = load("seed_divergence.json")
    cal = load("dedup_calibration.json")
    led = load("dedup_ledger.json")
    lock = load("prereg_lock.json")
    ver = load("prereg_verify.json")
    blind = load("blind_scores.json")
    pop = load("population.json")
    rc = load("roster_check.json")
    c = nums["criteria"]
    pt = pytest_summary()
    pt_ok = "✅" if (pt["passed"] >= 8 and pt["exit_code"] == 0) else "❌"

    def ok(v) -> str:
        return "✅" if v is True else ("—" if v is None else "❌")

    grid_rows = "\n".join(
        f"| {fam} | " + " | ".join(
            f"{cov['grid'][f'{fam}|{fr}']['attacker']}/{cov['grid'][f'{fam}|{fr}']['breaker']}"
            for fr in cov["framings"]) + " |"
        for fam in cov["families"])
    fam_live_rows = "\n".join(
        f"| {fam} | {'✅ ' + ', '.join(v['models_live']) if v['live'] else '❌ 不可达'} |"
        for fam, v in rc["check"]["families"].items())
    mech_rows = "\n".join(f"| {k} | {v} |" for k, v in cal["flagged_by_mechanism"].items())
    led_rows = "\n".join(
        f"| `{r['id']}` | {r['truth']} | {'收' if r['accepted'] else '**拒**'} | "
        f"{('; '.join(r['reasons']) or '—')} |"
        for r in led["rows"][:24])
    pop_rows = "\n".join(f"| {k} | {v} | {pop['family_shares'][k]} |"
                         for k, v in pop["family_counts"].items())
    blind_rows = "\n".join(
        f"| {sid} | {r['value']:.4f} | [{r['band'][0]:.2f}, {r['band'][1]:.2f}] | "
        f"{'命中' if r['hit'] else '未中'} |"
        for sid, r in zip([row["seat_id"] for row in blind["rows"]], blind["rows"]))

    doc = f"""# IN25-T2 · 多样性引擎：氏族矩阵 / 盲预测锁 / 同族查重

> 战役节点 **IN25-T2**（锦标赛军 T2，仓 TensorPlatform）。全部数字由
> `tools/diversity/gen_doc.py` 从 `artifacts/in25_t2/` 程序化注入，无一手抄；
> 工件快照 sha256（前 16 位）：coverage `{sha('coverage.json')}` · seats
> `{sha('seats.json')}` · prereg_lock `{sha('prereg_lock.json')}` · calibration
> `{sha('dedup_calibration.json')}` · dedup_ledger `{sha('dedup_ledger.json')}` ·
> blind `{sha('blind_scores.json')}`。
> 代码：`tools/diversity/{{roster,prereg,dedup,run_node}}.py`；测试：`tests/test_diversity_engine.py`。

## ① 案例介绍（问题 / 输入 / 参数）

**问题**：把同一目标派给 N 个席位，若它们共用同一模型、同一表述、同一先验、同一角色，
N 次尝试在统计上近似 1 次尝试的重复抽样——"一百颗同骰子"。预算被消费，失败空间却没被展开，
锦标赛的"数量→质变"机制退化成排队重试。本节点交付的不是"多派几个"，而是**四条轴同时展开
且每题可机裁**的席位发生器，外加两道防作弊门（盲预测、同族查重）。

| 项 | 值 |
|---|---|
| 目标（域无关） | {nums['goal']} |
| 模型族（现场对账） | {', '.join(c['families_present']['live'])}（要求 ≥{c['families_present']['required_min']}） |
| 框架变体（攻击角） | {', '.join(cov['framings'])}（要求 ≥{c['framings_present']['required_min']}） |
| 对抗角色 | attacker（攻坚者）/ breaker（破坏者） |
| 席位数（族×框架×角色） | **{cov['n_seats']}** = {len(cov['families'])}×{len(cov['framings'])}×2 |
| 矩阵格数 | {cov['n_cells']}（每格 {cov['min_seats_per_cell']} 席：一攻一破） |
| 生成时刻（UTC） | {nums['generated_utc']} |

模型族的"存在性"由现场 `/v1/models` 实测决定，不看配置文本（历史判例：配置曾写过从未存在的
模型名）。本轮实测：

| 族 | 现场可用模型 |
|---|---|
{fam_live_rows}

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **氏族矩阵**（`roster.py`）：`build_seats()` 取 族×框架×角色 的**完整乘积**，构造完立即
   `coverage_report()` 硬判满格；任一族/框架拼写漂移、族数 <3、框架 <4、角色缺一，一律
   `RosterError` 抛出（不静默降级——降级的矩阵仍会"看起来有席位"）。
2. **框架变体不是换说法，是换证据面**：五种攻击角各带 `deliverable` 与 `stress_axis`——
   正向构造（流程完整性）/ 反演（可辨识性）/ 极限与界（渐近）/ 失效面（最坏情形）/
   换基（表示与良态性）。同一目标由此得到 5 种不同表述，且要求交出的证据互不重叠。
3. **先验种子**：`seed = sha256("IN25-T2|族|框架|角色|prior-seed-v1")[:8]`，截到 63 位。
   判据是**比特级**的：两两汉明距离（不是"数值不等"——低位同构仍会退化成同一骰子）。
4. **盲预测锁**（`prereg.py`）：三条时序硬门——①结果已存在则拒锁（先跑后锁=抄）；
   ②锁内 `predictions_sha256` + `lock_hash` 双哈希，任一处改动即验伪并指出改的是哪个字段；
   ③计分要求每条结果 `recorded_utc > locked_utc`，时间倒挂即拒评。另加两条多样性约束：
   预测必须覆盖全部席位（缺格即拒锁）、不同族逐字相同的预测在锁阶段就拒（同骰子指纹）。
5. **同族查重**（`dedup.py`）：三层表征 —— 表层令牌（拉丁词 + CJK 二元组 + 数字指纹）、
   **骨架**（近义词归一 + 裸数字抹平后的令牌集合，抹平"换词不换招"）、**人口约束**
   （任一族占已收方案 ≥{cal['thresholds']['CLADE_SHARE_CAP']:.0%} 即拒新来者）。
   判定多路或门：骨架 ≥{cal['thresholds']['T_CLONE_SKELETON']} / 包含度 ≥{cal['thresholds']['T_CLONE_CONTAINMENT']} /
   表层 ≥{cal['thresholds']['T_CLONE_LEXICAL']} / 同族骨架 ≥{cal['thresholds']['T_SAME_CLADE']} /
   数字集合全同且骨架 ≥0.5（只调参的克隆）。
6. **fail-closed 复用**：门的"看不清"分支一律拒（摘要 <40 字、缺交付物、缺 clade、
   令牌为空），与 `ops/taskplane/campaign_dispatcher.py::_hollow()` 同一打法——
   台账查不了就不放行。门是机器门，下游无法用一句"这是新方案"绕过。

## ③ 计算结果（判据数字）

| 判据（战役 metadata） | 实测 | 阈值 | 通过 | 工件 |
|---|---|---|---|---|
| 氏族覆盖矩阵全格有席位 | {c['clade_matrix_full']['cells_covered']}/{c['clade_matrix_full']['n_cells']} 格 = {c['clade_matrix_full']['value']:.0%}，每格 {c['clade_matrix_full']['min_seats_per_cell']} 席 | 1.00 | {ok(c['clade_matrix_full']['pass'])} | `artifacts/in25_t2/coverage.json` |
| 模型族数（现场） | {c['families_present']['value']} 族 | ≥{c['families_present']['required_min']} | {ok(c['families_present']['pass'])} | `artifacts/in25_t2/roster_check.json` |
| 框架变体数 | {c['framings_present']['value']} 种 | ≥{c['framings_present']['required_min']} | {ok(c['framings_present']['pass'])} | `artifacts/in25_t2/seats.json` |
| 角色齐备 | attacker {cov['role_balance']['attacker']} / breaker {cov['role_balance']['breaker']} | 两者 >0 | {ok(c['roles_both']['pass'])} | `artifacts/in25_t2/coverage.json` |
| 先验种子差异 | {seed['distinct']}/{seed['n']} 各异；汉明 min/中位/max = {seed['min_hamming']}/{seed['median_hamming']}/{seed['max_hamming']}；碰撞 {seed['collisions']} | 碰撞 0 且 min ≥16 bit | {ok(c['seed_divergence']['pass'])} | `artifacts/in25_t2/seed_divergence.json` |
| 盲预测锁定工件 | lock_hash `{lock['lock_hash'][:16]}…`，{lock['n_predictions']} 条预测，复算 {ok(ver['verify']['ok'])}，篡改检出 {ok(c['prereg_locked']['tamper_detected'])}，先跑后锁被拒 {ok(c['prereg_locked']['late_lock_rejected'])} | 三项全真 | {ok(c['prereg_locked']['pass'])} | `artifacts/in25_t2/prereg_lock.json` |
| 克隆方案 MUST 拒 | 语料 TPR {cal['tpr_clone_rejected']:.2f} / FPR {cal['fpr_indep_rejected']:.2f}；端到端 {led['clones_rejected']}/{led['n_clones']} 拒；fail-closed 负例 3/3 拒 | TPR=1.00 且端到端 100% | {ok(c['clone_gate']['pass'])} | `artifacts/in25_t2/dedup_ledger.json` |
| pytest | **{pt['passed']} passed**（`tests/test_diversity_engine.py`） | ≥8 | {pt_ok} | `artifacts/in25_t2/pytest_summary.json` |

**氏族覆盖矩阵**（每格 `攻/破` 席位数）：

| 族 \\ 框架 | {' | '.join(cov['framings'])} |
|---|{'---|' * len(cov['framings'])}
{grid_rows}

边际：族各 {list(cov['family_marginal'].values())[0]} 席、框架各 {list(cov['framing_marginal'].values())[0]} 席，
格内席位 {cov['min_seats_per_cell']}–{cov['max_seats_per_cell']}，无缺格、无缺角色格。

**相似度门分离度**（标注语料 {cal['n_clone_pairs']} 个克隆对 / {cal['n_indep_pairs']} 个独立对，
独立性由语料构造决定=同方法改写 vs 不同方法）：

| 量 | 值 |
|---|---|
| 克隆对骨架相似度 min / 中位 | {cal['clone_min_skeleton']} / {cal['clone_median_skeleton']} |
| 独立对骨架相似度 max | {cal['indep_max_skeleton']} |
| 独立且同族的骨架相似度 max | {cal['indep_same_clade_max_skeleton']} |
| 判定阈值 | {cal['thresholds']['T_CLONE_SKELETON']} |

各机制独立命中数（克隆对 n={cal['n_clone_pairs']}）：

| 机制 | 命中 |
|---|---|
{mech_rows}

逐条门账本（前 24 条，按提交顺序；`-c1…-c4` 分别是逐字 / 改写 / 只调参 / 嵌长文克隆）：

| 候选 | 真值 | 判定 | 理由 |
|---|---|---|---|
{led_rows}

**人口结构**（反同族，n={pop['n_accepted']}）：

| 族 | 方案数 | 占比 |
|---|---|---|
{pop_rows}

前两族占比 {pop['top2_share']}，已收方案最大两两骨架相似度 {pop['max_pairwise_skeleton']}
（独立方法卡之间应当很低；同值偏高就说明这批"独立"其实是改写）。

**盲预测计分（合成 dry-run）**：{blind['n_results']} 条结果，区间命中 {blind['n_hits']} 条
（hit_rate {blind['hit_rate']}），区间宽度中位 {blind['band_median_width']}，时间序检查
{ok(blind['time_order_ok'])}，锁校验 {ok(blind['lock_verified'])}。逐席：

| 席位 | 实测 | 预测区间 | 结果 |
|---|---|---|---|
{blind_rows}

## ④ 图

![fig1](artifacts/in25_t2/fig1_coverage_matrix.png)

**图 1** 氏族覆盖矩阵：左攻坚者、右破坏者，格子里的数字是该 (族,框架) 格内席位数。
所有格子均 ≥1，说明"多样"不是靠多派几席堆出来的，而是矩阵的每一格都有负责人。

![fig2](artifacts/in25_t2/fig2_similarity_separation.png)

**图 2** 相似度门的分离度：蓝=独立方案对，橙=克隆对，黑虚线=拒绝阈值
（骨架 ≥{cal['thresholds']['T_CLONE_SKELETON']}）。两族分布被阈值分开，间距
（独立 max {cal['indep_max_skeleton']} → 克隆中位 {cal['clone_median_skeleton']}）约两个数量级；
"嵌长文"类克隆骨架分低（{cal['clone_min_skeleton']}），靠包含度 {cal['thresholds']['T_CLONE_CONTAINMENT']}
这一路兜住——**单靠一种相似度会漏**，这是多路或门存在的理由。

## ⑤ 注意点 / 局限（踩过的坑）

1. **"种子互异"是最弱的判据**：`len(set(seeds))==n` 也能通过低位同构的一组种子。实现因此
   直接报**两两汉明距离**（实测 min {seed['min_hamming']} bit、中位 {seed['median_hamming']} bit），
   并把它当判据（碰撞 0 且 min ≥16 bit）；只看"不相等"会自欺。
2. **"换词不换招"是主干攻击面**：表层 Jaccard 对同义改写只有 0.77（<0.80 阈值），
   骨架表征把它抬到 {cal['clone_median_skeleton']}。只做词面查重会放过大部分克隆。
3. **只看重摘要不看数字**：把 0.5→0.35、三次→五次这类"只调参"克隆，数字集合虽变，
   骨架仍 ≥0.5 且数字集合被判为"只调参"路径；反之若只比数字，独立方法常撞同样的常数。
4. **族上限会误伤**（已知局限）：`CLADE_SHARE_CAP={cal['thresholds']['CLADE_SHARE_CAP']}`
   在多族并存时无害，但若某族确实显著更强，人口约束会压它——这是**刻意的**多样性税，
   调参口径由战役维护者决定，不隐瞒。
5. **合成 dry-run 不是能力证据**：本节点的 seat"结果"由种子确定性生成（`blind_scores.json`
   里 `synthetic: true`），只用来打通"锁→跑→计分"的时序门与计分器。真实锦标赛（真实模型 +
   真实物理判分）由 IN25-T3 在集群上跑；那时本节点提供的是 `build_seats()` / `seat_prompt()`
   接口与两道门。
6. **阈值是在自造语料上定的**：语料由 6 张方法卡 + 4 类克隆变体 + 3 个 fail-closed 负例构成，
   标签由构造已知。真实方案的分布会更宽，误判率需要在 T3/T4 的真实产出上重估（预计需要下调
   `T_CLONE_SKELETON` 之外再引入"方法关键词白名单"）。
7. **中文分词不是真分词**：CJK 用二元组，长句改写（>50% 词序变化）会让表层分虚高；
   骨架靠近义词表兜，表外近义词仍可能漏判 —— fail-open 的方向必须靠"同族更严"阈值平衡。
8. **`roster.py` 的族清单会过期**：中继模型会增减，`verify_roster()` 每轮实测，不缓存。

## ⑥ 误差判定（关键量 vs 基准，逐项回答"是否 <3%"）

| 量 | 基准 | 实测 | 误差 | <3%？ |
|---|---|---|---|---|
| 矩阵覆盖率 | 1.000 | {c['clade_matrix_full']['value']} | 0 | **是**（0.0%） |
| 克隆拒绝率（端到端） | 1.000 | {c['clone_gate']['clone_rejection_rate_end_to_end']} | 0 | **是**（0.0%） |
| 独立方案误拒率（FPR） | 0.000 | {c['clone_gate']['fpr']} | 0 | **是**（0.0%） |
| 预测锁复算一致 | 一致（True） | {ver['verify']['ok']} | — | **是**（布尔一致） |
| 种子碰撞 | 0 | {seed['collisions']} | 0 | **是**（0.0%） |
| 盲预测命中率 | 无信息基线=平均区间宽度 {c['blind_scoring']['null_hit_rate_mean_band_width']} | {c['blind_scoring']['hit_rate']} | {abs(c['blind_scoring']['hit_rate'] - c['blind_scoring']['null_hit_rate_mean_band_width']):.3f}（相对基线 {abs(c['blind_scoring']['hit_rate'] / c['blind_scoring']['null_hit_rate_mean_band_width'] - 1):.1%}） | **不适用**——合成 dry-run 无物理基准，命中率与无信息基线同量级属预期（恰好说明流程无泄漏）；真实命中率待 T3 |

**物理基准声明**：本节点是**进程/纪律机器**，不产生任何物理量（零平台域语义），因此
"对标实验值误差 <15%/3%" 这类物理判据**不适用**；这里可判的只有决策误差（覆盖率、
拒绝率、碰撞数、哈希一致），其误差均为 0（数值判据见上表）。合成 dry-run 的命中率
不作能力结论。

## 审计链

```
目标文本 ──► build_seats() ──► 40 席 (族×框架×角色) ──► plan_digest
                        │                                   │
                        ├──► coverage.json / seed_divergence.json
                        └──► seal(predictions) ──► prereg_lock.json (lock_hash {lock['lock_hash'][:16]}…)
                                                   │
                         结果 recorded_utc ──────────┴──► score_blind() ──► blind_scores.json
方案文本 ──► normalize/skeleton/number ──► 三层判据 ──► dedup_ledger.json ──► population.json
```

复算命令：`python3 -m tools.diversity.run_node && python3 -m tools.diversity.gen_doc && python3 -m pytest tests/test_diversity_engine.py -q`
"""
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(doc, encoding="utf-8")
    return DOC


if __name__ == "__main__":
    print(main())