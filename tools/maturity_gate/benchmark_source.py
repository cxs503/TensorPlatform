"""maturity gate 的 L4 基准判据数据源：**有实证行才算通过**。

问题（丙令暂停的根因）：旧判据 ``benchmark_ok()`` 只看 evidence 里有没有数字
（``re.search(r"\\d")``），于是「ref/got/误差」可以手写成一段文字就过 L4 ——
claimed（自述）与 proven（实证）不分，L4 成了自声明。

新判据（本模块，纯函数，零领域语义）：

* 输入 = ``tensorbench_results`` 里该 mission 的**实证行**（平台侧从集群作业原始输出复算）；
* ``VERIFIED``            —— 至少一行：``passed`` 为真、ref/got/rel_error 非空、
  ``execution_status='success'``、``source='cluster_job'``、``cluster_job_id`` 非空，
  且 ``rel_error <= tolerance_rel``（行内容差；可用参数收紧）；
* ``CLAIMED_NOT_PROVEN``  —— 无实证行（或行全部不合格）：L4 不给，理由写清楚；
* ``FAILED``              —— 有实证行但误差超容差（跑过了，没过线；与「没跑」区分）。

红队可失败：伪造 L4 声明的单子在这里必然落 ``CLAIMED_NOT_PROVEN``，
声明的数字再漂亮也不参与判定（本模块从不读 evidence 自述数字）。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

VERIFIED = "VERIFIED"
CLAIMED_NOT_PROVEN = "CLAIMED_NOT_PROVEN"
FAILED = "FAILED"
WITHHELD = "WITHHELD"

CHECKS: tuple[str, ...] = (
    "execution_success", "source_cluster_job", "cluster_job_bound",
    "ref_got_error_present", "within_tolerance",
)


def _row_ok(row: Mapping[str, Any], tolerance_rel: Optional[float]) -> tuple[bool, list[str]]:
    bad: list[str] = []
    if str(row.get("execution_status")) != "success":
        bad.append("execution_success")
    if str(row.get("source") or "") != "cluster_job":
        bad.append("source_cluster_job")
    if row.get("cluster_job_id") in (None, "", 0):
        bad.append("cluster_job_bound")
    if row.get("ref_value") is None or row.get("got_value") is None or row.get("rel_error") is None:
        bad.append("ref_got_error_present")
    tol = tolerance_rel if tolerance_rel is not None else row.get("tolerance_rel")
    if not bad:
        rel = float(row["rel_error"])
        tol_f = float(tol) if tol is not None else -1.0
        if tol_f < 0 or rel > tol_f or not bool(row.get("passed")):
            bad.append("within_tolerance")
    return (not bad), bad


def verdict(rows: Sequence[Mapping[str, Any]], *, tolerance_rel: Optional[float] = None,
            claimed_level: Optional[int] = None) -> Dict[str, Any]:
    """实证行 → L4 基准判据裁定（三值，不二值化）。"""
    rows = list(rows or [])
    if not rows:
        return {
            "status": CLAIMED_NOT_PROVEN,
            "l4_benchmark_pass": False,
            "rows": 0,
            "claimed_level": claimed_level,
            "failed_checks": list(CHECKS),
            "reason": "no tensorbench_results row for this mission: the L4 benchmark claim "
                      "is self-declared, not measured",
            "rule": "L4 requires >=1 platform-recomputed benchmark row (ref/got/error)",
        }
    good: list[Mapping[str, Any]] = []
    failures: Dict[str, list[str]] = {}
    for row in rows:
        ok, bad = _row_ok(row, tolerance_rel)
        if ok:
            good.append(row)
        else:
            failures[str(row.get("metric"))] = bad
    if good:
        best = min(good, key=lambda r: float(r["rel_error"]))
        return {
            "status": VERIFIED, "l4_benchmark_pass": True, "rows": len(rows),
            "verified_metrics": sorted({str(r.get("metric")) for r in good}),
            "best": {"metric": best.get("metric"), "ref": best.get("ref_value"),
                     "got": best.get("got_value"), "rel_error": best.get("rel_error"),
                     "tolerance_rel": best.get("tolerance_rel"),
                     "run_id": best.get("run_id"),
                     "cluster_job_id": best.get("cluster_job_id")},
            "claimed_level": claimed_level, "failed_checks": [],
            "reason": "at least one platform-recomputed benchmark row meets tolerance",
        }
    return {
        "status": FAILED, "l4_benchmark_pass": False, "rows": len(rows),
        "claimed_level": claimed_level, "failed_checks": sorted({c for v in failures.values() for c in v}),
        "per_metric_failures": failures,
        "reason": "benchmark rows exist but none meets the declared tolerance",
    }


def l4_satisfied(rows: Sequence[Mapping[str, Any]], **kwargs: Any) -> bool:
    """给门用的布尔面：只有 ``VERIFIED`` 才给 L4。"""
    return verdict(rows, **kwargs)["status"] == VERIFIED
