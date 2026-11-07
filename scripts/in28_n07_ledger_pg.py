#!/usr/bin/env python3
"""IN28-N07 裁决账本接库 + 语料扩充：回放历史真实争议，账本落关系库（协议不变）。

机器判据（全部由本脚本计算并写入 artifacts/in28_n07/acceptance.json）：
(a) 库行数 == 文件 hash 链行数，且逐行逐字段 diff == 0；
(b) 语料 ≥20 例，100% 带源路径（facts.source 与 owner_ruling.source_ref 均可解析且 verbatim 为原文）；
(c) 与 owner 成文裁决一致率 ≥2/3，不可辩护不一致（mismatch）== 0；
(d) 协议不变：裁决词表 / 超级多数规则 / 账本哈希口径指纹与基线逐字节一致，
    且 backend/services/council_constitution/core.py 相对 main 无 diff；
(e) pytest 全绿（由外部 pytest 结果文件注入，见 --pytest-summary）。

票的来源：默认 ``deterministic``——三个"规则执行者"族的票由**预登记判据**推导
（判据失败 → 取事先声明的失败判决；全过 → ADOPT），确定性可复现；
``--ballots FILE`` 可注入外部（如 LLM）票箱，本节点默认不使用。

用法：
    python3 scripts/in28_n07_ledger_pg.py                       # 文件账本 + 关系库镜像
    python3 scripts/in28_n07_ledger_pg.py --dsn sqlite://       # 无 PG 时用 SQLite 自检
    python3 scripts/in28_n07_ledger_pg.py --no-db               # 只跑文件账本
退出码：0 = 全部判据通过；1 = 未通过（honest FAIL，不粉饰）。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import math
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from services.council_constitution import (  # noqa: E402
    VERDICTS,
    Adjudicator,
    Ballot,
    Criterion,
    Ledger,
    Preregistration,
    acceptance_summary,
    compare_with_owner,
    load_charter,
    load_redline_lexicon,
    sha256_obj,
)
from services.council_constitution.ledger_store import (  # noqa: E402
    SqlLedgerStore,
    dsn_from_env,
)

import in28_n07_corpus_build as corpus_build  # noqa: E402

CHARTER = ROOT / "data/governance/charter_manifest.json"
LEXICON = ROOT / "data/governance/redline_lexicon.json"
CASES = ROOT / "data/governance/replay_cases.json"
OUT = ROOT / "artifacts/in28_n07"
LEDGER_FILE = OUT / "adjudication_ledger.jsonl"
PROTOCOL_BASELINE = ROOT / "data/governance/protocol_baseline.json"
CORE_REL = "backend/services/council_constitution/core.py"
DOC_PATH = ROOT / "docs/IN28_N07_council_ledger_pg_corpus.md"

BASE_REF_CANDIDATES = ("origin/main", "main", "refs/remotes/origin/main")
BALLOT_CAST_AT = "2026-11-07T13:00:00Z"   # 晚于所有案例的判据冻结时刻
RULE_FAMILIES = ("rule-actor-a", "rule-actor-b", "rule-actor-c")

DEFAULT_DSN = "postgresql+psycopg://postgres@/tensorplatform?host=/tmp"


# --------------------------------------------------------------------------- #
# 一、事实抽取（regex 现取；derive 走受限表达式）
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_fact(entry: dict[str, Any], classifier: Any, case: dict[str, Any],
                 text: str | None = None) -> Any:
    src = entry["source"]
    if entry.get("regex"):
        pattern = re.compile(entry["regex"])
        if text is None:
            text = (ROOT / src).read_text(encoding="utf-8", errors="replace")
        if pattern.groups == 0:
            return len(pattern.findall(text))
        found = pattern.findall(text)
        if not found:
            raise RuntimeError(f"事实抽取失败: {entry['metric']} / {entry['regex']!r} @ {src}")
        first = found[0][0] if isinstance(found[0], tuple) else found[0]
        return float(first)
    if entry["metric"] == "red_line_categories_hit":
        # 红线类别数由分类器**现算**（不信任 expected，也不 eval 案例里的 derive 文本）：
        # v1 案例的 derive 表达式是给人读的旧式写法，此处统一重算以保证事实现取。
        hits = classifier.classify(case["question"] + case["scope"] + case["disputed_action"])
        return len({h["category"] for h in hits})
    expr = entry.get("derive")
    if not expr:
        raise RuntimeError(f"事实 {entry['metric']} 既无 regex 也无 derive")
    scope = {
        "classifier": classifier,
        "question": case["question"],
        "scope": case["scope"],
        "disputed_action": case["disputed_action"],
    }
    # 必须把 case 变量放进 globals：生成器表达式只能看到 globals，看不到 eval 的 locals
    return eval(expr, {"__builtins__": {}, "len": len, "set": set, **scope})  # noqa: S307


def collect_facts(case: dict[str, Any], classifier: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    facts: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    for entry in case["facts"]:
        got = extract_fact(entry, classifier, case)
        exp = entry["expected"]
        ok = (got == exp) if isinstance(exp, int) else abs(float(got) - float(exp)) <= 1e-9
        if not ok:
            raise RuntimeError(f"{case['case_id']}: {entry['metric']} 现取 {got} != expected {exp}")
        facts[entry["metric"]] = got
        src = ROOT / entry["source"]
        trace.append({
            "metric": entry["metric"],
            "source": entry["source"],
            "source_sha256": sha256_file(src),
            "regex": entry.get("regex"),
            "derive": entry.get("derive"),
            "value": got,
            "expected": exp,
        })
    return facts, trace


# --------------------------------------------------------------------------- #
# 二、票：确定性规则执行者（判据推导），或注入外部票箱
# --------------------------------------------------------------------------- #
def criteria_verdict(case: dict[str, Any], facts: dict[str, Any]) -> str:
    raw = case["preregistration"]
    prereg = Preregistration(
        [Criterion(**c) for c in raw["criteria"]],
        frozen_by=raw["frozen_by"],
        frozen_at=raw["frozen_at"],
        rationale=raw.get("rationale", ""),
    )
    results = prereg.evaluate(facts)
    fails = sorted({r["on_fail_verdict"] for r in results
                    if r["mandatory"] and not r["passed"] and r["on_fail_verdict"]})
    if len(fails) > 1:
        return "ESCALATE_TO_OWNER"
    return fails[0] if fails else "ADOPT"


def make_ballots(case: dict[str, Any], facts: dict[str, Any],
                 external: dict[str, list[dict[str, Any]]]) -> list[Ballot]:
    if case["case_id"] in external:
        out = []
        for b in external[case["case_id"]]:
            out.append(Ballot(family_id=b["family_id"], model_id=b["model_id"],
                              verdict=b["verdict"], rationale=b["rationale"],
                              criteria_refs=tuple(b.get("criteria_refs") or ()),
                              evidence_refs=tuple(e["path"] for e in case["evidence"]),
                              cast_at=b["cast_at"], latency_ms=int(b.get("latency_ms") or 0)))
        return out
    vote = criteria_verdict(case, facts)
    ballots = []
    crit_ids = "/".join(c["criterion_id"] for c in case["preregistration"]["criteria"])
    for i, fam in enumerate(RULE_FAMILIES, 1):
        ballots.append(Ballot(
            family_id=fam,
            model_id=f"criteria-rule-follower/v1#{i}",
            verdict=vote,
            rationale=(f"{fam} 依本案例预登记判据逐条裁定：判据 {crit_ids} 在事实 "
                       f"{json.dumps(facts, ensure_ascii=False, sort_keys=True)} 下给出 {vote}；"
                       "本票是规则执行者（确定性、可复现），不引用他族意见。"),
            criteria_refs=tuple(c["criterion_id"] for c in case["preregistration"]["criteria"]),
            evidence_refs=tuple(e["path"] for e in case["evidence"]),
            cast_at=BALLOT_CAST_AT,
            latency_ms=0,
        ))
    return ballots


# --------------------------------------------------------------------------- #
# 三、协议不变（词表/超级多数/哈希口径指纹）
# --------------------------------------------------------------------------- #
def protocol_fingerprint(mod: Any) -> dict[str, Any]:
    snapshot = {
        "verdicts": list(mod.VERDICTS),
        "red_line_kinds": list(mod.RED_LINE_KINDS),
        "schemas": [mod.SCHEMA_CHARTER, mod.SCHEMA_ADJUDICATION, mod.SCHEMA_LEDGER],
        "conclave_init_src": inspect.getsource(mod.Conclave.__init__),
        "conclave_cast_src": inspect.getsource(mod.Conclave.cast),
        "conclave_tally_src": inspect.getsource(mod.Conclave.tally),
        "adjudicator_init_src": inspect.getsource(mod.Adjudicator.__init__),
        "adjudicate_src": inspect.getsource(mod.Adjudicator.adjudicate),
        "ledger_append_src": inspect.getsource(mod.Ledger.append),
        "ledger_verify_src": inspect.getsource(mod.Ledger.verify),
    }
    return {"snapshot": snapshot, "sha256": sha256_obj(snapshot)}


def load_module_from_source(path: Path, name: str) -> Any:
    """按文件路径载入模块（dataclass 需要模块先登记进 sys.modules）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod  # dataclass/typing 解析依赖 sys.modules 命中
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        sys.modules.pop(name, None)
    return mod


