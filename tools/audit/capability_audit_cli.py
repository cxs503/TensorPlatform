#!/usr/bin/env python3
"""能力审计自动化 CLI（IN21-A1）：扫描 → 量化 → 对拍 → 自检 → 落产物。

产物（默认 ``artifacts/capability_audit/``）
--------------------------------------------
* ``capabilities.json`` —— 主产物：21 项能力的声明等级 / 量化等级 /
  6 分项分 / 加权与归一化权重 / 判级规则触发链 / 逐条证据（文件+行号+sha256）/
  与 Tensor* 库入口的只读对拍结果 / 与正典基准的覆盖对拍 / O7 卡片索引。
* ``audit_scores.md`` —— 人读摘要表（声明 vs 量化 vs 背离）。
* ``o7_cards/CAP-AUD-*.json`` —— 与 ``tools/audit/capability_ledger_upgrade.py``
  ``check_evidence()`` 兼容的升账卡片（``--verify-o7`` 会真把卡片喂回去校验）。
* ``self_checks.json`` —— S1~S7 自检结果。

用法
----
    python3 tools/audit/capability_audit_cli.py                    # 审计并写产物
    python3 tools/audit/capability_audit_cli.py --dry-run          # 不写盘
    python3 tools/audit/capability_audit_cli.py --check            # 只跑自检（仍需扫描）
    python3 tools/audit/capability_audit_cli.py --strict           # 有 OVERCLAIM 即非 0
    python3 tools/audit/capability_audit_cli.py --no-solvers       # 跳过求解器库探测
    python3 tools/audit/capability_audit_cli.py --solver-root DIR  # 指定 Tensor* 检出根

退出码：0 = 审计完成且自检全过；1 = 自检违规或 ``--strict`` 抓到 OVERCLAIM；
2 = 输入缺失（声明快照 / 台账不存在）。

纪律：默认路径**不联网、不 import、不执行**任何被测代码；求解器库缺失记 ``ABSENT``
并把 ``solver`` 分项标 n/a（不参与加权归一），绝不拿「没探测到」当「不真实」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capability_audit_scan as S  # noqa: E402

import ledger_paths as _LP  # noqa: E402  单一台账解析器（消费者契约门）

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLAIMS = ROOT / "tests" / "fixtures" / "capability_ledger" / "capabilities_snapshot.json"


def ledger_ref() -> dict:
    """消费者 #2 契约访问器：CLI 解析到的台账身份（path/sha256/status）。"""
    return _LP.resolve_ledger()


_RESOLVED = ledger_ref()
DEFAULT_LEDGER = Path(_RESOLVED["path"]) if _RESOLVED.get("path") else (
    ROOT / "artifacts" / "capability_ledger" / "capability_ledger.json")
DEFAULT_REFERENCE = ROOT / "backend" / "data" / "benchmark_reference.json"
DEFAULT_SOLVER_ROOT = Path("/root")
OUT_DIR = ROOT / "artifacts" / "capability_audit"

SCHEMA_VERSION = "1.0"
TOOL_VERSION = "capability-audit-cli/1.0"

# 证据面优先级：验证基准 > 测试 > 文档 > 代码（用于选 O7 卡片锚点）
ANCHOR_PRIORITY = ("verified", "test", "doc", "code")
PLACEHOLDER_GUARD = re.compile(r"placeholder|占位|TODO|TBD|待补|fake|dummy", re.I)


# ── 输入 ────────────────────────────────────────────────────────────────────
def load_claims(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    caps = data.get("capabilities")
    if isinstance(caps, list):  # 兼容 [{id, level}]
        caps = {c["id"]: c.get("claimed_level") or c.get("level") for c in caps}
    if not isinstance(caps, dict) or not caps:
        raise ValueError(f"claims 文件结构不可解析: {path}")
    return {"capabilities": caps, "source": data.get("source"),
            "capability_count": data.get("capability_count") or len(caps),
            "sha256": S.sha256_file(path), "path": str(path)}


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"capabilities": {}, "available": False, "sha256": None, "path": str(path)}
    data = json.loads(path.read_text(encoding="utf-8"))
    recs = {}
    for c in data.get("capabilities", []):
        recs[c["id"]] = c
    resolved = {}
    rp = path.with_name("capability_ledger_resolved.json")
    if rp.exists():
        for c in json.loads(rp.read_text(encoding="utf-8")).get("capabilities", []):
            # 只有挂过证据的能力才算「人工已裁决」；其余是未验证的默认 WITHHELD
            resolved[c["id"]] = {"level": c.get("reconciled_level"),
                                 "has_curation": bool(c.get("evidence_count"))}
    return {"capabilities": recs, "available": True,
            "sha256": S.sha256_file(path), "path": str(path),
            "level_rank": data.get("level_rank"), "reconciled": resolved}


