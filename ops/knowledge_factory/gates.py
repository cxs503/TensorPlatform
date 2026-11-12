"""双门校验器：结构门（JSON/schema/身份）+ 证据门（哈希/定位/去重）。

设计要点（可被测试证明）：
- 证据门**只认字节**：quote 必须在 tensor_reports.markdown 里逐字出现（不做换行归一化），
  provenance.source_hash 必须等于平台独立重算的 sha256 —— 生产者说什么都不算数。
- 零 claim 是合法完整结果（不得为填指标造知识）；此时定位率记 None 而非 100%。
- 所有门结果留痕（每个门的 ok/detail），落 tk_work_packages.gates，审计可复查。
"""
import hashlib
import json
import os
import re

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "schemas", "kf_result.v1.schema.json")

_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.S)


def sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_schema(path=None):
    with open(path or SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def extract_payload(markdown):
    """从报告 markdown 取唯一 ```json 代码块。返回 (obj, err)。"""
    blocks = _JSON_BLOCK.findall(markdown or "")
    if not blocks:
        return None, "no_json_block"
    if len(blocks) > 1:
        return None, f"multiple_json_blocks({len(blocks)})"
    try:
        return json.loads(blocks[0]), None
    except json.JSONDecodeError as e:
        return None, f"json_decode_error:{e.msg}@{e.pos}"


def _gate(name, ok, **detail):
    return {"gate": name, "ok": bool(ok), **detail}


def structure_gate(obj, schema=None, pkg=None, schema_validator=None):
    """结构门：schema + 身份（package_id/report_id/task_type 三者必须与工包一致）。"""
    gates = []
    if obj is None:
        return [_gate("g1_valid_json", False, reason="payload_missing")], False
    gates.append(_gate("g1_valid_json", True))
    schema = schema or load_schema()
    try:
        import jsonschema
        validator = schema_validator or jsonschema.Draft202012Validator(schema)
        errs = sorted(validator.iter_errors(obj), key=lambda e: list(e.path))
        if errs:
            e = errs[0]
            gates.append(_gate("g2_schema_pass", False,
                               path="/".join(str(p) for p in e.path),
                               reason=e.message[:200], n_errors=len(errs)))
        else:
            gates.append(_gate("g2_schema_pass", True))
    except ImportError:  # 无 jsonschema 时退化为关键字段检查（如实记录）
        need = ("package_id", "task_type", "report_id", "provenance", "outputs")
        miss = [k for k in need if k not in obj]
        gates.append(_gate("g2_schema_pass", not miss, degraded=True, missing=miss))
    if pkg:
        bad = []
        want_report = pkg.get("report_id") or (pkg.get("input") or {}).get("report_id")
        want_stage = str(pkg.get("task_type", "")).split("-")[0]
        if str(obj.get("package_id")) != pkg["package_id"]:
            bad.append("package_id")
        if str(obj.get("report_id")) != str(want_report):
            bad.append("report_id")
        if str(obj.get("task_type")).split("-")[0] != want_stage:
            bad.append("task_type")
        gates.append(_gate("g3_identity", not bad, mismatched=bad))
    ok = all(g["ok"] for g in gates)
    return gates, ok


def evidence_gate(obj, report_markdown, seen_claim_ids=None, min_quote_chars=12,
                  expected_report_id=None):
    """证据门：哈希真值 + locator 反查 + 未解析须给理由 + ID 去重。"""
    gates = []
    prov = (obj or {}).get("provenance") or {}
    actual = sha256_text(report_markdown or "")
    claimed = str(prov.get("source_hash") or "")
    gates.append(_gate("g4_source_hash_matches", claimed == actual,
                       claimed=claimed[:19], actual=actual[:19]))
    if prov.get("source_field") not in (None, "markdown"):
        gates.append(_gate("g4b_source_field", False, field=prov.get("source_field")))
    else:
        gates.append(_gate("g4b_source_field", True))

    claims = (obj or {}).get("claims") or []
    quotes, missing, short, no_evidence = [], [], [], []
    for c in claims:
        cid = str(c.get("claim_id", "?"))
        ev = c.get("evidence") or []
        if not ev:
            if c.get("status") == "unresolved" and str(c.get("unresolved_reason", "")).strip():
                continue          # 显式未解析，合法
            no_evidence.append(cid)
            continue
        for e in ev:
            q = e.get("quote") or ""
            if len(q) < int(min_quote_chars):
                short.append({"claim_id": cid, "chars": len(q)})
                continue
            if q in (report_markdown or ""):
                quotes.append(cid)
            else:
                missing.append({"claim_id": cid, "quote": q[:60]})
    total_loc = len(quotes) + len(missing) + len(short)
    loc_ok = not missing and not short
    gates.append(_gate("g5_source_locator_resolvable", loc_ok,
                       checked=total_loc, resolved=len(quotes),
                       unresolved=missing[:5], too_short=short[:5]))
    gates.append(_gate("g5b_all_claims_located_or_unresolved", not no_evidence,
                       offenders=no_evidence[:5]))

    ids = [str(c.get("claim_id")) for c in claims]
    intra = sorted({i for i in ids if ids.count(i) > 1})
    cross = sorted(set(ids) & set(seen_claim_ids or set()))
    gates.append(_gate("g6_no_duplicate_ids", not intra and not cross,
                       intra=intra[:5], cross=cross[:5], n_claims=len(ids)))

    if expected_report_id is not None:
        rid = str(expected_report_id)
        foreign = [cid for cid in ids
                   if not re.search(rf"(^|[^0-9]){re.escape(rid)}([^0-9]|$)", cid)]
        gates.append(_gate("g7_claim_namespace", not foreign, foreign=foreign[:5]))
    ok = all(g["ok"] for g in gates)
    stats = {"n_claims": len(ids), "locators_checked": total_loc,
             "locators_resolved": len(quotes),
             "locator_rate": (len(quotes) / total_loc) if total_loc else None}
    return gates, ok, stats


def review_gate(obj, pkg, producer_mission_id, reviewer_mission_id):
    """审核门：verdict 合法 + 审的就是本包 + 审核者与生产者在不同 mission（独立性硬证据）。"""
    gates = []
    rep = ((obj or {}).get("outputs") or {}).get("review_report.json") or {}
    verdict = rep.get("verdict")
    gates.append(_gate("r1_verdict_enum", verdict in ("pass", "fail", "needs-fix"),
                       verdict=verdict))
    gates.append(_gate("r2_reviews_this_package",
                       str(rep.get("reviewed_package_id", "")) == pkg["package_id"],
                       got=rep.get("reviewed_package_id")))
    gates.append(_gate("r3_reviewer_independent",
                       reviewer_mission_id is not None
                       and producer_mission_id is not None
                       and int(reviewer_mission_id) != int(producer_mission_id),
                       producer=producer_mission_id, reviewer=reviewer_mission_id))
    ok = all(g["ok"] for g in gates)
    return gates, ok, verdict


def summarize(gates, ok):
    fails = [g for g in gates if not g["ok"]]
    return {"ok": bool(ok),
            "gates": {g["gate"]: g for g in gates},
            "failed": [g["gate"] for g in fails]}