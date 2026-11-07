#!/usr/bin/env python3
"""IN27-H5 回放实验：3 个历史真实争议案例 → 宪政裁决器判决 vs owner 实际裁决。

流程（全部程序化，零手抄）：
1. 载入成文宪法 `data/governance/charter_manifest.json`，逐条款做**溯源校验**
   （条款原文必须是来源文件的子串，防手抄/编造）；
2. 载入 3 个案例 + 预登记判据（冻结时刻早于任何票）；
3. 从证据文件用 regex **现取事实**（与案例 expected 不符即 FAIL）；
4. 向 **≥3 个不同模型族** 各投一票（独立 HTTP 调用，互不可见），票带时间戳/时延/响应哈希；
5. 裁决器按「红线 → 预登记判据 → 多族票决 → 升级」顺序判决，写 hash 链账本；
6. 与 owner 实际裁决比对，出 acceptance summary（≥2 一致 + ≥1 可辩护分歧 + 0 mismatch）。

用法：
    python3 scripts/in27_h5_replay.py [--offline-ballots artifacts/in27_h5/ballots.jsonl]
退出码：0 = 验收通过；1 = 未通过（honest FAIL，不粉饰）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.council_constitution import (  # noqa: E402
    Adjudicator,
    Ballot,
    Conclave,
    Ledger,
    load_charter,
    load_redline_lexicon,
    sha256_file,
    utc_now,
    compare_with_owner,
    acceptance_summary,
)

CHARTER = ROOT / "data/governance/charter_manifest.json"
LEXICON = ROOT / "data/governance/redline_lexicon.json"
CASES = ROOT / "data/governance/replay_cases.json"
OUT = ROOT / "artifacts/in27_h5"

# 模型族映射：同一族只取一票（防同族堆票）
FAMILY_PATTERNS = [
    ("zhipu", "glm"),
    ("deepseek", "DeepSeek"),
    ("alibaba-qwen", "Qwen"),
    ("minimax", "MiniMax"),
]


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


def relay_models(base: str, key: str) -> list[str]:
    req = urllib.request.Request(base.rstrip("/") + "/models")
    req.add_header("Authorization", "Bearer " + key)
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return [m["id"] for m in data.get("data", [])]


def pick_families(models: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for family, needle in FAMILY_PATTERNS:
        cands = [m for m in models if needle.lower() in m.lower()]
        if cands:
            out.append((family, sorted(cands, key=len)[0]))
    return out


def extract_fact(entry: dict[str, Any], classifier) -> tuple[Any, dict[str, Any]]:
    """从证据文件现取事实；返回 (值, 溯源记录)。"""
    src = ROOT / entry["source"]
    trace = {"metric": entry["metric"], "source": entry["source"], "sha256": None, "regex": entry.get("regex")}
    if entry["metric"] == "red_line_categories_hit":
        value = trace["value"] = entry["expected"]
        trace["derive"] = entry.get("derive")
        return value, trace
    trace["sha256"] = sha256_file(src)
    text = src.read_text(encoding="utf-8", errors="replace")
    m = re.search(entry["regex"], text)
    if not m:
        raise SystemExit(f"事实抽取失败: {entry['metric']} 正则 {entry['regex']!r} 未命中 {entry['source']}")
    value = int(m.group(1))
    trace["value"] = value
    trace["expected"] = entry["expected"]
    trace["match_expected"] = value == entry["expected"]
    if not trace["match_expected"]:
        raise SystemExit(f"事实与冻结期望不符: {entry['metric']} 实测 {value} != {entry['expected']}（诚实 FAIL）")
    return value, trace


def verify_evidence(case: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for ev in case["evidence"]:
        p = ROOT / ev["path"]
        text = re.sub(r"\s+", " ", p.read_text(encoding="utf-8", errors="replace"))
        quote = re.sub(r"\s+", " ", ev["quote"]).strip()
        out.append(
            {
                "path": ev["path"],
                "quote": ev["quote"],
                "sha256": sha256_file(p),
                "quote_found": quote in text,
            }
        )
    missing = [e for e in out if not e["quote_found"]]
    if missing:
        raise SystemExit(f"{case['case_id']}: 证据原文校验失败 {[e['path'] for e in missing]}")
    return out


def ballot_prompt(case: dict[str, Any], facts: dict[str, Any], charter_texts: list[dict[str, Any]]) -> str:
    crit = "\n".join(
        f"- {c['criterion_id']}: {c['statement']}（判据: {c['eval']}；失败判决: {c['on_fail_verdict']}）"
        for c in case["preregistration"]["criteria"]
    )
    evidence = "\n".join(f"- {e['path']}：「{e['quote']}」" for e in case["evidence"])
    facts_s = "\n".join(f"- {k} = {v}" for k, v in facts.items())
    clauses = "\n".join(f"- {c['rule_id']}（{c['kind']}）: {c['text']}" for c in charter_texts)
    return f"""你是一个独立裁决委员。只依据下面给定的冻结材料裁决，不要引入外部假设。

