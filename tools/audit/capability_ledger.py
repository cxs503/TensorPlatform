#!/usr/bin/env python3
"""能力证据台账对账器（capability evidence ledger reconciler）。

背景 / 问题
-----------
平台的能力自述等级（audit_capabilities 的 21 项 REAL/MOCK/STUB/NOT_SUPPORTED）
与平台自己产出的审计报告（:8010 /api/v1/tensor/reports）之间存在系统性背离：
例如 propeller.open_water 自述 REAL，但报告 1128（6/6 判据不通过）与 1133
（0/7）都裁决 MOCK；calc.stability 自述 REAL，报告 1136 判 24 项 FAIL。
这些背离此前只散落在各报告的正文里，没有可机器裁决的单一真值文件。

本工具做三件事（全部只读，除自己的产物）：
1. 解析台账里每条证据的 `quote_match` 正则，**在报告正文里实例化**
   （证据必须逐字可查，禁止手抄/转述），落盘命中行与 sha256 前缀；
2. 按台账声明的 `level_rank` + `reconciliation_rules` 复算
   reconciled_level / divergence（OVERCLAIM / REVERIFY / UNVERIFIED / OK）；
3. 跑 7 条自检 C1~C7；任何一条不过 → 退出码非 0，并打印违规项。

用法
----
    python3 tools/audit/capability_ledger.py --check          # 离线对账（CI 用）
    python3 tools/audit/capability_ledger.py --check --write  # 同时刷新 artifacts/
    python3 tools/audit/capability_ledger.py --refresh        # 从 :8010 重取证据正文
                                                              # 再对账（需要 token）

token 解析顺序：--token-file（默认 /tmp/tok）→ $TENSOR_TOKEN → 用
$TENSOR_AGENT_USER/$TENSOR_AGENT_PASS 现场登录 $TENSOR_API_BASE。

退出码：0 = 全部自检通过；1 = 有违规；2 = 输入缺失/无法加载。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json"
FIXTURES = ROOT / "tests" / "fixtures" / "capability_ledger"
CAP_SNAPSHOT = FIXTURES / "capabilities_snapshot.json"
CORPUS = FIXTURES / "reports_corpus.json"
OUT_DIR = ROOT / "artifacts" / "capability_ledger"
RESOLVED = OUT_DIR / "capability_ledger_resolved.json"
TABLE_MD = OUT_DIR / "capability_ledger_table.md"

DEFAULT_API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
QUOTE_MAXLEN = 220


# ── 载入 ────────────────────────────────────────────────────────────────
def _load(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _rank_map(ledger) -> dict:
    return dict(ledger["level_rank"])


def rank(levels, level: str) -> int:
    if level not in levels:
        raise KeyError(f"未登记的等级 {level!r}（应在 ledger.level_rank 里声明）")
    return levels[level]


# ── 证据实例化（quote 必须逐字命中） ────────────────────────────────────
def resolve_quote(report_md: str, pattern: str) -> dict:
    """在报告正文里搜 quote_match；命中则返回所在整行（截断）+ 行 sha256。"""
    rx = re.compile(pattern)
    m = rx.search(report_md)
    if not m:
        return {"hit": False, "line": "", "line_sha256": "", "match": ""}
    start = report_md.rfind("\n", 0, m.start()) + 1
    end = report_md.find("\n", m.end())
    end = len(report_md) if end == -1 else end
    line = report_md[start:end].strip()
    return {
        "hit": True,
        "match": m.group(0),
        "line": line[:QUOTE_MAXLEN],
        "line_sha256": hashlib.sha256(line.encode()).hexdigest()[:16],
    }


# ── 对账 ────────────────────────────────────────────────────────────────
def _latest(evidence: list) -> dict | None:
    if not evidence:
        return None
    return max(evidence, key=lambda e: (e.get("as_of", ""), int(e.get("report_id", 0))))


def reconcile_one(cap: dict, levels: dict) -> dict:
    claimed = cap["claimed_level"]
    latest = _latest(cap["evidence"])
    if latest is None:
        reconciled = "WITHHELD"
        divergence = "UNVERIFIED" if rank(levels, claimed) > rank(levels, "WITHHELD") else "OK"
        basis = "无证据"
    elif latest["stance"] == "supports_real":
        reconciled, basis = "REAL", f"最新证据 {latest['report_id']} 支持 REAL"
        divergence = "OK" if rank(levels, claimed) <= rank(levels, "REAL") else "OK"
    else:
        reconciled = latest["asserted_level"]
        basis = f"最新证据 {latest['report_id']} stance={latest['stance']} → {reconciled}"
        divergence = (
            "OVERCLAIM"
            if rank(levels, claimed) > rank(levels, reconciled) and latest["stance"] == "contradicts"
            else "REVERIFY"
            if rank(levels, claimed) > rank(levels, reconciled)
            else "OK"
        )
    return {
        "id": cap["id"],
        "domain": cap.get("domain", ""),
        "claimed_level": claimed,
        "reconciled_level": reconciled,
        "divergence": divergence,
        "basis": basis,
        "latest_evidence_report": (latest or {}).get("report_id"),
        "evidence_count": len(cap["evidence"]),
    }


def check(ledger: dict) -> tuple[dict, list]:
    """返回 (对账结果, 违规列表)。违规 = 字符串列表（C 编号 + 说明）。"""
    levels = _rank_map(ledger)
    snapshot = _load(CAP_SNAPSHOT)
    corpus = _load(CORPUS)["reports"]
    violations: list[str] = []

    # C1 快照与台账逐项对齐
    snap_caps = snapshot["capabilities"]
    ledger_ids = [c["id"] for c in ledger["capabilities"]]
    if len(ledger_ids) != len(set(ledger_ids)):
        violations.append("C1 台账内部 capability id 重复")
    missing = sorted(set(snap_caps) - set(ledger_ids))
    extra = sorted(set(ledger_ids) - set(snap_caps))
    if missing:
        violations.append(f"C1 快照里有能力未入账: {missing}")
    if extra:
        violations.append(f"C1 台账里有能力不在快照中: {extra}")

    # C5 台账记的 claimed_level 必须等于快照（注册表翻转 → 必须重新 curation）
    for cap in ledger["capabilities"]:
        snap_level = snap_caps.get(cap["id"])
        if snap_level is not None and snap_level != cap["claimed_level"]:
            violations.append(
                f"C5 {cap['id']} 注册表等级已翻转: 台账={cap['claimed_level']} 快照={snap_level}（需重跑 curation）"
            )

    resolved_caps, evidence_rows = [], []
    for cap in ledger["capabilities"]:
        # C2/C3 每条证据的 report_id 必须可解析、quote 必须逐字命中
        resolved_evidence = []
        for ev in cap["evidence"]:
            rid = str(ev["report_id"])
            if rid not in corpus:
                violations.append(f"C2 {cap['id']} 引用了语料库外报告 {rid}")
                resolved_evidence.append({**ev, "quote": {"hit": False, "line": "", "line_sha256": ""}})
                continue
            q = resolve_quote(corpus[rid]["markdown"], ev["quote_match"])
            if not q["hit"]:
                violations.append(
                    f"C3 {cap['id']} 证据 {rid} 的 quote_match 在正文中不存在: {ev['quote_match']!r}"
                )
            resolved_evidence.append({**ev, "quote": q})
            evidence_rows.append(
                {
                    "capability": cap["id"],
                    "report_id": int(rid),
                    "stance": ev["stance"],
                    "asserted_level": ev["asserted_level"],
                    "line_sha256": q["line_sha256"],
                }
            )
        row = reconcile_one({**cap, "evidence": resolved_evidence}, levels)
        # C4 分歧项必须带 owner 动作句
        if row["divergence"] in ("OVERCLAIM", "REVERIFY") and not (cap.get("action") or "").strip():
            violations.append(f"C4 {cap['id']} divergence={row['divergence']} 但台账没有 action 句")
        resolved_caps.append({**row, "evidence": resolved_evidence})

    # C6 未注册域的缺口证据同样要逐字命中
    resolved_gaps = []
    for gap in ledger.get("unregistered_domains", []):
        ev_out = []
        for ev in gap["evidence"]:
            rid = str(ev["report_id"])
            if rid not in corpus:
                violations.append(f"C6 未注册域 {gap['domain']} 引用语料库外报告 {rid}")
                continue
            q = resolve_quote(corpus[rid]["markdown"], ev["quote_match"])
            if not q["hit"]:
                violations.append(
                    f"C6 未注册域 {gap['domain']} 证据 {rid} 的 quote_match 不存在: {ev['quote_match']!r}"
                )
            ev_out.append({**ev, "quote": q})
        latest = _latest(gap["evidence"])
        resolved_gaps.append(
            {
                "domain": gap["domain"],
                "gap": gap["gap"],
                "action": gap.get("action", ""),
                "latest_evidence_report": (latest or {}).get("report_id"),
                "latest_asserted_level": (latest or {}).get("asserted_level"),
                "evidence": ev_out,
            }
        )

    tally = {"OVERCLAIM": 0, "REVERIFY": 0, "UNVERIFIED": 0, "OK": 0}
    for row in resolved_caps:
        tally[row["divergence"]] += 1

    result = {
        "schema_version": ledger["schema_version"],
        "summary": {
            "capabilities": len(resolved_caps),
            "with_evidence": sum(1 for c in resolved_caps if c["evidence_count"]),
            "without_evidence": sum(1 for c in resolved_caps if not c["evidence_count"]),
            "divergence_tally": tally,
            "unregistered_domains": len(resolved_gaps),
            "evidence_rows": len(evidence_rows),
            "violations": len(violations),
        },
        "capabilities": [
            {k: v for k, v in c.items() if k != "evidence"} | {
                "evidence_refs": [
                    {
                        "report_id": e["report_id"],
                        "stance": e["stance"],
                        "asserted_level": e["asserted_level"],
                        "quote_hit": e["quote"]["hit"],
                        "line_sha256": e["quote"]["line_sha256"],
                        "line": e["quote"]["line"],
                    }
                    for e in c["evidence"]
                ]
            }
            for c in resolved_caps
        ],
        "unregistered_domains": resolved_gaps,
        "violations": violations,
    }

    # C7 漂移闸：本次复算必须等于已入库快照（注册表/证据/台账任一变动都会破闸，
    #    迫使重新 curation 并显式重写快照，避免"对账结果偷偷变好/变坏"）。
    if RESOLVED.exists():
        stored = json.loads(RESOLVED.read_text(encoding="utf-8"))
        canon = lambda d: json.dumps(
            {k: d.get(k) for k in ("summary", "capabilities", "unregistered_domains")},
            ensure_ascii=False,
            sort_keys=True,
        )
        if canon(stored) != canon(result):
            violations.append(
                "C7 对账结果与已入库快照不一致（注册表/证据正文/台账发生漂移），需重新 curation 后 --write 重写快照"
            )
    result["violations"] = violations
    return result, violations


# ── 渲染 ────────────────────────────────────────────────────────────────
def render_table(result: dict) -> str:
    s = result["summary"]
    t = s["divergence_tally"]
    lines = [
        "# 能力证据台账对账表（自动生成，勿手改）",
        "",
        f"- 能力数：{s['capabilities']}（有证据 {s['with_evidence']} / 无证据 {s['without_evidence']}）",
        f"- 分歧：OVERCLAIM {t['OVERCLAIM']} · REVERIFY {t['REVERIFY']} · UNVERIFIED {t['UNVERIFIED']} · OK {t['OK']}",
        f"- 未注册域缺口：{s['unregistered_domains']}；证据行：{s['evidence_rows']}；违规：{s['violations']}",
        "",
        "| 能力 | 域 | 自述 | 对账 | 分歧 | 最新证据 | 依据 |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in result["capabilities"]:
        lines.append(
            f"| `{c['id']}` | {c['domain']} | {c['claimed_level']} | **{c['reconciled_level']}** | "
            f"{c['divergence']} | {c['latest_evidence_report'] or '-'} | {c['basis']} |"
        )
    if result["unregistered_domains"]:
        lines += ["", "## 注册表覆盖缺口", "", "| 域 | 最新证据判定 | 缺口 | 动作 |", "|---|---|---|---|"]
        for g in result["unregistered_domains"]:
            lines.append(
                f"| {g['domain']} | {g['latest_asserted_level']}（报告 {g['latest_evidence_report']}） | "
                f"{g['gap']} | {g['action']} |"
            )
    if result["violations"]:
        lines += ["", "## 违规", ""] + [f"- {v}" for v in result["violations"]]
    return "\n".join(lines) + "\n"


# ── 在线刷新证据正文 ────────────────────────────────────────────────────
def _token(arg_file: str | None) -> str:
    if arg_file and Path(arg_file).exists():
        return Path(arg_file).read_text().strip()
    if os.environ.get("TENSOR_TOKEN"):
        return os.environ["TENSOR_TOKEN"].strip()
    user, pwd = os.environ.get("TENSOR_AGENT_USER"), os.environ.get("TENSOR_AGENT_PASS")
    if not (user and pwd):
        raise RuntimeError("无 token：给 --token-file，或设置 TENSOR_TOKEN / TENSOR_AGENT_USER+PASS")
    req = urllib.request.Request(
        DEFAULT_API + "/auth/login",
        method="POST",
        data=json.dumps({"username": user, "password": pwd}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def refresh(token_file: str | None) -> list[str]:
    """把语料库里每篇报告正文重取一次；返回正文发生变化的报告 id。"""
    token = _token(token_file)
    corpus = _load(CORPUS)
    drifted = []
    for rid, row in corpus["reports"].items():
        req = urllib.request.Request(
            f"{DEFAULT_API}/tensor/reports/{rid}", headers={"Authorization": "Bearer " + token}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                md = json.loads(r.read()).get("markdown") or ""
        except urllib.error.URLError as exc:  # 网络不可用：保持既有语料，如实报告
            print(f"[refresh] report {rid} 取回失败（保留既有正文）: {exc}", file=sys.stderr)
            continue
        sha = hashlib.sha256(md.encode()).hexdigest()
        if sha != row["sha256"]:
            drifted.append(rid)
            row["markdown"], row["sha256"] = md, sha
    CORPUS.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    return drifted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="能力证据台账对账器")
    ap.add_argument("--check", action="store_true", help="离线对账（默认动作）")
    ap.add_argument("--write", action="store_true", help="写出 artifacts/ 下的对账结果与表格")
    ap.add_argument("--refresh", action="store_true", help="先从 :8010 重取证据正文再对账")
    ap.add_argument("--token-file", default="/tmp/tok")
    args = ap.parse_args(argv)

    if not LEDGER.exists():
        print(f"台账不存在: {LEDGER}", file=sys.stderr)
        return 2
    if args.refresh:
        drifted = refresh(args.token_file)
        print(f"[refresh] 正文变化报告: {drifted or '无'}")
    ledger = _load(LEDGER)
    result, violations = check(ledger)

    if args.write or args.refresh:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        RESOLVED.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        TABLE_MD.write_text(render_table(result), encoding="utf-8")

    print(render_table(result))
    if violations:
        print(f"FAIL: {len(violations)} 项违规", file=sys.stderr)
        return 1
    print("PASS: C1~C7 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())