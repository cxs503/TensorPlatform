"""MOCK 指纹激光器的**自审**：带标签的合成语料 → 混淆矩阵 → 逐探测器灵敏度/特异度。

为什么要自审
------------
`tools/audit/mock_fingerprint.py` 是平台证据分级（REAL/MOCK/STUB/WITHHELD）的机器裁决器。
它自己从未被系统检验过：m1791 只用一组合成响应证明"探测器会响"，没有证明
**"MOCK 端点会不会从它手里走掉"**（灵敏度）与**"真端点会不会被它误杀"**（特异度）。

本工具构造一个**标签已知**的合成语料（每条 = 一个完整 probe 规格 + 一条响应族），
把 `score()` 的完整判决链（find_key → as_floats → classify_* → endpoint_verdict）跑在上面，
输出：

  * 灵敏度 sensitivity = 真 MOCK 被至少判到 PARTIAL 的比例（漏判 = 假 REAL，最危险）
  * 特异度 specificity = 真响应端点被判 RESPONSIVE 的比例（误杀 = 把 REAL 打成 MOCK）

两级口径并行跑：`legacy`（m1791 原判据，浮点 1e-9 / 无字符串解析 / 不跳回显 / 不查方向量尾部）
与 `hardened`（m2035 加固后）。**同一份语料、同一份代码**，差别只在口径开关，
因此表里的差值就是加固带来的真实增益，不含任何手抄数字。

用法
----
    python3 tools/audit/mock_gate_selfaudit.py                # 写 artifacts/mock_gate_selfaudit/audit.json
    python3 tools/audit/mock_gate_selfaudit.py --live-ab      # 额外对已落盘 raw.json 做 legacy/hardened A/B
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Callable, Dict, List, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.audit import mock_fingerprint as mf  # noqa: E402

OUT_DIR = os.path.join(ROOT, "artifacts", "mock_gate_selfaudit")
OUT_PATH = os.path.join(OUT_DIR, "audit.json")

# 检测口径：判到 MOCK 或 PARTIAL 都算"没有漏掉"（PARTIAL 仍是降级证据，不是清白的 REAL）
DETECTED = {"MOCK", "PARTIAL"}

SWEEP = "wave_period"
VALUES = [5.0, 7.0, 9.0, 12.0, 16.0]


# ──────────────────────────────────────────────────────────────────────────────
# 合成语料：每条 = (id, 真值标签, 物理说明, 规格, 响应生成器)
#   ground truth ∈ {"MOCK", "REAL"}；response 生成器吃扫参下标与取值
# ──────────────────────────────────────────────────────────────────────────────
def _b_series_kt(j: float) -> float:
    """Wageningen B4-70 形态的递减回归（结构正确、系数近似，用于造"健康端点"）。"""
    return max(-0.5, 0.5129 - 0.2400 * j - 0.1000 * j ** 2 + 0.0160 * j ** 3)


def _b_series_kq(j: float) -> float:
    return 0.0667 - 0.0520 * j - 0.0030 * j ** 2 - 0.0010 * j ** 3


def _raw(spec: Dict[str, Any], resp_fn: Callable[[int, float], Dict[str, Any]],
         values: Sequence[float] = VALUES) -> Dict[str, Any]:
    probes = []
    for i, v in enumerate(values):
        probes.append({"sweep_value": v, "payload": {SWEEP: v},
                       "http_status": 200, "response": resp_fn(i, v)})
    return {"api_base": "synthetic://selfaudit", "probes": {spec["id"]: {"spec": spec,
                                                                        "probes": probes}}}


def _spec(sid: str, keys: Dict[str, Any] | None = None, relations=None,
          vector_keys=None, plateau_keys=None) -> Dict[str, Any]:
    return {"id": sid, "endpoint": "synthetic/" + sid, "sweep": SWEEP,
            "values": list(VALUES), "keys": keys or {}, "relations": relations or [],
            "vector_keys": vector_keys or [], "plateau_keys": plateau_keys or [],
            "physics": "selfaudit corpus"}


def build_corpus() -> List[Dict[str, Any]]:
    corpus: List[Dict[str, Any]] = []

    def add(sid, truth, why, spec, fn):
        corpus.append({"id": sid, "truth": truth, "why": why, "raw": _raw(spec, fn)})

    # ── 真 MOCK：六类经典指纹（m1523/m1547/m1760 现场抓到的形态）────────────────
    add("mock_constant", "MOCK", "硬编码常量（m1547 垂荡 RAO ≡ 1.260）",
        _spec("mock_constant", {"gm": "dec"}),
        lambda i, v: {"gm": 1.26})

    add("mock_ratio_locked", "MOCK", "两回归式写成同一式的倍数（m1760 KQ ≡ KT/10）",
        _spec("mock_ratio_locked", {"kt": "dec", "kq": "dec"}, relations=[["kq", "kt"]]),
        lambda i, v: {"kt": _b_series_kt(0.1 * (i + 1)),
                      "kq": 0.1 * _b_series_kt(0.1 * (i + 1))})

    add("mock_direction", "MOCK", "回转要素随舵角反向（方向违背）",
        _spec("mock_direction", {"tactical_diameter": "dec"}),
        lambda i, v: {"tactical_diameter": 3.0 + 0.4 * i})

    add("mock_coarse_lattice", "MOCK", "查表栅格（m1523 回转要素落在 0.5L 整数倍）",
        _spec("mock_coarse_lattice", {"advance": "dec"}),
        lambda i, v: {"advance": 460.0 - 115.0 * i})

    add("mock_flat_vector", "MOCK", "向量通道常量填充（m1760 pitch_rao ≡ 0.02）",
        _spec("mock_flat_vector", vector_keys=["heave_rao"]),
        lambda i, v: {"heave_rao": [0.02] * 8})

    # ── 真 MOCK：四条**口径级伪装**（本单实测的逃逸通道）────────────────────────
    add("mock_string_encoded", "MOCK", "输出被 JSON 化成字符串，原判据整条静默丢弃",
        _spec("mock_string_encoded", {"gm": "dec"}),
        lambda i, v: {"gm": "1.260"})

    add("mock_echo_shadow", "MOCK", "响应回显请求体，DFS 取到 input.gm（随扫参变）",
        _spec("mock_echo_shadow", {"gm": None}),
        lambda i, v: {"input": {"gm": round(v, 3)}, "results": {"gm": 1.0}})

    add("mock_quantized_ratio", "MOCK", "恒比 KQ=KT/10 但输出 round(x,4)，1e-9 判据看不见",
        _spec("mock_quantized_ratio", {"kt": "dec", "kq": "dec"}, relations=[["kq", "kt"]]),
        lambda i, v: {"kt": round(_b_series_kt(0.1 * (i + 1)), 4),
                      "kq": round(0.1 * _b_series_kt(0.1 * (i + 1)), 4)})

    add("mock_blind_jitter", "MOCK", "硬编码值乘 1e-8 级伪抖动冒充有响应",
        _spec("mock_blind_jitter", {"gm": None}),
        lambda i, v: {"gm": 1.26 * (1.0 + 1e-8 * i)})

    add("mock_clamped_nonzero", "MOCK", "尾部夹紧在非零常数（d==0 逃过方向违背，未声明 plateau）",
        _spec("mock_clamped_nonzero", {"tactical_diameter": "dec"}),
        lambda i, v: {"tactical_diameter": [3.53, 3.17, 2.94, 2.94, 2.94][i]})

    # ── 真 REAL：必须保持 RESPONSIVE（误杀 = 把 REAL 打成 MOCK）────────────────
    add("real_b_series", "REAL", "两条独立 B 系列回归（结构健康）",
        _spec("real_b_series", {"kt": "dec", "kq": "dec"}, relations=[["kq", "kt"]]),
        lambda i, v: {"kt": _b_series_kt(0.1 * (i + 1)),
                      "kq": _b_series_kq(0.1 * (i + 1))})

    add("real_noisy", "REAL", "健康回归 + 1e-4 相对量测噪声（真实端点常态）",
        _spec("real_noisy", {"kt": "dec", "kq": "dec"}, relations=[["kq", "kt"]]),
        lambda i, v: {"kt": _b_series_kt(0.1 * (i + 1)) * (1 + 1e-4 * math.sin(7.1 * i)),
                      "kq": _b_series_kq(0.1 * (i + 1)) * (1 + 1e-4 * math.cos(5.3 * i))})

    add("real_rounded_4dp", "REAL", "健康回归但输出 round(x,4)（打印分辨率不构成指纹）",
        _spec("real_rounded_4dp", {"kt": "dec", "kq": "dec"}, relations=[["kq", "kt"]]),
        lambda i, v: {"kt": round(_b_series_kt(0.1 * (i + 1)), 4),
                      "kq": round(_b_series_kq(0.1 * (i + 1)), 4)})

    add("real_zero_floor", "REAL", "推力在高 J 归零（K_T=max(0,·) 物理零位，非截断）",
        _spec("real_zero_floor", {"kt": "dec"}),
        lambda i, v: {"kt": max(0.0, [0.42, 0.31, 0.18, 0.0, 0.0][i])})

    return corpus


# ──────────────────────────────────────────────────────────────────────────────
# 判决 + 混淆矩阵
# ──────────────────────────────────────────────────────────────────────────────
def verdict_of(raw: Dict[str, Any]) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    sc = mf.score(raw)
    sid = next(iter(sc["rows"]))
    row = sc["rows"][sid]
    per_key = {k: v.get("verdict") for k, v in row["keys"].items()}
    return row["verdict"], per_key, row


def run_profile(corpus: Sequence[Dict[str, Any]], profile: str) -> Dict[str, Any]:
    mf.set_detector_profile(profile)
    try:
        rows = []
        for item in corpus:
            verdict, per_key, row = verdict_of(item["raw"])
            rows.append({"id": item["id"], "truth": item["truth"], "why": item["why"],
                         "verdict": verdict, "keys": per_key,
                         "detected": verdict in DETECTED,
                         "correct": (verdict in DETECTED) if item["truth"] == "MOCK"
                         else (verdict == "RESPONSIVE")})
        mock = [r for r in rows if r["truth"] == "MOCK"]
        real = [r for r in rows if r["truth"] == "REAL"]
        sens = sum(1 for r in mock if r["detected"]) / len(mock) if mock else None
        spec = sum(1 for r in real if r["verdict"] == "RESPONSIVE") / len(real) if real else None
        return {"profile": profile, "rows": rows,
                "n_mock": len(mock), "n_real": len(real),
                "sensitivity": sens, "specificity": spec,
                "sensitivity_strong": (sum(1 for r in mock if r["verdict"] == "MOCK") / len(mock)
                                       if mock else None),
                "missed": [r["id"] for r in mock if not r["detected"]],
                "false_alarms": [r["id"] for r in real if r["verdict"] != "RESPONSIVE"]}
    finally:
        mf.set_detector_profile("hardened")


def live_ab(raw_path: str) -> Dict[str, Any]:
    """对**已落盘的真实端点回执**做 legacy/hardened A/B：口径加固不得改变任何真实判决。"""
    if not os.path.exists(raw_path):
        return {"available": False, "path": raw_path}
    raw = mf.load_raw(raw_path)
    out: Dict[str, Any] = {"available": True, "path": raw_path, "profiles": {}}
    for profile in ("legacy", "hardened"):
        mf.set_detector_profile(profile)
        try:
            sc = mf.score(raw)
        finally:
            mf.set_detector_profile("hardened")
        out["profiles"][profile] = {
            sid: {"verdict": row["verdict"],
                  "keys": {k: v.get("verdict") for k, v in row["keys"].items()}}
            for sid, row in sc["rows"].items()}
    a, b = out["profiles"]["legacy"], out["profiles"]["hardened"]
    out["verdict_changes"] = [sid for sid in a if a[sid]["verdict"] != b[sid]["verdict"]]
    key_changes = []
    for sid in a:
        for k in a[sid]["keys"]:
            if a[sid]["keys"][k] != b[sid]["keys"].get(k):
                key_changes.append({"probe": sid, "key": k,
                                    "legacy": a[sid]["keys"][k],
                                    "hardened": b[sid]["keys"].get(k)})
    out["key_changes"] = key_changes
    return out


def summarise(res: Dict[str, Any]) -> str:
    lines = []
    for profile in ("legacy", "hardened"):
        r = res[profile]
        lines.append(f"[{profile}] 灵敏度 {r['sensitivity']:.3f} "
                     f"({r['n_mock'] - len(r['missed'])}/{r['n_mock']})  "
                     f"特异度 {r['specificity']:.3f} "
                     f"({r['n_real'] - len(r['false_alarms'])}/{r['n_real']})  "
                     f"漏判 {r['missed']}  误杀 {r['false_alarms']}")
    for row in res["hardened"]["rows"]:
        lines.append(f"  {row['id']:<24} truth={row['truth']:<4} -> {row['verdict']:<20} {row['keys']}")
    if res.get("live_ab", {}).get("available"):
        ab = res["live_ab"]
        lines.append(f"[live A/B] probes={len(ab['profiles']['legacy'])} "
                     f"verdict_changes={ab['verdict_changes']} "
                     f"key_changes={len(ab['key_changes'])}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MOCK 指纹探测器自审（合成语料 + 混淆矩阵）")
    ap.add_argument("--live-ab", action="store_true",
                    help="对 artifacts/mock_sweep/raw.json 追加 legacy/hardened A/B")
    ap.add_argument("--json-out", default=OUT_PATH)
    args = ap.parse_args(argv)

    corpus = build_corpus()
    res: Dict[str, Any] = {"corpus_size": len(corpus),
                           "n_mock": sum(1 for c in corpus if c["truth"] == "MOCK"),
                           "n_real": sum(1 for c in corpus if c["truth"] == "REAL")}
    for profile in ("legacy", "hardened"):
        res[profile] = run_profile(corpus, profile)
    if args.live_ab:
        res["live_ab"] = live_ab(mf.RAW_PATH)

    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(summarise(res))
    print(f"→ {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())