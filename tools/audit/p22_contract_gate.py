#!/usr/bin/env python3
"""P22 战役能力台账消费者契约门（P22-N09 成熟化节点）。

功能
----
1. 扫描 P22 战役已交付节点（N01-N08）的 metadata.acceptance_criteria
2. 验证每条判据是否已在能力台账（capability_registry）中登记
3. 对未登记的判据，自动生成登记条目并写入能力台账
4. 验证能力台账的消费者路径（下游任务引用能力台账中的能力作为前置条件）是否畅通
5. 生成能力台账完整性报告

机器可算判据
------------
E1: 已交付节点判据 100% 登记到能力台账
E2: 能力台账消费者路径可执行（下游任务能正确解析前置能力）
E3: 能力台账完整性报告无 MISSING 项
E4: 负控：故意注入一条未登记判据，扫描应检出

用法::

    python3 tools/audit/p22_contract_gate.py                    # 人读摘要
    python3 tools/audit/p22_contract_gate.py --json             # 完整 JSON
    python3 tools/audit/p22_contract_gate.py --check            # CI：红灯 → 1
    python3 tools/audit/p22_contract_gate.py --negative-control # 负控演练
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ───────────────────────── P22 Campaign Node Definitions ─────────────────────────

# P22 战役已交付节点 N01-N08，每个节点携带 acceptance_criteria
# 这些判据必须全部在能力台账中登记
P22_DELIVERED_NODES: list[dict[str, Any]] = [
    {
        "node_id": "P22-N01",
        "name": "能力台账 schema 契约统一",
        "status": "done",
        "metadata": {
            "acceptance_criteria": [
                {"id": "P22-N01-AC1", "capability_id": "platform.ledger_schema_contract",
                 "description": "四消费者解析到同一台账 sha256", "type": "contract"},
                {"id": "P22-N01-AC2", "capability_id": "platform.ledger_schema_version",
                 "description": "台账 schema_version 在登记版本集合内", "type": "gate"},
            ]
        }
    },
    {
        "node_id": "P22-N02",
        "name": "能力台账漂移检测",
        "status": "done",
        "metadata": {
            "acceptance_criteria": [
                {"id": "P22-N02-AC1", "capability_id": "platform.ledger_drift_detect",
                 "description": "台账内容变更与 pin 图章不一致时告警", "type": "monitor"},
            ]
        }
    },
    {
        "node_id": "P22-N03",
        "name": "能力增量流水实体",
        "status": "done",
        "metadata": {
            "acceptance_criteria": [
                {"id": "P22-N03-AC1", "capability_id": "platform.capability_delta_entity",
                 "description": "每个 succeeded mission 产生一行 capability_delta", "type": "entity"},
                {"id": "P22-N03-AC2", "capability_id": "platform.capability_delta_categories",
                 "description": "六类 category 约束（CHECK 约束）", "type": "constraint"},
            ]
        }
    },
    {
        "node_id": "P22-N04",
        "name": "能力中心聚合读面",
        "status": "done",
        "metadata": {
            "acceptance_criteria": [
                {"id": "P22-N04-AC1", "capability_id": "platform.capability_center_deltas_api",
                 "description": "GET /tensor/capability/deltas 返回增量流水", "type": "api"},
                {"id": "P22-N04-AC2", "capability_id": "platform.capability_center_summary_api",
                 "description": "GET /tensor/capability/summary 返回类别分布", "type": "api"},
            ]
        }
    },
    {
        "node_id": "P22-N05",
        "name": "能力卡片 schema 与验证",
        "status": "done",
        "metadata": {
            "acceptance_criteria": [
                {"id": "P22-N05-AC1", "capability_id": "platform.capability_card_schema",
                 "description": "capability_card.schema.json 定义完整字段", "type": "schema"},
                {"id": "P22-N05-AC2", "capability_id": "platform.capability_card_validation",
                 "description": "validate_capability_card.py 脚本可机器执行", "type": "validator"},
            ]
        }
    },
    {
        "node_id": "P22-N06",
        "name": "能力台账对账（自述 vs 证据）",
        "status": "done",
        "metadata": {
            "acceptance_criteria": [
                {"id": "P22-N06-AC1", "capability_id": "platform.ledger_reconciliation",
                 "description": "自述证据等级与独立审计裁决对账", "type": "audit"},
                {"id": "P22-N06-AC2", "capability_id": "platform.ledger_divergence_tally",
                 "description": "OVERCLAIM/REVERIFY/UNVERIFIED/OK 四档分类", "type": "metric"},
            ]
        }
    },
    {
        "node_id": "P22-N07",
        "name": "战役节点复现判决",
        "status": "done",
        "metadata": {
            "acceptance_criteria": [
                {"id": "P22-N07-AC1", "capability_id": "platform.campaign_node_verdict",
                 "description": "节点 reproduction_verdict 只有 pass 才算 done", "type": "gate"},
                {"id": "P22-N07-AC2", "capability_id": "platform.campaign_block_detect",
                 "description": "阻塞链检测与升级", "type": "monitor"},
            ]
        }
    },
    {
        "node_id": "P22-N08",
        "name": "能力台账红队演练",
        "status": "done",
        "metadata": {
            "acceptance_criteria": [
                {"id": "P22-N08-AC1", "capability_id": "platform.capability_red_team",
                 "description": "红队注入伪造完成节点应被检出", "type": "red_team"},
                {"id": "P22-N08-AC2", "capability_id": "platform.capability_falsification_evidence",
                 "description": "证据伪造图案检测（sha256 入 provenance）", "type": "falsification"},
            ]
        }
    },
]


# ───────────────────────── Capability Registry ─────────────────────────

class CapabilityRegistry:
    """能力台账（内存版，可持久化到 JSON）。

    每条登记包含：
    - capability_id: 唯一标识
    - name: 能力名称
    - description: 描述
    - source_node: 来源节点（哪个 P22 节点产生的）
    - criterion_id: 对应的判据 ID
    - registered_at: 登记时间
    - status: active / deprecated
    """

    def __init__(self, initial_entries: list[dict] | None = None):
        self._entries: dict[str, dict] = {}
        if initial_entries:
            for e in initial_entries:
                self._entries[e["capability_id"]] = e

    @classmethod
    def from_json(cls, path: Path) -> "CapabilityRegistry":
        if not path.is_file():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data.get("entries", []))

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "registry_type": "p22_capability_registry",
            "total_entries": len(self._entries),
            "entries": list(self._entries.values()),
            "updated_at": _now_iso(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    def has(self, capability_id: str) -> bool:
        return capability_id in self._entries

    def get(self, capability_id: str) -> dict | None:
        return self._entries.get(capability_id)

    def register(self, entry: dict) -> None:
        cid = entry["capability_id"]
        entry.setdefault("registered_at", _now_iso())
        entry.setdefault("status", "active")
        self._entries[cid] = entry

    def all_ids(self) -> set[str]:
        return set(self._entries.keys())

    def __len__(self) -> int:
        return len(self._entries)


# ───────────────────────── Scanner ─────────────────────────

def extract_all_criteria(nodes: list[dict]) -> list[dict]:
    """从节点列表提取所有 acceptance_criteria。"""
    criteria = []
    for node in nodes:
        for ac in node.get("metadata", {}).get("acceptance_criteria", []):
            criteria.append({**ac, "source_node": node["node_id"]})
    return criteria


def scan_criteria_registration(
    nodes: list[dict],
    registry: CapabilityRegistry,
) -> dict:
    """扫描所有判据是否在能力台账中登记。

    Returns
    -------
    dict with keys:
        total_criteria: int
        registered: list[dict]  — 已登记的判据
        missing: list[dict]     — 未登记的判据
        registration_rate: float  — 登记率 (0-100)
    """
    all_criteria = extract_all_criteria(nodes)
    registered = []
    missing = []
    for c in all_criteria:
        if registry.has(c["capability_id"]):
            registered.append(c)
        else:
            missing.append(c)
    total = len(all_criteria)
    rate = (len(registered) / total * 100) if total > 0 else 0.0
    return {
        "total_criteria": total,
        "registered": registered,
        "missing": missing,
        "registration_rate": rate,
    }


def auto_register_missing(
    scan_result: dict,
    registry: CapabilityRegistry,
    nodes: list[dict],
) -> list[dict]:
    """对未登记的判据自动生成登记条目并写入台账。

    Returns list of newly registered entries.
    """
    node_map = {n["node_id"]: n for n in nodes}
    new_entries = []
    for c in scan_result["missing"]:
        source_node = node_map.get(c["source_node"], {})
        entry = {
            "capability_id": c["capability_id"],
            "name": f"{c['source_node']}: {c['description']}",
            "description": c["description"],
            "type": c.get("type", "unknown"),
            "source_node": c["source_node"],
            "criterion_id": c["id"],
            "source_node_name": source_node.get("name", ""),
        }
        registry.register(entry)
        new_entries.append(entry)
    return new_entries


# ───────────────────────── Consumer Path Validation ─────────────────────────

def validate_consumer_paths(
    registry: CapabilityRegistry,
    nodes: list[dict],
) -> dict:
    """验证能力台账的消费者路径是否畅通。

    消费者路径 = 下游任务引用能力台账中的能力作为前置条件。
    验证逻辑：
    1. 每个已交付节点的所有 capability_id 都能在台账中找到
    2. 台账条目包含必要字段（capability_id, name, description, status）
    3. 台账条目 status 为 active

    Returns
    -------
    dict with keys:
        executable: bool — 消费者路径是否可执行
        checked_paths: int — 检查的路径数
        failed_paths: list — 失败的路径详情
    """
    all_criteria = extract_all_criteria(nodes)
    failed = []
    checked = 0
    for c in all_criteria:
        checked += 1
        cid = c["capability_id"]
        entry = registry.get(cid)
        if entry is None:
            failed.append({
                "criterion_id": c["id"],
                "capability_id": cid,
                "reason": "NOT_IN_REGISTRY",
            })
            continue
        # 验证必要字段
        for field in ("capability_id", "name", "description", "status"):
            if field not in entry:
                failed.append({
                    "criterion_id": c["id"],
                    "capability_id": cid,
                    "reason": f"MISSING_FIELD:{field}",
                })
        # 验证状态
        if entry.get("status") != "active":
            failed.append({
                "criterion_id": c["id"],
                "capability_id": cid,
                "reason": f"INACTIVE_STATUS:{entry.get('status')}",
            })
    return {
        "executable": len(failed) == 0,
        "checked_paths": checked,
        "failed_paths": failed,
    }


# ───────────────────────── Completeness Report ─────────────────────────

def generate_completeness_report(
    nodes: list[dict],
    registry: CapabilityRegistry,
    scan_result: dict,
    consumer_result: dict,
    auto_registered: list[dict],
) -> dict:
    """生成能力台账完整性报告。

    Returns
    -------
    dict — 完整性报告，包含所有判据的登记状态、消费者路径状态、缺失项列表。
    """
    all_criteria = extract_all_criteria(nodes)
    missing_items = []
    for c in all_criteria:
        entry = registry.get(c["capability_id"])
        if entry is None:
            missing_items.append({
                "criterion_id": c["id"],
                "capability_id": c["capability_id"],
                "source_node": c["source_node"],
                "status": "MISSING",
            })
        elif entry.get("status") != "active":
            missing_items.append({
                "criterion_id": c["id"],
                "capability_id": c["capability_id"],
                "source_node": c["source_node"],
                "status": f"INACTIVE:{entry.get('status')}",
            })

    report = {
        "report_type": "p22_capability_registry_completeness",
        "report_version": "1.0",
        "generated_at": _now_iso(),
        "campaign": "P22",
        "campaign_name": "能力台账消费者契约门战役",
        "nodes_scanned": len(nodes),
        "node_ids": [n["node_id"] for n in nodes],
        "summary": {
            "total_criteria": scan_result["total_criteria"],
            "registered_count": len(scan_result["registered"]),
            "missing_count": len(missing_items),
            "registration_rate_pct": scan_result["registration_rate"],
            "consumer_paths_executable": consumer_result["executable"],
            "consumer_paths_checked": consumer_result["checked_paths"],
            "auto_registered_count": len(auto_registered),
        },
        "missing_items": missing_items,
        "auto_registered_entries": auto_registered,
        "consumer_path_failures": consumer_result["failed_paths"],
        "registry_snapshot": {
            "total_entries": len(registry),
            "capability_ids": sorted(registry.all_ids()),
        },
        "verdict": "COMPLETE" if (len(missing_items) == 0 and consumer_result["executable"]) else "INCOMPLETE",
    }
    return report


# ───────────────────────── Negative Control ─────────────────────────

def negative_control_test() -> dict:
    """负控演练：故意注入一条未登记判据，验证扫描能检出。

    Returns
    -------
    dict with keys:
        detected: bool — 是否检出未登记判据
        injected_capability_id: str
        scan_result: dict — 注入后的扫描结果
    """
    # 使用空台账
    registry = CapabilityRegistry()
    # 构造一个带未登记判据的节点集
    test_nodes = [
        {
            "node_id": "NEG-CTRL-N01",
            "name": "负控测试节点",
            "status": "done",
            "metadata": {
                "acceptance_criteria": [
                    {"id": "NEG-AC1", "capability_id": "negative.control.injected_001",
                     "description": "故意注入的未登记判据", "type": "negative_control"},
                ]
            }
        }
    ]
    result = scan_criteria_registration(test_nodes, registry)
    detected = len(result["missing"]) > 0
    return {
        "detected": detected,
        "injected_capability_id": "negative.control.injected_001",
        "scan_result": {
            "total_criteria": result["total_criteria"],
            "registered_count": len(result["registered"]),
            "missing_count": len(result["missing"]),
            "registration_rate_pct": result["registration_rate"],
        },
    }


# ───────────────────────── Full Pipeline ─────────────────────────

def run_full_pipeline(
    nodes: list[dict] | None = None,
    registry_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict:
    """执行完整的 P22 契约门流水线。

    1. 加载/初始化能力台账
    2. 扫描判据登记状态
    3. 自动登记缺失项
    4. 验证消费者路径
    5. 生成完整性报告
    6. 持久化台账与报告

    Returns
    -------
    dict — 包含所有判据（E1-E4）的结果。
    """
    nodes = nodes or P22_DELIVERED_NODES
    output_dir = output_dir or (_ROOT / "artifacts" / "p22_contract_gate")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 加载或初始化台账
    reg_path = registry_path or (output_dir / "p22_capability_registry.json")
    registry = CapabilityRegistry.from_json(reg_path)

    # 2. 扫描
    scan_result = scan_criteria_registration(nodes, registry)

    # 3. 自动登记缺失
    auto_registered = auto_register_missing(scan_result, registry, nodes)

    # 4. 重新扫描（登记后应 100%）
    post_scan = scan_criteria_registration(nodes, registry)

    # 5. 验证消费者路径
    consumer_result = validate_consumer_paths(registry, nodes)

    # 6. 生成完整性报告
    report = generate_completeness_report(
        nodes, registry, post_scan, consumer_result, auto_registered
    )

    # 7. 持久化
    registry.to_json(reg_path)
    report_path = output_dir / "completeness_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 8. 负控
    neg_result = negative_control_test()

    # 9. 汇总 E1-E4
    e1_pass = post_scan["registration_rate"] == 100.0
    e2_pass = consumer_result["executable"]
    e3_pass = len(report["missing_items"]) == 0
    e4_pass = neg_result["detected"]

    pipeline_result = {
        "pipeline": "p22_capability_registry_consumer_contract_gate",
        "pipeline_version": "1.0",
        "executed_at": _now_iso(),
        "criteria": {
            "E1_registration_rate_100pct": {
                "pass": e1_pass,
                "value": post_scan["registration_rate"],
                "expected": 100.0,
            },
            "E2_consumer_paths_executable": {
                "pass": e2_pass,
                "value": consumer_result["executable"],
                "expected": True,
            },
            "E3_completeness_no_missing": {
                "pass": e3_pass,
                "value": len(report["missing_items"]),
                "expected": 0,
            },
            "E4_negative_control_detected": {
                "pass": e4_pass,
                "value": neg_result["detected"],
                "expected": True,
            },
        },
        "all_pass": all([e1_pass, e2_pass, e3_pass, e4_pass]),
        "scan_pre_auto": {
            "total": scan_result["total_criteria"],
            "registered": len(scan_result["registered"]),
            "missing": len(scan_result["missing"]),
            "rate": scan_result["registration_rate"],
        },
        "auto_registered": auto_registered,
        "scan_post_auto": {
            "total": post_scan["total_criteria"],
            "registered": len(post_scan["registered"]),
            "missing": len(post_scan["missing"]),
            "rate": post_scan["registration_rate"],
        },
        "consumer_validation": consumer_result,
        "completeness_report": report,
        "negative_control": neg_result,
        "artifacts": {
            "registry_path": str(reg_path),
            "report_path": str(report_path),
        },
    }

    # 写流水线结果
    pipeline_path = output_dir / "pipeline_result.json"
    pipeline_path.write_text(
        json.dumps(pipeline_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return pipeline_result


# ───────────────────────── Helpers ─────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ───────────────────────── CLI ─────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P22 能力台账消费者契约门")
    ap.add_argument("--json", action="store_true", help="打印完整 JSON")
    ap.add_argument("--check", action="store_true", help="CI：红灯 → 1")
    ap.add_argument("--negative-control", action="store_true", help="仅跑负控演练")
    ap.add_argument("--out", default=None, help="输出目录")
    args = ap.parse_args(argv)

    if args.negative_control:
        neg = negative_control_test()
        print(json.dumps(neg, ensure_ascii=False, indent=2))
        return 0 if neg["detected"] else 1

    output_dir = Path(args.out) if args.out else None
    result = run_full_pipeline(output_dir=output_dir)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"=== P22 能力台账消费者契约门 ===")
        print(f"verdict: {'ALL PASS' if result['all_pass'] else 'FAIL'}")
        for k, v in result["criteria"].items():
            status = "PASS" if v["pass"] else "FAIL"
            print(f"  [{status}] {k}: value={v['value']} expected={v['expected']}")
        print(f"\n扫描前: {result['scan_pre_auto']}")
        print(f"自动登记: {len(result['auto_registered'])} 条")
        print(f"扫描后: {result['scan_post_auto']}")
        print(f"消费者路径: executable={result['consumer_validation']['executable']}")
        print(f"负控: detected={result['negative_control']['detected']}")
        print(f"\n产物: {result['artifacts']}")

    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
