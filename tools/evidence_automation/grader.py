"""证据→等级判定器（grade determiner）。

判定顺序（每一步都写清"凭什么这么判"，reasoning 逐条落产物）
--------------------------------------------------------------
1. 无映射、无测试、无探针、无实现 → **NOT_SUPPORTED**
2. 实现的代码里出现"空壳标记"（``NotImplementedError`` / ``TODO`` 占位）且占比
   达到 ``stub_dominance`` → **STUB**
3. 实现的代码里出现"造假标记"（``MOCK`` / ``synthetic`` / 均匀随机数直接当结果）
   且占比达到 ``mock_dominance`` → **MOCK**
4. 有真实执行的测试：
   * 通过率 ≥ ``min_pass_rate`` 且（``real_requires_zero_failures`` 时）零失败零错误
     → **REAL**（测试数 ≥ ``min_tests``）
   * 通过率 < ``min_pass_rate`` → **MOCK**（实现过不了自己声明的验收）
   * 介于两者之间 → **WITHHELD**
5. 只有运行时探针：
   * 探针不可用（环境未起）→ **WITHHELD**（环境缺失不算能力缺失）
   * 探针可用且判据全过 + 口径为 ``analytic_identity`` → **REAL**
   * 探针可用且判据全过 + 口径仅 ``behavioral`` → **WITHHELD**（有行为证据、无对标）
   * 探针判据不过 → **MOCK**（实测越限）
6. 其余（有实现、无执行证据）→ **WITHHELD**

最后施加**人工复核上限**：若人工台账对同一能力有"最新裁决"，自动判级不得越过它
（自动判级越权 = OVERCLAIM，本平台明令禁止）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .mapping import LEVEL_RANK, LEVELS

# 造假标记：只收**强**信号。像 ``"MOCK"`` 这种等级字面量在判级工具里到处都是，
# 用它当标记会让"讨论 MOCK 的工具"被误判成 MOCK 实现（实测假阳性来源）。
MOCK_MARKERS = (
    r"(?i)\bsynthetic\s+(?:response|data|result)\b",
    r"(?i)\bhardcoded\s+(?:value|response|result|array)\b",
    r"(?i)\bMOCK[_-]?DATA\b",
    r"(?i)np\.random\.(?:random|rand|randn|uniform)\s*\(",
    r"(?i)(?<!\.)random\.(?:random|uniform)\s*\(",
    r"(?i)\bfake\s+data\b",
)
# 空壳标记：``pass`` 单条不算（except 分支里满地都是），只收"明确宣布未实现"。
STUB_MARKERS = (
    r"raise\s+NotImplementedError",
    r"(?i)\bTODO:?\s*implement\b",
    r"(?i)\bnot implemented yet\b",
)
_DEFAULT_POLICY = {
    "min_tests": 1,
    "min_pass_rate": 0.8,
    "real_requires_zero_failures": True,
    "stub_dominance": 0.5,
    "mock_dominance": 0.5,
    # 收集期错误（error）占比超过该阈值时，测试结果不可用于判负：
    # 缺工件/缺环境造成的 error 不是"实现造假"，只能判证据不足。
    "error_dominance": 0.2,
}


@dataclass
class Grade:
    capability_id: str
    level: str
    reasoning: List[str] = field(default_factory=list)
    basis: str = "none"
    ceiling_applied: Optional[str] = None
    markers: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "level": self.level,
            "basis": self.basis,
            "reasoning": self.reasoning,
            "ceiling_applied": self.ceiling_applied,
            "markers": self.markers,
        }


# ------------------------------------------------------------ 静态标记扫描


def scan_markers(root: Path, globs: Iterable[str], *,
                 max_files: int = 200) -> Dict[str, Any]:
    """在映射声明的代码 glob 里统计空壳/造假标记。

    返回 ``{files_scanned, stub_files, mock_files, examples}``。只扫文件、
    不做语义推断：占比阈值由判级器掌握，扫描器只报事实。
    """
    files: List[Path] = []
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path.suffix in (".py", ".conf", ".yml", ".yaml", ".sh"):
                files.append(path)
            elif path.is_dir():
                files.extend(sorted(p for p in path.rglob("*.py")))
    files = files[:max_files]
    stub_hits: Dict[str, int] = {}
    mock_hits: Dict[str, int] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - 不可读文件
            continue
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        stub = sum(len(re.findall(p, text)) for p in STUB_MARKERS)
        mock = sum(len(re.findall(p, text)) for p in MOCK_MARKERS)
        if stub:
            stub_hits[rel] = stub
        if mock:
            mock_hits[rel] = mock
    return {
        "files_scanned": len(files),
        "stub_files": stub_hits,
        "mock_files": mock_hits,
    }


# ------------------------------------------------------------- 人工复核上限


def load_manual_ceiling(path: Path) -> Dict[str, str]:
    """从人工复核台账取"最新一条裁决"作为自动判级上限。

    台账结构见 ``artifacts/capability_ledger/capability_ledger_resolved.json``：
    每条能力带 ``evidence_refs``（按时间排），取**最后一条**的 ``asserted_level``。
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:  # pragma: no cover - 台账损坏
        return {}
    out: Dict[str, str] = {}
    for entry in data.get("capabilities", []):
        cap_id = entry.get("id") or entry.get("capability_id")
        refs = entry.get("evidence_refs") or []
        if not cap_id or not refs:
            continue
        level = str(refs[-1].get("asserted_level", "")).upper()
        if level in LEVEL_RANK:
            out[str(cap_id)] = level
    return out


# ----------------------------------------------------------------- 判级主体


