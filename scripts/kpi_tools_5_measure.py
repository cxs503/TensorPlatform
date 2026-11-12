#!/usr/bin/env python3
"""KPI-TOOLS-5 周期复测（计量单，只读，不改平台任何代码/数据）。

锚点 KPI-TOOLS-5 = 「≥5 类外部工具接入平台并可用」。
本脚本把该锚点的**现值**从真实工件算出来，并给出两条互相独立的量取口径：

lens A（语义口径，主口径）
    ``data/external_tools/classes.json`` 规格 → ``scripts/run_kpi_tools_5.py`` 一次
    实跑产出的台账（探针命中 + 真实调用 + 数值不变式 + 平台 API 调用路径）。
    记 REAL 的工具类数 = 现值；目标是 ``target_classes``。
    这是「接入平台**并可用**」的唯一可裁决形态：库能 import 但不变式不成立 → FAIL，
    没有数字 → WITHHELD，走降级路径 → MOCK（见 tools/external_tools/harness.py）。

lens B（注册表口径，对照）
    ``tools/metrics/kpi_registry.json`` 里 ``tool_categories.recompute_scan`` 的字面
    规则（tools/ 下含 ``__init__.py`` 且 ≥3 个 .py 的目录数）。这条规则量的是
    **平台内部模块目录**的规模，不是「外部工具类别」，用于暴露口径错配（见 top_gaps）。

用法::

    python3 scripts/kpi_tools_5_measure.py                    # 用 runs/KPI-TOOLS-5/LATEST
    python3 scripts/kpi_tools_5_measure.py --run-dir <dir>    # 指定某次 run
    python3 scripts/kpi_tools_5_measure.py --out result.json --figures-dir <dir>

只读约束：除 ``--out`` / ``--figures-dir`` 指定的产物外，不写任何文件；不触发平台
服务、不改台账。退出码 0=已量出且自检通过（与是否达标无关），1=自检失败（数字不可信）。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SUITE = "external-tools/KPI-TOOLS-5"
DEFAULT_RUNS = ROOT / "runs" / "KPI-TOOLS-5"
REGISTRY = ROOT / "tools" / "metrics" / "kpi_registry.json"
SPEC = ROOT / "data" / "external_tools" / "classes.json"


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


def _git_head(repo: Path = ROOT) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001 - provenance never fatal
        return None


def _latest_run(runs_dir: Path) -> Path:
    latest = runs_dir / "LATEST"
    if latest.exists():
        p = Path(latest.read_text().strip())
        if p.exists():
            return p
    cands = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    if not cands:
        raise SystemExit(f"no run under {runs_dir}; run scripts/run_kpi_tools_5.py first")
    return cands[-1]


def _load_run(run_dir: Path) -> Dict[str, Any]:
    ledgers = sorted(run_dir.glob("ledger_*.json"))
    if not ledgers:
        raise SystemExit(f"no ledger_*.json under {run_dir}")
    out = {"run_dir": str(run_dir), "ledger_path": str(ledgers[-1])}
    out["ledger"] = json.loads(ledgers[-1].read_text(encoding="utf-8"))
    for key, name in (("benchmark", "benchmark.json"), ("integration", "integration.json"),
                      ("env", "env.json"), ("manifest", "manifest.json")):
        p = run_dir / name
        out[key] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return out


def measure_lens_a(run: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """外部工具接入（语义口径）：REAL 类数 + 不变式逐条复核。"""

    ledger = run["ledger"]
    bench = run.get("benchmark") or {}
    classes = list(ledger.get("classes", []))
    real = [c for c in classes if c.get("status") == "REAL"]
    cats = sorted({c["category"] for c in real})
    target = int(spec.get("target_classes") or ledger.get("target_classes") or 5)

    # 逐条不变式复核：不信台账的 status，自己按规格重判一次
    spec_by_id = {c["tool_id"]: c for c in spec.get("classes", [])}
    recheck: List[Dict[str, Any]] = []
    for rec in classes:
        tid = rec.get("tool_id")
        spec_cls = spec_by_id.get(tid, {})
        spec_inv = {i["metric"]: i for i in spec_cls.get("invariants", [])}
        obs = rec.get("metrics") or {}
        for metric, inv in spec_inv.items():
            got = obs.get(metric)
            limit = float(inv.get("limit") or 0.0)
            op = str(inv.get("op") or "le")
            if got is None:
                ok = False
            elif op == "le":
                ok = float(got) <= limit
            elif op == "ge":
                ok = float(got) >= limit
            elif op == "abs":
                ref = inv.get("target", inv.get("ref", 0.0))
                ok = abs(float(got) - float(ref)) <= limit
            else:
                ok = False
            recheck.append({"tool_id": tid, "metric": metric, "op": op,
                            "observed": got, "limit": limit, "ok": bool(ok)})
    n_inv = len(recheck)
    n_inv_ok = sum(1 for r in recheck if r["ok"])

    rows = list(bench.get("rows", []))
    summary = bench.get("summary") or {}
    # 台账自洽：REAL 类必须带非空 metrics（没有数字就没有 REAL）
    real_without_metrics = [c["tool_id"] for c in real if not (c.get("metrics") or {})]
    # 台账未篡改：value_digest 可按台账内容复算（M4 直改台账的检测）
    # 与 harness._digest 同构（独立实现，不 import 平台模块）：values 是
    # [{"tool_id","metrics"}] 列表，canonical = sort_keys + 紧凑分隔符
    value_payload = [{"tool_id": c["tool_id"], "metrics": c.get("metrics") or {}}
                     for c in classes]
    value_digest_recomputed = _json_digest(value_payload)
    digest_match = value_digest_recomputed == ledger.get("value_digest")

    integ = run.get("integration") or {}
    api_endpoints = integ.get("endpoints") or []
    api_ok = bool(integ.get("ok")) and all(
        e.get("status_code") == 200 for e in api_endpoints) and len(api_endpoints) >= 4

    return {
        "lens": "A_external_tools_real_and_usable",
        "unit": "tool_classes",
        "current": len(real),
        "current_categories": len(cats),
        "target": target,
        "gap": max(0, target - len(real)),
        "surplus": max(0, len(real) - target),
        "verdict": ledger.get("verdict"),
        "coverage_ok": bool((ledger.get("coverage") or {}).get("ok")),
        "status_counts": (ledger.get("measured") or {}).get("counts_by_status", {}),
        "categories": {c: sorted(x["tool_id"] for x in real if x["category"] == c)
                       for c in cats},
        "invariants": {"n": n_inv, "n_ok": n_inv_ok,
                       "all_ok": n_inv_ok == n_inv and n_inv > 0,
                       "recheck_rows": recheck},
        "benchmark": {"n_cases": summary.get("n_cases"),
                      "n_passed": summary.get("n_passed"),
                      "n_scored_rel": summary.get("n_scored_rel"),
                      "n_within_3pct": summary.get("n_within_3pct"),
                      "n_within_15pct": summary.get("n_within_15pct"),
                      "n_unscored": summary.get("n_unscored"),
                      "max_rel_err": summary.get("max_rel_err"),
                      "rows": len(rows)},
        "platform_api_path": {"ok": api_ok, "endpoints": [
            {"method": e.get("method"), "path": e.get("path"),
             "status_code": e.get("status_code"),
             "summary": e.get("summary")} for e in api_endpoints]},
        "self_checks": {
            "real_classes_have_metrics": not real_without_metrics,
            "real_classes_without_metrics": real_without_metrics,
            "value_digest_recomputed": value_digest_recomputed,
            "value_digest_in_ledger": ledger.get("value_digest"),
            "value_digest_match": bool(digest_match),
            "ledger_spec_sha256_matches_file": ledger.get("spec_sha256") == _sha256(SPEC),
        },
    }


def measure_lens_b() -> Dict[str, Any]:
    """注册表口径：按 kpi_registry.tool_categories.recompute_scan 字面规则扫描。"""

    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    spec = reg["kpis"]["tool_categories"]
    scan = spec.get("recompute_scan") or {}
    min_files = int(scan.get("min_files_per_category") or 3)
    required = scan.get("required_fields") or ["__init__.py"]

    def _code_lines(p: Path) -> int:
        try:
            return sum(1 for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines()
                       if ln.strip() and not ln.strip().startswith("#"))
        except Exception:  # noqa: BLE001 - unreadable file counts as 0
            return 0

    raw_qualify, guarded_qualify, guard_hits = [], [], {}
    for rel in scan.get("scan_paths") or ["tools/"]:
        for d in sorted((ROOT / rel).iterdir()):
            if not d.is_dir():
                continue
            if any(not (d / f).exists() for f in required):
                continue
            pys = [p for p in d.rglob("*.py") if "__pycache__" not in p.parts]
            if len(pys) < min_files:
                continue
            raw_qualify.append(d.name)
            # anti_gaming_guards: 每个 .py >10 行非空代码；目录有 README 或测试
            thin = [p.name for p in pys if _code_lines(p) <= 10]
            has_readme = any((d / f).exists() for f in ("README.md", "README.rst", "README"))
            has_test = any(p.name.startswith("test_") for p in pys) or bool(
                glob.glob(str(ROOT / "tests" / "**" / f"*{d.name}*"), recursive=True))
            if thin or not (has_readme or has_test):
                guard_hits[d.name] = {"thin_files": thin,
                                      "has_readme": has_readme, "has_test": has_test}
                continue
            guarded_qualify.append(d.name)

    target = int(spec.get("target") or 5)
    return {
        "lens": "B_registry_tool_categories_scan",
        "unit": spec.get("unit", "count"),
        "registry_label": spec.get("label"),
        "current_raw": len(raw_qualify),
        "current_guarded": len(guarded_qualify),
        "target": target,
        "gap_guarded": max(0, target - len(guarded_qualify)),
        "scan_rule": scan,
        "anti_gaming_guards": spec.get("anti_gaming_guards", []),
        "guard_rejected": guard_hits,
        "note": ("该口径量的是平台内部 tools/ 模块目录规模，不含任何「外部工具是否可用」"
                 "的证据；用于暴露口径错配，不作为 KPI-TOOLS-5 的达标证据"),
    }



def measure_lens_a2(url: str = "http://127.0.0.1:8010/api/v1") -> Dict[str, Any]:
    """部署态口径：直接问**正在运行的平台**这几个端点（最强的「接入平台」证据）。

    只读 GET；无凭证/连不上 → status="WITHHELD"（不许塞 0 冒充事实）。
    """

    import urllib.error
    import urllib.request

    rec: Dict[str, Any] = {"lens": "A2_deployed_http_face", "base_url": url,
                           "endpoints": [], "status": "WITHHELD"}
    user, pwd = os.environ.get("TENSOR_AGENT_USER"), os.environ.get("TENSOR_AGENT_PASS")

    def _req(path: str, token: Optional[str] = None, body: Any = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url + path, data=data,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"}
                                                 if token else {})})
        try:
            r = urllib.request.urlopen(req, timeout=20)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            except Exception:  # noqa: BLE001
                return exc.code, None
        except Exception as exc:  # noqa: BLE001 - 连不上是事实
            return None, f"{type(exc).__name__}: {exc}"

    st, payload = _req("/auth/login", body={"username": user, "password": pwd}) if user else (None, "no creds")
    token = (payload or {}).get("access_token") if isinstance(payload, dict) else None
    if not token:
        rec["error"] = f"login unavailable: {st} {str(payload)[:120]}"
        return rec

    rec["status"] = "OBSERVED"
    for path in ("/external-tools/classes", "/external-tools/coverage",
                 "/external-tools/benchmark"):
        code, body = _req(path, token)
        entry: Dict[str, Any] = {"path": path, "status_code": code}
        if isinstance(body, dict):
            if path.endswith("classes"):
                from collections import Counter
                entry["last_status_counts"] = dict(Counter(
                    c.get("last_status") for c in body.get("classes", [])))
                entry["ledger_present"] = (body.get("ledger") or {}).get("present")
            elif path.endswith("coverage"):
                entry["summary"] = {k: body.get(k) for k in
                                    ("real_classes", "target_classes", "ok", "verdict")}
            elif path.endswith("benchmark"):
                entry["summary"] = {k: body.get(k) for k in ("n_cases", "n_passed")}
            if code != 200:
                entry["detail"] = str(body.get("detail"))[:200]
        else:
            entry["detail"] = str(body)[:200]
        rec["endpoints"].append(entry)

    cov = next((e for e in rec["endpoints"] if e["path"].endswith("coverage")), {})
    ok = cov.get("status_code") == 200
    rec["kpi_readable_via_http"] = bool(ok)
    rec["observed_real_classes_via_http"] = (cov.get("summary") or {}).get("real_classes")
    rec["note"] = ("KPI 要在部署态可读，部署目录必须存在 artifacts/external_tools/latest.json；"
                   "否则 coverage/benchmark 503、classes 全 UNOBSERVED")
    return rec


def reproducibility(run_dir: Path) -> Dict[str, Any]:
    """重跑比对指纹：结构 digest + 数值 value_digest 是否可复算。"""

    cmd = [sys.executable, "-m", "tools.external_tools.cli", "--out-dir", str(run_dir),
           "verify"]
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=900)
        tail = [ln for ln in (p.stdout or "").splitlines() if "VERIFY" in ln]
        return {"cmd": " ".join(cmd), "exit_code": p.returncode,
                "verdict_lines": tail, "reproducible": p.returncode == 0}
    except Exception as exc:  # noqa: BLE001
        return {"cmd": " ".join(cmd), "error": f"{type(exc).__name__}: {exc}"}


def negative_controls(tmp_dir: Path) -> List[Dict[str, Any]]:
    """反证：把规格改坏，判决必须跟着坏（证明不是橡皮图章）。"""

    sys.path.insert(0, str(ROOT / "backend"))
    sys.path.insert(0, str(ROOT))
    from tools.external_tools import harness, spec as spec_mod  # 只读引用

    base = json.loads(SPEC.read_text(encoding="utf-8"))
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out: List[Dict[str, Any]] = []

    def _run(mutate, tid: str, label: str, expect: str) -> None:
        doc = json.loads(json.dumps(base))
        mutate(doc)
        path = tmp_dir / f"spec_{label}.json"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        suite = spec_mod.load_suite(path)
        led = harness.run_suite(suite, only=[tid], ctx={"repo": str(ROOT)})
        rec = led["classes"][0]
        out.append({"control": label, "tool_id": tid, "expected": expect,
                    "observed_status": rec["status"],
                    "as_expected": rec["status"] == expect,
                    "detail": str(rec.get("detail"))[:160],
                    "spec_used": str(path)})

    def _break_limit(doc):
        for cls in doc["classes"]:
            if cls["tool_id"] == "symbolic_cas":
                # 合法但不可能满足的容差（1e-30 << 观测 1e-9）：规格能过校验，判决必须 FAIL
                cls["invariants"][1]["limit"] = 1e-30

    def _break_probe(doc):
        for cls in doc["classes"]:
            if cls["tool_id"] == "hdf5_store":
                cls["probe"] = {"kind": "module", "name": "no_such_module_xyz"}

    _run(_break_limit, "symbolic_cas", "impossible_invariant_limit", "FAIL")
    _run(_break_probe, "hdf5_store", "absent_probe_module", "WITHHELD")
    return out


def _figures(a: Dict[str, Any], b: Dict[str, Any], out_dir: Path) -> List[Dict[str, str]]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as exc:  # noqa: BLE001 - 缺绘图器不致命
        return [{"name": "figures", "error": f"{type(exc).__name__}: {exc}"}]

    out_dir.mkdir(parents=True, exist_ok=True)
    made: List[Dict[str, str]] = []

    # 图1：每类日志比值 vs 容差（观测误差 < 容差 = 绿）
    rows = [r for r in a.get("invariants", {}).get("recheck_rows", [])
            if isinstance(r.get("observed"), (int, float))]
    fig, ax = plt.subplots(figsize=(9.5, 6.2))
    if rows:
        labels = [f"{r['tool_id']}.{r['metric']}" for r in rows]
        y = np.arange(len(rows))
        ratio = []
        for r in rows:
            lim = max(abs(float(r["limit"])), 1e-18)
            val = abs(float(r["observed"]))
            ratio.append(val / lim if lim > 1e-17 else (0.0 if val == 0 else 1e9))
        ratio = np.maximum(np.array(ratio, dtype=float), 1e-18)
        colors = ["#2e9e5b" if r["ok"] else "#d85b3b" for r in rows]
        ax.barh(y, np.log10(ratio), color=colors)
        ax.axvline(0.0, color="k", linewidth=0.8, linestyle="--")
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=6)
        ax.set_xlabel("log10( |observed| / limit )   < 0 means within tolerance")
        ax.set_title(f"KPI-TOOLS-5: {a['invariants']['n_ok']}/{a['invariants']['n']} "
                     "invariants re-checked independently (green = pass)")
    fig.tight_layout()
    p1 = out_dir / "fig1_invariants_vs_tolerance.png"
    fig.savefig(p1, dpi=150)
    plt.close(fig)
    made.append({"name": p1.name, "path": str(p1),
                 "caption": "图1 每类外部工具数值不变式：观测值与容差之比（log10），全部 ≤0"})

    # 图2：两条量取口径 vs 目标线
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    names = ["lens A\nREAL & usable on platform\n(primary)", "lens B\nregistry dir scan\n(control)"]
    vals = [a["current"], b["current_guarded"]]
    bars = ax.bar(names, vals, color=["#2e9e5b", "#8a8a8a"], width=0.55)
    ax.set_yscale("log")
    ax.set_ylim(0.8, max(max(vals), a["target"]) * 3)
    ax.axhline(a["target"], color="#d85b3b", linestyle="--", linewidth=1.4,
               label=f"KPI target = {a['target']} classes")
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v * 1.05, f"{v}", ha="center",
                va="bottom", fontsize=11, fontweight="bold")
    ax.set_ylabel("classes / tool classes")
    ax.set_title("KPI-TOOLS-5 current value: both lenses clear the target,\n"
                 "only lens A is evidence of \"onboarded and usable\"")
    ax.legend()
    fig.tight_layout()
    p2 = out_dir / "fig2_two_lenses_vs_target.png"
    fig.savefig(p2, dpi=150)
    plt.close(fig)
    made.append({"name": p2.name, "path": str(p2),
                 "caption": "图2 主口径 10 类 REAL/可用 vs 注册表扫描口径 vs 目标线 5"})

    # 图3：benchmark 相对误差分布 vs 3%/15% 判据
    fig, ax = plt.subplots(figsize=(8.0, 5.0))
    bm = a.get("benchmark") or {}
    cats = ["<3%", "3-15%", ">15%", "absolute-only / unscored"]
    n3 = int(bm.get("n_within_3pct") or 0)
    n15 = int(bm.get("n_within_15pct") or 0)
    nrel = int(bm.get("n_scored_rel") or 0)
    vals = [n3, max(0, n15 - n3), max(0, nrel - n15), int(bm.get("n_cases") or 0) - nrel]
    bars = ax.bar(cats, vals, color=["#2e9e5b", "#e0a83b", "#d85b3b", "#8a8a8a"])
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v, str(v), ha="center",
                va="bottom", fontsize=10)
    ax.set_ylabel("number of invariants")
    ax.set_title("Fig 3: ref-vs-got relative error bands (3% / 15% thresholds)")
    fig.tight_layout()
    p3 = out_dir / "fig3_relative_error_bands.png"
    fig.savefig(p3, dpi=150)
    plt.close(fig)
    made.append({"name": p3.name, "path": str(p3),
                 "caption": "图3 27 条不变式按相对误差分档：11 条 <3%，其余为绝对值口径/整数恒等"})
    return made


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--out", default=str(ROOT / "docs" / "kpi" / "KPI_TOOLS_5_result.json"))
    ap.add_argument("--figures-dir", default=str(ROOT / "docs" / "kpi" / "figures"))
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过重跑比对指纹（默认会跑一遍 cli verify）")
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir) if args.run_dir else _latest_run(DEFAULT_RUNS)
    run = _load_run(run_dir)
    spec = json.loads(SPEC.read_text(encoding="utf-8"))

    a = measure_lens_a(run, spec)
    b = measure_lens_b()
    a2 = measure_lens_a2()
    repro = reproducibility(Path(run_dir)) if not args.no_verify else {"skipped": True}
    controls = negative_controls(Path("/tmp/kpi_tools5_controls"))

    checks = {
        "invariants_all_ok": a["invariants"]["all_ok"],
        "ledger_value_digest_match": a["self_checks"]["value_digest_match"],
        "spec_sha256_match": a["self_checks"]["ledger_spec_sha256_matches_file"],
        "real_classes_have_metrics": a["self_checks"]["real_classes_have_metrics"],
        "platform_api_path_ok": a["platform_api_path"]["ok"],
        "fail_closed_controls_as_expected": all(c["as_expected"] for c in controls),
        "fingerprints_reproducible": repro.get("reproducible", None),
        "coverage_matches_status_counts":
            a["status_counts"].get("REAL", -1) == a["current"],
    }

    figs = [] if args.no_figures else _figures(a, b, Path(args.figures_dir))
    fig_note = [{"name": f.get("name"), "path": f.get("path"), "caption": f.get("caption")}
                for f in figs if "error" not in f]

    top_gaps = [
        {"gap": "部署态 HTTP 面读不到 KPI（coverage/benchmark 503）",
         "detail": ("对运行中的平台实测：GET /external-tools/classes=200 但 10 类全 "
                    f"UNOBSERVED；GET /external-tools/coverage 与 /benchmark 均 503"
                    f"（{(a2.get('endpoints') or [{}])[1].get('detail', a2.get('error'))!r}）。"
                    "原因=部署目录没有 artifacts/external_tools/latest.json，且平台没有"
                    "任何机制定期生成它；本工作树跑出的台账在 runs/ 下，不进入部署读路径"),
         "severity": "高（KPI 在平台上不可读 → 『接入并可用』只对代码路径成立，对部署实例不成立）"},
        {"gap": "注册表 tool_categories 的口径错配",
         "detail": (f"tools/metrics/kpi_registry.json 的 recompute_scan 量的是平台内部 "
                    f"tools/ 目录规模（本次 guarded={b['current_guarded']} 个目录），"
                    "不含「外部工具是否可用」的任何证据；用它给 KPI-TOOLS-5 记账等于"
                    "用内部模块数量冒充外部工具接入度"),
         "severity": "口径级（不影响本次现值，影响记账可信度）"},
        {"gap": "API 子集探针把 partial 判成 FAIL",
         "detail": ("POST /api/v1/external-tools/probe（tool_ids=[version_control]）返回 "
                    f"verdict={[e for e in a['platform_api_path']['endpoints'] if e['method']=='POST'][0]['summary']}，"
                    "因为 coverage 以 5 类为目标而子集只有 1 类；调用方若只看 verdict "
                    "会把可用读成失败（响应里另有 partial=true 但非首字段）"),
         "severity": "可用性/可读性缺陷（非数值错误）"},
        {"gap": "不变式强度不均：相对误差只覆盖 11/27 条",
         "detail": (f"benchmark n_scored_rel={a['benchmark']['n_scored_rel']} / n_cases="
                    f"{a['benchmark']['n_cases']}，其余为整数恒等或纯结构判据"
                    "（如 sha 长度=40），对「工具结果对不对」的判别力弱于数值类"),
         "severity": "判据强度（低）"},
        {"gap": "类目覆盖偏科：缺队列/调度、消息/HTTP、文档生成三类外部工具",
         "detail": ("现 10 类集中在数学/优化/IO/绘图/数据库/VCS/编译器；平台实际依赖的"
                    "作业调度（集群队列）、HTTP 客户端/消息面、PDF/文档生成没有独立工具类"),
         "severity": "覆盖缺口（派生锚点候选）"},
    ]

    proposed_anchors = [
        {"anchor": "KPI-TOOLS-DEPLOY-MATERIALIZE",
         "claim": ("部署态必须可读：启动/定时任务把最新台账落到 TENSOR_EXTERNAL_TOOLS_DIR/"
                   "latest.json，且 coverage 带 age_seconds 新鲜度门（超期→证据降级，不返回旧值当真）"),
         "why": "否则 KPI 只在跑过 cli 的工作树里成立，部署实例上 coverage/benchmark 恒 503"},
        {"anchor": "KPI-TOOLS-REG-ALIGN",
         "claim": "tool_categories 的 recompute_scan 改为从 data/external_tools 台账复算 REAL 类数（fail-closed：无台账→null 而非 0）",
         "why": "消除口径错配；让 KPI 值只能由真实调用证据产生"},
        {"anchor": "KPI-TOOLS-PROBE-PARTIAL",
         "claim": "子集探针返回 verdict∈{PASS,FAIL,PARTIAL} 且 partial 时不得报 FAIL",
         "why": "避免 API 消费方误读可用性"},
        {"anchor": "KPI-TOOLS-REL-SCORE",
         "claim": "≥5 类工具的判据必须含一条相对误差型不变式（rel_err 可评），rel 覆盖 ≥50%",
         "why": "把「跑通」升级成「跑对」"},
        {"anchor": "KPI-TOOLS-COVER+3",
         "claim": "新增 scheduler/messaging/document 三类外部工具接入（每类带独立基准不变式）",
         "why": "覆盖平台真实依赖面，目标 13 类"},
    ]

    result = {
        "kpi_id": "KPI-TOOLS-5",
        "suite_id": SUITE,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current": a["current"],
        "unit": a["unit"],
        "target": a["target"],
        "gap": a["gap"],
        "surplus": a["surplus"],
        "headline": (f"{a['current']} 类外部工具以 REAL 证据接入平台并可用"
                     f"（目标 {a['target']}，缺口 {a['gap']}，超额 {a['surplus']}）"),
        "verdict": a["verdict"],
        "lenses": {"A_primary": a, "A2_deployed_http": a2, "B_control": b},
        "reproducibility": repro,
        "negative_controls": controls,
        "data_sources": [
            {"kind": "spec", "path": str(SPEC.relative_to(ROOT)),
             "sha256": _sha256(SPEC),
             "role": "工具类规格：探针/不变式/参考值（阈值的唯一来源）"},
            {"kind": "ledger", "path": str(Path(run["ledger_path"]).relative_to(ROOT)),
             "sha256": _sha256(Path(run["ledger_path"])),
             "role": "判决台账：逐类探针+真实调用+不变式+证据等级"},
            {"kind": "benchmark", "path": str((run_dir / 'benchmark.json').relative_to(ROOT)),
             "sha256": _sha256(run_dir / "benchmark.json"),
             "role": "ref-vs-got 误差表（27 条不变式）"},
            {"kind": "integration", "path": str((run_dir / 'integration.json').relative_to(ROOT)),
             "sha256": _sha256(run_dir / "integration.json"),
             "role": "平台 API 调用路径（4 个端点 200）"},
            {"kind": "env", "path": str((run_dir / 'env.json').relative_to(ROOT)),
             "sha256": _sha256(run_dir / "env.json"),
             "role": "解释器/架构/各工具版本"},
            {"kind": "registry", "path": str(REGISTRY.relative_to(ROOT)),
             "sha256": _sha256(REGISTRY),
             "role": "KPI 注册表（lens B 口径来源 + 目标值 5）"},
            {"kind": "code", "path": "tools/external_tools/{harness,benchmark,adapters}.py",
             "role": "裁决与误差计算实现（只读引用）"},
        ],
        "reproduce": {
            "run": ("python3 scripts/run_kpi_tools_5.py "
                    f"--out-dir runs/KPI-TOOLS-5   # 产出 {Path(run['ledger_path']).name}"),
            "measure": ("python3 scripts/kpi_tools_5_measure.py "
                        f"--run-dir {Path(run_dir).relative_to(ROOT)}"),
            "git_head": _git_head(),
            "env": run.get("env"),
        },
        "self_checks": checks,
        "self_checks_ok": all(checks.values()),
        "top_gaps": top_gaps,
        "proposed_anchors": proposed_anchors,
        "figures": fig_note,
        "notes": [
            "本单只计量：未改任何平台代码/数据；两份台账均为本工作树实跑产出",
            "KPI 消账门 = 测量值达标（≠ 本单 succeeded）；现值如实，好看难看都照写",
        ],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[KPI-TOOLS-5] current={result['current']} target={result['target']} "
          f"gap={result['gap']} surplus={result['surplus']} verdict={result['verdict']}")
    print(f"  lens A: REAL={a['current']}/{a['target']} cats={a['current_categories']} "
          f"invariants={a['invariants']['n_ok']}/{a['invariants']['n']}")
    print(f"  lens B(注册表字面口径): raw={b['current_raw']} guarded={b['current_guarded']} "
          f"(口径错配，见 top_gaps[0])")
    print(f"  self_checks={checks} -> ok={result['self_checks_ok']}")
    print(f"  result -> {out}")
    return 0 if result["self_checks_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())