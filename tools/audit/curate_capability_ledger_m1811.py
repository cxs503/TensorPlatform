#!/usr/bin/env python3
"""能力证据台账 curation v2（m1811）：把仓内已合并复核文档入账，重裁三条分歧能力。

背景
----
台账 v1（m1567 产物）只吃在线报告语料，且**其 artifacts 从未入库**——
`artifacts/capability_ledger/capability_ledger.json` 在 HEAD 上不存在，
于是 `tools/audit/capability_ledger.py --check` 直接退出 2，
`tests/test_capability_ledger.py` 15 条测试全红（13 error + 2 failed）。
本脚本做三件事，全部程序化、可重复执行（幂等）：

1. `as_of` 归一：所有在线报告证据的时间戳改为语料库里该报告的 `created_at`
   （不再用手写日期；手写日期无法机器核验，且会让「最新证据」排序失真）；
2. 入账：把 5 篇已合并复核文档作为新证据行挂到对应能力/覆盖缺口上，
   引文一律用正则 + 逐字命中（C3），文档正文 sha256 由 corpus builder 落库（C10）；
3. 防腐朽：写入 `curation` 段（as_of / max_evidence_age_days / must_consume），
   让 C8（证据过期）、C9（已合并复核文档必须入账或豁免）成为机器判据。

用法
----
    python3 tools/audit/curate_capability_ledger_m1811.py --write    # 写回台账
    python3 tools/audit/curate_capability_ledger_m1811.py --check     # 只校验幂等
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"
CORPUS = ROOT / "tests" / "fixtures" / "capability_ledger" / "reports_corpus.json"

CURATION = {
    "revision": "m1811",
    "as_of": "2026-11-02T00:00:00+00:00",
    "max_evidence_age_days": 30,
    "rationale": (
        "v1 只吃在线报告，已合并的复核文档对台账不可见 → 台账把已修复的能力永久钉在旧判决上；"
        "v2 起把仓内已合并复核文档作为第二路证据，并用 C8/C9/C10 三条判据防止再次腐朽。"
    ),
    "must_consume": [],  # 由 build() 从 corpus builder 的 MANIFEST 填入（单一真值）
}

# ── 新增证据行（capability id -> 证据列表） ──────────────────────────────
NEW_EVIDENCE = {
    "propeller.open_water": [
        {
            "report_id": "doc:docs/verification/PROPULSION_OPENWATER_M1584.md",
            "stance": "contradicts",
            "asserted_level": "MOCK",
            "quote_match": r"证据门 2/8 通过",
            "note": (
                "m1584 用不依赖文献数值的硬判据重裁：端点自报 eta0 越过动量理论上限 "
                "eta_i=2/(1+sqrt(1+CT))，最大越界 0.1421（物理不可能）；"
                "另有 KQ/螺距/盘面比零敏感等 5 项非物理。修复单 1556 failed → 不得回填 REAL。"
            ),
        }
    ],
    "calc.stability": [
        {
            "report_id": "doc:docs/reports/m1564_stability_adjudication.md",
            "stance": "supports_real",
            "asserted_level": "REAL",
            "quote_match": r"受证合成（certified synthesis）",
            "note": (
                "m1564 平行实现裁决：域内三实现 vs 箱型驳船独立积分基准差在 1e-6 % 量级，"
                "正典全程停在舍入噪声（≤1e-3 %），并把有效域改为 atan(2*min(T,D-T)/B) 解析导出、"
                "域外截断不再抛无界臂 → 报告 1136 的 24 项 FAIL 已被结构性清偿。"
            ),
        }
    ],
    "calc.resistance": [
        {
            "report_id": "doc:docs/verification/inline_resistance_audit_2026-11-01.md",
            "stance": "contradicts",
            "asserted_level": "WITHHELD",
            "quote_match": r"FAIL 8 · PASS 1 · WITHHELD 1",
            "note": (
                "10 条判据 12 个数据集案例：FAIL 8 / PASS 1 / WITHHELD 1。"
                "inline 湿表面积 vs 解析长球体 -20.9%；SUBOFF inline Ct 误差到 +31.5%；"
                "数据集 Rt 与 Froude 恒等式自身不自洽（+852% / -462.9%）→ 基准侧也不可判据。"
                "自述 REAL 被推翻，改记 OVERCLAIM（比 v1 的 REVERIFY 更严）。"
            ),
        }
    ],
}

# ── 新增注册表覆盖缺口（域在跑、账上无条目） ────────────────────────────
NEW_GAPS = [
    {
        "domain": "结构/structure",
        "gap": (
            "注册表 21 项里没有结构能力条目，但平台有 4 个结构端点（板格屈曲/疲劳/板厚屈曲/极限强度），"
            "且 m1590 已做闭式基准复核 → 注册表覆盖缺口。"
        ),
        "evidence": [
            {
                "report_id": "doc:docs/reports/m1590_structure_verification_debt.md",
                "stance": "contradicts",
                "asserted_level": "MOCK",
                "quote_match": r"16/21 项判定 FAIL",
                "note": (
                    "21 项检查仅 5 项 PASS：屈曲容量/剪屈曲/Miner 求和为真实现；"
                    "ultimate_strength 与 fatigue_life 判 MOCK/STUB（actual_stress 恒 = 0.5 sigma_y，"
                    "与载荷无关）。结构域不可用于疲劳寿命与极限强度定量决策。"
                ),
            }
        ],
        "action": (
            "注册表新增 structure.buckling（可 REAL）与 structure.fatigue / structure.ultimate"
            "（记 MOCK），并按 m1590 的契约层建议让 actual_stress 缺输入时返回 WITHHELD 而不是 0.5 sigma_y。"
        ),
    },
    {
        "domain": "振动噪声/vibration-noise",
        "gap": (
            "注册表无振动噪声域条目；仓内 m1493 已实现梁/板固有频率、隔振传递率、板隔声、"
            "声级合成共 9 个解析端点并复核到 <3%，但**未挂进在线服务** → 在线仍 NOT_SUPPORTED。"
        ),
        "evidence": [
            {
                "report_id": "doc:docs/audits/vibration_noise_domain_audit.md",
                "stance": "contradicts",
                "asserted_level": "NOT_SUPPORTED",
                "quote_match": r"在线平台当前仍为 NOT_SUPPORTED，仓内为 REAL",
                "note": (
                    "梁 lambda1..4 对 Blevins(1979) 误差 1e-7 %；板 7 模态对 Rayleigh 商 0.115 %；"
                    "A 计权对 IEC 61672-1 0.090 dB。能力 REAL 但路由未挂载 → 在线 404（控制组 405）。"
                ),
            }
        ],
        "action": (
            "把 backend/api/domain/ship_vibration.py 的 router 挂进主站并重启（属运维动作），"
            "挂载后本行寄存器新增 vibration.beam/plate、noise.panel_tl 并把在线等级升 REAL。"
        ),
    },
]


def _manifest() -> list[dict]:
    """从 corpus builder 读入账清单（单一真值，不在两处各写一份）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "capability_corpus_builder", ROOT / "tools/audit/build_capability_corpus_docs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MANIFEST