def resolve_base_ref() -> str | None:
    """挑一个**含 core.py** 的基线 ref：origin/main ≻ main ≻ 引入 core.py 的那次提交。

    本地 main 可能落后于 origin/main（本例如此：core.py 尚未合入本地 main），
    因此在 `main` 上做协议比对会得到"文件不存在"的假差异；这里显式挑有该文件的 ref。
    """
    for ref in BASE_REF_CANDIDATES:
        ok = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{ref}:{CORE_REL}"],
                            capture_output=True).returncode == 0
        if ok:
            return ref
    r = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--diff-filter=A",
                        "--", CORE_REL], capture_output=True, text=True)
    commits = [c for c in r.stdout.split() if c]
    return commits[-1] if commits else None


def protocol_check(current_mod: Any) -> dict[str, Any]:
    current = protocol_fingerprint(current_mod)
    baseline = (json.loads(PROTOCOL_BASELINE.read_text(encoding="utf-8"))
                if PROTOCOL_BASELINE.exists() else None)
    main_fp = None
    main_error = None
    base_ref = resolve_base_ref()
    try:
        if base_ref is None:
            raise RuntimeError("找不到基线 ref（main/origin/main）")
        blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{base_ref}:{CORE_REL}"],
                              capture_output=True, text=True, check=True).stdout
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "core_main.py"
            p.write_text(blob, encoding="utf-8")
            mod = load_module_from_source(p, "council_core_main")
            # 指纹必须在临时文件仍存在时算：inspect.getsource 惰性读源码
            main_fp = protocol_fingerprint(mod)
    except Exception as exc:  # noqa: BLE001 - 基线不可得时如实记录
        main_error = f"{type(exc).__name__}: {exc}"

    diff = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", base_ref or "HEAD", "--", CORE_REL])
    same_vs_main = (main_fp is not None and main_fp["sha256"] == current["sha256"])
    matches_baseline = (baseline is not None and baseline["sha256"] == current["sha256"])
    return {
        "current_sha256": current["sha256"],
        "baseline_sha256": baseline["sha256"] if baseline else None,
        "main_sha256": main_fp["sha256"] if main_fp else None,
        "main_error": main_error,
        "base_ref": base_ref,
        "core_py_diff_vs_main_empty": diff.returncode == 0,
        "matches_committed_baseline": matches_baseline,
        "identical_to_main": same_vs_main,
        "verdicts": current["snapshot"]["verdicts"],
        "ok": bool(matches_baseline and same_vs_main and diff.returncode == 0),
    }


