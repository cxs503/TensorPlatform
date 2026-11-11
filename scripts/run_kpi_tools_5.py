#!/usr/bin/env python3
"""KPI-TOOLS-5 entry point: one command → ledger + benchmark + figures + integration.

The anchor is "≥5 classes of external tool are *onboarded and usable* on the
platform".  A single run of this script produces every artifact the acceptance
gate asks for, so the claim is not assembled by hand:

============================  ===============================================
artifact                      what it proves
============================  ===============================================
``ledger.json`` / ``.md``     probe → smoke call → invariant, fail-closed status
``benchmark.json`` / ``.md``  ref vs got per metric + absolute/relative error
``figures/*.png``             error vs tolerance, REAL coverage, relative error
``integration.json``          platform-side call path (API → service → harness)
``env.json``                  interpreter/platform + versions of the probed tools
``manifest.json``             sha256 of every artifact + spec + git HEAD
``repro.json``                the exact command that reproduces this directory
============================  ===============================================

Usage::

    python3 scripts/run_kpi_tools_5.py                 # full run, default out dir
    python3 scripts/run_kpi_tools_5.py --out-dir X --skip-api

Exit code 0 only when the ledger verdict is PASS *and* every invariant in the
benchmark table is green; anything else is 1 (honest fail, never a quiet zero).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
# ROOT must win over backend/ in sys.path: backend/ contains a same-named `tools`
# namespace, and shadowing it turns a working import into a confusing ModuleNotFound.
for _p in (ROOT / "backend", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.external_tools import benchmark as bench_mod  # noqa: E402
from tools.external_tools import harness, report, spec as spec_mod  # noqa: E402

DEFAULT_SPEC = ROOT / "data" / "external_tools" / "classes.json"
DEFAULT_OUT = ROOT / "runs" / "KPI-TOOLS-5"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_digest(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _git_head(repo: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001 - provenance is optional, never fatal
        return None


def _package_versions(names: List[str]) -> Dict[str, str]:
    from importlib import metadata

    out: Dict[str, str] = {}
    for name in names:
        if name.startswith("exe:"):
            exe = name[4:]
            try:
                r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                                   timeout=20)
                out[name] = ((r.stdout or r.stderr).splitlines() or ["present"])[0].strip()[:80] \
                    if r.returncode == 0 else "present (non-zero --version)"
            except Exception:  # noqa: BLE001 - absent tool is a fact
                out[name] = "absent"
            continue
        try:
            out[name] = metadata.version(name)
        except Exception:  # noqa: BLE001 - absent package is a fact, not an error
            out[name] = "absent"
    return out


def _environment(suite: spec_mod.Suite) -> Dict[str, Any]:
    dists = {}
    for cls in suite.classes:
        probe = cls.probe or {}
        if probe.get("kind") == "module" and probe.get("name"):
            dists[str(probe["name"])] = str(probe["name"])
        if probe.get("kind") == "executable" and probe.get("name"):
            dists[f"exe:{probe['name']}"] = str(probe["name"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": _package_versions(sorted(dists)),
        "git_head": _git_head(ROOT),
        "cwd": os.getcwd(),
        "env_flags": {"TP_PG_DB_set": bool(os.environ.get("TP_PG_DB")),
                      "TENSOR_EXTERNAL_TOOLS_SPEC": os.environ.get(
                          "TENSOR_EXTERNAL_TOOLS_SPEC", "")},
    }


def _figures(bench: Mapping[str, Any], ledger: Mapping[str, Any],
             out_dir: Path) -> List[Dict[str, str]]:
    """Three panels: error vs tolerance, REAL coverage, relative error bands."""

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as exc:  # noqa: BLE001 - a missing plotter must not fail the run
        return [{"name": "figures", "error": f"{type(exc).__name__}: {exc}"}]

    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    rows = list(bench.get("rows", []))
    made: List[Dict[str, str]] = []

    # ── panel A: |abs_err| vs tolerance (log10, zero -> floor 1e-18) ──────────
    labels, errs, tols = [], [], []
    for r in rows:
        if r.get("abs_err") is None:
            continue
        labels.append(r["case_id"])
        errs.append(max(float(r["abs_err"]), 1e-18))
        tols.append(max(float(r.get("tolerance") or 0.0), 1e-18))
    fig, axes = plt.subplots(1, 3, figsize=(17.0, 5.2))
    ax = axes[0]
    if labels:
        y = np.arange(len(labels))
        ax.barh(y - 0.2, np.log10(errs), height=0.4, color="#3b7dd8",
                label="log10 |abs_err|")
        ax.barh(y + 0.2, np.log10(tols), height=0.4, color="#d85b3b",
                label="log10 tolerance")
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=6)
        ax.axvline(0.0, color="k", linewidth=0.6)
    ax.set_xlabel("log10")
    ax.set_title("A · observed error vs tolerance (left of red = pass)")
    ax.legend(fontsize=7, loc="lower right")

    # ── panel B: REAL coverage per category against the KPI target ───────────
    ax = axes[1]
    by_cat: Dict[str, int] = {}
    for rec in ledger.get("classes", []):
        if rec.get("status") == "REAL":
            by_cat[str(rec.get("category"))] = by_cat.get(str(rec.get("category")), 0) + 1
    target = int((ledger.get("coverage") or {}).get("target_classes") or 5)
    real = len(ledger.get("measured", {}).get("real_tool_ids", []))
    ax.bar(["REAL", "target"], [real, target], color=["#2e9e5b", "#999999"])
    ax.axhline(target, color="#d85b3b", linestyle="--", linewidth=1.0)
    ax.set_ylim(0, max(real, target) + 2)
    ax.set_ylabel("tool classes")
    ax.set_title(f"B · coverage {real}/{target} REAL classes "
                 f"({len(by_cat)} categories)")

    # ── panel C: relative error per value case, with 3% / 15% bands ──────────
    ax = axes[2]
    rel = [r for r in rows if r.get("rel_err") is not None]
    if rel:
        xs = np.arange(len(rel))
        vals = [max(float(r["rel_err"]), 1e-18) for r in rel]
        ax.bar(xs, vals, color="#3b7dd8")
        ax.set_yscale("log")
        ax.set_xticks(xs)
        ax.set_xticklabels([r["case_id"].split(".")[-1] for r in rel],
                           rotation=60, fontsize=6, ha="right")
        ax.axhline(bench_mod.REL_LIMIT_STRICT, color="#2e9e5b", linestyle="--",
                   linewidth=1.0, label="3%")
        ax.axhline(bench_mod.REL_LIMIT_UNACCEPTABLE, color="#d85b3b",
                   linestyle="--", linewidth=1.0, label="15%")
        ax.legend(fontsize=7)
    ax.set_ylabel("relative error")
    ax.set_title("C · ref-vs-got relative error")

    fig.tight_layout()
    p = fig_dir / "benchmark_summary.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    made.append({"name": "benchmark_summary.png",
                 "path": str(p.relative_to(out_dir)),
                 "sha256": _sha256(p),
                 "caption": "A 误差 vs 容差；B REAL 类数 vs KPI 目标；C 相对误差 vs 3%/15% 线"})
    return made


def _api_integration(spec_file: Path, ledger_dir: Path) -> Dict[str, Any]:
    """Call the platform HTTP face through the same router the app mounts."""

    os.environ["TENSOR_EXTERNAL_TOOLS_SPEC"] = str(spec_file)
    os.environ["TENSOR_EXTERNAL_TOOLS_DIR"] = str(ledger_dir)
    record: Dict[str, Any] = {"endpoints": [], "ok": False}
    try:
        import fastapi
        from fastapi.testclient import TestClient

        from api import external_tools as api_mod

        app = fastapi.FastAPI()
        app.include_router(api_mod.router, prefix="/api/v1")
        client = TestClient(app)
    except Exception as exc:  # noqa: BLE001 - integration is evidence, not a blocker
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    for method, path, body in (
            ("GET", "/api/v1/external-tools/classes", None),
            ("GET", "/api/v1/external-tools/coverage", None),
            ("GET", "/api/v1/external-tools/benchmark", None),
            ("POST", "/api/v1/external-tools/probe",
             {"tool_ids": ["version_control"], "persist": False})):
        try:
            r = client.request(method, path, json=body)
            payload = r.json()
            record["endpoints"].append({
                "method": method, "path": path, "status_code": r.status_code,
                "response_digest": _json_digest(payload),
                "summary": {k: payload.get(k) for k in
                            ("real_classes", "target_classes", "ok", "verdict",
                             "n_cases", "n_passed", "partial") if k in payload},
            })
        except Exception as exc:  # noqa: BLE001
            record["endpoints"].append({"method": method, "path": path,
                                        "error": f"{type(exc).__name__}: {exc}"})
    record["ok"] = all(e.get("status_code") == 200 for e in record["endpoints"])
    record["caller"] = "backend/api/external_tools.py -> services/external_tools_service.py"
    return record


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--skip-api", action="store_true",
                    help="不跑平台 HTTP 集成段（只测 harness + benchmark）")
    ap.add_argument("--trials", type=int, default=0,
                    help="覆盖优化类的试验次数（敏感性用；0=用规格默认 200）")
    args = ap.parse_args(argv)

    t_start = time.perf_counter()
    spec_file = Path(args.spec)
    suite = spec_mod.load_suite(spec_file)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx: Dict[str, Any] = {"repo": args.repo, "out_dir": str(out_dir),
                           "dotenv": os.environ.get("EXTERNAL_TOOLS_DOTENV", "")}
    if args.trials:
        # only the optimisation adapter reads this; other adapters ignore it
        ctx["trials"] = args.trials

    ledger = harness.run_suite(suite, ctx=ctx)
    ledger["verdict"] = harness.verdict(ledger)
    ledger["spec_sha256"] = _sha256(spec_file)
    artifacts = report.write_artifacts(ledger, out_dir, stem="ledger")

    table = bench_mod.build(ledger, suite)
    table["spec_sha256"] = ledger["spec_sha256"]
    bench_paths = bench_mod.write(table, out_dir, stem="benchmark")

    env = _environment(suite)
    (out_dir / "env.json").write_text(
        json.dumps(env, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    figs = _figures(table, ledger, out_dir)

    integration: Dict[str, Any] = {"skipped": True}
    if not args.skip_api:
        integration = _api_integration(spec_file, out_dir)
    (out_dir / "integration.json").write_text(
        json.dumps(integration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    repro_cmd = (f"python3 scripts/run_kpi_tools_5.py --out-dir {args.out_dir}"
                 + (f" --trials {args.trials}" if args.trials else "")
                 + (" --skip-api" if args.skip_api else ""))
    (out_dir / "repro.json").write_text(json.dumps({
        "cmd": repro_cmd, "cwd": str(ROOT), "python": sys.executable,
        "spec": str(spec_file), "note": "parameters above; artifacts are sha256-pinned "
                                        "in manifest.json",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    files: List[Dict[str, Any]] = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({"path": str(path.relative_to(out_dir)),
                          "bytes": path.stat().st_size, "sha256": _sha256(path)})
    manifest = {
        "anchor_slug": bench_mod.ANCHOR,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_dir": str(out_dir),
        "spec": {"path": str(spec_file), "sha256": ledger["spec_sha256"]},
        "git_head": env["git_head"],
        "duration_s": round(time.perf_counter() - t_start, 3),
        "artifacts": files,
        "verdict": ledger["verdict"],
        "benchmark_summary": table["summary"],
        "repro_cmd": repro_cmd,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (Path(args.out_dir) / "LATEST").write_text(str(out_dir) + "\n", encoding="utf-8")

    s = table["summary"]
    print(f"[KPI-TOOLS-5] run_dir={out_dir}")
    print(f"  verdict={ledger['verdict']}  REAL={ledger['coverage']['real_classes']}"
          f"/{ledger['coverage']['target_classes']}  "
          f"classes={len(ledger['classes'])}")
    print(f"  invariants {s['n_passed']}/{s['n_cases']} passed; "
          f"rel-scored={s['n_scored_rel']} within3%={s['n_within_3pct']} "
          f"within15%={s['n_within_15pct']} max_rel_err={s['max_rel_err']}")
    print(f"  integration={'skipped' if integration.get('skipped') else integration.get('ok')}")
    print(f"  repro: {repro_cmd}")

    ok = ledger["verdict"] == "PASS" and bool(s["all_invariants_ok"])
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except spec_mod.SpecError as exc:      # configuration error, not a judgement
        print(f"SPEC ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception:                       # noqa: BLE001 - honest loud failure
        traceback.print_exc()
        sys.exit(1)