def grade_capability(
    capability_id: str,
    *,
    tests: Dict[str, Any],
    probes: List[Dict[str, Any]],
    markers: Dict[str, Any],
    implementation_present: bool,
    mapped: bool,
    ceiling: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
) -> Grade:
    """对一条能力做等级判定（纯函数，便于单测与复算）。"""
    pol = dict(_DEFAULT_POLICY)
    if policy:
        pol.update(policy)
    reasoning: List[str] = []
    decisive = int(tests.get("decisive", 0) or 0)
    pass_rate = float(tests.get("pass_rate", 0.0) or 0.0)
    failed = int(tests.get("failed", 0) or 0)
    errored = int(tests.get("errored", 0) or 0)

    stub_files = markers.get("stub_files", {}) or {}
    mock_files = markers.get("mock_files", {}) or {}
    scanned = max(1, int(markers.get("files_scanned", 0) or 0))
    stub_ratio = len(stub_files) / scanned
    mock_ratio = len(mock_files) / scanned

    if not mapped or (decisive == 0 and not probes and not implementation_present):
        reasoning.append("无映射/无实现/无执行证据 → 平台未支持")
        return Grade(capability_id, "NOT_SUPPORTED", reasoning, basis="none", markers=markers)

    if stub_ratio >= float(pol["stub_dominance"]):
        reasoning.append(f"空壳标记覆盖 {len(stub_files)}/{scanned} 文件 ≥ "
                         f"{pol['stub_dominance']:.0%} → STUB")
        return Grade(capability_id, "STUB", reasoning, basis="static_markers", markers=markers)

    if mock_ratio >= float(pol["mock_dominance"]):
        reasoning.append(f"造假标记覆盖 {len(mock_files)}/{scanned} 文件 ≥ "
                         f"{pol['mock_dominance']:.0%} → MOCK")
        return Grade(capability_id, "MOCK", reasoning, basis="static_markers", markers=markers)

    level: Optional[str] = None
    basis = "none"

    if decisive:
        basis = "executed_tests"
        min_tests = int(tests.get("min_tests", pol["min_tests"]) or pol["min_tests"])
        threshold = float(tests.get("min_pass_rate", pol["min_pass_rate"]) or pol["min_pass_rate"])
        error_ratio = errored / decisive
        reasoning.append(
            f"执行 {decisive} 条映射测试：pass_rate={pass_rate:.3f}"
            f"（阈值 {threshold:.2f}），failed={failed} errored={errored}"
            f"（error 占比 {error_ratio:.2f}，阈值 {pol['error_dominance']:.2f}）")
        if error_ratio >= float(pol["error_dominance"]):
            level = "WITHHELD"
            reasoning.append("收集期错误占主导（缺工件/环境）→ 测试结果不可用于判负，判 WITHHELD")
        elif decisive < min_tests:
            level = "WITHHELD"
            reasoning.append(f"测试数量 {decisive} < 声明下限 {min_tests} → 证据不足")
        elif pass_rate < threshold:
            level = "MOCK"
            reasoning.append("通过率低于验收阈值 → 实现过不了自身验收")
        elif failed or (errored and pol.get("real_requires_zero_failures", True)):
            level = "WITHHELD"
            reasoning.append("通过率达标但存在失败/错误用例 → 证据不干净，判 WITHHELD")
        else:
            level = "REAL"
            reasoning.append("映射测试全绿且达数量下界 → REAL")

    if probes:
        available = [p for p in probes if p.get("available")]
        unavailable = [p for p in probes if not p.get("available")]
        if unavailable:
            reasoning.append(
                "探针不可用（环境未起）：" + ",".join(sorted(p["probe"] for p in unavailable))
                + " → 不据此升级，也不据此降级")
        failing = [p for p in available if not p.get("ok")]
        analytic_ok = [p for p in available if p.get("ok")
                       and p.get("criterion_kind") == "analytic_identity"]
        if failing:
            reasoning.append("探针判据不通过：" + ",".join(
                sorted(p["probe"] for p in failing)) + " → 实测越限")
            level = "MOCK" if level in (None, "REAL") else level
            basis = "runtime_probe"
        elif available and level is None:
            basis = "runtime_probe"
            if analytic_ok:
                level = "REAL"
                reasoning.append("探针以解析/独立解对比取证（analytic_identity）→ REAL")
            else:
                level = "WITHHELD"
                reasoning.append("仅有行为性探针证据（behavioral），无对标误差 → WITHHELD")

    if level is None:
        level = "WITHHELD"
        reasoning.append("有实现但无任何执行证据（测试/探针）→ WITHHELD")

    ceiling_applied = None
    if ceiling and ceiling in LEVEL_RANK and LEVEL_RANK[level] > LEVEL_RANK[ceiling]:
        reasoning.append(
            f"人工复核上限生效：自动判级 {level} 高于最新人工裁决 {ceiling} → 取 {ceiling}"
            "（自动判级不得越过人工复核，防 OVERCLAIM）")
        ceiling_applied = ceiling
        level = ceiling
    return Grade(capability_id, level, reasoning, basis=basis,
                 ceiling_applied=ceiling_applied, markers=markers)


def summarize(grades: Iterable[Grade]) -> Dict[str, Any]:
    """等级分布汇总（供报告程序化注入，零手抄）。"""
    tally = {level: 0 for level in LEVELS}
    for grade in grades:
        tally[grade.level] += 1
    total = sum(tally.values())
    return {
        "total": total,
        "grade_tally": tally,
        "real_fraction": round(tally["REAL"] / total, 4) if total else 0.0,
    }