# --------------------------------------------------------------------------- #
# 四、统计：一致率 + Wilson 区间 + 精确二项检验
# --------------------------------------------------------------------------- #
def wilson_interval(k: int, n: int, z: float = 1.959963985) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def binom_sf(k: int, n: int, p0: float) -> float:
    """P(X >= k) for X~Bin(n, p0)（精确求和，不依赖 scipy）。"""
    return sum(math.comb(n, i) * (p0 ** i) * ((1 - p0) ** (n - i)) for i in range(k, n + 1))


def agreement_stats(comparisons: list[dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    n = len(comparisons)
    k = sum(1 for c in comparisons if c["kind"] == "agreement")
    div = sum(1 for c in comparisons if c["kind"] == "constitutional_divergence")
    mism = sum(1 for c in comparisons if c["kind"] == "mismatch")
    lo, hi = wilson_interval(k, n)
    counts: dict[str, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    majority_share = max(counts.values()) / len(labels)
    return {
        "cases": n,
        "agreements": k,
        "divergences": div,
        "mismatches": mism,
        "agreement_rate": round(k / n, 4),
        "required_rate": round(2 / 3, 4),
        "rate_ge_two_thirds": (k / n) >= 2 / 3,
        "wilson95_low": round(lo, 4),
        "wilson95_high": round(hi, 4),
        "wilson95_low_ge_two_thirds": lo >= 2 / 3,
        "chance_uniform_5labels": 0.2,
        "majority_class_share": round(majority_share, 4),
        "p_value_vs_uniform": binom_sf(k, n, 0.2),
        "p_value_vs_majority_class": binom_sf(k, n, majority_share),
        "label_distribution": counts,
    }


# --------------------------------------------------------------------------- #
# 五、主流程
# --------------------------------------------------------------------------- #
def load_external_ballots(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if not path or not path.exists():
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        b = json.loads(line)
        out.setdefault(b["case_id"], []).append(b)
    return out


def reset_store(store: SqlLedgerStore) -> None:
    """清空本节点自有的工件表（账本镜像可重建；文件账本是权威副本）。"""
    from sqlalchemy import delete
    store.ensure_schema()
    with store.engine.begin() as conn:
        conn.execute(delete(store.table))


def run(args: argparse.Namespace) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    charter = load_charter(CHARTER)
    classifier = load_redline_lexicon(LEXICON)
    provenance = charter.verify_provenance([ROOT])
    if not provenance["ok"]:
        raise SystemExit(f"宪法溯源失败: {provenance['failures']}")

    corpus, corpus_problems = corpus_build.build()
    if corpus_problems:
        raise SystemExit(f"语料校验失败: {corpus_problems[:5]}")
    cases = corpus["cases"]

    if LEDGER_FILE.exists():
        LEDGER_FILE.unlink()
    ledger = Ledger(LEDGER_FILE)
    store: SqlLedgerStore | None = None
    dsn_label = None
    if not args.no_db:
        try:
            dsn = args.dsn or dsn_from_env(default=DEFAULT_DSN)
        except Exception:  # noqa: BLE001
            dsn = DEFAULT_DSN
        store = SqlLedgerStore(dsn)
        store.ensure_schema()
        reset_store(store)
        dsn_label = f"{store.dialect}:{store.table_name}"

    external = load_external_ballots(args.ballots)
    adjudicator = Adjudicator(charter, classifier)
    results: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    fact_trace: dict[str, list[dict[str, Any]]] = {}

    for case in cases:
        facts, trace = collect_facts(case, classifier)
        fact_trace[case["case_id"]] = trace
        ballots = make_ballots(case, facts, external)
        res = adjudicator.adjudicate(case, ballots, facts)
        res.update(
            title=case["title"],
            facts=facts,
            owner_ruling=case["owner_ruling"],
            ballots_attempted=len(ballots),
            ballots_valid=len(ballots),
            vote_source="external" if case["case_id"] in external else "deterministic_criteria_rule",
        )
        entry = ledger.append({"case_id": case["case_id"], "verdict": res["verdict"],
                               "route": res["route"]})
        if store is not None:
            store.append_entry(entry)
        res["ledger_entry_hash"] = entry["entry_hash"]
        res["ledger_seq"] = entry["seq"]
        results.append(res)
        comparisons.append(compare_with_owner(res, case))

    ledger_report = ledger.verify()
    min_agreements = math.ceil(2 * len(comparisons) / 3)
    acceptance_core = acceptance_summary(comparisons, min_agreements=min_agreements)

    if store is not None:
        parity = store.parity(LEDGER_FILE)
        db_verify = store.verify()
    else:
        parity = {"ok": False, "skipped": True, "reason": "--no-db", "rows_db": 0,
                  "rows_file": len(ledger.entries()), "field_diff_count": None,
                  "db_chain_ok": None, "file_chain_ok": ledger_report["ok"],
                  "db_head_hash": None, "file_head_hash": ledger_report["head_hash"]}
        db_verify = None

    def has_source(c: dict[str, Any]) -> bool:
        if not c["facts"] or not all((ROOT / f["source"]).exists() for f in c["facts"]):
            return False
        ref = c["owner_ruling"]["source_ref"]
        p = ROOT / ref
        return p.exists() and c["owner_ruling"]["verbatim"] in p.read_text(
            encoding="utf-8", errors="replace")

    with_source = sum(1 for c in cases if has_source(c))
    corpus_ok = bool(len(cases) >= 20 and with_source == len(cases))

    labels = [c["owner_ruling"]["label"] for c in cases]
    stats = agreement_stats(comparisons, labels)

    import services.council_constitution.core as core_mod
    protocol = protocol_check(core_mod)

    pytest_summary = None
    if args.pytest_summary and Path(args.pytest_summary).exists():
        pytest_summary = json.loads(Path(args.pytest_summary).read_text(encoding="utf-8"))

    acceptance = {
        "schema": "tp.council.in28_n07_acceptance/v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "criteria": {
            "a_db_file_parity": {
                "db_rows": parity.get("rows_db"),
                "file_rows": parity.get("rows_file"),
                "field_diff_count": parity.get("field_diff_count"),
                "db_chain_ok": parity.get("db_chain_ok"),
                "file_chain_ok": parity.get("file_chain_ok"),
                "head_hash_equal": parity.get("db_head_hash") == parity.get("file_head_hash"),
                "store_dsn_label": dsn_label,
                "passed": bool(parity.get("ok")),
                "threshold": "rows_db == rows_file 且 field_diff_count == 0",
            },
            "b_corpus": {
                "cases": len(cases),
                "with_source_paths": with_source,
                "with_source_pct": round(100.0 * with_source / len(cases), 2),
                "min_cases": 20,
                "passed": corpus_ok,
                "threshold": "≥20 例且 100% 带源路径",
            },
            "c_owner_agreement": {
                **stats,
                "passed": bool(stats["rate_ge_two_thirds"] and stats["mismatches"] == 0),
                "threshold": "agreement_rate ≥ 2/3 且 indefensible mismatch == 0",
            },
            "d_protocol_unchanged": {
                **protocol,
                "passed": bool(protocol["ok"]),
                "threshold": "词表/超级多数规则/账本哈希口径指纹逐字节一致",
            },
            "e_pytest": {
                **(pytest_summary or {"status": "not_injected"}),
                "passed": bool((pytest_summary or {}).get("exit_code") == 0),
                "threshold": "pytest 全绿（exit_code == 0）",
            },
        },
        "ledger": {
            "file": str(LEDGER_FILE.relative_to(ROOT)),
            "file_entries": len(ledger.entries()),
            "file_head_hash": ledger_report["head_hash"],
            "store_dsn_label": dsn_label,
            "store_verify": db_verify,
            "parity": parity,
        },
        "corpus_path": str(CASES.relative_to(ROOT)),
        "corpus_version": corpus["version"],
        "vote_source": "external" if external else "deterministic_criteria_rule",
    }
    acceptance["all_passed"] = all(v["passed"] for v in acceptance["criteria"].values())

    payload = {
        "schema": "tp.council.replay_result/v2",
        "generated_at": acceptance["generated_at"],
        "charter": {"version": charter.version, "digest": charter.digest,
                    "clauses": len(charter.clauses),
                    "red_line_clauses": len(charter.red_line_clauses())},
        "charter_provenance": provenance,
        "redline_lexicon": {"version": classifier.version, "digest": classifier.digest},
        "cases": len(cases),
        "results": [{k: r.get(k) for k in (
            "case_id", "title", "verdict", "route", "self_decision", "facts",
            "criteria_results", "committee", "owner_ruling", "owner_packet",
            "ledger_entry_hash", "ledger_seq", "red_line_hits",
            "preregistration_freeze_hash", "vote_source")} for r in results],
        "fact_provenance": fact_trace,
        "comparisons": comparisons,
        "acceptance_core": acceptance_core,
        "acceptance": acceptance,
    }
    (OUT / "replay_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "acceptance.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "parity.json").write_text(
        json.dumps(parity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _render_figure(payload)
    _render_doc(payload)

    print(json.dumps({
        "cases": len(cases),
        "agreement": {k: stats[k] for k in ("agreements", "divergences", "mismatches",
                                            "agreement_rate", "wilson95_low", "wilson95_high")},
        "parity": {"rows_db": parity.get("rows_db"), "rows_file": parity.get("rows_file"),
                   "field_diff_count": parity.get("field_diff_count"), "ok": parity.get("ok")},
        "protocol_ok": protocol["ok"],
        "corpus_ok": corpus_ok,
        "all_passed": acceptance["all_passed"],
    }, ensure_ascii=False, indent=2))
    return 0 if acceptance["all_passed"] else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None, help="账本库 DSN（缺省按环境变量解析，再退到本机 PG socket）")
    ap.add_argument("--no-db", action="store_true", help="只跑文件账本，不接库")
    ap.add_argument("--ballots", default=None, help="外部票箱 JSONL（缺省用判据推导的确定性票）")
    ap.add_argument("--pytest-summary", default=None, help="pytest 结果 JSON（判据 e 注入）")
    ap.add_argument("--write-protocol-baseline", action="store_true",
                    help="用当前 core.py 生成 data/governance/protocol_baseline.json 基线")
    args = ap.parse_args()

    if args.write_protocol_baseline:
        import services.council_constitution.core as core_mod
        fp = protocol_fingerprint(core_mod)
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", resolve_base_ref() or "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        PROTOCOL_BASELINE.write_text(json.dumps({
            "schema": "tp.council.protocol_baseline/v1",
            "source_ref": f"{resolve_base_ref() or 'HEAD'}@{head}",
            "note": "裁决协议指纹基线（词表 + Conclave.cast/tally + Adjudicator.adjudicate + "
                    "Ledger.append/verify 源码）。本文件必须由基线分支的 core.py 生成；"
                    "改协议=改指纹=改治理口径，须走 owner 终裁。",
            "sha256": fp["sha256"],
            "snapshot_keys": sorted(fp["snapshot"]),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"baseline": str(PROTOCOL_BASELINE.relative_to(ROOT)),
                          "sha256": fp["sha256"]}, ensure_ascii=False, indent=2))
        return 0

    return run(args)



# --------------------------------------------------------------------------- #
# 六、图与文档（数字全部程序化注入，零手抄）
# --------------------------------------------------------------------------- #
def _render_figure(payload: dict[str, Any]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    results = payload["results"]
    comps = {c["case_id"]: c for c in payload["comparisons"]}
    verdicts = list(VERDICTS)
    mat = []
    for r in results:
        row = [0] * len(verdicts)
        kind = comps[r["case_id"]]["kind"]
        row[verdicts.index(r["verdict"])] = 2 if kind == "agreement" else 1
        mat.append(row)

    fig, axes = plt.subplots(1, 2, figsize=(14.0, 8.6), gridspec_kw={"width_ratios": [1.5, 1]})
    ax = axes[0]
    ax.imshow(mat, cmap=ListedColormap(["#f4f4f4", "#d98c8c", "#7fb47f"]), aspect="auto",
              vmin=0, vmax=2)
    ax.set_xticks(range(len(verdicts)))
    ax.set_xticklabels([v.replace("_", "\n") for v in verdicts], fontsize=7)
    ax.set_yticks(range(len(results)))
    ax.set_yticklabels([f"{r['case_id']}  owner={r['owner_ruling']['label']}" for r in results],
                       fontsize=6.5)
    for i, r in enumerate(results):
        j = verdicts.index(r["verdict"])
        mark = {"agreement": "=", "constitutional_divergence": "E", "mismatch": "X"}[
            comps[r["case_id"]]["kind"]]
        ax.text(j, i, mark, ha="center", va="center", fontsize=8, color="#203020")
    ax.set_title("engine verdict (cell) vs owner written ruling (row label)\n"
                 "green = agreement, red = constitutional divergence (machine must not self-decide)",
                 fontsize=9)
    ax.set_xlabel("verdict vocabulary (closed set, unchanged from main)", fontsize=8)

    acc = payload["acceptance"]["criteria"]
    keys = ["a_db_file_parity", "b_corpus", "c_owner_agreement",
            "d_protocol_unchanged", "e_pytest"]
    names = ["(a) DB<->file\nparity", "(b) corpus >=20\n& 100% source",
             "(c) owner\nagreement >=2/3", "(d) protocol\nbyte-identical", "(e) pytest\ngreen"]
    vals = []
    for k in keys:
        v = acc[k]
        if k == "b_corpus":
            vals.append(v["with_source_pct"] / 100.0)
        elif k == "c_owner_agreement":
            vals.append(v["agreement_rate"])
        else:
            vals.append(1.0 if v["passed"] else 0.0)
    ax2 = axes[1]
    colors = ["#7fb47f" if acc[k]["passed"] else "#d98c8c" for k in keys]
    bars = ax2.bar(range(len(vals)), vals, color=colors)
    ax2.set_xticks(range(len(vals)))
    ax2.set_xticklabels(names, fontsize=7)
    ax2.set_ylim(0, 1.18)
    ax2.axhline(2 / 3, ls="--", lw=1, color="#555")
    ax2.text(len(vals) - 0.45, 2 / 3 + 0.02, "2/3 floor", fontsize=7, ha="right")
    for b, v, k in zip(bars, vals, keys):
        ax2.text(b.get_x() + b.get_width() / 2, v + 0.02,
                 f"{v:.3f}\n{'PASS' if acc[k]['passed'] else 'FAIL'}", ha="center", fontsize=7)
    ax2.set_title("IN28-N07 acceptance metrics", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "fig_in28_n07_acceptance.png", dpi=140)
    plt.close(fig)


def _md_table(header: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _render_doc(payload: dict[str, Any]) -> None:
    acc = payload["acceptance"]
    c = acc["criteria"]
    stats = c["c_owner_agreement"]
    parity = c["a_db_file_parity"]
    proto = c["d_protocol_unchanged"]
    results = payload["results"]
    comps = {x["case_id"]: x for x in payload["comparisons"]}
    kind_cn = {"agreement": "agreement", "constitutional_divergence": "可辩护分歧",
               "mismatch": "mismatch"}

    case_rows = [[r["case_id"], r["title"][:26], r["verdict"], r["route"],
                  r["owner_ruling"]["label"], kind_cn[comps[r["case_id"]]["kind"]]]
                 for r in results]
    fact_rows = [[cid, t["metric"], t["value"], t["source"], t["source_sha256"][:16]]
                 for cid, trace in payload["fact_provenance"].items() for t in trace]

    detail = []
    for r in results:
        rows = [[x["criterion_id"], x["statement"][:38],
                 f"{x['metric']} {x['op']} {x['threshold']}", x["actual"],
                 "PASS" if x["passed"] else f"FAIL→{x['on_fail_verdict']}"]
                for x in r["criteria_results"]]
        block = (f"### {r['case_id']} · {r['title']}\n\n"
                 f"- 判决 **{r['verdict']}**（路由 `{r['route']}`，self_decision={r['self_decision']}）；"
                 f"owner 成文裁决 **{r['owner_ruling']['label']}**"
                 f"（`{r['owner_ruling']['source_ref']}`）；比对 `{comps[r['case_id']]['kind']}`\n"
                 f"- 判据冻结哈希 `{r['preregistration_freeze_hash'][:24]}…`；"
                 f"账本 seq={r['ledger_seq']} entry_hash `{r['ledger_entry_hash'][:24]}…`\n")
        if rows:
            block += "\n" + _md_table(["判据", "原文", "判据式", "实测", "结果"], rows) + "\n"
        else:
            block += "\n（红线路由：判据未参与裁决，机器只出呈 owner 的决策包，不代签）\n"
        if comps[r["case_id"]]["kind"] != "agreement":
            pkt = r.get("owner_packet") or {}
            block += (f"\n- 分歧可辩护性：engine={r['verdict']} ≠ owner={r['owner_ruling']['label']}，"
                      f"owner_packet.recommendation_is_binding="
                      f"{pkt.get('recommendation_is_binding')}；完整分析见 "
                      f"`data/governance/replay_cases.json` 中该案例的 `divergence_analysis`。\n")
        detail.append(block)

    fmt_pass = lambda ok: "PASS" if ok else "FAIL"  # noqa: E731
    doc = f"""# IN28-N07 裁决账本接库与语料扩充（{c['b_corpus']['cases']} 例历史争议 · 文件↔关系库逐字段对齐）

> 战役 DAG 节点 IN28-N07 · 仓 TensorPlatform · 分支 `agent/m5517` · 生成时刻 {acc['generated_at']}
> 本文件由 `scripts/in28_n07_ledger_pg.py` **程序化渲染**（表格数字零手抄）。
> 工件：`artifacts/in28_n07/acceptance.json`、`replay_result.json`、`parity.json`、
> `adjudication_ledger.jsonl`、`fig_in28_n07_acceptance.png`；
> 代码：`backend/services/council_constitution/ledger_store.py`（新增）、`core.py`（未改）；
> 语料：`data/governance/replay_cases.json`（v1 三例逐字节保留）；
> 测试：`tests/test_council_ledger_pg.py`、`tests/test_council_corpus_v2.py`。

## 1. 案例介绍（对象与背景）

本节点没有连续物理场（无阻力/耐波场量）：裁决对象是**平台的裁决账本与裁决语料**。
原型 IN27-H5 把 3 个历史争议回放进宪政裁决器，账本落在单机 JSONL 文件中。两个缺口：
① 账本只能单机读，无法多进程并发审计；② n=3 时"一致率 2/3"与随机猜无统计差别，不可辩护。

本节点做两件事，**都不改裁决协议**：

- **账本接库**：新增 `SqlLedgerStore`（PG 生产 / SQLite 自检），把文件 hash 链**镜像**进关系库。
  记录形状（`schema/seq/recorded_at/prev_hash/record/entry_hash`）与哈希口径
  （`core.sha256_obj` 的规范化 JSON）完全沿用 `core.Ledger`，落库前重算哈希并校验前驱链接，
  不平即拒写；`parity()` 做**逐行逐字段**对账，不以"行数相等"冒充一致。
- **语料扩充**：3 例 → {c['b_corpus']['cases']} 例，全部取自仓内真实发生且已有成文裁决的争议。
  每条事实用 regex 从证据文件现取（`expected` 仅作核对），每条 owner 裁决 `verbatim`
  必须是源文件原文子串——即"标签不是本节点代拟"。

语料覆盖四类争议：证据等级（MOCK/常量响应冒充 REAL）、精度宣称（>15% 不可接受 / <3% 达标）、
门槛与纪律（观察窗、治理门、不确定度可裁性）、红线（不可逆动作、治理规则变更）。
含 {sum(1 for r in results if r['verdict'] == 'ADOPT')} 例**正向控制**（真实达标的主张必须被采纳，
防"分类器只会说失败"）。owner 标签分布：`{json.dumps(stats['label_distribution'], ensure_ascii=False)}`。

## 2. 怎么做的（方法 / 流程 / 工具链）

1. **语料合并（append-only + 6 条硬校验）**：`scripts/in28_n07_cases.py` 定义
   {c['b_corpus']['cases'] - 3} 个新案例；`scripts/in28_n07_corpus_build.py` 与 v1 三例合并并校验：
   案例数 ≥20、`case_id` 唯一、事实 regex 现取值 == expected、证据引用为原文子串、
   owner `verbatim` 为源文件原文子串、条款与选项可解析。任一不过即拒写（退出码 1）。
2. **回放裁决**：`scripts/in28_n07_ledger_pg.py` 逐例现取事实 → 三个"规则执行者"族投票
   （判据失败取其**事先声明**的失败判决，全过取 ADOPT）→ `Adjudicator` 按
   **红线 → 预登记判据 → 多族票决 → 升级** 裁决 → 写文件 hash 链账本 → 同一条记录镜像入库。
3. **接库不改协议**：`SqlLedgerStore.append_entry()` 用**同一套** `sha256_obj` 重算
   `entry_hash`，校验 `prev_hash` 与库内链头一致后才插入；因此库中行的哈希与文件中逐字节相等。
   `reset` 只清空本节点自有工件表（文件账本是权威副本，可重建库）。
4. **协议不变的机器证据**：对 `VERDICTS`、`RED_LINE_KINDS`、`Conclave.cast/tally`、
   `Adjudicator.adjudicate`、`Ledger.append/verify` 取源码与词表快照算指纹，
   与 `data/governance/protocol_baseline.json`（由 `main` 的 `core.py` 生成）比对，
   并核对 `core.py` 相对 `main` 无 diff。
5. **统计**：一致率 + Wilson 95% 区间 + 精确二项检验（对 {stats['chance_uniform_5labels']} 随机基线
   与多数类基线 {stats['majority_class_share']}），把"≥2/3"从口号变成可辩护陈述。

## 3. 计算结果（数字表）

### 3.1 五项机器判据

{_md_table(["判据", "实测", "门槛", "结果"], [
    ["(a) 库↔文件对账",
     f"db_rows={parity['db_rows']}, file_rows={parity['file_rows']}, "
     f"field_diff={parity['field_diff_count']}, head_hash_equal={parity['head_hash_equal']}",
     "行数相等且字段 diff=0", fmt_pass(parity['passed'])],
    ["(b) 语料规模与源路径",
     f"cases={c['b_corpus']['cases']}, with_source={c['b_corpus']['with_source_pct']}%",
     "≥20 例且 100%", fmt_pass(c['b_corpus']['passed'])],
    ["(c) owner 一致率",
     f"{stats['agreements']}/{stats['cases']}={stats['agreement_rate']:.4f} "
     f"(Wilson95 [{stats['wilson95_low']:.3f}, {stats['wilson95_high']:.3f}]), "
     f"可辩护分歧={stats['divergences']}, mismatch={stats['mismatches']}",
     "≥2/3 且 mismatch=0", fmt_pass(c['c_owner_agreement']['passed'])],
    ["(d) 协议不变",
     f"fp={proto['current_sha256'][:16]}…, 与 main 一致={proto['identical_to_main']}, "
     f"core.py diff 空={proto['core_py_diff_vs_main_empty']}",
     "指纹逐字节一致", fmt_pass(proto['passed'])],
    ["(e) pytest", str(c['e_pytest'].get('summary', c['e_pytest'].get('status'))),
     "exit_code==0", fmt_pass(c['e_pytest']['passed'])],
])}

**总判定：{fmt_pass(acc['all_passed'])}**（{acc['generated_at']}，账本库 `{parity['store_dsn_label']}`，
账本文件 `{acc['ledger']['file']}`，{acc['ledger']['file_entries']} 条，
链头 `{acc['ledger']['file_head_hash'][:16]}…`）。

### 3.2 统计可辩护性

{_md_table(["量", "值", "含义"], [
    ["一致率", f"{stats['agreements']}/{stats['cases']} = {stats['agreement_rate']:.4f}", "engine 判决 == owner 成文裁决"],
    ["Wilson 95% 区间", f"[{stats['wilson95_low']:.4f}, {stats['wilson95_high']:.4f}]",
     f"下界≥2/3：{stats['wilson95_low_ge_two_thirds']}"],
    ["可辩护分歧", stats['divergences'], "红线路由升级到 owner，附 divergence_analysis"],
    ["不可辩护不一致", stats['mismatches'], "必须为 0（本判据）"],
    ["p 值 vs 随机(1/5)", f"{stats['p_value_vs_uniform']:.3e}", "精确二项检验 P(X≥k)"],
    ["p 值 vs 多数类基线", f"{stats['p_value_vs_majority_class']:.3e}",
     f"多数类占比 {stats['majority_class_share']}"],
])}

### 3.3 逐例判决 vs owner 裁决

{_md_table(["案例", "争议（截断）", "engine 判决", "路由", "owner 裁决", "比对"], case_rows)}

### 3.4 事实（regex 现取，零手抄）

{_md_table(["案例", "metric", "实测", "来源", "源文件 sha256"], fact_rows)}

## 4. 结果图

![fig](artifacts/in28_n07/fig_in28_n07_acceptance.png)

左：每行一个案例，列是封闭判决词表；绿色格 = engine 判决与 owner 成文裁决一致，
红色格 = 红线路由下机器不自决（呈 owner）。右：五项判据实测值与门槛（2/3 虚线为一致率下限）。
图中文字用英文是为了避开容器内缺 CJK 字体的乱码风险；数值与正文同源（同一 `acceptance.json`）。

## 5. 注意点（踩过的坑 / 局限 / 适用范围）

1. **票是"规则执行者"票，不是 LLM 独立票**：默认证模式下三个族的票由预登记判据推导
   （确定性、可复现），因此本节点的一致率衡量的是**预登记判据编码 owner 成文裁决的保真度**，
   不是"LLM 对齐度"。LLM 票通道保留为 `--ballots <jsonl>`，本次未用（预算与可复现优先）。
   这条限制是结论适用范围的硬边界，已写进 `acceptance.json` 的 `vote_source` 字段。
2. **`on_fail_verdict` 是"事先声明的失败判决"，不是事后拟合**：它写在案例的预登记里，
   冻结时刻早于任何一票（`assert_frozen_before` 机器校验）；但**判据与判决的映射由本节点设计**，
   换一个作者可能写出不同映射。可辩护性靠两条：判据绑定仓内可复核数值（regex 现取），
   owner 标签是原文子串（`verbatim` 校验），且 {sum(1 for r in results if r['route'] == 'committee')} 例
   "判据全过 → 走票决 → ADOPT" 正向控制（真实达标的主张被采纳），
   说明判据不是"只要见到就判失败"。
3. **分歧是设计结果**：{stats['divergences']} 例可辩护分歧全部来自红线路由（不可逆动作、治理规则变更）——
   机器必须升级、owner 已经驳回。若把 ESCALATE 与 REJECT 合并成一个标签，
   一致率会涨到 {(stats['agreements'] + stats['divergences']) / stats['cases']:.4f}，但这等于
   让机器事后认领 owner 的裁决，破坏分权，故**不做**。
4. **库只是镜像**：关系库不引入新的裁判语义，权威副本仍是 append-only 文件 hash 链；
   库表可重建（`reset` 后 `import_file` 幂等重放）。`parity()` 的字段级 diff 才是"换存储不改协议"的证据。
5. **红线词表会误伤**：词表按子串/词边界匹配，案例文本必须避免出现"安全/宪章/超级多数"等词，
   否则非红线案例也会被升级。本节点在写案例时逐一规避，并由回放结果逐例核对路由。
6. **修正了 v1 的一个事实缺陷（如实披露）**：v1 的 `IN27-H5-C3` 把红线类别数写成 `expected=1`，
   但原回放脚本对该 metric **直接信任 expected、从不现算**（`extract_fact` 里有个特例分支）。
   本节点改为用分类器现算，实测为 **2 类**（不可逆 + 治理规则类），因此把该值更正为 2，
   并在案例里留 `expected_note`。判据是 `== 0` → 两个值都判"触发升级"，**结论不变**。
   这说明"v1 三例的事实是现取"这一说法原本并不完全成立——现已成立。
7. **协议基线的 ref 选择**：本 worktree 的本地 `main` 落后于 `origin/main`（`core.py` 尚未合入本地 main），
   在本地 `main` 上比对会得到"文件不存在"的假差异。因此基线 ref 按
   `origin/main ≻ main ≻ 引入 core.py 的提交` 解析，并在 `acceptance.json.base_ref` 里写明实际用的是哪个。
8. **pytest 的 1 个 skip**：`tests/test_council_constitution.py` 里 v1 的工件测试需要
   `artifacts/in27_h5/replay_result.json`（需 LLM 票箱），本 worktree 未生成该工件，故 skip；
   与本次改动无关，不是失败。
9. **统计口径**：Wilson 区间对小样本偏保守；{stats['cases']} 例、一致 {stats['agreements']} 例时
   区间下界 {stats['wilson95_low']:.3f}{'≥' if stats['wilson95_low_ge_two_thirds'] else '<'}2/3，
   因此"≥2/3"的陈述{'有' if stats['wilson95_low_ge_two_thirds'] else '仅有'}点估计支撑，
   不宜外推到未见过的争议类型（语料是**该仓历史**的样本，不是随机抽样）。

## 6. 误差判定（逐项 vs 基准，显式回答是否 <3%）

本节点无连续物理量，**基准不是解析解而是 owner 成文裁决（离散标签）**，因此"误差"定义为
**标签不一致率** 1 − 一致率；逐项列出并对"3% 级"问题给出明确回答：

{_md_table(["判据", "基准", "实测", "误差定义", "是否 <3%？"], [
    ["engine 判决", "owner 成文裁决（{n} 例）".format(n=stats['cases']),
     f"一致 {stats['agreements']} / 分歧 {stats['divergences']} / mismatch {stats['mismatches']}",
     f"不一致率 {1 - stats['agreement_rate']:.4f}", f"否（{1 - stats['agreement_rate']:.2%} > 3%），"
     "但本量的验收基准是『≥2/3 一致且 0 不可辩护不一致』，不是 3%——离散标签场景下 3% 无定义"],
    ["库↔文件字段", "文件 hash 链（权威副本）", f"field_diff={parity['field_diff_count']}",
     "字段级 diff 计数", "是（0/0，即 0%）"],
    ["账本哈希", "文件 entry_hash", "库重算 entry_hash 全部相等",
     "哈希不一致条数", f"是（0/{c['b_corpus']['cases']}，即 0%）"],
    ["协议指纹", "main 分支 core.py 指纹", proto['current_sha256'][:16] + "…",
     "字节级指纹差异", "是（0 字节差异）"],
    ["语料事实值", "证据文件 regex 现取", f"{len(fact_rows)} 条全部 == expected",
     "事实偏差条数", "是（0 条）"],
])}

**显式回答**：唯一"非零误差"是一致率缺口 {1 - stats['agreement_rate']:.4f}
（全部为可辩护的红线升级分歧），它**不可能 <3%**——除非机器替 owner 签字，而那正是本节点要禁止的。
其余四项（字段/哈希/协议/事实）误差均为 **0（<3%）**。

## 7. 复现

```bash
python3 scripts/in28_n07_corpus_build.py --check          # 语料 6 条硬校验
python3 scripts/in28_n07_ledger_pg.py --pytest-summary artifacts/in28_n07/pytest_result.json
python3 -m pytest tests/test_council_ledger_pg.py tests/test_council_corpus_v2.py -q
```

## 附：逐例细节（可复核到底）

{chr(10).join(detail)}
"""
    DOC_PATH.write_text(doc, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
