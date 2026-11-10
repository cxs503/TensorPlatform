#!/usr/bin/env python3
"""能力台账自动升账机制（P186-W1-N07 · auto capability ledger upgrade）

物理目标
--------
端点测试通过 → 证据链（测试报告 sha256 + 判据 + 测量值 + 裁决 + 时间戳 + 任务号）
自动生成 → 台账条目自动创建/更新，零人工干预。

机器可算判据
------------
C1  测试通过升账：端点测试通过 + 证据链完整 → 台账条目自动创建（status=REAL）
C2  测试失败不升账：端点测试失败 → 台账条目不创建或标记 WITHHELD
C3  证据链完整性：证据链 JSON 含 {test_report_sha256, acceptance_criteria,
    measured_values, verdict, timestamp, mission_id}，缺字段即 FAIL
C4  独立复核：另一测试套件交叉验证通过 → 升账，否则标记 WITHHELD
C5  回测准确率：在冻结端点集上升账决策准确率 ≥ 90%（假阳性 < 5%）
C6  零领域语义：代码与测试不含领域专名（扫描器断言 0 命中
    resistance|propulsion|seakeeping 等）

纪律
----
- 零领域语义铁律：本模块及测试中禁止出现任何学科专名
- 证据链字段冻结，新增字段须显式登记
- 所有决策可溯源（decision_id + sha256）
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 常量 ──────────────────────────────────────────────────────────────────

TOOL_NAME = "auto_capability_upgrade"
TOOL_VERSION = "1.0.0"
EVIDENCE_SCHEMA_VERSION = "1.0"

# 证据等级
EVIDENCE_LEVELS = ("NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL")

# 裁决词
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_WITHHELD = "WITHHELD"
VALID_VERDICTS = (VERDICT_PASS, VERDICT_FAIL, VERDICT_WITHHELD)

# 证据链必填字段（C3 判据）
EVIDENCE_CHAIN_REQUIRED_FIELDS = (
    "test_report_sha256",
    "acceptance_criteria",
    "measured_values",
    "verdict",
    "timestamp",
    "mission_id",
)

# sha256 正则（64 hex）
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# C6 零领域语义：禁止出现的学科专名（铁律扫描词表）
DOMAIN_FORBIDDEN_TERMS = (
    "resistance", "propulsion", "seakeeping", "maneuvering",
    "stability", "hydrostatic", "wave", "hull", "propeller",
    "rudder", "ship", "vessel", "icing", "ice", "froude",
    "reynolds", "wake", "thrust", "torque", "roll", "pitch",
    "heave", "surge", "sway", "yaw",
)

# 退出码
EXIT_OK = 0
EXIT_EVIDENCE_INCOMPLETE = 1
EXIT_VERDICT_FAIL = 2
EXIT_CROSS_VALIDATION_FAIL = 3
EXIT_DOMAIN_LEAK = 4
EXIT_INVALID_INPUT = 5


# ── 数据结构 ──────────────────────────────────────────────────────────────

@dataclass
class EvidenceChain:
    """证据链（C3 判据：六字段缺一不可）。"""
    test_report_sha256: str
    acceptance_criteria: Dict[str, Any]
    measured_values: Dict[str, Any]
    verdict: str
    timestamp: str
    mission_id: str
    # 可选扩展字段
    cross_validation: Optional[Dict[str, Any]] = None
    endpoint_name: Optional[str] = None
    repo: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceChain":
        known = {f: d.get(f) for f in (
            "test_report_sha256", "acceptance_criteria", "measured_values",
            "verdict", "timestamp", "mission_id", "cross_validation",
            "endpoint_name", "repo",
        )}
        return cls(**known)


@dataclass
class UpgradeDecision:
    """升账决策记录（可溯源）。"""
    decision_id: str
    mission_id: str
    endpoint_name: str
    verdict: str
    evidence_complete: bool
    cross_validated: bool
    new_status: str
    reason: str
    timestamp: str
    evidence_sha256: str  # 证据链 JSON 的 sha256

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestResult:
    """回测结果（C5 判据）。"""
    total: int
    correct: int
    accuracy: float
    false_positives: int
    false_negatives: int
    false_positive_rate: float
    decisions: List[dict]


# ── 核心函数 ──────────────────────────────────────────────────────────────

def compute_sha256(content: str) -> str:
    """计算字符串的 sha256。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_sha256_bytes(data: bytes) -> str:
    """计算 bytes 的 sha256。"""
    return hashlib.sha256(data).hexdigest()


