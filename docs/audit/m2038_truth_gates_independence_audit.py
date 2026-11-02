"""m2038 — Truth Gates 独立性收敛机制自身的验证欠账审计（攻击复现 + 修复前后对照）。

只读脚本：不改工作树任何业务代码，把「旧的 2-of-3 规则」（git HEAD 版本的
`aggregate_votes` 逐字复刻，仅去掉 docstring）与新规则放在同一批选票上跑，
输出 JSON 供报告程序化取数（零手抄）+ 两张 png。

用法：python3 docs/audit/m2038_truth_gates_independence_audit.py
输出：artifacts/m2038/truth_gates_independence.json, artifacts/m2038/fig_*.png
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services.truth_gates_aggregator import (  # noqa: E402
    aggregate_votes as aggregate_votes_new,
    reconcile_hash as reconcile_hash_new,
    QUORUM, CHEAT_SHARE,
)

OUT_DIR = os.path.join(ROOT, "artifacts", "m2038")


# ─────────────── 旧规则（git HEAD backend/services/truth_gates_aggregator.py 逐字复刻） ───────────────
def aggregate_votes_legacy(votes):
    if not votes:
        return {"total_voters": 0, "tally": {}, "majority_verdict": None,
                "is_confirmed_cheat": False, "is_split": False, "confidence": 0.0}
    tally, confidence_sum = {}, 0.0
    for v in votes:
        verdict = v.get("verdict", "unknown")
        tally[verdict] = tally.get(verdict, 0) + 1
        confidence_sum += float(v.get("confidence", 0.5))
    total = len(votes)
    avg_confidence = confidence_sum / total if total > 0 else 0.0
    majority_verdict, max_count = None, 0
    for verdict, count in tally.items():
        if count > max_count:
            max_count = count
            majority_verdict = verdict
    is_confirmed_cheat = tally.get("cheat", 0) >= 2
    is_split = max_count < 2
    return {"total_voters": total, "tally": tally,
            "majority_verdict": majority_verdict if max_count >= 2 else None,
            "is_confirmed_cheat": is_confirmed_cheat, "is_split": is_split,
            "confidence": round(avg_confidence, 4)}


def reconcile_hash_legacy(a, b):
    match = str(a).lower() == str(b).lower()
    return {"match": match, "is_anomaly": not match}


def V(model, verdict, conf=0.9, ev=None):
    d = {"voter_model": model, "verdict": verdict, "confidence": conf}
    if ev is not None:
        d["evidence_hash"] = ev
    return d


# ─────────────── 攻击场景（每条：注入的选票 + 攻击者试图达成的结论 + 该结论是否合理） ───────────────
SCENARIOS = [
    dict(key="S1_split_1_1_1",
         title="3 票各执一词（cheat/pass/partial）——正确基线，必须判 split 且不打击",
         votes=[V("m1", "cheat"), V("m2", "pass"), V("m3", "partial")],
         attacker_wants="strike", legitimate=False),
    dict(key="S2_two_of_three",
         title="3 票 2 cheat + 1 pass，独立证据哈希不同——应确认打击",
         votes=[V("m1", "cheat", 0.92, "aaa1"), V("m2", "cheat", 0.88, "bbb2"), V("m3", "pass")],
         attacker_wants="strike", legitimate=True),
    dict(key="S3_minority_2_of_10",
         title="10 票中 2 票 cheat、8 票 pass——少数派打击（2/10=20% < 2/3）",
         votes=[V("m1", "cheat"), V("m2", "cheat")] + [V("m%d" % i, "pass") for i in range(3, 11)],
         attacker_wants="strike", legitimate=False),
    dict(key="S4_quorum_2_voters",
         title="只有 2 名投票者 {cheat, cheat}——「2-of-3」分母不足仍打击",
         votes=[V("m1", "cheat"), V("m2", "cheat")],
         attacker_wants="strike", legitimate=False),
    dict(key="S5_tie_2_2_orderA",
         title="平分票 {cheat:2, pass:2}（cheat 先插入）——平局不该有赢家",
         votes=[V("m1", "cheat"), V("m2", "cheat"), V("m3", "pass"), V("m4", "pass")],
         attacker_wants="cheat_majority", legitimate=False),
    dict(key="S5b_tie_2_2_orderB",
         title="同一组平分票换个提交顺序（pass 先插入）——结果必须与 S5 相同",
         votes=[V("m3", "pass"), V("m4", "pass"), V("m1", "cheat"), V("m2", "cheat")],
         attacker_wants="cheat_majority", legitimate=False),
    dict(key="S6_duplicate_voter",
         title="同一 voter_model 投两票 cheat（+1 票 pass）——自我确认",
         votes=[V("rogue", "cheat"), V("rogue", "cheat"), V("m3", "pass")],
         attacker_wants="strike", legitimate=False),
    dict(key="S7_correlated_evidence",
         title="3 票 2 cheat 但共用同一 evidence_hash——同一份证据冒充两次独立复现",
         votes=[V("m1", "cheat", 0.9, "same-hash"), V("m2", "cheat", 0.9, "same-hash"),
                V("m3", "pass")],
         attacker_wants="strike", legitimate=False),
    dict(key="S8_invalid_verdict",
         title="2 票 verdict='unknown'（拼写/注入垃圾）——不该成为「多数裁决」",
         votes=[V("m1", "unknown"), V("m2", "unknown"), V("m3", "pass")],
         attacker_wants="any_majority", legitimate=False),
]


def verdict_of(res):
    return {
        "is_confirmed_cheat": res["is_confirmed_cheat"],
        "is_confirmed_cheat_independent": res.get("is_confirmed_cheat_independent",
                                                  res["is_confirmed_cheat"]),
        "is_split": res["is_split"],
        "majority_verdict": res["majority_verdict"],
        "tally": res["tally"],
        "total_voters": res["total_voters"],
    }


def attack_succeeded(res, wants):
    if wants == "strike":
        # 部署口径：API 的 strike_confirmed = 独立性确认（quorum + 份额 + 证据独立）
        return bool(res.get("is_confirmed_cheat_independent", res["is_confirmed_cheat"]))
    if wants == "cheat_majority":
        return res["majority_verdict"] == "cheat"
    if wants == "any_majority":
        return res["majority_verdict"] is not None
    raise ValueError(wants)


def static_wiring():
    """第 2 支柱（平台侧独立计量）的接线状态：静态证据，只读。"""
    main_py = os.path.join(ROOT, "backend", "main.py")
    src = open(main_py, encoding="utf-8").read()
    mounted = "TruthGatesMeterMiddleware" in src
    meter_src = open(os.path.join(ROOT, "backend", "middleware", "truth_gates_meter.py"),
                     encoding="utf-8").read()
    # flush 只在定义处出现 => 从未被调用
    flush_calls = len(re.findall(r"flush_mission_metrics\(", meter_src)) - 1
    callers = 0
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "backend")):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dirpath, f)
            if p.endswith("truth_gates_meter.py"):
                continue
            if "flush_mission_metrics" in open(p, encoding="utf-8", errors="ignore").read():
                callers += 1
    return {
        "middleware_mounted_in_main": mounted,
        "flush_defined": flush_calls > 0,
        "flush_callers_outside_module": callers,
        "tokens_estimator": "content-length // 4（启发式，非真实 token 计量）",
        "verdict": "STUB" if not mounted else "PARTIAL",
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    for sc in SCENARIOS:
        old = aggregate_votes_legacy(sc["votes"])
        new = aggregate_votes_new(sc["votes"])
        rows.append({
            "key": sc["key"],
            "title": sc["title"],
            "attacker_goal": sc["attacker_wants"],
            "legitimate_conclusion": sc["legitimate"],
            "legacy": verdict_of(old),
            "fixed": verdict_of(new),
            "legacy_attack_succeeded": attack_succeeded(old, sc["attacker_wants"]),
            "fixed_attack_succeeded": attack_succeeded(new, sc["attacker_wants"]),
            "fixed_blocks": new.get("independence_blocks", []),
            "fixed_evidence": new.get("evidence", {}),
        })

    # 只有「攻击者不该得手」的场景才计入检出率
    adversarial = [r for r in rows if not r["legitimate_conclusion"]]
    legit = [r for r in rows if r["legitimate_conclusion"]]
    hash_rows = [
        {"case": "blank vs blank", "self": "", "stored": "",
         "legacy_match": reconcile_hash_legacy("", "")["match"],
         "fixed_match": reconcile_hash_new("", "")["match"],
         "expected": False},
        {"case": "sha256:ABC vs abc（大小写/前缀）", "self": "sha256:ABC", "stored": "abc",
         "legacy_match": reconcile_hash_legacy("sha256:ABC", "abc")["match"],
         "fixed_match": reconcile_hash_new("sha256:ABC", "abc")["match"],
         "expected": True},
        {"case": "abc123 vs def456", "self": "abc123", "stored": "def456",
         "legacy_match": reconcile_hash_legacy("abc123", "def456")["match"],
         "fixed_match": reconcile_hash_new("abc123", "def456")["match"],
         "expected": False},
    ]

    # 平分票的顺序依赖（确定性判据）
    tie_a = [r for r in rows if r["key"] == "S5_tie_2_2_orderA"][0]
    tie_b = [r for r in rows if r["key"] == "S5b_tie_2_2_orderB"][0]
    determinism = {
        "legacy_same_answer_across_orders":
            tie_a["legacy"]["majority_verdict"] == tie_b["legacy"]["majority_verdict"],
        "fixed_same_answer_across_orders":
            tie_a["fixed"]["majority_verdict"] == tie_b["fixed"]["majority_verdict"],
        "legacy_verdicts": [tie_a["legacy"]["majority_verdict"], tie_b["legacy"]["majority_verdict"]],
        "fixed_verdicts": [tie_a["fixed"]["majority_verdict"], tie_b["fixed"]["majority_verdict"]],
    }

    summary = {
        "rule": {"quorum": QUORUM, "cheat_share_min": round(CHEAT_SHARE, 4),
                 "cheat_votes_min": 2, "unique_plurality": 2},
        "n_scenarios": len(rows),
        "n_adversarial": len(adversarial),
        "legacy_escape_rate_pct": round(
            100.0 * sum(r["legacy_attack_succeeded"] for r in adversarial) / len(adversarial), 1),
        "fixed_escape_rate_pct": round(
            100.0 * sum(r["fixed_attack_succeeded"] for r in adversarial) / len(adversarial), 1),
        "legit_strike_preserved": all(r["fixed_attack_succeeded"]
                                      for r in legit if r["attacker_goal"] == "strike"),
        "legit_strike_preserved_legacy": all(r["legacy_attack_succeeded"]
                                             for r in legit if r["attacker_goal"] == "strike"),
        "hash_blank_false_pass_legacy": hash_rows[0]["legacy_match"],
        "hash_blank_false_pass_fixed": hash_rows[0]["fixed_match"],
    }

    payload = {"summary": summary, "scenarios": rows, "hash_cases": hash_rows,
               "determinism": determinism, "pillar2_wiring": static_wiring()}
    with open(os.path.join(OUT_DIR, "truth_gates_independence.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    _figures(payload)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("pillar2:", json.dumps(payload["pillar2_wiring"], ensure_ascii=False))
    print("determinism:", json.dumps(determinism, ensure_ascii=False))


def _figures(payload):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    keys = [r["key"] for r in payload["scenarios"]]
    old = [1 if r["legacy_attack_succeeded"] else 0 for r in payload["scenarios"]]
    new = [1 if r["fixed_attack_succeeded"] else 0 for r in payload["scenarios"]]

    fig, ax = plt.subplots(figsize=(11, 4.6))
    x = np.arange(len(keys))
    ax.bar(x - 0.2, old, 0.4, label="legacy rule (HEAD)", color="#c0392b")
    ax.bar(x + 0.2, new, 0.4, label="m2038 fixed rule", color="#27ae60")
    ax.set_xticks(x)
    ax.set_xticklabels([k.replace("_", "\n", 1) for k in keys], fontsize=7.5)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["attack blocked", "attack SUCCEEDED"])
    ax.set_title("Truth-Gates 2-of-3 aggregation: adversarial ballot sets, before vs after")
    for i, (o, n) in enumerate(zip(old, new)):
        ax.text(i - 0.2, o + 0.03, str(o), ha="center", fontsize=8)
        ax.text(i + 0.2, n + 0.03, str(n), ha="center", fontsize=8)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig_attack_matrix.png"), dpi=160)
    plt.close(fig)

    # 图 2：投票数与 cheat 份额平面上的打击判定域（旧=只看票数，新=份额+法定人数）
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    for n, share in [(n / 10, s / 10) for n in range(1, 11) for s in range(0, n + 1)]:
        pass
    grid = np.zeros((11, 11))
    grid_new = np.zeros((11, 11))
    for nv in range(1, 11):
        for c in range(0, nv + 1):
            votes = [V("v%d" % i, "cheat", 0.9, "h%d" % i) for i in range(c)] + \
                    [V("v%d" % i, "pass", 0.9) for i in range(c, nv)]
            grid[nv, c] = 1 if aggregate_votes_legacy(votes)["is_confirmed_cheat"] else 0
            grid_new[nv, c] = 1 if aggregate_votes_new(votes)["is_confirmed_cheat"] else 0
    ax.imshow(grid_new - grid, origin="lower", cmap="coolwarm", vmin=-1, vmax=1,
              extent=[-0.5, 10.5, 0.5, 10.5])
    ax.plot([0, 10], [0, 10], "k--", lw=0.8)
    ax.plot([0, 10], [0, 6.67], "k:", lw=0.8)
    ax.text(7.2, 9.3, "2/3 cheat-share line", fontsize=7, rotation=34)
    ax.set_xlabel("cheat votes")
    ax.set_ylabel("distinct voters")
    ax.set_title("Strike domain change (red = strike removed, blue = strike added)", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "fig_strike_domain.png"), dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()