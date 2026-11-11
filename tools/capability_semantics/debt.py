"""验证欠账台账（verification-debt registry）：把「未达 REAL 的能力条目」变成
可执行整改项，并把整改前置条件换算成**机器可量测**的缺口。

为什么需要这一层
----------------
判据 (c) 只给一个比例（WITHHELD 占比）。比例是**症状**，不能行动。本模块把每条非
REAL 条目展开成债务记录：理由码 → 提升条件（lift_condition）→ 当前量测缺口
（独立验证脚本真跑出来的判决与误差）→ 下一步动作。这样「证据不足」不再是一句
话，而是一条带 owner 动作、可复算、可关账的欠账。

两数据面
--------
1. **静态面**：来自证据审计的逐条目理由码（``evidence.audit_evidence`` 的 rows）。
2. **动态面**：配置声明的**独立验证脚本被真正执行**，输出 JSON 按声明路径抽取判决
   与误差；量测值入账 ⇒ 「这条欠账还差多少」有数字。

纪律（不可越线）
----------------
* 零领域语义：能力 id / 脚本路径 / 抽取路径 / 外仓清单全部来自配置文件，
  本模块代码里不出现任何域名、算法名或能力名。
* 不造假数：脚本缺失 → ``SKIPPED_MISSING_SCRIPT``；命令失败 → ``EXEC_FAILED``；
  JSON 缺字段 → ``PARSE_INCOMPLETE``；**一律不填默认值冒充量测结果**。
* 可复核：每次执行记录仓内命令、脚本 sha256、退出码、输出 sha256、耗时与
  UTC 时间戳；任何人可重跑得到同一判决。
* 只读：本模块不改写规范能力台账，只产出欠账工件与整改动作。

与证据等级的关系：债务记录**不参与**等级判定（等级仍由 ``evidence.derive_level``
级联决定）；它只解释为什么没到 REAL、以及到 REAL 需要什么。这样避免「为了关账
而改判」的自证循环。
"""
from __future__ import annotations

import datetime as _dt
import json
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .sources import SemanticsConfig, file_sha256, sha256_payload

#: 理由码 → 提升条件（要到 REAL 还差什么；纯动作，不含域语义）
LIFT_CONDITION: dict[str, str] = {
    "NO_DELIVERY_NO_EVIDENCE": "补实现交付，或把声明降级为 NOT_SUPPORTED（不允许无交付声明 REAL）",
    "NO_CODE_DELIVERY_WITH_TEST_REFERENCE": "把测试引用绑到真实实现文件（扫描根/模块路径），或补实现",
    "NO_CODE_DELIVERY_WITH_EVIDENCE": "把证据绑到实现文件（补实现或修正扫描根），使证据可追溯到代码",
    "UNVERIFIED_IMPLEMENTATION": "补独立数值验证工件（基准/文献对标 + 明确容差），误差 ≤3% 才上 REAL",
    "TEST_WITHOUT_NUMERIC_ASSERT": "给测试补数值断言（基准值 + 容差），无数值断言的测试不算精度证据",
    "CODE_ONLY": "补带数值断言的测试，并补独立验证工件",
    "CONTRADICTED_BY_AUDIT": "先修实现缺陷（审计裁决已封顶等级），复验通过后再上 REAL",
    "PATCHED": "已完成误用治理改判，按补丁后的等级继续补证据",
}

#: 债务优先级（按「声明高于实得」的危害排序；rank 越小越急）
PRIORITY_BY_REASON: dict[str, str] = {
    "CONTRADICTED_BY_AUDIT": "P0",
    "NO_DELIVERY_NO_EVIDENCE": "P0",
    "NO_CODE_DELIVERY_WITH_EVIDENCE": "P1",
    "UNVERIFIED_IMPLEMENTATION": "P1",
    "NO_CODE_DELIVERY_WITH_TEST_REFERENCE": "P1",
    "TEST_WITHOUT_NUMERIC_ASSERT": "P2",
    "CODE_ONLY": "P2",
    "PATCHED": "P3",
}

