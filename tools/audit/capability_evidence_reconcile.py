#!/usr/bin/env python3
"""Capability evidence reconciliation (platform audit, offline, stdlib only).

Problem
-------
`audit_capabilities` advertises 21 platform capabilities with an evidence grade
(REAL / MOCK / STUB / WITHHELD / NOT_SUPPORTED).  The grade is a *claim*; the
platform's own accountability mechanism for substantiating such claims is the
credit ledger (`artifacts/credit_ledger.jsonl`), which only records
independently-issued verifier adjudications ("workers cannot self-certify").

Nothing in the repo checks that the two agree, so a grade can read REAL while no
adjudication exists anywhere.  This tool performs that reconciliation
mechanically and emits a ranked debt list with closing paths.

Tiers
-----
NUMERIC      physics/number-producing capabilities: REAL requires >=1 ledger
             entry with verdict=pass for the matching domain (independent
             verifier, not self-test).
OPERATIONAL  infrastructure capabilities (dev.*, gov.stats, knowledge.docs):
             a numeric adjudication does not apply; they are graded
             OPERATIONAL-PENDING and excluded from the numeric evidence bar.

Rules
-----
R1 UNSUBSTANTIATED_REAL    NUMERIC + REAL + no pass adjudication          P0
R2 CONTRADICTED_BY_LEDGER  latest adjudication for the domain is fail/error
                           while the registry still says REAL              P0
R3 PARTIAL_ADJUDICATION    pass exists but pass_rate < 1.0                  P1
R4 LEDGER_ORPHAN           ledger domain with no registry capability        P2
R5 INVALID_ADJUDICATION    adjudication缺少 verifier/evidence hash 或
                           questions_total == 0（自证不可计入）            P1
R6 SUB-BAR_GRADE           grade in MOCK/STUB/NOT_SUPPORTED (informational) P3
R7 STALE_EVIDENCE          adjudication older than --max-age-days            P2

Usage
-----
    python3 tools/audit/capability_evidence_reconcile.py \
        --ledger artifacts/credit_ledger.jsonl \
        --out artifacts/audit/capability_evidence_reconcile.json \
        --md docs/audit/CAPABILITY_EVIDENCE_RECONCILE.md
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# Registry snapshot: verbatim output of the platform `audit_capabilities`
# tool, retrieved 2026-11-01T07:54Z.  Snapshot (not live probe) so the audit is
# reproducible offline; refresh by re-running the tool and updating the block.
# --------------------------------------------------------------------------
REGISTRY_SNAPSHOT = {
    "source": "audit_capabilities tool (platform :8010), snapshot 2026-11-01T07:54Z",
    "capabilities": [
        ("ai.predict", "NOT_SUPPORTED"),
        ("calc.resistance", "REAL"),
        ("calc.stability", "REAL"),
        ("dev.gate_5415", "REAL"),
        ("dev.git", "REAL"),
        ("dev.logs", "REAL"),
        ("dev.nginx_audit", "REAL"),
        ("dev.probe", "REAL"),
        ("dev.restart", "REAL"),
        ("dev.services", "REAL"),
        ("geometry.generate", "REAL"),
        ("geometry.hydro_curves", "REAL"),
        ("geometry.list_models", "REAL"),
        ("geometry.verify_official", "REAL"),
        ("gov.stats", "REAL"),
        ("knowledge.docs", "REAL"),
        ("methods.computation", "NOT_SUPPORTED"),
        ("misc.stubs", "STUB"),
        ("propeller.open_water", "REAL"),
        ("shiphull.generate", "REAL"),
        ("stdmodels.lookup", "REAL"),
    ],
}

# capability -> credit-ledger domain(s) that would substantiate it
DOMAIN_MAP = {
    "ai.predict": ["ai"],
    "calc.resistance": ["resistance"],
    "calc.stability": ["stability", "stability_imo"],
    "geometry.generate": ["geometry"],
    "geometry.hydro_curves": ["hydrostatics", "geometry"],
    "geometry.list_models": ["geometry"],
    "geometry.verify_official": ["geometry", "hydrostatics"],
    "methods.computation": ["methods"],
    "propeller.open_water": ["propeller", "open_water"],
    "shiphull.generate": ["geometry", "hullform"],
    "stdmodels.lookup": ["stdmodels"],
}

OPERATIONAL_PREFIXES = ("dev.", "gov.", "knowledge.")

# closing path per capability (what a verifier mission must do to substantiate)
CLOSING_PATH = {
    "calc.resistance": "verifier: Holtrop-Mennen 分项 vs 公开船模试验/ITTC 基准，逐项误差 <3%",
    "calc.stability": "verifier: 矩形浮箱解析 GZ(GM/KB/BM/面积) 复核，需修复 D1–D7 后复判",
    "propeller.open_water": "verifier: Wageningen B-series 多项式 K_T/K_Q/eta 逐 J 点复核",
    "geometry.hydro_curves": "verifier: 排水量闭合 Cb/L/B/T 与 BM=I/∇ 定义级恒等式复核",
    "geometry.verify_official": "verifier: 官方主尺度表复算 + 排水量闭合门",
    "geometry.generate": "verifier: 生成船型 Cb 闭合与体积积分独立复算",
    "geometry.list_models": "verifier: 目录清单与磁盘工件一致性核对",
    "shiphull.generate": "verifier: 生成船型闭合/自交检查",
    "stdmodels.lookup": "verifier: 标准船型主尺度与公开表逐字段比对",
    "methods.computation": "先补齐能力（当前 NOT_SUPPORTED）",
    "ai.predict": "先补齐能力（当前 NOT_SUPPORTED）",
}

NUMERIC_GRADES = {"REAL"}


def domain_matches(cap: str, domain: str) -> bool:
    return domain in DOMAIN_MAP.get(cap, [])


def classify_tier(cap: str) -> str:
    return "OPERATIONAL" if cap.startswith(OPERATIONAL_PREFIXES) else "NUMERIC"


def load_ledger(path: Path) -> list[dict]:
    entries: list[dict] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"__parse_error__": line[:120]})
    return entries


def is_valid_adjudication(e: dict) -> bool:
    if "__parse_error__" in e:
        return False
    if not e.get("verifier_id") or not str(e.get("verifier_id")).strip():
        return False
    if int(e.get("questions_total") or 0) <= 0:
        return False
    if not e.get("evidence_hash") or len(str(e.get("evidence_hash"))) < 32:
        return False
    return True


def reconcile(ledger: list[dict], registry: dict | None = None,
              now: float | None = None, max_age_days: float = 30.0) -> dict:
    registry = registry or REGISTRY_SNAPSHOT
    now = time.time() if now is None else now
    caps = [(c, g) for c, g in registry["capabilities"]]

    debt: list[dict] = []
    rows: list[dict] = []

    known_domains = {d for c, _ in caps for d in DOMAIN_MAP.get(c, [])}

    for cap, grade in caps:
        tier = classify_tier(cap)
        mine = [e for e in ledger if domain_matches(cap, e.get("domain", ""))]
        valid = [e for e in mine if is_valid_adjudication(e)]
        passes = [e for e in valid if e.get("verdict") == "pass"]
        fails = [e for e in valid if e.get("verdict") in ("fail", "error")]

        status = "NOT_APPLICABLE"
        if tier == "OPERATIONAL":
            status = "OPERATIONAL-PENDING"
            if grade == "REAL":
                debt.append({
                    "rule": "R0_OPERATIONAL_UNADJUDICATED", "priority": 2,
                    "capability": cap,
                    "detail": "infra 能力标 REAL 但无运行探针证据；numeric 证据栏不适用",
                    "closing_path": "infra 席: 探针日志 + 近 24h 成功率入账",
                })
        elif grade not in NUMERIC_GRADES:
            status = "SUB-BAR_GRADE"
            debt.append({
                "rule": "R6_SUB_BAR_GRADE", "priority": 3, "capability": cap,
                "detail": f"grade={grade} 低于证据栏；合规但需在能力清单显式标注",
                "closing_path": CLOSING_PATH.get(cap, "补齐能力后重判"),
            })
        else:
            # grade == REAL, numeric
            if not passes:
                status = "UNSUBSTANTIATED"
                debt.append({
                    "rule": "R1_UNSUBSTANTIATED_REAL", "priority": 0,
                    "capability": cap,
                    "detail": f"grade=REAL 但台账无 verdict=pass 裁决（拿到 {len(valid)} 条有效裁决）",
                    "closing_path": CLOSING_PATH.get(cap, "派 verifier 席复核并对参考值"),
                })
            else:
                status = "SUBSTANTIATED"
                latest = max(passes, key=lambda e: float(e.get("created_at") or 0))
                if float(latest.get("pass_rate") or 0.0) < 1.0:
                    status = "PARTIAL"
                    debt.append({
                        "rule": "R3_PARTIAL_ADJUDICATION", "priority": 1,
                        "capability": cap,
                        "detail": f"最新 pass 裁决 pass_rate={latest.get('pass_rate')} < 1.0",
                        "closing_path": "补跑失败项",
                    })
                if now - float(latest.get("created_at") or 0) > max_age_days * 86400:
                    debt.append({
                        "rule": "R7_STALE_EVIDENCE", "priority": 2, "capability": cap,
                        "detail": f"最新裁决距今 >{max_age_days:.0f} 天",
                        "closing_path": "复跑裁决刷新证据",
                    })
            if fails:
                latest_fail = max(fails, key=lambda e: float(e.get("created_at") or 0))
                latest_pass = max(passes, key=lambda e: float(e.get("created_at") or 0)) if passes else None
                if latest_pass is None or float(latest_fail.get("created_at") or 0) > float(latest_pass.get("created_at") or 0):
                    status = "CONTRADICTED"
                    debt.append({
                        "rule": "R2_CONTRADICTED_BY_LEDGER", "priority": 0,
                        "capability": cap,
                        "detail": f"最新裁决 verdict={latest_fail.get('verdict')}，但 grade 仍为 REAL",
                        "closing_path": "降级为 WITHHELD 或修复后复判",
                    })

        rows.append({
            "capability": cap, "grade": grade, "tier": tier, "status": status,
            "ledger_entries": len(mine), "valid_entries": len(valid),
            "pass_entries": len(passes), "fail_entries": len(fails),
        })

    # R4 orphans / R5 invalid entries (entry-level rules)
    for e in ledger:
        if not is_valid_adjudication(e):
            debt.append({
                "rule": "R5_INVALID_ADJUDICATION", "priority": 1,
                "capability": f"ledger::{e.get('candidate_id', '?')}",
                "detail": "缺少 verifier_id/evidence_hash 或 questions_total=0（自证不计入）",
                "closing_path": "作废该条并重跑独立裁决",
            })
        elif e.get("domain") not in known_domains:
            debt.append({
                "rule": "R4_LEDGER_ORPHAN", "priority": 2,
                "capability": f"domain::{e.get('domain')}",
                "detail": "台账 domain 在能力注册表中无对应能力，证据无法归属",
                "closing_path": "在能力注册表补登或作废该裁决",
            })

    numeric = [r for r in rows if r["tier"] == "NUMERIC"]
    substantiated = [r for r in numeric if r["status"] == "SUBSTANTIATED"]
    coverage = len(substantiated) / len(numeric) if numeric else math.nan

    debt.sort(key=lambda d: (d["priority"], d["rule"], d["capability"]))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "registry_source": registry["source"],
        "ledger_entries": len(ledger),
        "summary": {
            "capabilities": len(rows),
            "numeric_capabilities": len(numeric),
            "numeric_real": len([r for r in numeric if r["grade"] == "REAL"]),
            "numeric_substantiated": len(substantiated),
            "numeric_evidence_coverage": round(coverage, 4) if numeric else None,
            "operational": len([r for r in rows if r["tier"] == "OPERATIONAL"]),
            "debt_items": len(debt),
            "debt_by_priority": {
                str(p): len([d for d in debt if d["priority"] == p]) for p in (0, 1, 2, 3)
            },
        },
        "rows": rows,
        "debt": debt,
    }


def render_markdown(result: dict, ledger_path: str) -> str:
    s = result["summary"]
    out = []
    out.append("# 能力证据台账对账（平台审计）\n")
    out.append(f"- 生成时间：{result['generated_at']}")
    out.append(f"- 能力注册表来源：{result['registry_source']}")
    out.append(f"- 台账：`{ledger_path}`（{result['ledger_entries']} 条）\n")
    out.append("## 摘要\n")
    out.append("| 指标 | 值 |")
    out.append("|---|---|")
    out.append(f"| 能力总数 | {s['capabilities']} |")
    out.append(f"| NUMERIC 能力 | {s['numeric_capabilities']} |")
    out.append(f"| NUMERIC 且标 REAL | {s['numeric_real']} |")
    out.append(f"| NUMERIC 已获独立裁决 | {s['numeric_substantiated']} |")
    cov = s["numeric_evidence_coverage"]
    out.append(f"| **数值证据覆盖率** | **{cov:.1%}** |" if cov is not None else "| 数值证据覆盖率 | n/a |")
    out.append(f"| 债务项 | {s['debt_items']}（P0={s['debt_by_priority']['0']}, P1={s['debt_by_priority']['1']}, "
               f"P2={s['debt_by_priority']['2']}, P3={s['debt_by_priority']['3']}） |\n")
    out.append("## 逐能力状态\n")
    out.append("| 能力 | 分级 | 层级 | 状态 | 台账条数 | pass | fail |")
    out.append("|---|---|---|---|---|---|---|")
    for r in result["rows"]:
        out.append(f"| `{r['capability']}` | {r['grade']} | {r['tier']} | {r['status']} | "
                   f"{r['ledger_entries']} | {r['pass_entries']} | {r['fail_entries']} |")
    out.append("\n## 债务清单（按优先级）\n")
    out.append("| 优先级 | 规则 | 对象 | 说明 | 关闭路径 |")
    out.append("|---|---|---|---|---|")
    for d in result["debt"]:
        out.append(f"| P{d['priority']} | {d['rule']} | `{d['capability']}` | {d['detail']} | {d['closing_path']} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default="artifacts/credit_ledger.jsonl")
    ap.add_argument("--out", default="artifacts/audit/capability_evidence_reconcile.json")
    ap.add_argument("--md", default=None)
    ap.add_argument("--max-age-days", type=float, default=30.0)
    ap.add_argument("--fail-on-p0", action="store_true",
                    help="exit 1 if any P0 debt exists (CI gate mode)")
    args = ap.parse_args(argv)

    ledger = load_ledger(Path(args.ledger))
    result = reconcile(ledger, max_age_days=args.max_age_days)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        md = Path(args.md)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render_markdown(result, args.ledger), encoding="utf-8")
    s = result["summary"]
    print(json.dumps(s, ensure_ascii=False))
    if args.fail_on_p0 and s["debt_by_priority"]["0"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())