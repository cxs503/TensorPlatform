#!/usr/bin/env python3
"""CLI：跑 S×E 判级的外部效度校准，落 JSON 工件 + 论文图。

用法
----
    # 真实数据（只读 psql，读不到就如实 WITHHELD）
    python3 tools/eval_validity/run.py --out artifacts/m6153/validity_calibration.json

    # 离线回放（JSONL 记录，测试/复算用）
    python3 tools/eval_validity/run.py --from-jsonl /tmp/records.jsonl --no-figures

产物
----
- ``artifacts/m6153/validity_calibration.json``：判据数字 / 分箱表 / 逐窗明细 /
  跨仓 AUC / 置换零分布摘要 / 复算 SQL 与命令。
- ``artifacts/m6153/figures/*.png``：ROC、校准曲线、判级画像、跨仓、负控直方图。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eval_validity import calibrate, dataset, metrics  # noqa: E402

DEFAULT_OUT = ROOT / "artifacts" / "m6153" / "validity_calibration.json"
DEFAULT_FIG_DIR = ROOT / "artifacts" / "m6153" / "figures"
#: 本地落账台账（平台 merge_master / merge_governor 的状态文件，只读）
DEFAULT_LANDING_LEDGERS = (
    "/root/tp-agent/merge_master_state.json",
    "/root/tp-agent/merge_governor_state.json",
)


def _load_jsonl(path: str) -> list[dataset.EvalRecord]:
    recs = []
    if str(path).endswith(".gz"):
        import gzip
        text = gzip.open(path, "rt", encoding="utf-8").read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        d = json.loads(ln)
        recs.append(dataset.EvalRecord(
            mission_id=int(d["mission_id"]), repo=d["repo"],
            grade=d.get("grade"), e_level=d.get("e_level"),
            eval_status=d.get("eval_status", "confirmed"),
            decided_at=float(d["decided_at"]),
            outcome=d.get("outcome", "censored"),
            evidence=d.get("evidence", "jsonl"),
            mission_status=d.get("mission_status"),
            finished_at=d.get("finished_at"),
            detail=d.get("detail") or {},
        ))
    return recs


def _dump_jsonl(records, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r.to_dict(), ensure_ascii=False) for r in records]
    if str(path).endswith(".gz"):
        import gzip
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write("\n".join(lines) + "\n")
    else:
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _figures(result: dict, outdir: Path) -> list[dict]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    outdir.mkdir(parents=True, exist_ok=True)
    figs: list[dict] = []
    pooled = result["walk_forward"].get("pooled") or []

    # 1) ROC
    if pooled:
        y = np.array([p["y"] for p in pooled])
        p = np.array([p["p"] for p in pooled])
        order = np.argsort(-p)
        y = y[order]
        p = p[order]
        tpr, fpr, tp, fp = [0.0], [0.0], 0, 0
        P, N = y.sum(), len(y) - y.sum()
        for yi, pi in zip(y, p):
            tp += yi == 1
            fp += yi == 0
            tpr.append(tp / max(P, 1))
            fpr.append(fp / max(N, 1))
        fig, ax = plt.subplots(figsize=(4.2, 4.2))
        ax.plot(fpr, tpr, "-o", ms=2.5, lw=1.2, label=f"AUC={result['walk_forward']['auc']:.3f}")
        ax.plot([0, 1], [0, 1], "--", color="grey", lw=1, label="chance (0.5)")
        ax.set_xlabel("false positive rate")
        ax.set_ylabel("true positive rate")
        ax.set_title("Walk-forward OOS ROC (S×E grade → merge)")
        ax.legend(loc="lower right", fontsize=8)
        fig.tight_layout()
        f = outdir / "roc_walk_forward.png"
        fig.savefig(f, dpi=150)
        plt.close(fig)
        figs.append({"name": f.name, "path": str(f),
                     "caption": "walk-forward 样本外 ROC：判级预测下游 merge"})

    # 2) 校准曲线
    bins = result["walk_forward"].get("bins") or []
    pts = [b for b in bins if b["n"]]
    if pts:
        fig, ax = plt.subplots(figsize=(4.6, 4.0))
        xs = [b["mean_pred"] for b in pts]
        ys = [b["emp_rate"] for b in pts]
        lo = [b["ci95"][0] for b in pts]
        hi = [b["ci95"][1] for b in pts]
        ax.errorbar(xs, ys, yerr=[[y_ - l for y_, l in zip(ys, lo)],
                                  [h - y_ for y_, h in zip(ys, hi)]],
                    fmt="o-", ms=4, lw=1.2, capsize=3, label="observed")
        ax.plot([0, 1], [0, 1], "--", color="grey", lw=1, label="perfect")
        for b in pts:
            ax.annotate(f"n={b['n']}", (b["mean_pred"], b["emp_rate"]),
                        textcoords="offset points", xytext=(4, -10), fontsize=6)
        ax.set_xlabel("predicted P(merge)")
        ax.set_ylabel("empirical merge rate")
        ax.set_title(f"Calibration (Brier={result['walk_forward']['brier']:.3f}, "
                     f"ECE={result['walk_forward']['ece']:.3f})")
        ax.legend(fontsize=8)
        fig.tight_layout()
        f = outdir / "calibration_curve.png"
        fig.savefig(f, dpi=150)
        plt.close(fig)
        figs.append({"name": f.name, "path": str(f),
                     "caption": "预测概率十分箱 vs 实际 merge 率（Wilson 95% CI）"})

    # 3) 判级画像
    prof = result.get("grade_profile", {}).get("by_e_level") or []
    if prof:
        fig, ax = plt.subplots(figsize=(4.6, 3.6))
        xs = np.arange(len(prof))
        vals = [c["merge_rate"] for c in prof]
        err_lo = [c["merge_rate"] - c["ci95"][0] for c in prof]
        err_hi = [c["ci95"][1] - c["merge_rate"] for c in prof]
        ax.bar(xs, vals, yerr=[err_lo, err_hi], capsize=3, color="#4C78A8")
        ax.set_xticks(xs)
        ax.set_xticklabels([f"{c['value']}\nn={c['n']}" for c in prof], fontsize=8)
        ax.set_ylabel("merge rate")
        ax.set_title("Downstream merge rate by E level")
        fig.tight_layout()
        f = outdir / "grade_profile.png"
        fig.savefig(f, dpi=150)
        plt.close(fig)
        figs.append({"name": f.name, "path": str(f),
                     "caption": "各证据等级的下游 merge 率（含 95% CI）"})

    # 4) 跨仓
    repos = result.get("cross_repo", {}).get("repos") or {}
    ok = {k: v for k, v in repos.items() if v.get("auc") is not None
          and not (isinstance(v["auc"], float) and v["auc"] != v["auc"])}
    if ok:
        fig, ax = plt.subplots(figsize=(4.8, 3.4))
        names = list(ok)
        vals = [ok[k]["auc"] for k in names]
        lo = [ok[k]["auc"] - ok[k]["auc_ci95"]["lo"] for k in names]
        hi = [ok[k]["auc_ci95"]["hi"] - ok[k]["auc"] for k in names]
        ax.bar(np.arange(len(names)), vals, yerr=[lo, hi], capsize=4, color="#F58518")
        ax.axhline(0.7, ls="--", color="grey", lw=1, label="AUC=0.7")
        ax.axhline(0.5, ls=":", color="k", lw=1, label="chance")
        ax.set_xticks(np.arange(len(names)))
        ax.set_xticklabels([n.replace("-performance-platform", "") for n in names],
                           fontsize=7)
        ax.set_ylim(0, 1)
        ax.set_ylabel("walk-forward AUC")
        ax.set_title(f"Cross-repo consistency (spread={result['cross_repo']['spread']:.3f})")
        ax.legend(fontsize=7)
        fig.tight_layout()
        f = outdir / "cross_repo_auc.png"
        fig.savefig(f, dpi=150)
        plt.close(fig)
        figs.append({"name": f.name, "path": str(f),
                     "caption": "三仓 walk-forward AUC 及 95% CI（极差判一致性）"})

    # 5) 负控直方图
    hist = result.get("negative_control_null_samples") or []
    if hist:
        fig, ax = plt.subplots(figsize=(4.6, 3.2))
        ax.hist(hist, bins=24, color="#54A24B", alpha=0.85)
        obs = result["negative_control"]["observed_auc"]
        ax.axvline(obs, color="crimson", lw=1.5, label=f"observed={obs:.3f}")
        ax.axvline(0.5, color="k", ls=":", lw=1, label="0.5")
        ax.set_xlabel("AUC under permuted labels")
        ax.set_ylabel("count")
        ax.set_title("Negative control (label permutation)")
        ax.legend(fontsize=7)
        fig.tight_layout()
        f = outdir / "negative_control.png"
        fig.savefig(f, dpi=150)
        plt.close(fig)
        figs.append({"name": f.name, "path": str(f),
                     "caption": "标签置换负控零分布 vs 观测 AUC"})
    return figs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S×E 判级外部效度校准")
    ap.add_argument("--psql-cmd", default="psql -U foundation -d tensorplatform")
    ap.add_argument("--repos", default="", help="逗号分隔；空=全生态")
    ap.add_argument("--ship-ledger", default=str(dataset.DEFAULT_SHIP_LEDGER))
    ap.add_argument("--landing-ledger", action="append", default=None,
                    help="落账台账（可多次给）：merge_master_state.json / merge_governor_state.json")
    ap.add_argument("--from-jsonl", default="", help="离线回放记录（跳过 DB）")
    ap.add_argument("--dump-records", default="",
                    help="把抽取到的记录落 JSONL（.gz 结尾自动压缩）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--fig-dir", default=str(DEFAULT_FIG_DIR))
    ap.add_argument("--n-windows", type=int, default=4)
    ap.add_argument("--min-train", type=int, default=40)
    ap.add_argument("--min-test", type=int, default=15)
    ap.add_argument("--n-perm", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261106)
    ap.add_argument("--top-grades", default="S1",
                    help="「S级」口径：默认严格单档 S1；S1,S2 作口径敏感性对照")
    ap.add_argument("--horizon-hours", type=float, default=24.0,
                    help="PR 未合并的观测期（小时）；超期仍未合并=unlanded(discard)")
    ap.add_argument("--as-of", type=float, default=None,
                    help="观测截止时刻（epoch 秒），默认取当前时刻")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    repos = tuple(r.strip() for r in args.repos.split(",") if r.strip()) or None

    if args.from_jsonl:
        records = _load_jsonl(args.from_jsonl)
        source = {"kind": "jsonl", "path": args.from_jsonl}
    else:
        try:
            records = dataset.extract_records(
                dsn_cmd=args.psql_cmd, repos=repos, ship_ledger=args.ship_ledger,
                as_of=(args.as_of if args.as_of is not None else time.time()),
                horizon_hours=args.horizon_hours,
                landing_ledgers=(args.landing_ledger or DEFAULT_LANDING_LEDGERS))
            source = {"kind": "psql+ship_ledger", "psql": args.psql_cmd,
                      "ship_ledger": args.ship_ledger}
        except Exception as exc:  # DB 不可达 → 如实 WITHHELD，不伪造数字
            payload = {"status": "WITHHELD", "reason": f"数据源不可达：{exc}",
                       "source": {"kind": "psql", "psql": args.psql_cmd}}
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            print(json.dumps(payload, ensure_ascii=False))
            return 3
        source = {"kind": "psql+ledgers", "psql": args.psql_cmd,
                  "ship_ledger": args.ship_ledger,
                  "landing_ledgers": list(args.landing_ledger or DEFAULT_LANDING_LEDGERS),
                  "horizon_hours": args.horizon_hours,
                  "as_of": args.as_of if args.as_of is not None else time.time()}

    if args.dump_records:
        _dump_jsonl(records, args.dump_records)

    target_repos = repos or calibrate.DEFAULT_TARGET_REPOS
    result = calibrate.run_calibration(
        records, n_windows=args.n_windows, min_train=args.min_train,
        min_test=args.min_test, n_perm=args.n_perm, seed=args.seed,
        repos=target_repos,
        top_grades=tuple(g.strip() for g in args.top_grades.split(",") if g.strip()),
    )
    result["status"] = "OK" if result["n_labeled"] else "WITHHELD"
    result["label_policy"] = {
        "positive": "downstream outcome == merge（PR merged / 船仓台账 merged）",
        "negative": ("rollback（merged 后 closed）/ rework（custody_requeue、船仓 held/"
                     "conflict）/ discard（closed 未合并、orphan_detect、write_off、"
                     f"open 超 {args.horizon_hours}h 未合并）"),
        "censored": "无可观测终局或 open 未超期 → 剔除（不参与 AUC/Brier）",
        "horizon_hours": args.horizon_hours,
    }
    result["source"] = source
    result["commands"] = {
        "rerun": ("python3 tools/eval_validity/run.py --out " + args.out),
        "replay_records": "python3 tools/eval_validity/run.py --from-jsonl <records.jsonl>",
        "extract_sql": dataset.EXTRACT_SQL.format(status="ANY (ARRAY['confirmed'])")
        .replace("\n", " "),
    }
    result["coverage"] = dataset.class_summary(records)
    if not args.no_figures and result["n_labeled"]:
        try:
            result["figures"] = _figures(result, Path(args.fig_dir))
        except Exception as exc:  # 图失败不该毁掉数字
            result["figures_error"] = f"{type(exc).__name__}: {exc}"

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ("status", "n_records", "n_labeled",
                                             "positives", "negative")},
                     ensure_ascii=False))
    for k, v in result["criteria"].items():
        print(f"{k}: pass={v['pass']} value={v.get('value')}")
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())