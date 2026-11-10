"""sealed_evidence.recovery_gate —— worktree_recyclable 候选四关拦截。

四关：
  1. 契约存在性：sealed_evidence.json 是否存在
  2. sha256 匹配：契约中记录的 sha256 与实际文件 sha256 是否一致
  3. 重放可行：replay.cmd 是否可执行（命令存在、超时内完成）
  4. 缺件显式登记：缺失的 artifact 是否已登记到债务账本

失败置 blocked_* 并写事件 evidence_seal_gate{verdict, reasons[]}。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .schema import validate_sealed_evidence, _sha256_file


class GateVerdict(str, Enum):
    PASS = "pass"
    BLOCKED_NO_CONTRACT = "blocked_no_contract"
    BLOCKED_SHA256_MISMATCH = "blocked_sha256_mismatch"
    BLOCKED_REPLAY_FAILED = "blocked_replay_failed"
    BLOCKED_MISSING_ARTIFACTS = "blocked_missing_artifacts"


@dataclass
class GateResult:
    verdict: GateVerdict
    reasons: list[str] = field(default_factory=list)
    missing_artifacts: list[dict[str, Any]] = field(default_factory=list)
    sha256_mismatches: list[dict[str, Any]] = field(default_factory=list)
    replay_result: dict[str, Any] | None = None
    event: dict[str, Any] | None = None


def _check_contract_exists(workdir: str) -> tuple[bool, str | None, dict | None]:
    """关1：检查 sealed_evidence.json 是否存在且有效。"""
    contract_path = os.path.join(workdir, "sealed_evidence.json")
    if not os.path.isfile(contract_path):
        # 也检查 artifacts/ 目录
        alt_path = os.path.join(workdir, "artifacts", "sealed_evidence.json")
        if os.path.isfile(alt_path):
            contract_path = alt_path
        else:
            return False, None, None

    try:
        with open(contract_path, "r", encoding="utf-8") as f:
            contract = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return False, f"contract parse error: {e}", None

    errs = validate_sealed_evidence(contract)
    if errs:
        return False, f"contract validation failed: {errs}", contract

    return True, None, contract


def _check_sha256_match(workdir: str, contract: dict) -> tuple[bool, list[dict], list[dict]]:
    """关2：检查契约中记录的 sha256 与实际文件是否一致。"""
    mismatches: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for c in contract.get("criteria", []):
        for art in c.get("artifacts", []):
            path = art["path"]
            expected_sha = art["sha256"]
            full_path = os.path.join(workdir, path) if not os.path.isabs(path) else path

            if not os.path.isfile(full_path):
                missing.append({
                    "path": path,
                    "expected_sha256": expected_sha,
                    "reason": "file_missing",
                })
                continue

            actual_sha = _sha256_file(full_path)
            if actual_sha != expected_sha:
                mismatches.append({
                    "path": path,
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                    "reason": "sha256_mismatch",
                })

    return len(mismatches) == 0 and len(missing) == 0, mismatches, missing


def _check_replay_feasible(
    workdir: str,
    contract: dict,
    timeout_s: int = 300,
) -> tuple[bool, dict[str, Any]]:
    """关3：检查 replay.cmd 是否可执行。

    在沙箱中执行（禁网、禁写库），超时 300s。
    """
    replay = contract.get("replay", {})
    cmd = replay.get("cmd", "")
    cwd = replay.get("cwd", ".")
    full_cwd = os.path.join(workdir, cwd) if not os.path.isabs(cwd) else cwd

    if not cmd:
        return False, {"reason": "no_replay_cmd"}

    # 检查命令是否存在
    cmd_parts = cmd.split()
    cmd_name = cmd_parts[0]
    if not shutil.which(cmd_name):
        return False, {"reason": "command_not_found", "command": cmd_name}

    # 执行重放（带超时）
    started = time.time()
    full_env = dict(os.environ)
    full_env["PYTHONDONTWRITEBYTECODE"] = "1"
    # 禁网：设置空代理
    full_env["HTTP_PROXY"] = ""
    full_env["HTTPS_PROXY"] = ""
    full_env["NO_PROXY"] = "*"

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=full_cwd,
            capture_output=True,
            text=True,
            timeout=min(timeout_s, replay.get("timeout_s", 300)),
            env=full_env,
        )
        duration = round(time.time() - started, 2)
        result = {
            "exit_code": proc.returncode,
            "timed_out": False,
            "duration_sec": duration,
            "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
            "stderr_tail": proc.stderr[-2000:] if proc.stderr else "",
        }
        return proc.returncode == 0, result
    except subprocess.TimeoutExpired:
        duration = round(time.time() - started, 2)
        return False, {
            "exit_code": None,
            "timed_out": True,
            "duration_sec": duration,
            "reason": "replay_timeout",
        }
    except Exception as e:
        return False, {"reason": f"replay_error: {e}"}


def _register_missing_artifacts(
    workdir: str,
    missing: list[dict[str, Any]],
    mission_id: str,
    debt_ledger_path: str | None = None,
) -> list[dict[str, Any]]:
    """关4：把缺失的 artifact 登记到债务账本。"""
    if debt_ledger_path is None:
        debt_ledger_path = os.path.join(workdir, "artifacts", "debt_ledger.json")

    # 加载现有账本
    ledger: list[dict[str, Any]] = []
    if os.path.isfile(debt_ledger_path):
        try:
            with open(debt_ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        except (OSError, json.JSONDecodeError):
            ledger = []

    # 添加缺失项
    for m in missing:
        entry = {
            "mission_id": mission_id,
            "artifact_path": m["path"],
            "expected_sha256": m.get("expected_sha256", ""),
            "reason": m.get("reason", "missing"),
            "registered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # 去重
        if not any(
            e.get("artifact_path") == entry["artifact_path"]
            and e.get("mission_id") == entry["mission_id"]
            for e in ledger
        ):
            ledger.append(entry)

    # 写回
    os.makedirs(os.path.dirname(debt_ledger_path), exist_ok=True)
    with open(debt_ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2, sort_keys=True)

    return ledger


def run_recovery_gate(
    workdir: str,
    mission_id: str,
    replay_timeout_s: int = 300,
    debt_ledger_path: str | None = None,
    skip_replay: bool = False,
) -> GateResult:
    """运行四关回收闸。

    Args:
        workdir: 工作目录
        mission_id: 任务标识
        replay_timeout_s: 重放超时
        debt_ledger_path: 债务账本路径（可选）
        skip_replay: 跳过重放检查（用于快速验证）

    Returns:
        GateResult
    """
    result = GateResult(verdict=GateVerdict.PASS)

    # 关1：契约存在性
    exists, err, contract = _check_contract_exists(workdir)
    if not exists:
        result.verdict = GateVerdict.BLOCKED_NO_CONTRACT
        result.reasons.append(err or "contract not found")
        result.event = _build_event(result.verdict, result.reasons, mission_id)
        return result

    # 关2：sha256 匹配
    sha_ok, mismatches, missing = _check_sha256_match(workdir, contract)
    result.sha256_mismatches = mismatches
    result.missing_artifacts = missing

    if mismatches:
        result.verdict = GateVerdict.BLOCKED_SHA256_MISMATCH
        result.reasons.extend(
            f"sha256 mismatch: {m['path']}" for m in mismatches
        )

    if missing:
        if result.verdict == GateVerdict.PASS:
            result.verdict = GateVerdict.BLOCKED_MISSING_ARTIFACTS
        result.reasons.extend(
            f"missing artifact: {m['path']}" for m in missing
        )
        # 登记缺件
        _register_missing_artifacts(workdir, missing, mission_id, debt_ledger_path)

    # 关3：重放可行
    if not skip_replay and result.verdict == GateVerdict.PASS:
        replay_ok, replay_result = _check_replay_feasible(workdir, contract, replay_timeout_s)
        result.replay_result = replay_result
        if not replay_ok:
            result.verdict = GateVerdict.BLOCKED_REPLAY_FAILED
            reason = replay_result.get("reason", "replay failed")
            result.reasons.append(f"replay failed: {reason}")

    # 构建事件
    result.event = _build_event(result.verdict, result.reasons, mission_id)
    return result


def _build_event(
    verdict: GateVerdict,
    reasons: list[str],
    mission_id: str,
) -> dict[str, Any]:
    """构建 evidence_seal_gate 事件。"""
    return {
        "event_type": "evidence_seal_gate",
        "version": "1.0",
        "mission_id": mission_id,
        "verdict": verdict.value,
        "reasons": reasons,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
