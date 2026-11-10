"""Continuation-subtask seeding: turn ``salvage.json`` into a dispatchable seed.

A seed is only useful if it changes what the next worker does.  Three levers,
all derived from measured material rather than from prose:

1. **inputs** — the inheritable files with byte sizes and sha256, so the worker
   can verify it starts from exactly the salvaged state (``do_not_redo``).
2. **residual** — the obligations still ``OPEN`` after lexical matching; these
   are the only things the worker is asked to close.
3. **budget hint** — ``inherited_bytes``/``residual_open`` let the dispatcher
   size the retry instead of re-running the full original budget.

The subtask id is derived from the parent mission id and the salvage
fingerprint, so re-salvaging the same task twice yields the *same* subtask id
(idempotent seeding, no duplicate dispatch).
"""
from __future__ import annotations

SCHEMA_VERSION = "1.0.0"
GOAL_TEMPLATE = (
    "【续跑|{subtask_id}】父单 {mission_id} 在终局 {reason_code} 中断，已抢救可继承材料 {n_files} 个"
    "（{n_bytes} 字节，校验清单见 inputs）。执行顺序：① 逐文件校验 sha256 并阅读，禁止重写 do_not_redo 清单内文件；"
    "② 只针对 residual 清单内 OPEN 项补齐，逐项落到 artifacts/ 或代码并附 pytest/运行证据；"
    "③ 若 inputs 与目标结构冲突，以 residual 为准并在报告 deviation 段记录。"
    "验收沿用父单判据；无基准须显式声明，honest FAIL 不可覆盖。"
)


def _fmt_bytes(n: int) -> str:
    return f"{n}B" if n < 1024 else f"{n / 1024:.1f}KiB"


def build(salvage_payload: dict, provisional_fingerprint: str = "") -> dict:
    """Deterministic continuation seed for one salvage payload.

    ``provisional_fingerprint`` digests the salvage body *without* the seed, so
    the seed id is stable and independent of the seed text itself.
    """
    mission_id = (salvage_payload.get("task") or {}).get("mission_id")
    state = salvage_payload.get("state")
    inherited = salvage_payload.get("inheritable") or []
    residual = salvage_payload.get("residual") or []
    counts = salvage_payload.get("counts") or {}
    fingerprint = provisional_fingerprint or (salvage_payload.get("fingerprint") or {}).get("subject_sha256") or ""
    n_bytes = int(counts.get("inheritable_bytes") or 0)
    open_items = [r["item"] for r in residual if r.get("state") == "OPEN"]
    subtask_id = f"cont-{mission_id}-{fingerprint[:12]}" if fingerprint else f"cont-{mission_id}"

    seedable = state == "INHERITABLE" and bool(inherited)
    goal = GOAL_TEMPLATE.format(
        subtask_id=subtask_id,
        mission_id=mission_id,
        reason_code=(salvage_payload.get("terminal") or {}).get("reason_code", "TERMINAL_OTHER"),
        n_files=len(inherited),
        n_bytes=_fmt_bytes(n_bytes),
    )
    if not seedable:
        goal += (
            "【注意】父单无文件通过三筛（blocked={code}）：本单以残项清单为唯一输入，"
            "先建立目标结构再补交付，不得把父单未验证内容当作已完成。".format(
                code=(salvage_payload.get("blocked") or {}).get("code", "unknown")
            )
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "subtask_id": subtask_id,
        "parent_mission_id": mission_id,
        "repo": (salvage_payload.get("task") or {}).get("repo"),
        "seedable": seedable,
        "goal": goal,
        "inputs": [
            {"path": row["path"], "sha256": row["sha256"], "bytes": row["bytes"]}
            for row in inherited
        ],
        "do_not_redo": [row["path"] for row in inherited],
        "residual": [
            {"item": r["item"], "item_sha256": r["item_sha256"], "evidence": r.get("evidence") or []}
            for r in residual
            if r.get("state") == "OPEN"
        ],
        "residual_open_count": len(open_items),
        "acceptance": [r["item"] for r in residual],
        "budget_hint": {
            "inherited_bytes": n_bytes,
            "inherited_files": len(inherited),
            "residual_open": len(open_items),
            "retry_scale": round(1.0 / (1.0 + len(inherited)), 4) if inherited else 1.0,
        },
        "provenance": {
            "salvage_fingerprint": fingerprint,
            "salvage_state": state,
            "blocked_code": (salvage_payload.get("blocked") or {}).get("code"),
        },
    }