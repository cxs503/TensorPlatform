"""只读数据抽取：``outcome_evaluations`` → (mission, grader, S, E, reason, ts)。

三条硬纪律（后面所有判据都靠它们才可信）：

1. **只读**：SQL 白名单只放行单条 ``SELECT``/``WITH``，防只读工具被复用成写通道。
2. **UNKEYED 不插补**：``reviewer IS NULL`` 的记录是「不知道谁评的」，既不能
   当成某个判级者，也不能悄悄丢掉——它们进入**覆盖率分母**并单独计数。缺判级者
   身份就做不出判级者间一致性，这是覆盖率缺口，不是可以补的缺失值。
3. **不在序数尺度上的等级不硬塞**：``S-N``（否决轨）与 S1..S5 是不同构念，
   不参与 α；它们在 ``off_scale`` 里单列，绝不当成「最差一档」混进序数尺度。

判级者身份的现实形态（本仓台账实测，写进报告）：``reviewer`` 是一串**席位复合
键**（如 ``seat-执行+seat-台账``）。它不是「一个人」，而是「一组席位的合并判级」。
因此 α 度量的是**复合判级键之间**的一致性，其解释边界写在报告「注意点」里。
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

#: S 轨序数尺度（S1 最强 → S5 最弱；α 的序数度量只用相对次序）
S_SCALE: tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5")
#: E 轨序数尺度（E0 无证据 → E4 最强）
E_SCALE: tuple[str, ...] = ("E0", "E1", "E2", "E3", "E4")
#: 不参与序数 α 的等级（不同构念）
OFF_SCALE: tuple[str, ...] = ("S-N",)

UNKEYED = "UNKEYED"

#: 抽取用 SQL（单条 SELECT；不含任何写操作）
EXTRACT_SQL = """
SELECT evaluation_id,
       mission_id,
       reviewer,
       reviewer_kind,
       grade,
       e_level,
       grade_reason,
       status,
       decided_at,
       window_finished_at
  FROM outcome_evaluations
 WHERE grade IS NOT NULL
 ORDER BY mission_id NULLS LAST, decided_at NULLS LAST, evaluation_id
