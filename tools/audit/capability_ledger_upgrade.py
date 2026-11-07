#!/usr/bin/env python3
"""能力台账升账 CLI（capability ledger upgrade CLI）

功能
----
1. 新增/更新能力卡片条目，强制证据分级校验：
   - 升 REAL 必须挂 ≥1 工件 sha256 引用 + 判词，否则拒收并降为 WITHHELD
   - 升 PASS 必须满足 E2+E3+E7 全绿（锚点可解析、摘要匹配、quote 命中）
2. dry-run 预览模式：不写盘，只输出变更摘要
3. 变更日志追加：每次实跑在 changelog.md 追加一行审计记录

纪律
----
- 只增不改：不动冻结 API 面、不做 schema 手术、不改既有表结构
- 新工具入 tools/audit/ 独立目录
- 每工具带 dry-run 模式 + pytest

证据分级
--------
REAL  = 真实算法，有可复现证据
MOCK  = 硬编码/随机输出
STUB  = 空壳
WITHHELD = 证据不足（降级的默认目标等级）
NOT_SUPPORTED = 不支持

用法
----
    # dry-run 预览
    python3 tools/audit/capability_ledger_upgrade.py --card docs/capability_registry/cards/CAP-XXX.json --dry-run

    # 实跑升账
    python3 tools/audit/capability_ledger_upgrade.py --card docs/capability_registry/cards/CAP-XXX.json

    # 批量升级
    python3 tools/audit/capability_ledger_upgrade.py --dir docs/capability_registry/cards/ --dry-run

    # 审计旧案（只读）
    python3 tools/audit/capability_ledger_upgrade.py --audit --card docs/capability_registry/cards/CAP-002-tensorlbm-suboff-resistance.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 路径常量 ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
CARDS_DIR = ROOT / "docs" / "capability_registry" / "cards"
INDEX = ROOT / "docs" / "capability_registry" / "index.json"
SNAPSHOT = ROOT / "tests" / "fixtures" / "capability_evidence" / "reports_snapshot.json"
CHANGELOG = ROOT / "docs" / "capability_registry" / "changelog.md"

# ── 证据分级 ────────────────────────────────────────────────────────────────
EVIDENCE_LEVELS = ["NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"]
PASS_STATES = {"PASS", "VERIFIED"}
FAIL_STATES = {"FAIL", "HONEST_FAIL"}
FAILURE_QUOTE = re.compile(
    r"是否\s*<3%\s*[:：]\s*否|不可接受|超.{0,4}15%|\bFAIL\b|❌|error.*>.*15%|unacceptable"
)
PLACEHOLDER = re.compile(r"placeholder|占位|TODO|TBD|待补|not yet frozen|fake|dummy", re.I)
SENTINEL_DIGESTS = {"0" * 32, "0" * 64, "f" * 32, "f" * 64}
ANCHOR_TYPES_VALID = {"platform_report", "repo_file"}

# ── 工具函数 ────────────────────────────────────────────────────────────────
def line_sha256(line: str) -> str:
    """行级 sha256 前缀（16 hex）。"""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def body_digests(body: str) -> dict[str, str]:
    """计算 body 的 sha256 和 md5。"""
    b = body.encode("utf-8")
    return {"body_sha256": hashlib.sha256(b).hexdigest(), "body_md5": hashlib.md5(b).hexdigest()}


def load_card(path: Path) -> dict:
    """加载能力卡片。"""
    return json.loads(path.read_text(encoding="utf-8"))


def load_snapshot() -> dict:
    """加载证据快照（离线）。"""
    if not SNAPSHOT.exists():
        return {"reports": {}}
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def load_index() -> dict:
    """加载索引。"""
    if not INDEX.exists():
        return {"cards": []}
    return json.loads(INDEX.read_text(encoding="utf-8"))


def anchored_text(card: dict, snapshot: dict, root: Path) -> tuple[str | None, str]:
    """解析锚点文本。返回 (text, reason)。"""
    ve = card.get("validation_evidence") or {}
    atype = ve.get("anchor_type")
    if atype == "platform_report":
        rid = str(ve.get("report_id", ""))
        rec = (snapshot.get("reports") or {}).get(rid)
        if rec is None:
            return None, f"report_id={rid!r} not in pinned snapshot"
        return rec["body"], f"pinned report {rid}"
    if atype == "repo_file":
        path = ve.get("anchor_path")
        if not path:
            return None, "anchor_path missing"
        p = root / path
        if not p.exists():
            return None, f"anchor_path {path!r} missing on disk"
        return p.read_text(encoding="utf-8"), f"repo file {path}"
    return None, f"unknown anchor_type={atype!r}"


# ── 证据校验链 E1-E7 ────────────────────────────────────────────────────────
def check_evidence(card: dict, snapshot: dict, root: Path = ROOT) -> dict:
    """对单张卡片执行 E1-E7 校验，返回校验结果字典。"""
    cid = card.get("card_id", "unknown")
    ve = card.get("validation_evidence") or {}
    status = ve.get("verification_status")
    atype = ve.get("anchor_type")
    declared_empty = atype == "none"

    result = {
        "card_id": cid,
        "checks": {},
        "violations": [],
        "artifacts": [],
    }

    # E2: 锚点可解析
    if declared_empty:
        e2 = bool(str(ve.get("gap_reason") or "").strip()) and status not in PASS_STATES
        if not e2:
            result["violations"].append(
                f"E2 {cid}: anchor_type=none requires gap_reason + non-pass status "
                f"(status={status!r}, gap_reason={'yes' if ve.get('gap_reason') else 'no'})"
            )
    else:
        text, reason = anchored_text(card, snapshot, root)
        e2 = atype in ANCHOR_TYPES_VALID and text is not None
        if atype == "platform_report":
            rid = str(ve.get("report_id", ""))
            if not re.fullmatch(r"\d+", rid):
                e2 = False
                result["violations"].append(f"E2 {cid}: report_id={rid!r} not numeric")
        if not e2:
            result["violations"].append(f"E2 {cid}: anchor unresolvable ({reason})")
    result["checks"]["E2"] = e2

    # E3: 摘要匹配
    e3 = "n/a" if declared_empty else False
    if not declared_empty:
        text, _ = anchored_text(card, snapshot, root)
        if text is not None:
            d = body_digests(text)
            want_sha = ve.get("report_body_sha256")
            want_md5 = ve.get("report_md5")
            e3 = d["body_sha256"] == want_sha and d["body_md5"] == want_md5
            if not e3:
                result["violations"].append(
                    f"E3 {cid}: digest mismatch "
                    f"(card sha={str(want_sha)[:12] if want_sha else 'None'}, "
                    f"got sha={d['body_sha256'][:12]})"
                )
            result["artifacts"].append({
                "type": "body_digest",
                "sha256": d["body_sha256"],
                "md5": d["body_md5"],
            })
        else:
            result["violations"].append(f"E3 {cid}: digest unverifiable (anchor unresolvable)")
    result["checks"]["E3"] = e3

    # E4: 无占位符
    e4 = "n/a" if declared_empty else True
    blob = "{}" if declared_empty else json.dumps(ve, ensure_ascii=False)
    m = PLACEHOLDER.search(blob)
    if m:
        e4 = False
        result["violations"].append(f"E4 {cid}: placeholder wording: {m.group(0)!r}")
    for key in ("report_md5", "report_body_sha256"):
        if str(ve.get(key, "")).lower() in SENTINEL_DIGESTS:
            e4 = False
            result["violations"].append(f"E4 {cid}: sentinel digest in {key}")
    result["checks"]["E4"] = e4

    # E7: quote 命中
    quote = ve.get("quote_match")
    e7 = "n/a" if declared_empty else False
    if not declared_empty:
        if not quote:
            result["violations"].append(f"E7 {cid}: missing quote_match")
        else:
            text, _ = anchored_text(card, snapshot, root)
            if text is None:
                result["violations"].append(f"E7 {cid}: quote unverifiable (anchor unresolvable)")
            else:
                hit = [ln for ln in text.split("\n") if re.search(quote, ln)]
                if not hit:
                    result["violations"].append(f"E7 {cid}: quote_match={quote!r} no hit")
                elif ve.get("quote_line_sha256") != line_sha256(hit[0]):
                    result["violations"].append(
                        f"E7 {cid}: quote_line_sha256 drift "
                        f"(card={ve.get('quote_line_sha256')!r}, got={line_sha256(hit[0])!r})"
                    )
                else:
                    e7 = True
                    result["artifacts"].append({
                        "type": "quote_hit",
                        "line": hit[0].strip()[:120],
                        "line_sha256": line_sha256(hit[0]),
                    })
    result["checks"]["E7"] = e7

    # E6: 容差一致性
    err, tol = ve.get("relative_error_pct"), ve.get("tolerance_pct")
    e6 = True
    if err is not None and tol is not None and status in PASS_STATES and abs(float(err)) > float(tol):
        e6 = False
        result["violations"].append(f"E6 {cid}: |error|={err}% > tolerance={tol}%")
    result["checks"]["E6"] = e6

    # E5: 过度声明
    e5 = True
    if status in PASS_STATES:
        if not all(v is True for v in (e2, e3, e7)):
            e5 = False
            result["violations"].append(
                f"E5 {cid}: OVERCLAIM status={status} but E2/E3/E7 not green"
            )
        elif isinstance(quote, str):
            text, _ = anchored_text(card, snapshot, root)
            if text and FAILURE_QUOTE.search(text):
                hit_line = [ln for ln in text.split("\n") if re.search(quote, ln)]
                if hit_line and FAILURE_QUOTE.search(hit_line[0]):
                    e5 = False
                    result["violations"].append(
                        f"E5 {cid}: OVERCLAIM - quoted evidence asserts failure"
                    )
    result["checks"]["E5"] = e5

    # 综合判定
    if declared_empty:
        result["verdict"] = "DECLARED_EMPTY"
    elif all(v is True for v in (e2, e3, e4, e7)):
        result["verdict"] = "ANCHORED"
    else:
        result["verdict"] = "UNVERIFIABLE"

    return result


# ── 升账核心逻辑 ────────────────────────────────────────────────────────────
def validate_for_upgrade(card: dict, snapshot: dict, root: Path = ROOT) -> dict:
    """验证卡片是否满足升账条件。返回 (ok: bool, grade: str, details: list[str])。"""
    cid = card.get("card_id", "unknown")
    ve = card.get("validation_evidence") or {}
    violations: list[str] = []
    artifacts: list[dict] = []

    # 先跑 E1-E7
    evidence_result = check_evidence(card, snapshot, root)
    violations.extend(evidence_result["violations"])
    artifacts.extend(evidence_result["artifacts"])

    checks = evidence_result["checks"]
    verdict = evidence_result["verdict"]
    current_grade = ve.get("verification_status")

    # 目标等级推断
    target_grade = current_grade  # 默认保持当前等级

    # 如果当前是 FAIL/HONEST_FAIL，不升级（即使证据链完整）
    if current_grade in FAIL_STATES:
        target_grade = current_grade
        violations.append(
            f"当前等级 {current_grade}，不升级（需先修复问题后重新提交）"
        )

    # 如果当前是 PASS 且证据链完整，保持 REAL
    elif current_grade in PASS_STATES and verdict == "ANCHORED" and checks.get("E5") is True:
        target_grade = "REAL"

    # 如果当前是 NOT_VERIFIED 且证据链完整，可升 REAL
    elif current_grade in ("NOT_VERIFIED", None) and verdict == "ANCHORED" and checks.get("E5") is True:
        target_grade = "REAL"

    # 明确声明无证据 → WITHHELD
    elif verdict == "DECLARED_EMPTY":
        target_grade = "WITHHELD"

    # 证据不可验证 → WITHHELD
    elif verdict == "UNVERIFIABLE":
        target_grade = "WITHHELD"

    # 额外检查：REAL 必须有 ≥1 artifact sha256 + 判词
    if target_grade == "REAL":
        has_sha256 = any(a.get("type") in ("body_digest", "quote_hit") for a in artifacts)
        has_judgment = bool(str(ve.get("verification_status_note") or "").strip())
        if not has_sha256:
            violations.append(
                f"REAL 升账失败: 缺少工件 sha256 引用（需 ≥1 个 body_digest 或 quote_hit）"
            )
            target_grade = "WITHHELD"
        if not has_judgment:
            violations.append(
                f"REAL 升账失败: 缺少判词 verification_status_note（需说明判定依据）"
            )
            target_grade = "WITHHELD"

    ok = len(violations) == 0
    return {
        "card_id": cid,
        "ok": ok,
        "current_grade": current_grade,
        "target_grade": target_grade,
        "verdict": verdict,
        "checks": checks,
        "violations": violations,
        "artifacts": artifacts,
    }


def build_upgrade_entry(card: dict, validation: dict, snapshot: dict) -> dict:
    """构建升级后的卡片条目。"""
    ve = card.get("validation_evidence", {}).copy()
    new_grade = validation["target_grade"]

    # 更新验证状态
    if new_grade == "REAL":
        ve["verification_status"] = "PASS"
    elif new_grade == "WITHHELD":
        ve["verification_status"] = "NOT_VERIFIED"

    # 补充 artifacts 信息
    if validation["artifacts"]:
        ve["artifact_refs"] = validation["artifacts"]

    ve["upgraded_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ve["upgraded_by"] = "capability_ledger_upgrade.py"

    return {**card, "validation_evidence": ve}


def append_changelog(card_id: str, old_grade: str, new_grade: str,
                     dry_run: bool = False, violations: list[str] | None = None) -> str:
    """追加变更日志。"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    action = "DRY-RUN" if dry_run else "UPGRADE"
    change = f"[{ts}] [{action}] {card_id}: {old_grade} → {new_grade}"
    if violations:
        change += f" | REJECTED: {'; '.join(violations)}"

    if not dry_run:
        CHANGELOG.parent.mkdir(parents=True, exist_ok=True)
        if not CHANGELOG.exists():
            CHANGELOG.write_text("# Capability Ledger Changelog\n\n", encoding="utf-8")
        with open(CHANGELOG, "a", encoding="utf-8") as f:
            f.write(change + "\n")

    return change