STATUS_OPEN = "OPEN"
STATUS_MEASURED = "MEASURED"
STATUS_BLOCKED = "BLOCKED_OUT_OF_REPO"

RUN_EXECUTED = "EXECUTED"
RUN_MISSING = "SKIPPED_MISSING_SCRIPT"
RUN_FAILED = "EXEC_FAILED"
RUN_PARSE_INCOMPLETE = "PARSE_INCOMPLETE"
RUN_DISABLED = "NOT_RUN_DISABLED"


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dig(payload: Any, path: str) -> Any:
    """按点号路径取值；缺失返回 None（不抛、不猜）。"""
    cur = payload
    for part in [p for p in str(path).split(".") if p]:
        if isinstance(cur, Mapping):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def extract_metric(payload: Any, spec: Mapping[str, Any]) -> float | None:
    """按声明抽取一个指标（支持「列表 + id 选择 + 值字段」与直路径两种形状）。

    ``spec`` 例：``{"list_path": "checks", "id_field": "id", "id": "R1",
    "value_field": "worst_abs_rel_error"}`` 或 ``{"path": "summary.max_error"}``。
    找不到 → None（不返回 0，避免把「没量到」写成「误差为零」）。
    """
    if not isinstance(spec, Mapping):
        return None
    raw: Any
    if spec.get("list_path"):
        rows = _dig(payload, str(spec["list_path"]))
        if not isinstance(rows, list):
            return None
        id_field = str(spec.get("id_field") or "id")
        want = str(spec.get("id"))
        hit = next((r for r in rows
                    if isinstance(r, Mapping) and str(r.get(id_field)) == want), None)
        if hit is None:
            return None
        raw = hit.get(str(spec.get("value_field") or ""))
    else:
        raw = _dig(payload, str(spec.get("path") or ""))
    if raw is None or isinstance(raw, bool):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val:  # NaN 不是量测值
        return None
    unit = str(spec.get("unit") or "pct").lower()
    if unit == "ratio":
        val *= 100.0
    return round(val, 6)


def _resolve_tokens(tokens: Sequence[str], out_file: Path) -> list[str]:
    """把命令里的 ``{out}`` / ``{tmp}`` 占位替换为本次执行的落盘路径。"""
    subst = {"{out}": str(out_file), "{tmp}": str(out_file.parent)}
    cmd: list[str] = []
    for tok in tokens:
        tok = str(tok)
        for k, v in subst.items():
            tok = tok.replace(k, v)
        cmd.append(tok)
    return cmd


