#!/usr/bin/env python3
"""收单时的冲突矩阵：本次 ``finish.changed_files`` × 同窗口其他任务。

判据（每一条都可被单独推翻）
--------------------------
对一个"同窗口"的其他任务（同仓、时间差 ≤ ``window_seconds``）比较三件事：

============================  ====================================================  ==========
lane                          含义                                                  是否阻塞
============================  ====================================================  ==========
``file_overlap``              双方 ``changed_files`` 精确同路径                      阻塞
``lease_breach``              本次改到的文件落在对方的 ``write`` 租约内              阻塞
``scope_violation``           本次改到的文件不在**自己**声明的租约内                 阻塞
``invalid_path``              路径越界（绝对 / ``..`` / 超子集写法）                  阻塞
``glob_overlap``              自己的路径模式与对方模式可能相交（文件级无重叠）        仅记录
``clean``                     以上皆否                                               —
============================  ====================================================  ==========

阻塞集合与处置策略来自配置（``intake.on_conflict`` = ``block`` / ``report_only``），
矩阵的键序、列表序全部排序固定 ⇒ 同一 (<finish>, <peers>, <config>) 逐字节一致。
"""
from __future__ import annotations

from .model import INTAKE_SCHEMA, FinishRecord, TenancyError, normalize_path, parse_ts, with_digest
from .pattern import GlobSyntaxError, glob_covers_path, globs_intersect

__all__ = ["conflict_matrix", "BLOCKING_LANES", "RISK_LANES"]

BLOCKING_LANES = ("file_overlap", "lease_breach", "scope_violation", "invalid_path")
RISK_LANES = ("glob_overlap",)


def _resolve_files(finish: FinishRecord) -> tuple[list[str], list[str]]:
    ok, bad = [], []
    for raw in finish.changed_files:
        try:
            ok.append(normalize_path(raw))
        except TenancyError:
            bad.append(str(raw))
    return sorted(set(ok)), sorted(set(bad))


def _own_lease_globs(finish: FinishRecord, peers) -> list[str]:
    """本次任务自己的租约模式：优先用 finish 自带的，其次从 peers 里按 mission_id 找。"""
    globs = list(finish.path_globs)
    if not globs:
        for peer in peers:
            if str(peer.get("mission_id")) == finish.mission_id:
                for key in ("path_globs", "lease_globs"):
                    globs.extend(str(g) for g in (peer.get(key) or []))
                lease = peer.get("lease")
                if isinstance(lease, dict) and lease.get("path_glob"):
                    globs.append(str(lease["path_glob"]))
    return sorted(set(globs))


def _peer_globs(peer: dict) -> list[str]:
    globs = [str(g) for g in (peer.get("path_globs") or [])]
    lease = peer.get("lease")
    if isinstance(lease, dict) and lease.get("path_glob"):
        globs.append(str(lease["path_glob"]))
    return sorted(set(globs))


def _peer_mode(peer: dict) -> str:
    lease = peer.get("lease")
    if isinstance(lease, dict) and lease.get("mode"):
        return str(lease["mode"]).lower()
    return "write"


