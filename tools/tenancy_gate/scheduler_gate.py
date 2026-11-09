#!/usr/bin/env python3
"""调度器标准门（scheduler gate）：把路径预留 / changed_files 交集 / 三方合并预演
串成一条可机器裁决、可复算、可入台账的流水线，供平台派单器直接调用。

五个阶段
--------
1. :meth:`SchedulerGate.reserve` —— 派单前**路径预留**。同一仓内写面（路径模式）相交
   即冲突；处置策略由 ``config['scheduler']['on_conflict']`` 决定：``queue``（默认，
   FIFO 排队）/ ``refuse``（拒派）/ ``downgrade_readonly``（降级为只读预留）。
2. :meth:`SchedulerGate.drain_queue` —— 冲突释放后按 FIFO 自动放行排队任务（幂等）。
3. :meth:`SchedulerGate.complete` —— 收单时 **changed_files 交集检测**（复用
   :func:`tools.tenancy_gate.intake.conflict_matrix` 的 file/lease/glob 三条 lane）。
4. :meth:`SchedulerGate.merge_dry_run` —— 推送前 **三方合并 dry-run**（``git merge-tree``，
   只读），可自动合并 → ``exit_code=0``；内容冲突 → 1；无法判定 → 2（fail-closed）。
5. :meth:`SchedulerGate.decide` —— 串起上述阶段，输出统一裁决（``exit_code`` +
   ``resolution`` + ``digest``）并追加 ``gate_decision`` 台账事件。

退出码（机器可算判据）
----------------------
``0`` 放行 · ``10`` 排队 · ``11`` 人工仲裁 · ``12`` 回滚 · ``13`` 拒派。
取"最严"者：``13 > 12 > 11 > 10 > 0``。

纪律
----
* **零领域语义**：本模块不出现任何学科/业务词条；路径、窗口、处置全部由配置注入。
* **只读 git**：合并预演只调 ``rev-parse`` / ``merge-base`` / ``diff`` / ``merge-tree``。
* **可复算**：同一输入（含 ``ts``）必得逐字节相同的裁决与 ``digest``；耗时字段不进摘要。
* **fail-closed**：仓不可读、ref 不可解析、merge-tree 失败或超时 → ``unknown`` + 回滚。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .intake import conflict_matrix
from .lease import check_lease
from .ledger import append_events, make_event, record_intake
from .model import (
    LEASE_SCHEMA,
    READ,
    WRITE,
    FinishRecord,
    PathLease,
    TenancyError,
    canonical_json,
    digest,
    normalize_path,
    parse_ts,
    with_digest,
)
from .rehearsal import rehearse_pair

__all__ = [
    "EXIT_PASS",
    "EXIT_QUEUED",
    "EXIT_MANUAL",
    "EXIT_ROLLBACK",
    "EXIT_REFUSED",
    "SCHEDULER_SCHEMA",
    "SchedulerGate",
    "exit_code_of",
]

SCHEDULER_SCHEMA = "tenancy_gate/scheduler/v1"
STATE_SCHEMA = "tenancy_gate/scheduler_state/v1"

EXIT_PASS = 0
EXIT_QUEUED = 10
EXIT_MANUAL = 11
EXIT_ROLLBACK = 12
EXIT_REFUSED = 13

#: 越界/非法预留原因：不排队，直接拒派（排队也永远不会变成合法）
_HARD_REASONS = frozenset({"invalid_glob", "protected_path", "outside_writable_roots"})
#: 退出码从严顺序（数字越大越严）
_SEVERITY = {
    "pass": EXIT_PASS,
    "auto_merge": EXIT_PASS,          # 合并阶段：可自动合并 == 放行
    "queued": EXIT_QUEUED,
    "manual_arbitration": EXIT_MANUAL,
    "rollback": EXIT_ROLLBACK,
    "refused": EXIT_REFUSED,
}


#: 裁决从严次序（数字越大越严；复合裁决取最严者）
_RANK = {"pass": 0, "auto_merge": 0, "queued": 1, "manual_arbitration": 2,
         "rollback": 3, "refused": 4}


def exit_code_of(resolution: str) -> int:
    """裁决名 → 退出码（未知裁决按最严回滚处理，不静默放行）。"""
    return _SEVERITY.get(resolution, EXIT_ROLLBACK)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_globs(patterns) -> list[str]:
    if isinstance(patterns, str):
        patterns = [patterns]
    out: list[str] = []
    for raw in patterns or []:
        text = str(raw).strip()
        if not text:
            continue
        out.append(normalize_path(text))
    if not out:
        raise TenancyError("reservation_without_paths")
    return sorted(set(out))


class SchedulerGate:
    """多租户并发写的调度器标准门（状态文件 + 台账，双写可复算）。"""

    def __init__(self, config: dict | None = None, config_path: str | Path | None = None,
                 state_path: str | Path | None = None, ledger_path: str | Path | None = None,
                 overrides: dict | None = None):
        self.cfg = config or load_config(config_path, overrides)
        sched = self.cfg["scheduler"]
        self.state_path = Path(state_path or sched["state_path"])
        self.ledger_path = Path(ledger_path or self.cfg["ledger"]["path"])
        self.state = self._load_state()

    # ───────────────────────── 状态文件 ─────────────────────────
    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:  # pragma: no cover - 损坏状态
                raise TenancyError(f"bad_state_file:{exc}") from exc
            if str(raw.get("schema")) != STATE_SCHEMA:
                raise TenancyError(f"unsupported_state_schema:{raw.get('schema')}")
            raw.setdefault("reservations", [])
            raw.setdefault("queue", [])
            raw.setdefault("finishes", [])
            return raw
        return {"schema": STATE_SCHEMA, "reservations": [], "queue": [], "finishes": []}

    def _save_state(self) -> None:
        body = {
            "schema": STATE_SCHEMA,
            "reservations": sorted(
                self.state["reservations"],
                key=lambda r: (r["repo"], r["mission_id"], r["path_glob"]),
            ),
            "queue": list(self.state["queue"]),
            "finishes": self.state["finishes"][-int(self.cfg["scheduler"]["max_finishes"]):],
        }
        body["digest"] = digest(body)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(canonical_json(body), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _record(self, kind: str, payload: dict, ts: str) -> dict:
        if not self.cfg["scheduler"]["record_ledger"]:
            return {"appended": 0, "skipped": 0, "path": str(self.ledger_path)}
        return append_events(self.ledger_path, [make_event(kind, payload, ts)])

    # ───────────────────────── 预留（派单前） ─────────────────────────
    def active_reservations(self, now_ts: str | None = None) -> list[dict]:
        """TTL 内仍然有效的预留（过期预留不参与冲突判定，但留在状态里供审计）。"""
        now = parse_ts(now_ts or _now_iso())
        ttl = float(self.cfg["lease"]["ttl_seconds"])
        alive = []
        for rec in self.state["reservations"]:
            try:
                age = (now - parse_ts(rec["ts"])).total_seconds()
            except Exception:
                continue
            if abs(age) <= ttl:
                alive.append(rec)
        return alive

    def _reserve(self, mission_id: str, repo: str, globs: list[str], mode: str,
                 ts: str) -> dict:
        """纯判定（不写状态）：把一条预留与现有有效预留逐模式比对。"""
        now_ts = ts
        peers = [r for r in self.active_reservations(now_ts) if r["mission_id"] != mission_id]
        lease_objs = [PathLease.from_dict(r) for r in peers]
        decisions = []
        conflicts: list[dict] = []
        hard = False
        for glob in globs:
            cand = PathLease(repo=repo, path_glob=glob, mission_id=mission_id, ts=ts, mode=mode)
            dec = check_lease(cand, lease_objs, self.cfg, now_ts=now_ts)
            decisions.append(dec)
            for hit in dec["conflicts"]:
                item = {"path_glob": glob, **hit}
                if item not in conflicts:
                    conflicts.append(item)
            if dec["reason"] in _HARD_REASONS:
                hard = True
        admitted = all(d["admitted"] for d in decisions)
        granted = {d["granted_mode"] for d in decisions}
        return {
            "admitted": admitted,
            "hard": hard,
            "conflicts": sorted(conflicts, key=lambda c: (c["mission_id"], c["path_glob"])),
            "granted_mode": (granted.pop() if len(granted) == 1 else "mixed") if admitted else "none",
            "reasons": sorted({d["reason"] for d in decisions}),
            "lease_digest": digest([d["digest"] for d in decisions]),
            "lease_schema": LEASE_SCHEMA,
        }

    def reserve(self, mission_id: str, repo: str, paths, mode: str = WRITE,
                ts: str | None = None) -> dict:
        """派单前路径预留：冲突则按配置排队 / 拒派 / 降级只读。"""
        mission_id, repo, mode = str(mission_id).strip(), str(repo).strip(), str(mode).strip().lower()
        if not mission_id or not repo:
            raise TenancyError("reservation_missing_mission_or_repo")
        if mode not in (WRITE, READ):
            raise TenancyError(f"bad_lease_mode:{mode}")
        ts = ts or _now_iso()
        parse_ts(ts)
        globs = _normalize_globs(paths)

        # 幂等：先撤掉本任务在同一仓的旧预留（重声明即覆盖）
        self._drop_mission(mission_id, repo)

        verdict = self._reserve(mission_id, repo, globs, mode, ts)
        sched = self.cfg["scheduler"]
        resolution = "pass"
        queue_position = None
        granted_mode = verdict["granted_mode"]

        if verdict["admitted"]:
            self._grant(mission_id, repo, globs, mode, ts)
        elif verdict["hard"] or mode == READ:
            resolution = "refused"
            granted_mode = "none"
        elif sched["on_conflict"] == "downgrade_readonly":
            self._grant(mission_id, repo, globs, READ, ts)
            granted_mode, resolution = READ, "pass"
        elif sched["on_conflict"] == "queue":
            queue = [q for q in self.state["queue"] if q["mission_id"] not in (mission_id,)]
            if len(queue) >= int(sched["queue_limit"]):
                resolution = "refused"
            else:
                queue.append(
                    {
                        "mission_id": mission_id,
                        "repo": repo,
                        "path_globs": globs,
                        "mode": mode,
                        "ts": ts,
                        "requested_at": ts,
                        "attempts": 0,
                        "blocked_by": sorted({c["mission_id"] for c in verdict["conflicts"]}),
                    }
                )
                self.state["queue"] = queue
                queue_position = len(queue)
                resolution = "queued"
        else:  # refuse
            resolution = "refused"
            granted_mode = "none"

        out = with_digest(
            {
                "schema": SCHEDULER_SCHEMA,
                "stage": "reserve",
                "mission_id": mission_id,
                "repo": repo,
                "path_globs": globs,
                "mode": mode,
                "granted_mode": granted_mode,
                "resolution": resolution,
                "exit_code": exit_code_of(resolution),
                "queue_position": queue_position,
                "conflicts": verdict["conflicts"],
                "reasons": verdict["reasons"],
                "policy": sched["on_conflict"],
                "lease_digest": verdict["lease_digest"],
                "ts": ts,
            }
        )
        self._save_state()
        self._record("lease_decision", out, ts)
        return out

    def adopt(self, mission_id: str, new_mission_id: str, repo: str | None = None,
              ts: str | None = None) -> dict:
        """把临时/占位 id 的预留改挂到真实 mission id（派单器拿到 id 后调用）。"""
        old, new = str(mission_id), str(new_mission_id)
        hit = 0
        for rec in self.state["reservations"]:
            if rec["mission_id"] == old and (repo is None or rec["repo"] == repo):
                rec["mission_id"] = new
                hit += 1
        for que in self.state["queue"]:
            if que["mission_id"] == old and (repo is None or que["repo"] == repo):
                que["mission_id"] = new
                hit += 1
        self._save_state()
        ts = ts or _now_iso()
        out = with_digest({"schema": SCHEDULER_SCHEMA, "stage": "adopt", "from": old,
                           "to": new, "repo": repo, "moved": hit, "ts": ts})
        if hit:
            self._record("lease_adopt", out, ts)
        return out

    def release(self, mission_id: str, repo: str | None = None, ts: str | None = None) -> dict:
        """任务终态：释放其预留并移出队列。"""
        dropped = self._drop_mission(str(mission_id), repo)
        self._save_state()
        ts = ts or _now_iso()
        out = with_digest({"schema": SCHEDULER_SCHEMA, "stage": "release",
                           "mission_id": str(mission_id), "repo": repo,
                           "dropped": dropped, "ts": ts})
        if dropped:
            self._record("lease_release", out, ts)
        return out

    def drain_queue(self, ts: str | None = None) -> dict:
        """按 FIFO 重放队列：冲突已释放的任务自动放行（幂等，可重复调用）。"""
        ts = ts or _now_iso()
        parse_ts(ts)
        queue = list(self.state["queue"])
        admitted, still = [], []
        for entry in queue:
            verdict = self._reserve(entry["mission_id"], entry["repo"],
                                    list(entry["path_globs"]), entry["mode"], ts)
            if verdict["admitted"]:
                self._grant(entry["mission_id"], entry["repo"], list(entry["path_globs"]),
                            entry["mode"], entry["ts"])
                admitted.append(
                    {"mission_id": entry["mission_id"], "repo": entry["repo"],
                     "path_globs": list(entry["path_globs"]), "waited_from": entry["requested_at"]}
                )
            else:
                entry["attempts"] = int(entry.get("attempts", 0)) + 1
                entry["blocked_by"] = sorted({c["mission_id"] for c in verdict["conflicts"]})
                still.append(entry)
        self.state["queue"] = still
        self._save_state()
        out = with_digest(
            {
                "schema": SCHEDULER_SCHEMA,
                "stage": "drain_queue",
                "checked": len(queue),
                "admitted": admitted,
                "still_queued": [{"mission_id": e["mission_id"], "repo": e["repo"],
                                  "attempts": e["attempts"], "blocked_by": e["blocked_by"]}
                                 for e in still],
                "ts": ts,
            }
        )
        if admitted:
            self._record("queue_drain", out, ts)
        return out

    def _grant(self, mission_id: str, repo: str, globs, mode: str, ts: str) -> None:
        for glob in globs:
            self.state["reservations"].append(
                {"repo": repo, "path_glob": glob, "mission_id": mission_id, "ts": ts, "mode": mode}
            )

    def _drop_mission(self, mission_id: str, repo: str | None = None) -> int:
        before = len(self.state["reservations"]) + len(self.state["queue"])
        self.state["reservations"] = [
            r for r in self.state["reservations"]
            if not (r["mission_id"] == mission_id and (repo is None or r["repo"] == repo))
        ]
        self.state["queue"] = [
            q for q in self.state["queue"]
            if not (q["mission_id"] == mission_id and (repo is None or q["repo"] == repo))
        ]
        return before - len(self.state["reservations"]) - len(self.state["queue"])

    # ───────────────────────── 收单（changed_files 交集） ─────────────────────────
    def complete(self, mission_id: str, repo: str, changed_files, path_globs=None,
                 ts: str | None = None) -> dict:
        """收单：本次 ``changed_files`` 与同窗口其他任务的交集检测。"""
        ts = ts or _now_iso()
        finish = FinishRecord(
            mission_id=str(mission_id),
            repo=str(repo),
            changed_files=tuple(str(f) for f in (changed_files or [])),
            ts=ts,
            path_globs=tuple(str(g) for g in (path_globs or [])),
        )
        peers = self._peers_for(finish)
        matrix = conflict_matrix(finish, peers, self.cfg, now_ts=ts)
        decision = matrix["decision"]
        resolution = "pass" if decision == "admit" else "refused"
        out = with_digest(
            {
                "schema": SCHEDULER_SCHEMA,
                "stage": "changed_files",
                "mission_id": finish.mission_id,
                "repo": finish.repo,
                "changed_files": list(finish.changed_files),
                "resolution": resolution,
                "exit_code": exit_code_of(resolution),
                "decision": decision,
                "reason": matrix["reason"],
                "conflict_pairs": matrix["conflict_pairs"],
                "risk_pairs": matrix["risk_pairs"],
                "pairs": matrix["pairs"],
                "scope_violations": matrix["scope_violations"],
                "matrix_digest": matrix["digest"],
                "ts": ts,
            }
        )
        # 收单记录留档：后续任务的交集检测要看得见已收单的文件面
        self.state["finishes"] = [
            f for f in self.state["finishes"] if f["mission_id"] != finish.mission_id
        ] + [{"mission_id": finish.mission_id, "repo": finish.repo, "ts": ts,
              "changed_files": list(finish.changed_files),
              "path_globs": list(finish.path_globs)}]
        self._save_state()
        record_intake(self.ledger_path, matrix, finish, peers, ts, now_ts=ts)
        self._record("gate_stage", out, ts)
        return out

    def _peers_for(self, finish: FinishRecord) -> list[dict]:
        peers: list[dict] = []
        seen = set()
        for rec in self.active_reservations(finish.ts):
            if rec["mission_id"] == finish.mission_id:
                continue
            key = (rec["mission_id"], rec["repo"])
            if key in seen:
                continue
            seen.add(key)
            globs = sorted({r["path_glob"] for r in self.state["reservations"]
                            if r["mission_id"] == rec["mission_id"] and r["repo"] == rec["repo"]})
            peers.append({"mission_id": rec["mission_id"], "repo": rec["repo"],
                          "ts": rec["ts"], "changed_files": [], "path_globs": globs,
                          "mode": rec["mode"]})
        for rec in self.state["finishes"]:
            if rec["mission_id"] == finish.mission_id or (rec["mission_id"], rec["repo"]) in seen:
                continue
            peers.append({"mission_id": rec["mission_id"], "repo": rec["repo"],
                          "ts": rec["ts"], "changed_files": list(rec["changed_files"]),
                          "path_globs": list(rec.get("path_globs") or [])})
        return peers

    # ───────────────────────── 推送前合并预演 ─────────────────────────
    def merge_dry_run(self, repo_path: str | Path, ours: str, theirs: str,
                      base: str | None = None, ts: str | None = None) -> dict:
        """三方合并 dry-run：只读 ``git merge-tree``，输出可机器裁决的退出码。"""
        cfg = self.cfg["scheduler"]
        report = rehearse_pair(str(repo_path), str(ours), str(theirs), self.cfg, base)
        state = report["state"]
        if state == "clean":
            resolution, exit_code = cfg["on_merge_clean"], 0
        elif state == "conflict":
            resolution = cfg["on_merge_conflict"]
            exit_code = 1
        else:
            resolution, exit_code = cfg["on_unknown"], 2
        # digest 只覆盖**判定内容**：仓的物理位置、解析出的 oid、树对象、耗时都是观测值，
        # 不可能也不应该跨环境复现（同一份内容在不同机器上 commit oid 不同）。
        payload = {
            "schema": SCHEDULER_SCHEMA,
            "stage": "merge_dry_run",
            "ours": str(ours),
            "theirs": str(theirs),
            "base_ref": str(base or ""),
            "state": state,
            "mergeable": state == "clean",
            "exit_code": exit_code,
            "resolution": resolution,
            "reason": report.get("reason", ""),
            "conflicted_paths": report.get("conflicted_paths", []),
            "hunk_overlaps": [
                {"path": h["path"], "overlap": h["overlap"]}
                for h in report.get("hunks", []) if h.get("overlap")
            ],
            "ts": ts or _now_iso(),
        }
        out = with_digest(payload)
        out.update(
            {
                "repo_path": str(repo_path),
                "base_oid": report.get("base", ""),
                "rehearsal_digest": report.get("digest", ""),
            }
        )
        self._record("gate_stage", out, out["ts"])
        return out

    # ───────────────────────── 复合裁决 ─────────────────────────
    def decide(self, mission_id: str, repo: str, paths=None, changed_files=None,
               repo_path: str | Path | None = None, ours: str | None = None,
               theirs: str | None = None, base: str | None = None,
               ts: str | None = None) -> dict:
        """一条流水线跑完预留 → 交集 → 合并预演，输出统一裁决并入台账。"""
        ts = ts or _now_iso()
        stages: list[dict] = []
        resolution = "pass"

        def take(stage_out: dict) -> None:
            nonlocal resolution
            stages.append(stage_out)
            cand = stage_out["resolution"]
            if _RANK.get(cand, 3) > _RANK.get(resolution, 0):
                resolution = cand

        if paths:
            res = self.reserve(mission_id, repo, paths, ts=ts)
            take(res)
            if res["resolution"] in ("queued", "refused"):
                return self._verdict(mission_id, repo, stages, resolution, ts, stopped=True)
        if changed_files is not None:
            take(self.complete(mission_id, repo, changed_files,
                               path_globs=paths, ts=ts))
        if repo_path and ours and theirs:
            take(self.merge_dry_run(repo_path, ours, theirs, base=base, ts=ts))
        return self._verdict(mission_id, repo, stages, resolution, ts, stopped=False)

    def _verdict(self, mission_id: str, repo: str, stages: list[dict], resolution: str,
                 ts: str, stopped: bool) -> dict:
        out = with_digest(
            {
                "schema": SCHEDULER_SCHEMA,
                "stage": "decision",
                "mission_id": str(mission_id),
                "repo": str(repo),
                "resolution": resolution,
                "exit_code": exit_code_of(resolution),
                "passed": resolution == "pass",
                "stopped_early": bool(stopped),
                "stages": [
                    {"stage": s["stage"], "resolution": s["resolution"],
                     "exit_code": s["exit_code"], "digest": s["digest"]}
                    for s in stages
                ],
                "ts": ts,
            }
        )
        self._record("gate_decision", out, ts)
        return out

# ───────────────────────────── 运维 CLI ─────────────────────────────
def _cli(argv=None) -> int:
    """运维入口：reserve / drain / complete / merge / decide / release（只读+状态文件）。"""
    import argparse

    ap = argparse.ArgumentParser(prog="scheduler_gate", description="多租户并发写调度器门")
    ap.add_argument("--state", default=None, help="状态文件路径（默认取配置）")
    ap.add_argument("--ledger", default=None, help="台账 JSONL 路径（默认取配置）")
    ap.add_argument("--config", default=None, help="配置文件路径（默认仅用内置默认值）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("reserve", help="派单前路径预留")
    r.add_argument("--mission", required=True)
    r.add_argument("--repo", required=True)
    r.add_argument("--paths", required=True, help="逗号分隔的路径/glob")
    r.add_argument("--mode", default="write", choices=["write", "read"])
    r.add_argument("--ts", default=None)

    d = sub.add_parser("drain", help="队列按 FIFO 重放")
    d.add_argument("--ts", default=None)

    c = sub.add_parser("complete", help="收单 changed_files 交集检测")
    c.add_argument("--mission", required=True)
    c.add_argument("--repo", required=True)
    c.add_argument("--files", required=True, help="逗号分隔的 changed_files")
    c.add_argument("--globs", default="", help="逗号分隔的声明写面")
    c.add_argument("--ts", default=None)

    m = sub.add_parser("merge", help="三方合并 dry-run")
    m.add_argument("--repo-path", required=True)
    m.add_argument("--ours", required=True)
    m.add_argument("--theirs", required=True)
    m.add_argument("--base", default=None)

    v = sub.add_parser("decide", help="复合裁决（预留→交集→合并预演）")
    v.add_argument("--mission", required=True)
    v.add_argument("--repo", required=True)
    v.add_argument("--paths", default="")
    v.add_argument("--files", default=None)
    v.add_argument("--repo-path", default=None)
    v.add_argument("--ours", default=None)
    v.add_argument("--theirs", default=None)
    v.add_argument("--ts", default=None)

    q = sub.add_parser("release", help="释放任务预留")
    q.add_argument("--mission", required=True)
    q.add_argument("--repo", default=None)
    q.add_argument("--ts", default=None)

    args = ap.parse_args(argv)
    gate = SchedulerGate(config_path=args.config, state_path=args.state, ledger_path=args.ledger)
    split = lambda text: [p.strip() for p in (text or "").split(",") if p.strip()]  # noqa: E731

    if args.cmd == "reserve":
        out = gate.reserve(args.mission, args.repo, split(args.paths), mode=args.mode, ts=args.ts)
    elif args.cmd == "drain":
        out = gate.drain_queue(ts=args.ts)
    elif args.cmd == "complete":
        out = gate.complete(args.mission, args.repo, split(args.files),
                            path_globs=split(args.globs), ts=args.ts)
    elif args.cmd == "merge":
        out = gate.merge_dry_run(args.repo_path, args.ours, args.theirs, base=args.base)
    elif args.cmd == "decide":
        out = gate.decide(args.mission, args.repo, paths=split(args.paths),
                          changed_files=None if args.files is None else split(args.files),
                          repo_path=args.repo_path, ours=args.ours, theirs=args.theirs, ts=args.ts)
    else:
        out = gate.release(args.mission, repo=args.repo, ts=args.ts)
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if out.get("exit_code", 0) == 0 else out.get("exit_code", 1)


if __name__ == "__main__":  # pragma: no cover - 运维入口
    raise SystemExit(_cli())