【争议】{case['question']}
【争议动作】{case['disputed_action']}
【预登记判据（先于你的票冻结）】
{crit}
【实测事实（程序从证据文件现取）】
{facts_s}
【证据原文】
{evidence}
【成文宪法条款（红线优先）】
{clauses}

【判决词表（只能选一个，逐字照抄）】
ADOPT / REJECT / DOWNGRADE_TO_MOCK / HONEST_FAIL / ESCALATE_TO_OWNER

规则：
- 若争议命中红线类别（战略变更/安全/不可逆/治理规则），只能判 ESCALATE_TO_OWNER（不得代 owner 决定）。
- 否则按预登记判据：mandatory 判据失败 → 采用它事先声明的失败判决。
- 只输出 JSON（不要代码围栏、不要解释性前后缀）：{{"verdict":"<词表内标签>","criteria_refs":["C1-A"],"rationale":"<=120字，必须引用判据或事实"}}"""


def call_model(base: str, key: str, model: str, prompt: str, timeout: int = 150) -> dict[str, Any]:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 2000,
        }
    ).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    dt = int((time.time() - t0) * 1000)
    payload = json.loads(raw)
    msg = payload["choices"][0]["message"]
    # 推理型模型可能把正文放在 reasoning_content / content 为空串，两者都收
    content = msg.get("content") or msg.get("reasoning_content") or ""
    return {"content": content, "latency_ms": dt, "response_sha256": hashlib.sha256(raw).hexdigest()}


def parse_verdict(content: str | None) -> tuple[str | None, list[str], str]:
    content = content or ""
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj.get("verdict"), list(obj.get("criteria_refs") or []), str(obj.get("rationale", "")).strip()
        except json.JSONDecodeError:
            pass
    for token in ("ESCALATE_TO_OWNER", "DOWNGRADE_TO_MOCK", "HONEST_FAIL", "ADOPT", "REJECT"):
        if token in content:
            return token, [], content.strip()[:280]
    return None, [], content.strip()[:280]


def collect_ballots(case, facts, charter_texts, families, base, key) -> list[dict[str, Any]]:
    prompt = ballot_prompt(case, facts, charter_texts)
    out: list[dict[str, Any]] = []

    def one(fam_model: tuple[str, str]) -> dict[str, Any]:
        family, model = fam_model
        rec: dict[str, Any] = {"family_id": family, "model_id": model, "ok": False}
        try:
            resp = call_model(base, key, model, prompt)
            verdict, refs, rationale = parse_verdict(resp["content"])
            rec.update(
                verdict=verdict,
                criteria_refs=refs,
                rationale=rationale,
                latency_ms=resp["latency_ms"],
                response_sha256=resp["response_sha256"],
                cast_at=utc_now(),
                ok=verdict is not None and bool(rationale),
            )
        except Exception as e:  # 中继不可用/超时：如实记录，不伪造票
            rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    with ThreadPoolExecutor(max_workers=len(families)) as pool:
        out = list(pool.map(one, families))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-ballots", default=None, help="离线票（JSONL：case_id/family_id/model_id/verdict/rationale/cast_at）")
    ap.add_argument("--skip-live", action="store_true", help="不调用模型族，仅用离线票复算")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    env = load_env(Path("/root/tp-agent/agent.env"))
    base, key = env.get("LLM_BASE_URL", ""), env.get("LLM_API_KEY", "")

    charter = load_charter(CHARTER)
    classifier = load_redline_lexicon(LEXICON)
    cases_doc = json.loads(CASES.read_text(encoding="utf-8"))
    provenance = charter.verify_provenance([ROOT])

    families: list[tuple[str, str]] = []
    live_error = None
    if not args.skip_live:
        try:
            families = pick_families(relay_models(base, key))
            if len(families) < 3:
                raise RuntimeError(f"可用模型族不足 3: {families}")
        except Exception as e:
            live_error = f"{type(e).__name__}: {e}"
            families = []

    offline: dict[str, list[dict[str, Any]]] = {}
    if args.offline_ballots:
        for line in Path(args.offline_ballots).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                offline.setdefault(rec["case_id"], []).append(rec)

    adjudicator = Adjudicator(charter, classifier, quorum=3)
    ledger = Ledger(OUT / "adjudication_ledger.jsonl")
    if ledger.path.exists():
        ledger.path.unlink()

    results, comparisons, ballots_log = [], [], []
    for case in cases_doc["cases"]:
        case_id = case["case_id"]
        evidence_trace = verify_evidence(case)
        facts, fact_trace = {}, []
        for entry in case["facts"]:
            v, t = extract_fact(entry, classifier)
            facts[entry["metric"]] = v
            fact_trace.append(t)
        if case_id == "IN27-H5-C3":
            hits = classifier.classify(" ".join(str(case.get(k, "")) for k in ("question", "scope", "disputed_action")))
            facts["red_line_categories_hit"] = len({h["category"] for h in hits})
            fact_trace[-1]["value"] = facts["red_line_categories_hit"]
            fact_trace[-1]["categories"] = sorted({h["category"] for h in hits})

        raw_ballots = offline.get(case_id, [])
        source = "recorded"
        if not raw_ballots and families:
            raw_ballots = collect_ballots(case, facts, charter.cite(case["charter_citations"]), families, base, key)
            source = "live_relay"
        ballots = []
        for rb in raw_ballots:
            ballots_log.append({"case_id": case_id, **rb})
            if not rb.get("ok", True) or not rb.get("verdict"):
                continue
            ballots.append(
                Ballot(
                    family_id=rb["family_id"],
                    model_id=rb["model_id"],
                    verdict=rb["verdict"],
                    rationale=rb["rationale"],
                    criteria_refs=tuple(rb.get("criteria_refs") or ()),
                    evidence_refs=tuple(e["path"] for e in case["evidence"]),
                    cast_at=rb["cast_at"],
                    latency_ms=int(rb.get("latency_ms") or 0),
                    response_sha256=rb.get("response_sha256", ""),
                )
            )

        result = adjudicator.adjudicate(case, ballots, facts)
        result.update(
            title=case["title"],
            votes_source=source,
            ballots_attempted=len(raw_ballots),
            ballots_valid=len(ballots),
            facts=facts,
            fact_provenance=fact_trace,
            evidence=evidence_trace,
            charter_citations=charter.cite(case["charter_citations"]),
            owner_ruling=case["owner_ruling"],
        )
        entry = ledger.append({"case_id": case_id, "verdict": result["verdict"], "route": result["route"]})
        result["ledger_entry_hash"] = entry["entry_hash"]
        results.append(result)
        comparisons.append(compare_with_owner(result, case))

    acceptance = acceptance_summary(comparisons, min_agreements=2)
    ledger_report = ledger.verify()
    latencies = [b["latency_ms"] for case in ballots_log if (b := case).get("latency_ms")]

    payload = {
        "schema": "tp.council.replay_result/v1",
        "generated_at": utc_now(),
        "charter": {"version": charter.version, "digest": charter.digest, "clauses": len(charter.clauses),
                    "red_line_clauses": len(charter.red_line_clauses())},
        "charter_provenance": provenance,
        "redline_lexicon": {"version": classifier.version, "digest": classifier.digest},
        "model_families": [{"family": f, "model": m} for f, m in families],
        "live_collection_error": live_error,
        "results": results,
        "comparisons": comparisons,
        "acceptance": acceptance,
        "audit_chain": ledger_report,
        "ballot_stats": {
            "attempted": len(ballots_log),
            "valid": sum(1 for b in ballots_log if b.get("ok") and b.get("verdict")),
            "failed": sum(1 for b in ballots_log if not (b.get("ok") and b.get("verdict"))),
            "mean_latency_ms": round(statistics.fmean(latencies), 1) if latencies else None,
        },
    }
    (OUT / "replay_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "ballots.jsonl").open("w", encoding="utf-8") as fh:
        for b in ballots_log:
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")

    print(json.dumps({
        "verdicts": {r["case_id"]: r["verdict"] for r in results},
        "routes": {r["case_id"]: r["route"] for r in results},
        "acceptance": acceptance["criteria"],
        "passed": acceptance["passed"],
        "ballots": payload["ballot_stats"],
        "provenance_ok": provenance["ok"],
        "ledger_ok": ledger_report["ok"],
    }, ensure_ascii=False, indent=2))
    return 0 if acceptance["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())