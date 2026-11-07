#!/usr/bin/env python3
"""IN25-T2 多样性引擎·节点执行器（生成全部可机验工件 + 报告用数字）。

跑法：
    python3 -m tools.diversity.run_node            # 全量（含现场 /v1/models 对账）

产物（默认 worktree 内 artifacts/in25_t2/）：
    roster_check.json      —— 氏族清单现场对账（族存在性由 /v1/models 实测）
    seats.json             —— 全格席位矩阵（族×框架×角色）
    coverage.json          —— 氏族覆盖矩阵（满格判据）+ 角色平衡 + 边际覆盖
    seed_divergence.json   —— 先验种子比特级距离
    prereg_lock.json       —— 盲预测锁定工件（含 lock_hash）
    prereg_verify.json     —— 锁复算结果（verify + 篡改负例）
    blind_scores.json      —— 锁+结果 → 盲预测成绩（时序门先跑）
    dedup_ledger.json      —— 方案相似度门逐条判定（含克隆负例）
    dedup_calibration.json —— 标注语料上的分离度（TPR/FPR + 间距）
    population.json        —— 已收方案人口结构（反同族）
    numbers.json           —— 判据数字与工件路径（报告程序化注入的唯一来源）
    fig1_coverage_matrix.png / fig2_similarity_separation.png

**诚实边界**：本节点的 seat "结果" 是**合成 dry-run**（由种子决定的确定性数值），
用途是打通并证伪"盲预测锁 → 结果 → 计分"的时序门，**不是**模型能力证据；
真实锦标赛由 IN25-T3 在集群上跑。此点写进 blind_scores.json 的 `synthetic: true` 与报告。
零平台域语义：语料是域无关的通用方法卡，不含任何学科量。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Sequence

from tools.diversity import dedup, prereg, roster

OUT = Path("artifacts/in25_t2")
GOAL = ("为目标量 y*(θ) 设计一条可复算的求解/估计路径，并给出可被下一代检验的预期收益"
        "（目标文本本身与学科无关：θ 是待定参数，y* 是唯一靶值）")


# ---------------------------------------------------------------------------
# 1. 通用方法卡（域无关）：base = 独立方案；*_clone = 各类克隆变体
# ---------------------------------------------------------------------------
BASE_CARDS: List[dict] = [
    {"id": "M1", "clade_id": "deepseek",
     "summary": "用 Richardson 外推把一阶格式的步长序列 h, h/2, h/4 的结果组合成二阶估计，"
                "每级误差按 0.5 的比例衰减，用三次重复确认外推稳定性并给出截断项上界。",
     "deliverable": "外推表 + 截断项上界 + 收敛阶实测"},
    {"id": "M2", "clade_id": "qwen",
     "summary": "用多重网格 V 循环：粗网格上做 Gauss-Seidel 平滑，细网格上只处理高频残差，"
                "限制/延拓算子取全权重，收敛判据用残差 1e-8 的相对下降。",
     "deliverable": "V 循环收敛曲线 + 每层迭代次数 + 残差判据"},
    {"id": "M3", "clade_id": "minimax",
     "summary": "用谱配置法在 Chebyshev 点上展开解，利用谱系数指数衰减做截断，"
                "把边界条件吸收进基函数，误差用系数衰减率 与 余项估计双重核验。",
     "deliverable": "系数衰减表 + 余项估计 + 边界吸收的代数推导"},
    {"id": "M4", "clade_id": "glm",
     "summary": "用伴随方法求目标对全部参数的敏感度：一次前向 + 一次伴随即可得到全梯度，"
                "再用拟牛顿更新参数，步长用线搜索保证目标单调下降。",
     "deliverable": "伴随梯度与有限差分的一致性核查 + 更新序列 + 单调性证据"},
    {"id": "M5", "clade_id": "deepseek",
     "summary": "用贝叶斯校准：给参数先验，构造似然并用 MCMC 采样后验，"
                "以 95% 可信区间作为不确定度报告，收敛用 R-hat<1.01 判定。",
     "deliverable": "后验样本 + R-hat 诊断 + 可信区间"},
    {"id": "M6", "clade_id": "qwen",
     "summary": "用同伦/连续延拓：把一个解析可解的参数极限作为起点，"
                "按伪弧长步进把参数推到目标值，每步用 Newton 校正并记录转折点。",
     "deliverable": "延拓路径 + 转折点位置 + 每步 Newton 残差"},
]

# 克隆变体：换词不换招（骨架相同）
SYN = [("给出", "提供"), ("采用", "使用"), ("必须", "需要"), ("与", "和"),
       ("误差", "偏差"), ("计算", "求解"), ("方案", "思路"), ("用", "利用")]


def _paraphrase(text: str) -> str:
    out = text
    for a, b in SYN:
        out = out.replace(a, b)
    parts = [p for p in out.replace("；", "，").split("，") if p.strip()]
    return "，".join(reversed(parts)) + "。"


def _retune(text: str) -> str:
    return (text.replace("0.5", "0.35").replace("1e-8", "1e-7").replace("95%", "90%")
                .replace("1.01", "1.05").replace("三次", "五次").replace("二阶", "三阶"))


def clone_variants(base: dict) -> List[dict]:
    s = base["summary"]
    return [
        {"id": base["id"] + "-c1", "clade_id": base["clade_id"], "kind": "verbatim",
         "summary": s, "deliverable": base["deliverable"]},
        {"id": base["id"] + "-c2", "clade_id": base["clade_id"], "kind": "paraphrase",
         "summary": _paraphrase(s), "deliverable": base["deliverable"].replace("证据", "佐证")},
        {"id": base["id"] + "-c3", "clade_id": base["clade_id"], "kind": "retune",
         "summary": _retune(s), "deliverable": base["deliverable"]},
        {"id": base["id"] + "-c4", "clade_id": base["clade_id"], "kind": "embedded",
         "summary": "在复现既有工作之后，本文进一步" + s.rstrip("。") + "，并补充少量讨论与展望。",
         "deliverable": base["deliverable"]},
    ]


# ---------------------------------------------------------------------------
# 2. 现场对账（/v1/models）
# ---------------------------------------------------------------------------
def live_models() -> dict:
    import urllib.request
    base = "http://10.10.30.8:3000/v1/models"
    key = "sk-B2QPaLiIz4zxcrAmxT6dtdnny6ZaYboR2aEAk1VPLugiZ4x6"
    try:
        req = urllib.request.Request(base, headers={"Authorization": "Bearer " + key})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        ids = sorted(m["id"] for m in d.get("data", []))
        return {"reachable": True, "models": ids}
    except Exception as exc:  # 网络不可达时如实降级（报告写 honest FAIL 面）
        return {"reachable": False, "models": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# 3. 盲预测 + 合成 dry-run 结果
# ---------------------------------------------------------------------------
def make_predictions(seats: Sequence[roster.Seat], truth: float = 0.62) -> List[prereg.Prediction]:
    """每席先验预测：攻坚者窄区间、破坏者宽区间（破坏者的任务决定它更不确定）。"""
    preds = []
    for s in seats:
        u = (s.seed % 1000) / 1000.0
        f = next(fr for fr in roster.FRAMINGS if fr.key == s.framing)
        width = 0.10 if s.role == "attacker" else 0.22
        center = min(0.95, max(0.05, 0.15 + 0.75 * u))
        bias = {"direct": 0.04, "inverse": -0.02, "limits": 0.00, "failure": 0.06,
                "orthogonal": -0.05}[s.framing]
        lo = round(max(0.0, center + bias - width / 2), 4)
        hi = round(min(1.0, center + bias + width / 2), 4)
        preds.append(prereg.Prediction(
            seat_id=s.seat_id, band_lo=lo, band_hi=hi,
            claim=f"{s.family} 在「{f.cn}」上能把目标推进到 y≈{center:.2f}±{width/2:.2f}，"
                  f"因为该路径直接作用于 {f.stress_axis} 轴。",
            falsifier=f"若在 {f.stress_axis} 轴上测得改进 < 0.02 或方向相反，则该席预测作废。",
        ))
    return preds


def synthetic_results(seats: Sequence[roster.Seat], lock: dict, truth: float = 0.62) -> List[dict]:
    """合成 dry-run：值 = truth + 由种子决定的有界扰动（无任何模型调用）。"""
    t0 = prereg._parse(lock["locked_utc"])
    rows = []
    for i, s in enumerate(sorted(seats, key=lambda x: x.seat_id)):
        u = ((s.seed >> 11) % 1000) / 1000.0
        fam_bonus = {"deepseek": 0.03, "qwen": 0.01, "minimax": -0.02, "glm": -0.01}[s.family]
        val = round(min(1.0, max(0.0, truth + fam_bonus + 0.16 * (u - 0.5))), 4)
        rows.append({
            "seat_id": s.seat_id,
            "value": val,
            "recorded_utc": (t0 + timedelta(minutes=7 + i)).isoformat(timespec="seconds"),
            "plan_digest": lock["plan_digest"],
            "synthetic": True,
        })
    return rows


# ---------------------------------------------------------------------------
# 4. 相似度门：标注语料分离度 + 逐条判定
# ---------------------------------------------------------------------------
def calibration() -> dict:
    """标注语料：克隆对（骨架相同）vs 独立对（不同方法），量出 TPR/FPR 与阈值间距。"""
    clone_pairs, indep_pairs = [], []
    for base in BASE_CARDS:
        for v in clone_variants(base):
            m = dedup.measures(v["summary"], base["summary"])
            m["a"], m["b"], m["kind"] = v["id"], base["id"], v["kind"]
            m["flagged"] = bool(dedup._clone_reasons(m, same_clade=True))
            clone_pairs.append(m)
    for i in range(len(BASE_CARDS)):
        for j in range(i + 1, len(BASE_CARDS)):
            a, b = BASE_CARDS[i], BASE_CARDS[j]
            m = dedup.measures(a["summary"], b["summary"])
            m["a"], m["b"] = a["id"], b["id"]
            m["flagged"] = bool(dedup._clone_reasons(m, same_clade=(a["clade_id"] == b["clade_id"])))
            indep_pairs.append(m)
    def mech(p):
        return sorted(dedup._clone_reasons(p, same_clade=True))

    by_mech = {"CLONE_SKELETON": 0, "CLONE_CONTAINMENT": 0, "CLONE_LEXICAL": 0,
               "SAME_CLADE_REPEAT": 0, "RETUNE_ONLY": 0}
    for p in clone_pairs:
        for r in mech(p):
            key = r.split("(")[0]
            by_mech[key] = by_mech.get(key, 0) + 1
    same_clade_indep = [p["skeleton"] for p in indep_pairs
                        if BASE_CARDS[[c["id"] for c in BASE_CARDS].index(p["a"])]["clade_id"]
                        == BASE_CARDS[[c["id"] for c in BASE_CARDS].index(p["b"])]["clade_id"]]
    tpr = sum(1 for p in clone_pairs if p["flagged"]) / len(clone_pairs)
    fpr = sum(1 for p in indep_pairs if p["flagged"]) / len(indep_pairs)
    return {
        "n_clone_pairs": len(clone_pairs),
        "n_indep_pairs": len(indep_pairs),
        "tpr_clone_rejected": round(tpr, 4),
        "fpr_indep_rejected": round(fpr, 4),
        "clone_min_skeleton": min(p["skeleton"] for p in clone_pairs),
        "clone_median_skeleton": sorted(p["skeleton"] for p in clone_pairs)[len(clone_pairs) // 2],
        "indep_max_skeleton": max(p["skeleton"] for p in indep_pairs),
        "thresholds": {
            "T_CLONE_SKELETON": dedup.T_CLONE_SKELETON,
            "T_CLONE_CONTAINMENT": dedup.T_CLONE_CONTAINMENT,
            "T_CLONE_LEXICAL": dedup.T_CLONE_LEXICAL,
            "T_SAME_CLADE": dedup.T_SAME_CLADE,
            "CLADE_SHARE_CAP": dedup.CLADE_SHARE_CAP,
        },
        "flagged_by_mechanism": by_mech,
        "indep_same_clade_max_skeleton": max(same_clade_indep) if same_clade_indep else None,
        "clone_pairs": clone_pairs,
        "indep_pairs": indep_pairs,
    }


def admission_ledger() -> dict:
    """逐条过门：6 张独立卡 + 每卡 4 个克隆变体，顺序提交，克隆必须被拒。"""
    accepted: List[dict] = []
    rows = []
    for base in BASE_CARDS:
        d = dedup.admit(base["summary"], accepted, candidate_clade=base["clade_id"],
                        deliverable=base["deliverable"])
        rows.append({"id": base["id"], "truth": "independent", **d.as_dict()})
        if d.accepted:
            accepted.append({**base})
        for v in clone_variants(base):
            d = dedup.admit(v["summary"], accepted, candidate_clade=v["clade_id"],
                            deliverable=v["deliverable"])
            rows.append({"id": v["id"], "truth": "clone:" + v["kind"], **d.as_dict()})
            if d.accepted:
                accepted.append(v)
    # 负例补充：缺交付物 / 过短摘要 / 无 clade（fail-closed 三例）
    extra = [
        dedup.admit(BASE_CARDS[0]["summary"], accepted, candidate_clade="deepseek", deliverable=""),
        dedup.admit("短摘要。", accepted, candidate_clade="deepseek", deliverable="x"),
        dedup.admit(BASE_CARDS[0]["summary"], accepted, candidate_clade="", deliverable="x"),
    ]
    n_clones = sum(1 for r in rows if r["truth"].startswith("clone:"))
    clones_rejected = sum(1 for r in rows if r["truth"].startswith("clone:") and not r["accepted"])
    indep = [r for r in rows if r["truth"] == "independent"]
    return {
        "n_candidates": len(rows) + len(extra),
        "n_clones": n_clones,
        "clones_rejected": clones_rejected,
        "clone_rejection_rate": round(clones_rejected / n_clones, 4) if n_clones else None,
        "independents": len(indep),
        "independents_accepted": sum(1 for r in indep if r["accepted"]),
        "fail_closed_negatives": [e.as_dict() for e in extra],
        "fail_closed_all_rejected": all(not e.accepted for e in extra),
        "rows": rows,
        "n_accepted_total": len(accepted),
    }


# ---------------------------------------------------------------------------
# 5. 图
# ---------------------------------------------------------------------------
def figures(seats: Sequence[roster.Seat], cal: dict, out: Path) -> List[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    made = []
    fams = list(dict.fromkeys(s.family for s in seats))
    frs = [f.key for f in roster.FRAMINGS]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    for ax, role in zip(axes, roster.ROLES):
        M = np.zeros((len(fams), len(frs)))
        for s in seats:
            if s.role == role:
                M[fams.index(s.family), frs.index(s.framing)] += 1
        im = ax.imshow(M, cmap="Blues", vmin=0, vmax=max(1, M.max()))
        ax.set_xticks(range(len(frs)), frs, rotation=25)
        ax.set_yticks(range(len(fams)), fams)
        ax.set_title(f"{role} seats  min={M.min():.0f} (per cell)")
        for i in range(len(fams)):
            for j in range(len(frs)):
                ax.text(j, i, f"{M[i, j]:.0f}", ha="center", va="center",
                        color="white" if M[i, j] > M.max() * 0.6 else "black")
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.suptitle("IN25-T2 clade grid: families × framings × roles (every cell staffed)")
    fig.tight_layout()
    p1 = out / "fig1_coverage_matrix.png"
    fig.savefig(p1, dpi=140)
    plt.close(fig)
    made.append(str(p1))

    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    clone = [p["skeleton"] for p in cal["clone_pairs"]]
    indep = [p["skeleton"] for p in cal["indep_pairs"]]
    bins = np.linspace(0, 1, 26)
    ax.hist(indep, bins=bins, alpha=0.75, label=f"independent pairs (n={len(indep)})", color="#2c7fb8")
    ax.hist(clone, bins=bins, alpha=0.75, label=f"clone pairs (n={len(clone)})", color="#d95f0e")
    ax.axvline(cal["thresholds"]["T_CLONE_SKELETON"], color="k", ls="--",
               label=f"reject @ skeleton≥{cal['thresholds']['T_CLONE_SKELETON']}")
    ax.set_xlabel("skeleton similarity (synonym+number normalized)")
    ax.set_ylabel("pair count")
    ax.set_title(f"Clone gate separation: TPR={cal['tpr_clone_rejected']:.2f}, "
                 f"FPR={cal['fpr_indep_rejected']:.2f}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p2 = out / "fig2_similarity_separation.png"
    fig.savefig(p2, dpi=140)
    plt.close(fig)
    made.append(str(p2))
    return made


# ---------------------------------------------------------------------------
# 6. 主流程
# ---------------------------------------------------------------------------
def main(out: Path = OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    live = live_models()
    rc = roster.verify_roster(live["models"])
    fams_live = [f for f, v in rc["families"].items() if v["live"]]
    seats = roster.build_seats(GOAL, families=fams_live or tuple(roster.FAMILY_ROSTER))
    cov = roster.coverage_report(seats, fams_live or list(roster.FAMILY_ROSTER))
    seeddiv = roster.seed_divergence(seats)

    preds = make_predictions(seats)
    lock = prereg.seal(preds, seats, now=now, goal=GOAL)
    verify_ok = prereg.verify(lock)
    # 篡改负例：改一条预测的区间 → verify 必须 False
    tampered = json.loads(json.dumps(lock))
    tampered["predictions"][0]["band_lo"] = 0.0
    tampered["predictions"][0]["band_hi"] = 1.0
    tamper_check = prereg.verify(tampered)
    # 先跑后锁负例：带着结果去 seal → 必须拒
    res = synthetic_results(seats, lock)
    try:
        prereg.seal(preds, seats, results=res, now=now, goal=GOAL)
        late_lock = {"rejected": False, "reason": "未拒（缺陷）"}
    except prereg.PreregError as exc:
        late_lock = {"rejected": True, "reason": str(exc)[:160]}
    scores = prereg.score_blind(lock, res, seats=seats)

    cal = calibration()
    led = admission_ledger()
    pop = dedup.population_report(
        [{**c, "id": c["id"]} for c in BASE_CARDS], list(rc["families"]))

    figs = figures(seats, cal, out)

    numbers = {
        "node": roster.NODE_ID,
        "generated_utc": now,
        "goal": GOAL,
        "criteria": {
            "clade_matrix_full": {
                "value": cov["coverage_ratio"],
                "full_grid": cov["full_grid"],
                "n_cells": cov["n_cells"],
                "cells_covered": cov["cells_covered"],
                "min_seats_per_cell": cov["min_seats_per_cell"],
                "artifact": "artifacts/in25_t2/coverage.json",
                "pass": bool(cov["full_grid"]),
            },
            "families_present": {
                "value": len(fams_live),
                "required_min": roster.MIN_FAMILIES,
                "live": fams_live,
                "artifact": "artifacts/in25_t2/roster_check.json",
                "pass": len(fams_live) >= roster.MIN_FAMILIES,
            },
            "framings_present": {
                "value": len({s.framing for s in seats}),
                "required_min": roster.MIN_FRAMINGS,
                "pass": len({s.framing for s in seats}) >= roster.MIN_FRAMINGS,
            },
            "roles_both": {
                "value": cov["role_balance"],
                "pass": all(v > 0 for v in cov["role_balance"].values()),
            },
            "seed_divergence": {**seeddiv, "pass": seeddiv["collisions"] == 0 and (seeddiv["min_hamming"] or 0) >= 16},
            "prereg_locked": {
                "lock_hash": lock["lock_hash"],
                "predictions_sha256": lock["predictions_sha256"],
                "n_predictions": lock["n_predictions"],
                "verified": verify_ok["ok"],
                "tamper_detected": not tamper_check["ok"],
                "late_lock_rejected": late_lock["rejected"],
                "artifact": "artifacts/in25_t2/prereg_lock.json",
                "pass": verify_ok["ok"] and (not tamper_check["ok"]) and late_lock["rejected"],
            },
            "clone_gate": {
                "tpr": cal["tpr_clone_rejected"],
                "fpr": cal["fpr_indep_rejected"],
                "clone_rejection_rate_end_to_end": led["clone_rejection_rate"],
                "clones_rejected": led["clones_rejected"],
                "n_clones": led["n_clones"],
                "fail_closed_all_rejected": led["fail_closed_all_rejected"],
                "artifact": "artifacts/in25_t2/dedup_ledger.json",
                "pass": cal["tpr_clone_rejected"] == 1.0 and led["clone_rejection_rate"] == 1.0
                        and led["fail_closed_all_rejected"],
            },
            "blind_scoring": {
                "hit_rate": scores["hit_rate"],
                "null_hit_rate_mean_band_width": round(
                    sum(p.band_hi - p.band_lo for p in preds) / len(preds), 4),
                "family_hit_rate": scores["family_hit_rate"],
                "n_results": scores["n_results"],
                "time_order_ok": scores["time_order_ok"],
                "lock_verified": scores["lock_verified"],
                "synthetic": True,
                "pass": bool(scores["time_order_ok"] and scores["lock_verified"]),
                "artifact": "artifacts/in25_t2/blind_scores.json",
            },
        },
        "seat_count": len(seats),
        "figures": figs,
    }

    payloads = {
        "roster_check.json": {"live": live, "check": rc, "families_used": fams_live},
        "seats.json": [s.as_dict() for s in seats],
        "coverage.json": cov,
        "seed_divergence.json": seeddiv,
        "prereg_lock.json": lock,
        "prereg_verify.json": {
            "verify": verify_ok,
            "tamper_negative": tamper_check,
            "late_lock_negative": late_lock,
        },
        "blind_scores.json": {**scores, "synthetic": True,
                              "note": "合成 dry-run：结果由种子确定性生成，用于验证时序门与计分器，"
                                      "不代表模型能力"},
        "dedup_ledger.json": led,
        "dedup_calibration.json": cal,
        "population.json": pop,
        "numbers.json": numbers,
    }
    for name, obj in payloads.items():
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True),
                                encoding="utf-8")
    return numbers


if __name__ == "__main__":
    n = main()
    print(json.dumps(n["criteria"], ensure_ascii=False, indent=1))
    fails = [k for k, v in n["criteria"].items() if v.get("pass") is False]
    sys.exit(1 if fails else 0)