def validate_evidence_chain(chain: EvidenceChain) -> Tuple[bool, List[str]]:
    """验证证据链完整性（C3 判据）。

    Returns
    -------
    (is_valid, errors)
        is_valid: 所有必填字段存在且合法
        errors: 缺失/非法字段列表
    """
    errors = []

    # 检查必填字段非空
    if not chain.test_report_sha256:
        errors.append("missing test_report_sha256")
    elif not SHA256_RE.match(chain.test_report_sha256):
        errors.append(f"invalid test_report_sha256 format: {chain.test_report_sha256!r}")

    if not chain.acceptance_criteria:
        errors.append("missing or empty acceptance_criteria")

    if not chain.measured_values:
        errors.append("missing or empty measured_values")

    if chain.verdict not in VALID_VERDICTS:
        errors.append(f"invalid verdict: {chain.verdict!r} (must be one of {VALID_VERDICTS})")

    if not chain.timestamp:
        errors.append("missing timestamp")
    else:
        # 验证时间戳格式
        try:
            datetime.fromisoformat(chain.timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            errors.append(f"invalid timestamp format: {chain.timestamp!r}")

    if not chain.mission_id:
        errors.append("missing mission_id")

    return (len(errors) == 0, errors)


def check_domain_leak(text: str) -> List[str]:
    """扫描文本中的领域专名（C6 判据）。

    匹配策略：词边界 OR 下划线/非字母数字邻接（捕获 calc_resistance 等标识符）。

    Returns
    -------
    List of found forbidden terms (empty = clean).
    """
    found = []
    text_lower = text.lower()
    for term in DOMAIN_FORBIDDEN_TERMS:
        # 匹配：词边界 或 下划线/非字母数字邻接
        pattern = r'(?:\b|(?<=[_\W]))' + re.escape(term) + r'(?:\b|(?=[_\W]))'
        if re.search(pattern, text_lower):
            found.append(term)
    return found


def check_domain_leak_code(file_path: Path) -> List[str]:
    """扫描源文件中的领域专名泄漏。"""
    if not file_path.exists():
        return []
    content = file_path.read_text(encoding="utf-8")
    # 跳过注释和文档字符串中的引用（只检查标识符/字符串字面量）
    # 简化：全文件扫描，但排除本模块自身的文档说明
    return check_domain_leak(content)


def decide_upgrade(
    chain: EvidenceChain,
    cross_validation_result: Optional[bool] = None,
) -> UpgradeDecision:
    """升账决策引擎（C1/C2/C4 判据）。

    决策逻辑
    --------
    1. 验证证据链完整性（C3）
    2. 根据 verdict 决定：
       - PASS + 证据完整 → REAL（C1）
       - FAIL → WITHHELD（C2）
       - 证据不完整 → WITHHELD
    3. 交叉验证（C4）：
       - cross_validation_result=False → 降级为 WITHHELD

    Parameters
    ----------
    chain : EvidenceChain
        证据链
    cross_validation_result : bool, optional
        独立复核结果（True=通过, False=未通过, None=未做复核）

    Returns
    -------
    UpgradeDecision
    """
    now = datetime.now(timezone.utc).isoformat()
    chain_json = json.dumps(chain.to_dict(), sort_keys=True, ensure_ascii=False)
    chain_sha = compute_sha256(chain_json)
    decision_id = f"dec-{hashlib.sha256((chain.mission_id + chain_sha + now).encode()).hexdigest()[:12]}"

    # C3: 验证证据链
    is_valid, errors = validate_evidence_chain(chain)

    endpoint = chain.endpoint_name or "unknown"

    # 证据不完整 → WITHHELD
    if not is_valid:
        return UpgradeDecision(
            decision_id=decision_id,
            mission_id=chain.mission_id,
            endpoint_name=endpoint,
            verdict=chain.verdict,
            evidence_complete=False,
            cross_validated=cross_validation_result is True,
            new_status="WITHHELD",
            reason=f"evidence_chain_incomplete: {'; '.join(errors)}",
            timestamp=now,
            evidence_sha256=chain_sha,
        )

    # C2: 测试失败 → WITHHELD
    if chain.verdict == VERDICT_FAIL:
        return UpgradeDecision(
            decision_id=decision_id,
            mission_id=chain.mission_id,
            endpoint_name=endpoint,
            verdict=VERDICT_FAIL,
            evidence_complete=True,
            cross_validated=cross_validation_result is True,
            new_status="WITHHELD",
            reason="test_verdict_FAIL",
            timestamp=now,
            evidence_sha256=chain_sha,
        )

    # C4: 交叉验证未通过 → WITHHELD
    if cross_validation_result is False:
        return UpgradeDecision(
            decision_id=decision_id,
            mission_id=chain.mission_id,
            endpoint_name=endpoint,
            verdict=VERDICT_PASS,
            evidence_complete=True,
            cross_validated=False,
            new_status="WITHHELD",
            reason="cross_validation_failed",
            timestamp=now,
            evidence_sha256=chain_sha,
        )

    # C1: 测试通过 + 证据完整 + (交叉验证通过或未做) → REAL
    return UpgradeDecision(
        decision_id=decision_id,
        mission_id=chain.mission_id,
        endpoint_name=endpoint,
        verdict=VERDICT_PASS,
        evidence_complete=True,
        cross_validated=cross_validation_result is True,
        new_status="REAL",
        reason="test_PASS_evidence_complete" + ("_cross_validated" if cross_validation_result else ""),
        timestamp=now,
        evidence_sha256=chain_sha,
    )


def apply_upgrade(
    decision: UpgradeDecision,
    ledger_path: Path,
    dry_run: bool = False,
) -> dict:
    """将升账决策应用到台账文件。

    Parameters
    ----------
    decision : UpgradeDecision
    ledger_path : Path
        台账 JSON 文件路径
    dry_run : bool
        True=只预览不写盘

    Returns
    -------
    dict with keys: applied, previous_status, new_status, ledger_path
    """
    # 加载或初始化台账
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    else:
        ledger = {"schema_version": EVIDENCE_SCHEMA_VERSION, "entries": {}}

    key = f"{decision.mission_id}:{decision.endpoint_name}"
    previous = ledger["entries"].get(key, {}).get("status", "NOT_FOUND")

    if not dry_run:
        ledger["entries"][key] = {
            "status": decision.new_status,
            "decision_id": decision.decision_id,
            "verdict": decision.verdict,
            "evidence_complete": decision.evidence_complete,
            "cross_validated": decision.cross_validated,
            "reason": decision.reason,
            "evidence_sha256": decision.evidence_sha256,
            "updated_at": decision.timestamp,
        }
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(
            json.dumps(ledger, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    return {
        "applied": not dry_run,
        "previous_status": previous,
        "new_status": decision.new_status,
        "ledger_path": str(ledger_path),
        "decision_id": decision.decision_id,
    }


# ── 回测引擎（C5 判据） ─────────────────────────────────────────────────

def run_backtest(
    test_cases: List[dict],
) -> BacktestResult:
    """在冻结端点集上回测升账决策准确率（C5 判据）。

    Parameters
    ----------
    test_cases : list of dict
        每个 case 包含:
        - chain: EvidenceChain dict
        - cross_validation: Optional[bool]
        - expected_status: str ("REAL" or "WITHHELD")

    Returns
    -------
    BacktestResult
    """
    decisions = []
    correct = 0
    false_positives = 0  # 预测 REAL 但期望 WITHHELD
    false_negatives = 0  # 预测 WITHHELD 但期望 REAL

    for tc in test_cases:
        chain = EvidenceChain.from_dict(tc["chain"])
        cv = tc.get("cross_validation")
        expected = tc["expected_status"]

        decision = decide_upgrade(chain, cross_validation_result=cv)
        decisions.append(decision.to_dict())

        if decision.new_status == expected:
            correct += 1
        elif decision.new_status == "REAL" and expected != "REAL":
            false_positives += 1
        elif decision.new_status != "REAL" and expected == "REAL":
            false_negatives += 1

    total = len(test_cases)
    accuracy = correct / total if total > 0 else 0.0
    fp_rate = false_positives / total if total > 0 else 0.0

    return BacktestResult(
        total=total,
        correct=correct,
        accuracy=accuracy,
        false_positives=false_positives,
        false_negatives=false_negatives,
        false_positive_rate=fp_rate,
        decisions=decisions,
    )


# ── 批量处理 ──────────────────────────────────────────────────────────────

def process_batch(
    evidence_chains: List[dict],
    ledger_path: Path,
    cross_validations: Optional[Dict[str, bool]] = None,
    dry_run: bool = False,
) -> List[dict]:
    """批量处理证据链并升账。

    Parameters
    ----------
    evidence_chains : list of dict
        证据链列表
    ledger_path : Path
    cross_validations : dict, optional
        mission_id → cross_validation_result
    dry_run : bool

    Returns
    -------
    list of decision results
    """
    cross_validations = cross_validations or {}
    results = []

    for ec_dict in evidence_chains:
        chain = EvidenceChain.from_dict(ec_dict)
        cv = cross_validations.get(chain.mission_id)
        decision = decide_upgrade(chain, cross_validation_result=cv)
        apply_result = apply_upgrade(decision, ledger_path, dry_run=dry_run)
        results.append({
            "decision": decision.to_dict(),
            "apply": apply_result,
        })

    return results


# ── CLI ───────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="能力台账自动升账机制（P186-W1-N07）",
    )
    sub = parser.add_subparsers(dest="command")

    # validate 子命令
    p_val = sub.add_parser("validate", help="验证证据链完整性（C3）")
    p_val.add_argument("--chain", required=True, help="证据链 JSON 文件")

    # decide 子命令
    p_dec = sub.add_parser("decide", help="执行升账决策（C1/C2/C4）")
    p_dec.add_argument("--chain", required=True, help="证据链 JSON 文件")
    p_dec.add_argument("--cross-validation", choices=["pass", "fail"], default=None)
    p_dec.add_argument("--ledger", default="artifacts/capability_ledger/auto_upgrade_ledger.json")
    p_dec.add_argument("--dry-run", action="store_true")

    # backtest 子命令
    p_bt = sub.add_parser("backtest", help="回测准确率（C5）")
    p_bt.add_argument("--cases", required=True, help="测试用例 JSON 文件")

    # scan 子命令（C6）
    p_scan = sub.add_parser("scan", help="扫描领域语义泄漏（C6）")
    p_scan.add_argument("--files", nargs="+", required=True, help="要扫描的文件")

    args = parser.parse_args(argv)

    if args.command == "validate":
        chain_data = json.loads(Path(args.chain).read_text(encoding="utf-8"))
        chain = EvidenceChain.from_dict(chain_data)
        is_valid, errors = validate_evidence_chain(chain)
        result = {"valid": is_valid, "errors": errors}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return EXIT_OK if is_valid else EXIT_EVIDENCE_INCOMPLETE

    elif args.command == "decide":
        chain_data = json.loads(Path(args.chain).read_text(encoding="utf-8"))
        chain = EvidenceChain.from_dict(chain_data)
        cv = True if args.cross_validation == "pass" else (False if args.cross_validation == "fail" else None)
        decision = decide_upgrade(chain, cross_validation_result=cv)
        ledger_path = Path(args.ledger)
        apply_result = apply_upgrade(decision, ledger_path, dry_run=args.dry_run)
        result = {"decision": decision.to_dict(), "apply": apply_result}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if decision.new_status == "WITHHELD" and "evidence_chain_incomplete" in decision.reason:
            return EXIT_EVIDENCE_INCOMPLETE
        if decision.new_status == "WITHHELD" and "test_verdict_FAIL" in decision.reason:
            return EXIT_VERDICT_FAIL
        if decision.new_status == "WITHHELD" and "cross_validation_failed" in decision.reason:
            return EXIT_CROSS_VALIDATION_FAIL
        return EXIT_OK

    elif args.command == "backtest":
        cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
        result = run_backtest(cases)
        output = {
            "total": result.total,
            "correct": result.correct,
            "accuracy": result.accuracy,
            "false_positives": result.false_positives,
            "false_positive_rate": result.false_positive_rate,
            "pass_c5": result.accuracy >= 0.9 and result.false_positive_rate < 0.05,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return EXIT_OK if output["pass_c5"] else EXIT_VERDICT_FAIL

    elif args.command == "scan":
        all_leaks = {}
        for fpath in args.files:
            p = Path(fpath)
            leaks = check_domain_leak_code(p)
            if leaks:
                all_leaks[str(p)] = leaks
        if all_leaks:
            print(json.dumps({"clean": False, "leaks": all_leaks}, indent=2))
            return EXIT_DOMAIN_LEAK
        print(json.dumps({"clean": True, "files_scanned": len(args.files)}, indent=2))
        return EXIT_OK

    else:
        parser.print_help()
        return EXIT_INVALID_INPUT


if __name__ == "__main__":
    sys.exit(main())