""".strip()

_SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_WRITE_WORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|vacuum)\b",
    re.IGNORECASE,
)


# ─────────────────────────────── 记录模型 ───────────────────────────────

@dataclass(frozen=True)
class ReliabilityRecord:
    """一条判级记录（只保留可靠性分析需要的字段）。"""

    mission_id: str | None
    grader: str | None
    grade: str | None
    e_level: str | None
    status: str
    reason_chars: int = 0
    decided_at: str | None = None
    evaluation_id: str | None = None

    @property
    def keyed(self) -> bool:
        return self.grader is not None and str(self.grader).strip() != ""

    @property
    def off_scale(self) -> bool:
        return (self.grade or "") in OFF_SCALE

    def s_level(self) -> int | None:
        """S 轨 → 序数 1..5（``S-N`` / 非法值 → ``None``，不参与 α）。"""
        g = (self.grade or "").upper()
        return S_SCALE.index(g) + 1 if g in S_SCALE else None

    def e_rank(self) -> int | None:
        """E 轨 → 序数 1..5（E0→1 ... E4→5）。"""
        e = (self.e_level or "").upper()
        return E_SCALE.index(e) + 1 if e in E_SCALE else None


def record_from_row(row: dict[str, Any]) -> ReliabilityRecord:
    """把 SQL 行（dict）转成记录；空串判级者归一为 ``None``（UNKEYED）。"""
    reviewer = row.get("reviewer")
    reviewer = None if reviewer is None or str(reviewer).strip() == "" else str(reviewer)
    reason = row.get("grade_reason")
    return ReliabilityRecord(
        mission_id=None if row.get("mission_id") is None else str(row["mission_id"]),
        grader=reviewer,
        grade=(None if row.get("grade") is None else str(row["grade"])),
        e_level=(None if row.get("e_level") is None else str(row["e_level"])),
        status=str(row.get("status") or ""),
        reason_chars=len(reason) if isinstance(reason, str) else 0,
        decided_at=(None if row.get("decided_at") is None else str(row["decided_at"])),
        evaluation_id=(None if row.get("evaluation_id") is None else str(row["evaluation_id"])),
    )


def records_from_rows(rows: Iterable[dict[str, Any]]) -> list[ReliabilityRecord]:
    return [record_from_row(r) for r in rows]


# ─────────────────────────────── 只读 psql ───────────────────────────────

def psql_rows(sql: str, dsn_cmd: str = "psql -U foundation -d tensorplatform") -> list[dict[str, Any]]:
    """跑一条只读 SELECT 并返回 dict 行（``-t -A -F'\\t'`` 对齐 CSV 解析）。

    写操作 / 多条语句一律拒绝（``ValueError``），不静默改写 SQL。
    """
    if not _SELECT_ONLY.match(sql):
        raise ValueError("只允许单条 SELECT/WITH 查询（只读通道）")
    if _WRITE_WORDS.search(sql) or ";" in sql.strip().rstrip(";"):
        raise ValueError("检测到写关键字或多条语句，拒绝执行")
    cmd = dsn_cmd.split() + ["-t", "-A", "-F", "\t", "-c", sql]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"psql 失败（{proc.returncode}）：{proc.stderr.strip()[:400]}")
    cols = ["evaluation_id", "mission_id", "reviewer", "reviewer_kind", "grade",
            "e_level", "grade_reason", "status", "decided_at", "window_finished_at"]
    out: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        row = {c: (parts[i] if i < len(parts) else None) for i, c in enumerate(cols)}
        out.append(row)
    return out


def extract(dsn_cmd: str = "psql -U foundation -d tensorplatform",
            sql: str = EXTRACT_SQL,
            statuses: Sequence[str] = ("confirmed",)) -> dict[str, Any]:
    """抽取台账记录。

    ``statuses`` 默认只要 ``confirmed``（``candidate`` 按台账语义「不计入分布」，
    但保留开关做敏感性）；``pending``（等级为空）在 SQL 里已被排除。
    """
    rows = psql_rows(sql, dsn_cmd=dsn_cmd)
    recs = [r for r in records_from_rows(rows) if r.status in statuses]
    return {"records": recs, "sql": sql, "statuses": list(statuses),
            "rows_scanned": len(rows)}


# ─────────────────────────────── 覆盖率 ───────────────────────────────

def coverage(records: Sequence[ReliabilityRecord]) -> dict[str, Any]:
    """覆盖率 / 可分析性概览——**判定能否出数**的第一道闸。

    ``unkeyed_share`` 的分母是全部记录（含 ``S-N`` 与 off-scale），因为「谁评的
    不知道」这件事与等级无关；``pairable_missions`` 才是 α 的真正样本量。
    """
    total = len(records)
    keyed = [r for r in records if r.keyed]
    unkeyed = total - len(keyed)
    on_scale = [r for r in keyed if r.s_level() is not None]
    by_mission: dict[str, set[str]] = {}
    for r in on_scale:
        by_mission.setdefault(r.mission_id or "", set()).add(r.grader or "")
    sizes = sorted((len(v) for v in by_mission.values()), reverse=True)
    pairable = [m for m, v in by_mission.items() if len(v) >= 2]
    grader_counts: dict[str, int] = {}
    for r in on_scale:
        grader_counts[r.grader or ""] = grader_counts.get(r.grader or "", 0) + 1
    # 同一 mission 的多条记录（同一判级者对不同成果、或多个判级者）——用于区分
    # 「跨判级者配对」与「同一判级者内部离散」，两者绝不可混为一谈。
    all_by_mission: dict[str, list[str]] = {}
    for r in on_scale:
        all_by_mission.setdefault(r.mission_id or "", []).append(r.grader or "")
    n_multi_record = sum(1 for v in all_by_mission.values() if len(v) >= 2)
    n_cross, n_same = 0, 0
    for v in all_by_mission.values():
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                if v[i] == v[j]:
                    n_same += 1
                else:
                    n_cross += 1
    return {
        "n_records": total,
        "n_missions_multi_record": n_multi_record,
        "n_cross_grader_pairs": n_cross,
        "n_same_grader_pairs": n_same,
        "n_keyed": len(keyed),
        "n_unkeyed": unkeyed,
        "unkeyed_share": (unkeyed / total) if total else float("nan"),
        "n_on_s_scale": len(on_scale),
        "n_off_scale": sum(1 for r in keyed if r.off_scale),
        "n_missions": len(by_mission),
        "n_pairable_missions": len(pairable),
        "pairable_mission_ids": sorted(pairable, key=str)[:50],
        "max_graders_per_mission": sizes[0] if sizes else 0,
        "n_missions_with_ge2": sum(1 for s in sizes if s >= 2),
        "n_missions_with_ge3": sum(1 for s in sizes if s >= 3),
        "n_graders": len(grader_counts),
        "grader_sizes": dict(sorted(grader_counts.items(), key=lambda kv: -kv[1])),
        "pairable_share_of_missions": (len(pairable) / len(by_mission)) if by_mission else float("nan"),
    }


# ───────────────────────────── 矩阵 / 长表 ─────────────────────────────

def to_matrix(records: Sequence[ReliabilityRecord], dimension: str = "S",
              graders: Sequence[str] | None = None,
              units: Sequence[str] | None = None) -> tuple[np.ndarray, list[str], list[str]]:
    """转成 ``units × graders`` 矩阵（缺失格为 ``nan``）。

    ``dimension="S"`` 用 S1..S5（1..5）；``"E"`` 用 E0..E4（1..5）。
    一个单元被同一判级者重复判级时取**最后一次**（``decided_at`` 最大），并在
    报告里作为已知约定写明（重复判级在台账里不是独立证据）。
    """
    def level(r: ReliabilityRecord) -> int | None:
        return r.s_level() if dimension.upper() == "S" else r.e_rank()

    rows = [r for r in records if r.keyed and level(r) is not None]
    if graders is None:
        graders = sorted({r.grader or "" for r in rows})
    if units is None:
        units = sorted({r.mission_id or "" for r in rows})
    gidx = {g: j for j, g in enumerate(graders)}
    uidx = {u: i for i, u in enumerate(units)}
    mat = np.full((len(units), len(graders)), np.nan)
    seen: dict[tuple[int, int], tuple[str, int]] = {}
    for r in rows:
        i, j = uidx[r.mission_id or ""], gidx[r.grader or ""]
        ts = r.decided_at or ""
        if (i, j) in seen and seen[(i, j)][0] > ts:
            continue
        seen[(i, j)] = (ts, level(r) or 0)
        mat[i, j] = float(level(r) or 0)
    return mat, list(graders), list(units)


def to_long(records: Sequence[ReliabilityRecord], dimension: str = "S") -> list[tuple[str, str, float]]:
    """长表 ``(mission, grader, level)``——供 ``variance_components`` / φ_g。"""
    out: list[tuple[str, str, float]] = []
    for r in records:
        if not r.keyed:
            continue
        lv = r.s_level() if dimension.upper() == "S" else r.e_rank()
        if lv is None:
            continue
        out.append((r.mission_id or "", r.grader or "", float(lv)))
    return out


def pair_agreement(records: Sequence[ReliabilityRecord]) -> list[dict[str, Any]]:
    """多评单元的逐对一致性明细（审计用：谁和谁、差几级）。

    只输出 ≥2 条记录的单元；差值为 ``|Δ级|``（同一序数尺度上）。``same_grader=True``
    的配对是**同一判级者键对同一 mission 的不同成果**给出不同等级 → 那是
    「判级者内部离散」，**不是**判级者间一致性，报告里必须分开计。
    """
    by_mission: dict[str, list[ReliabilityRecord]] = {}
    for r in records:
        if r.keyed and r.s_level() is not None:
            by_mission.setdefault(r.mission_id or "", []).append(r)
    out: list[dict[str, Any]] = []
    for mid, rs in sorted(by_mission.items(), key=lambda kv: str(kv[0])):
        if len(rs) < 2:
            continue
        rs = sorted(rs, key=lambda r: str(r.grader))
        for a in range(len(rs)):
            for b in range(a + 1, len(rs)):
                la, lb = rs[a].s_level(), rs[b].s_level()
                out.append({
                    "mission_id": mid,
                    "same_grader": rs[a].grader == rs[b].grader,
                    "grader_a": rs[a].grader, "grader_b": rs[b].grader,
                    "grade_a": rs[a].grade, "grade_b": rs[b].grade,
                    "abs_diff_levels": abs((la or 0) - (lb or 0)),
                    "e_a": rs[a].e_level, "e_b": rs[b].e_level,
                })
    return out


def summary_json(records: Sequence[ReliabilityRecord]) -> str:
    return json.dumps(coverage(records), ensure_ascii=False, sort_keys=True)


__all__ = [
    "S_SCALE", "E_SCALE", "OFF_SCALE", "UNKEYED", "EXTRACT_SQL",
    "ReliabilityRecord", "record_from_row", "records_from_rows",
    "psql_rows", "extract", "coverage", "to_matrix", "to_long",
    "pair_agreement", "summary_json",
]