# ── O7 卡片 ─────────────────────────────────────────────────────────────────
def _anchor_record(ev: dict) -> dict | None:
    """按优先级选一条最强证据作为卡片锚点（跳过含占位/失败词的弱行）。"""
    for kind in ANCHOR_PRIORITY:
        for rec in ev.get(kind, []):
            quote = rec.get("quote") or ""
            if not quote or PLACEHOLDER_GUARD.search(quote):
                continue
            if kind in ("doc", "verified") and rec.get("fail_quote"):
                continue
            return {"kind": kind, **rec}
    return None


def build_o7_card(idx: int, cid: str, rec: dict, root: Path = ROOT) -> dict:
    """生成与 capability_ledger_upgrade.check_evidence() 兼容的升账卡片。"""
    graded = rec["graded_level"]
    anchor = _anchor_record(rec["evidence"])
    ve: dict = {
        "verification_status": "WITHHELD",
        "anchor_type": "none",
        "gap_reason": "",
        "relative_error_pct": None,
        "tolerance_pct": 3.0,
    }
    if graded == "REAL" and anchor:
        path = root / anchor["path"]
        text = path.read_text(encoding="utf-8")
        quote_re = re.escape(anchor["quote"])
        hit = [ln for ln in text.split("\n") if re.search(quote_re, ln)]
        line = hit[0] if hit else anchor["quote"]
        failure = bool(S.FAILURE_QUOTE.search(line))
        ve.update({
            "verification_status": "WITHHELD" if failure else "PASS",
            "anchor_type": "repo_file",
            "anchor_path": anchor["path"],
            "report_body_sha256": S.sha256_text(text),
            "report_md5": S.md5_text(text),
            "quote_match": quote_re,
            "quote_line_sha256": S.line_sha256(line),
            "gap_reason": "anchor line asserts failure" if failure else "",
        })
    elif anchor:
        path = root / anchor["path"]
        text = path.read_text(encoding="utf-8")
        quote_re = re.escape(anchor["quote"])
        hit = [ln for ln in text.split("\n") if re.search(quote_re, ln)]
        ve.update({
            "verification_status": "HONEST_FAIL" if graded in ("MOCK", "STUB") else "WITHHELD",
            "anchor_type": "repo_file",
            "anchor_path": anchor["path"],
            "report_body_sha256": S.sha256_text(text),
            "report_md5": S.md5_text(text),
            "quote_match": quote_re,
            "quote_line_sha256": S.line_sha256(hit[0] if hit else anchor["quote"]),
        })
    else:
        ve["gap_reason"] = f"审计未找到可锚定证据行（quantified level={graded}）"
    return {
        "card_id": f"CAP-AUD-{idx:03d}",
        "name": f"{cid} 能力审计（自动量化）",
        "repo": "TensorPlatform",
        "discipline": rec["domain"],
        "status": "active",
        "generated_by": TOOL_VERSION,
        "capability_id": cid,
        "claimed_level": rec["claimed_level"],
        "graded_level": graded,
        "total_score": rec["total_score"],
        "rule": rec["classification"]["rule"],
        "validation_evidence": ve,
    }


def verify_o7_cards(cards: list, root: Path = ROOT) -> dict:
    """把生成的卡片喂回 O7 升账 CLI 的 check_evidence() 做机器对拍。"""
    try:
        import capability_ledger_upgrade as UP
    except Exception as exc:  # pragma: no cover - 环境缺依赖
        return {"available": False, "reason": f"import failed: {exc}", "violations": None}
    violations, verdicts = [], {}
    for card in cards:
        res = UP.check_evidence(card, {"reports": {}}, root)
        verdicts[card["card_id"]] = res["verdict"]
        violations.extend(res["violations"])
    return {"available": True, "cards": len(cards), "violations": violations,
            "violation_count": len(violations), "verdicts": verdicts,
            "checker": "tools/audit/capability_ledger_upgrade.py::check_evidence"}


