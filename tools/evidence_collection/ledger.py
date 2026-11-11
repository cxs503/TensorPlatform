"""台账回写：把自动采集结论写进能力台账，且**不覆盖人审证据**。

回写约定
========
* 只在台账 JSON 里写一个独立顶层键 ``evidence_collection``；
  原有 ``capabilities[*].evidence`` / ``reconciled_level`` 等人工与审计字段一律不动
  （人审结论与机器采集结论并列存放，冲突在报告里显式列出，不静默合并）。
* 块内自带 ``spec_sha256`` / ``ruleset`` / 覆盖率 / 逐能力记录（等级、理由码、工件摘要），
  并带 ``previous_block_sha256`` 形成链，便于追"上一次是什么时候变的"。
* 回写后重新计算台账文件 sha256，写进 summary，供回归门比对。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLOCK_KEY = "evidence_collection"


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _block_sha(block: dict) -> str:
    body = {k: v for k, v in block.items() if k != "block_sha256"}
    return _sha_text(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def build_block(summary: dict, records: dict, previous_block: dict | None) -> dict:
    block = {
        "schema": "evidence_collection.ledger_block.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ruleset": summary["ruleset"],
        "spec_sha256": summary["spec_sha256"],
        "runtime": summary["runtime"],
        "coverage": summary["coverage"],
        "level_distribution": summary["level_distribution"],
        "levels": {cap: rec["level"] for cap, rec in records.items()},
        "records": records,
        "previous_block_sha256": (previous_block or {}).get("block_sha256"),
    }
    block["block_sha256"] = _block_sha(block)
    return block


def update_ledger(ledger_path: str | Path, block: dict) -> dict:
    """把块写进台账（保留全部既有字段），返回回写前后的 sha256。"""
    path = Path(ledger_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    ledger: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    ledger[BLOCK_KEY] = block
    # indent=1 与台账既有排版一致：回写只应产生"新增一个键"的最小 diff
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"ledger_path": str(path), "sha256_before": before, "sha256_after": after}


def write_view(out_dir: str | Path, block: dict) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    view = {
        "schema": "evidence_collection.ledger_view.v1",
        "generated_at": block["generated_at"],
        "coverage": block["coverage"],
        "level_distribution": block["level_distribution"],
        "levels": block["levels"],
        "records": {cap: {k: rec[k] for k in ("level", "reason_codes", "acceptance_sha256",
                                             "acceptance_path", "criteria_table_path",
                                             "criteria_rows", "executed_evidence_items")}
                    for cap, rec in block["records"].items()},
    }
    path = out / "ledger_view.json"
    path.write_text(json.dumps(view, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path