"""规格装载 / 结果核对 / 度量对照——纯函数，无数据库、无领域语义。

硬规矩（与 maturity_gate 同一套纪律）：

1. **配置写错 ≠ 判据不过**：未知键/缺键/空 metrics 一律 ``ContractError``，
   笔误不能静默降级成「未验证」。
2. **数字由平台复算**：``ref`` / ``got`` 由规格指针 + 原始 stdout 文档取出，
   误差在平台侧算；调用方自报的误差字段不采用。
3. **取不到就不写**：stdout 里没有 JSON 文档 / 指针缺失 → 抛错，绝不填 0 或均值。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA_VERSION = "tensorbench-eval/1"

#: TensorBench ``benchmark-result.schema.json`` v1.0.0 的必填顶层键
RESULT_REQUIRED: tuple[str, ...] = (
    "schema_version", "run_id", "benchmark", "implementation", "workload",
    "execution", "metrics", "validation",
)
RESULT_SCHEMA_VERSION = "1.0.0"

_SPEC_TOP = {"benchmark", "workload", "execution", "evaluation", "implementation", "notes"}
_METRIC_KEYS = {"name", "got_pointer", "ref_pointer", "tolerance_rel", "unit", "better"}
_EVAL_KEYS = {"metrics", "extract", "notes"}
_EXTRACT_KEYS = {"format"}


class ContractError(ValueError):
    """规格/结果/原始输出不合法（配置或取证问题，不是「判据不过」）。"""


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def spec_digest(spec: Mapping[str, Any]) -> str:
    return digest_of_text(canonical_json(spec))


# ───────────────────────────── 规格 ─────────────────────────────

def parse_spec(spec: Mapping[str, Any]) -> Dict[str, Any]:
    """严格解析基准规格；返回规范化视图（不改原对象）。"""
    if not isinstance(spec, Mapping):
        raise ContractError("spec must be an object")
    unknown = set(spec) - _SPEC_TOP
    if unknown:
        raise ContractError(f"unknown spec keys: {sorted(unknown)}")

    bench = spec.get("benchmark")
    if not isinstance(bench, Mapping) or not bench.get("benchmark_id") or not bench.get("version"):
        raise ContractError("spec.benchmark.{benchmark_id,version} required")

    workload = spec.get("workload")
    if not isinstance(workload, Mapping):
        raise ContractError("spec.workload must be an object")
    for key in ("workload_id", "version"):
        if not workload.get(key):
            raise ContractError(f"spec.workload.{key} required")
    command = workload.get("command")
    if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command):
        raise ContractError("spec.workload.command must be a non-empty string array")

    evaluation = spec.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise ContractError("spec.evaluation must be an object")
    unknown = set(evaluation) - _EVAL_KEYS
    if unknown:
        raise ContractError(f"unknown spec.evaluation keys: {sorted(unknown)}")
    extract = evaluation.get("extract") or {"format": "json_stdout"}
    if not isinstance(extract, Mapping) or set(extract) - _EXTRACT_KEYS:
        raise ContractError("spec.evaluation.extract must be {format}")
    if extract.get("format", "json_stdout") != "json_stdout":
        raise ContractError("only extract.format='json_stdout' is supported")
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ContractError("spec.evaluation.metrics must be a non-empty array")
    norm_metrics: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in metrics:
        if not isinstance(item, Mapping):
            raise ContractError("each metric must be an object")
        if set(item) - _METRIC_KEYS:
            raise ContractError(f"unknown metric keys: {sorted(set(item) - _METRIC_KEYS)}")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ContractError("metric.name required")
        if name in seen:
            raise ContractError(f"duplicate metric.name: {name}")
        seen.add(name)
        for key in ("got_pointer", "ref_pointer"):
            ptr = item.get(key)
            if not isinstance(ptr, str) or not ptr:
                raise ContractError(f"metric {name}: {key} required")
        tol = item.get("tolerance_rel")
        if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol < 0:
            raise ContractError(f"metric {name}: tolerance_rel must be a non-negative number")
        norm_metrics.append({
            "name": name, "got_pointer": item["got_pointer"],
            "ref_pointer": item["ref_pointer"], "tolerance_rel": float(tol),
            "unit": str(item.get("unit", "-")), "better": str(item.get("better", "target")),
        })

    execution = spec.get("execution") or {}
    if not isinstance(execution, Mapping):
        raise ContractError("spec.execution must be an object")
    timeout = execution.get("timeout_seconds", 300)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ContractError("spec.execution.timeout_seconds must be positive")

    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": {"benchmark_id": str(bench["benchmark_id"]), "version": str(bench["version"])},
        "workload": {
            "workload_id": str(workload["workload_id"]), "version": str(workload["version"]),
            "command": [str(p) for p in command],
            "cwd": str(workload.get("cwd", ".")),
            "precision": str(workload.get("precision", "not_declared")),
            "configuration": dict(workload.get("configuration") or {}),
        },
        "implementation": dict(spec.get("implementation") or {}),
        "execution": {"timeout_seconds": float(timeout),
                      "env": dict(execution.get("env") or {})},
        "evaluation": {"extract": {"format": "json_stdout"}, "metrics": norm_metrics},
        "sha256": spec_digest(spec),
    }


# ───────────────────────────── 结果 ─────────────────────────────

def validate_result(result: Mapping[str, Any], parsed_spec: Mapping[str, Any]) -> Dict[str, Any]:
    """按 TensorBench result-schema 必填面核对；benchmark 身份须与规格一致。"""
    if not isinstance(result, Mapping):
        raise ContractError("result must be an object")
    missing = [k for k in RESULT_REQUIRED if k not in result]
    if missing:
        raise ContractError(f"result missing required keys: {missing}")
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ContractError(f"result.schema_version must be {RESULT_SCHEMA_VERSION}")
    bench = result.get("benchmark") or {}
    want = parsed_spec["benchmark"]
    if bench.get("benchmark_id") != want["benchmark_id"]:
        raise ContractError(
            f"result.benchmark_id {bench.get('benchmark_id')!r} != spec {want['benchmark_id']!r}")
    if bench.get("version") != want["version"]:
        raise ContractError(f"result.benchmark.version {bench.get('version')!r} != spec {want['version']!r}")
    execution = result.get("execution") or {}
    if not isinstance(execution, Mapping) or not execution.get("status"):
        raise ContractError("result.execution.status required")
    impl = result.get("implementation") or {}
    if not impl.get("project") or not impl.get("revision"):
        raise ContractError("result.implementation.{project,revision} required")
    workload = result.get("workload") or {}
    if workload.get("workload_id") != parsed_spec["workload"]["workload_id"]:
        raise ContractError("result.workload.workload_id != spec")
    run_id = result.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ContractError("result.run_id required")
    return {
        "run_id": run_id,
        "benchmark_id": str(bench["benchmark_id"]),
        "benchmark_version": str(bench["version"]),
        "workload_id": str(workload.get("workload_id")),
        "workload_version": str(workload.get("version")),
        "execution_status": str(execution.get("status")),
        "implementation_project": str(impl.get("project")),
        "implementation_revision": str(impl.get("revision")),
    }


# ─────────────────────── 原始输出 → 度量对照 ───────────────────────

def extract_document(stdout: str) -> Dict[str, Any]:
    """从 workload stdout 取一份 JSON 文档（``json_stdout`` 约定）。

    先整体解析；不行再从第一个 ``{`` 到最后一个 ``}`` 试一次（允许日志前缀）。
    仍失败 → :class:`ContractError`（取不到就不写，绝不补零）。
    """
    if isinstance(stdout, (bytes, bytearray)):
        stdout = bytes(stdout).decode("utf-8", "replace")
    if not isinstance(stdout, str):
        raise ContractError("workload stdout must be text")
    text = stdout.strip()
    if not text:
        raise ContractError("workload stdout is empty")
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            doc = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(doc, dict):
            return doc
    raise ContractError("no JSON object found in workload stdout")


def _get_pointer(doc: Any, pointer: str) -> Any:
    from tools.maturity_gate.evidence import PointerError, get_pointer
    try:
        return get_pointer(doc, pointer)
    except PointerError as exc:
        raise ContractError(f"pointer {pointer!r} not found in workload document") from exc


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where} must be numeric, got {type(value).__name__}")
    return float(value)


def evaluate_metrics(parsed_spec: Mapping[str, Any],
                     doc: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """逐度量算 ref/got/误差/是否达标。误差在平台侧复算。"""
    rows: List[Dict[str, Any]] = []
    for metric in parsed_spec["evaluation"]["metrics"]:
        ref = _number(_get_pointer(doc, metric["ref_pointer"]),
                      f"metric {metric['name']} ref")
        got = _number(_get_pointer(doc, metric["got_pointer"]),
                      f"metric {metric['name']} got")
        abs_err = abs(got - ref)
        rel_err: Optional[float] = (abs_err / abs(ref)) if ref != 0 else None
        passed = bool(rel_err is not None and rel_err <= metric["tolerance_rel"])
        rows.append({
            "metric": metric["name"],
            "unit": metric["unit"],
            "ref_value": ref,
            "got_value": got,
            "abs_error": abs_err,
            "rel_error": rel_err,
            "tolerance_rel": metric["tolerance_rel"],
            "passed": passed,
        })
    return rows
