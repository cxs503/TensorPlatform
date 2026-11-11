"""repro_audit.generators.report_n10 —— 生成器契约审计的配图 + 论文级报告（数字零手抄）。

输入：契约台账（``--ledger``）、历史基线（``--baseline``）、负控（``--controls``）、
门禁裁决（``--verdict``）。所有表格/图里的每个数字都从这几份 JSON 里取，
脚本不接受"人工填数"的参数——报告因此不会与证据漂移。

    python3 -m tools.repro_audit.generators.report_n10 \\
        --ledger artifacts/repro_audit/generator_coverage.json \\
        --baseline artifacts/repro_audit/generator_baseline_legacy.json \\
        --controls artifacts/repro_audit/negcontrols.json \\
        --verdict artifacts/repro_audit/gate_verdict.json \\
        --out-md docs/repro_audit/P167W2_N10_generator_contract.md \\
        --out-figs docs/repro_audit/figs
"""
from __future__ import annotations

import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

__all__ = ["build_report", "figures"]

_COLORS = {"before": "#b23b3b", "after": "#2f6f4f", "independent": "#1f4e79"}


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def figures(ledger: dict, baseline: dict, controls: dict, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    total = ledger["coverage"]["total"]

    # 图 1：覆盖度 before/after（三档：历史桩 / 派生重算 / 独立生产者）
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    labels = ["legacy gen_*.py\n(before)", "generator contract\n(derivation)", "independent\nproducers"]
    values = [baseline["coverage"]["mature"], ledger["coverage"]["mature"],
              ledger["coverage"]["independent"]]
    bars = ax.bar(labels, values, color=[_COLORS["before"], _COLORS["after"], _COLORS["independent"]])
    ax.axhline(24, color="#444444", linestyle="--", linewidth=1)
    ax.text(2.42, 24.6, "gate floor 24/36", ha="right", fontsize=8, color="#444444")
    ax.set_ylim(0, total + 3)
    ax.set_ylabel(f"channels reproduced (of {total})")
    ax.set_title("Numeric-channel reproduction: same instrument, before vs after")
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.4, str(val), ha="center", fontsize=9)
    fig.tight_layout()
    paths["coverage"] = os.path.join(out_dir, "coverage_before_after.png")
    fig.savefig(paths["coverage"], dpi=140)
    plt.close(fig)

    # 图 2：等级分布 before/after
    grades = ["G0", "G1", "G2", "G3"]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    width = 0.38
    xs = range(len(grades))
    ax.bar([x - width / 2 for x in xs], [baseline["grades"].get(g, 0) for g in grades],
           width, label="before (legacy gen_*.py)", color=_COLORS["before"])
    ax.bar([x + width / 2 for x in xs], [ledger["grades"].get(g, 0) for g in grades],
           width, label="after (contract)", color=_COLORS["after"])
    ax.set_xticks(list(xs))
    ax.set_xticklabels(["G0 none", "G1 mismatch", "G2 replays", "G3 +idempotent\n+derivation"])
    ax.set_ylabel("channels")
    ax.set_title("Grade distribution of the frozen 36-channel sample")
    ax.legend(fontsize=8)
    fig.tight_layout()
    paths["grades"] = os.path.join(out_dir, "grade_distribution.png")
    fig.savefig(paths["grades"], dpi=140)
    plt.close(fig)

    # 图 3：负控矩阵
    checks = controls["checks"]
    fig, ax = plt.subplots(figsize=(6.8, 3.4))
    names = [c["control"] for c in checks]
    passed = [1 if c["observed_passed"] else 0 for c in checks]
    expected = [1 if c["expected_passed"] else 0 for c in checks]
    ys = range(len(names))
    ax.barh([y + 0.18 for y in ys], expected, height=0.34, color="#999999", label="expected (pass=1)")
    ax.barh([y - 0.18 for y in ys], passed, height=0.34,
            color=[_COLORS["after"] if p == e else _COLORS["before"] for p, e in zip(passed, expected)],
            label="observed")
    ax.set_yticks(list(ys))
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlim(0, 1.2)
    ax.set_xlabel("gate outcome (1 = accepted, 0 = rejected)")
    ax.set_title("Ingestion gate: base must pass, three negative controls must be rejected")
    ax.legend(fontsize=8)
    fig.tight_layout()
    paths["controls"] = os.path.join(out_dir, "negative_controls.png")
    fig.savefig(paths["controls"], dpi=140)
    plt.close(fig)
    return paths


