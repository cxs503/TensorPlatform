"""dev_acceptance 强制前门：没有可复算的 gate.json 就不许机器自动验收。

决策映射（三值，禁止二值化）：

=========  ==========================  =========================================
grade      decision                    动作
=========  ==========================  =========================================
PASS       allow_auto_approve          机器可自动批（仍可人审）
FAIL       reject                      证据齐但判据未达标 → 拒绝
WITHHELD   withhold                    缺/不符证据 → 不许批，派补证单
=========  ==========================  =========================================

策略（数据注入，``config/gate_contract/acceptance_policy.json``）：

* ``mode``: ``enforce``（默认，缺门即 withhold）/ ``shadow``（只记录不拦）
* ``gate_root``: 门目录根（其下 ``*/gate.json``）
* ``registry``: 权威判据登记表
* ``ledger_db``: 判定台账
* ``registry_sha256``: （可选）权威登记表指纹锚；不符 → WITHHELD(registry_tampered)
* 同一 mission 出现多份门 → WITHHELD(gate_ambiguous)，不允许「谁排在前面谁生效」

零领域语义：门目录/登记表/台账路径全部来自配置；本模块不认识任何域词。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import canonical_json, canonical_sha256, sha256_file, sha256_text
from .ledger import append_verdict, connect
from .verify import FAIL, PASS, WITHHELD, verify_gate

DEFAULT_POLICY = {
    "mode": "enforce",
    "gate_root": "artifacts/gate_contract/plan258",
    "registry": "artifacts/gate_contract/plan258/criteria_registry.json",
    "ledger_db": "artifacts/gate_contract/ledger.db",
}

DECISION_BY_GRADE = {PASS: "allow_auto_approve", FAIL: "reject", WITHHELD: "withhold"}
APPROVER_VERDICT_BY_GRADE = {PASS: "approve", FAIL: "reject", WITHHELD: "escalate"}


def load_policy(repo_root: str | Path | None = None,
                path: str | Path | None = None) -> dict:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    policy = dict(DEFAULT_POLICY)
    candidate = Path(path) if path is not None else root / "config" / "gate_contract" / "acceptance_policy.json"
    if candidate.is_file():
        try:
            policy.update(json.loads(candidate.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            policy["mode"] = "shadow"
            policy["policy_error"] = "unparsable"
    policy.setdefault("_repo_root", str(root))
    return policy


def _resolve(policy: dict, key: str) -> Path:
    p = Path(policy.get(key) or "")
    return p if p.is_absolute() else Path(policy.get("_repo_root", ".")) / p


def gates_for_mission(mission_id: int, gate_root: str | Path) -> list[Path]:
    """按 ``mission_id`` 找**全部**声明该 mission 的 gate.json（判定对象必须唯一）。

    只取第一份是不安全的：往门目录里多放一份「更宽松」的同 mission 门，
    就能改变判定（谁排在前面谁生效）。所以调用方必须能看到重数并据以 withhold。
    """
    root = Path(gate_root)
    out: list[Path] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*/gate.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        mid = doc.get("mission_id") if isinstance(doc, dict) else None
        if isinstance(mid, int) and not isinstance(mid, bool) and mid == int(mission_id):
            out.append(path)
    return out


def gate_for_mission(mission_id: int, gate_root: str | Path) -> Path | None:
    """按 ``mission_id`` 在门目录里找对应的 gate.json（文件名不参与约定）。"""
    root = Path(gate_root)
    if not root.is_dir():
        return None
    for path in sorted(root.glob("*/gate.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mid = doc.get("mission_id")
        if isinstance(mid, int) and not isinstance(mid, bool) and mid == int(mission_id):
            return path
    return None


def build_supplement_task(result: dict, mission_id: int | None = None) -> dict:
    """补证单：缺什么、缺哪条判据、要哪个工件/单位、判定的问题码。"""
    codes = sorted({i["code"] for i in result.get("issues", [])})
    mid = mission_id if mission_id is not None else result.get("mission_id")
    dedupe = sha256_text(canonical_json([mid, result.get("gate_id"),
                                         result.get("verdict_digest"), codes]))[:16]
    return {
        "kind": "supplement_evidence",
        "dedupe_key": dedupe,
        "mission_id": mid,
        "gate_id": result.get("gate_id"),
        "grade": result.get("grade"),
        "reason_codes": codes,
        "required": [
            {"criterion_id": r["id"], "code": r.get("code"),
             "artifact": r.get("artifact"), "unit": r.get("unit"),
             "threshold": r.get("threshold"), "op": r.get("op")}
            for r in result.get("criteria", []) if r.get("status") == "withheld"
        ],
        "blocked_criteria": [r["id"] for r in result.get("criteria", [])
                             if r.get("status") == "fail"],
        "note": "gate.json 缺失/不符：补可复算工件+sha256+单位后重跑 gate_verify",
    }


def evaluate_acceptance(mission_id: int, *, gate_path: str | Path | None = None,
                        registry_path: str | Path | None = None,
                        evidence_root: str | Path | None = None,
                        ledger_db: str | Path | None = None,
                        run_cmd: str | None = None,
                        registry_sha256: str | None = None,
                        pre_issues: list[dict] | None = None) -> dict:
    """三值判定。``registry_sha256`` 存在时，权威登记表必须与锚定指纹一致。

    锚定指纹是对「谁能改判据」的控制：登记表本身如果可被生产者随手改，
    那么「阈值不许改宽」只是一句口号。指纹不符 → WITHHELD（registry_tampered）。
    """
    issues = list(pre_issues or [])
    registry = None
    if registry_path is not None and Path(registry_path).is_file():
        registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
        if registry_sha256:
            actual = sha256_file(Path(registry_path))
            if actual != str(registry_sha256):
                issues.append({"code": "registry_tampered",
                               "detail": f"{actual[:12]}!={str(registry_sha256)[:12]}"})
                registry = None
    result = verify_gate(gate_path, registry=registry, evidence_root=evidence_root,
                         pre_issues=issues, require_registry=True)
    grade = result["grade"]
    decision = DECISION_BY_GRADE[grade]
    supplement = build_supplement_task(result, mission_id) if decision != "allow_auto_approve" else None
    if ledger_db is not None:
        con = connect(ledger_db)
        append_verdict(con, result, run_cmd=run_cmd)
        con.close()
    return {"decision": decision, "grade": grade,
            "verdict_digest": result["verdict_digest"],
            "supplement_task": supplement, "result": result}


def precheck_mission(mission_id: int, *, policy: dict | None = None) -> dict | None:
    """给审批器（自动批）用的前门。策略关闭 → 返回 None（不改旧行为）。

    返回 ``{decision, grade, approver_verdict, note, supplement_task}``。
    """
    policy = policy or load_policy()
    if policy.get("mode") not in ("enforce", "shadow"):
        return None
    gate_root = _resolve(policy, "gate_root")
    candidates = gates_for_mission(mission_id, gate_root)
    pre: list[dict] = []
    if len(candidates) > 1:
        pre.append({"code": "gate_ambiguous",
                    "detail": ",".join(p.parent.name for p in candidates)})
    gate_path = candidates[0] if candidates else None
    out = evaluate_acceptance(
        mission_id,
        gate_path=gate_path,
        registry_path=_resolve(policy, "registry"),
        ledger_db=_resolve(policy, "ledger_db") if policy.get("ledger_db") else None,
        run_cmd=(f"gate_verify mission={mission_id}"),
        registry_sha256=policy.get("registry_sha256"),
        pre_issues=pre,
    )
    decision = out["decision"]
    if policy.get("mode") == "shadow":
        decision = "allow_auto_approve"
    note = (f"gate_contract: grade={out['grade']} decision={out['decision']}"
            f" digest={out['verdict_digest'][:16]}"
            + ("" if out["supplement_task"] is None
               else f" supplement={out['supplement_task']['dedupe_key']}"))
    return {"decision": decision, "grade": out["grade"],
            "approver_verdict": APPROVER_VERDICT_BY_GRADE[out["grade"]],
            "note": note, "supplement_task": out["supplement_task"],
            "result": out["result"], "mode": policy.get("mode")}


def assert_auto_approve_allowed(mission_id: int, *, policy: dict | None = None) -> dict:
    """硬前门：非 PASS 直接抛 PermissionError（供调用方当阻塞用）。"""
    res = precheck_mission(mission_id, policy=policy)
    if res is None:
        raise PermissionError("gate_contract: policy disabled")
    if res["decision"] != "allow_auto_approve":
        raise PermissionError(f"gate_contract: {res['grade']} -> {res['decision']}: {res['note']}")
    return res
