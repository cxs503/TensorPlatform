#!/usr/bin/env python3
"""重放器：逐条执行机制纯函数入口（不连库，输入即记录），与冻结判词 diff。

重放的三条硬约束（缺一条就抓不到对应退化）
------------------------------------------
1. **判词一致**：``actual`` 与 fixture 冻结的 ``expected_verdict`` 逐字节相同
   （规范 JSON 比对，浮点 9 位）。任何机制退化——阈值、异常策略、归约方式、审计字段、
   短路常量——都会在这里变成 diff。
2. **幂等**：同一输入连跑两次判词必须相同（抓 M4「二次运行判词不同」）。
3. **顺序无关**：``permutation_pair`` 标记的 A/B 两条 fixture（同一集合换序）判词必须相同
   （抓 M3「输入顺序置换敏感」）。
4. **差分**：判词还要与 :mod:`tools.mech_replay.reference` 的独立实现一致（抓规则表写错）。

本模块只做「执行 + 比对 + 计数」，判定绿/红的门在 :mod:`tools.mech_replay.gate`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.mech_replay import mechanisms, reference, spec

DEFAULT_FIXTURES = Path(__file__).resolve().parent.parent.parent \
    / "artifacts" / "mech_replay" / "fixtures"


def load_fixtures(fixtures_dir: Path | str = DEFAULT_FIXTURES,
                  mechanism: str | None = None) -> list[dict[str, Any]]:
    """读入 fixture（按 fixture_id 排序，读入即验 sha256 内部一致性）。"""
    root = Path(fixtures_dir)
    if not root.exists():
        raise FileNotFoundError(f"fixtures dir not found: {root}")
    paths = sorted(root.rglob("*.json"))
    fixtures = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if mechanism and payload.get("mechanism") != mechanism:
            continue
        fixtures.append(payload)
    fixtures.sort(key=lambda f: f["fixture_id"])
    return fixtures


def _diff(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """逐字段定位差异（给报告人读；判定仍以整判词 canon 相等为准）。"""
    diffs: list[str] = []
    canon_expected, canon_actual = spec.canon(expected), spec.canon(actual)
    if canon_expected == canon_actual:
        return diffs
    if expected.get("decision") != actual.get("decision"):
        diffs.append(f"decision:{expected.get('decision')}->{actual.get('decision')}")
    exp_codes = reference.spec_codes(expected)
    act_codes = reference.spec_codes(actual)
    if exp_codes != act_codes:
        diffs.append(f"reasons:{exp_codes}->{act_codes}")
    exp_audit = expected.get("audit") or {}
    act_audit = actual.get("audit") or {}
    for key in sorted(set(exp_audit) | set(act_audit)):
        if exp_audit.get(key) != act_audit.get(key):
            diffs.append(f"audit.{key}")
    if not diffs:
        diffs.append("checks_or_observed_fields")
    return diffs


def replay_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """单条重放：判词一致 + 幂等 + 独立实现一致。"""
    mechanism = fixture["mechanism"]
    record = fixture["record"]
    expected = fixture["expected_verdict"]
    actual = mechanisms.judge(mechanism, record)
    second = mechanisms.judge(mechanism, record)
    ref = reference.reference_verdict(mechanism, record)
    match = spec.canon(actual) == spec.canon(expected)
    return {
        "fixture_id": fixture["fixture_id"],
        "mechanism": mechanism,
        "origin": fixture.get("origin"),
        "probe_class": fixture.get("probe_class"),
        "decision": actual["decision"],
        "expected_decision": expected["decision"],
        "match": match,
        "diffs": _diff(expected, actual),
        "idempotent": spec.canon(actual) == spec.canon(second),
        "reference_agree": (actual["decision"] == ref["decision"]
                            and reference.spec_codes(actual) == ref["codes"]),
        "verdict_sha256_ok": spec.digest(expected) == fixture.get("verdict_sha256"),
    }


def replay(fixtures_dir: Path | str = DEFAULT_FIXTURES,
           mechanism: str | None = None) -> dict[str, Any]:
    fixtures = load_fixtures(fixtures_dir, mechanism)
    results = [replay_fixture(f) for f in fixtures]

    # 顺序置换对：同 pair 的 A/B 判词必须相同
    pairs: dict[str, list[str]] = {}
    for result in results:
        probe_class = str(result.get("probe_class") or "")
        if not probe_class.startswith("order-pair"):
            continue
        key = f"{result['mechanism']}:order-pair"
        fixture = next(f for f in fixtures if f["fixture_id"] == result["fixture_id"])
        signature = spec.pair_signature(fixture["expected_verdict"]) + "|" + \
            spec.pair_signature(mechanisms.judge(fixture["mechanism"], fixture["record"]))
        pairs.setdefault(key, []).append(signature)
    pair_violations = sorted(k for k, v in pairs.items() if len(set(v)) > 1)

    per_mechanism: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = per_mechanism.setdefault(result["mechanism"],
                                          {"fixtures": 0, "matched": 0, "failed": 0})
        bucket["fixtures"] += 1
        if result["match"]:
            bucket["matched"] += 1
        else:
            bucket["failed"] += 1

    total = len(results)
    matched = sum(1 for r in results if r["match"])
    return {
        "mechanism_filter": mechanism,
        "fixtures_total": total,
        "fixtures_matched": matched,
        "fixtures_failed": total - matched,
        "consistency": round(matched / total, 6) if total else 0.0,
        "idempotency_violations": sorted(r["fixture_id"] for r in results if not r["idempotent"]),
        "reference_disagreements": sorted(r["fixture_id"] for r in results if not r["reference_agree"]),
        "verdict_sha_mismatches": sorted(r["fixture_id"] for r in results
                                         if not r["verdict_sha256_ok"]),
        "permutation_pairs": sorted(pairs),
        "permutation_violations": pair_violations,
        "per_mechanism": dict(sorted(per_mechanism.items())),
        "results": results,
        "all_green": (total == matched
                      and not pair_violations
                      and all(r["idempotent"] and r["reference_agree"] and r["verdict_sha256_ok"]
                              for r in results)),
    }


def summary_line(report: Mapping[str, Any]) -> str:
    return (f"fixtures={report['fixtures_total']} matched={report['fixtures_matched']} "
            f"failed={report['fixtures_failed']} consistency={report['consistency']:.4f} "
            f"idem_violations={len(report['idempotency_violations'])} "
            f"pair_violations={len(report['permutation_violations'])} "
            f"all_green={report['all_green']}")


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="mech_replay 重放器（离线判词一致性）")
    parser.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    parser.add_argument("--mechanism", default=None)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = replay(args.fixtures, args.mechanism)
    payload = {k: v for k, v in report.items() if k != "results"}
    payload["results"] = [
        {k: r[k] for k in ("fixture_id", "decision", "match", "diffs", "idempotent",
                           "reference_agree")}
        for r in report["results"]
    ]
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    print(summary_line(report))
    return 0 if report["all_green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())