# ── 审计主流程 ──────────────────────────────────────────────────────────────
def audit(claims: dict, ledger: dict, reference: dict, solver_root: Path | None,
          profiles=None, subdirs=None, root: Path = ROOT) -> dict:
    profiles = profiles or S.PROFILES
    subdirs = subdirs or ("backend", "src", "tests", "docs")
    scanned = S.scan_worktree(root, profiles, subdirs)
    solver_probe = S.probe_solver_libs(solver_root) if solver_root else {}

    results = []
    for idx, cid in enumerate(sorted(profiles), start=1):
        prof = profiles[cid]
        claimed = claims["capabilities"].get(cid, "WITHHELD")
        lrec = ledger["capabilities"].get(cid)
        ref = S.reference_coverage(reference, prof.get("reference_domain"))
        libs = {lib: solver_probe[lib] for lib in prof.get("solver_libs", ()) if lib in solver_probe}
        comps = S.component_scores(scanned[cid], ref, libs or None, prof["domain"])
        weights = S.normalize_weights(comps)
        total = S.total_score(comps, weights)
        sig = S.ledger_signals(lrec)
        verdict = S.classify(comps, total, sig)
        results.append({
            "id": cid,
            "domain": prof["domain"],
            "claimed_level": claimed,
            "claimed_source": "tests/fixtures/capability_ledger/capabilities_snapshot.json",
            "ledger_reconciled_level": (ledger.get("reconciled", {}).get(cid) or {}).get("level"),
            "ledger_has_curation": bool(
                (ledger.get("reconciled", {}).get(cid) or {}).get("has_curation")),
            "graded_level": verdict["level"],
            "classification": verdict,
            "divergence": S.divergence(claimed, verdict["level"]),
            "total_score": total,
            "weights": weights,
            "components": {k: {"score": v["score"], "detail": v["detail"]}
                           for k, v in comps.items()},
            "ledger_signals": sig,
            "evidence": {k: scanned[cid][k] for k in ("verified", "test", "doc", "code")},
            "evidence_scope": {
                "worktree": str(root),
                "no_local_code": not scanned[cid]["code"],
                "note": ("本仓无实现面命中：判决基于 docs/tests 侧证据，"
                         "不能据此断言平台未实现该能力") if not scanned[cid]["code"] else "",
            },
            "solver_probe": libs,
            "reference_check": {k: v for k, v in ref.items() if k != "cases"} |
                               {"cases": ref.get("cases", [])[:10]},
        })

    cards = [build_o7_card(i, r["id"], r, root) for i, r in enumerate(results, start=1)]
    o7 = verify_o7_cards(cards, root)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "claims": {k: claims[k] for k in ("path", "sha256", "capability_count", "source")},
            "ledger": {"path": ledger["path"], "sha256": ledger["sha256"],
                       "available": ledger["available"]},
            "reference": {"path": reference.get("path", str(DEFAULT_REFERENCE)),
                          "version": reference.get("version"),
                          "available": reference.get("available"),
                          "case_count": len(reference.get("cases", []))},
            "solver_root": str(solver_root) if solver_root else None,
            "level_rank": S.LEVEL_RANK,
        },
        "scoring_model": {
            "weights_base": S.WEIGHTS,
            "saturation": S.SATURATION,
            "thresholds": {"TOTAL_REAL": S.TOTAL_REAL, "VERIFY_REAL": S.VERIFY_REAL,
                           "STUB_RATIO_STUB": S.STUB_RATIO_STUB,
                           "TOTAL_WITHHELD": S.TOTAL_WITHHELD},
            "levels": S.LEVELS,
            "notes": [
                "不适用分项（score=null）在加权前剔除并重新归一，weights 字段逐能力落盘。",
                "REAL 需 total≥TOTAL_REAL 且 verify≥VERIFY_REAL 且无矛盾证据；单靠代码量大不得升 REAL。",
                "台账最新矛盾证据（R2）优先于分数：分数再高也压不回 REAL。",
            ],
        },
        "scope": {
            "scan_roots": list(subdirs),
            "root": str(root),
            "worktree_scoped": True,
            "note": ("本审计只对工作树内的实现面取证（backend/ src/ tests/ docs/）；"
                     "21 项能力的线上实现在 :8001 服务侧，若其代码不在本仓，"
                     "dev.* / 平台域名下的结果只表示『本仓无证据』，不等于平台无实现。"),
            "relevance_gate": list(S.RARE_TERMS_NOTE) if isinstance(S.RARE_TERMS_NOTE, tuple)
                               else S.RARE_TERMS_NOTE,
        },
        "o7_alignment": {k: v for k, v in o7.items() if k != "verdicts"} |
                         {"verdicts": o7.get("verdicts", {})},
        "capabilities": results,
    }
    payload["summary"] = summarize(results)
    payload["content_digest"] = content_digest(results)
    return {"payload": payload, "cards": cards, "o7": o7}


