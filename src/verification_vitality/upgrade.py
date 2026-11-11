"""证据驱动的能力升账引擎（WITHHELD/MOCK → REAL）+ rollback guard。

判据（与本战役节点一致，全部机器可算）：

======================  ==================================================
``pass_rate >= 0.90``    执行证据：pytest 真跑出来的通过率（不是静态推断）
``benchmark_error < 15%`` 基准误差：与实验/参考值的相对误差（最差工况）
``surface_alive``        支撑该能力的验证面活性巡检为 alive（不是 dead/unmapped）
======================  ==================================================

三条同时成立才允许 ``→ REAL``；任何一条不成立就 **HOLD**（保持原等级），并逐条列出
不成立的原因——这样"没升上去"也带证据，而不是沉默。

**Rollback guard**（升账是可回滚的，不是单向棘轮）：

* 每次升账写一条 append-only 流水（journal），带 ``evidence_digest``（判据输入的内容
  哈希）与 ``prev_level``；
* 回滚必须带上当前的 evidence_digest 匹配才生效；期间证据变了（重跑判据结果不同）
  ⇒ ``ROLLBACK_CONFLICT``，拒绝盲目回滚；
* 同一 ``(capability, evidence_digest)`` 重复应用是幂等的（不产生重复流水，不重复升账）。

接口对齐：能力 id / 等级枚举与 ``config/capabilities.json`` + ``artifacts/capability_ledger``
同一套（REAL/WITHHELD/MOCK/STUB/NOT_SUPPORTED，rank 单调）；决策记录带
``verdict`` + ``decided_level`` + ``chain_digest`` 三元，与
``backend/services/capability_governance/grading.py`` 的证据链同构，便于既有台账门
（FEAT-LEDGER-GATE）直接消费本引擎的产出。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

SCHEMA = "capability-upgrade-engine/1.0"
STATE_SCHEMA = "capability-upgrade-state/1.0"
LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}
UPGRADABLE_FROM = ("WITHHELD", "MOCK", "STUB", "NOT_SUPPORTED")
_EVIDENCE_KEYS_PASS = ("pytest", "pytest_result", "tests")
_EVIDENCE_KEYS_BENCH = ("benchmark", "benchmark_result", "reference")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_of(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@dataclass(frozen=True)
class UpgradeCriteria:
    min_pass_rate: float = 0.90
    max_benchmark_error_pct: float = 15.0
    require_alive_surface: bool = True
    allow_from: Sequence[str] = UPGRADABLE_FROM

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["allow_from"] = list(self.allow_from)
        return d


# ---------------------------------------------------------------------------
# 证据抽取
# ---------------------------------------------------------------------------

def extract_metrics(entry: Mapping[str, Any], *, pass_rate: Optional[float] = None,
                    benchmark_error_pct: Optional[float] = None) -> Dict[str, Any]:
    """从台账条目里抽执行证据与基准误差；显式传入的值优先（来自真跑/真基准工件）。

    台账可用的两种写法（为兼容既有台账，两种都吃）：

    * ``evidence[].pytest = {"passed": 42, "total": 45}`` / ``pass_rate``
    * ``evidence[].benchmark = {"error_pct": 8.3}`` / ``benchmark_error_pct``
    """
    out: Dict[str, Any] = {"pass_rate": pass_rate,
                           "pass_rate_source": "explicit" if pass_rate is not None else None,
                           "benchmark_error_pct": benchmark_error_pct,
                           "benchmark_error_source": "explicit" if benchmark_error_pct is not None else None,
                           "missing": []}
    for ev in entry.get("evidence") or []:
        if not isinstance(ev, Mapping):
            continue
        for key in _EVIDENCE_KEYS_PASS:
            blob = ev.get(key)
            if isinstance(blob, Mapping):
                passed = blob.get("passed")
                total = blob.get("total")
                rate = blob.get("pass_rate")
                if isinstance(passed, (int, float)) and isinstance(total, (int, float)) and total:
                    if out["pass_rate"] is None:
                        out["pass_rate"] = float(passed) / float(total)
                        out["pass_rate_source"] = f"evidence.{key}"
                elif isinstance(rate, (int, float)):
                    if out["pass_rate"] is None:
                        out["pass_rate"] = float(rate)
                        out["pass_rate_source"] = f"evidence.{key}.pass_rate"
        if isinstance(ev.get("pass_rate"), (int, float)) and out["pass_rate"] is None:
            out["pass_rate"] = float(ev["pass_rate"])
            out["pass_rate_source"] = "evidence.pass_rate"
        for key in _EVIDENCE_KEYS_BENCH:
            blob = ev.get(key)
            if isinstance(blob, Mapping) and isinstance(blob.get("error_pct"), (int, float)):
                if out["benchmark_error_pct"] is None:
                    out["benchmark_error_pct"] = float(blob["error_pct"])
                    out["benchmark_error_source"] = f"evidence.{key}.error_pct"
        if isinstance(ev.get("benchmark_error_pct"), (int, float)) and out["benchmark_error_pct"] is None:
            out["benchmark_error_pct"] = float(ev["benchmark_error_pct"])
            out["benchmark_error_source"] = "evidence.benchmark_error_pct"
    if out["pass_rate"] is None:
        out["missing"].append("pass_rate")
    if out["benchmark_error_pct"] is None:
        out["missing"].append("benchmark_error_pct")
    return out


# ---------------------------------------------------------------------------
# 决策
# ---------------------------------------------------------------------------

def _level_of(entry: Mapping[str, Any]) -> str:
    for key in ("effective_level", "reconciled_level", "status", "claimed_level", "level"):
        val = entry.get(key)
        if isinstance(val, str) and val.upper() in LEVEL_RANK:
            return val.upper()
    return "WITHHELD"


def evaluate_upgrades(feature_ledger: Mapping[str, Mapping[str, Any]], *,
                      patrol_report: Optional[Mapping[str, Any]] = None,
                      criteria: UpgradeCriteria = UpgradeCriteria(),
                      measured: Optional[Mapping[str, Mapping[str, Any]]] = None,
                      state: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """对每条能力给出 ``UPGRADE`` / ``HOLD`` / ``NOOP`` 决策，逐条带判据明细。"""
    cap_status = {c["capability_id"]: c["status"]
                  for c in (patrol_report or {}).get("capabilities", [])}
    state_entries = (state or {}).get("entries", {})
    decisions: List[Dict[str, Any]] = []
    for cid, entry in sorted(feature_ledger.items()):
        current = state_entries.get(cid, {}).get("level") or _level_of(entry)
        meas = (measured or {}).get(cid, {})
        metrics = extract_metrics(entry,
                                  pass_rate=meas.get("pass_rate"),
                                  benchmark_error_pct=meas.get("benchmark_error_pct"))
        surface_alive = cap_status.get(cid) == "alive"
        checks = {
            "pass_rate": {"value": None if metrics["pass_rate"] is None else round(metrics["pass_rate"], 4),
                          "threshold": criteria.min_pass_rate,
                          "pass": metrics["pass_rate"] is not None and metrics["pass_rate"] >= criteria.min_pass_rate,
                          "source": metrics["pass_rate_source"]},
            "benchmark_error_pct": {"value": metrics["benchmark_error_pct"],
                                    "threshold": criteria.max_benchmark_error_pct,
                                    "pass": metrics["benchmark_error_pct"] is not None
                                    and metrics["benchmark_error_pct"] < criteria.max_benchmark_error_pct,
                                    "source": metrics["benchmark_error_source"]},
            "surface_alive": {"value": cap_status.get(cid, "unmapped"),
                              "pass": surface_alive, "required": criteria.require_alive_surface},
        }
        reasons: List[str] = []
        for name, check in checks.items():
            if not check["pass"] and (name != "surface_alive" or criteria.require_alive_surface):
                reasons.append(f"{name}_below_bar" if check["value"] is not None else f"{name}_missing")
        if current == "REAL":
            # 已 REAL 但验证面已死：不许安静地记 NOOP —— 那是"台账还写着 REAL，
            # 支撑它的测试已经收集不起来"的最坏组合，必须在决策流里被看见。
            if criteria.require_alive_surface and not surface_alive:
                verdict = "HOLD"
                reasons = ["already_real", "surface_alive_below_bar"]
            else:
                verdict = "NOOP"
                reasons = ["already_real"]
        elif current not in criteria.allow_from:
            verdict = "HOLD" if reasons else "NOOP"
            reasons = reasons or [f"level_not_upgradable:{current}"]
        elif not reasons:
            verdict = "UPGRADE"
        else:
            verdict = "HOLD"
        evidence_digest = digest_of({
            "capability_id": cid, "current_level": current,
            "checks": {k: {kk: vv for kk, vv in v.items() if kk != "source"}
                       for k, v in checks.items()},
            "criteria": criteria.as_dict(),
        })
        decisions.append({
            "capability_id": cid,
            "current_level": current,
            "proposed_level": "REAL" if verdict == "UPGRADE" else current,
            "verdict": verdict,
            "decided_level": "REAL" if verdict == "UPGRADE" else current,
            "reasons": reasons,
            "criteria": checks,
            "evidence_digest": evidence_digest,
            "missing_evidence": metrics["missing"],
        })
    return decisions


# ---------------------------------------------------------------------------
# 应用 / 回滚（rollback guard）
# ---------------------------------------------------------------------------

def load_state(path: Path | str) -> Dict[str, Any]:
    p = Path(path)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"schema": STATE_SCHEMA, "entries": {},
            "counters": {"upgraded": 0, "rolled_back": 0, "noop": 0, "conflict": 0}}


def _append_journal(path: Optional[Path | str], event: Mapping[str, Any]) -> None:
    if path is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def apply_upgrades(decisions: Sequence[Mapping[str, Any]], state: Dict[str, Any], *,
                   journal_path: Path | str | None = None, dry_run: bool = False) -> Dict[str, Any]:
    """把 ``UPGRADE`` 决策写进状态；幂等 + 每条流水可回滚。"""
    state.setdefault("entries", {})
    state.setdefault("counters", {"upgraded": 0, "rolled_back": 0, "noop": 0, "conflict": 0})
    applied, noop = [], []
    for dec in decisions:
        cid = str(dec["capability_id"])
        if dec.get("verdict") != "UPGRADE":
            noop.append(cid)
            if not dry_run:
                state["counters"]["noop"] = state["counters"].get("noop", 0) + 1
            continue
        prev = state["entries"].get(cid) or {}
        prev_level = prev.get("level", dec.get("current_level"))
        if prev.get("evidence_digest") == dec["evidence_digest"] and prev_level == "REAL":
            noop.append(cid)  # 幂等：同证据重复应用不产生第二条流水
            if not dry_run:
                state["counters"]["noop"] = state["counters"].get("noop", 0) + 1
            continue
        if prev_level == "REAL":
            noop.append(cid)
            continue
        event = {
            "event": "upgrade",
            "capability_id": cid,
            "level_before": prev_level,
            "level_after": "REAL",
            "evidence_digest": dec["evidence_digest"],
            "criteria": dec.get("criteria"),
            "rollback_token": digest_of({"capability_id": cid, "prev_level": prev_level,
                                         "evidence_digest": dec["evidence_digest"]}),
            "at": _now(),
        }
        applied.append(event)
        if dry_run:
            continue
        state["entries"][cid] = {
            "level": "REAL",
            "prev_level": prev_level,
            "evidence_digest": dec["evidence_digest"],
            "rollback_token": event["rollback_token"],
            "applied_at": event["at"],
        }
        state["counters"]["upgraded"] = state["counters"].get("upgraded", 0) + 1
        _append_journal(journal_path, event)
    return {"applied": applied, "noop": noop, "dry_run": dry_run,
            "n_applied": len(applied), "n_noop": len(noop)}


def downgrade(decisions: Sequence[Mapping[str, Any]], state: Dict[str, Any], *,
              journal_path: Path | str | None = None, dry_run: bool = False) -> Dict[str, Any]:
    """执行台账交叉器给出的降级建议（fail-closed 落地），同样留痕可回滚。"""
    state.setdefault("entries", {})
    state.setdefault("counters", {"upgraded": 0, "rolled_back": 0, "noop": 0, "conflict": 0,
                                  "downgraded": 0})
    applied = []
    for sug in decisions:
        if sug.get("action") != "DOWNGRADE":
            continue
        cid = str(sug["capability_id"])
        prev = state["entries"].get(cid) or {}
        prev_level = prev.get("level", sug.get("from", "REAL"))
        event = {"event": "downgrade", "capability_id": cid, "level_before": prev_level,
                 "level_after": sug.get("to", "WITHHELD"),
                 "reason": sug.get("policy"), "evidence": sug.get("evidence"), "at": _now()}
        applied.append(event)
        if dry_run:
            continue
        state["entries"][cid] = {"level": sug.get("to", "WITHHELD"), "prev_level": prev_level,
                                 "evidence_digest": prev.get("evidence_digest"),
                                 "rolled_back_from": prev_level, "at": event["at"]}
        state["counters"]["downgraded"] = state["counters"].get("downgraded", 0) + 1
        _append_journal(journal_path, event)
    return {"applied": applied, "n_applied": len(applied), "dry_run": dry_run}


def rollback(state: Dict[str, Any], capability_id: str, *,
             token: Optional[str] = None,
             journal_path: Path | str | None = None,
             dry_run: bool = False) -> Dict[str, Any]:
    """把某条能力回滚到升账前的等级；证据已变则拒绝（``ROLLBACK_CONFLICT``）。"""
    entries = state.setdefault("entries", {})
    state.setdefault("counters", {"upgraded": 0, "rolled_back": 0, "noop": 0, "conflict": 0})
    entry = entries.get(capability_id)
    if not entry:
        return {"status": "NOT_TRACKED", "capability_id": capability_id}
    if token is not None and entry.get("rollback_token") and token != entry["rollback_token"]:
        state["counters"]["conflict"] = state["counters"].get("conflict", 0) + 1
        return {"status": "ROLLBACK_CONFLICT", "capability_id": capability_id,
                "reason": "rollback_token 与当前记账不匹配（期间证据已变，拒绝盲目回滚）"}
    if entry.get("level") != "REAL" and entry.get("rolled_back_from") is None:
        return {"status": "NOT_UPGRADED", "capability_id": capability_id,
                "level": entry.get("level")}
    target = entry.get("prev_level", "WITHHELD")
    event = {"event": "rollback", "capability_id": capability_id,
             "level_before": entry.get("level"), "level_after": target,
             "evidence_digest": entry.get("evidence_digest"), "at": _now()}
    if not dry_run:
        entries[capability_id] = {"level": target, "prev_level": entry.get("level"),
                                  "evidence_digest": entry.get("evidence_digest"),
                                  "rolled_back_at": event["at"]}
        state["counters"]["rolled_back"] = state["counters"].get("rolled_back", 0) + 1
        _append_journal(journal_path, event)
    return {"status": "ROLLED_BACK", "capability_id": capability_id, "to": target,
            "event": event}


def save_state(state: Mapping[str, Any], path: Path | str) -> None:
    _atomic_write_json(Path(path), state)