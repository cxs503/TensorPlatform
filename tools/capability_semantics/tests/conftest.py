"""测试夹具：合成工作树（域名一律 ``synth.*``，零领域语义）。

用**注入式假扫描器**代替 40 秒的真实仓扫描，保证单测快、确定性、可断言：
测试关注的是分层/判级/治理/图的逻辑，不是扫描内核本身（后者已有独立测试）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def scan_record(root: Path, kind: str, rel: str, *, line: int = 1, quote: str = "q",
                subject_hit: bool = True, numeric_asserts: int = 0,
                numeric_pass: bool | None = None, min_error_pct: float | None = None,
                stub_markers: tuple[str, ...] = (), mock_markers: tuple[str, ...] = ()) -> dict:
    """构造一条与 ``capability_audit_scan`` 同形的扫描记录。"""
    import hashlib

    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# synth fixture {rel}\n", encoding="utf-8")
    rec = {
        "path": rel,
        "line": line,
        "quote": quote,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "kind": kind,
        "subject_hit": subject_hit,
    }
    if kind == "test":
        rec["numeric_asserts"] = numeric_asserts
    if kind == "code":
        rec["stub_markers"] = list(stub_markers)
        rec["mock_markers"] = list(mock_markers)
    if numeric_pass is not None or min_error_pct is not None:
        rec.update({"pass": bool(numeric_pass), "min_error_pct": min_error_pct,
                    "fail_quote": False, "negative_verdict": False})
    return rec


def make_scanner(records: Mapping[str, Mapping[str, list[dict]]]):
    """返回一个满足 ``Scanner`` 协议的假扫描器（忽略 root/profiles）。"""

    def _scan(root: Path, profiles: Mapping[str, Any]) -> Mapping[str, Mapping[str, list[dict]]]:
        return records

    return _scan


@dataclass
class Workspace:
    """一个最小可跑工作树。"""

    root: Path
    ledger_path: Path
    config_path: Path
    patterns_path: Path
    records: dict[str, dict[str, list[dict]]] = field(default_factory=dict)

    def scanner(self):
        return make_scanner(self.records)

    def run(self, **kwargs: Any):
        from tools.capability_semantics.pipeline import run_pipeline

        kwargs.setdefault("root", self.root)
        kwargs.setdefault("scanner", self.scanner())
        kwargs.setdefault("out_dir", self.root / "artifacts/capability_semantics")
        return run_pipeline(**kwargs)


CAPABILITIES = [
    {"id": "synth.base", "label": "Base", "domain": "d1", "graph_role": "domain",
     "depends_on": []},
    {"id": "synth.derived", "label": "Derived", "domain": "d1", "graph_role": "domain",
     "depends_on": ["synth.base"]},
    {"id": "synth.tool", "label": "Tool", "domain": "d2", "graph_role": "tooling",
     "depends_on": []},
]


def build_workspace(tmp_path: Path, *, capabilities: list[dict] | None = None,
                    ledger_levels: Mapping[str, Any] | None = None,
                    thresholds: Mapping[str, Any] | None = None) -> Workspace:
    """构造带配置/台账/端点模块/假扫描记录的工作树。"""
    root = tmp_path
    caps = capabilities if capabilities is not None else CAPABILITIES
    cfg = {
        "schema_version": "1.0",
        "thresholds": dict(thresholds or {}),
        "delivery_discovery": {"endpoint_roots": ["backend/api/domain"],
                               "unbound_module_allowlist": []},
        "capabilities": caps,
    }
    config_path = write_json(root / "config/capability_semantics.json", cfg)

    levels = dict(ledger_levels or {"synth.base": "NOT_SUPPORTED",
                                    "synth.derived": "REAL"})
    ledger = {"schema_version": "2.0", "capabilities": [
        {"id": cid, "domain": "d1", "claimed_level": lv,
         "evidence": ([{"report_id": 7, "stance": "contradicts",
                        "asserted_level": "MOCK", "as_of": "2026-01-01T00:00:00"}]
                      if lv == "REAL_AS_MOCK" else [])}
        for cid, lv in levels.items()
    ]}
    ledger_path = write_json(root / "artifacts/capability_ledger/capability_ledger.json", ledger)
    patterns_path = write_json(root / "config/capabilities.json", {
        "capabilities": [{"capability_id": c["id"], "code_patterns": [c["id"]],
                          "test_patterns": [f"test_{c['id'].split('.')[-1]}"]}
                         for c in caps]
    })
    # 端点模块：一个绑定到 synth.base（路径一致），一个孤立
    bound = "backend/api/domain/synth_bound.py"
    orphan = "backend/api/domain/synth_orphan.py"
    (root / bound).parent.mkdir(parents=True, exist_ok=True)
    (root / bound).write_text(
        "@router.get('/x')\ndef x():\n    return {}\n@router.post('/y')\ndef y():\n    return {}\n",
        encoding="utf-8")
    (root / orphan).write_text("@router.get('/z')\ndef z():\n    return {}\n", encoding="utf-8")

    records: dict[str, dict[str, list[dict]]] = {
        "synth.base": {
            "code": [scan_record(root, "code", bound, quote="def x")],
            "test": [scan_record(root, "test", "backend/tests/test_base.py",
                                 numeric_asserts=3)],
            "verified": [scan_record(root, "verified",
                                     "benchmarks/verified/synth_base.md",
                                     numeric_pass=True, min_error_pct=1.2)],
        },
        "synth.derived": {
            "code": [scan_record(root, "code", "backend/services/synth_derived.py")],
            "test": [],
            "verified": [],
        },
        "synth.tool": {"code": [], "test": [], "verified": []},
    }
    return Workspace(root=root, ledger_path=ledger_path, config_path=config_path,
                     patterns_path=patterns_path, records=records)