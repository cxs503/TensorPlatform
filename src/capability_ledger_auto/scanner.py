"""证据扫描器（evidence scanner）。

对每项能力扫描三个维度：
(a) 代码 src/ 下是否有对应实现（函数/类/模块名匹配）
(b) 测试 tests/ 下是否有对应测试且通过率>80%（可执行真实 pytest）
(c) 报告 tensor_reports/ 或报告语料中是否有对应场景且裁决非 FAIL

零领域语义：仅通过能力定义注册表的模式匹配，不硬编码任何域词条。

证据强度纪律（本模块的核心不变量）
--------------------------------
* **节点级证据**：MOCK/STUB 标记只在**匹配到的函数/类体内**识别，
  不再因为"仓库里某个不相关文件出现过 NotImplementedError"就把整条能力判成 STUB。
* **自指防护**：扫描器自身的包目录默认排除，否则 scanner.py 里的标记字面量
  会反过来污染被扫描能力（曾经导致全部能力误判 STUB）。
* **真实执行优先**：测试通过率可由真实 pytest 执行得到（`source="executed"`）；
  静态推断（`source="static"`）只是降级信号，不可冒充执行证据。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess  # nosec B404 - 仅用于有界地跑仓内 pytest
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .registry import CapabilityDef, CapabilityRegistry

# 扫描排除目录
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", "artifacts", "runs",
}
SCAN_EXTENSIONS = {".py", ".md", ".json"}
MAX_FILE_BYTES = 500_000

# 默认排除路径（相对仓库根）：扫描器自身所在包 = 自指污染源
DEFAULT_EXCLUDE_PREFIXES = ("src/capability_ledger_auto/",)

# 默认代码扫描根（仓库布局，非域语义）。
# 修复前只扫 "src/"：平台真实实现大量位于 backend/（如 backend/modules/、
# backend/api/domain/），这些能力的代码证据恒为空 → 五元缺 code → 永远判不出
# REAL。带标注算例（eval_validity，variant=backend_root）实测 REAL 召回率
# 0/2，全部被误判 WITHHELD。故默认改为多根扫描，并允许按能力/按调用覆盖。
DEFAULT_CODE_ROOTS = ("src", "backend", "api", "services")

# 测试代码不算"实现证据"（仓库布局规则，非域语义）。
# 证据：多根扫描后 ``backend/tests/test_ship_proxy.py::test_stability_call``
# 体内的 ``mock_response`` 把 calc.stability 判成 MOCK——测试替身不是被审实现。
TEST_PATH_SEGMENTS = {"tests", "test", "__tests__"}
TEST_FILE_PREFIXES = ("test_", "conftest")


def is_test_path(rel_path: str) -> bool:
    """判断相对路径是否是测试代码（按目录名/文件名，不看内容）。"""
    parts = rel_path.replace("\\", "/").split("/")
    if any(seg in TEST_PATH_SEGMENTS for seg in parts[:-1]):
        return True
    name = parts[-1]
    return name.startswith(TEST_FILE_PREFIXES)

# MOCK 标记：返回假数据 / 随机数 / 显式 mock 契约
MOCK_MARKERS = (
    "[MOCK]", "MOCK:", "np.random.rand", "np.random.uniform",
    "random.uniform", "random.random", "mock_response", "dummy_value",
)
# STUB 标记：占位实现
STUB_MARKERS = (
    "NotImplementedError", "TODO: implement", "# stub", "placeholder",
)

# 裁决词表（治理语义，与写域词条无关）：报告里对能力的裁决
VERDICT_LEVELS = {
    "PASS", "WARN", "FAIL", "HONEST_FAIL", "WITHHELD", "UNKNOWN",
    "REAL", "MOCK", "STUB", "NOT_SUPPORTED", "UNVERIFIED",
}
PASSING_LEVELS = {"PASS", "REAL"}
FAILING_LEVELS = {"FAIL", "HONEST_FAIL", "MOCK", "STUB"}


@dataclass
class CodeEvidence:
    """代码证据（节点级）。

    ``is_stub_like`` / ``mock_markers`` 只描述**该匹配节点自己的函数体**，
    不使用整文件级信号，避免"一个文件里有一处 NotImplementedError"污染全仓能力判级。
    """
    file_path: str
    line_number: int
    match_type: str  # "function" | "class" | "module" | "comment" | "text"
    matched_text: str
    line_sha256: str
    file_sha256: str
    is_stub_like: bool = False
    mock_markers: list[str] = field(default_factory=list)


@dataclass
class TestEvidence:
    """测试证据。"""
    file_path: str
    test_name: str
    pass_rate: float  # 0.0-1.0
    total_tests: int
    passed_tests: int
    file_sha256: str
    failed_tests: int = 0
    source: str = "static"  # "executed" | "static"
    detail: str = ""


@dataclass
class ReportEvidence:
    """报告证据。"""
    file_path: str
    scenario_key: str
    verdict: str  # "PASS" | "FAIL" | "WARN" | "HONEST_FAIL" | "WITHHELD" | ...
    capability_match: str
    file_sha256: str
    asserted_level: str = ""  # 报告对被审能力的显式裁决（REAL/MOCK/...）
    as_of: str = ""  # 裁决时间戳（用于取"最新裁决"）
    source_kind: str = "repo_json"  # repo_json | corpus
    note: str = ""


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
    # 节点级实现证据
    substantive_code_count: int = 0
    stub_only_code_count: int = 0
    mock_markers: list[str] = field(default_factory=list)
    # 测试证据强度
    test_evidence_source: str = ""  # "executed" | "static" | ""
    executed_test_files: int = 0
    executed_passed: int = 0
    executed_failed: int = 0
    # 最新报告裁决
    adjudicated_level: str = ""
    adjudicated_as_of: str = ""
    adjudicated_source: str = ""
    # 弱证据计数（仅文件文本行命中，未锚定到 AST 节点）：不得算作"实质实现"
    weak_code_count: int = 0
    # 本次实际扫描的代码根（可复算审计用）
    code_roots: list[str] = field(default_factory=list)
    # 计数是否由扫描器本人给出（True 时判级器不得再用启发式回退重算，避免
    # 弱文本证据被回退逻辑重新算回"实质实现"）
    counts_authoritative: bool = False


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


def _norm_rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace(os.sep, "/")
    except ValueError:
        return str(path).replace(os.sep, "/")


def iter_files(
    root: Path,
    subdirs: list[str],
    max_files: int = 20000,
    exclude_prefixes: Iterable[str] = (),
) -> list[Path]:
    """有界遍历文件（结果排序，保证两次扫描字节一致）。"""
    excludes = tuple(exclude_prefixes or ())
    files: list[Path] = []
    count = 0
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
            for fname in sorted(filenames):
                if count >= max_files:
                    return files
                ext = os.path.splitext(fname)[1]
                if ext in SCAN_EXTENSIONS:
                    fpath = Path(dirpath) / fname
                    rel = _norm_rel(root, fpath)
                    if any(rel.startswith(p) for p in excludes):
                        continue
                    try:
                        if fpath.stat().st_size <= MAX_FILE_BYTES:
                            files.append(fpath)
                            count += 1
                    except OSError:
                        continue
    return files


# ---------------------------------------------------------------- AST 节点分析

def _dotted_name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return ""


def is_stub_like(node: ast.AST) -> bool:
    """判断函数/类体是否为"只有接口声明、没有实现"。

    仅当函数体只含 docstring / ``pass`` / ``...`` / ``raise NotImplementedError`` 时成立。
    """
    body = list(getattr(node, "body", []) or [])
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return True
    for st in body:
        if isinstance(st, ast.Pass):
            continue
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and st.value.value is Ellipsis:
            continue
        if isinstance(st, ast.Raise):
            name = _dotted_name(st.exc)
            if name.split(".")[-1] == "NotImplementedError":
                continue
        return False
    return True


def node_mock_markers(node: ast.AST, content: str) -> list[str]:
    """在节点自身源码片段内识别 MOCK 标记。"""
    try:
        segment = ast.get_source_segment(content, node) or ""
    except Exception:
        segment = ""
    return [m for m in MOCK_MARKERS if m in segment]


@dataclass
class CodeMatch:
    """一次模式匹配的结果（证据有界 + 截断/跳过的如实登记）。"""
    evidence: list[CodeEvidence]
    truncated: int = 0          # 超出 max_hits 未落盘的证据条数
    parse_skipped: int = 0      # 超出 AST 解析预算被跳过的文件数


@dataclass
class FileRecord:
    """代码索引的单文件记录（只读一次；AST 按需解析一次并缓存）。"""
    rel_path: str
    file_sha256: str
    content: str
    tree: ast.AST | None = None
    parsed: bool = False


class CodeIndex:
    """代码索引：一次遍历、一次 AST 解析，供多条能力复用。

    修复前每条能力各自 ``os.walk`` + 读文件 + ``ast.parse``：21 条能力 × 4 个
    扫描根会让仓库被反复解析数十遍（实测套件从 ~95 s 涨到 >900 s）。索引把
    这部分工作摊到一次，扫描结果仍按 ``(file_path, line_number, match_type)``
    排序，保证确定性不变。
    """

    def __init__(self, records: list[FileRecord]):
        self.records = records

    @classmethod
    def build(
        cls,
        root: Path,
        code_roots: Iterable[str],
        exclude_prefixes: Iterable[str] = DEFAULT_EXCLUDE_PREFIXES,
        max_files: int = 20000,
    ) -> "CodeIndex":
        root = Path(root)
        records: list[FileRecord] = []
        seen: set[str] = set()
        for sub in code_roots:
            for fpath in iter_files(root, [sub], max_files=max_files,
                                    exclude_prefixes=exclude_prefixes):
                rel = _norm_rel(root, fpath)
                if rel in seen or is_test_path(rel):
                    continue
                seen.add(rel)
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                # 不在建索引时解析 AST：仓库里绝大多数文件与任何能力模式无关，
                # 全量 ast.parse 是纯浪费（实测 951 个 .py 文件 14 s）。
                # 解析推迟到"该文件命中某能力模式"的那一刻，并按文件缓存。
                records.append(FileRecord(rel, sha256_file(fpath), content))
        records.sort(key=lambda r: r.rel_path)
        return cls(records)

    def match(
        self,
        patterns: list[str],
        max_hits: int = 300,
        max_parse_files: int = 80,
        max_text_hits: int = 120,
    ) -> "CodeMatch":
        """按模式匹配索引，返回 ``(证据列表[有界], 被截断条数)``。

        预筛用一条合并正则（每条能力对每个文件只扫一遍）；只有命中文件才解析 AST。
        """
        if not patterns:
            return CodeMatch([], 0, 0)
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        combined = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
        evidence: list[CodeEvidence] = []
        total = 0
        skipped = 0
        parsed_now = 0
        text_emitted = 0
        text_truncated = 0
        for rec in self.records:
            if combined.search(rec.content) is None:
                continue
            matched = [p for p, rx in zip(patterns, compiled) if rx.search(rec.content)]
            if not matched:
                continue
            has_symbol = any(
                any(rx.search(name) for rx in compiled) for name in _symbol_names(rec.content)
            )
            if not has_symbol:
                # 没有同名符号：不需要 AST。产出模块级（若文件名命中）+ 文本行级
                # （弱证据，不计入实质实现）。文本弱证据总量有界，避免短模式在
                # md/json 语料上炸出海量行命中（曾实测单能力 1 万+ 行）。
                hits = _module_hit(rec, matched)
                remaining = max_text_hits - text_emitted
                if remaining > 0:
                    text_hits = _text_hits(rec, compiled, max_lines=min(20, remaining))
                    text_emitted += len(text_hits)
                    hits = hits + text_hits
                else:
                    # 弱证据预算用尽：该文件的行级命中不再落盘，按文件计一次截断
                    text_truncated += 1
                total += len(hits)
                if len(evidence) < max_hits:
                    evidence.extend(hits[: max_hits - len(evidence)])
                continue
            if not rec.parsed:
                # 有界解析：宽短模式（Cf/Rt 之类）仍可能命中很多同名符号文件，
                # 全量 ast.parse 是算力开销；超预算的文件如实登记为跳过。
                if parsed_now >= max_parse_files:
                    skipped += 1
                    continue
                parsed_now += 1
                try:
                    rec.tree = ast.parse(rec.content, filename=rec.rel_path)
                except SyntaxError:
                    rec.tree = None
                rec.parsed = True
            hits = _match_file(rec, matched)
            total += len(hits)
            if len(evidence) < max_hits:
                evidence.extend(hits[: max_hits - len(evidence)])
        return CodeMatch(evidence, max(0, total - len(evidence)) + text_truncated, skipped)


_SYMBOL_RX = re.compile(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_]\w*)", re.MULTILINE)


def _symbol_names(content: str) -> list[str]:
    """廉价抽取 def/class 名（正则，C 级扫描，不建 AST）。

    用途：只有"名字命中模式"的文件才值得 ``ast.parse``。仓库里绝大多数命中
    文件（含 .md/.json 语料）根本没有同名符号，全量解析是纯浪费。
    """
    return _SYMBOL_RX.findall(content)


def _text_hits(
    rec: FileRecord,
    compiled: list[re.Pattern[str]],
    max_lines: int = 20,
) -> list[CodeEvidence]:
    """文本行级弱证据（未锚定 AST 节点）。"""
    out: list[CodeEvidence] = []
    for i, line in enumerate(rec.content.split("\n"), 1):
        if any(rx.search(line) for rx in compiled):
            out.append(CodeEvidence(
                file_path=rec.rel_path,
                line_number=i,
                match_type="text",
                matched_text=line.strip()[:120],
                line_sha256=line_sha256(line.strip()),
                file_sha256=rec.file_sha256,
            ))
            if len(out) >= max_lines:
                break
    return out


def _module_hit(rec: FileRecord, matched_patterns: list[str]) -> list[CodeEvidence]:
    module_name = Path(rec.rel_path).stem
    if any(re.search(p, module_name, re.IGNORECASE) for p in matched_patterns):
        return [CodeEvidence(
            file_path=rec.rel_path,
            line_number=1,
            match_type="module",
            matched_text=f"module: {module_name}",
            line_sha256=line_sha256(module_name),
            file_sha256=rec.file_sha256,
        )]
    return []


def _match_file(rec: FileRecord, matched_patterns: list[str]) -> list[CodeEvidence]:
    """在单个已索引文件上产出证据（AST 节点级，语法错误时退化为文本行级）。"""
    rel_path = rec.rel_path
    file_hash = rec.file_sha256
    evidence: list[CodeEvidence] = []
    if rec.tree is None:
        for i, line in enumerate(rec.content.split("\n"), 1):
            if any(re.search(p, line, re.IGNORECASE) for p in matched_patterns):
                evidence.append(CodeEvidence(
                    file_path=rel_path,
                    line_number=i,
                    match_type="text",
                    matched_text=line.strip()[:120],
                    line_sha256=line_sha256(line.strip()),
                    file_sha256=file_hash,
                ))
        return evidence

    for node in ast.walk(rec.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            hit = [p for p in matched_patterns if re.search(p, node.name, re.IGNORECASE)]
            if not hit:
                continue
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            label = f"class {node.name}" if kind == "class" else f"def {node.name}"
            evidence.append(CodeEvidence(
                file_path=rel_path,
                line_number=node.lineno,
                match_type=kind,
                matched_text=label,
                line_sha256=line_sha256(label),
                file_sha256=file_hash,
                is_stub_like=is_stub_like(node),
                mock_markers=node_mock_markers(node, rec.content),
            ))

    module_name = Path(rel_path).stem
    if any(re.search(p, module_name, re.IGNORECASE) for p in matched_patterns):
        if not any(e.file_path == rel_path for e in evidence):
            evidence.append(CodeEvidence(
                file_path=rel_path,
                line_number=1,
                match_type="module",
                matched_text=f"module: {module_name}",
                line_sha256=line_sha256(module_name),
                file_sha256=file_hash,
            ))
    return evidence


def scan_code_for_patterns(
    root: Path,
    patterns: list[str],
    subdir: str = "src",
    exclude_prefixes: Iterable[str] = DEFAULT_EXCLUDE_PREFIXES,
    code_roots: Iterable[str] | None = None,
    index: "CodeIndex | None" = None,
    max_hits: int = 500,
) -> list[CodeEvidence]:
    """扫描代码实现（默认 ``subdir``；给出 ``code_roots`` 时扫多个根）。

    使用 AST 解析识别函数/类定义，也支持文本模式匹配。多根扫描按
    ``(file_path, line_number, match_type)`` 去重并排序，保证确定性。
    传入 ``index`` 时复用已建索引（同一仓库多能力扫描的唯一正确用法）。
    """
    if index is not None:
        return index.match(patterns, max_hits=max_hits).evidence
    roots = list(code_roots) if code_roots else [subdir]
    if len(roots) == 1:
        return _scan_code_in_subdir(root, patterns, roots[0], exclude_prefixes)
    merged: dict[tuple[str, int, str], CodeEvidence] = {}
    for sub in roots:
        for ev in _scan_code_in_subdir(root, patterns, sub, exclude_prefixes):
            merged[(ev.file_path, ev.line_number, ev.match_type)] = ev
    return [merged[k] for k in sorted(merged)]


def _scan_code_in_subdir(
    root: Path,
    patterns: list[str],
    subdir: str,
    exclude_prefixes: Iterable[str] = DEFAULT_EXCLUDE_PREFIXES,
) -> list[CodeEvidence]:
    """在单个子目录下扫描代码实现（AST 节点级 + 文本级兜底）。"""
    evidence: list[CodeEvidence] = []
    files = [f for f in iter_files(root, [subdir], exclude_prefixes=exclude_prefixes)
             if not is_test_path(_norm_rel(root, f))]

    for fpath in files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        file_hash = sha256_file(fpath)
        rel_path = _norm_rel(root, fpath)

        matched_patterns = [p for p in patterns if re.search(p, content, re.IGNORECASE)]
        if not matched_patterns:
            continue

        try:
            tree = ast.parse(content, filename=str(fpath))
        except SyntaxError:
            for i, line in enumerate(content.split("\n"), 1):
                if any(re.search(p, line, re.IGNORECASE) for p in matched_patterns):
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
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                hit = [p for p in matched_patterns if re.search(p, node.name, re.IGNORECASE)]
                if not hit:
                    continue
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                label = f"class {node.name}" if kind == "class" else f"def {node.name}"
                evidence.append(CodeEvidence(
                    file_path=rel_path,
                    line_number=node.lineno,
                    match_type=kind,
                    matched_text=label,
                    line_sha256=line_sha256(label),
                    file_sha256=file_hash,
                    is_stub_like=is_stub_like(node),
                    mock_markers=node_mock_markers(node, content),
                ))

        module_name = fpath.stem
        if any(re.search(p, module_name, re.IGNORECASE) for p in matched_patterns):
            if not any(e.file_path == rel_path for e in evidence):
                evidence.append(CodeEvidence(
                    file_path=rel_path,
                    line_number=1,
                    match_type="module",
                    matched_text=f"module: {module_name}",
                    line_sha256=line_sha256(module_name),
                    file_sha256=file_hash,
                ))

    evidence.sort(key=lambda e: (e.file_path, e.line_number, e.matched_text))
    return evidence


def detect_mock_markers(content: str) -> list[str]:
    """检测文本中的 MOCK 标记（文件级工具函数，仅用于人工提示）。"""
    return [m for m in MOCK_MARKERS if m in content]


def detect_stub_markers(content: str) -> list[str]:
    """检测文本中的 STUB 标记（文件级工具函数，仅用于人工提示）。"""
    return [m for m in STUB_MARKERS if m in content]


# ---------------------------------------------------------------- 测试证据

_PYTEST_COUNT_RE = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "error": re.compile(r"(\d+) errors?\b"),
    "skipped": re.compile(r"(\d+) skipped"),
}


def parse_pytest_counts(output: str) -> dict[str, int]:
    """从 pytest 输出解析计数（缺失键=0）。"""
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for key, rx in _PYTEST_COUNT_RE.items():
        m = rx.search(output)
        if m:
            counts[key] = int(m.group(1))
    return counts


def run_test_file(
    root: Path,
    rel_path: str,
    timeout_s: int = 60,
    python: str | None = None,
) -> dict[str, Any] | None:
    """用一个 pytest 子进程真实执行单个测试文件。

    返回 ``{"passed","failed","error","skipped","returncode","source":"executed"}``；
    超时/无法启动时返回 ``None``（调用方必须如实降级，不得假装通过）。
    """
    cmd = [python or sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", rel_path]
    try:
        proc = subprocess.run(  # nosec B603 - 固定 argv，无 shell
            cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    counts = parse_pytest_counts(proc.stdout + proc.stderr)
    counts["returncode"] = proc.returncode
    counts["source"] = "executed"
    return counts


def _filename_matches(rel_path: str, patterns: list[str]) -> bool:
    stem = Path(rel_path).stem
    return any(re.search(p, stem, re.IGNORECASE) for p in patterns)


def scan_tests_for_patterns(
    root: Path,
    patterns: list[str],
    subdir: str = "tests",
    exclude_prefixes: Iterable[str] = (),
) -> list[TestEvidence]:
    """在 tests/ 下扫描测试（静态信号：文件内容/文件名匹配 + skip/xfail 推断）。"""
    evidence: list[TestEvidence] = []
    files = iter_files(root, [subdir], exclude_prefixes=exclude_prefixes)

    for fpath in files:
        if not fpath.name.startswith("test_") and not fpath.name.endswith("_test.py"):
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if not (any(re.search(p, content, re.IGNORECASE) for p in patterns)
                or any(re.search(p, fpath.stem, re.IGNORECASE) for p in patterns)):
            continue

        file_hash = sha256_file(fpath)
        rel_path = _norm_rel(root, fpath)

        test_funcs = []
        try:
            tree = ast.parse(content, filename=str(fpath))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    test_funcs.append(node.name)
        except SyntaxError:
            for line in content.split("\n"):
                m = re.match(r"\s*def\s+(test_\w+)", line)
                if m:
                    test_funcs.append(m.group(1))

        if not test_funcs:
            continue

        skip_count = 0
        fail_count = 0
        for line in content.split("\n"):
            if "@pytest.mark.skip" in line or "@pytest.mark.xfail" in line:
                skip_count += 1
            if "pytest.fail" in line or "assert False" in line:
                fail_count += 1

        total = len(test_funcs)
        passed = max(0, total - skip_count - fail_count)
        pass_rate = passed / total if total > 0 else 0.0

        evidence.append(TestEvidence(
            file_path=rel_path,
            test_name=test_funcs[0] if test_funcs else "",
            pass_rate=round(pass_rate, 4),
            total_tests=total,
            passed_tests=passed,
            failed_tests=min(total, skip_count + fail_count),
            file_sha256=file_hash,
            source="static",
            detail="static inference (skip/xfail markers only)",
        ))

    evidence.sort(key=lambda e: e.file_path)
    return evidence


# ---------------------------------------------------------------- 报告证据

_SOURCE_PRIORITY = {"repo_json": 3, "adjudication_ledger": 2, "corpus": 1}


def as_of_sort_key(value: str) -> str:
    """把 as_of 归一化成可比较的 UTC ISO 串。

    台账里时区混用（``+00:00`` / ``+08:00`` / 无时区），按字符串排序会错序，
    故统一换算到 UTC 再排序（无法解析时原样返回，退化但不崩）。
    """
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return str(value)

_ADJUDICATION_PATTERNS = (
    re.compile(r"审计裁决\*{0,2}[：:]\s*\*{0,2}([A-Z_]{3,14})"),
    re.compile(r"裁决\*{0,2}[：:]\s*\*{0,2}([A-Z_]{3,14})"),
    re.compile(r"判决\*{0,2}[：:]\s*\*{0,2}([A-Z_]{3,14})"),
    re.compile(r"verdict\*{0,2}[：:]\s*\*{0,2}([A-Za-z_]{3,14})"),
    re.compile(r'"asserted_level"\s*:\s*"([A-Z_]{3,14})"'),
)


def extract_adjudications(text: str) -> list[str]:
    """从报告文本抽取显式裁决等级（按出现顺序去重）。

    零领域语义：只认治理裁决词表 ``VERDICT_LEVELS``，不认任何域词条。
    """
    found: list[str] = []
    for rx in _ADJUDICATION_PATTERNS:
        for m in rx.finditer(text):
            lv = m.group(1).upper()
            if lv in VERDICT_LEVELS and lv not in found:
                found.append(lv)
    return found


def _match_capability_in_text(text: str, capability_ids: Iterable[str]) -> str:
    """用注册表里的能力 id 匹配文本（id 来自注册表，故不含硬编码域词）。"""
    for cid in capability_ids:
        if re.search(re.escape(cid), text, re.IGNORECASE):
            return cid
    return ""


def scan_reports_for_patterns(
    root: Path,
    patterns: list[str],
    subdir: str = "tensor_reports",
    exclude_prefixes: Iterable[str] = (),
) -> list[ReportEvidence]:
    """在 tensor_reports/ 下扫描 JSON 报告。"""
    evidence: list[ReportEvidence] = []
    files = iter_files(root, [subdir], exclude_prefixes=exclude_prefixes)

    for fpath in files:
        if fpath.suffix != ".json":
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        matched_pattern = ""
        for pat in patterns:
            if re.search(pat, content, re.IGNORECASE) or re.search(pat, fpath.stem, re.IGNORECASE):
                matched_pattern = pat
                break
        if not matched_pattern:
            continue

        try:
            report = json.loads(content)
        except json.JSONDecodeError:
            continue

        verdict = report.get("verdict", report.get("status", "UNKNOWN"))
        if isinstance(verdict, dict):
            verdict = verdict.get("verdict", verdict.get("status", "UNKNOWN"))
        verdict = str(verdict).upper()

        adjudication = extract_adjudications(content)
        asserted = adjudication[0] if adjudication else verdict
        scenario_key = report.get("scenario_key", report.get("mission_id", fpath.stem))
        as_of = str(report.get("created_at") or report.get("generated_at") or "")

        evidence.append(ReportEvidence(
            file_path=_norm_rel(root, fpath),
            scenario_key=str(scenario_key),
            verdict=verdict,
            capability_match=matched_pattern,
            file_sha256=sha256_file(fpath),
            asserted_level=asserted,
            as_of=as_of,
            source_kind="repo_json",
        ))

    evidence.sort(key=lambda e: e.file_path)
    return evidence


def scan_report_corpus(
    corpus_path: str | Path,
    capability_ids: Iterable[str],
    max_reports: int = 2000,
) -> list[ReportEvidence]:
    """扫描离线报告语料（``reports_corpus.json``：{reports:{id:{...}}, docs:{...}}）。

    只用注册表能力 id 做关联；裁决等级用 ``extract_adjudications`` 抽取。
    """
    corpus_path = Path(corpus_path)
    if not corpus_path.exists():
        return []
    with open(corpus_path, encoding="utf-8") as f:
        raw = json.load(f)

    file_hash = sha256_file(corpus_path)
    ids = list(capability_ids)
    evidence: list[ReportEvidence] = []
    retrieved_at = str(raw.get("retrieved_at", ""))

    def _consume(key: str, body: Any, kind: str) -> None:
        if len(evidence) >= max_reports:
            return
        if not isinstance(body, dict):
            return
        text = "\n".join(str(body.get(k, "")) for k in ("title", "summary", "markdown", "note", "quote_match"))
        cap_id = _match_capability_in_text(text, ids)
        if not cap_id:
            return
        adjudications = extract_adjudications(text)
        if not adjudications:
            return
        as_of = str(body.get("created_at") or body.get("as_of") or retrieved_at)
        evidence.append(ReportEvidence(
            file_path=f"{kind}:{key}",
            scenario_key=f"{kind}:{key}",
            verdict=adjudications[0],
            capability_match=cap_id,
            file_sha256=file_hash,
            asserted_level=adjudications[0],
            as_of=as_of,
            source_kind="corpus",
            note=str(body.get("note", ""))[:200],
        ))

    for key, body in sorted((raw.get("reports") or {}).items()):
        _consume(key, body, "report")
    docs = raw.get("docs") or {}
    if isinstance(docs, dict):
        for key, body in sorted(docs.items()):
            _consume(key, body, "doc")

    return evidence


def scan_adjudication_ledger(
    ledger_path: str | Path,
    capability_ids: Iterable[str],
) -> list[ReportEvidence]:
    """扫描**结构化裁决台账**（``capability_ledger.json`` 格式）作为报告证据源。

    台账里每条证据自带 ``asserted_level`` / ``as_of`` / ``stance``——这是治理记录
    （机器可读的裁决），不是自由文本，故不需要"猜语义"。用它而不是重新做 NLP 解析
    散文，是本引擎的显式设计选择（见 docs 报告"局限"节）。
    """
    ledger_path = Path(ledger_path)
    if not ledger_path.exists():
        return []
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    file_hash = sha256_file(ledger_path)
    wanted = set(capability_ids)
    out: list[ReportEvidence] = []
    for cap in raw.get("capabilities", []) or []:
        cid = cap.get("id") or cap.get("capability_id")
        if cid not in wanted:
            continue
        for ev in cap.get("evidence", []) or []:
            level = str(ev.get("asserted_level", "")).upper()
            if level not in VERDICT_LEVELS:
                continue
            key = ev.get("report_id") or ev.get("doc") or "adjudication"
            out.append(ReportEvidence(
                file_path=f"adjudication:{key}",
                scenario_key=f"adjudication:{key}",
                verdict=level,
                capability_match=cid,
                file_sha256=file_hash,
                asserted_level=level,
                as_of=str(ev.get("as_of", "")),
                source_kind="adjudication_ledger",
                note=f"stance={ev.get('stance', '')}; {str(ev.get('note', ''))[:160]}",
            ))
    out.sort(key=lambda r: (as_of_sort_key(r.as_of), r.file_path))
    return out


# ---------------------------------------------------------------- 扫描器

class EvidenceScanner:
    """证据扫描器。

    对注册表中的每项能力，扫描代码/测试/报告三个维度。
    """

    def __init__(
        self,
        root: Path,
        registry: CapabilityRegistry,
        exec_tests: bool = False,
        max_exec_files: int = 6,
        exec_timeout_s: int = 60,
        exec_budget_s: int = 900,
        corpus_paths: Iterable[str | Path] = (),
        adjudication_ledgers: Iterable[str | Path] = (),
        exclude_prefixes: Iterable[str] = DEFAULT_EXCLUDE_PREFIXES,
        test_cache: dict[str, dict[str, Any]] | None = None,
        python: str | None = None,
        exec_only: Iterable[str] | None = None,
        code_roots: Iterable[str] | None = DEFAULT_CODE_ROOTS,
    ):
        self.root = Path(root)
        self.registry = registry
        self.exec_tests = exec_tests
        self.max_exec_files = max_exec_files
        self.exec_timeout_s = exec_timeout_s
        self.exec_budget_s = exec_budget_s
        self.corpus_paths = [Path(p) for p in corpus_paths]
        self.adjudication_ledgers = [Path(p) for p in adjudication_ledgers]
        self.exclude_prefixes = tuple(exclude_prefixes)
        self.test_cache = test_cache if test_cache is not None else {}
        self.python = python
        self.exec_only = set(exec_only) if exec_only is not None else None
        # None = 用默认布局根；显式空元组 = 关闭多根扫描（退回单根 subdir 语义）
        self.code_roots = tuple(DEFAULT_CODE_ROOTS if code_roots is None else code_roots)
        self._code_index: CodeIndex | None = None
        self._budget_left = exec_budget_s
        self.exec_ledger: list[dict[str, Any]] = []

    # -- 单条能力 --------------------------------------------------------
    def scan_capability(self, cap: CapabilityDef) -> ScanResult:
        result = ScanResult(capability_id=cap.capability_id)

        # (a) 代码
        if cap.code_patterns:
            roots = tuple(getattr(cap, "code_roots", None) or self.code_roots or ())
            result.code_roots = list(roots)
            result.counts_authoritative = True
            if roots:
                if self._code_index is None:
                    self._code_index = CodeIndex.build(
                        self.root, roots, exclude_prefixes=self.exclude_prefixes,
                    )
                match = self._code_index.match(cap.code_patterns)
                code_ev = match.evidence
                if match.truncated:
                    result.scan_notes.append(
                        f"代码证据超出上限，已截断 {match.truncated} 条（根: {list(roots)}）"
                    )
                if match.parse_skipped:
                    result.scan_notes.append(
                        f"超出 AST 解析预算，跳过 {match.parse_skipped} 个命中文件"
                        "（宽模式+大仓时的有界扫描，判级按已解析部分）"
                    )
            else:
                code_ev = scan_code_for_patterns(
                    self.root, cap.code_patterns, exclude_prefixes=self.exclude_prefixes,
                )
            result.code_evidence = code_ev
            result.code_count = len(code_ev)
            result.has_implementation = any(
                e.match_type in ("function", "class") for e in code_ev
            ) or len(code_ev) > 0

            # 实质实现必须是**锚定到 AST 节点**的命中（def/class/module）。
            # 仅文件文本行命中（match_type="text"）记作弱证据：它证明"这个词出现在
            # 仓库里"，不证明"能力被实现"；把它算成实质实现会让短模式（Cf/Rt 之类）
            # 把任意能力抬到 WITHHELD，进而污染链条质量。
            substantive = [e for e in code_ev if e.match_type in ("function", "class", "module")
                           and not e.is_stub_like]
            stub_only = [e for e in code_ev if e.match_type in ("function", "class")
                         and e.is_stub_like]
            weak = [e for e in code_ev if e.match_type == "text"]
            result.substantive_code_count = len(substantive)
            result.stub_only_code_count = len(stub_only)
            result.weak_code_count = len(weak)
            for e in code_ev:
                for m in e.mock_markers:
                    result.mock_markers.append(f"{e.file_path}:{e.matched_text}:{m}")
            if stub_only:
                result.scan_notes.append(
                    f"stub-only 实现 {len(stub_only)} 处（仅签名/NotImplementedError），"
                    f"实质实现 {result.substantive_code_count} 处"
                )
            if result.mock_markers:
                result.scan_notes.append(
                    f"MOCK 标记 {len(result.mock_markers)} 处（匹配节点体内）"
                )
            if weak:
                result.scan_notes.append(
                    f"弱证据 {len(weak)} 处（仅文本行命中，未锚定 AST 节点），不计入实质实现"
                )

        # (b) 测试
        if cap.test_patterns:
            test_ev = scan_tests_for_patterns(
                self.root, cap.test_patterns, exclude_prefixes=self.exclude_prefixes,
            )
            exec_allowed = self.exec_only is None or cap.capability_id in self.exec_only
            if self.exec_tests and exec_allowed and test_ev:
                test_ev = self._execute_tests(cap, test_ev)
            result.test_evidence = test_ev
            result.test_count = len(test_ev)
            result.has_tests = len(test_ev) > 0

            executed = [t for t in test_ev if t.source == "executed"]
            if executed:
                total = sum(t.total_tests for t in executed)
                passed = sum(t.passed_tests for t in executed)
                result.test_pass_rate = round(passed / total, 4) if total else 0.0
                result.test_evidence_source = "executed"
                result.executed_test_files = len(executed)
                result.executed_passed = passed
                result.executed_failed = sum(t.failed_tests for t in executed)
            elif test_ev:
                total = sum(t.total_tests for t in test_ev)
                passed = sum(t.passed_tests for t in test_ev)
                result.test_pass_rate = round(passed / total, 4) if total else 0.0
                result.test_evidence_source = "static"

        # (c) 报告：仓内报告 + 离线语料
        report_ev: list[ReportEvidence] = []
        if cap.report_patterns:
            report_ev.extend(scan_reports_for_patterns(
                self.root, cap.report_patterns, exclude_prefixes=self.exclude_prefixes,
            ))
        for corpus in self.corpus_paths:
            report_ev.extend(scan_report_corpus(corpus, [cap.capability_id]))
        for ledger in self.adjudication_ledgers:
            report_ev.extend(scan_adjudication_ledger(ledger, [cap.capability_id]))
        report_ev.sort(key=lambda r: (as_of_sort_key(r.as_of), r.file_path))
        result.report_evidence = report_ev
        result.report_count = len(report_ev)
        result.has_reports = len(report_ev) > 0
        result.report_verdicts = [r.verdict for r in report_ev]

        latest = self._latest_adjudication(report_ev)
        if latest:
            result.adjudicated_level = latest.asserted_level or latest.verdict
            result.adjudicated_as_of = latest.as_of
            result.adjudicated_source = latest.file_path

        return result

    def _latest_adjudication(self, report_ev: list[ReportEvidence]) -> ReportEvidence | None:
        """取"最新"裁决：as_of 最大；同 as_of 时仓内 JSON 优先于离线语料。"""
        candidates = [r for r in report_ev if (r.asserted_level or r.verdict)]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda r: (as_of_sort_key(r.as_of), _SOURCE_PRIORITY.get(r.source_kind, 0), r.file_path),
        )

    def _execute_tests(self, cap: CapabilityDef, test_ev: list[TestEvidence]) -> list[TestEvidence]:
        """对**文件名匹配**的测试文件真实执行 pytest（有界、可缓存、如实降级）。"""
        name_matched = [t for t in test_ev if _filename_matches(t.file_path, cap.test_patterns)]
        ordered = sorted(name_matched, key=lambda t: t.file_path)
        for ev in ordered:
            if self._budget_left <= 0:
                self.exec_ledger.append({
                    "capability_id": cap.capability_id, "file": ev.file_path,
                    "source": "skipped", "reason": "exec budget exhausted",
                })
                continue
            cached = self.test_cache.get(ev.file_path)
            if cached and cached.get("file_sha256") == ev.file_sha256:
                counts = cached
            else:
                counts = run_test_file(
                    self.root, ev.file_path, timeout_s=self.exec_timeout_s, python=self.python,
                )
                if counts is None:
                    self.exec_ledger.append({
                        "capability_id": cap.capability_id, "file": ev.file_path,
                        "source": "static", "reason": "exec failed/timeout -> static fallback",
                    })
                    continue
                counts["file_sha256"] = ev.file_sha256
                self.test_cache[ev.file_path] = counts
            ran = counts["passed"] + counts["failed"] + counts["error"]
            ev.passed_tests = counts["passed"]
            ev.failed_tests = counts["failed"] + counts["error"]
            ev.total_tests = ran if ran else ev.total_tests
            ev.pass_rate = round(counts["passed"] / ran, 4) if ran else 0.0
            ev.source = "executed"
            ev.detail = f"pytest rc={counts.get('returncode')} skipped={counts.get('skipped', 0)}"
            self.exec_ledger.append({
                "capability_id": cap.capability_id, "file": ev.file_path, "source": "executed",
                "passed": counts["passed"], "failed": counts["failed"] + counts["error"],
                "returncode": counts.get("returncode"),
            })
        return test_ev

    def scan_all(self) -> dict[str, ScanResult]:
        results = {}
        for cap in self.registry.list_all():
            results[cap.capability_id] = self.scan_capability(cap)
        return results