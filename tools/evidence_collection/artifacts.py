"""验收工件生成：每个能力一份 ``acceptance.json`` + 一份 ``criteria_table.md``。

工件是证据链的载体，必须自带可复核性：

* ``inputs``  段记录 spec / 台账 / 在线 OpenAPI / 参考工件 / 测试模块的 sha256；
* ``signals`` 段保留探针请求与响应原文（体积小，便于人复核）、判据行、测试用例状态；
* ``acceptance_sha256`` 覆盖除自身外的全部字段，任何后续手改都会被 ``verify`` 抓住。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_sha256(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "acceptance_sha256"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def seal(payload: dict) -> dict:
    payload = dict(payload)
    payload["acceptance_sha256"] = payload_sha256(payload)
    return payload


def slug(cap_id: str) -> str:
    return cap_id.replace("/", "_").replace(" ", "_")


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, list):
        if len(value) > 6:
            return "[" + ", ".join(_fmt(v) for v in value[:3]) + f", … ({len(value)} items)]"
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    if isinstance(value, dict):
        return "`" + _canonical(value)[:80] + "`"
    if value is None:
        return "—"
    return str(value)


def criteria_table(signals: dict) -> str:
    """判据表（markdown）：每行一个机器判据，带来源、测量值、阈值、判定。"""
    rows: list[str] = []
    surface = signals.get("surface") or {}
    if surface:
        rows.append("| surface | 面清单 | contract | "
                    f"declared={surface.get('declared')} present={len(surface.get('present') or [])} "
                    f"absent={len(surface.get('absent') or [])} | all present | {surface.get('status')} |")
    ref = signals.get("reference") or None
    if ref:
        rows.append(f"| reference | 参考误差复算 | reference | worst_pct={_fmt(ref.get('worst_pct'))} "
                    f"n={ref.get('n_values')} | ≤{_fmt(ref.get('tolerance_pct'))}% | {ref.get('status')} |")
    for probe in signals.get("probes") or []:
        for row in probe.get("criteria") or []:
            rows.append(
                f"| {probe['id']} | {row['id']} ({row['kind']}) | {row['class']} | "
                f"{_fmt(row.get('measured'))} | {_fmt(row.get('threshold'))} | {row['verdict']} |"
            )
    tests = signals.get("tests")
    if tests:
        totals = tests.get("totals") or {}
        rows.append(f"| tests | 绑定测试执行 | reference | passed={totals.get('passed', 0)}/"
                    f"{totals.get('tests', 0)} failed={totals.get('failures', 0)} "
                    f"errors={totals.get('errors', 0)} | all passed | {tests.get('status')} |")
    if not rows:
        return "| — | 未采集到任何机器判据 | — | — | — | — |\n"
    header = "| 来源 | 判据 | 类别 | 测量值 | 阈值 | 判定 |\n|---|---|---|---|---|---|\n"
    return header + "\n".join(rows) + "\n"


def write_acceptance(out_dir: str | Path, cap_id: str, signals: dict, inputs: dict) -> dict:
    out = Path(out_dir) / slug(cap_id)
    out.mkdir(parents=True, exist_ok=True)
    verdict = signals["verdict"]
    payload = {
        "schema": "evidence_collection.acceptance.v1",
        "capability": cap_id,
        "level": verdict["level"],
        "reason_codes": verdict["reason_codes"],
        "ruleset": verdict["ruleset"],
        "executed_evidence_items": verdict["executed_evidence_items"],
        "counts": verdict["counts"],
        "inputs": inputs,
        "signals": {
            "surface": signals.get("surface"),
            "probes": [
                {
                    "id": p["id"], "method": p["method"], "path": p["path"], "sweep": p.get("sweep"),
                    "executed": p.get("executed"), "digest": p.get("digest"),
                    "criteria": p["criteria"],
                    "samples": [
                        {"status": s.get("status"), "error": s.get("error"),
                         "response_sha256": s.get("response_sha256"),
                         "elapsed_ms": s.get("elapsed_ms"), "response": s.get("response")}
                        for s in p["samples"]
                    ],
                }
                for p in signals.get("probes") or []
            ],
            "reference": signals.get("reference"),
            "tests": signals.get("tests"),
        },
        "criteria_table_md": None,
    }
    table = criteria_table(signals)
    payload["criteria_table_md"] = f"criteria_table.md ({len(table)} chars)"
    sealed = seal(payload)
    (out / "acceptance.json").write_text(json.dumps(sealed, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    (out / "criteria_table.md").write_text(
        f"# {cap_id} — 证据判据表\n\n"
        f"- 等级：**{verdict['level']}**（ruleset `{verdict['ruleset']}`）\n"
        f"- 理由码：{', '.join(verdict['reason_codes'])}\n"
        f"- 已执行证据项：{verdict['executed_evidence_items']}\n"
        f"- spec sha256：`{inputs.get('spec_sha256')}`\n"
        f"- 台账 sha256：`{inputs.get('ledger_sha256')}`\n"
        f"- 在线 OpenAPI sha256：`{inputs.get('openapi_sha256')}`\n\n" + table,
        encoding="utf-8")
    return {"acceptance_path": str((out / "acceptance.json").as_posix()),
            "criteria_table_path": str((out / "criteria_table.md").as_posix()),
            "acceptance_sha256": sealed["acceptance_sha256"],
            "criteria_rows": sum(1 for line in table.splitlines() if line.startswith("| ") and
                                 not line.startswith("| 来源") and not line.startswith("| ---")),
            "table_sha256": hashlib.sha256(table.encode("utf-8")).hexdigest()}


def verify_acceptance(path: str | Path) -> dict:
    """复核工件：自摘要一致 + 判据表可重建。"""
    p = Path(path)
    payload = json.loads(p.read_text(encoding="utf-8"))
    expected = payload.get("acceptance_sha256")
    recomputed = payload_sha256(payload)
    return {"path": str(p), "sha256_match": expected == recomputed,
            "expected": expected, "recomputed": recomputed}