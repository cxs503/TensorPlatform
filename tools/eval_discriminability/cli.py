"""命令行：``export``（库→快照）/ ``run``（快照→分析）/ ``all``。

```bash
DSN="postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql"
python3 -m tools.eval_discriminability.cli all --dsn "$DSN" --out-dir artifacts/eval_discriminability
```

``run`` 只吃冻结快照，不碰数据库——审计者可在无库环境完整复算。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from . import acceptance as acc
from . import analysis as an
from . import cohort as ch
from . import plots as pl
from . import regression as rg
from . import report as rp


def _json_default(obj: Any):
    import numpy as np
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"not serializable: {type(obj)}")


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                               default=_json_default), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ───────────────────────────────── export ─────────────────────────────────

def export(dsn: str, out_dir: str | Path, *, as_of: str | None = None) -> dict[str, Any]:
    """只读拉数 → 研究表快照（gzip + sha256）。"""
    from sqlalchemy import create_engine

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    engine = create_engine(dsn)
    with engine.connect() as conn:
        rows = ch.build_study(conn, as_of=as_of)
    payload = ch.snapshot_payload(rows, as_of=as_of)
    info = ch.dump_snapshot(str(out / "snapshot.json.gz"), payload)
    info["retention"] = payload["retention"]
    write_json(out / "snapshot_manifest.json", info)
    return info


# ───────────────────────────────── analyse ─────────────────────────────────

def analyse(snapshot: Mapping[str, Any], *, out_dir: str | Path,
            seed_neg: int = 20261109, seed_rec: int = 20261109,
            n_perm: int = 100, n_rep: int = 5,
            guards: Mapping[str, int] | None = None,
            snapshot_sha256: str = "") -> dict[str, Any]:
    """快照 → 全套指标 + 判据 + 图 + 报告。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = ch.labeled(snapshot.get("rows") or [])

    # (1) walk-forward：两个特征集（归因消融）
    wf: dict[str, Any] = {}
    for name in ("grade_only", "full"):
        samples = an.samples_with_features(rows, name)
        wf[name] = an.walk_forward_oos(samples, guards=guards)
    oos_full = wf["full"]["oos"]

    # (2) 校准（用 full 的样本外概率）
    cal = an.calibration_block(oos_full)

    # (3) 通胀（全量判级行，不以标签可观测为条件——通胀是判级自身的现象）
    inf = an.inflation_block(snapshot.get("rows") or [])

    # (4) 分解（用 full 的样本外预测，保证 OOS）
    decomp = {dim: an.decomposition(oos_full, dim) for dim in ch.DIMENSIONS}

    # (5) 仪器营业特性
    samples_grade = an.samples_with_features(rows, "grade_only")
    neg = an.negative_control(samples_grade, n_perm=n_perm, seed=seed_neg, guards=guards)
    rec = an.recovery_control(samples_grade, target_auc=0.75, n_rep=n_rep,
                              seed=seed_rec, guards=guards)

    # (6) 可识别性
    ident = an.identifiability(rows)

    # (7) 判据（J1 以 grade_only 为准：外部效度问的是「判级本身」的判别力）
    reg = rg.check()
    accr = acc.build(wf["full"], wf["grade_only"], cal, inf, neg, rec, decomp,
                     regression_check=reg, j1_source="grade_only")
    accr["thresholds"] = acc.THRESHOLDS

    result: dict[str, Any] = {
        "meta": {
            "as_of": snapshot.get("as_of"),
            "snapshot_sha256": snapshot_sha256,
            "schema_version": snapshot.get("schema_version"),
            "dimensions": snapshot.get("dimensions") or list(ch.DIMENSIONS),
            "feature_sets": {k: list(v) for k, v in an.FEATURE_SETS.items()},
            "seed_neg": seed_neg, "seed_rec": seed_rec,
            "n_perm": n_perm, "n_rep": n_rep,
        },
        "retention": snapshot.get("retention") or ch.retention(snapshot.get("rows") or []),
        "walkforward": wf,
        "calibration": cal,
        "inflation": inf,
        "decomposition": decomp,
        "negative_control": neg,
        "recovery_control": rec,
        "identifiability": ident,
        "regression": reg,
        "acceptance": accr,
    }

    # (8) 图（缺数据时出「WITHHELD」占位图而不是崩）
    figs: dict[str, str] = {}
    figdir = out / "figures"
    figdir.mkdir(exist_ok=True)
    figs["calibration"] = str(figdir / "calibration.png")
    pl.calibration_plot(cal, figs["calibration"])
    figs["auc_timeline"] = str(figdir / "auc_timeline.png")
    pl.auc_timeline_plot(wf["grade_only"], figs["auc_timeline"],
                         title="Walk-forward AUC (grade_only features)")
    figs["inflation"] = str(figdir / "inflation.png")
    pl.inflation_plot(inf, figs["inflation"])
    figs["decomposition"] = str(figdir / "decomposition.png")
    pl.decomposition_plot(decomp, figs["decomposition"])
    result["figures"] = figs

    write_json(out / "result.json", result)
    (out / "REPORT.md").write_text(rp.render(result), encoding="utf-8")
    return result


def run_from_snapshot(snapshot_path: str, out_dir: str | Path, **kw) -> dict[str, Any]:
    snap = ch.load_snapshot(snapshot_path)
    sha = ch.snapshot_sha256(snapshot_path)
    return analyse(snap, out_dir=out_dir, snapshot_sha256=sha, **kw)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="评价外部效度校准（判别力/校准/通胀/分解）")
    p.add_argument("cmd", choices=["export", "run", "all"])
    p.add_argument("--dsn", default="postgresql+psycopg://postgres@/tensorplatform"
                                   "?host=/var/run/postgresql")
    p.add_argument("--out-dir", default="artifacts/eval_discriminability")
    p.add_argument("--snapshot", default=None)
    p.add_argument("--as-of", default=None, help="只保留 day <= as_of 的行（回放）")
    p.add_argument("--n-perm", type=int, default=100)
    p.add_argument("--n-rep", type=int, default=5)
    p.add_argument("--seed", type=int, default=20261109)
    args = p.parse_args(argv)

    out = Path(args.out_dir)
    snap_path = args.snapshot or str(out / "snapshot.json.gz")
    if args.cmd in ("export", "all"):
        info = export(args.dsn, out, as_of=args.as_of)
        print(f"[export] {info['path']} sha256={info['sha256'][:16]}…")
    if args.cmd in ("run", "all"):
        res = run_from_snapshot(snap_path, out, seed_neg=args.seed,
                                seed_rec=args.seed, n_perm=args.n_perm,
                                n_rep=args.n_rep)
        a = res["acceptance"]
        print(f"[run] verdict={a['verdict']} pass={a['n_pass']}/{a['n_rows']}")
        for c in a["criteria"]:
            print(f"  {c['id']} {c['name']}: {c['value']} [{c['status']}] {c['note']}")
        print(f"[run] report → {out / 'REPORT.md'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())