def build() -> dict:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    # 1) as_of 归一（在线报告 → 语料库 created_at）
    for cap in ledger["capabilities"] + ledger.get("unregistered_domains", []):
        for ev in cap.get("evidence", []):
            rid = str(ev["report_id"])
            if rid.isdigit() and rid in corpus["reports"]:
                ev["as_of"] = corpus["reports"][rid]["created_at"][:25]

    # 2) 入账新证据（幂等：按 report_id 去重后 append）
    for cap in ledger["capabilities"]:
        for ev in NEW_EVIDENCE.get(cap["id"], []):
            if not any(str(e["report_id"]) == str(ev["report_id"]) for e in cap["evidence"]):
                cap["evidence"].append({**ev, "as_of": corpus["docs"][ev["report_id"]]["as_of"]})

    # 3) 两条新覆盖缺口（幂等：按 domain 去重）
    have = {g["domain"] for g in ledger.get("unregistered_domains", [])}
    for gap in NEW_GAPS:
        if gap["domain"] in have:
            continue
        gap = json.loads(json.dumps(gap, ensure_ascii=False))
        for ev in gap["evidence"]:
            ev["as_of"] = corpus["docs"][ev["report_id"]]["as_of"]
        ledger.setdefault("unregistered_domains", []).append(gap)

    # 4) 防腐朽段
    cur = dict(CURATION)
    cur["must_consume"] = [{"path": m["path"], "why": m["why"]} for m in _manifest()]
    ledger["curation"] = cur

    # 5) schema 版本 / 证据来源声明
    ledger["schema_version"] = "2.0"
    ledger["evidence_source"] = {
        "kind": "http+repo",
        "endpoints": [
            "GET http://127.0.0.1:8010/api/v1/tensor/reports/{id}（在线报告，id 为数字）",
            "仓内已合并复核文档（id 形如 doc:docs/verification/xxx.md）",
        ],
        "corpus": "tests/fixtures/capability_ledger/reports_corpus.json",
        "corpus_builder": "tools/audit/build_capability_corpus_docs.py",
        "retrieved_at": "2026-11-01T07:52:00Z",
    }
    rules = [r for r in ledger["reconciliation_rules"] if "最新" not in r] + [
        "『最新』= as_of 时间戳最大；同一时间戳取证据种类（在线报告=1 > 仓内文档=0），再取 id 字典序。",
        "仓内文档当且仅当被 C10 校验过正文 sha256 才可作为证据（文档被改 → 旧引文失效）。",
    ]
    ledger["reconciliation_rules"] = list(dict.fromkeys(rules))
    return ledger


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    ledger = build()
    new = json.dumps(ledger, ensure_ascii=False, indent=1) + "\n"
    old = LEDGER.read_text(encoding="utf-8") if LEDGER.exists() else ""
    in_sync = json.loads(new) == json.loads(old) if old else False
    if args.check:
        print("SYNCED" if in_sync else "STALE")
        return 0 if in_sync else 1
    if args.write:
        LEDGER.write_text(new, encoding="utf-8")
        print(f"ledger 已写入（{len(ledger['capabilities'])} 能力 / "
              f"{len(ledger['unregistered_domains'])} 覆盖缺口）")
        return 0
    print("给 --write 或 --check")
    return 2


if __name__ == "__main__":
    sys.exit(main())