# ── CLI 入口 ────────────────────────────────────────────────────────────────
def cmd_single(args: argparse.Namespace) -> int:
    """处理单张卡片。"""
    card_path = Path(args.card)
    if not card_path.exists():
        print(f"ERROR: card file not found: {card_path}", file=sys.stderr)
        return 2

    card = load_card(card_path)
    snapshot = load_snapshot()
    validation = validate_for_upgrade(card, snapshot, ROOT)

    print(f"=== {card.get('name', card.get('card_id', 'unknown'))} ===")
    print(f"  card_id:    {card.get('card_id')}")
    print(f"  current:    {validation['current_grade']}")
    print(f"  target:     {validation['target_grade']}")
    print(f"  verdict:    {validation['verdict']}")
    print(f"  checks:     {json.dumps(validation['checks'], indent=4)}")

    if validation["violations"]:
        print(f"  violations:")
        for v in validation["violations"]:
            print(f"    ✗ {v}")

    if validation["artifacts"]:
        print(f"  artifacts:")
        for a in validation["artifacts"]:
            if a["type"] == "body_digest":
                print(f"    ✓ body_sha256: {a['sha256'][:32]}...")
            elif a["type"] == "quote_hit":
                print(f"    ✓ quote_hit: {a['line'][:80]}...")

    if args.dry_run:
        print(f"\n  [DRY-RUN] No files written.")
        if validation["ok"]:
            print(f"  [DRY-RUN] Would write: {card_path}")
            upgraded = build_upgrade_entry(card, validation, snapshot)
            print(f"  [DRY-RUN] New validation_evidence:")
            print(f"    verification_status: {upgraded['validation_evidence']['verification_status']}")
            print(f"    upgraded_at: {upgraded['validation_evidence']['upgraded_at']}")
    else:
        if validation["ok"]:
            upgraded = build_upgrade_entry(card, validation, snapshot)
            card_path.write_text(
                json.dumps(upgraded, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"\n  ✓ Written: {card_path}")
            changelog = append_changelog(
                card.get("card_id", ""),
                validation["current_grade"],
                validation["target_grade"],
                dry_run=False,
            )
            print(f"  changelog: {changelog}")
        else:
            print(f"\n  ✗ REJECTED: {validation['target_grade']} (evidence insufficient)")
            changelog = append_changelog(
                card.get("card_id", ""),
                validation["current_grade"],
                validation["target_grade"],
                dry_run=False,
                violations=validation["violations"],
            )
            print(f"  changelog: {changelog}")

    return 0 if validation["ok"] else 1


def cmd_batch(args: argparse.Namespace) -> int:
    """批量处理卡片目录。"""
    cards_dir = Path(args.dir)
    if not cards_dir.exists():
        print(f"ERROR: directory not found: {cards_dir}", file=sys.stderr)
        return 2

    snapshot = load_snapshot()
    results = []
    for card_file in sorted(cards_dir.glob("*.json")):
        card = load_card(card_file)
        validation = validate_for_upgrade(card, snapshot, ROOT)
        results.append({
            "card_id": card.get("card_id"),
            "file": card_file.name,
            "current": validation["current_grade"],
            "target": validation["target_grade"],
            "ok": validation["ok"],
            "violations": validation["violations"],
        })

        if not args.quiet:
            status = "✓" if validation["ok"] else "✗"
            print(f"  {status} {card_file.name}: {validation['current_grade']} → {validation['target_grade']}")

    # 汇总
    total = len(results)
    ok_count = sum(1 for r in results if r["ok"])
    reject_count = sum(1 for r in results if not r["ok"])

    print(f"\n  Summary: {total} cards, {ok_count} OK, {reject_count} rejected")

    if not args.dry_run:
        # 更新 index.json
        index = load_index()
        for r in results:
            for ent in index.get("cards", []):
                if ent.get("card_id") == r["card_id"]:
                    ent["verification_status"] = r["target"]
                    ent["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    break
        index["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  Updated: {INDEX.relative_to(ROOT)}")

        # 追加 changelog
        for r in results:
            append_changelog(
                r["card_id"], r["current"], r["target"],
                dry_run=False, violations=r["violations"] if r["violations"] else None,
            )

    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """只读审计模式：对指定卡片出具审计输出。"""
    card_path = Path(args.card)
    if not card_path.exists():
        print(f"ERROR: card file not found: {card_path}", file=sys.stderr)
        return 2

    card = load_card(card_path)
    snapshot = load_snapshot()
    validation = validate_for_upgrade(card, snapshot, ROOT)

    # 只读审计输出
    audit_output = {
        "audit_type": "read-only-audit",
        "card_id": card.get("card_id"),
        "card_name": card.get("name"),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "validation": validation,
        "original_card": {
            "card_id": card.get("card_id"),
            "name": card.get("name"),
            "repo": card.get("repo"),
            "discipline": card.get("discipline"),
            "validation_evidence": {
                "report_id": ve.get("report_id") if (ve := card.get("validation_evidence")) else None,
                "report_md5": ve.get("report_md5") if ve else None,
                "report_body_sha256": ve.get("report_body_sha256") if ve else None,
                "anchor_type": ve.get("anchor_type") if ve else None,
                "anchor_path": ve.get("anchor_path") if ve else None,
                "anchor_title": ve.get("anchor_title") if ve else None,
                "verification_status": ve.get("verification_status") if ve else None,
                "quote_match": ve.get("quote_match") if ve else None,
                "quote_line_sha256": ve.get("quote_line_sha256") if ve else None,
                "relative_error_pct": ve.get("relative_error_pct") if ve else None,
                "tolerance_pct": ve.get("tolerance_pct") if ve else None,
                "gap_reason": ve.get("gap_reason") if ve else None,
            },
        },
    }

    # 输出 JSON
    out_path = Path(args.output) if args.output else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(audit_output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Audit output written to: {out_path}")
    else:
        print(json.dumps(audit_output, ensure_ascii=False, indent=2))

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="能力台账升账 CLI — 强制证据分级校验 + dry-run + 变更日志",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            证据分级: REAL(真实) > MOCK(硬编码) > STUB(空壳) > WITHHELD(证据不足) > NOT_SUPPORTED
            升 REAL 条件: E2+E3+E4+E7 全绿 + ≥1 artifact sha256 + 判词
            升账失败默认降级为: WITHHELD
        """),
    )
    ap.add_argument("--card", help="单张卡片路径")
    ap.add_argument("--dir", help="批量处理卡片目录")
    ap.add_argument("--audit", action="store_true", help="只读审计模式")
    ap.add_argument("--dry-run", action="store_true", help="预览模式，不写盘")
    ap.add_argument("--output", "-o", help="审计输出路径（--audit 模式）")
    ap.add_argument("--quiet", "-q", action="store_true", help="静默模式（仅 --dir）")

    args = ap.parse_args(argv)

    if args.audit:
        return cmd_audit(args)
    elif args.card:
        return cmd_single(args)
    elif args.dir:
        return cmd_batch(args)

    return 2


if __name__ == "__main__":
    sys.exit(main())
