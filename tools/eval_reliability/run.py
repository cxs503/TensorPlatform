#!/usr/bin/env python3
"""CLI：跑判级可靠性分析，落 JSON 工件 + 论文级 Markdown + 图。

用法
----
    # 真实台账（只读 psql；读不到就如实 WITHHELD）
    python3 tools/eval_reliability/run.py --out artifacts/m10363/grader_reliability.json

    # 离线回放（JSON 记录，测试/复算用；不连库）
    python3 tools/eval_reliability/run.py --from-json /tmp/rows.json --no-figures

产物
----
- ``artifacts/m10363/grader_reliability.json``：判据数字 / 覆盖率 / 系数 /
  φ_g / 恢复与注入实验 / 最小审数表 / 决策门逐 mission 标签 / 复算命令。
- ``artifacts/m10363/figures/*.png``：6 张图（恢复、覆盖率、注入、判级画像、
  最小审数、门特性）。
- ``docs/IN55W3-N07_grader_reliability.md``：论文级六要素报告。
- ``/tmp/report.json``：平台报告 payload（title/summary/markdown/figures）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eval_reliability import (  # noqa: E402
    acceptance, dataset, gate, report, stats, synthetic,
)

DEFAULT_OUT = ROOT / "artifacts" / "m10363" / "grader_reliability.json"
DEFAULT_FIG = ROOT / "artifacts" / "m10363" / "figures"
DEFAULT_DOC = ROOT / "docs" / "IN55W3-N07_grader_reliability.md"


def _rel(p: Path) -> str:
    """相对仓根路径（不在仓内则原样返回，便于本地临时跑）。"""
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


# ─────────────────────────────── 真实数据部分 ───────────────────────────────

def grader_profile(records) -> dict[str, Any]:
    """按判级者键统计等级构成（看系统性宽/严）+ 每键的 E 档中位数。"""
    by_grader: dict[str, dict[str, int]] = {}
    e_ranks: dict[str, list[int]] = {}
    for r in records:
        if not r.keyed or r.grade is None:
            continue
        by_grader.setdefault(r.grader or "", {})
        by_grader[r.grader or ""][r.grade] = by_grader[r.grader or ""].get(r.grade, 0) + 1
        e = r.e_rank()
        if e is not None:
            e_ranks.setdefault(r.grader or "", []).append(e)
    med_e = {g: (sorted(v)[len(v) // 2] if v else None) for g, v in e_ranks.items()}
    lv: dict[str, list[int]] = {}
    for r in records:
        if r.keyed and r.s_level() is not None:
            lv.setdefault(r.grader or "", []).append(r.s_level() or 0)
    mean_level = {g: float(sum(v) / len(v)) for g, v in lv.items() if v}
    return {"by_grader": by_grader, "median_e_rank_by_grader": med_e,
            "mean_level_by_grader": mean_level}


def real_coefficients(records) -> tuple[dict[str, Any], Any]:
    """真实数据上的系数（含 CI）+ 多评单元逐对明细。"""
    mat, graders, units = dataset.to_matrix(records, "S")
    out: dict[str, Any] = {"n_units": len(units), "n_graders": len(graders)}
    a_ord = stats.krippendorff_alpha(mat, "ordinal")
    a_nom = stats.krippendorff_alpha(mat, "nominal")
    out["alpha_ordinal"] = a_ord.get("alpha")
    out["alpha_nominal"] = a_nom.get("alpha")
    out["alpha_n_pairable_units"] = a_ord.get("n_units")
    try:
        out["gwet_ac1"] = stats.gwet_ac1(mat).get("ac1")
        out["gwet_ac2"] = stats.gwet_ac2(mat).get("ac2")
        out["fleiss_kappa"] = stats.fleiss_kappa(mat).get("kappa")
    except ValueError as exc:  # 完整格不足
        out["gwet_ac1"] = out["gwet_ac2"] = out["fleiss_kappa"] = None
        out["complete_case_error"] = str(exc)
    ic = stats.icc(mat)
    for k in ("icc_1_1", "icc_2_1", "icc_2_k", "icc_3_k", "p_value"):
        out[k] = ic.get(k)
    out["icc_n_units"] = ic.get("n_units")
    if a_ord.get("status") == "ok" and (a_ord.get("n_units") or 0) >= 5:
        ci = stats.bootstrap_alpha_ci(mat, "ordinal", n_boot=2000)
        out["alpha_ci_lo"] = ci.get("lo")
        out["alpha_ci_hi"] = ci.get("hi")
        out["alpha_ci_halfwidth"] = ci.get("half_width")
        out["alpha_ci_method"] = ci.get("method")
    else:
        out["alpha_ci_lo"] = out["alpha_ci_hi"] = out["alpha_ci_halfwidth"] = None
        out["alpha_ci_status"] = "insufficient_pairable_units"
    return out, mat


# ─────────────────────────────── 主流程 ───────────────────────────────

def run(*, dsn: str | None = None, from_json: str | None = None,
        reps: int = 200, n_units: int = 100, n_boot: int = 600,
        make_figures: bool = True, out: Path = DEFAULT_OUT,
        fig_dir: Path = DEFAULT_FIG, doc: Path = DEFAULT_DOC,
        statuses: tuple[str, ...] = ("confirmed", "candidate")) -> dict[str, Any]:
    t0 = time.time()
    if from_json:
        rows = json.loads(Path(from_json).read_text())
        records = [r for r in dataset.records_from_rows(rows) if r.status in statuses]
        source = {"kind": "json_replay", "path": str(from_json), "rows": len(rows)}
    else:
        ex = dataset.extract(dsn_cmd=dsn or "psql -U foundation -d tensorplatform",
                            statuses=statuses)
        records = ex["records"]
        source = {"kind": "psql_readonly", "rows_scanned": ex["rows_scanned"],
                  "sql": ex["sql"].replace("\n", " "), "statuses": list(statuses)}

    cov = dataset.coverage(records)
    # 状态敏感性：confirmed-only 下同一套覆盖/系数（说明结论不依赖 candidate 的取舍）
    sensitivity: list[dict[str, Any]] = []
    if not from_json:
        for sts in (("confirmed",), ("confirmed", "candidate"),
                    ("confirmed", "candidate", "rejected")):
            try:
                sub = dataset.extract(dsn_cmd=dsn or "psql -U foundation -d tensorplatform",
                                     statuses=sts)["records"]
            except Exception as exc:  # pragma: no cover - DB 不可用
                sensitivity.append({"statuses": list(sts), "error": str(exc)})
                continue
            c_sub = dataset.coverage(sub)
            mat_sub, _, _ = dataset.to_matrix(sub, "S")
            a_sub = stats.krippendorff_alpha(mat_sub, "ordinal")
            sensitivity.append({
                "statuses": list(sts), "n_records": c_sub["n_records"],
                "n_pairable_missions": c_sub["n_pairable_missions"],
                "n_cross_grader_pairs": c_sub["n_cross_grader_pairs"],
                "n_same_grader_pairs": c_sub["n_same_grader_pairs"],
                "unkeyed_share": c_sub["unkeyed_share"],
                "alpha_ordinal": a_sub["alpha"], "n_pairable_units": a_sub["n_units"],
            })
    coef, _mat = real_coefficients(records)
    long_rows = dataset.to_long(records, "S")
    vc = stats.variance_components(long_rows)
    vci = stats.bootstrap_phi_g(long_rows, n_boot=400) if vc.get("status") == "ok" else {}
    # 探索性：把「被判据拒收的尝试」也算作判级行为——这是台账里**唯一**存在
    # 同一成果双评的来源（rejected 行与 confirmed 行共享 artifact_id）。
    # 界定清楚：这些等级可能带拒收前的草稿性质，故只作探索、不进生产裁决。
    exploratory: dict[str, Any] = {}
    if not from_json:
        try:
            recs_all = dataset.extract(dsn_cmd=dsn or "psql -U foundation -d tensorplatform",
                                       statuses=("confirmed", "candidate", "rejected"))["records"]
            cov_all = dataset.coverage(recs_all)
            mat_all, g_all, u_all = dataset.to_matrix(recs_all, "S")
            a_all = stats.krippendorff_alpha(mat_all, "ordinal")
            ci_all = (stats.bootstrap_alpha_ci(mat_all, "ordinal", n_boot=2000)
                      if (a_all.get("n_units") or 0) >= 5 else {"status": "insufficient"})
            pairs_all = dataset.pair_agreement(recs_all)
            mat_e, _, _ = dataset.to_matrix(recs_all, "E")
            a_e = stats.krippendorff_alpha(mat_e, "ordinal")
            n_pairs = len(pairs_all)
            n_agree_s = sum(1 for p in pairs_all if p["abs_diff_levels"] == 0)
            n_agree_e = sum(1 for p in pairs_all if p["e_a"] == p["e_b"])
            exploratory = {
                "alpha_e_ordinal": a_e.get("alpha"),
                "alpha_e_n_pairable_units": a_e.get("n_units"),
                "pair_agreement_S": {"n_pairs": n_pairs, "n_agree": n_agree_s,
                                     **stats.binomial_ci(n_agree_s, n_pairs)},
                "pair_agreement_E": {"n_pairs": n_pairs, "n_agree": n_agree_e,
                                     **stats.binomial_ci(n_agree_e, n_pairs)},
                "scope": "confirmed+candidate+rejected（含被判据拒收的尝试）",
                "n_records": cov_all["n_records"],
                "n_pairable_missions": cov_all["n_pairable_missions"],
                "n_cross_grader_pairs": cov_all["n_cross_grader_pairs"],
                "alpha_ordinal": a_all.get("alpha"),
                "alpha_n_pairable_units": a_all.get("n_units"),
                "alpha_ci": ci_all,
                "pairs": pairs_all,
                "caveat": ("rejected 行是被判据拒收的尝试，其等级可能只是拒收前的草稿；"
                           "但它们是台账中仅有的同成果双评证据，故作为探索性区间报出，"
                           "生产裁决仍只用 confirmed/candidate。"),
            }
        except Exception as exc:  # pragma: no cover - DB 不可用
            exploratory = {"error": str(exc)}
    pairs = dataset.pair_agreement(records)
    pairs_summary = {
        "n_pairs": len(pairs),
        "n_cross_grader_pairs": sum(1 for p in pairs if not p["same_grader"]),
        "n_same_grader_pairs": sum(1 for p in pairs if p["same_grader"]),
        "mean_abs_diff": (sum(p["abs_diff_levels"] for p in pairs) / len(pairs)) if pairs else None,
        "note": ("cross-grader 配对才是判级者间一致性证据；same-grader 配对是同一"
                 "判级者键对同一 mission 不同成果的离散（判级者内部误差）。"),
    }

    verdict = gate.alpha_verdict(
        coef.get("alpha_ordinal", float("nan")),
        n_pairable=int(coef.get("alpha_n_pairable_units") or 0),
        unkeyed_share=float(cov.get("unkeyed_share", float("nan"))),
        ci={"status": "ok" if coef.get("alpha_ci_lo") is not None else "insufficient",
            "lo": coef.get("alpha_ci_lo", float("nan")),
            "hi": coef.get("alpha_ci_hi", float("nan"))},
    )
    applied = gate.apply(records, verdict)

    # 合成恢复 + 注入 + 先验敏感性
    recovery = synthetic.alpha_recovery(targets=(0.2, 0.5, 0.8), reps=reps,
                                        n_units=n_units, n_raters=4, n_boot=n_boot)
    injection = synthetic.injection_shift(0.8, reps=reps, n_units=max(n_units, 120))
    prior = synthetic.prior_sensitivity(reps=max(40, reps // 4))
    min_tab = acceptance.min_rating_table(target_halfwidth=acceptance.THRESHOLDS["target_ci_halfwidth"])

    # ICC 功效 + 负控（判据 J3）
    power = stats.icc_power(n_units=100, n_raters=3, icc_true=0.3, reps=300)
    null = stats.icc_null_false_positive(n_units=100, n_raters=3, reps=300)

    criteria = [
        acceptance.j1_alpha_recovery(recovery),
        acceptance.j2_ci_coverage(recovery),
        acceptance.j3_icc_power(power, null),
        acceptance.j4_real_admissibility(cov, verdict, min_tab),
        acceptance.j5_injection(injection),
        acceptance.j6_gate_operating_characteristic(reps=max(60, reps // 3)),
    ]
    adjudication = acceptance.adjudicate(criteria)

    result: dict[str, Any] = {
        "mission_id": 10363,
        "branch": "agent/m10363",
        "node": "IN55W3-N07",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "thresholds": dict(acceptance.THRESHOLDS),
        "coverage": cov,
        "coefficients": coef,
        "variance": vc,
        "variance_ci": vci,
        "pairs": pairs,
        "pairs_summary": pairs_summary,
        "exploratory": exploratory,
        "sensitivity": sensitivity,
        "grader_profile": grader_profile(records),
        "gate": applied,
        "recovery": recovery,
        "injection": injection,
        "prior_sensitivity": prior,
        "min_rating_table": min_tab,
        "icc_power": power,
        "icc_null_control": null,
        "criteria": criteria,
        "adjudication": adjudication,
        "policy_text": gate.render_policy(verdict, applied),
        "reproduce": {
            "command": "python3 tools/eval_reliability/run.py",
            "artifact": _rel(out),
            "tests": "python3 -m pytest tests/test_eval_reliability.py -q",
        },
        "elapsed_s": None,
    }
    result["artifact_path"] = _rel(out)
    # 图/表的配对面板用「探索性全尝试」配对（含 rejected），并在图注/正文标注口径
    if exploratory.get("pairs"):
        result["pairs"] = exploratory["pairs"]
        result["pairs_summary"] = {
            "n_pairs": len(exploratory["pairs"]),
            "n_cross_grader_pairs": sum(1 for p in exploratory["pairs"] if not p["same_grader"]),
            "n_same_grader_pairs": sum(1 for p in exploratory["pairs"] if p["same_grader"]),
            "mean_abs_diff": sum(p["abs_diff_levels"] for p in exploratory["pairs"]) / len(exploratory["pairs"]),
            "scope": "all_attempts_incl_rejected",
        }

    if make_figures:
        figs = report.make_figures(result, fig_dir)
        for f in figs:
            f["path"] = _rel(f["path"])
        result["figures"] = [{k: v for k, v in f.items() if k != "png_base64"} for f in figs]
        result["_figures_with_b64"] = figs
    else:
        result["figures"] = []
        result["_figures_with_b64"] = []

    markdown = report.build_markdown(result)
    result["markdown_path"] = _rel(doc)
    result["elapsed_s"] = round(time.time() - t0, 1)

    out.parent.mkdir(parents=True, exist_ok=True)
    payload_art = {k: v for k, v in result.items() if not k.startswith("_")}
    out.write_text(json.dumps(payload_art, ensure_ascii=False, indent=2, default=str))
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(markdown)
    return result


def report_payload(result: dict[str, Any]) -> dict[str, Any]:
    """构造平台报告 payload（figures 内嵌 base64）。"""
    adj = result["adjudication"]
    crit = result["criteria"]
    summary = (
        f"判级可靠性门落地：Krippendorff α(ordinal)+bootstrap CI+φ_g 方差分解+合成恢复夹具+"
        f"α<{result['thresholds']['alpha_gate']} 强制第三审判；总裁决 {adj['overall']}；"
        f"真实台账 α WITHHELD（可配对 mission={result['coverage']['n_pairable_missions']}，"
        f"UNKEYED {result['coverage']['unkeyed_share']:.1%}），"
        f"合成恢复 max|bias|={crit[0]['value'].get('max_abs_bias'):.4f}、"
        f"CI 覆盖率 min(BC)={crit[1]['value'].get('min_coverage_bc'):.2f}"
        f"/min(百分位)={crit[1]['value'].get('min_coverage_percentile'):.2f}、"
        f"注入 Δα 相对误差={result['injection'].get('relative_error'):.3f}"
    )
    return {
        "title": "判级者间一致性门（Krippendorff α/ICC/φ_g）+ 可靠度置信区间 — IN55W3-N07",
        "summary": summary,
        "scenario_key": "platform/grader_reliability",
        "mission_id": 10363,
        "tags": ["inter-rater-reliability", "krippendorff-alpha", "icc", "bootstrap-ci",
                 "synthetic-recovery", "decision-gate", "SxE-ledger"],
        "markdown": result["_markdown"] if "_markdown" in result else "",
        "figures": [{"name": f["name"], "caption": f["caption"],
                     "png_base64": f["png_base64"]}
                    for f in result.get("_figures_with_b64", [])],
        "criteria": [{k: c[k] for k in ("id", "name", "value", "threshold", "status")}
                     for c in result["criteria"]],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="判级可靠性分析（IN55W3-N07）")
    ap.add_argument("--dsn", default=None, help="psql 连接串（只读）")
    ap.add_argument("--from-json", default=None, help="离线回放的行 JSON（测试用）")
    ap.add_argument("--reps", type=int, default=200, help="恢复/注入重复数")
    ap.add_argument("--n-units", type=int, default=100, help="恢复夹具的可配对单元数")
    ap.add_argument("--n-boot", type=int, default=600, help="CI bootstrap 次数")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--fig-dir", default=str(DEFAULT_FIG))
    ap.add_argument("--doc", default=str(DEFAULT_DOC))
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--from-artifact", default=None,
                    help="从已生成工件重建图/报告（不重算实验，秒级）")
    ap.add_argument("--report-json", default=str(Path("/tmp/report.json")),
                    help="平台报告 payload 输出路径")
    args = ap.parse_args(argv)

    if args.from_artifact:
        result = json.loads(Path(args.from_artifact).read_text())
        figs = ([] if args.no_figures
                else report.make_figures(result, Path(args.fig_dir)))
        for f in figs:
            f["path"] = _rel(f["path"])
        result["figures"] = [{k: v for k, v in f.items() if k != "png_base64"} for f in figs]
        result["_figures_with_b64"] = figs
        markdown = report.build_markdown(result)
        result["_markdown"] = markdown
        Path(args.doc).parent.mkdir(parents=True, exist_ok=True)
        Path(args.doc).write_text(markdown)
        Path(args.report_json).write_text(
            json.dumps(report_payload(result), ensure_ascii=False))
        print(f"[eval_reliability] rebuilt doc={args.doc} figures={len(figs)}")
        return 0

    result = run(dsn=args.dsn, from_json=args.from_json, reps=args.reps,
                 n_units=args.n_units, n_boot=args.n_boot,
                 make_figures=not args.no_figures, out=Path(args.out),
                 fig_dir=Path(args.fig_dir), doc=Path(args.doc))
    markdown = report.build_markdown(result)
    result["_markdown"] = markdown
    payload = report_payload(result)
    Path(args.report_json).write_text(json.dumps(payload, ensure_ascii=False))

    adj = result["adjudication"]
    print(f"[eval_reliability] adjudication={adj['overall']} "
          f"criteria={adj['by_id']} elapsed={result['elapsed_s']}s")
    for c in result["criteria"]:
        print(f"  {c['id']} {c['status']:9s} {c['name']}")
    print(f"[eval_reliability] artifact={args.out}")
    print(f"[eval_reliability] doc={args.doc}")
    print(f"[eval_reliability] report_payload={args.report_json}")
    return 0 if adj["overall"] in ("PASS", "WITHHELD", "IMPROVE") else 1


if __name__ == "__main__":
    raise SystemExit(main())