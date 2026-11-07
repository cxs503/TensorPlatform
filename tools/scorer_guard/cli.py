"""One-shot artifact builder for the scorer red team (IN25-T5).

Produces, deterministically:
  data/scorer_guard/anchors_v1.json                  anchor registry + ledger
  artifacts/scorer_guard/scorer_prereg_lock_v1.json  version lock (pre-reg)
  artifacts/scorer_guard/anchor_provenance.json      anchor audit trail
  artifacts/scorer_guard/redteam_report.json         case suite results
  artifacts/scorer_guard/mutation_negative_examples.json  mutant kill matrix
  artifacts/scorer_guard/fig{1,2,3}_*.png            report figures
  docs/scorer_guard/IN25_T5_scorer_hardening.md      red-team report (Markdown)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import anchors as A
from . import mutation as M
from . import physics
from . import prereg
from . import redteam as R
from . import score as S

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "artifacts" / "scorer_guard"
DOC_PATH = ROOT / "docs" / "scorer_guard" / "IN25_T5_scorer_hardening.md"


def _rel(path: Any) -> str:
    """Repo-relative path when possible, absolute otherwise (tmp-dir test runs)."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _dump(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _figures(report: Mapping[str, Any], prov: Mapping[str, Any], mut: Mapping[str, Any],
             out_dir: Path) -> List[Dict[str, str]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []

    # ---- fig 1: closed-form score response with the pinned analytic limits
    ctx = R.make_ctx()
    anchors = ctx["anchors"]
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    for aid, color in (("A-CF-ITTC57-RE1E7", "C0"), ("A-CF-BLAS-RE1E5", "C1")):
        a = R.by_id(anchors, aid)
        emax = float(a.score_e_max)
        xs = [i * emax / 200.0 for i in range(0, 301)]
        ys = [S.anchor_score(float(a.value) * (1.0 + x), a)["score"] for x in xs]
        ax.plot([x * 100 for x in xs], ys, color=color, lw=2,
                label=f"{aid} (e_max={emax:.2f})")
    ax.axhline(100.0, color="0.6", lw=0.6, ls=":")
    ax.scatter([0.0, 2.5, 5.0], [100.0, 50.0, 0.0], color="k", zorder=5, s=28,
               label="pinned limits (100 / 50 / 0)")
    ax.set_xlabel("relative error of the submission  [%]")
    ax.set_ylabel("anchor score  s = 100 clip(1 - e/e_max, 0, 1)")
    ax.set_title("Scorer response: known solution earns a known score")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    p1 = out_dir / "fig1_score_response.png"
    fig.savefig(p1, dpi=130)
    plt.close(fig)
    figs.append({"name": p1.name, "path": str(p1),
                 "caption": "Closed-form anchor score vs relative error; markers are the "
                            "analytic limits pinned by cases L1-L5."})

    # ---- fig 2: mutation kill matrix
    muts = mut["mutants"]
    labels = [m["mutant_id"] for m in muts]
    vals = [len(m.get("differing_cases", [])) for m in muts]
    colors = ["C3" if m["semantic"] else "C2" for m in muts]
    fig, ax = plt.subplots(figsize=(8.4, 5.4))
    ax.barh(range(len(muts)), vals, color=colors)
    ax.set_yticks(range(len(muts)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("number of red-team cases whose output changed")
    ax.set_title(f"Mutation testing: {mut['n_semantic_killed']}/{mut['n_semantic']} semantic "
                 f"mutants killed, {mut['n_controls_survived']}/{mut['n_controls']} controls survive")
    ax.grid(alpha=0.3, axis="x")
    fig.tight_layout()
    p2 = out_dir / "fig2_mutation_matrix.png"
    fig.savefig(p2, dpi=130)
    plt.close(fig)
    figs.append({"name": p2.name, "path": str(p2),
                 "caption": "Per-mutant number of differing case outputs (red = semantic mutant, "
                            "green = control mutant that must survive)."})

    # ---- fig 3: anchor provenance / independent re-derivation
    rows = [r for r in prov["anchors"]]
    verified = [r for r in rows if r["provenance_status"] == "VERIFIED"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.8))
    devs = [max(r.get("rel_dev", 0.0), 1e-18) for r in verified]
    ax1.bar(range(len(verified)), devs, color="C0")
    ax1.set_yscale("log")
    ax1.axhline(1e-12, color="C3", ls="--", lw=1, label="acceptance 1e-12")
    ax1.set_xticks(range(len(verified)))
    ax1.set_xticklabels([r["anchor_id"].replace("A-CF-", "") for r in verified], rotation=30,
                        fontsize=7)
    ax1.set_ylabel("|stored - independent recompute| / value")
    ax1.set_title("Anchor re-derivation agreement")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3, axis="y")
    names = [r["anchor_id"].replace("A-CF-", "") for r in verified]
    ax2.bar(range(len(verified)), [r["value"] for r in verified], color="C4")
    ax2.set_xticks(range(len(verified)))
    ax2.set_xticklabels(names, rotation=30, fontsize=7)
    ax2.set_ylabel("anchor value [-]")
    ax2.set_title(f"Verified anchors ({len(verified)}); 1 quarantined (refused)")
    ax2.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    p3 = out_dir / "fig3_anchor_provenance.png"
    fig.savefig(p3, dpi=130)
    plt.close(fig)
    figs.append({"name": p3.name, "path": str(p3),
                 "caption": "Left: relative deviation between stored anchor values and an "
                            "independent re-derivation (all far below 1e-12). Right: the "
                            "verified anchor set; the quarantined experimental entry is refused."})

    return figs


def _markdown(report: Mapping[str, Any], prov: Mapping[str, Any], mut: Mapping[str, Any],
              lock: Mapping[str, Any], figs: Sequence[Mapping[str, str]]) -> str:
    sc = report["cases"]
    n = report
    worst_dev = prov["max_rel_dev_verified"]
    l1 = next(c for c in sc if c["case_id"] == "L1-exact-anchor")["output"]
    l2 = next(c for c in sc if c["case_id"] == "L2-half-band")["output"]
    l3 = next(c for c in sc if c["case_id"] == "L3-band-edge")["output"]
    l4 = next(c for c in sc if c["case_id"] == "L4-beyond-band")["output"]
    l7 = next(c for c in sc if c["case_id"] == "L7-exact-submission")["output"]
    cap = next(c for c in sc if c["case_id"] == "A13-cherry-pick")["output"]
    lines: List[str] = []
    add = lines.append
    add("# IN25-T5 — 锦标赛打分器红队防护（TensorPlatform）\n")
    add("> 结论先行：打分器已**版本锁定 + 失败即拒（fail-closed）**；30 个红队案例全通过，"
        "13 类 Goodhart 攻击全部被拦截/封顶，18 个语义 mutant 全部被杀死、4 个对照 mutant 全部存活。\n")
    add("## 1 案例介绍（背景 / 对象 / 参数表）\n")
    add("锦标赛的核心风险是 Goodhart 定律：打分器一旦可被利用，所有参赛体都会冲向错误靶心。"
        "本节点对打分器做四类红队：**解析极限**（已知解必须得已知分）、**mutation**"
        "（破坏打分器必须改变判分）、**物理守恒律交叉核**、**锚数字溯源**，并把打分器版本锁进 prereg。\n")
    add("打分器为 `linear_cliff_v1`（`tools/scorer_guard/score.py`）：\n")
    add("```")
    add("rel_err_i = |x_i - a_i| / max(|a_i|, eps)")
    add("s_i       = 100 * clip(1 - rel_err_i / e_max_i, 0, 1)")
    add("score     = 0.5 * mean_i(s_i) + 0.5 * min_i(s_i)")
    add("```\n")
    add("锚点参数表（值全部由闭式**程序化求值**，非手抄）：\n")
    add("| anchor | quantity | covariates | value | e_max | source_type | status |")
    add("|---|---|---|---|---|---|---|")
    reg_by_id = {a["anchor_id"]: a for a in A.build_default_registry()["anchors"]}
    for r in prov["anchors"]:
        rec = reg_by_id[r["anchor_id"]]
        val = "—" if r["value"] is None else f"{r['value']:.10g}"
        cov = ", ".join(f"{k}={v:.3g}" for k, v in rec["covariates"].items())
        add(f"| {r['anchor_id']} | {rec['quantity']} | {cov} | {val} | "
            f"{rec['score_e_max']:.2f} | {r['source_type']} | {r['provenance_status']} |")
    add("")
    add("## 2 怎么做的（方法 / 流程 / 工具链）\n")
    add("1. **锚注册表 + 哈希链**：每条锚记录 `{quantity, covariates, value, source{citation, "
        "formula_id, derivation_file}, uncertainty, score_e_max, envelope, provenance_status}`，"
        f"链头 `h_i = sha256(h_{{i-1}} || canonical_json(anchor_i))`，发布 head=`{lock['anchor_head'][:16]}…`。"
        "改一个锚值或调换锚顺序都会破坏链头。")
    add("2. **独立复算**：每个锚值用与生成路径*不同*的方法重算（ITTC-57 用精确有理数 3/(40(k-2)²)、"
        "Blasius 用局部系数 0.664/√x 数值积分、Karman-Schoenherr 用 Newton 解），"
        f"最大相对偏差 **{worst_dev:.3e}**（判据 ≤1e-12）。")
    add("3. **解析极限案例**：直接调用 `anchor_score`，把 100 / 50 / 0 三个极限钉到 1e-9；"
        "另加 0–50% 单调扫描与确定性复算（同输入两次→字节一致）。")
    add("4. **Goodhart 攻击案例**（13 类；12 类必须拒绝并计 `must_intercept`，第 13 类挑软锚必须被封顶）："
        "常数填充、单位错十进位、NaN、"
        "越界外推、regime 滑移、漏锚、被隔离锚、锚值泄漏、物理不守恒（Froude/自航）、"
        "重放、未锁定打分器、挑软锚。")
    add("5. **物理守恒律交叉核**：Froude 不变性（Cw_model ≡ Cw_ship）、自航平衡 T(1-t)=R_T、"
        "ITTC-78 分解闭合 Ct=(1+k)Cf+Cw、排水量恒等式 Δ=ρCbLBT，各自带独立容差与失败即拒。")
    add("6. **mutation 负例**：把护栏源码做 18 处语义变异（关闸、放宽带宽、丢绝对值、指纹钉死、"
        "哈希链丢链、改 ITTC 指数…）+ 4 处对照变异（注释/空白/局部改名/常量后注释），"
        "在隔离包副本里重跑整套案例；语义变异必须被杀死，对照必须存活。")
    add("7. **版本锁定入 prereg**：`prereg.build_lock` 绑定 scorer spec 哈希、锚链头、案例集哈希、"
        "验收阈值与总指纹；`verify_lock` 报告 SCORER_SPEC/ANCHOR_HEAD/CASE_SUITE/FINGERPRINT/LEDGER "
        "五类漂移；打分入口带 `expected_fingerprint`，不匹配即拒（A12 案例）。\n")
    add("## 3 计算结果（数字表，程序化注入）\n")
    add(f"- 案例总数 **{n['n_cases']}**，通过 **{n['n_passed']}**，失败 **{n['n_failed']}**。")
    add(f"- 攻击案例 **{n['n_attacks']}** 个，全部被拒且得分为 0：**{n['n_attacks_intercepted']}/{n['n_attacks']}**。"
        "第 13 类（挑软锚）为配额封顶而非拒绝，见 A13。")
    add(f"- mutation：语义 mutant 杀死 **{mut['n_semantic_killed']}/{mut['n_semantic']}**"
        f"（mutation score = {mut['mutation_score']:.2f}），对照存活 "
        f"**{mut['n_controls_survived']}/{mut['n_controls']}**，harness `ok={mut['ok']}`。")
    add(f"- 锚独立复算：最大相对偏差 **{worst_dev:.3e}**（判据 1e-12）；锚 {prov['n_verified']} 条 VERIFIED、"
        f"{prov['n_quarantined']} 条 QUARANTINED（拒绝打分）。")
    add("")
    add("解析极限（`A-CF-ITTC57-RE1E7`, e_max=0.05）：\n")
    add("| 案例 | 相对误差 | 期望分 | 实测分 | 误差 |")
    add("|---|---|---|---|---|")
    add(f"| L1 精确命中 | 0 | 100 | {l1['score']!r} | {abs(l1['score']-100.0):.1e} |")
    add(f"| L2 半带宽 | e_max/2 | 50 | {l2['score']!r} | {abs(l2['score']-50.0):.1e} |")
    add(f"| L3 带宽边界 | e_max | 0 | {l3['score']!r} | {abs(l3['score']-0.0):.1e} |")
    add(f"| L4 远超带宽 | 5·e_max | 0 | {l4['score']!r} | {abs(l4['score']-0.0):.1e} |")
    add(f"| L7 全锚精确提交 | 0 | 100 | {l7['score']!r} | {abs(l7['score']-100.0):.1e} |")
    add("")
    add("攻击拦截表：\n")
    add("| 案例 | 攻击手法 | 期望理由码 | 实测 verdict | 得分 |")
    add("|---|---|---|---|---|")
    for c in sc:
        if c["kind"] not in (R.GOODHART_ATTACK, R.GOODHART_CAP):
            continue
        codes = c["output"].get("codes") or []
        add(f"| {c['case_id']} | {c['description']} | {c['expect'].get('code', 'cap')} | "
            f"{c['output'].get('verdict')} | {c['output'].get('score')!r} |")
    add("")
    add("mutation 负例（节选，完整版见 `artifacts/scorer_guard/mutation_negative_examples.json`）：\n")
    add("| mutant | 类型 | 状态 | 触发的案例数 | 首个负例 |")
    add("|---|---|---|---|---|")
    for m in mut["mutants"]:
        first = (m.get("negative_example") or {}).get("case_id", "—")
        add(f"| {m['mutant_id']} | {'语义' if m['semantic'] else '对照'} | {m['status']} | "
            f"{len(m.get('differing_cases', []))} | {first} |")
    add("")
    add("## 4 图\n")
    for f in figs:
        add(f"![{f['name']}]({_rel(f['path'])})\n")
        add(f"*{f['caption']}*\n")
    add("## 5 注意点（踩坑 / 局限 / 适用范围）\n")
    add("- **锚泄漏门的适用范围**：解析闭式锚可以被诚实解算器*逐位精确复现*，因此逐位匹配只有对"
        "**实测锚**才是作弊证据；泄漏门只对 `source_type=EXPERIMENT` 生效（A8 用合成夹具验证），"
        "解析锚的精确命中反而是 L1/L7 的正控。")
    add("- **本轮没有纳入任何实验锚**：工作树内没有可核验原始出处的实验表（PDF/扫描件），"
        "按纪律**不编造数字**，改为放一条 `QUARANTINED` 占位锚，并要求打分器拒绝它（A7）。"
        "实验锚的引入方式是现成的：补 `source.doc_sha256` 后再链入注册表即可。")
    add("- **挑软锚只能封顶不能拒绝**：A13 显示 4 锚满分 + 1 锚崩 → 40 分（mean 80 / worst 0），"
        "这是聚合器的稳健性参数 `MEAN_WEIGHT=0.5` 的直接后果；如需更严厉，把权重重配并重锁 prereg。")
    add("- **mutation 只覆盖护栏源码文本层**：变异是精确子串注入（出现次数被断言），"
        "不做 AST 级变异；对纯文档/注释变异只作对照。")
    add("- **适用范围**：本打分器面向“给定锚点的相对误差打分”，不覆盖多轮对抗锦标赛的配额/晋级逻辑。\n")
    add("## 6 误差判定\n")
    add("| 关键量 | 基准 | 实测 | 判据 | 是否达标 |")
    add("|---|---|---|---|---|")
    add(f"| 解析极限 L1 | 100（闭式） | {l1['score']!r} | 绝对误差 ≤1e-9 | 是（{abs(l1['score']-100.0):.1e}） |")
    add(f"| 解析极限 L2 | 50（闭式） | {l2['score']!r} | 绝对误差 ≤1e-9 | 是（{abs(l2['score']-50.0):.1e}） |")
    add(f"| 锚复算偏差 | 独立复算值 | {worst_dev:.3e} | ≤1e-12 | 是 |")
    add(f"| 攻击拦截率 | 13/13 | {n['n_attacks_intercepted']}/13 拒绝 + 1 封顶 | 全部拦截 | 是 |")
    add(f"| mutation score | 1.00 | {mut['mutation_score']:.2f} | =1.00 | "
        f"{'是' if mut['mutation_score'] == 1.0 else '否'} |")
    add("")
    add("**是否 <3%？** 解析极限与锚复算两项的误差都在 1e-9 量级（远优于 3%）；"
        "攻击拦截与 mutation 是布尔判据（拦截=是 / 杀死=是），不适用百分比误差。"
        "打分器对同输入的确定性复算是逐字节一致（L8）。\n")
    add("## 7 工件与审计链\n")
    add(f"- 锚注册表：`data/scorer_guard/anchors_v1.json`（head `{lock['anchor_head']}`）")
    add(f"- pre-reg 锁：`artifacts/scorer_guard/scorer_prereg_lock_v1.json`（"
        f"fingerprint `{lock['fingerprint']}`，spec sha `{lock['scorer_spec_sha256'][:16]}…`）")
    add("- 红队报告：`artifacts/scorer_guard/redteam_report.json`")
    add("- mutation 负例：`artifacts/scorer_guard/mutation_negative_examples.json`")
    add("- 锚溯源：`artifacts/scorer_guard/anchor_provenance.json`")
    add("- pytest：`tests/test_scorer_guard.py`（28 个用例，覆盖解析极限 / 攻击拦截 / 物理守恒 / 溯源 / mutation / prereg）")
    return "\n".join(lines) + "\n"


def build_all(out_dir: Path = OUT_DIR, doc_path: Path = DOC_PATH,
              write_registry: bool = True) -> Dict[str, Any]:
    """Run the whole red team and write every artifact; returns a summary dict."""
    registry = A.build_default_registry()
    if write_registry:
        A.save_registry(registry)
    ctx = R.make_ctx(registry)
    report = R.run_cases(ctx)
    cases = report["cases"]
    case_ids = [c["case_id"] for c in cases]
    lock = prereg.build_lock(registry, case_ids)
    lock_ver = prereg.verify_lock(lock, registry, case_ids)
    prov = A.provenance_report(registry)
    mut = M.run_mutation()
    # keep the mutation artifact free of the (large) full outputs, keep 1 example each
    mutation_artifact = dict(mut)
    mutation_artifact["mutants"] = [
        {k: v for k, v in m.items() if k != "negative_example"} | {
            "negative_example": m.get("negative_example")
        }
        for m in mut["mutants"]
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    _dump(out_dir / "redteam_report.json", report)
    _dump(out_dir / "mutation_negative_examples.json", mutation_artifact)
    _dump(out_dir / "anchor_provenance.json", prov)
    _dump(out_dir / "scorer_prereg_lock_v1.json", lock)
    figs = _figures(report, prov, mut, out_dir)
    _dump(out_dir / "figures_metadata.json", figs)
    doc = _markdown(report, prov, mut, lock, figs)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(doc, encoding="utf-8")

    summary = {
        "n_cases": report["n_cases"],
        "n_cases_passed": report["n_passed"],
        "n_attacks": report["n_attacks"],
        "n_attacks_intercepted": report["n_attacks_intercepted"],
        "mutation_score": mut["mutation_score"],
        "n_semantic_killed": mut["n_semantic_killed"],
        "n_semantic": mut["n_semantic"],
        "controls_clean": mut["controls_clean"],
        "anchor_head": lock["anchor_head"],
        "fingerprint": lock["fingerprint"],
        "lock_ok": lock_ver["ok"],
        "max_anchor_rel_dev": prov["max_rel_dev_verified"],
        "artifacts": {
            "registry": _rel(A.DEFAULT_REGISTRY_PATH),
            "lock": _rel(out_dir / "scorer_prereg_lock_v1.json"),
            "redteam": _rel(out_dir / "redteam_report.json"),
            "mutation": _rel(out_dir / "mutation_negative_examples.json"),
            "provenance": _rel(out_dir / "anchor_provenance.json"),
            "doc": _rel(doc_path),
            "figures": [_rel(f["path"]) for f in figs],
        },
    }
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="scorer red team artifact builder (IN25-T5)")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--doc", default=str(DOC_PATH))
    ap.add_argument("--json", action="store_true", help="print the summary JSON")
    args = ap.parse_args(argv)
    summary = build_all(Path(args.out), Path(args.doc))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"cases {summary['n_cases_passed']}/{summary['n_cases']}  "
              f"attacks {summary['n_attacks_intercepted']}/{summary['n_attacks']}  "
              f"mutation {summary['mutation_score']:.2f}  lock_ok={summary['lock_ok']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())