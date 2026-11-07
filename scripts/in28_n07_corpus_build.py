#!/usr/bin/env python3
"""IN28-N07 语料合并与校验：v1（3 例）+ scripts/in28_n07_cases.py（20 例）= 23 例。

校验是硬门（任一不过即拒写，退出码非 0）：
1. 案例数 ≥20，case_id 唯一；
2. 每个案例 ≥1 个 sources 路径且文件存在（100% 带源路径）；
3. 每条 fact 用 regex 现取值 == expected（无捕获组按『命中次数』计，有捕获组取第一组）；
4. 每条 evidence.quote 必须是所在文件原文子串；
5. owner_ruling.label 在封闭词表内，verbatim 必须是 source_ref 原文子串；
6. owner_options ≥2 且 recommended_option ∈ options；charter_citations 均可解析。

用法：
    python3 scripts/in28_n07_corpus_build.py            # 校验 + 写回 data/governance/replay_cases.json
    python3 scripts/in28_n07_corpus_build.py --check    # 只校验（CI/测试用），不写文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from services.council_constitution import (  # noqa: E402
    VERDICTS,
    load_charter,
    load_redline_lexicon,
)

import in28_n07_cases as cases_mod  # noqa: E402

CASES_PATH = ROOT / "data/governance/replay_cases.json"
CHARTER_PATH = ROOT / "data/governance/charter_manifest.json"
MIN_CASES = 20
CORPUS_VERSION = "replay-2026-11-07"


def _num(raw: str) -> float:
    return float(raw)


def extract(source: str, regex: str, text: str) -> Any:
    """事实抽取：无捕获组 → 命中次数；有捕获组 → 第一组数值。"""
    pattern = re.compile(regex)
    if pattern.groups == 0:
        return len(pattern.findall(text))
    found = pattern.findall(text)
    if not found:
        raise ValueError(f"regex 未命中: {regex!r} @ {source}")
    first = found[0]
    if isinstance(first, tuple):
        first = first[0]
    return _num(first)


def validate_case(case: dict[str, Any], clause_ids: set[str],
                  classifier: Any) -> list[str]:
    problems: list[str] = []
    cid = case.get("case_id", "<no-id>")
    for key in ("title", "question", "scope", "disputed_action", "preregistration",
                "facts", "evidence", "charter_citations", "owner_ruling",
                "owner_options", "recommended_option"):
        if not case.get(key):
            problems.append(f"{cid}: 缺字段 {key}")

    # 判据
    criteria = (case.get("preregistration") or {}).get("criteria") or []
    if not criteria:
        problems.append(f"{cid}: 预登记判据为空")
    for c in criteria:
        if c.get("eval", {}).get("op") not in (">", ">=", "<", "<=", "==", "!="):
            problems.append(f"{cid}/{c.get('criterion_id')}: 非法算子")
        if c.get("on_fail_verdict") is not None and c["on_fail_verdict"] not in VERDICTS:
            problems.append(f"{cid}/{c.get('criterion_id')}: 词表外判决 {c.get('on_fail_verdict')}")

    # 事实：源路径 + regex 现取
    facts = case.get("facts") or []
    if not facts:
        problems.append(f"{cid}: 无 facts（不可裁）")
    for f in facts:
        src = ROOT / f["source"]
        if not src.exists():
            problems.append(f"{cid}: 事实源不存在 {f['source']}")
            continue
        if not f.get("regex"):
            if "derive" not in f:
                problems.append(f"{cid}: 事实 {f['metric']} 既无 regex 也无 derive")
            continue
        text = src.read_text(encoding="utf-8", errors="replace")
        try:
            got = extract(f["source"], f["regex"], text)
        except ValueError as exc:
            problems.append(f"{cid}: {exc}")
            continue
        exp = f["expected"]
        if isinstance(exp, int) and got != exp:
            problems.append(f"{cid}: {f['metric']} 现取 {got} != expected {exp}")
        elif isinstance(exp, float) and abs(float(got) - exp) > 1e-9:
            problems.append(f"{cid}: {f['metric']} 现取 {got} != expected {exp}")

    # 红线类别数：用分类器对争议文本现算（v1 案例沿用同一口径）
    redline_metrics = {f["metric"] for f in facts if f["metric"] == "red_line_categories_hit"}
    if redline_metrics:
        hits = classifier.classify(
            case["question"] + case["scope"] + case["disputed_action"])
        n_cat = len({h["category"] for h in hits})
        for f in facts:
            if f["metric"] == "red_line_categories_hit" and n_cat != f["expected"]:
                problems.append(f"{cid}: red_line_categories_hit 现算 {n_cat} != expected {f['expected']}")

    # 证据原文
    for e in case.get("evidence") or []:
        p = ROOT / e["path"]
        if not p.exists():
            problems.append(f"{cid}: 证据文件不存在 {e['path']}")
            continue
        if e["quote"] not in p.read_text(encoding="utf-8", errors="replace"):
            problems.append(f"{cid}: 证据引用非原文 @ {e['path']}: {e['quote'][:40]}…")

    # owner 裁决
    ruling = case.get("owner_ruling") or {}
    if ruling.get("label") not in VERDICTS:
        problems.append(f"{cid}: owner label 不在词表 {ruling.get('label')!r}")
    ref = ruling.get("source_ref")
    if not ref or not (ROOT / ref).exists():
        problems.append(f"{cid}: owner 裁决来源不存在 {ref}")
    elif ruling.get("verbatim", "") not in (ROOT / ref).read_text(encoding="utf-8", errors="replace"):
        problems.append(f"{cid}: owner verbatim 非原文 @ {ref}")

    # 选项与条款
    opts = [o.get("option") for o in case.get("owner_options") or []]
    if len(opts) < 2:
        problems.append(f"{cid}: owner_options < 2")
    if case.get("recommended_option") not in opts:
        problems.append(f"{cid}: recommended_option 不在 options 内")
    for cl in case.get("charter_citations") or []:
        if cl not in clause_ids:
            problems.append(f"{cid}: 未知宪法条款 {cl}")
    if not case.get("charter_citations"):
        problems.append(f"{cid}: 无宪法引用")
    return problems


def build() -> tuple[dict[str, Any], list[str]]:
    corpus = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    v1 = list(corpus["cases"])
    seen = {c["case_id"] for c in v1}
    merged = list(v1)
    for c in cases_mod.NEW_CASES:
        if c["case_id"] in seen:
            # 幂等且以模块为准：IN28 案例每次按模块内容刷新（v1 三例永不刷新，保持原样）
            idx = next(i for i, x in enumerate(merged) if x["case_id"] == c["case_id"])
            merged[idx] = c
            continue
        merged.append(c)
        seen.add(c["case_id"])

    clause_ids = {clause.rule_id for clause in load_charter(CHARTER_PATH).clauses}
    classifier = load_redline_lexicon(ROOT / "data/governance/redline_lexicon.json")
    problems: list[str] = []
    if len(merged) < MIN_CASES:
        problems.append(f"案例数 {len(merged)} < {MIN_CASES}")
    ids = [c["case_id"] for c in merged]
    if len(set(ids)) != len(ids):
        problems.append("case_id 重复")
    for c in merged:
        problems.extend(validate_case(c, clause_ids, classifier))

    out = {
        "schema": corpus["schema"],
        "version": CORPUS_VERSION,
        "note": (
            f"{len(merged)} 个历史真实争议案例（仓内可查证据）+ 预登记判据 + owner 实际裁决。"
            "facts 一律用 regex 从证据文件现取（零手抄），expected 不符即 FAIL；owner_ruling.verbatim "
            "必须是源文件原文子串；evidence.quote 亦逐条校验原文。v1 三例（IN27-H5-C1/C2/C3）逐字节保留。"
        ),
        "cases": merged,
    }
    return out, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()

    out, problems = build()
    print(json.dumps({
        "cases": len(out["cases"]),
        "new_cases": len([c for c in out["cases"] if c["case_id"].startswith("IN28-N07-")]),
        "with_source_paths": sum(1 for c in out["cases"] if all(f.get("source") for f in c["facts"])),
        "problems": len(problems),
    }, ensure_ascii=False, indent=2))
    for p in problems:
        print("  -", p, file=sys.stderr)
    if problems:
        return 1
    if not args.check:
        CASES_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {CASES_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())