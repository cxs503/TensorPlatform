"""Bitwise-reproducibility audit harness (mission m1804, verifier seat).

Measures — instead of asserting — the platform KPI "复现 >= 95%": for every case
in :mod:`tools.repro.kernels` the harness collects the full-precision result
field set, hashes it, and compares hashes

* across repeated in-process evaluations (same interpreter), and
* across independent *processes* that differ in ``PYTHONHASHSEED`` (dict/set
  iteration order) and in the BLAS/OpenMP thread count (reduction order),

which are the two realistic sources of run-to-run drift in a Python numeric
stack.  Comparison is exact: floats are serialised with ``float.hex()`` so the
64-bit pattern is compared, not a rounded decimal.

A deliberately unseeded-random case (``control.unseeded_random``) is carried
through the same pipeline as a **negative control**: if the harness ever
reports that case as reproducible, the gate is broken and the audit is void.

Usage
-----
    python3 tools/repro/repro_audit.py                # full audit + artifacts
    python3 tools/repro/repro_audit.py --emit         # internal: one-process set
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import kernels as K  # noqa: E402

KPI_TARGET = 0.95                 # platform KPI: 复现 >= 95 %
BITWISE_TOL = 0.0
THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")

CONFIGS: List[Tuple[str, Dict[str, str]]] = [
    ("inproc.A", {}),
    ("inproc.B", {}),
    ("proc.hashseed0.thr1", {"PYTHONHASHSEED": "0", "threads": "1"}),
    ("proc.hashseed12345.thr4", {"PYTHONHASHSEED": "12345", "threads": "4"}),
    ("proc.hashseed987654321.thr16", {"PYTHONHASHSEED": "987654321", "threads": "16"}),
    ("proc.hashseedrandom.thr2", {"PYTHONHASHSEED": "random", "threads": "2"}),
]


# ---------------------------------------------------------------------------
# canonicalisation
# ---------------------------------------------------------------------------
def to_hex(values: Dict[str, float]) -> Dict[str, str]:
    out = {}
    for k, v in values.items():
        try:
            out[k] = float(v).hex()
        except (TypeError, ValueError):
            out[k] = f"nonfloat:{v!r}"
    return out


def from_hex(hexmap: Dict[str, str]) -> Dict[str, float]:
    out = {}
    for k, v in hexmap.items():
        try:
            out[k] = float.fromhex(v)
        except (TypeError, ValueError):
            out[k] = float("nan")
    return out


def digest(hexmap: Dict[str, str]) -> str:
    payload = json.dumps(hexmap, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------
def collect_local(with_control: bool = True) -> Dict[str, Dict[str, str]]:
    data = {name: to_hex(K.run_case(name)) for name in K.CASES}
    if with_control:
        data.update({name: to_hex(K.run_control(name)) for name in K.CONTROLS})
    return data


def collect_subprocess(cfg: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    env = dict(os.environ)
    for var in THREAD_VARS:
        env[var] = cfg.get("threads", "1")
    env["PYTHONHASHSEED"] = cfg.get("PYTHONHASHSEED", "0")
    env.pop("M1804_BASELINE", None)
    cmd = [sys.executable, os.path.join(HERE, "repro_audit.py"), "--emit"]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"subprocess failed ({proc.returncode}): {proc.stderr[-400:]}")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------
def compare(base: Dict[str, float], other: Dict[str, float]) -> Dict[str, object]:
    paths = sorted(set(base) | set(other))
    mismatch = 0
    max_rel = 0.0
    worst = ""
    missing = 0
    for p in paths:
        if p not in base or p not in other:
            missing += 1
            continue
        b, o = base[p], other[p]
        if b.hex() == o.hex():
            continue
        mismatch += 1
        scale = max(abs(b), abs(o), 1e-300)
        rel = abs(b - o) / scale
        if rel >= max_rel:
            max_rel, worst = rel, p
    return {"n_paths": len(paths), "n_mismatch": mismatch, "missing": missing,
            "max_rel_diff": max_rel, "worst_path": worst}


# ---------------------------------------------------------------------------
# static scan for non-determinism sources
# ---------------------------------------------------------------------------
PATTERNS = {
    "unseeded_random_call": re.compile(r"\b(?:np\.random\.(?:rand|randn|random|uniform|normal|choice|permutation|shuffle)\s*\(|random\.(?:random|gauss|uniform|choice|shuffle|randint)\s*\()"),
    "time_based": re.compile(r"\b(?:time\.time\(\)|datetime\.now\(\)|time\.perf_counter\(\))\s*[-+)]"),
    "set_iteration": re.compile(r"for\s+\w+\s+in\s+\{[^}]*\}\s*:"),
}


def static_scan(paths: List[str]) -> Dict[str, object]:
    hits: Dict[str, List[str]] = {k: [] for k in PATTERNS}
    for path in paths:
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for name, pat in PATTERNS.items():
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits[name].append(f"{os.path.relpath(path, ROOT)}:{line}")
    return {k: sorted(v) for k, v in hits.items()}


def scan_targets() -> List[str]:
    base = os.path.join(ROOT, "backend", "services")
    out: List[str] = []
    for dirpath, _dirnames, names in os.walk(base):
        if "test" in dirpath:
            continue
        for n in names:
            if n.endswith(".py"):
                out.append(os.path.join(dirpath, n))
    return sorted(out)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def run_audit(outdir: str) -> Dict[str, object]:
    records = []
    baseline_hex: Dict[str, Dict[str, str]] = {}
    per_config: Dict[str, Dict[str, Dict[str, str]]] = {}

    for label, cfg in CONFIGS:
        if label.startswith("inproc"):
            data = collect_local()
        else:
            data = collect_subprocess(cfg)
        per_config[label] = data
        if not baseline_hex:
            baseline_hex = data

    for name in list(K.CASES) + list(K.CONTROLS):
        base = from_hex(baseline_hex[name])
        row = {"case": name,
               "domain": (K.DOMAINS.get(name, "negative control")),
               "is_control": name in K.CONTROLS,
               "n_values": len(baseline_hex[name]),
               "configs": []}
        for label, _cfg in CONFIGS:
            other_hex = per_config[label].get(name, {})
            cmp = compare(base, from_hex(other_hex)) if other_hex else \
                {"n_paths": 0, "n_mismatch": -1, "missing": -1, "max_rel_diff": 1.0,
                 "worst_path": "<case missing>"}
            row["configs"].append({"config": label, **cmp,
                                   "sha256": digest(other_hex) if other_hex else None})
        # case is reproducible iff every config matches the baseline bit for bit
        row["bitwise_identical"] = all(c["n_mismatch"] == 0 and c["missing"] == 0
                                       for c in row["configs"])
        row["max_rel_diff"] = max(c["max_rel_diff"] for c in row["configs"])
        row["hashes"] = sorted({c["sha256"] for c in row["configs"] if c["sha256"]})
        records.append(row)

    cases = [r for r in records if not r["is_control"]]
    controls = [r for r in records if r["is_control"]]
    n_ok = sum(1 for r in cases if r["bitwise_identical"])
    ratio = n_ok / len(cases) if cases else 0.0
    control_ok = all(not r["bitwise_identical"] for r in controls)

    result = {
        "mission": 1804,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "numpy": _numpy_version(),
        "comparison": "exact float64 bit pattern (float.hex()); tolerance = 0",
        "configs": [{"id": label, "env": {k: v for k, v in cfg.items()}}
                    for label, cfg in CONFIGS],
        "n_cases": len(cases),
        "n_reproducible": n_ok,
        "ratio": ratio,
        "kpi_target": KPI_TARGET,
        "kpi_pass": ratio >= KPI_TARGET,
        "negative_control_detected": control_ok,
        "gate_verdict": "VALID" if control_ok else "INVALID (gate cannot detect drift)",
        "cases": records,
        "static_scan": static_scan(scan_targets()),
    }
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "repro_results.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    write_tables(result, outdir)
    make_figures(result, outdir)
    return result


def _numpy_version() -> str:
    try:
        import numpy
        return numpy.__version__
    except Exception:  # pragma: no cover
        return "n/a"


# ---------------------------------------------------------------------------
# report tables + figures
# ---------------------------------------------------------------------------
def write_tables(res: Dict[str, object], outdir: str) -> None:
    lines: List[str] = []
    lines.append("<!-- generated by tools/repro/repro_audit.py; do not edit -->\n")
    lines.append("### T1. 逐案例 × 逐配置 逐位一致性矩阵（1 = 与基线逐位相同）\n")
    cfg_ids = [c["id"] for c in res["configs"]]
    lines.append("| 案例 | 学科 | 数值个数 | " + " | ".join(cfg_ids) + " | 判定 |")
    lines.append("|---|---|---|" + "---|" * len(cfg_ids) + "---|")
    for r in res["cases"]:
        marks = ["1" if (c["n_mismatch"] == 0 and c["missing"] == 0) else "0"
                 for c in r["configs"]]
        verdict = "REPRO" if r["bitwise_identical"] else "DRIFT"
        lines.append(f"| {r['case']} | {r['domain']} | {r['n_values']} | "
                     + " | ".join(marks) + f" | {verdict} |")
    lines.append("")
    lines.append("### T2. 逐案例最大相对漂移（跨全部配置）\n")
    lines.append("| 案例 | 最大相对漂移 | 最差字段 | 不同哈希数 |")
    lines.append("|---|---|---|---|")
    for r in res["cases"]:
        worst = ""
        if r["max_rel_diff"] > 0:
            worst = max(r["configs"], key=lambda c: c["max_rel_diff"]).get("worst_path", "")
        lines.append(f"| {r['case']} | {r['max_rel_diff']:.3e} | {worst} | {len(r['hashes'])} |")
    lines.append("")
    lines.append("### T3. KPI 判定\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 案例数 | {res['n_cases']} |")
    lines.append(f"| 逐位可复现案例数 | {res['n_reproducible']} |")
    lines.append(f"| 可复现率 | {res['ratio']:.4f} |")
    lines.append(f"| KPI 目标 | >= {res['kpi_target']} |")
    lines.append(f"| KPI 判定 | {'PASS' if res['kpi_pass'] else 'FAIL'} |")
    lines.append(f"| 负控制被识别 | {'YES' if res['negative_control_detected'] else 'NO'} |")
    lines.append(f"| 门禁有效性 | {res['gate_verdict']} |")
    lines.append("")
    lines.append("### T4. 静态非确定性源扫描（backend/services）\n")
    lines.append("| 模式 | 命中数 | 前 5 处 |")
    lines.append("|---|---|---|")
    for name, hits in res["static_scan"].items():
        lines.append(f"| {name} | {len(hits)} | {'<br>'.join(hits[:5])} |")
    lines.append("")
    with open(os.path.join(outdir, "report_tables.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def make_figures(res: Dict[str, object], outdir: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception:  # pragma: no cover
        return

    cfg_ids = [c["id"] for c in res["configs"]]
    rows = res["cases"]
    mat = np.array([[1.0 if (c["n_mismatch"] == 0 and c["missing"] == 0) else 0.0
                     for c in r["configs"]] for r in rows])

    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    ax.imshow(mat, cmap="RdYlGn", vmin=0.0, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(cfg_ids)))
    ax.set_xticklabels(cfg_ids, rotation=25, ha="right", fontsize=8)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["case"] for r in rows], fontsize=8)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            ax.text(j, i, "match" if mat[i, j] == 1 else "DIFF",
                    ha="center", va="center", fontsize=7,
                    color="black" if mat[i, j] == 1 else "white")
    ax.set_title("Bitwise reproducibility: case x environment configuration\n"
                 f"(ratio = {res['n_reproducible']}/{res['n_cases']} = {res['ratio']:.2%}; "
                 f"gate {res['gate_verdict']})", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig1_repro_matrix.png"), dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.2, 4.2))
    drifts = [max(r["max_rel_diff"], 5e-324) for r in rows]
    colours = ["#c0392b" if r["is_control"] else ("#27ae60" if r["bitwise_identical"] else "#e67e22")
               for r in rows]
    ax.bar(range(len(rows)), drifts, color=colours)
    ax.set_yscale("log")
    ax.axhline(3e-2, color="k", ls="--", lw=1, label="3 % unacceptable band")
    ax.axhline(2.3e-16, color="gray", ls=":", lw=1, label="float64 eps")
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([r["case"] for r in rows], rotation=25, ha="right", fontsize=8)
    ax.set_ylabel("max relative drift vs baseline (log)")
    ax.set_title("Cross-configuration drift per case (0 plotted at floor 5e-324)", fontsize=9)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "fig2_repro_drift.png"), dpi=160)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true", help="print one-process result set as JSON")
    ap.add_argument("--out", default=os.path.join(ROOT, "artifacts", "m1804"))
    args = ap.parse_args(argv)
    if args.emit:
        print(json.dumps(collect_local(), sort_keys=True))
        return 0
    res = run_audit(args.out)
    print(json.dumps({k: res[k] for k in ("n_cases", "n_reproducible", "ratio",
                                          "kpi_pass", "negative_control_detected",
                                          "gate_verdict")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())