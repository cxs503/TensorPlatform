"""既有评价管线的**回归契约**（金标指纹）。

外部效度校准依赖 :mod:`tools.external_validity.metrics` 的 AUC / Brier / 校准 /
分层 / CUSUM / walk-forward。这些函数一旦被无意改动（重构、依赖升级、边界条件
「优化」），下游所有判级结论都会静默漂移。故本模块：

1. 用**固定合成夹具**（seeded，无 DB、无随机源）跑一遍参考实现的全部出口；
2. 把结果四舍五入到 10 位，形成指纹，存 ``golden_external_validity.json``；
3. 每次分析时重算并按 ``atol=1e-9`` 比对——不一致即 J7 FAIL（仪器漂移，禁止放行）。

合成夹具刻意只用 ``repo-a`` / ``agent-1`` 这类**无域语义**占位名，符合平台铁律。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from tools.external_validity import metrics as ref

GOLDEN_PATH = Path(__file__).with_name("golden_external_validity.json")
ATOL = 1e-9


def fixture(n_days: int = 6, per_day: int = 40, seed: int = 7) -> list[dict[str, Any]]:
    """确定性合成 cohort：``day`` / ``x``（4 维）/ ``y``，含正负与可分离信号。"""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for d in range(n_days):
        for i in range(per_day):
            x = rng.normal(size=4)
            latent = 1.1 * x[0] - 0.7 * x[1] + 0.4 * x[2]
            p = 1.0 / (1.0 + math.exp(-latent))
            y = int(rng.random() < p)
            rows.append({"day": f"2026-01-{d + 1:02d}", "x": x.tolist(), "y": y})
    return rows


def fingerprint() -> dict[str, Any]:
    """参考管线的全部出口 → 可比对的指纹（浮点统一 round 到 10 位）。"""
    rows = fixture()
    scores = [1.0 / (1.0 + math.exp(-(r["x"][0] - r["x"][1]))) for r in rows]
    y = [r["y"] for r in rows]
    days = [r["day"] for r in rows]
    cal = ref.calibration_curve(scores, y, n_bins=10)
    strat = ref.stratified_auc(scores, y, days)
    wf = ref.walk_forward(rows, "y")
    cusum = ref.cusum_detect([sum(y[i:i + 10]) / 10.0 for i in range(0, len(y) - 9, 10)],
                             target=float(np.mean(y)))
    return {
        "fixture": {"n_days": 6, "per_day": 40, "seed": 7, "n": len(rows),
                    "pos_rate": round(float(np.mean(y)), 10)},
        "auc_score": round(ref.auc_score(scores, y), 10),
        "brier_score": round(ref.brier_score(scores, y), 10),
        "brier_skill": round(ref.brier_skill_score(scores, y), 10),
        "max_calibration_deviation": round(ref.max_calibration_deviation(cal, 10), 10),
        "calibration_bins_n": [b["n"] for b in cal],
        "calibration_actual": [None if math.isnan(b["actual"]) else round(b["actual"], 10)
                               for b in cal],
        "stratified_auc": round(strat["auc"], 10),
        "stratified_pairs": round(strat["pairs"], 10),
        "walk_forward_pooled_auc": round(wf["pooled_auc"], 10),
        "walk_forward_pooled_brier": round(wf["pooled_brier"], 10),
        "walk_forward_windows": len(wf["windows"]),
        "cusum_alarms": list(cusum.alarms),
    }


def _diff(a: Any, b: Any, path: str = "") -> list[str]:
    """递归比对，返回不匹配项（浮点用 ``ATOL``）。"""
    out: list[str] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: 缺键")
            else:
                out += _diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: 长度 {len(a)}!={len(b)}"]
        for i, (x, z) in enumerate(zip(a, b)):
            out += _diff(x, z, f"{path}[{i}]")
        return out
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a == b else [f"{path}: {a}!={b}"]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a is None or b is None:
            return [] if a == b else [f"{path}: {a}!={b}"]
        return [] if abs(float(a) - float(b)) <= ATOL else [f"{path}: {a}!={b}"]
    return [] if a == b else [f"{path}: {a}!={b}"]


def check(golden_path: str | Path | None = None) -> dict[str, Any]:
    """重算指纹并与金标比对。"""
    path = Path(golden_path) if golden_path else GOLDEN_PATH
    if not path.exists():
        return {"status": "WITHHELD", "n_mismatch": 0, "mismatches": [],
                "note": f"金标缺失：{path}"}
    golden = json.loads(path.read_text(encoding="utf-8"))
    now = fingerprint()
    mism = _diff(golden, now)
    return {
        "status": "PASS" if not mism else "FAIL",
        "n_mismatch": len(mism),
        "mismatches": mism[:20],
        "golden_path": str(path),
        "note": "tools.external_validity.metrics 出口指纹不变" if not mism
                else "参考评价管线输出漂移——禁止放行",
    }


def write_golden(golden_path: str | Path | None = None) -> Path:
    """生成/刷新金标（只在**有意变更**参考管线时人工执行，并在报告里声明）。"""
    path = Path(golden_path) if golden_path else GOLDEN_PATH
    path.write_text(json.dumps(fingerprint(), ensure_ascii=False, indent=2,
                               sort_keys=True), encoding="utf-8")
    return path