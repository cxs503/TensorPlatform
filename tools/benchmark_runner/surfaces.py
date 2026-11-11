"""既有平台验证面适配层：把仓库里**已经存在**的纯函数验证面接进无人值守引擎。

为什么要这一层（P167W2B-N09）
------------------------------
基准引擎（N08）的算例当时全是"新造的合成数学"。这留下一个真实缺口：
引擎能不能**回头把仓库里已有的验证面**自动跑一遍？如果不能，它就只是
一个自带玩具题的跑步机，而不是平台的回归基础设施。

本模块把已有的平台机制（**零应用域**：记账 ETL 的数值工具、证据工件的
哈希链工具、回归分级器）包装成 runner 认得的 ``"module:function"`` 工作负载。
包装必须**极薄**（只做类型转换与指标编码），否则测的是适配层而不是被验证面；
所以每个 SUT 函数体都应只有 1-3 行调用。

独立参考解（oracle）纪律
------------------------
* **不复制 SUT 的算法路径**：SUT 走仓库里的实现，参考解走另一条独立路径
  （整数除法 / :mod:`decimal` 定点 / 直接 :mod:`hashlib` 手写串），
  这样两边同时错的可能性很低（"同一个 bug 写两遍"是最经典的假验证）。
* 参考解只用标准库，不 import 本包被测模块。
* 返回 ``dict``：``value`` 进 ``rel_error``，其余数值项进 metrics 供判据使用。

没有任何应用域词条：这里只有整数截断、定点四舍五入、SHA-256、回归分级阈值。
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import tempfile
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Dict

__all__ = [
    # 既有验证面 A：KPI 记账 ETL 的数值工具（tools/guide_kpi_ledger.py）
    "kpi_hour_bucket", "ref_hour_bucket",
    "kpi_ratio", "ref_ratio",
    "kpi_pct", "ref_pct",
    # 既有验证面 B：证据工件哈希链（tools/benchmark_runner/artifact.py）
    "digest_order_invariance", "ref_digest_order_invariance",
    "digest_payload_sensitivity", "ref_digest_payload_sensitivity",
    "manifest_tamper_detected", "ref_manifest_tamper_detected",
    # 既有验证面 C：回归分级器（tools/benchmark_runner/regression.py）
    "regression_severity_code", "ref_regression_severity_code",
]


# ── 既有验证面 A：记账 ETL 的数值工具 ──────────────────────────────────────
# SUT = tools.guide_kpi_ledger.{hour_bucket,ratio,pct}（仓库既有实现）
# 参考解 = 整数截断 / decimal 定点，与上面那条路径无共享代码。

def _parse_ts(ts_iso: str) -> datetime:
    ts = datetime.fromisoformat(ts_iso)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def kpi_hour_bucket(ts_iso: str) -> Dict[str, Any]:
    """SUT：既有 ``hour_bucket`` 的小时桶起点（epoch 秒）+ 时区是否保留。"""
    from tools.guide_kpi_ledger import hour_bucket          # 既有实现（薄调用）

    bucket = hour_bucket(_parse_ts(ts_iso))
    return {"value": float(bucket.timestamp()),
            "has_tz": 1.0 if bucket.tzinfo is not None else 0.0}


def ref_hour_bucket(ts_iso: str) -> Dict[str, Any]:
    """参考解：epoch 秒整数除 3600 再乘回（完全不同的一条路径）。"""
    epoch = _parse_ts(ts_iso).timestamp()
    hour_index = math.floor(epoch / 3600.0)
    return {"value": float(hour_index * 3600), "has_tz": 1.0}


def kpi_ratio(num: float, den: float, ndigits: int = 4) -> Dict[str, Any]:
    """SUT：既有 ``ratio``；分母 0 → None（"算不出来"不是 0），此处编码为 defined=0。"""
    from tools.guide_kpi_ledger import ratio

    r = ratio(num, den, ndigits)
    return {"value": 0.0 if r is None else float(r),
            "defined": 0.0 if r is None else 1.0}


def ref_ratio(num: float, den: float, ndigits: int = 4) -> Dict[str, Any]:
    """参考解：decimal 定点 ROUND_HALF_EVEN（标准库独立路径）。"""
    if float(den) == 0.0:
        return {"value": 0.0, "defined": 0.0}
    with localcontext() as ctx:
        ctx.prec = 50
        q = Decimal(1).scaleb(-int(ndigits))
        d = (Decimal(str(num)) / Decimal(str(den))).quantize(q, rounding=ROUND_HALF_EVEN)
    return {"value": float(d), "defined": 1.0}


def kpi_pct(value: float, ndigits: int = 1) -> Dict[str, Any]:
    """SUT：既有 ``pct``。"""
    from tools.guide_kpi_ledger import pct

    r = pct(value, ndigits)
    return {"value": 0.0 if r is None else float(r),
            "defined": 0.0 if r is None else 1.0}


def ref_pct(value: float, ndigits: int = 1) -> Dict[str, Any]:
    """参考解：十进制放大 100 倍后定点舍入（None 语义同 SUT：defined=0）。"""
    if value is None:
        return {"value": 0.0, "defined": 0.0}
    with localcontext() as ctx:
        ctx.prec = 50
        q = Decimal(1).scaleb(-int(ndigits))
        d = (Decimal(str(value)) * Decimal(100)).quantize(q, rounding=ROUND_HALF_EVEN)
    return {"value": float(d), "defined": 1.0}


# ── 既有验证面 B：证据工件哈希链 ───────────────────────────────────────────
# 性质型算例：被验证的不是"某个数"，而是"哈希链的两个不变式"。
#   B1 键序不变性：同内容的两种键序必须同摘要（规范化正确）；
#   B2 载荷敏感性：内容不同必须不同摘要（不退化）。
# 参考解用 hashlib 直接对手写规范串取哈希，不经过被测模块。

_B1_PAYLOAD = {"alpha": 1, "beta": [1, 2, 3], "gamma": {"x": 2.5, "y": -1}}
_B1_REORDERED = {"gamma": {"y": -1, "x": 2.5}, "beta": [1, 2, 3], "alpha": 1}


def digest_order_invariance() -> Dict[str, Any]:
    """SUT：``artifact.sha256_obj`` 对键序不同的同一载荷给出相同摘要 → value=1。"""
    from tools.benchmark_runner import artifact

    same = artifact.sha256_obj(_B1_PAYLOAD) == artifact.sha256_obj(_B1_REORDERED)
    return {"value": 1.0 if same else 0.0, "digest_len": float(len(artifact.sha256_obj(_B1_PAYLOAD)))}


def ref_digest_order_invariance() -> Dict[str, Any]:
    """参考解：手写规范串（sort_keys + 紧凑分隔符）→ hashlib；期望不变式成立。"""
    canon = json.dumps(_B1_PAYLOAD, sort_keys=True, separators=(",", ":"))
    canon2 = json.dumps(_B1_REORDERED, sort_keys=True, separators=(",", ":"))
    same = hashlib.sha256(canon.encode()).hexdigest() == hashlib.sha256(canon2.encode()).hexdigest()
    return {"value": 1.0 if same else 0.0, "digest_len": 64.0}


def digest_payload_sensitivity() -> Dict[str, Any]:
    """SUT：改动一个叶子的数值必须改变摘要 → value=1。"""
    from tools.benchmark_runner import artifact

    a = dict(_B1_PAYLOAD)
    b = dict(_B1_PAYLOAD, alpha=2)
    different = artifact.sha256_obj(a) != artifact.sha256_obj(b)
    return {"value": 1.0 if different else 0.0, "shared_prefix_ok": 1.0}


def ref_digest_payload_sensitivity() -> Dict[str, Any]:
    """参考解：同一不变式的手写路径。"""
    a = json.dumps(_B1_PAYLOAD, sort_keys=True, separators=(",", ":"))
    b = json.dumps(dict(_B1_PAYLOAD, alpha=2), sort_keys=True, separators=(",", ":"))
    return {"value": 1.0 if hashlib.sha256(a.encode()).digest() != hashlib.sha256(b.encode()).digest()
            else 0.0, "shared_prefix_ok": 1.0}


def _write_probe_run(root: pathlib.Path, tamper: bool = False) -> Dict[str, Any]:
    """在临时目录里构造一个最小证据工件目录（含自洽的 artifact_sha256）。"""
    from tools.benchmark_runner import artifact

    prov = {"engine_version": "probe", "config_sha256": "0" * 64,
            "git_sha": "0" * 40, "generated_at": "1970-01-01T00:00:00+00:00"}
    cases_dir = root / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    payload = {"case_id": "probe_case", "status": "pass", "passed": True,
               "metrics": {"value": 1.0}, "provenance": prov}
    payload["artifact_sha256"] = artifact.sha256_obj({k: v for k, v in payload.items()})
    artifact.write_json(cases_dir / "probe_case.json", payload)
    result = {"suite": "probe_suite", "run_id": "probe_run", "passed": True,
              "cases": [{"case_id": "probe_case", "passed": True, "status": "pass"}]}
    artifact.write_json(root / "result.json", result)
    artifact.write_manifest(root, {"suite": "probe_suite", "run_id": "probe_run", "passed": True}, prov)
    clean = artifact.verify_manifest(root)["ok"]
    if tamper:
        artifact.write_json(root / "result.json", {**result, "passed": False})
        after = artifact.verify_manifest(root)["ok"]
    else:
        after = clean
    return {"clean_verifies": clean, "after_verifies": after}


def manifest_tamper_detected() -> Dict[str, Any]:
    """SUT：落盘→回验必须 ok；改动字节后回验必须 False（篡改必被发现）。"""
    with tempfile.TemporaryDirectory() as td:
        out = _write_probe_run(pathlib.Path(td), tamper=True)
    detected = (out["clean_verifies"] is True) and (out["after_verifies"] is False)
    return {"value": 1.0 if detected else 0.0,
            "clean_ok": 1.0 if out["clean_verifies"] else 0.0,
            "tamper_flagged": 0.0 if out["after_verifies"] else 1.0}


def ref_manifest_tamper_detected() -> Dict[str, Any]:
    """参考解：预声明期望值（干净链必过、改字节必挂）——由不变式定义，非观测结果。"""
    return {"value": 1.0, "clean_ok": 1.0, "tamper_flagged": 1.0}


# ── 既有验证面 C：回归分级器 ───────────────────────────────────────────────
# SUT = regression.compare 的告警分级；参考解 = 阈值不等式独立分类。
# 编码：0=clean / 1=warn（>3%）/ 2=critical（>3×3%=9%）/ 3=算例消失类致命
_BASE_VALUE = 1.0


def _probe_suite(value: float) -> Dict[str, Any]:
    return {"suite": "probe_regression", "run_id": "probe", "passed": True,
            "cases": [{"case_id": "probe_case", "status": "pass", "passed": True,
                       "metrics": {"value": value}}]}


def regression_severity_code(current_value: float, threshold: float = 0.03) -> Dict[str, Any]:
    """SUT：既有 ``compare`` 对同一算例同一指标的漂移分级 → 数值编码。"""
    from tools.benchmark_runner import regression as reg

    baseline = {"cases": {"probe_case": {"passed": True, "metrics": {"value": _BASE_VALUE}}}}
    report = reg.compare(_probe_suite(current_value), baseline, drift_threshold=threshold)
    code = 0.0
    for alert in report.get("alerts", []):
        if alert.get("kind") == "drift":
            code = max(code, 1.0 if alert.get("severity") == "warn" else 2.0)
        elif alert.get("severity") == "critical":
            code = max(code, 3.0)
    return {"value": code, "n_alerts": float(len(report.get("alerts", [])))}


def ref_regression_severity_code(current_value: float, threshold: float = 0.03) -> Dict[str, Any]:
    """参考解：|cur-1|/1 与 threshold / 3×threshold 的不等式分类。"""
    drift = abs(float(current_value) - _BASE_VALUE) / max(abs(_BASE_VALUE), 1e-12)
    if drift <= threshold:
        code, n = 0.0, 0.0
    elif drift <= 3.0 * threshold:
        code, n = 1.0, 1.0
    else:
        code, n = 2.0, 1.0
    return {"value": code, "n_alerts": n}