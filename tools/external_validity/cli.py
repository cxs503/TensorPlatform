"""命令行入口：``python3 -m tools.external_validity.cli {export,run,all}``.

典型用法（只读生产库 → 落 cohort 快照 → 离线复算）：

.. code-block:: bash

    DSN="postgresql+psycopg://postgres@/tensorplatform?host=/var/run/postgresql"
    python3 -m tools.external_validity.cli all --dsn "$DSN" \
        --out-dir artifacts/external_validity

``all`` = ``export``（拉 cohort 快照 JSON）+ ``run``（离线算 J1..J7 + 出图）。
``run`` 只吃快照，不碰数据库 —— 审计时任何人可复算。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from . import acceptance as acc
from . import ledger as lg
from . import metrics as mt
from . import plots as pl


def _json_default(obj: Any):
    import numpy as np
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"not serializable: {type(obj)}")


def write_json(path: Path, payload: Any) -> Path:
    """写 JSON；``.gz`` 后缀自动压缩（快照 2 MB+，压缩后约 1/8，便于入库）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)
    if str(path).endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    if str(path).endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_cohort(dsn: str, out: Path, *, as_of: str | None = None) -> dict[str, Any]:
    """只读拉取 cohort 并落快照（含每条的标签证据链，便于审计抽检）。"""
    from sqlalchemy import create_engine

    engine = create_engine(dsn)
    with engine.connect() as conn:
        cohort = lg.build_cohort(conn, as_of=as_of)
    payload = {
        "source": dsn.split("@")[-1],
        "as_of": as_of,
        "summary": lg.summarize(cohort),
        "feature_names": list(lg.FEATURE_NAMES),
        "cohort": cohort,
    }
    write_json(out, payload)
    return payload


def run_from_snapshot(snapshot: Path, out_dir: Path, *,
                      repo_root: Path | None = None,
                      n_control_draws: int = 20,
                      seed: int = 20261107) -> dict[str, Any]:
    """离线复算：快照 → 指标 → 图 → artifacts。"""
    data = read_json(Path(snapshot))
    cohort = data["cohort"]
    result = acc.evaluate(cohort, repo_root=repo_root or Path.cwd(),
                          n_control_draws=n_control_draws, seed=seed)
    figs = pl.make_all(result, Path(out_dir) / "figures")
    payload = {k: v for k, v in result.items() if k not in ("pooled_probs", "pooled_labels")}
    payload["figures"] = {k: str(v) for k, v in figs.items()}
    payload["snapshot"] = str(snapshot)
    payload["snapshot_sha256"] = sha256_file(Path(snapshot))
    payload["as_of"] = data.get("as_of")
    write_json(Path(out_dir) / "acceptance.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="外部效度校准：S/E 判级 → 下游结果")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="从生产库拉 cohort 快照（只读）")
    p_exp.add_argument("--dsn", required=True)
    p_exp.add_argument("--out", required=True)
    p_exp.add_argument("--as-of", default=None)

    p_run = sub.add_parser("run", help="从快照离线复算 J1..J7 + 出图")
    p_run.add_argument("--snapshot", required=True)
    p_run.add_argument("--out-dir", required=True)
    p_run.add_argument("--control-draws", type=int, default=20)
    p_run.add_argument("--seed", type=int, default=20261107)

    p_all = sub.add_parser("all", help="export + run")
    p_all.add_argument("--dsn", required=True)
    p_all.add_argument("--out-dir", required=True)
    p_all.add_argument("--as-of", default=None)
    p_all.add_argument("--control-draws", type=int, default=20)
    p_all.add_argument("--seed", type=int, default=20261107)

    args = ap.parse_args(argv)
    if args.cmd == "export":
        payload = export_cohort(args.dsn, Path(args.out), as_of=args.as_of)
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "run":
        payload = run_from_snapshot(Path(args.snapshot), Path(args.out_dir),
                                    n_control_draws=args.control_draws, seed=args.seed)
        _print_checks(payload)
        return 0 if payload["all_pass"] else 1

    out_dir = Path(args.out_dir)
    snap = out_dir / "cohort_snapshot.json.gz"
    export_cohort(args.dsn, snap, as_of=args.as_of)
    payload = run_from_snapshot(snap, out_dir, n_control_draws=args.control_draws,
                                seed=args.seed)
    _print_checks(payload)
    return 0 if payload["all_pass"] else 1


def _print_checks(payload: dict[str, Any]) -> None:
    print(f"{'check':<22}{'value':>12}{'threshold':>16}  pass")
    for name, c in payload["checks"].items():
        val = c["value"]
        val_s = f"{val:.4f}" if isinstance(val, float) else str(val)
        print(f"{name:<22}{val_s:>12}{c['threshold']:>16}  {c['pass']}")
    print(f"\n{payload['passed']}/{payload['total']} pass")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())