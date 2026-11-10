"""证据扫描器（evidence scanner）。

对每项能力扫描三个维度：
(a) 代码 src/ 下是否有对应实现（函数/类/模块名匹配）
(b) 测试 tests/ 下是否有对应测试且通过率>80%
(c) 报告 tensor_reports/ 中是否有对应场景且 verdict≠FAIL

零领域语义：仅通过能力定义注册表的模式匹配，不硬编码任何域词条。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import CapabilityDef, CapabilityRegistry

# 扫描排除目录
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", "artifacts", "runs",
}
SCAN_EXTENSIONS = {".py", ".md", ".json"}
MAX_FILE_BYTES = 500_000


@dataclass
class CodeEvidence:
    """代码证据。"""
    file_path: str
    line_number: int
    match_type: str  # "function" | "class" | "module" | "comment"
    matched_text: str
    line_sha256: str
    file_sha256: str


@dataclass
class TestEvidence:
    """测试证据。"""
    file_path: str
    test_name: str
    pass_rate: float  # 0.0-1.0
    total_tests: int
    passed_tests: int
    file_sha256: str


@dataclass
class ReportEvidence:
    """报告证据。"""
    file_path: str
    scenario_key: str
    verdict: str  # "PASS" | "FAIL" | "WARN" | "HONEST_FAIL" | "WITHHELD"
    capability_match: str
    file_sha256: str


@dataclass
class ScanResult:
    """单条能力的扫描结果。"""
    capability_id: str
    code_evidence: list[CodeEvidence] = field(default_factory=list)
    test_evidence: list[TestEvidence] = field(default_factory=list)
    report_evidence: list[ReportEvidence] = field(default_factory=list)
    code_count: int = 0
    test_count: int = 0
    report_count: int = 0
    has_implementation: bool = False
    has_tests: bool = False
    has_reports: bool = False
    test_pass_rate: float = 0.0
    report_verdicts: list[str] = field(default_factory=list)
    scan_notes: list[str] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    """计算文件 sha256。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    """计算文本 sha256。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def line_sha256(line: str) -> str:
    """计算行 sha256 前缀。"""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def iter_files(root: Path, subdirs: list[str], max_files: int = 20000) -> list[Path]:
    """有界遍历文件。"""
    files = []
    count = 0
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            # 排除目录
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fname in filenames:
                if count >= max_files:
                    return files
                ext = os.path.splitext(fname)[1]
                if ext in SCAN_EXTENSIONS:
                    fpath = Path(dirpath) / fname
                    if fpath.stat().st_size <= MAX_FILE_BYTES:
                        files.append(fpath)
                        count += 1
    return files


def scan_code_for_patterns(
    root: Path,
    patterns: list[str],
    subdir: str = "src",
) -> list[CodeEvidence]:
    """在 src/ 下扫描代码实现。

    使用 AST 解析识别函数/类定义，也支持文本模式匹配。
    """
    evidence = []
    files = iter_files(root, [subdir])

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        file_hash = sha256_file(fpath)
        rel_path = str(fpath.relative_to(root))

        # 先做文本模式匹配（快速过滤）
        matched_patterns = []
        for pat in patterns:
            if re.search(pat, content, re.IGNORECASE):
                matched_patterns.append(pat)

        if not matched_patterns:
            continue

        # AST 解析识别函数/类
        try:
            tree = ast.parse(content, filename=str(fpath))
        except SyntaxError:
            # 回退到文本匹配
            for i, line in enumerate(content.split("\n"), 1):
                for pat in matched_patterns:
                    if re.search(pat, line, re.IGNORECASE):
                        evidence.append(CodeEvidence(
                            file_path=rel_path,
                            line_number=i,
                            match_type="text",
                            matched_text=line.strip()[:120],
                            line_sha256=line_sha256(line.strip()),
                            file_sha256=file_hash,
                        ))
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for pat in matched_patterns:
                    if re.search(pat, node.name, re.IGNORECASE):
                        evidence.append(CodeEvidence(
                            file_path=rel_path,
                            line_number=node.lineno,
                            match_type="function",
                            matched_text=f"def {node.name}",
                            line_sha256=line_sha256(f"def {node.name}"),
                            file_sha256=file_hash,
                        ))
            elif isinstance(node, ast.ClassDef):
                for pat in matched_patterns:
                    if re.search(pat, node.name, re.IGNORECASE):
                        evidence.append(CodeEvidence(
                            file_path=rel_path,
                            line_number=node.lineno,
                            match_type="class",
                            matched_text=f"class {node.name}",
                            line_sha256=line_sha256(f"class {node.name}"),
                            file_sha256=file_hash,
                        ))

        # 模块级匹配（文件名匹配）
        module_name = fpath.stem
        for pat in matched_patterns:
            if re.search(pat, module_name, re.IGNORECASE):
                # 检查是否已有更具体的匹配
                existing = [e for e in evidence if e.file_path == rel_path]
                if not existing:
                    evidence.append(CodeEvidence(
                        file_path=rel_path,
                        line_number=1,
                        match_type="module",
                        matched_text=f"module: {module_name}",
                        line_sha256=line_sha256(module_name),
                        file_sha256=file_hash,
                    ))

    return evidence


def detect_mock_markers(content: str) -> list[str]:
    """检测代码中的 MOCK 标记。"""
    markers = []
    strong_markers = (
        "[MOCK]", "MOCK:", "np.random.rand", "random.uniform",
        "random.random", "mock_response", "dummy_value",
    )
    for marker in strong_markers:
        if marker in content:
            markers.append(marker)
    return markers


def detect_stub_markers(content: str) -> list[str]:
    """检测代码中的 STUB 标记。"""
    markers = []
    stub_markers = (
        "NotImplementedError", "TODO: implement", "# stub", "placeholder",
    )
    for marker in stub_markers:
        if marker in content:
            markers.append(marker)
    return markers


def scan_tests_for_patterns(
    root: Path,
    patterns: list[str],
    subdir: str = "tests",
) -> list[TestEvidence]:
    """在 tests/ 下扫描测试。

    解析测试文件，统计测试函数数量和通过率（从 pytest 输出或文件内容推断）。
    """
    evidence = []
    files = iter_files(root, [subdir])

    for fpath in files:
        if not fpath.name.startswith("test_") and not fpath.name.endswith("_test.py"):
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # 检查是否匹配能力模式
        matched = False
        for pat in patterns:
            if re.search(pat, content, re.IGNORECASE) or re.search(pat, fpath.stem, re.IGNORECASE):
                matched = True
                break

        if not matched:
            continue

        file_hash = sha256_file(fpath)
        rel_path = str(fpath.relative_to(root))

        # 解析测试函数
        test_funcs = []
        try:
            tree = ast.parse(content, filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("test_"):
                        test_funcs.append(node.name)
        except SyntaxError:
            # 文本匹配
            for line in content.split("\n"):
                m = re.match(r"\s*def\s+(test_\w+)", line)
                if m:
                    test_funcs.append(m.group(1))

        if not test_funcs:
            continue

        # 推断通过率：检查是否有 skip/xfail 标记
        skip_count = 0
        fail_count = 0
        for line in content.split("\n"):
            if "@pytest.mark.skip" in line or "@pytest.mark.xfail" in line:
                skip_count += 1
            if "pytest.fail" in line or "assert False" in line:
                fail_count += 1

        total = len(test_funcs)
        passed = total - skip_count - fail_count
        pass_rate = passed / total if total > 0 else 0.0

        evidence.append(TestEvidence(
            file_path=rel_path,
            test_name=test_funcs[0] if test_funcs else "",
            pass_rate=round(pass_rate, 4),
            total_tests=total,
            passed_tests=max(0, passed),
            file_sha256=file_hash,
        ))

    return evidence


def scan_reports_for_patterns(
    root: Path,
    patterns: list[str],
    subdir: str = "tensor_reports",
) -> list[ReportEvidence]:
    """在 tensor_reports/ 下扫描报告。

    解析 JSON 报告，提取 verdict 和能力匹配信息。
    """
    evidence = []
    files = iter_files(root, [subdir])

    for fpath in files:
        if fpath.suffix != ".json":
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # 检查是否匹配能力模式
        matched = False
        matched_pattern = ""
        for pat in patterns:
            if re.search(pat, content, re.IGNORECASE) or re.search(pat, fpath.stem, re.IGNORECASE):
                matched = True
                matched_pattern = pat
                break

        if not matched:
            continue

        file_hash = sha256_file(fpath)
        rel_path = str(fpath.relative_to(root))

        # 解析 JSON 报告
        try:
            report = json.loads(content)
        except json.JSONDecodeError:
            continue

        # 提取 verdict
        verdict = report.get("verdict", report.get("status", "UNKNOWN"))
        if isinstance(verdict, dict):
            verdict = verdict.get("verdict", verdict.get("status", "UNKNOWN"))

        # 提取 scenario_key
        scenario_key = report.get("scenario_key", report.get("mission_id", fpath.stem))

        evidence.append(ReportEvidence(
            file_path=rel_path,
            scenario_key=str(scenario_key),
            verdict=str(verdict).upper(),
            capability_match=matched_pattern,
            file_sha256=file_hash,
        ))

    return evidence


class EvidenceScanner:
    """证据扫描器。

    对注册表中的每项能力，扫描代码/测试/报告三个维度。
    """

    def __init__(self, root: Path, registry: CapabilityRegistry):
        self.root = root
        self.registry = registry

    def scan_capability(self, cap: CapabilityDef) -> ScanResult:
        """扫描单条能力。"""
        result = ScanResult(capability_id=cap.capability_id)

        # 扫描代码
        if cap.code_patterns:
            code_ev = scan_code_for_patterns(self.root, cap.code_patterns)
            result.code_evidence = code_ev
            result.code_count = len(code_ev)
            result.has_implementation = len(code_ev) > 0

            # 检测 MOCK/STUB 标记
            for ev in code_ev:
                try:
                    fpath = self.root / ev.file_path
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    mock_markers = detect_mock_markers(content)
                    stub_markers = detect_stub_markers(content)
                    if mock_markers:
                        result.scan_notes.append(f"MOCK markers in {ev.file_path}: {mock_markers}")
                    if stub_markers:
                        result.scan_notes.append(f"STUB markers in {ev.file_path}: {stub_markers}")
                except Exception:
                    pass

        # 扫描测试
        if cap.test_patterns:
            test_ev = scan_tests_for_patterns(self.root, cap.test_patterns)
            result.test_evidence = test_ev
            result.test_count = len(test_ev)
            result.has_tests = len(test_ev) > 0

            # 计算综合通过率
            if test_ev:
                total_tests = sum(t.total_tests for t in test_ev)
                passed_tests = sum(t.passed_tests for t in test_ev)
                result.test_pass_rate = round(passed_tests / total_tests, 4) if total_tests > 0 else 0.0

        # 扫描报告
        if cap.report_patterns:
            report_ev = scan_reports_for_patterns(self.root, cap.report_patterns)
            result.report_evidence = report_ev
            result.report_count = len(report_ev)
            result.has_reports = len(report_ev) > 0
            result.report_verdicts = [r.verdict for r in report_ev]

        return result

    def scan_all(self) -> dict[str, ScanResult]:
        """扫描所有能力。"""
        results = {}
        for cap in self.registry.list_all():
            results[cap.capability_id] = self.scan_capability(cap)
        return results