def _default_runner(cmd: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    """真跑子进程；超时/OSError 由调用方换算成 EXEC_FAILED。"""
    proc = subprocess.run(list(cmd), cwd=str(cwd), timeout=timeout,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


Runner = Callable[[Sequence[str], Path, float], tuple[int, str, str]]


def run_verifier(spec: Mapping[str, Any], root: Path, runner: Runner | None = None,
                 enabled: bool = True) -> dict[str, Any]:
    """执行一条配置声明的独立验证脚本，返回可复核的量测记录。

    返回字段：``status`` / ``command``（仓内相对）/ ``script_sha256`` /
    ``exit_code`` / ``stdout_sha256`` / ``output_sha256`` / ``duration_s`` /
    ``executed_at`` / ``verdict`` / ``metrics`` / ``metrics_pct`` / ``reason``。
    **任何失败路径都返回明确状态，不返回伪造数字。**
    """
    script = str(spec.get("script") or (spec.get("command") or [""])[0] or "")
    rec: dict[str, Any] = {
        "capability_id": str(spec.get("capability_id") or ""),
        "label": str(spec.get("label") or Path(script).name),
        "status": RUN_DISABLED if not enabled else RUN_MISSING,
        "command": " ".join(shlex.quote(str(t)) for t in (spec.get("command") or [])),
        "script": script,
        "script_sha256": "",
        "exit_code": None,
        "stdout_sha256": "",
        "output_sha256": "",
        "duration_s": None,
        "executed_at": None,
        "verdict": None,
        "verdict_pass": None,
        "metrics": {},
        "reason": "",
    }
    if not enabled:
        rec["reason"] = "verifier execution disabled (run_verifiers=False)"
        return rec
    script_path = (root / script).resolve()
    try:
        script_path.relative_to(root.resolve())
    except ValueError:
        rec["reason"] = f"script escapes repo root: {script}"
        return rec
    if not script or not script_path.is_file():
        rec["reason"] = f"script missing: {script}"
        return rec
    rec["script_sha256"] = file_sha256(script_path)

    tmp_dir = Path(tempfile.mkdtemp(prefix="tp_cs_verify_"))
    out_file = tmp_dir / str(spec.get("out_name") or (Path(script).stem + ".json"))
    cmd = _resolve_tokens(spec.get("command") or [], out_file)
    timeout = float(spec.get("timeout_s") or 120)
    started = time.time()
    try:
        code, stdout, stderr = (runner or _default_runner)(cmd, root, timeout)
    except Exception as exc:  # noqa: BLE001 - 子进程任何异常都归为执行失败
        rec.update(status=RUN_FAILED, duration_s=round(time.time() - started, 3),
                   executed_at=_utc_now(), reason=f"{type(exc).__name__}: {exc}")
        return rec
    rec.update(status=RUN_EXECUTED, exit_code=int(code),
               duration_s=round(time.time() - started, 3), executed_at=_utc_now(),
               stdout_sha256=sha256_payload(stdout or ""))
    payload: Any = None
    if out_file.exists():
        rec["output_sha256"] = file_sha256(out_file)
        try:
            payload = json.loads(out_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            payload = None
            rec["reason"] = f"output unreadable: {type(exc).__name__}"
    else:
        rec["reason"] = "verifier produced no output file"

    verdict_spec = spec.get("verdict") or {}
    if payload is not None and verdict_spec:
        rec["verdict"] = _dig(payload, str(verdict_spec.get("path") or "verdict"))
        pass_values = [str(v) for v in (verdict_spec.get("pass_values") or [])]
        if rec["verdict"] is not None and pass_values:
            rec["verdict_pass"] = str(rec["verdict"]) in pass_values
    metrics: dict[str, float] = {}
    for mspec in spec.get("metrics") or []:
        if not isinstance(mspec, Mapping):
            continue
        name = str(mspec.get("label") or mspec.get("path") or mspec.get("id") or "metric")
        val = extract_metric(payload, mspec)
        if val is not None:
            metrics[name] = val
    rec["metrics"] = metrics
    declared = [str(m.get("label") or m.get("id") or "") for m in (spec.get("metrics") or [])
                if isinstance(m, Mapping)]
    missing = [n for n in declared if n and n not in metrics]
    if payload is None or missing:
        rec["reason"] = "; ".join(filter(None, [rec["reason"],
                                                f"metrics not parsed: {missing}" if missing else ""]))
        if payload is None:
            rec["status"] = RUN_PARSE_INCOMPLETE if rec["exit_code"] == 0 else RUN_FAILED
        else:
            rec["status"] = RUN_PARSE_INCOMPLETE
    elif rec["exit_code"] != 0 and not rec.get("verdict"):
        rec["status"] = RUN_FAILED
    return rec


def build_debt_registry(
    rows: Sequence[Mapping[str, Any]],
    cfg: SemanticsConfig,
    root: Path | str = ".",
    runner: Runner | None = None,
    run_verifiers: bool = False,
) -> dict[str, Any]:
    """把证据审计 rows 展开成欠账台账（含可选的真跑验证量测）。"""
    root = Path(root).resolve()
    policy = cfg.debt_policy or {}
    out_of_repo = {str(c) for c in (policy.get("out_of_repo_capabilities") or [])}
    lifts = {**LIFT_CONDITION,
             **{str(k): str(v) for k, v in (policy.get("lift_condition_overrides") or {}).items()}}

    runs_spec = [r for r in (cfg.verification_runs or []) if isinstance(r, Mapping)]
    runs: list[dict[str, Any]] = []
    for spec in runs_spec:
        spec = dict(spec)
        spec.setdefault("capability_id", "")
        runs.append(run_verifier(spec, root, runner=runner, enabled=run_verifiers))
    run_by_cap: dict[str, list[dict[str, Any]]] = {}
    for rec in runs:
        run_by_cap.setdefault(rec["capability_id"], []).append(rec)

    records: list[dict[str, Any]] = []
    for row in rows:
        derived = str(row.get("derived_level") or "")
        if derived == "REAL":
            continue
        cid = str(row.get("capability_id") or "")
        reason = str(row.get("reason_code") or "")
        cap_runs = run_by_cap.get(cid, [])
        measured = [r for r in cap_runs if r["status"] == RUN_EXECUTED]
        metrics: dict[str, float] = {}
        for rec in measured:
            metrics.update(rec.get("metrics") or {})
        if cid in out_of_repo:
            status = STATUS_BLOCKED
        elif metrics:
            status = STATUS_MEASURED
        else:
            status = STATUS_OPEN
        records.append({
            "capability_id": cid,
            "domain": row.get("domain"),
            "declared_level": row.get("declared_level"),
            "derived_level": derived,
            "reason_code": reason,
            "priority": PRIORITY_BY_REASON.get(reason, "P2"),
            "claim_above_derived": bool(row.get("claim_rank_gt_derived")),
            "lift_condition": lifts.get(reason, "补独立数值验证工件（误差 ≤3% 才上 REAL）"),
            "status": status,
            "status_note": {
                STATUS_BLOCKED: "实现位于扫描边界外的仓（见 policy.out_of_repo_capabilities），"
                                "本仓无法量测；需跨仓证据",
                STATUS_MEASURED: "已完成独立验证量测（见 runs），缺口有数字",
                STATUS_OPEN: "尚无独立验证量测：需按 lift_condition 补验证脚本或补实现",
            }[status],
            "measurements": metrics,
            "verifier_runs": [r["label"] for r in cap_runs if r["status"] == RUN_EXECUTED],
            "accuracy_flag": row.get("accuracy_flag"),
        })
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    records.sort(key=lambda r: (order.get(r["priority"], 9), r["capability_id"]))

    by_priority: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for r in records:
        by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_reason[r["reason_code"]] = by_reason.get(r["reason_code"], 0) + 1
    measured_ratio = (len([r for r in records if r["status"] == STATUS_MEASURED])
                      / len(records)) if records else 0.0
    return {
        "schema": "capability_verification_debt/v1",
        "records": records,
        "summary": {
            "debt_entries": len(records),
            "by_priority": dict(sorted(by_priority.items())),
            "by_status": dict(sorted(by_status.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "measured_ratio": round(measured_ratio, 4),
            "out_of_repo_count": len(out_of_repo & {str(r["capability_id"]) for r in records}),
            "verifiers_declared": len(runs_spec),
            "verifiers_executed": len([r for r in runs if r["status"] == RUN_EXECUTED]),
            "verifiers_failed": len([r for r in runs
                                     if r["status"] in (RUN_FAILED, RUN_PARSE_INCOMPLETE)]),
            "verifier_execution_enabled": bool(run_verifiers),
        },
        "runs": runs,
        "policy": {
            "out_of_repo_capabilities": sorted(out_of_repo),
            "lift_conditions": lifts,
            "note": "债务记录不参与等级判定；只解释缺口与整改动作（避免为关账而改判）",
        },
    }


__all__ = [
    "LIFT_CONDITION",
    "PRIORITY_BY_REASON",
    "STATUS_OPEN",
    "STATUS_MEASURED",
    "STATUS_BLOCKED",
    "RUN_EXECUTED",
    "RUN_MISSING",
    "RUN_FAILED",
    "RUN_PARSE_INCOMPLETE",
    "RUN_DISABLED",
    "extract_metric",
    "run_verifier",
    "build_debt_registry",
]