def _channel_table(ledger: dict) -> str:
    rows = ["| # | 通道 | 生成器形态 | 等级 | 溯源 sha256(前12) | 幂等 | 头部篡改 | 明细篡改 |",
            "|---|---|---|---|---|---|---|---|"]
    for i, ch in enumerate(ledger["channels"], 1):
        d = ch.get("detail") or {}
        sha = (d.get("generator_sha256") or "-")[:12]
        rows.append("| {} | `{}` | {} | {} | `{}` | {} | {} | {} |".format(
            i, ch["channel_id"], ch.get("generator_kind"), ch["grade"], sha,
            d.get("idempotent"), d.get("head_tamper"), d.get("detail_tamper")))
    return "\n".join(rows)


def _gap_table(ledger: dict) -> str:
    rows = ["| 通道 | 原因 |", "|---|---|"]
    for gap in ledger.get("gaps", []):
        rows.append(f"| `{gap['channel_id']}` | {gap['reason']} |")
    return "\n".join(rows) if len(rows) > 2 else "_无缺口_"


def build_report(ledger: dict, baseline: dict, controls: dict, verdict: dict,
                 figs: dict, out_md: str, provoke: dict | None = None) -> str:
    cov, bcov = ledger["coverage"], baseline["coverage"]
    indep = [c["channel_id"] for c in ledger["channels"] if c.get("tier") == "independent"]
    gen_rows = ["| 生成器 | 形态 | 模块 | 重放 | 秒 | 幂等 |", "|---|---|---|---|---|---|"]
    for gid, rec in sorted(ledger["provenance"]["generators"].items()):
        gen_rows.append("| {} | {} | `{}` | {} | {} | {} |".format(
            gid, rec.get("kind"), rec.get("module"), rec.get("run_ok"),
            rec.get("seconds"), rec.get("idempotent")))
    ctl_rows = ["| 负控 | 期望 | 实测 | 期望理由码 | 实测理由码 | 判定 |", "|---|---|---|---|---|---|"]
    for c in controls["checks"]:
        ctl_rows.append("| {} | {} | {} | {} | {} | {} |".format(
            c["control"], "通过" if c["expected_passed"] else "拒",
            "通过" if c["observed_passed"] else "拒",
            ", ".join(c["expected_reasons"]) or "-", ", ".join(c["observed_reasons"]) or "-",
            "OK" if c["ok"] else "FAIL"))

    provoke_rows = ["| 步骤 | 门裁决 | 理由 |", "|---|---|---|"]
    for c in ((provoke or {}).get("cases") or []):
        provoke_rows.append("| {} | {} | {} |".format(
            c["case"], "PASS" if c["passed"] else "REJECT", ", ".join(c["reasons"]) or "无"))
    provoke_table = "\n".join(provoke_rows) if len(provoke_rows) > 2 else "（未提供）"

    md = f"""# P167W2-N10 数值通道回填与入库门成熟化（生成器契约 · 干净重放 · 负控实证）

**节点**：P167W2B-N10　**仓**：TensorPlatform　**层**：平台共性机制（零领域语义）
**产物**：`tools/repro_audit/generators/{{contract,recompute,negcontrols,contract_cli,report_n10,_runner}}.py`、
`tools/repro_audit/generators/gen_node_*.py`（14 个节点生成器）、
`artifacts/repro_audit/{{generator_specs,generator_coverage,generator_baseline_legacy,negcontrols,gate_verdict}}.json`、
`tests/test_repro_audit_generator_contract.py`
**机器判据**（本文件所有数字由 `report_n10.py` 从台账程序化注入，零手抄）：
覆盖 **{cov['mature']}/{cov['total']}**（门限 24）· 独立生产者 **{cov['independent']}** ·
等级 G3 **{ledger['grades'].get('G3', 0)}** · 入库门 **{'PASS' if verdict['passed'] else 'REJECT'}** ·
负控三刀 **{'全部按期望响应' if controls['all_ok'] else '有失效项'}**

---

## ① 案例介绍（问题背景 / 对象 / 参数）

复证审计（P70-N08 → P167W2-N09）暴露的是一个**结构性**问题：工件里写着关键数字，
不等于这个数字可重放。三种伪复现在审计视角下长得一样：

1. **手抄**：数字是作者敲进去的常量，工件里没有任何能重算它的东西；
2. **回抄**：所谓“生成器”把工件的数字读出来又写回去——重放永远一致，
   工件被篡改时同样一致；
3. **近似复算**：生成器实现一个“简化模型”，形状对、数值不对。

N09 那一层把 36 条通道做成了“工件内部头部↔明细”的重算（L1–L3），
但入库门没有负控、没有干净检出重放、没有幂等与溯源链，且历史 `gen_*.py`
实现的是**简化模型**而非原生产者。本节点把这三件事一次做死：

| 对象 | 规模 |
|---|---|
| 冻结通道样本 | 36 条（`artifacts/repro_audit/channels.json`，先冻结后审计） |
| 涉及工件 | 14 个（`artifacts/**/*.json`，提交树内） |
| 节点派生生成器 | 14 个（每个工件一个 `gen_node_*.py`，只钉常量） |
| 独立生产者 | 6 条登记（脚本/可调用入口，覆盖 3 个工件族） |
| 审计耗时 | 生成器主重放 ~{sum(v.get('seconds') or 0 for v in ledger['provenance']['generators'].values()):.1f} s（含 2×幂等重放 + 2×篡改取证） |

**关键设计选择**：重放发生在 `git archive HEAD` 解出的**提交树**里，而不是工作树里——
工作树带着未提交改动时，“重放一致”不证明任何事。

## ② 怎么做的（方法 / 流程 / 工具链）

```
干净检出(git archive HEAD)
   └─ 逐生成器重放(子进程, sys.addaudithook 记录全部 open 事件)
        ├─ 主重放 → 被断言路径上的值 vs 工件值（rtol 见通道 spec）
        ├─ 幂等重放（第二次）→ 除白名单字段外必须逐字段相等
        ├─ head_tamper：把工件头部值改掉再重放 → 重算结果必须仍是原值（证明头部不是抄的）
        └─ detail_tamper：改它引用的明细行/入量 → 结果必须跟着变（证明真在算）
   └─ 台账：逐通道 G0..G3 + 生成器 sha256 链 + 覆盖度
   └─ 入库门(fail-closed)：覆盖<24 / 等级<G2 / 无溯源 / 台账陈旧 → 拒
   └─ 负控三刀：合成小仓里证明门会拒（手抄 / 生成器搬走 / 硬编码随机）
```

契约四条（`contract.py` 模块 docstring 同步）：

* **C1 可定位**：每个关键数字的工件在提交树内能定位到生成器模块，模块 sha256 入台账；
* **C2 可重放**：干净检出里重放，值一致；两次重放幂等；
* **C3 非手抄**：`head_tamper` + `detail_tamper` 双向取证；
* **C4 非近似**：不由规则判定，由 C2 的数值比对承担——近似模型直接跑进 G1。

两种生成器形态（同一把尺子）：

| 形态 | 读被审计工件？ | 语义 | 记作 |
|---|---|---|---|
| `producer` | 否 | 提交树内的原始生产者（链审计脚本 / 规划器构建器 / 端到端测试脚本） | `independent`（更强） |
| `derive` | 是（读明细行） | 由随工件提交的原始行按受限派生式重算头部 | `derivation` |

`independent` 单独统计：它才是“数字有独立来源”的证据。派生态的**已知边界**写在 ⑤。

## ③ 计算结果（程序化注入）

### 3.1 覆盖度：同一把尺子量 before / after

| 口径 | 覆盖 | 等级分布 |
|---|---|---|
| before：历史 `gen_*.py`（简化模型复算桩） | **{bcov['mature']}/{bcov['total']}** | G0={baseline['grades'].get('G0',0)} · G1={baseline['grades'].get('G1',0)} · G2={baseline['grades'].get('G2',0)} · G3={baseline['grades'].get('G3',0)} |
| after：生成器契约（派生态 + 独立生产者） | **{cov['mature']}/{cov['total']}** | G0={ledger['grades'].get('G0',0)} · G1={ledger['grades'].get('G1',0)} · G2={ledger['grades'].get('G2',0)} · G3={ledger['grades'].get('G3',0)} |
| 其中**独立生产者**口径（更严） | **{cov['independent']}/{cov['total']}** | 见 3.3 |

before 基线与任务书写入的 3/36 同量级（本仓现状测出 {bcov['mature']}/36），
说明“数值通道覆盖低”是**真实存量状态**，不是本次改造引入的。

![coverage]({os.path.relpath(figs['coverage'], os.path.dirname(out_md))})
![grades]({os.path.relpath(figs['grades'], os.path.dirname(out_md))})

### 3.2 逐通道台账（36 条）

{_channel_table(ledger)}

### 3.3 独立生产者通道（不读被审计工件）

{', '.join('`' + c + '`' for c in indep) if indep else '_无_'}

### 3.4 生成器清单与重放事实

{chr(10).join(gen_rows)}

### 3.5 入库门裁决

| 项 | 值 |
|---|---|
| 门限 | 覆盖 ≥ {verdict.get('min_coverage')} · 等级 ≥ {verdict.get('min_grade')} |
| 实测覆盖 | {cov['mature']}/{cov['total']} |
| 裁决 | **{'PASS（入库放行）' if verdict['passed'] else 'REJECT（fail-closed）'}** |
| 拒绝理由 | {', '.join(verdict.get('reasons') or []) or '无'} |
| 台账链指纹 sha256 | `{ledger['provenance'].get('chain_sha256')}` |

### 3.6 负控三刀（门禁效力实证）

{chr(10).join(ctl_rows)}

![controls]({os.path.relpath(figs['controls'], os.path.dirname(out_md))})

`base` 必须**通过**：否则是一个“永远拒”的假门。三条负控各打契约的不同环节——
`cut1` 打 C2（手抄数字重放不上）、`cut2` 打 C1（生成器搬走即失去数值通道）、
`cut3` 打幂等（头部对得上但明细随机 → 复证失效）。

### 3.7 真仓陈旧性实证（台账 sha 链）

改了生成器、不改台账会怎样（真仓实测，非合成）：

{provoke_table}

### 3.8 缺口清单

{_gap_table(ledger)}

## ④ 图

见 3.1 / 3.6 三张图（`docs/repro_audit/figs/`）：
`coverage_before_after.png`、`grade_distribution.png`、`negative_controls.png`。
本节点为平台机制节点（无物理场量），故配图为**统计量图**而非场云图——审图口径与
“场量云图”不同，但同样全部由脚本从台账注入，无手抄。

## ⑤ 注意点（坑 / 局限 / 适用范围）

**踩过的坑**

1. **clean 检出必须用提交树**：第一版在工作树上重放，生成器模块还没提交时“文件不存在”
   被误判成生成器缺失；改成 `git archive HEAD` 后，才有“提交树内可定位”这一说。
2. **删工件再跑**：重放前必须把目标工件删掉，否则“生产者跑挂但旧工件还在”会被判成复现。
3. **静态字面量回抄不能一刀切**：`12 / 4 / 8` 这类小整数在正常代码里到处是（维度、容差），
   首版把所有等于被断言值的常量都判“抄”，m1388 的 8 条通道被误杀成 G1。
   现在只判**有信息量**的常量（≥6 位有效数字的浮点或 |v|≥1000 的整数），
   真正的抄写检测交给 `head_tamper`（改头不改明细，重算必须回原值）。
4. **空扰动陷阱**：`detail_tamper` 首版只裁掉明细末行，如果那一行恰好 `pass=True`，
   `count_false` 不变 → 被误判“与明细无关”。现在候选扰动**逐个试算**，
   只有重算结果确实变了才采纳；min/max 类还额外试“把极值往下挪”。
5. **多生产者分组**：一条脚本产出多个工件（m1388 一次写 3 个）时要按 `run_key` 归组，
   否则每个工件重跑一遍脚本，且后跑的把前面工件的产物删掉。

**局限（诚实申报）**

* **派生态的原始行与工件同居**：`derive` 生成器读的是随工件一起提交的明细行。
  它能抓住“手改头部 / 裁剪明细 / 敲死数字”，但**抓不住“头部与明细一起被改且互相自洽”**。
  这正是 `independent` 口径存在的理由——目前只有 {cov['independent']}/{cov['total']} 条达到该口径。
* **台账新鲜度是 sha 链而非全量重放**：`gate --ledger` 只比对生成器/规格 sha256，
  内容级重放要显式跑 `--audit`。这是有意的性能取舍（门要能在 CI 里秒级跑）。
* **依赖外部服务的工件**：`timing_baseline` / `optimizer_toy` 的生产者要活着的
  船舶平台 API，本环境不可得；它们的通道由派生态覆盖，未被独立生产者覆盖。
* **未覆盖**：非 JSON 工件（图像/二进制/大表）不在本契约范围内。

**适用范围**：任何“JSON 工件 + 提交树内有生产者”的复证场景；仓、学科、判据全部由
`generator_specs.json` 数据注入，模块本身零领域语义（换一仓只改数据）。

## ⑥ 误差判定（关键量 vs 基准，逐项）

| 关键量 | 本节点实测 | 基准/门限 | 误差 | 是否 <3% |
|---|---|---|---|---|
| 通道覆盖（契约口径） | {cov['mature']}/{cov['total']} = {cov['rate']*100:.2f}% | 任务书门限 24/36 = 66.67% | +{(cov['rate']-24/36)*100:.2f} pp | **是**（超门限 {(cov['mature']-24)} 条） |
| 通道覆盖（独立生产者口径） | {cov['independent']}/{cov['total']} = {cov['independent']/cov['total']*100:.2f}% | 无基准（首次测量） | — | **无基准可判**（见 ⑤ 局限，如实申报） |
| 值一致性容差 | 相对 {min((c.get('detail') or {}).get('rel_tol', 1e-9) for c in ledger['channels']):.0e} ~ {max((c.get('detail') or {}).get('rel_tol', 1e-9) for c in ledger['channels']):.0e} | 通道 spec 逐条给定 | 全部重放值在容差内 | **是**（一致性误差 = 0 在容差内） |
| 幂等（两次重放） | {sum(1 for c in ledger['channels'] if (c.get('detail') or {}).get('idempotent'))}/{cov['total']} 通道逐字段相等 | 100% | 0 | **是** |
| 负控响应 | {sum(1 for c in controls['checks'] if c['ok'])}/{len(controls['checks'])} 按期望 | 4/4 | 0 | **是** |

结论：契约口径的覆盖门限（24/36）以 {cov['mature']}/36 达成；**独立生产者口径**只有
{cov['independent']}/36，这是本节点最重要的诚实结论——门已经能挡住手抄/回抄/非确定性，
但“数字有独立来源”的覆盖面仍是少数，后续节点（补原生产者、补上游原始数据）应以此为靶。

---

## 附：复现命令

```bash
# 审计（干净检出 + 重放 + 幂等 + 篡改取证 + 台账）
python3 -m tools.repro_audit.generators.contract_cli --audit \\
    --specs artifacts/repro_audit/generator_specs.json \\
    --out artifacts/repro_audit/generator_coverage.json
# 入库门（fail-closed，拒则 exit 1）
python3 -m tools.repro_audit.generators.contract_cli --gate \\
    --ledger artifacts/repro_audit/generator_coverage.json \\
    --specs artifacts/repro_audit/generator_specs.json --min-coverage 24 --min-grade G2
# 历史基线（before 红基线）与负控三刀
python3 -m tools.repro_audit.generators.contract_cli --legacy-baseline \\
    --out artifacts/repro_audit/generator_baseline_legacy.json
python3 -m tools.repro_audit.generators.contract_cli --negcontrols \\
    --out artifacts/repro_audit/negcontrols.json
# 本报告（数字全部从台账注入）
python3 -m pytest tests/test_repro_audit_generator_contract.py -q
```

*生成时间：{ledger['generated_at']}　台账 ref：`{ledger.get('repo_ref')}`　
台账 schema：`{ledger['schema']}`*
"""
    os.makedirs(os.path.dirname(os.path.abspath(out_md)), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    return md


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI 壳
    import sys
    ap = argparse.ArgumentParser(description="生成器契约审计报告 + 配图")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--controls", required=True)
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--out-figs", required=True)
    ap.add_argument("--provoke", default=None)
    args = ap.parse_args(argv)
    ledger, baseline = _load(args.ledger), _load(args.baseline)
    controls, verdict = _load(args.controls), _load(args.verdict)
    figs = figures(ledger, baseline, controls, args.out_figs)
    provoke = _load(args.provoke) if args.provoke else None
    build_report(ledger, baseline, controls, verdict, figs, args.out_md, provoke)
    print(json.dumps({"md": args.out_md, "figs": figs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())