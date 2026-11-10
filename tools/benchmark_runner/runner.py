"""无人值守 benchmark runner（配置驱动 → 自动执行 → 自动判据 → 证据工件 → 回归检测）。

一次调用做完全部五步，**不需要任何人工判读**：

1. 读 suite 配置（JSON/YAML），校验阈值（预声明）；
2. 逐算例经点分路径解析 SUT 与参考解 → 执行 → 派生 ``abs_error`` / ``rel_error``；
3. 判据门（:mod:`.gate`）逐条裁决，fail-closed；
4. 工件生成（:mod:`.artifact`）：逐算例 JSON + suite ``result.json`` + PNG + ``manifest.json``（sha256）；
5. 回归检测（:mod:`.regression`）：与基线逐指标对比，漂移 > 阈值告警。

退出码（机器可裁，CI 直接用）::

    0  全部算例通过 & 无回归告警
    1  判据门不通过（有算例 FAIL）
    2  检测到回归漂移（门本身可能仍通过）
    3  配置非法
    4  运行期内部错误

CLI::

    python3 -m tools.benchmark_runner.runner \
        --config tools/benchmark_runner/configs/fx_suite.json \
        --out artifacts/benchmark_runner \
        --baseline artifacts/benchmark_runner/baselines/fx_suite.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

if __package__ in (None, ""):  # 允许 `python3 tools/benchmark_runner/runner.py` 直跑
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from . import ENGINE_VERSION  # noqa: E402
from . import artifact as art  # noqa: E402
from . import gate  # noqa: E402
from . import regression as reg  # noqa: E402
from .config import ConfigError, config_digest, load_config  # noqa: E402
from .model import CaseVerdict, SuiteVerdict, is_finite, rel_error  # noqa: E402

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MAX_SERIES_ECHO = 64          # 工件里序列最多回显 64 个采样点（其余截断并标注）

EXIT_OK, EXIT_GATE_FAIL, EXIT_REGRESSION, EXIT_CONFIG, EXIT_INTERNAL = 0, 1, 2, 3, 4


# ── 点分路径解析（引擎不认识任何具体求解器） ───────────────────────────────
def resolve(path: str):
    """``"module:function"`` → callable。"""
    mod_name, _, fn_name = path.partition(":")
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    mod = importlib.import_module(mod_name)
    try:
        return getattr(mod, fn_name)
    except AttributeError as exc:
        raise ConfigError(f"workload {path!r}: {mod_name} has no attribute {fn_name!r}") from exc


def _trim(value: Any) -> Any:
    if isinstance(value, list) and len(value) > _MAX_SERIES_ECHO:
        return {"_truncated": True, "_n": len(value), "head": value[:_MAX_SERIES_ECHO]}
    return value


def _as_dict(out: Any) -> Dict[str, Any]:
    if isinstance(out, Mapping):
        return dict(out)
    return {"value": out}


def run_case(case: Mapping[str, Any], config_digest_value: Optional[str] = None) -> CaseVerdict:
    """执行一个算例并裁决；异常一律转成 status=error（不崩、不静默）。"""
    cid = case["id"]
    cv = CaseVerdict(case_id=cid, title=case.get("title", cid), inputs=dict(case.get("inputs") or {}))
    metrics: Dict[str, Any] = {}
    try:
        sut = resolve(case["workload"])
        raw = _as_dict(sut(**case["inputs"]))
        cv.outputs = {k: _trim(v) for k, v in raw.items()}
        for k, v in raw.items():
            metrics[k] = v
        value = raw.get("value")
        if case.get("reference"):
            ref_fn = resolve(case["reference"]["workload"])
            ref_raw = _as_dict(ref_fn(**(case["reference"].get("inputs") or {})))
            reference = ref_raw.get("value")
            cv.outputs["reference"] = _trim(ref_raw)
            metrics["reference"] = reference
            metrics["rel_error"] = rel_error(value, reference)
            if is_finite(value) and is_finite(reference):
                metrics["abs_error"] = abs(float(value) - float(reference))
        cv.metrics = metrics
        cv.checks = gate.evaluate(case.get("criteria") or [], metrics, label=f"case {cid}")
        cv.passed = gate.verdict_of(cv.checks)
        cv.status = "pass" if cv.passed else "fail"
        cv.failed_checks = [f"{c.metric}:{c.op}" for c in cv.checks if not c.passed]
    except Exception as exc:                       # noqa: BLE001 - 任何异常都要落证据
        cv.status = "error"
        cv.passed = False
        cv.metrics = metrics
        cv.error = f"{type(exc).__name__}: {exc}"
        cv.checks = cv.checks or []
        cv.failed_checks = ["__error__"]
    cv.outputs.setdefault("_config_sha256", config_digest_value)
    return cv


def suite_metrics(cases: Sequence[CaseVerdict]) -> Dict[str, Any]:
    """suite 级聚合指标（供 suite_criteria 与回归使用；空集 → 不造 0）。"""
    n = len(cases)
    passed = sum(1 for c in cases if c.passed)
    errs = [float(c.metrics["rel_error"]) for c in cases
            if is_finite(c.metrics.get("rel_error"))]
    abss = [float(c.metrics["abs_error"]) for c in cases
            if is_finite(c.metrics.get("abs_error"))]
    return {
        "n_cases": n,
        "n_passed": passed,
        "n_failed": n - passed,
        "n_error": sum(1 for c in cases if c.status == "error"),
        "pass_ratio": (passed / n) if n else None,
        "max_rel_error": max(errs) if errs else None,
        "mean_rel_error": (sum(errs) / len(errs)) if errs else None,
        "max_abs_error": max(abss) if abss else None,
    }


def run_suite(cfg: Mapping[str, Any], run_id: Optional[str] = None) -> SuiteVerdict:
    """跑一整个 suite（纯执行，不写盘）。"""
    run_id = run_id or new_run_id(cfg["suite"])
    digest = config_digest(cfg)
    verdicts = [run_case(c, config_digest_value=digest) for c in cfg["cases"]]
    agg = suite_metrics(verdicts)
    checks = gate.evaluate(cfg.get("suite_criteria") or [], agg, label=f"suite {cfg['suite']}")
    suite_ok = all(v.passed for v in verdicts) and gate.verdict_of(checks)
    return SuiteVerdict(suite=cfg["suite"], run_id=run_id, passed=suite_ok,
                        cases=verdicts, checks=checks, counts=agg)


def new_run_id(suite: str) -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{suite}"


def write_artifacts(out_root: str | pathlib.Path,
                    cfg: Mapping[str, Any],
                    suite: SuiteVerdict,
                    regression_report: Mapping[str, Any],
                    provenance: Mapping[str, Any]) -> Dict[str, Any]:
    """落证据工件，返回路径索引（含 manifest 路径与 sha256）。"""
    out_dir = pathlib.Path(out_root) / suite.suite / suite.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    suite_dict = suite.to_dict()
    suite_dict["suite_metrics"] = suite.counts
    suite_dict["config"] = {k: v for k, v in cfg.items() if not k.startswith("_")}

    case_files: List[str] = []
    for cv in suite.cases:
        payload = cv.to_dict()
        payload["provenance"] = {
            "engine_version": ENGINE_VERSION,
            "config_sha256": provenance.get("config_sha256"),
            "git_sha": provenance.get("git_sha"),
            "generated_at": provenance.get("generated_at"),
            "workload": next(c["workload"] for c in cfg["cases"] if c["id"] == cv.case_id),
            "reference_workload": next((c["reference"] or {}).get("workload")
                                       for c in cfg["cases"] if c["id"] == cv.case_id),
        }
        payload["artifact_sha256"] = art.sha256_obj({k: v for k, v in payload.items()})
        p = art.write_json(out_dir / "cases" / f"{cv.case_id}.json", payload)
        case_files.append(str(p))

    png = art.render_suite_figure(out_dir / "figures" / f"{suite.suite}_verdict.png",
                                  suite_dict, suite_dict["cases"])
    result_path = art.write_json(out_dir / "result.json", {**suite_dict, "provenance": dict(provenance)})
    reg_path = art.write_json(out_dir / "regression.json", dict(regression_report))
    manifest_path = art.write_manifest(out_dir, suite_dict, provenance)
    return {
        "out_dir": str(out_dir),
        "case_files": case_files,
        "figure": str(png),
        "result": str(result_path),
        "regression": str(reg_path),
        "manifest": str(manifest_path),
        "manifest_sha256": art.sha256_file(manifest_path),
    }


def run_config(config_path: str | pathlib.Path,
               out_root: str | pathlib.Path = "artifacts/benchmark_runner",
               baseline_path: Optional[str | pathlib.Path] = None,
               update_baseline: bool = False,
               drift: Optional[float] = None,
               track_metrics: Optional[Sequence[str]] = None,
               run_id: Optional[str] = None,
               write: bool = True) -> Dict[str, Any]:
    """端到端：加载 → 执行 → 判据 → 工件 → 回归。返回汇总 dict（含退出码）。"""
    cfg = load_config(config_path)
    suite = run_suite(cfg, run_id=run_id)
    threshold = float(drift if drift is not None else cfg.get("drift_threshold", reg.DEFAULT_DRIFT))

    prov = art.collect_provenance(_REPO_ROOT, config_digest=cfg.get("_config_sha256"),
                                  ignore_paths=(str(out_root),),
                                  extra={"suite": cfg["suite"], "engine_version": ENGINE_VERSION})
    baseline = None
    if baseline_path:
        bp = pathlib.Path(baseline_path)
        if bp.is_file():
            baseline = art.read_json(bp)
    report = reg.compare(suite.to_dict(), baseline, drift_threshold=threshold,
                         track_metrics=track_metrics)

    paths: Dict[str, Any] = {}
    if write:
        paths = write_artifacts(out_root, cfg, suite, report, prov)
        if update_baseline and baseline_path:
            bp = pathlib.Path(baseline_path)
            bp.parent.mkdir(parents=True, exist_ok=True)
            art.write_json(bp, reg.build_baseline(suite.to_dict(), prov))

    exit_code = EXIT_OK
    if not suite.passed:
        exit_code = EXIT_GATE_FAIL
    elif reg.has_regression(report):
        exit_code = EXIT_REGRESSION

    return {
        "suite": cfg["suite"],
        "run_id": suite.run_id,
        "passed": suite.passed,
        "suite_metrics": suite.counts,
        "cases": [{"id": c.case_id, "status": c.status, "passed": c.passed,
                   "metrics": {k: v for k, v in c.metrics.items() if is_finite(v)},
                   "failed_checks": c.failed_checks, "error": c.error} for c in suite.cases],
        "suite_checks": [c.to_dict() for c in suite.checks],
        "regression": report,
        "paths": paths,
        "exit_code": exit_code,
        "engine_version": ENGINE_VERSION,
        "config_sha256": cfg.get("_config_sha256"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="无人值守 benchmark runner（零领域语义）")
    ap.add_argument("--config", required=True, help="suite 配置（.json/.yaml）")
    ap.add_argument("--out", default="artifacts/benchmark_runner", help="工件根目录")
    ap.add_argument("--baseline", default=None, help="基线 JSON（不存在则首次运行）")
    ap.add_argument("--update-baseline", action="store_true", help="用本次结果覆盖基线")
    ap.add_argument("--drift", type=float, default=None, help="漂移容差（缺省取配置/0.03）")
    ap.add_argument("--track", default=None, help="只跟踪这些指标（逗号分隔）")
    ap.add_argument("--run-id", default=None, help="固定 run_id（默认时间戳）")
    ap.add_argument("--no-write", action="store_true", help="只裁决不落盘（调试）")
    ap.add_argument("--json", action="store_true", help="把汇总 JSON 打到 stdout")
    args = ap.parse_args(argv)

    track = [t.strip() for t in args.track.split(",") if t.strip()] if args.track else None
    try:
        summary = run_config(args.config, out_root=args.out, baseline_path=args.baseline,
                             update_baseline=args.update_baseline, drift=args.drift,
                             track_metrics=track, run_id=args.run_id, write=not args.no_write)
    except ConfigError as exc:
        print(f"[benchmark_runner] CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:                        # noqa: BLE001
        traceback.print_exc()
        print(f"[benchmark_runner] INTERNAL ERROR: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"suite={summary['suite']} run_id={summary['run_id']} "
              f"passed={summary['passed']} cases={summary['suite_metrics'].get('n_passed')}"
              f"/{summary['suite_metrics'].get('n_cases')} regression={summary['regression']['status']}")
        for c in summary["cases"]:
            if not c["passed"]:
                print(f"  FAIL {c['id']}: {c['failed_checks']} {c['error'] or ''}")
        for a in summary["regression"].get("alerts", []):
            print(f"  [{a['severity']}] {a['kind']} {a['message']}")
        if summary["paths"]:
            print(f"  artifacts: {summary['paths']['out_dir']}")
            print(f"  manifest : {summary['paths']['manifest']} sha256={summary['paths']['manifest_sha256']}")
    return int(summary["exit_code"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())