def summarize(results: list) -> dict:
    by_level = {lv: 0 for lv in S.LEVELS}
    by_div = {"OK": 0, "OVERCLAIM": 0, "UNDERCLAIM": 0}
    for r in results:
        by_level[r["graded_level"]] += 1
        by_div[r["divergence"]] += 1
    return {
        "capability_count": len(results),
        "graded_by_level": by_level,
        "divergence": by_div,
        "claimed_real_but_not_real": sorted(
            r["id"] for r in results
            if r["claimed_level"] == "REAL" and r["graded_level"] != "REAL"),
        "underclaimed": sorted(r["id"] for r in results if r["divergence"] == "UNDERCLAIM"),
        "solver_libs_present": sorted({
            lib for r in results for lib, p in r["solver_probe"].items()
            if p["state"].startswith("PRESENT")}),
        "solver_libs_absent": sorted({
            lib for r in results for lib, p in r["solver_probe"].items()
            if p["state"] == "ABSENT"}),
    }


def content_digest(results: list) -> str:
    """只对可复现内容取摘要（tokens: id/ claimed / graded / total / 分项分 / 证据路径）。"""
    canon = [
        {
            "id": r["id"], "claimed": r["claimed_level"], "graded": r["graded_level"],
            "total": r["total_score"], "rule": r["classification"]["rule"],
            "components": {k: v["score"] for k, v in r["components"].items()},
            "evidence_paths": sorted({
                e["path"] for kind in r["evidence"] for e in r["evidence"][kind]}),
        }
        for r in results
    ]
    return hashlib.sha256(json.dumps(canon, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


# ── 自检 S1~S7 ──────────────────────────────────────────────────────────────
def self_checks(payload: dict, claims: dict, o7: dict, cards: list,
                root: Path = ROOT) -> dict:
    results = payload["capabilities"]
    checks = []

    def add(name, ok, detail):
        checks.append({"id": name, "ok": bool(ok), "detail": detail})

    # S1 覆盖：能力集合与声明快照一致
    ids = [r["id"] for r in results]
    want = set(claims["capabilities"])
    add("S1_COVERAGE", set(ids) == want and len(ids) == len(want),
        f"{len(ids)} capabilities; missing={sorted(want - set(ids))} extra={sorted(set(ids) - want)}")

    # S2 等级枚举合法
    bad = [r["id"] for r in results
           if r["graded_level"] not in S.LEVELS or r["claimed_level"] not in S.LEVELS]
    add("S2_LEVEL_ENUM", not bad, f"invalid={bad}")

    # S3 证据可复核：文件存在且 sha256 与扫描时一致
    seen, bad = {}, []
    for r in results:
        for kind in r["evidence"]:
            for e in r["evidence"][kind]:
                seen.setdefault(e["path"], e["sha256"])
    for path, want_sha in seen.items():
        p = root / path
        if not p.exists() or S.sha256_file(p) != want_sha:
            bad.append(path)
    add("S3_EVIDENCE_SHA", not bad, f"{len(seen)} unique evidence files; mismatched={bad[:5]}")

    # S4 divergence 与等级序一致
    def rank(lv):
        return S.LEVEL_RANK[lv]
    bad = [r["id"] for r in results
           if r["divergence"] != S.divergence(r["claimed_level"], r["graded_level"])
           or (r["divergence"] == "OVERCLAIM" and rank(r["claimed_level"]) <= rank(r["graded_level"]))
           or (r["divergence"] == "UNDERCLAIM" and rank(r["claimed_level"]) >= rank(r["graded_level"]))]
    add("S4_DIVERGENCE_ORDERING", not bad, f"inconsistent={bad}")

    # S5 分数面：分项在 [0,1] 或 null；total == 加权和
    bad = []
    for r in results:
        for k, v in r["components"].items():
            if v["score"] is not None and not (0.0 <= v["score"] <= 1.0):
                bad.append(f"{r['id']}:{k}={v['score']}")
        recomputed = sum(r["components"][k]["score"] * w for k, w in r["weights"].items())
        if abs(recomputed - r["total_score"]) > 1e-6:
            bad.append(f"{r['id']}:total {r['total_score']} != {recomputed:.6f}")
        if abs(sum(r["weights"].values()) - 1.0) > 1e-6:
            bad.append(f"{r['id']}:weights sum {sum(r['weights'].values()):.6f}")
    add("S5_SCORE_CONSISTENCY", not bad, f"issues={bad[:5]}")

    # S6 O7 卡片通过升账 CLI 校验（0 违规）
    ok6 = bool(o7.get("available")) and o7.get("violation_count") == 0 and len(cards) == len(results)
    add("S6_O7_CARD_ALIGNMENT", ok6,
        f"cards={len(cards)} violations={o7.get('violation_count')} "
        f"checker={o7.get('checker')} first={ (o7.get('violations') or [None])[0] }")

    # S7 可重放：同一 capabilities 数组重算摘要必须一致
    add("S7_REPLAY_DIGEST", content_digest(results) == payload["content_digest"],
        payload["content_digest"][:16])

    return {"all_ok": all(c["ok"] for c in checks), "checks": checks}


# ── 产物 ────────────────────────────────────────────────────────────────────
def write_artifacts(payload: dict, cards: list, checks: dict, out_dir: Path,
                    root: Path = ROOT) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    cap_json = out_dir / "capabilities.json"
    cap_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(str(cap_json.relative_to(root)))

    chk_json = out_dir / "self_checks.json"
    chk_json.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(str(chk_json.relative_to(root)))

    # O7 卡片
    card_dir = out_dir / "o7_cards"
    card_dir.mkdir(exist_ok=True)
    for old in card_dir.glob("CAP-AUD-*.json"):
        old.unlink()
    for card in cards:
        p = card_dir / f"{card['card_id']}.json"
        p.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(str(card_dir.relative_to(root)) + "/ (cards)")
    index = {
        "tool": TOOL_VERSION,
        "checker": "tools/audit/capability_ledger_upgrade.py::check_evidence",
        "cards": [{"card_id": c["card_id"], "capability_id": c["capability_id"],
                   "verification_status": c["validation_evidence"]["verification_status"],
                   "path": f"o7_cards/{c['card_id']}.json"} for c in cards],
        "violation_count": payload["o7_alignment"].get("violation_count"),
    }
    idx = out_dir / "o7_index.json"
    idx.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(str(idx.relative_to(root)))

    md = render_scores_md(payload)
    md_path = out_dir / "audit_scores.md"
    md_path.write_text(md, encoding="utf-8")
    written.append(str(md_path.relative_to(root)))
    return written


def render_scores_md(payload: dict) -> str:
    s = payload["summary"]
    lines = [
        "# 平台能力审计（自动量化）",
        "",
        f"- 工具：`{payload['tool']}` · schema `{payload['schema_version']}`",
        f"- 声明来源：`{payload['inputs']['claims']['path']}`"
        f"（sha256 `{str(payload['inputs']['claims']['sha256'])[:16]}`）",
        f"- 内容摘要：`{payload['content_digest'][:32]}`",
        f"- 能力数：{s['capability_count']}；量化等级分布：{s['graded_by_level']}",
        f"- 背离：{s['divergence']}",
        f"- 声明 REAL 但未量化到 REAL：{len(s['claimed_real_but_not_real'])} 项",
        f"- O7 卡片违规：{payload['o7_alignment'].get('violation_count')}"
        f"（校验器 `{payload['o7_alignment'].get('checker')}`）",
        "",
        "| 能力 | 域 | 声明 | 台账 | 量化 | 背离 | total | code | test | verify | doc | ref | solver | 规则 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def f(v):
        return "n/a" if v is None else f"{v:.2f}"

    for r in payload["capabilities"]:
        c = r["components"]
        lines.append(
            f"| `{r['id']}` | {r['domain']} | {r['claimed_level']} | "
            f"{r['ledger_reconciled_level'] or '—'} | **{r['graded_level']}** | "
            f"{r['divergence']} | {r['total_score']:.3f} | "
            f"{f(c['code']['score'])} | {f(c['test']['score'])} | {f(c['verify']['score'])} | "
            f"{f(c['doc']['score'])} | {f(c['reference']['score'])} | {f(c['solver']['score'])} | "
            f"{r['classification']['rule']} |"
        )
    lines += ["", "## 判级规则触发链（前 6 项示例）", ""]
    for r in payload["capabilities"][:6]:
        for t in r["classification"]["triggered"]:
            lines.append(f"- `{r['id']}` · {t['rule']}：{t['why']}")
    lines += ["", "## 求解器库入口只读对拍", ""]
    libs = {}
    for r in payload["capabilities"]:
        for lib, p in r["solver_probe"].items():
            libs.setdefault(lib, p)
    if libs:
        lines.append("| 库 | 状态 | 公开入口 | verified 基准 | 测试文件 | 备注 |")
        lines.append("|---|---|---|---|---|---|")
        for lib, p in sorted(libs.items()):
            lines.append(f"| {lib} | {p['state']} | {p.get('entry_point_count', 0)} | "
                         f"{p.get('verified_benchmarks', 0)} | {p.get('test_files', 0)} | "
                         f"{p.get('reason') or '—'} |")
    else:
        lines.append("（本次运行关闭了求解器探测：`--no-solvers`）")
    return "\n".join(lines) + "\n"


# ── CLI ─────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="平台能力审计自动化 CLI（IN21-A1）")
    ap.add_argument("--claims", type=Path, default=DEFAULT_CLAIMS)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    ap.add_argument("--solver-root", type=Path, default=DEFAULT_SOLVER_ROOT)
    ap.add_argument("--no-solvers", action="store_true", help="跳过 Tensor* 库探测")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--dry-run", action="store_true", help="不写盘，只打印摘要")
    ap.add_argument("--check", action="store_true", help="只跑自检并以退出码报告")
    ap.add_argument("--strict", action="store_true", help="有 OVERCLAIM 即非 0")
    args = ap.parse_args(argv)

    if not args.claims.exists():
        print(f"[FAIL] claims 缺失: {args.claims}", file=sys.stderr)
        return 2
    claims = load_claims(args.claims)
    ledger = load_ledger(args.ledger)
    reference = S.load_reference(args.reference)
    solver_root = None if args.no_solvers else args.solver_root

    res = audit(claims, ledger, reference, solver_root)
    payload, cards, o7 = res["payload"], res["cards"], res["o7"]
    checks = self_checks(payload, claims, o7, cards)
    payload["self_checks"] = {k: v for k, v in checks.items() if k != "checks"}
    payload["self_checks"]["details"] = checks["checks"]

    s = payload["summary"]
    print(f"[audit] capabilities={s['capability_count']} "
          f"levels={s['graded_by_level']} divergence={s['divergence']}")
    print(f"[audit] content_digest={payload['content_digest'][:16]} "
          f"o7_cards={len(cards)} o7_violations={o7.get('violation_count')}")
    for c in checks["checks"]:
        print(f"  {'OK  ' if c['ok'] else 'FAIL'} {c['id']}: {c['detail']}")

    if not args.dry_run:
        written = write_artifacts(payload, cards, checks, args.out)
        print("[audit] written: " + ", ".join(written))

    if not checks["all_ok"]:
        return 1
    if args.strict and s["divergence"]["OVERCLAIM"] > 0:
        print(f"[strict] OVERCLAIM={s['divergence']['OVERCLAIM']} → 非 0", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())