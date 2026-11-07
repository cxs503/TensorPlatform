#!/usr/bin/env python3
"""部署门 + 回滚演练（IN28-N06 判据 (d)）。

判据原文：**回滚演练 PASS（坏部署被拦、回滚 < 5 min、状态逐字节一致）**。
三项都能机器裁，本模块把它们分别变成布尔量：

| 判据 | 机器定义 |
|---|---|
| 坏部署被拦 | 三种坏候选（schema 错 / 文件被篡改 / 版本不单调）必须**全部**被 ``DeployGate`` 拒绝，且拒绝后状态摘要不变 |
| 回滚 < 5 min | ``rollback_seconds < 300``（用 ``time.monotonic``，含快照恢复与校验） |
| 状态逐字节一致 | 回滚后重算 ``sha256`` 清单，必须与基线清单**逐条相等**（文件集合相同、无多余/缺失） |

负控
----
"逐字节一致"这句判据最容易假阳：如果校验函数永远返回 True，画面上也过得去。
所以演练里额外做一次**篡改负控**：故意改快照里 1 个字节，``verify_identical`` 必须返回 False。
负控不通过 → 整个演练 FAIL（校验器不可信等于没有校验）。

沙箱
----
所有状态、快照、候选部署都在沙箱根目录之内（``sandbox.SandboxGuard``，越界写即拒）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .sandbox import SandboxGuard

SCHEMA = "coordinator-shadow-deploy-drill/1"
STATE_SCHEMA = "coordinator-shadow-deployment/1"
ROLLBACK_BUDGET_SECONDS = 300.0


def _iso(ts: datetime | None = None) -> str:
    return (ts or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root: str) -> dict[str, str]:
    """目录 → {相对路径: sha256}（只收文件，跳过隐藏临时文件）。"""
    out: dict[str, str] = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            if name.startswith("."):
                continue
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root)
            out[rel] = sha256_file(full)
    return dict(sorted(out.items()))


def verify_identical(a: Mapping[str, str], b: Mapping[str, str]) -> bool:
    """逐字节一致 = 文件集合相同 且 每个 sha256 相同。"""
    if set(a) != set(b):
        return False
    return all(a[k] == b[k] for k in a)


@dataclass
class GateVerdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons)}


class DeployGate:
    """部署门：schema / 必需文件 / 哈希 / 版本单调 四类校验。"""

    REQUIRED = ("manifest.json", "shadow_state.json")

    def __init__(self, current_version: int = 1) -> None:
        self.current_version = current_version

    def check(self, state_dir: str) -> GateVerdict:
        reasons: list[str] = []
        for name in self.REQUIRED:
            if not os.path.exists(os.path.join(state_dir, name)):
                reasons.append(f"missing:{name}")
        if reasons:
            return GateVerdict(False, reasons)
        try:
            with open(os.path.join(state_dir, "manifest.json")) as fh:
                doc = json.load(fh)
        except Exception as exc:
            return GateVerdict(False, [f"manifest-unreadable:{type(exc).__name__}"])
        if doc.get("schema") != STATE_SCHEMA:
            reasons.append(f"bad-schema:{doc.get('schema')!r}")
        files = doc.get("files") or {}
        for rel, expected in files.items():
            full = os.path.join(state_dir, rel)
            if not os.path.exists(full):
                reasons.append(f"file-missing:{rel}")
                continue
            actual = sha256_file(full)
            if actual != expected:
                reasons.append(f"hash-mismatch:{rel}")
        try:
            version = int(doc.get("version"))
        except (TypeError, ValueError):
            reasons.append("bad-version")
            version = -1
        if version <= self.current_version:
            reasons.append(f"version-not-monotonic:{version}<= {self.current_version}")
        return GateVerdict(not reasons, reasons)


# ---------------------------------------------------------------------------
# 状态构造
# ---------------------------------------------------------------------------


def _write_state(guard: SandboxGuard, root: str, version: int,
                 payload: Mapping[str, Any], extra_files: Mapping[str, str] | None = None,
                 schema: str = STATE_SCHEMA) -> str:
    os.makedirs(root, exist_ok=True)
    files: dict[str, str] = {}
    body = dict(payload)
    body["version"] = version
    body["deployed_at"] = _iso()
    guard.write_json(os.path.join(root, "shadow_state.json"), body)
    files["shadow_state.json"] = sha256_file(os.path.join(root, "shadow_state.json"))
    for rel, text in (extra_files or {}).items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full) or root, exist_ok=True)
        guard.write_bytes(full, text.encode())
        files[rel] = sha256_file(full)
    guard.write_json(os.path.join(root, "manifest.json"), {
        "schema": schema,
        "deployment_id": f"coordinator-shadow-v{version}",
        "version": version,
        "files": files,
        "created_at": _iso(),
    })
    return root


def _copy_tree(src: str, dst: str, guard: SandboxGuard) -> None:
    guard.resolve(dst)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst)
    for base, _dirs, files in os.walk(src):
        for name in files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, src)
            target = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(full, target)


def rollback_drill(root: str, *, guard: SandboxGuard | None = None,
                   bad_cases: Sequence[str] = ("bad_schema", "tampered", "stale_version"),
                   rollback_budget: float = ROLLBACK_BUDGET_SECONDS) -> dict[str, Any]:
    """完整演练：基线 → 三种坏部署（须被拦）→ 好部署 → 回滚（须逐字节一致）。"""
    guard = guard or SandboxGuard(root)
    guard.resolve(root)
    state = os.path.join(root, "state")
    snap = os.path.join(root, "snapshots", "v1")
    stage = os.path.join(root, "stage")

    # 1. 基线部署 v1
    _write_state(guard, state, 1, {"mode": "live", "interval": 1800, "writer": "v1"},
                 {"policy.json": '{"thresholds": {"oldest_queued_minutes": 240}}\n'})
    baseline = manifest(state)
    _copy_tree(state, snap, guard)
    base_gate = DeployGate(current_version=1)          # 候选必须比基线新（version>1）
    baseline_ok = DeployGate(current_version=0).check(state).to_dict()  # 基线自校验(version 1>0)

    # 2. 三种坏候选
    blocked: list[dict[str, Any]] = []
    for case in bad_cases:
        if os.path.isdir(stage):
            shutil.rmtree(stage)
        if case == "bad_schema":
            _write_state(guard, stage, 2, {"mode": "live", "writer": "v1"},
                         {"policy.json": "{}\n"}, schema="wrong-schema/0")
        elif case == "tampered":
            _write_state(guard, stage, 2, {"mode": "live", "writer": "v1"},
                         {"policy.json": "{}\n"})
            target = os.path.join(stage, "shadow_state.json")
            with open(target, "a") as fh:  # 部署后篡改：manifest 与实体不一致
                fh.write("\n")
        elif case == "stale_version":
            _write_state(guard, stage, 1, {"mode": "live", "writer": "v1"},
                         {"policy.json": "{}\n"})
        else:  # pragma: no cover - 未知用例
            continue
        before = manifest(state)
        verdict = base_gate.check(stage).to_dict()
        blocked.append({"case": case, "verdict": verdict,
                        "state_untouched": verify_identical(before, manifest(state))})

    # 3. 好部署 v2（必须过门）
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    _write_state(guard, stage, 2, {"mode": "live", "interval": 1800, "writer": "v1",
                                   "rollback_drill": True},
                 {"policy.json": '{"thresholds": {"oldest_queued_minutes": 180}}\n'})
    good_verdict = base_gate.check(stage).to_dict()
    applied = False
    if good_verdict["ok"]:
        _copy_tree(stage, state, guard)
        applied = True
    deployed = manifest(state)

    # 4. 回滚到 v1 快照并计时
    t0 = time.monotonic()
    _copy_tree(snap, state, guard)
    restored = manifest(state)
    rollback_seconds = time.monotonic() - t0

    # 5. 篡改负控：校验器必须能发现差异
    neg_dir = os.path.join(root, "negative_control")
    _copy_tree(snap, neg_dir, guard)
    with open(os.path.join(neg_dir, "shadow_state.json"), "ab") as fh:
        fh.write(b" ")
    negative_detected = not verify_identical(manifest(neg_dir), baseline)

    identical = verify_identical(restored, baseline)
    criteria = {
        "baseline_valid": bool(baseline_ok["ok"]),
        "bad_deploys_blocked": all(b["verdict"]["ok"] is False for b in blocked) and bool(blocked),
        "state_untouched_by_bad": all(b["state_untouched"] for b in blocked),
        "good_deploy_accepted": bool(good_verdict["ok"] and applied),
        "state_changed_by_good_deploy": not verify_identical(deployed, baseline),
        "rollback_seconds": round(rollback_seconds, 4),
        "rollback_within_budget": rollback_seconds < rollback_budget,
        "state_identical_after_rollback": identical,
        "negative_control_detects_tamper": negative_detected,
        "files": len(baseline),
    }
    criteria["pass"] = bool(
        criteria["baseline_valid"] and criteria["bad_deploys_blocked"]
        and criteria["state_untouched_by_bad"] and criteria["good_deploy_accepted"]
        and criteria["state_changed_by_good_deploy"] and criteria["rollback_within_budget"]
        and criteria["state_identical_after_rollback"]
        and criteria["negative_control_detects_tamper"])
    return {
        "schema": SCHEMA,
        "generated_at": _iso(),
        "root": os.path.realpath(root),
        "rollback_budget_seconds": rollback_budget,
        "baseline_manifest": baseline,
        "restored_manifest": restored,
        "deployed_manifest": deployed,
        "blocked": blocked,
        "good_deploy_verdict": good_verdict,
        "criteria": criteria,
        "sandbox": guard.summary(),
        "honest_limits": [
            "演练对象是本引擎自己的部署状态目录（state/），不是生产 systemd 单元；"
            "生产回滚仍走运维通道，本单只证明门的判据可机器裁。",
            "时间预算 300 s 是在单机 tmpfs/盘上测的；冷缓存或跨机拷贝会更慢。",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="bad-deploy gate + rollback drill (IN28-N06)")
    ap.add_argument("--root", default="runs/in28_n06/sandbox/drill")
    ap.add_argument("--out", default="artifacts/coordinator_shadow/rollback_drill.json")
    args = ap.parse_args(list(argv) if argv is not None else None)
    report = rollback_drill(args.root)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(report["criteria"], ensure_ascii=False, indent=2))
    print(f"artifact: {os.path.realpath(args.out)}")
    return 0 if report["criteria"]["pass"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())