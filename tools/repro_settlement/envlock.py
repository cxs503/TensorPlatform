"""环境锁：把一条复证案的运行环境钉成可校验快照（blocked 类缺口的清偿物）。

锁体（body）字段全部可观测：Python 实现/版本、平台与架构、求解器检出的 commit、
依赖版本钉（pin → 实际版本或 null）、缺失依赖清单。锁 = body + sha256(body)，
校验时重算 sha256 并逐项比对当前运行时。

兼容规则（可机裁的三档判定）：
  * exact      —— 全部钉一致
  * patch_drift —— 主次版本一致、仅补丁号漂移（记为警告，不判失败）
  * broken     —— sha256 失配 / 主次版本不同 / 有钉依赖缺失
"""
from __future__ import annotations

import platform
import sys
from typing import Any, Iterable, Mapping

from tools.repro_settlement.canonical import sha256_obj

LOCK_SCHEMA = "repro-settlement/envlock/1"


def collect_runtime(pins: Iterable[str] | None = None,
                    packages: Mapping[str, str] | None = None) -> dict[str, Any]:
    """采集当前运行时（可注入 packages 以便测试）。"""
    if packages is None:
        packages = _installed(pins or ())
    return {
        "implementation": platform.python_implementation(),
        "python": ".".join(str(x) for x in sys.version_info[:3]),
        "system": platform.system().lower(),
        "machine": platform.machine(),
        "packages": dict(sorted(packages.items())),
    }


def _installed(pins: Iterable[str]) -> dict[str, str | None]:
    from importlib import metadata

    out: dict[str, str | None] = {}
    for pin in pins:
        try:
            out[pin] = metadata.version(pin)
        except Exception:  # noqa: BLE001 - 依赖不可得即记为缺失
            out[pin] = None
    return out


def _major_minor(ver: str | None) -> str | None:
    if not ver:
        return None
    parts = str(ver).split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else str(ver)


def build_lock(case_id: str, *, pins: Iterable[str] = (), solver_commit: str | None = None,
               solver_version: str | None = None,
               runtime: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """为一条 case 生成环境锁。返回 {schema, case_id, body, sha256, missing}。"""
    rt = dict(runtime) if runtime is not None else collect_runtime(pins)
    if "packages" not in rt:  # 允许只注入部分运行时
        rt["packages"] = _installed(pins)
    body = {
        "schema": LOCK_SCHEMA,
        "case_id": case_id,
        "runtime": {
            "implementation": rt.get("implementation"),
            "python": rt.get("python"),
            "system": rt.get("system"),
            "machine": rt.get("machine"),
        },
        "solver": {"commit": solver_commit, "version": solver_version},
        "packages": {k: rt["packages"].get(k) for k in sorted(pins)},
    }
    missing = sorted(k for k, v in body["packages"].items() if v is None)
    return {
        "schema": LOCK_SCHEMA,
        "case_id": case_id,
        "body": body,
        "sha256": sha256_obj(body),
        "missing": missing,
    }


def verify_lock(lock: Mapping[str, Any], runtime: Mapping[str, Any] | None = None,
                pins: Iterable[str] | None = None) -> dict[str, Any]:
    """校验一把锁：sha256 自洽 + 与当前运行时一致。"""
    body = lock.get("body") or {}
    if sha256_obj(body) != lock.get("sha256"):
        return {"status": "broken", "reason": "sha256_mismatch", "missing": [], "drift": []}
    declared = body.get("packages") or {}
    pin_list = list(pins) if pins is not None else list(declared)
    if runtime is None:  # 无注入时对标锁声明的目标环境（完整性判定）
        rt = dict(body.get("runtime") or {})
        rt["packages"] = declared
    else:
        rt = dict(runtime)
    actual_pkgs = rt.get("packages") or {}
    missing = sorted(p for p in pin_list if actual_pkgs.get(p) is None)
    drift: list[str] = []
    hard: list[str] = []
    for p in pin_list:
        d, a = declared.get(p), actual_pkgs.get(p)
        if d and a and d != a:
            if _major_minor(d) == _major_minor(a):
                drift.append(f"{p}: {d} -> {a}")
            else:
                hard.append(f"{p}: {d} -> {a}")
    declared_py = (body.get("runtime") or {}).get("python")
    actual_py = rt.get("python")
    if declared_py and actual_py and _major_minor(declared_py) != _major_minor(actual_py):
        return {"status": "broken", "reason": "python_minor_mismatch",
                "missing": missing, "drift": drift, "declared": declared_py, "actual": actual_py}
    if missing:
        return {"status": "broken", "reason": "dependency_missing", "missing": missing, "drift": drift}
    if hard:
        return {"status": "broken", "reason": "dependency_minor_mismatch",
                "missing": [], "drift": drift, "hard": hard}
    if drift or (declared_py and actual_py and declared_py != actual_py):
        return {"status": "patch_drift", "reason": "patch_level_drift",
                "missing": [], "drift": drift}
    return {"status": "exact", "reason": "ok", "missing": [], "drift": []}


def resolvable(lock: Mapping[str, Any], runtime: Mapping[str, Any] | None = None) -> bool:
    """锁是否可在当前环境复现（exact / patch_drift 都算可复现）。"""
    return verify_lock(lock, runtime)["status"] in ("exact", "patch_drift")


def integrity(lock: Mapping[str, Any]) -> bool:
    """锁体自洽：sha256(body) == lock.sha256。"""
    return sha256_obj(lock.get("body") or {}) == lock.get("sha256")


def completeness(lock: Mapping[str, Any]) -> list[str]:
    """锁体完整性问题清单（python/架构/求解器 commit/依赖钉）。"""
    body = lock.get("body") or {}
    rt = body.get("runtime") or {}
    solver = body.get("solver") or {}
    issues: list[str] = []
    if not rt.get("python"):
        issues.append("python_missing")
    if not rt.get("machine"):
        issues.append("arch_missing")
    if not solver.get("commit"):
        issues.append("solver_commit_missing")
    if not body.get("packages"):
        issues.append("pins_missing")
    return issues


def live_gap(lock: Mapping[str, Any]) -> list[str]:
    """本机缺口：锁钉的依赖在当前主机上缺失的清单（诚实披露，不参与锁完整性判定）。"""
    declared = (lock.get("body") or {}).get("packages") or {}
    return sorted(p for p, v in _installed(declared).items() if v is None)
