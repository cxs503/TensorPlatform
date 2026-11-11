"""判据语料采集：把「历史判决」变成可回放的快照。

两条源
------
``docs``
    仓内 markdown（离线、确定性、随分支版本走）。这是**首选**：语料随时间不腐烂，
    任何人 checkout 本分支就能重跑回放。
``reports``
    平台报告库（``/api/v1/tensor/reports``）。报告库里存着大量历史判决，但接口按
    mission 过滤、无全量分页，故采集是**采样式**的：给定 mission id 样本，逐条取报告
    markdown，抽行后落快照。网络不可达时该源自动跳过（快照仍可回放）。

快照契约
--------
``data/criteria_metrology/corpus.json`` 每条记录自带：

* ``source.kind / path / report_id / mission_id / line``：溯源三件套
* ``source.row``：原始行文本（人可复核）
* ``source.row_sha256``：行文本指纹（改写即变，用于"判据是否被悄悄改过"）
* ``source.derived_y``：y 是否由"基准+报告误差"反推（方向不可知，只用于退化测绘）

纪律：采集**不改**任何被采文件；``--check`` 只比指纹不写盘。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .parse import DEFAULT_TERMS, parse_markdown_criteria


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 质量过滤：宁可少收，不可收错
# --------------------------------------------------------------------------- #


def record_is_usable(rec: dict[str, Any]) -> tuple[bool, str | None]:
    """质量门：拒绝"看起来像判据但其实不是"的行。"""
    tau = rec.get("tau")
    if tau is None or not isinstance(tau, (int, float)) or not math.isfinite(tau) or tau <= 0.0:
        return False, "tau_missing_or_nonpositive"
    if rec.get("metric") == "one_sided":
        # 单边界判据必须带显式比较符，否则"容差"其实是别列的数值
        if rec.get("comparator") not in (">=", "<="):
            return False, "one_sided_without_comparator"
        raw_tol = (rec.get("source", {}).get("cells", {}) or {}).get("tolerance", "")
        if not re.search(r"(≥|>=|≤|<=|>|<)", raw_tol):
            return False, "one_sided_tolerance_without_relation"
    y, y_ref = rec.get("y"), rec.get("y_ref")
    iv = rec.get("reference_interval")
    if y is not None and y_ref is not None:
        if y == 0.0 and y_ref == 0.0:
            return False, "both_zero_uninformative"
        if not (math.isfinite(y) and math.isfinite(y_ref)):
            return False, "non_finite"
    elif y_ref is None and iv is None:
        return False, "no_reference"
    if rec.get("source", {}).get("derived_y") and (rec.get("source", {}) or {}).get("reported_error") is None:
        return False, "derived_without_error"
    return True, None


CONSISTENCY_TOL = 0.02


def faithfulness(rec: dict[str, Any]) -> tuple[str, float | None]:
    """记录忠实度分级（抽取自检）：

    ``faithful``      被测量来自"实测"列，且（若有报告误差）能被 (y, y_ref) 复现到
                      ``CONSISTENCY_TOL``（相对偏差 2%）——四元组自洽
    ``derived``       被测量由"基准 + 报告误差"反推（方向不可知）
    ``inconsistent``  报告误差与 (y, y_ref) 不自洽 ⇒ 抽取把列搞错了，**剔除**
    """
    src = rec.get("source") or {}
    reported = src.get("reported_error")
    if src.get("derived_y"):
        return "derived", None
    y, y_ref = rec.get("y"), rec.get("y_ref")
    if y is None or y_ref is None or reported is None:
        return "faithful", None
    if not (isinstance(y, (int, float)) and isinstance(y_ref, (int, float))) or y_ref == 0.0:
        return "faithful", None
    err = abs(y - y_ref) / abs(y_ref)
    dev = abs(err - reported) / max(abs(reported), 1e-300)
    return ("faithful" if dev <= CONSISTENCY_TOL else "inconsistent"), dev


def finalize_record(rec: dict[str, Any]) -> dict[str, Any]:
    """补指纹与出处指纹、忠实度分级。"""
    src = rec.setdefault("source", {})
    row = src.get("row") or ""
    src["row_sha256"] = _sha(row)
    tier, dev = faithfulness(rec)
    src["faithfulness"] = tier
    src["consistency_deviation"] = dev
    rec["criterion_id"] = _sha(
        json.dumps(
            {
                "q": rec.get("quantity"),
                "y": rec.get("y"),
                "y_ref": rec.get("y_ref"),
                "iv": rec.get("reference_interval"),
                "tau": rec.get("tau"),
                "src": {k: src.get(k) for k in ("kind", "path", "report_id", "mission_id", "line")},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )[:16]
    return rec


# --------------------------------------------------------------------------- #
# 源 1：仓内 markdown
# --------------------------------------------------------------------------- #


def harvest_docs(
    paths: Iterable[Path],
    *,
    root: Path | None = None,
    terms: dict[str, list[str]] | None = None,
    require_tolerance_role: bool = True,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    files = sorted(Path(p) for p in paths)
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(root)) if root else str(p)
        try:
            rows, rej = parse_markdown_criteria(
                text,
                source={"kind": "markdown", "path": rel},
                terms=terms or DEFAULT_TERMS,
                require_tolerance_role=require_tolerance_role,
            )
        except Exception as exc:  # 采集不得因单个文件失败而中断
            rejected[f"parse_error:{type(exc).__name__}"] = rejected.get(f"parse_error:{type(exc).__name__}", 0) + 1
            continue
        for x in rej:
            rejected[x["reason"]] = rejected.get(x["reason"], 0) + 1
        for r in rows:
            rec = r.to_criterion().as_dict()
            rec["source"]["path"] = rel
            ok, why = record_is_usable(rec)
            if not ok:
                rejected[why or "unknown"] = rejected.get(why or "unknown", 0) + 1
                continue
            records.append(finalize_record(rec))
    return {
        "kind": "docs",
        "n_files": len(files),
        "n_records": len(records),
        "rejected": rejected,
        "records": records,
    }


# --------------------------------------------------------------------------- #
# 源 2：平台报告库（采样）
# --------------------------------------------------------------------------- #


@dataclass
class PlatformClient:
    base: str
    token: str

    def get(self, path: str) -> Any:
        req = urllib.request.Request(
            self.base + path,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def mission_ids(self, limit: int = 100, offset: int = 0, status: str | None = "succeeded") -> list[int]:
        q = f"/tensor/missions?limit={limit}&offset={offset}"
        if status:
            q += f"&status={status}"
        try:
            data = self.get(q)
        except (urllib.error.URLError, OSError, ValueError):
            return []
        items = data.get("items") if isinstance(data, dict) else data
        return [int(i["id"]) for i in (items or []) if i.get("id") is not None]

    def reports_for_mission(self, mission_id: int) -> list[dict[str, Any]]:
        try:
            data = self.get(f"/tensor/reports?mission_id={mission_id}")
        except (urllib.error.URLError, OSError, ValueError):
            return []
        return data.get("items") or []

    def report(self, report_id: int) -> dict[str, Any]:
        try:
            return self.get(f"/tensor/reports/{report_id}") or {}
        except (urllib.error.URLError, OSError, ValueError):
            return {}


def harvest_reports(
    client: PlatformClient,
    *,
    mission_ids: Sequence[int],
    terms: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """按 mission 采样取报告 markdown，抽行入语料。"""
    records: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    n_reports = 0
    n_missing = 0
    for mid in mission_ids:
        items = client.reports_for_mission(int(mid))
        if not items:
            n_missing += 1
            continue
        for it in items:
            rid = it.get("id")
            if rid is None:
                continue
            full = client.report(int(rid))
            md = full.get("markdown") or ""
            n_reports += 1
            if not md:
                rejected["empty_markdown"] = rejected.get("empty_markdown", 0) + 1
                continue
            rows, rej = parse_markdown_criteria(
                md,
                source={"kind": "report", "report_id": int(rid), "mission_id": int(mid), "title": full.get("title")},
                terms=terms or DEFAULT_TERMS,
                require_tolerance_role=True,
            )
            for x in rej:
                rejected[x["reason"]] = rejected.get(x["reason"], 0) + 1
            for r in rows:
                rec = r.to_criterion().as_dict()
                ok, why = record_is_usable(rec)
                if not ok:
                    rejected[why or "unknown"] = rejected.get(why or "unknown", 0) + 1
                    continue
                records.append(finalize_record(rec))
    return {
        "kind": "reports",
        "n_missions": len(mission_ids),
        "n_missions_without_report": n_missing,
        "n_reports": n_reports,
        "n_records": len(records),
        "rejected": rejected,
        "records": records,
    }


def sample_mission_ids(all_ids: Sequence[int], n: int, *, seed: int = 7810) -> list[int]:
    """确定性采样（不依赖 random 的版本差异：用乘法步长）。"""
    ids = sorted(set(int(i) for i in all_ids))
    if n >= len(ids):
        return ids
    if not ids:
        return []
    step = max(1, len(ids) // n)
    start = seed % len(ids)
    out = []
    idx = start
    while len(out) < n and len(out) < len(ids):
        out.append(ids[idx % len(ids)])
        idx += step
    return sorted(set(out))


def merge_snapshots(*snaps: dict[str, Any]) -> dict[str, Any]:
    """合并多源快照，按 criterion_id 去重（先到先得），保留每源统计。"""
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for s in snaps:
        sources.append({k: v for k, v in s.items() if k != "records"})
        for r in s.get("records", []):
            cid = r.get("criterion_id")
            if cid in seen:
                continue
            seen.add(cid)
            records.append(r)
    records.sort(key=lambda r: (str(r.get("source", {}).get("kind")), str(r.get("source", {}).get("path") or r.get("source", {}).get("report_id")), int(r.get("source", {}).get("line") or 0)))
    return {
        "n_records": len(records),
        "sources": sources,
        "records": records,
    }


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    """语料指纹：只取每条记录的语义字段，便于漂移检测。"""
    payload = [
        {
            "id": r.get("criterion_id"),
            "y": r.get("y"),
            "y_ref": r.get("y_ref"),
            "tau": r.get("tau"),
            "row": r.get("source", {}).get("row_sha256"),
        }
        for r in snapshot.get("records", [])
    ]
    return _sha(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))