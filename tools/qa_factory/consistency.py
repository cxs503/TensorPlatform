#!/usr/bin/env python3
"""QA 产线 · 分层抽样与口径一致性裁决。

抽样：层（pass / machine-flag / suspect）× 层内 `sha256(f"{seed}:{report_id}")` 升序取前 n
—— 确定性、可复现、不看内容（避免挑好看的样本）。

一致性：机器判（tier）vs 深检判（deep_tier）
  · 三分类精确一致率（G1 主门 ≥ 0.90）
  · 二分类「pass vs 非 pass」一致率（G2 安全门 ≥ 0.90：防止把可疑报告清成 passed）
  · Cohen's κ（机会修正，报告不作门）
任一主门不过 → gate_passed=false → 机器门不投用、清账不执行。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

TIERS = ("pass", "machine-flag", "suspect")
G1_MIN = 0.90      # 三分类一致率门（预注册）
G2_MIN = 0.90      # pass/非pass 一致率门（预注册）
MIN_N = 30         # 低于此样本量一律判 not_enough_sample（门不放行）


def _h(seed: str, rid: int) -> str:
    return hashlib.sha256(f"{seed}:{rid}".encode()).hexdigest()


def stratified_sample(tiers: Dict[int, str], *, per_stratum: int = 15, seed: str = "qa-factory-r1",
                      strata: Sequence[str] = TIERS) -> List[Dict[str, Any]]:
    """tiers: report_id -> tier。返回 [{report_id, tier, pick_rank}]，层内确定性排序取前 n。"""
    out: List[Dict[str, Any]] = []
    for stratum in strata:
        members = sorted((rid for rid, t in tiers.items() if t == stratum),
                         key=lambda rid: _h(seed, rid))
        for rank, rid in enumerate(members[:per_stratum]):
            out.append({"report_id": rid, "tier": stratum, "pick_rank": rank, "seed": seed})
    return out


def cohen_kappa(pairs: Iterable[tuple], labels: Sequence[str] = TIERS) -> Optional[float]:
    pairs = list(pairs)
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pe = 0.0
    for lab in labels:
        pa = sum(1 for a, _ in pairs if a == lab) / n
        pb = sum(1 for _, b in pairs if b == lab) / n
        pe += pa * pb
    if pe >= 1.0:
        return 1.0
    return round((po - pe) / (1 - pe), 4)


def wilson_ci(k: int, n: int, z: float = 1.96) -> Optional[List[float]]:
    """Wilson 区间（比例的小样本置信区间，用于披露统计功效）。"""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def evaluate_consistency(machine: Dict[int, str], deep: Dict[int, str], *,
                         gate_ref: str = "", corpus_sha: str = "",
                         g1_min: float = G1_MIN, g2_min: float = G2_MIN) -> Dict[str, Any]:
    ids = sorted(set(machine) & set(deep))
    missing = sorted(set(deep) - set(machine))
    pairs3 = [(machine[i], deep[i]) for i in ids]
    pairs2 = [("pass" if machine[i] == "pass" else "not_pass",
               "pass" if deep[i] == "pass" else "not_pass") for i in ids]
    n = len(pairs3)
    agree3 = sum(1 for a, b in pairs3 if a == b)
    agree2 = sum(1 for a, b in pairs2 if a == b)
    a3 = agree3 / n if n else None
    a2 = agree2 / n if n else None
    per_stratum: Dict[str, Dict[str, Any]] = {}
    for st in TIERS:
        sub = [(machine[i], deep[i]) for i in ids if machine[i] == st]
        if sub:
            ag = sum(1 for a, b in sub if a == b)
            per_stratum[st] = {"n": len(sub), "agree": ag, "rate": round(ag / len(sub), 4),
                               "disagreements": [i for i in ids if machine[i] == st and machine[i] != deep[i]]}
    # 安全侧误判：机器 pass 但深检非 pass（会被错误清账）——单列，最要紧
    unsafe = [i for i in ids if machine[i] == "pass" and deep[i] != "pass"]
    conservative = [i for i in ids if machine[i] != "pass" and deep[i] == "pass"]
    enough = n >= MIN_N
    gate = bool(enough and a3 is not None and a3 >= g1_min and a2 is not None and a2 >= g2_min)
    return {
        "gate_ref": gate_ref, "corpus_sha256": corpus_sha,
        "n": n, "min_n": MIN_N, "enough_sample": enough,
        "agreement_3class": None if a3 is None else round(a3, 4),
        "agreement_pass_vs_not": None if a2 is None else round(a2, 4),
        "ci95_3class": wilson_ci(agree3, n), "ci95_pass_vs_not": wilson_ci(agree2, n),
        "kappa_3class": cohen_kappa(pairs3),
        "thresholds": {"g1_3class": g1_min, "g2_pass_vs_not": g2_min},
        "gate_passed": gate,
        "gate_reason": ("ok" if gate else
                        ("not_enough_sample" if not enough else
                         ("g1_fail" if (a3 or 0) < g1_min else "g2_fail"))),
        "per_stratum": per_stratum,
        "unsafe_machine_pass": unsafe,
        "conservative_machine_flag": conservative,
        "deep_ids_not_in_machine": missing,
    }


def load_deep_reviews(path: Path) -> Dict[int, str]:
    out: Dict[int, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rec = json.loads(line)
        tier = rec.get("deep_tier") or rec.get("tier")
        if tier not in TIERS:
            raise ValueError(f"bad deep_tier {tier!r} in {p}")
        out[int(rec["report_id"])] = tier
    return out


def load_machine_tiers(path: Path) -> Dict[int, str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    reps = payload.get("reports") or payload.get("verdicts") or []
    return {int(r["report_id"]): r["tier"] for r in reps if r.get("report_id") is not None}


def render_markdown(res: Dict[str, Any], *, title: str = "QA 产线第 1 轮 · 机器门口径一致性") -> str:
    L: List[str] = [f"# {title}", ""]
    L += ["## 判据（预注册，未事后调整）", "",
          f"- G1 三分类一致率 ≥ {res['thresholds']['g1_3class']:.2f}（主门）",
          f"- G2 pass/非pass 一致率 ≥ {res['thresholds']['g2_pass_vs_not']:.2f}（安全门：防把可疑报告清成 passed）",
          f"- 最小样本量 {res['min_n']}；当前 n = {res['n']}", ""]
    L += ["## 结果", "",
          "| 指标 | 值 | 95% CI |", "|---|---|---|",
          f"| 三分类一致率 | {res['agreement_3class']} | {res['ci95_3class']} |",
          f"| pass/非pass 一致率 | {res['agreement_pass_vs_not']} | {res['ci95_pass_vs_not']} |",
          f"| Cohen's κ（三分类，参考） | {res['kappa_3class']} | — |", ""]
    L += ["| 层 | n | 一致 | 一致率 |", "|---|---|---|---|"]
    for st, d in res["per_stratum"].items():
        L.append(f"| {st} | {d['n']} | {d['agree']} | {d['rate']} |")
    L += ["", "## 裁决", "",
          f"- **gate_passed = {str(res['gate_passed']).lower()}**（{res['gate_reason']}）",
          f"- 危险误判（机器 pass 但深检非 pass）：{res['unsafe_machine_pass'] or '无'}",
          f"- 保守误判（机器非 pass 但深检 pass）：{res['conservative_machine_flag'] or '无'}", ""]
    if not res["gate_passed"]:
        L += ["> **机器门不投用**：qa_status 清账不执行机器授权路径，保持人工/Agent 深检路径。", ""]
    L += ["## 与报告语言无关的边界", "",
          "- 机器门只判纪律形态 + 证据可追溯性，不判物理正确性；物理对错仍归深检与复算。",
          "- 本表一致率带小样本 CI；要认证 ≥0.90 需累计 n≈140（下轮扩样）。", ""]
    return "\n".join(L)