def conflict_matrix(finish: FinishRecord, peers, config: dict, now_ts: str | None = None) -> dict:
    """产出可入库、可复算的冲突矩阵。

    ``peers`` 是"同窗口候选集合"的**原始声明**（每项含 ``mission_id`` / ``repo`` /
    ``changed_files`` / ``ts``，可选 ``path_globs`` 与 ``lease``）。窗口外的 peer 会被
    记进 ``peers_out_of_window`` 而不是静默丢弃。
    """
    intake_cfg = config["intake"]
    window = int(intake_cfg["window_seconds"])
    now = parse_ts(now_ts or finish.ts)
    finish_ts = parse_ts(finish.ts)
    files, bad_files = _resolve_files(finish)
    own_globs = _own_lease_globs(finish, peers)

    in_window, out_window = [], []
    for peer in peers:
        mid = str(peer.get("mission_id", "")).strip()
        if not mid or mid == finish.mission_id:
            continue
        try:
            peer_ts = parse_ts(str(peer.get("ts", "")))
        except TenancyError:
            out_window.append(mid)
            continue
        clock_skew = abs((now - peer_ts).total_seconds())
        if clock_skew <= window:
            in_window.append((peer_ts, mid, peer))
        else:
            out_window.append(mid)

    in_window.sort(key=lambda item: (item[0], item[1]))
    pairs: list[dict] = []
    conflict_pairs = 0
    risk_pairs = 0

    for peer_ts, mid, peer in in_window:
        same_repo = str(peer.get("repo", "")) == finish.repo
        if not same_repo and not intake_cfg["cross_repo_check"]:
            pairs.append(
                {
                    "mission_id": mid,
                    "repo": str(peer.get("repo", "")),
                    "severity": "clean",
                    "reason": "cross_repo",
                    "files": [],
                    "file_overlap": [],
                    "lease_breach_files": [],
                    "glob_overlaps": [],
                }
            )
            continue
        try:
            peer_files = sorted({normalize_path(f) for f in (peer.get("changed_files") or [])})
        except TenancyError:
            peer_files = sorted(str(f) for f in (peer.get("changed_files") or []))
        shared = sorted(set(files) & set(peer_files))
        leased_hits = _peer_leased_files(peer, files)
        touched = sorted(set(shared) | set(leased_hits))
        glob_overlaps = []
        for mine in own_globs + files:
            try:
                for theirs in _peer_globs(peer):
                    if globs_intersect(mine, theirs):
                        glob_overlaps.append({"a": mine, "b": theirs})
            except GlobSyntaxError:      # pragma: no cover
                pass
        glob_overlaps = sorted({(g["a"], g["b"]) for g in glob_overlaps})
        glob_overlaps = [{"a": a, "b": b} for a, b in glob_overlaps]

        lanes = []
        if bad_files:
            lanes.append("invalid_path")
        if shared:
            lanes.append("file_overlap")
        if leased_hits:
            lanes.append("lease_breach")
        if not shared and not leased_hits and glob_overlaps:
            lanes.append("glob_overlap")

        severity = "clean"
        if any(lane in BLOCKING_LANES for lane in lanes):
            severity = "conflict"
        elif any(lane in RISK_LANES for lane in lanes):
            severity = "risk"

        if severity == "conflict":
            conflict_pairs += 1
        elif severity == "risk":
            risk_pairs += 1
        pairs.append(
            {
                "mission_id": mid,
                "repo": str(peer.get("repo", "")),
                "peer_ts": str(peer.get("ts", "")),
                "lease_mode": _peer_mode(peer),
                "severity": severity,
                "lanes": sorted(lanes),
                "files": touched,
                "file_overlap": shared,
                "lease_breach_files": leased_hits,
                "glob_overlaps": glob_overlaps,
                "peer_files_count": len(peer_files),
            }
        )

    scope_violations: list[str] = []
    if intake_cfg["require_lease_coverage"] and own_globs:
        for path in files:
            if not any(glob_covers_path(g, path) for g in own_globs):
                scope_violations.append(path)

    decision, reason = "admit", "no_conflict"
    if bad_files:
        decision, reason = _blocked(intake_cfg), "invalid_path"
    elif scope_violations:
        decision, reason = _blocked(intake_cfg), "scope_violation"
    elif conflict_pairs:
        decision, reason = _blocked(intake_cfg), "conflict_detected"
    elif risk_pairs:
        decision, reason = "admit", "risk_recorded_only"

    return with_digest(
        {
            "schema": INTAKE_SCHEMA,
            "window_seconds": window,
            "finish": {
                "mission_id": finish.mission_id,
                "repo": finish.repo,
                "ts": finish.ts,
                "changed_files": files,
                "invalid_files": bad_files,
                "own_globs": own_globs,
            },
            "peers_in_window": [mid for _ts, mid, _p in in_window],
            "peers_out_of_window": sorted(out_window),
            "pairs": pairs,
            "conflict_pairs": conflict_pairs,
            "risk_pairs": risk_pairs,
            "scope_violations": sorted(scope_violations),
            "decision": decision,
            "reason": reason,
            "policy": intake_cfg["on_conflict"],
            "evaluated_at": str(now_ts or finish.ts),
        }
    )


def _blocked(intake_cfg: dict) -> str:
    return "block" if intake_cfg["on_conflict"] == "block" else "admit"


def _peer_leased_files(peer: dict, shared: list[str]) -> list[str]:
    """shared 中落在对方 write 租约内的文件。"""
    if _peer_mode(peer) == "read":
        return []
    globs = _peer_globs(peer)
    hit = []
    for path in shared:
        for g in globs:
            try:
                if glob_covers_path(g, path):
                    hit.append(path)
                    break
            except GlobSyntaxError:      # pragma: no cover
                continue
    return hit