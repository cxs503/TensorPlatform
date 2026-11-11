"""无人值守 campaign 编排器：一条命令跑完整批 benchmark，并给出机器裁决。

为什么需要它（P167W2B-N09）
---------------------------
N08 交付的是**单 suite** 执行器：`runner --config X`。真做回归基础设施时，
那个粒度过细——每一批要跑十几个 suite（正向面、负向对照、漂移探针），
还要回答三个只有**跨 suite** 才能回答的问题：

1. 整批是否可裁（每条 suite 的既有退出码是否都符合**预声明期望**）？
2. 负向对照是否真的挂住了（该 FAIL 的必须 FAIL，fail-closed）？
3. 无人值守算例总数相对**外部基线**涨了多少（KPI 增量），证据链是否完整？

本模块把这些"批级"结论变成一次运行 + 一个退出码；它是**零领域语义**的：
suite 列表、期望、阈值全在 campaign JSON 里，代码里没有任何应用域词条。

退出码
------
====  ==========================================================
0     全部预声明批级判据通过
1     有批级判据不通过（含期望不符 / 证据链破 / KPI 未达目标）
3     campaign 配置非法（fail-closed：配置错 ≠ 算例失败）
4     运行期内部错误
====  ==========================================================

CLI::

    # 1) 抓一次外部基线（KPI ETL 口径，SQL 溯源）
    python3 -m tools.benchmark_runner.kpi_baseline --capture \
        --out artifacts/benchmark_runner/kpi_baseline.json

    # 2) 一条命令跑完整批（正向 suite + 负向对照 + 漂移探针 + KPI 对账）
    python3 -m tools.benchmark_runner.unattended \
        --campaign tools/benchmark_runner/campaigns/pf_default.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

if __package__ in (None, ""):  # 允许 `python3 tools/benchmark_runner/unattended.py` 直跑
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from . import ENGINE_VERSION  # noqa: E402
from . import artifact as art  # noqa: E402
from . import gate  # noqa: E402
from . import kpi_bridge  # noqa: E402
from .config import ConfigError, load_config  # noqa: E402
from .model import is_finite  # noqa: E402
from .runner import EXIT_OK, run_config  # noqa: E402

EXIT_OK_, EXIT_FAIL, EXIT_CONFIG, EXIT_INTERNAL = 0, 1, 3, 4
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_EXPECTS = ("pass", "fail")
_BASELINE_MODES = ("establish_if_missing", "update", "none")
_PROBE_CODES = {"clean": 0, "warn": 1, "critical": 2}


# ─────────────────────────── campaign 配置（fail-closed 校验） ───────────────────────────
def validate_campaign(data: Any) -> Dict[str, Any]:
    """校验 campaign JSON；任何含糊之处一律报错（配置错不等于算例失败）。"""
    if not isinstance(data, Mapping):
        raise ConfigError(f"campaign root must be a mapping, got {type(data).__name__}")
    name = data.get("campaign")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("campaign requires non-empty 'campaign'")

    suites = data.get("suites")
    if not isinstance(suites, list) or not suites:
        raise ConfigError(f"campaign {name!r}: non-empty 'suites' is required")
    seen: set[str] = set()
    norm_suites: List[Dict[str, Any]] = []
    for i, s in enumerate(suites):
        if not isinstance(s, Mapping):
            raise ConfigError(f"campaign {name!r}: suites[{i}] must be a mapping")
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            raise ConfigError(f"campaign {name!r}: suites[{i}] missing 'id'")
        if sid in seen:
            raise ConfigError(f"campaign {name!r}: duplicate suite id {sid!r}")
        seen.add(sid)
        cfg_path = s.get("config")
        if not isinstance(cfg_path, str) or not cfg_path:
            raise ConfigError(f"suite {sid!r}: 'config' path required")
        expect = s.get("expect", "pass")
        if expect not in _EXPECTS:
            raise ConfigError(f"suite {sid!r}: 'expect' must be one of {_EXPECTS}, got {expect!r}")
        mode = s.get("baseline", "establish_if_missing")
        if mode not in _BASELINE_MODES:
            raise ConfigError(f"suite {sid!r}: 'baseline' must be one of {_BASELINE_MODES}, "
                              f"got {mode!r}")
        norm_suites.append({"id": sid, "config": cfg_path, "expect": expect,
                            "baseline": mode, "control": bool(s.get("control", expect == "fail")),
                            "count_in_kpi": bool(s.get("count_in_kpi", True))})

    probes = data.get("drift_probes", []) or []
    if not isinstance(probes, list):
        raise ConfigError(f"campaign {name!r}: 'drift_probes' must be a list")
    norm_probes: List[Dict[str, Any]] = []
    for i, p in enumerate(probes):
        if not isinstance(p, Mapping):
            raise ConfigError(f"campaign {name!r}: drift_probes[{i}] must be a mapping")
        pid = p.get("id")
        if not isinstance(pid, str) or not pid:
            raise ConfigError(f"campaign {name!r}: drift_probes[{i}] missing 'id'")
        base = p.get("base")
        if base not in seen:
            raise ConfigError(f"drift probe {pid!r}: base suite {base!r} not in 'suites'")
        expect = p.get("expect_status")
        if expect not in _PROBE_CODES:
            raise ConfigError(f"drift probe {pid!r}: 'expect_status' must be one of "
                              f"{tuple(_PROBE_CODES)}, got {expect!r}")
        overhead = p.get("overhead")
        if not is_finite(overhead) or not (0.0 < float(overhead) < 1.0):
            raise ConfigError(f"drift probe {pid!r}: 'overhead' must be a finite number in (0,1)")
        norm_probes.append({"id": pid, "base": base, "overhead": float(overhead),
                            "expect_status": expect, "track_metrics": list(p.get("track_metrics")
                                                                           or ["evals"])})

    criteria = data.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ConfigError(f"campaign {name!r}: non-empty 'criteria' is required "
                          f"(a batch with no predeclared criterion cannot be called passed)")
    criteria = [gate.validate_criterion(c) for c in criteria]

    targets = data.get("targets") or {}
    if not isinstance(targets, Mapping):
        raise ConfigError(f"campaign {name!r}: 'targets' must be a mapping")
    target_cases = targets.get("unattended_cases")
    if target_cases is not None and (not is_finite(target_cases) or float(target_cases) <= 0):
        raise ConfigError(f"campaign {name!r}: targets.unattended_cases must be a positive number")

    recon = data.get("reconciliation") or {}
    if not isinstance(recon, Mapping):
        raise ConfigError(f"campaign {name!r}: 'reconciliation' must be a mapping")

    return {
        "campaign": name.strip(),
        "version": int(data.get("version", 1) or 1),
        "description": str(data.get("description", "")),
        "drift_threshold": float(data.get("drift_threshold", 0.03)),
        "suites": norm_suites,
        "drift_probes": norm_probes,
        "criteria": criteria,
        "targets": {k: float(v) for k, v in targets.items() if is_finite(v)},
        "reconciliation": {"baseline": recon.get("baseline")} if recon.get("baseline") else {},
    }


def load_campaign(path: str | pathlib.Path) -> Dict[str, Any]:
    p = pathlib.Path(path)
    if not p.is_file():
        raise ConfigError(f"campaign not found: {p}")
    raw = p.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid JSON in {p}: {exc}") from exc
    cfg = validate_campaign(data)
    cfg["_campaign_sha256"] = hashlib.sha256(raw).hexdigest()
    cfg["_campaign_path"] = str(p)
    return cfg


# ─────────────────────────── 漂移探针（回归检测的判别力/特异性） ───────────────────────────
def build_probe_config(base_cfg: Mapping[str, Any], overhead: float,
                       suite_name: str) -> Dict[str, Any]:
    """由基础 suite 派生"数值不变、工作量放大 (1+overhead)"的探针配置。

    逐位相同的数值 → 判据门仍应通过；只有 ``evals`` 漂移 → 回归检测器该开口。
    这两件事同时成立，才说明回归检测**独立于**判据门（而不是判据门的影子）。
    """
    cases: List[Dict[str, Any]] = []
    for case in base_cfg["cases"]:
        inputs = dict(case.get("inputs") or {})
        inputs["overhead"] = float(overhead)
        cases.append({
            "id": case["id"],
            "title": f"{case.get('title', case['id'])} (drift probe +{overhead:.0%})",
            "workload": "tools.benchmark_runner.instrumented:integral_with_overhead",
            "inputs": inputs,
            # 探针不做误差判据（没有参考解）：只要求数值有限——门绿，漂移靠回归检测
            "criteria": [{"metric": "value", "op": "finite"}],
            "timeout_s": case.get("timeout_s"),
        })
    return {
        "suite": suite_name,
        "version": int(base_cfg.get("version", 1) or 1),
        "description": "drift probe: identical numbers, inflated workload (regression detector calibration)",
        "drift_threshold": float(base_cfg.get("drift_threshold", 0.03)),
        "timeout_s": float(base_cfg.get("timeout_s") or 300.0),
        "cases": cases,
        "suite_criteria": [
            {"metric": "n_cases", "op": ">=", "value": 1},
            {"metric": "n_error", "op": "==", "value": 0},
        ],
    }


def probe_severity_code(report: Mapping[str, Any]) -> int:
    """把回归报告压成 0=clean / 1=warn / 2=critical（取最坏级别）。"""
    code = 0
    for alert in report.get("alerts", []):
        if alert.get("kind") != "drift":
            continue
        code = max(code, 1 if alert.get("severity") == "warn" else 2)
    return code


# ─────────────────────────── KPI 对账（外部基线 + 引擎增量） ───────────────────────────
def reconcile(baseline: Optional[Mapping[str, Any]], ledger: Mapping[str, Any],
              target: Optional[float], counted_suites: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """外部基线 + 引擎新增算例 = 无人值守算例总数（口径显式、fail-closed）。

    口径（预声明，不改上游 ETL 定义）：
    * ``baseline`` = KPI ETL 的 DB 代理口径在**捕获时刻**的值（带 SQL + 名字清单 + sha256）；
    * ``engine``   = 引擎工件树里"判据通过 + 哈希链完整"的去重 ``case_id``；
    * ``delta``    = engine − baseline（按 id 求差，重叠会被**显式**扣掉并报告，不重复计）。
    基线缺失/不可用 → ``status="unavailable"``、``verdict="fail"``（"没比过"≠"达标"）。
    """
    suites = set(counted_suites or [])
    engine_ids = sorted({c["case_id"] for c in ledger.get("cases", [])
                         if not suites or c.get("suite") in suites})
    out: Dict[str, Any] = {
        "definition": ("unattended_cases = external baseline (KPI ETL DB proxy, SQL-traced) "
                       "+ engine-verified distinct case_ids with pass verdict and sha256 chain, "
                       "de-duplicated by id"),
        "engine_case_count": len(engine_ids),
        "engine_case_ids": engine_ids,
        "engine_root": ledger.get("root"),
        "target": target,
        "verdict": "fail",
        "status": "unavailable",
    }
    if not baseline:
        out.update({"reason": "no baseline record: absence of a baseline is not evidence of progress"})
        return out
    if baseline.get("status") != "ok" or not is_finite(baseline.get("value")):
        out.update({"reason": f"baseline unusable: status={baseline.get('status')!r} "
                              f"value={baseline.get('value')!r}",
                    "baseline": {k: baseline.get(k) for k in ("metric", "status", "value",
                                                              "captured_at", "sql")}})
        return out

    base_value = float(baseline["value"])
    base_ids = set(baseline.get("case_ids") or [])
    overlap = sorted(set(engine_ids) & base_ids)
    new_ids = [c for c in engine_ids if c not in base_ids]
    by_suite: Dict[str, int] = {}
    id_to_suite = {c["case_id"]: c.get("suite") for c in ledger.get("cases", [])}
    for cid in new_ids:
        key = str(id_to_suite.get(cid))
        by_suite[key] = by_suite.get(key, 0) + 1
    combined = base_value + len(new_ids)
    verdict = "pass"
    reason = "combined unattended cases >= target"
    if target is not None and combined < float(target):
        verdict, reason = "fail", f"combined {combined} < target {int(target)}"
    out.update({
        "status": "ok",
        "verdict": verdict,
        "reason": reason,
        "baseline": {"metric": baseline.get("metric"), "value": base_value,
                     "captured_at": baseline.get("captured_at"),
                     "sql": baseline.get("sql"), "artifact": baseline.get("artifact"),
                     "artifact_sha256": baseline.get("artifact_sha256"),
                     "case_ids_sha256": baseline.get("case_ids_sha256")},
        "baseline_case_count": int(base_value),
        "overlap_count": len(overlap),
        "overlap_ids": overlap,
        "delta_case_count": len(new_ids),
        "delta_case_ids": new_ids,
        "delta_by_suite": dict(sorted(by_suite.items())),
        "combined": combined,
    })
    return out


# ─────────────────────────── 图（无人值守：Agg 后端，不依赖显示器） ───────────────────────────
def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def render_campaign_figure(out_png: str | pathlib.Path, summary: Mapping[str, Any]) -> pathlib.Path:
    """两面板：KPI 进度（基线+增量 vs 目标）与逐 suite 通过/失败计数。"""
    plt = _mpl()
    recon = summary.get("reconciliation") or {}
    suites = summary.get("suites") or []
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    ax = axes[0]
    base = float(recon.get("baseline_case_count") or 0.0)
    delta = float(recon.get("delta_case_count") or 0.0)
    target = recon.get("target")
    ax.bar(["baseline", "engine delta"], [base, delta], color=["#4c78a8", "#59a14f"],
           label="baseline / delta")
    ax.bar(["baseline", "engine delta"], [0, base], color=["#4c78a8", "#4c78a8"], alpha=0.35)
    ax.axhline(base + delta, color="#333", lw=1, ls=":", label=f"combined={base + delta:.0f}")
    if target is not None:
        ax.axhline(float(target), color="#e45756", lw=1.6, ls="--", label=f"target={float(target):.0f}")
    ax.set_ylabel("unattended cases")
    ax.set_title("unattended_cases: baseline + engine delta")
    ax.legend(fontsize=8)

    ax = axes[1]
    ids = [s["id"] for s in suites]
    passed = [1 if s.get("passed") else 0 for s in suites]
    expected_fail = [1 if (s.get("expect") == "fail" and not s.get("passed")) else 0 for s in suites]
    ax.barh(ids, passed, color="#59a14f", label="gate pass")
    ax.barh(ids, expected_fail, color="#e45756", label="expected fail (control held)")
    ax.set_xlabel("verdict (1 = as declared)")
    ax.set_title("per-suite verdicts vs predeclared expectation")
    ax.legend(fontsize=8)
    fig.tight_layout()
    out = pathlib.Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def render_probe_figure(out_png: str | pathlib.Path, probes: Sequence[Mapping[str, Any]],
                        threshold: float) -> pathlib.Path:
    """漂移探针：观测漂移 vs 预声明容差（特异性/判别力一张图）。"""
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    ids = [p["id"] for p in probes]
    drifts = [100.0 * float(p.get("observed_drift") or 0.0) for p in probes]
    colors = ["#59a14f" if p.get("ok") else "#e45756" for p in probes]
    ax.bar(ids, drifts, color=colors)
    ax.axhline(100.0 * threshold, color="#e45756", ls="--", lw=1.4,
               label=f"warn threshold {100 * threshold:.0f}%")
    ax.axhline(300.0 * threshold, color="#b0413e", ls=":", lw=1.4,
               label=f"critical threshold {300 * threshold:.0f}%")
    for p, d in zip(probes, drifts):
        ax.annotate(f"{d:.1f}%\n(pred {p['expect_status']})", (p["id"], d),
                    textcoords="offset points", xytext=(0, 4), ha="center", fontsize=7)
    ax.set_ylabel("observed drift on evals [%]")
    ax.set_title("regression detector: specificity & discriminative power")
    ax.legend(fontsize=7)
    fig.tight_layout()
    out = pathlib.Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


# ─────────────────────────── 主编排 ───────────────────────────
def _baseline_path(out_root: pathlib.Path, suite_id: str) -> pathlib.Path:
    return out_root / "baselines" / f"{suite_id}.json"


def _run_one_suite(entry: Mapping[str, Any], out_root: pathlib.Path,
                   campaign_drift: float) -> Dict[str, Any]:
    """跑一条 suite（含基线建立/对比），返回带期望裁决的步骤记录。"""
    sid = entry["id"]
    bp = _baseline_path(out_root, sid)
    mode = entry["baseline"]
    use_baseline = mode != "none"
    update = mode == "update" or (mode == "establish_if_missing" and not bp.is_file())
    summary = run_config(entry["config"], out_root=out_root,
                         baseline_path=bp if use_baseline else None,
                         update_baseline=update and use_baseline,
                         drift=campaign_drift, run_id=None, write=True)
    expect_pass = entry["expect"] == "pass"
    ok = (summary["passed"] is True) == expect_pass
    if ok and expect_pass and summary["exit_code"] not in (EXIT_OK, 2):
        ok = False           # 正向面不允许证据链/配置类异常退出
    if ok and not expect_pass and summary["exit_code"] != 1:
        ok = False           # 负向对照必须**以判据门不通过**的方式挂住（退出码 1）
    return {
        "id": sid,
        "kind": "suite",
        "config": entry["config"],
        "expect": entry["expect"],
        "control": entry["control"],
        "count_in_kpi": entry["count_in_kpi"],
        "passed": bool(summary["passed"]),
        "exit_code": int(summary["exit_code"]),
        "ok": bool(ok),
        "suite_metrics": summary["suite_metrics"],
        "regression_status": (summary.get("regression") or {}).get("status"),
        "baseline_updated": bool(update and use_baseline),
        "baseline_path": str(bp) if use_baseline else None,
        "integrity": {"ok": (summary.get("integrity") or {}).get("ok"),
                      "checked": (summary.get("integrity") or {}).get("checked")},
        "run_dir": (summary.get("paths") or {}).get("out_dir"),
        "manifest_sha256": (summary.get("paths") or {}).get("manifest_sha256"),
        "config_sha256": summary.get("config_sha256"),
    }


def _run_probe(probe: Mapping[str, Any], suites_by_id: Mapping[str, Mapping[str, Any]],
               out_root: pathlib.Path, campaign_drift: float) -> Dict[str, Any]:
    """跑一条漂移探针：数值不变、工作量放大 → 期望回归分级与预声明一致。"""
    base_entry = suites_by_id[probe["base"]]
    base_cfg = load_config(base_entry["config"])
    cfg = build_probe_config(base_cfg, probe["overhead"], suite_name=f"probe_{probe['id']}")
    probe_dir = out_root / "probes"
    cfg_path = probe_dir / f"{cfg['suite']}.json"
    art.write_json(cfg_path, cfg)
    baseline_path = _baseline_path(out_root, probe["base"])
    summary = run_config(cfg_path, out_root=probe_dir, baseline_path=baseline_path,
                         update_baseline=False, drift=campaign_drift,
                         track_metrics=probe["track_metrics"], write=True)
    code = probe_severity_code(summary.get("regression") or {})
    expected = _PROBE_CODES[probe["expect_status"]]
    # 观测漂移**独立于告警**地算一遍：门没响时也要给出"漂了多少"，
    # 否则特异性证据只剩"没有告警"这句空话（0% 与"没测"必须可区分）。
    base_data = art.read_json(baseline_path) if baseline_path.is_file() else {}
    base_cases = (base_data.get("cases") or {})
    observed_drift = None
    for case in summary.get("cases", []):
        bm = (base_cases.get(case["id"]) or {}).get("metrics") or {}
        for metric in probe["track_metrics"]:
            cur, base_val = case.get("metrics", {}).get(metric), bm.get(metric)
            if is_finite(cur) and is_finite(base_val) and float(base_val) != 0.0:
                d = abs(float(cur) - float(base_val)) / abs(float(base_val))
                observed_drift = max(observed_drift or 0.0, d)
    return {
        "id": probe["id"],
        "kind": "drift_probe",
        "base": probe["base"],
        "expect_status": probe["expect_status"],
        "overhead": probe["overhead"],
        "expected_code": expected,
        "observed_code": code,
        "observed_drift": observed_drift,
        "ok": code == expected,
        "exit_code": int(summary["exit_code"]),
        "passed_gate": bool(summary["passed"]),
        "regression_status": (summary.get("regression") or {}).get("status"),
        "integrity": {"ok": (summary.get("integrity") or {}).get("ok")},
        "run_dir": (summary.get("paths") or {}).get("out_dir"),
        "manifest_sha256": (summary.get("paths") or {}).get("manifest_sha256"),
        "config_path": str(cfg_path),
    }


def run_campaign(campaign: Mapping[str, Any], out_root: str | pathlib.Path = "artifacts/benchmark_runner",
                 run_id: Optional[str] = None, write: bool = True) -> Dict[str, Any]:
    """端到端：逐 suite → 漂移探针 → KPI 对账 → 批级判据 → 工件 + 图。"""
    root = pathlib.Path(out_root)
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    drift = float(campaign.get("drift_threshold", 0.03))
    suites_by_id = {s["id"]: s for s in campaign["suites"]}

    steps: List[Dict[str, Any]] = [_run_one_suite(s, root, drift) for s in campaign["suites"]]
    probes: List[Dict[str, Any]] = [_run_probe(p, suites_by_id, root, drift)
                                    for p in campaign.get("drift_probes", [])]

    counted = {s["id"] for s in campaign["suites"] if s["count_in_kpi"]}
    ledger = kpi_bridge.collect(root, require_manifest=True)
    recon_cfg = campaign.get("reconciliation") or {}
    baseline_record = None
    baseline_path = recon_cfg.get("baseline")
    if baseline_path:
        bp = pathlib.Path(baseline_path)
        bp = bp if bp.is_absolute() else _REPO_ROOT / bp
        if bp.is_file():
            baseline_record = art.read_json(bp)
            try:      # 工件路径尽量给仓库相对路径（报告可移植，不泄漏机器绝对路径）
                baseline_record["artifact"] = str(bp.relative_to(_REPO_ROOT))
            except ValueError:
                baseline_record["artifact"] = str(bp)
            baseline_record["artifact_sha256"] = art.sha256_file(bp)
    target = (campaign.get("targets") or {}).get("unattended_cases")
    recon = reconcile(baseline_record, ledger, target, counted_suites=sorted(counted))
    recon["target"] = target

    n_steps = len(steps) + len(probes)
    n_ok = sum(1 for s in steps + probes if s["ok"])
    controls = [s for s in steps if s["control"]]
    controls_held = [s for s in controls if s["ok"] and not s["passed"]]
    integrity_failures = sum(1 for s in steps + probes if s["integrity"].get("ok") is not True)
    probe_codes = {p["expect_status"]: 0 for p in probes}
    for p in probes:
        probe_codes[p["expect_status"]] += 1 if p["ok"] else 0
    metrics: Dict[str, Any] = {
        "steps_total": float(n_steps),
        "steps_ok": float(n_ok),
        "steps_ok_ratio": (n_ok / n_steps) if n_steps else 0.0,
        "suites_passed": float(sum(1 for s in steps if s["passed"])),
        "negative_controls": float(len(controls)),
        "negative_controls_held": float(len(controls_held)),
        "integrity_failures": float(integrity_failures),
        "drift_probes_ok": float(sum(1 for p in probes if p["ok"])),
        "drift_probes_total": float(len(probes)),
        "drift_specificity_ok": 1.0 if probe_codes.get("clean", 1) >= 1 else 0.0,
        "drift_power_ok": 1.0 if probe_codes.get("warn", 1) >= 1 and probe_codes.get("critical", 1) >= 1 else 0.0,
        "unattended_cases_engine": float(recon.get("engine_case_count") or 0),
        "unattended_cases_delta": float(recon.get("delta_case_count") or 0),
        "unattended_cases_baseline": float(recon.get("baseline_case_count") or 0),
        "unattended_cases": float(recon.get("combined") or 0),
    }
    checks = gate.evaluate(campaign["criteria"], metrics, label=f"campaign {campaign['campaign']}")
    passed = gate.verdict_of(checks)

    summary: Dict[str, Any] = {
        "campaign": campaign["campaign"],
        "campaign_version": campaign.get("version", 1),
        "run_id": run_id,
        "engine_version": ENGINE_VERSION,
        "generated_at": art.utc_now(),
        "campaign_sha256": campaign.get("_campaign_sha256"),
        "campaign_path": campaign.get("_campaign_path"),
        "drift_threshold": drift,
        "suites": steps,
        "probes": probes,
        "metrics": metrics,
        "criteria": [c.to_dict() for c in checks],
        "passed": passed,
        "reconciliation": recon,
        "kpi_ledger": {"count": ledger.get("count"), "definition": ledger.get("definition"),
                       "generated_at": ledger.get("generated_at"),
                       "root": ledger.get("root"),
                       "cases": [{k: c.get(k) for k in ("case_id", "suite", "run_id", "status",
                                                        "rel_error", "case_artifact_sha256",
                                                        "manifest_sha256", "config_sha256",
                                                        "git_sha")}
                                 for c in ledger.get("cases", [])]},
        "git_sha": art.collect_provenance(_REPO_ROOT, ignore_paths=(str(root),)).get("git_sha"),
    }

    if write:
        out_dir = root / "campaign" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        fig1 = render_campaign_figure(out_dir / "figures" / "campaign_kpi_vs_target.png", summary)
        fig2 = render_probe_figure(out_dir / "figures" / "regression_probes.png", probes, drift)
        fig3 = None
        try:
            from . import report_figure as rfig  # 复用跨 suite 汇总图（误差 vs 预声明阈值）
            rows = rfig.load_rows(root)
            if rows:
                fig3 = rfig.render(rows, out_dir / "figures" / "suite_error_vs_threshold.png")
        except Exception as exc:                      # noqa: BLE001 - 图失败不该伪造结论
            summary["figure_error"] = f"{type(exc).__name__}: {exc}"
        figs = [p for p in (fig1, fig2, fig3) if p]
        summary["artifacts"] = {
            "dir": str(out_dir),
            "figures": [{"path": str(p), "sha256": art.sha256_file(p), "bytes": p.stat().st_size}
                        for p in figs],
        }
        camp_path = art.write_json(out_dir / "campaign.json", summary)
        # 统一用引擎既有的 manifest（与 suite 工件同一套回验命令）：campaign 目录本身也可验证
        man_path = art.write_manifest(
            out_dir,
            {"suite": summary["campaign"], "run_id": run_id, "passed": passed},
            {"engine_version": ENGINE_VERSION, "config_sha256": summary.get("campaign_sha256"),
             "git_sha": summary.get("git_sha"), "generated_at": summary.get("generated_at")},
        )
        summary["artifacts"].update({"campaign_json": str(camp_path),
                                     "campaign_json_sha256": art.sha256_file(camp_path),
                                     "manifest": str(man_path),
                                     "manifest_sha256": art.sha256_file(man_path)})
        # 回验：图与 JSON 落盘后立刻重算哈希，签过字的证据才算证据
        ok_files = art.verify_manifest(out_dir).get("ok") is True
        summary["artifacts"]["verified"] = ok_files

    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="无人值守 benchmark campaign 编排器（零领域语义）")
    ap.add_argument("--campaign", required=True, help="campaign 配置（.json）")
    ap.add_argument("--out", default="artifacts/benchmark_runner", help="工件根目录")
    ap.add_argument("--run-id", default=None, help="固定 run_id（默认 UTC 时间戳）")
    ap.add_argument("--json", action="store_true", help="把汇总 JSON 打到 stdout")
    args = ap.parse_args(argv)

    try:
        campaign = load_campaign(args.campaign)
    except ConfigError as exc:
        print(f"[unattended] CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        summary = run_campaign(campaign, out_root=args.out, run_id=args.run_id, write=True)
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        print(f"[unattended] INTERNAL ERROR: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    else:
        m = summary["metrics"]
        print(f"campaign={summary['campaign']} run_id={summary['run_id']} passed={summary['passed']}")
        print(f"  suites={int(m['suites_passed'])}/{int(m['steps_total'] - m['drift_probes_total'])} "
              f"controls_held={int(m['negative_controls_held'])}/{int(m['negative_controls'])} "
              f"probes_ok={int(m['drift_probes_ok'])}/{int(m['drift_probes_total'])}")
        print(f"  unattended_cases: baseline={int(m['unattended_cases_baseline'])} "
              f"+ delta={int(m['unattended_cases_delta'])} = {int(m['unattended_cases'])} "
              f"(target={m['unattended_cases_target'] if 'unattended_cases_target' in m else summary['reconciliation'].get('target')})")
        for c in summary["criteria"]:
            print(f"  [{'ok' if c['passed'] else 'FAIL'}] {c['metric']} {c['op']} "
                  f"{c.get('threshold')} (actual={c['actual']})")
        for s in summary["suites"] + summary["probes"]:
            if not s["ok"]:
                print(f"  UNEXPECTED {s['id']}: expect={s.get('expect') or s.get('expect_status')} "
                      f"got passed={s.get('passed')} code={s.get('exit_code')} "
                      f"status={s.get('regression_status')}")
        if summary.get("artifacts"):
            print(f"  artifacts: {summary['artifacts']['dir']} verified={summary['artifacts'].get('verified')}")
    return EXIT_OK_ if summary["passed"] else EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())