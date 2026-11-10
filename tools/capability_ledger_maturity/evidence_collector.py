"""证据采集器：扫描 pytest/benchmark/对标报告，提取关键量与误差。

设计：
- 从 pytest 输出提取数值断言（assert x == y, np.allclose, pytest.approx）
- 从 benchmark JSON 提取误差百分比
- 从对标报告 markdown 提取裁决等级与关键数字
- 输出标准化的 EvidenceItem 列表，供裁定引擎消费

零领域语义：不硬编码任何船舶域词汇；域名/能力名通过配置注入。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 数据模型 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceItem:
    """一条标准化证据项。"""
    capability_id: str
    source_type: str  # "pytest" | "benchmark" | "report" | "contract"
    source_path: str
    metric_name: str
    metric_value: float
    reference_value: Optional[float]
    error_pct: Optional[float]  # 误差百分比
    tolerance_pct: Optional[float]  # 预声明容差
    passed: bool  # 是否通过容差门
    grade_hint: str  # 从证据推断的等级提示
    sha256: str  # 证据内容指纹
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "reference_value": self.reference_value,
            "error_pct": self.error_pct,
            "tolerance_pct": self.tolerance_pct,
            "passed": self.passed,
            "grade_hint": self.grade_hint,
            "sha256": self.sha256,
            "timestamp": self.timestamp,
        }


# ── 模式定义 ──────────────────────────────────────────────────────────────

# pytest 数值断言模式
PYTEST_ASSERT_PATTERNS = [
    # pytest.approx(value, rel=...)
    re.compile(r'pytest\.approx\(\s*([\d.eE+-]+)\s*(?:,\s*rel\s*=\s*([\d.eE+-]+))?\s*\)'),
    # np.allclose(a, b, rtol=...)
    re.compile(r'(?:np|numpy)\.allclose\([^)]*rtol\s*=\s*([\d.eE+-]+)'),
    # assert abs(a - b) / b < tol
    re.compile(r'assert\s+abs\([^)]+\)\s*/\s*[^<]+\s*<\s*([\d.eE+-]+)'),
    # assert error < X%
    re.compile(r'assert\s+(?:error|err|偏差|误差)\s*<\s*([\d.eE+-]+)\s*%?'),
]

# benchmark JSON 模式
BENCHMARK_ERROR_PATTERNS = [
    re.compile(r'"(?:error|error_pct|偏差|误差)[^"]*"\s*:\s*([\d.eE+-]+)'),
    re.compile(r'"(?:rtol|tol|tolerance)[^"]*"\s*:\s*([\d.eE+-]+)'),
]

# 对标报告 markdown 模式（支持跨行匹配，允许关键词带 ** 标记）
REPORT_GRADE_PATTERNS = [
    re.compile(r'\*{0,2}(?:证据等级|evidence.?level|grade)\*{0,2}\s*[:：]?\s*\*\*(REAL|MOCK|STUB|WITHHELD|NOT_SUPPORTED)\*\*', re.I | re.DOTALL),
    re.compile(r'\*{0,2}(?:裁决|adjudication|判决)\*{0,2}\s*[:：]?\s*\*\*(REAL|MOCK|STUB|WITHHELD|NOT_SUPPORTED)\*\*', re.I | re.DOTALL),
]

REPORT_ERROR_PATTERNS = [
    re.compile(r'(?:误差|error|偏差|deviation)\s*[:：]?\s*([\d.eE+-]+)\s*%'),
    re.compile(r'([+-]?\d+(?:\.\d+)?)\s*%\s*(?:误差|error)'),
]

# 能力 ID 提取模式（从路径或内容中提取）
CAPABILITY_ID_PATTERNS = [
    re.compile(r'(?:capability|cap)[_.]id["\s:=]+["\']?([a-z][a-z0-9_.]+)["\']?', re.I),
    re.compile(r'"capability_id"\s*:\s*"([a-z][a-z0-9_.]+)"', re.I),
]


# ── 采集器 ────────────────────────────────────────────────────────────────

class EvidenceCollector:
    """证据采集器：从多种来源提取标准化证据项。

    Parameters
    ----------
    worktree : Path
        工作树根目录。
    capability_map : dict, optional
        能力 ID → 搜索关键词映射。若为 None，使用空映射（仅靠模式匹配）。
    """

    def __init__(self, worktree: Optional[Path] = None,
                 capability_map: Optional[Dict[str, List[str]]] = None):
        self.worktree = Path(worktree) if worktree else Path(__file__).resolve().parents[2]
        self.capability_map = capability_map or {}
        self._collected: List[EvidenceItem] = []

    def collect_all(self) -> List[EvidenceItem]:
        """从所有已知来源采集证据。"""
        self._collected = []
        self._collect_pytest()
        self._collect_benchmarks()
        self._collect_reports()
        self._collect_contracts()
        return self._collected

    def collect_from_path(self, path: Path, source_type: str) -> List[EvidenceItem]:
        """从指定路径采集证据（按 source_type 选择解析策略）。"""
        items = []
        if not path.exists():
            return items
        content = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(self.worktree)) if path.is_relative_to(self.worktree) else str(path)

        if source_type == "pytest":
            items = self._parse_pytest_content(content, rel)
        elif source_type == "benchmark":
            items = self._parse_benchmark_content(content, rel)
        elif source_type == "report":
            items = self._parse_report_content(content, rel)
        elif source_type == "contract":
            items = self._parse_contract_content(content, rel)

        self._collected.extend(items)
        return items

    # ── pytest 采集 ────────────────────────────────────────────────────

    def _collect_pytest(self):
        """扫描 tests/ 目录下的 pytest 文件。"""
        test_dirs = [self.worktree / "tests"]
        for td in test_dirs:
            if not td.is_dir():
                continue
            for f in td.rglob("test_*.py"):
                items = self._parse_pytest_file(f)
                self._collected.extend(items)

    def _parse_pytest_file(self, path: Path) -> List[EvidenceItem]:
        """解析单个 pytest 文件，提取数值断言证据。"""
        items = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return items
        rel = str(path.relative_to(self.worktree))
        items = self._parse_pytest_content(content, rel)
        return items

    def _parse_pytest_content(self, content: str, source_path: str) -> List[EvidenceItem]:
        """从 pytest 内容提取证据项。"""
        items = []
        cap_id = self._extract_capability_id(content, source_path)

        for pat in PYTEST_ASSERT_PATTERNS:
            for m in pat.finditer(content):
                try:
                    tol = float(m.group(1))
                    tol_pct = tol * 100 if tol < 1 else tol
                    # 从断言上下文推断 metric
                    line_start = content.rfind("\n", 0, m.start()) + 1
                    line_end = content.find("\n", m.end())
                    line = content[line_start:line_end].strip()
                    metric_name = self._infer_metric_name(line)

                    item = EvidenceItem(
                        capability_id=cap_id or "unknown",
                        source_type="pytest",
                        source_path=source_path,
                        metric_name=metric_name,
                        metric_value=0.0,  # pytest 断言通常不直接给出值
                        reference_value=None,
                        error_pct=None,
                        tolerance_pct=tol_pct,
                        passed=True,  # 测试通过意味着断言成立
                        grade_hint="REAL",
                        sha256=self._sha256(content),
                    )
                    items.append(item)
                except (ValueError, IndexError):
                    continue
        return items

    # ── benchmark 采集 ─────────────────────────────────────────────────

    def _collect_benchmarks(self):
        """扫描 benchmarks/ 和 artifacts/ 下的 benchmark JSON。"""
        for d in [self.worktree / "artifacts", self.worktree / "runs"]:
            if not d.is_dir():
                continue
            for f in d.rglob("*.json"):
                if "benchmark" in f.name.lower() or "verif" in f.name.lower():
                    items = self._parse_benchmark_file(f)
                    self._collected.extend(items)

    def _parse_benchmark_file(self, path: Path) -> List[EvidenceItem]:
        """解析 benchmark JSON 文件。"""
        items = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return items
        rel = str(path.relative_to(self.worktree)) if path.is_relative_to(self.worktree) else str(path)
        items = self._parse_benchmark_content(json.dumps(data), rel, data)
        return items

    def _parse_benchmark_content(self, content: str, source_path: str,
                                  data: Optional[dict] = None) -> List[EvidenceItem]:
        """从 benchmark 内容提取证据项。"""
        items = []
        if data is None:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return items

        if not isinstance(data, dict):
            # 顶层可能是 list/标量（批量结果文件很常见）：直接递归提取，
            # 具体 capability_id 由元素内字段决定。
            self._extract_benchmark_errors(data, "unknown", source_path, items)
            return items

        cap_id = data.get("capability_id") or self._extract_capability_id(content, source_path)

        # 递归查找误差值
        self._extract_benchmark_errors(data, cap_id or "unknown", source_path, items)
        return items

    def _extract_benchmark_errors(self, obj: Any, cap_id: str, source_path: str,
                                   items: List[EvidenceItem]):
        """递归提取 benchmark 数据中的误差信息。"""
        if isinstance(obj, dict):
            error_val = None
            ref_val = None
            metric_val = None
            tol_val = None
            metric_name = "unknown"

            for key, val in obj.items():
                key_lower = key.lower()
                if any(k in key_lower for k in ["error", "偏差", "误差"]):
                    if isinstance(val, (int, float)):
                        error_val = float(val)
                        metric_name = key
                elif any(k in key_lower for k in ["reference", "expected", "analytic", "基准"]):
                    if isinstance(val, (int, float)):
                        ref_val = float(val)
                elif any(k in key_lower for k in ["value", "computed", "actual", "计算"]):
                    if isinstance(val, (int, float)):
                        metric_val = float(val)
                elif any(k in key_lower for k in ["tol", "tolerance", "容差"]):
                    if isinstance(val, (int, float)):
                        tol_val = float(val) * 100 if float(val) < 1 else float(val)
                elif key == "capability_id" or key == "cap_id":
                    cap_id = str(val)
                elif isinstance(val, (dict, list)):
                    self._extract_benchmark_errors(val, cap_id, source_path, items)

            if error_val is not None or (metric_val is not None and ref_val is not None):
                if error_val is None and ref_val != 0:
                    error_val = abs(metric_val - ref_val) / abs(ref_val) * 100
                elif error_val is not None and ref_val is None:
                    error_val = error_val if error_val < 100 else error_val

                passed = error_val <= (tol_val or 15.0) if error_val is not None else False
                grade = "REAL" if passed else "MOCK" if error_val is not None and error_val > 15 else "WITHHELD"

                item = EvidenceItem(
                    capability_id=cap_id,
                    source_type="benchmark",
                    source_path=source_path,
                    metric_name=metric_name,
                    metric_value=metric_val or 0.0,
                    reference_value=ref_val,
                    error_pct=error_val,
                    tolerance_pct=tol_val,
                    passed=passed,
                    grade_hint=grade,
                    sha256=self._sha256(json.dumps(obj, sort_keys=True)),
                )
                items.append(item)

        elif isinstance(obj, list):
            for elem in obj:
                self._extract_benchmark_errors(elem, cap_id, source_path, items)

    # ── 报告采集 ───────────────────────────────────────────────────────

    def _collect_reports(self):
        """扫描 docs/ 下的验证/对标报告。"""
        for d in [self.worktree / "docs", self.worktree / "artifacts"]:
            if not d.is_dir():
                continue
            for f in d.rglob("*.md"):
                if any(k in f.name.lower() for k in ["verif", "audit", "report", "对标", "裁决"]):
                    items = self._parse_report_file(f)
                    self._collected.extend(items)

    def _parse_report_file(self, path: Path) -> List[EvidenceItem]:
        """解析 markdown 报告文件。"""
        items = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return items
        rel = str(path.relative_to(self.worktree)) if path.is_relative_to(self.worktree) else str(path)
        items = self._parse_report_content(content, rel)
        return items

    def _parse_report_content(self, content: str, source_path: str) -> List[EvidenceItem]:
        """从报告内容提取证据项。"""
        items = []
        cap_id = self._extract_capability_id(content, source_path)

        # 提取等级裁决
        for pat in REPORT_GRADE_PATTERNS:
            for m in pat.finditer(content):
                grade = m.group(1).upper()
                item = EvidenceItem(
                    capability_id=cap_id or "unknown",
                    source_type="report",
                    source_path=source_path,
                    metric_name="evidence_grade",
                    metric_value=0.0,
                    reference_value=None,
                    error_pct=None,
                    tolerance_pct=None,
                    passed=True,
                    grade_hint=grade,
                    sha256=self._sha256(content),
                )
                items.append(item)

        # 提取误差数字
        for pat in REPORT_ERROR_PATTERNS:
            for m in pat.finditer(content):
                try:
                    error_val = float(m.group(1))
                    # 找上下文中的能力 ID
                    ctx_start = max(0, m.start() - 200)
                    ctx = content[ctx_start:m.start()]
                    ctx_cap = self._extract_capability_id(ctx, source_path)

                    item = EvidenceItem(
                        capability_id=ctx_cap or cap_id or "unknown",
                        source_type="report",
                        source_path=source_path,
                        metric_name="reported_error",
                        metric_value=error_val,
                        reference_value=None,
                        error_pct=error_val,
                        tolerance_pct=15.0,
                        passed=error_val <= 15.0,
                        grade_hint="REAL" if error_val <= 3.0 else ("WITHHELD" if error_val <= 15.0 else "MOCK"),
                        sha256=self._sha256(content),
                    )
                    items.append(item)
                except ValueError:
                    continue

        return items

    # ── 合约采集 ───────────────────────────────────────────────────────

    def _collect_contracts(self):
        """扫描 evidence contract JSON 文件。"""
        for d in [self.worktree / "artifacts", self.worktree / "tests"]:
            if not d.is_dir():
                continue
            for f in d.rglob("contract.json"):
                items = self._parse_contract_file(f)
                self._collected.extend(items)

    def _parse_contract_file(self, path: Path) -> List[EvidenceItem]:
        """解析 evidence contract JSON。"""
        items = []
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
        except (OSError, json.JSONDecodeError):
            return items
        rel = str(path.relative_to(self.worktree)) if path.is_relative_to(self.worktree) else str(path)
        items = self._parse_contract_content(content, rel, data)
        return items

    def _parse_contract_content(self, content: str, source_path: str,
                                 data: Optional[dict] = None) -> List[EvidenceItem]:
        """从 contract 内容提取证据项。"""
        items = []
        if data is None:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return items

        cap_id = data.get("capability_id", "unknown")
        claim = data.get("claim", {})
        artifacts = data.get("artifacts", [])

        # 从 claim 提取容差
        tolerance = claim.get("tolerance_pct") or claim.get("tolerance")
        if tolerance is None:
            tol_obj = claim.get("tolerance", {})
            if isinstance(tol_obj, dict):
                tolerance = tol_obj.get("rel") or tol_obj.get("pct")

        # 从 artifacts 提取数值
        for art in artifacts:
            if not isinstance(art, dict):
                continue
            kind = art.get("kind", "")
            value = art.get("value")
            if value is not None and isinstance(value, (int, float)):
                item = EvidenceItem(
                    capability_id=cap_id,
                    source_type="contract",
                    source_path=source_path,
                    metric_name=f"artifact_{kind}",
                    metric_value=float(value),
                    reference_value=None,
                    error_pct=None,
                    tolerance_pct=float(tolerance) if tolerance else None,
                    passed=True,
                    grade_hint="REAL" if kind in ("anchor", "independent_path") else "WITHHELD",
                    sha256=self._sha256(content),
                )
                items.append(item)

        return items

    # ── 辅助方法 ───────────────────────────────────────────────────────

    def _extract_capability_id(self, content: str, source_path: str) -> Optional[str]:
        """从内容或路径中提取能力 ID。"""
        for pat in CAPABILITY_ID_PATTERNS:
            m = pat.search(content)
            if m:
                return m.group(1)

        # 从路径推断
        for cap_id, keywords in self.capability_map.items():
            for kw in keywords:
                if kw.lower() in source_path.lower():
                    return cap_id
        return None

    def _infer_metric_name(self, line: str) -> str:
        """从断言行推断指标名。"""
        # 简单启发式：取第一个有意义的标识符
        tokens = re.findall(r'[a-zA-Z_]\w*', line)
        for t in tokens:
            if t not in ("assert", "abs", "pytest", "approx", "np", "numpy", "allclose", "def", "return"):
                return t
        return "unknown_metric"

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def summary(self) -> Dict[str, Any]:
        """采集摘要。"""
        by_cap: Dict[str, List[EvidenceItem]] = {}
        for item in self._collected:
            by_cap.setdefault(item.capability_id, []).append(item)

        return {
            "total_items": len(self._collected),
            "by_capability": {
                cap: {
                    "count": len(items),
                    "source_types": list(set(i.source_type for i in items)),
                    "grade_hints": list(set(i.grade_hint for i in items)),
                    "passed_count": sum(1 for i in items if i.passed),
                }
                for cap, items in by_cap.items()
            },
            "by_source_type": {
                st: sum(1 for i in self._collected if i.source_type == st)
                for st in ("pytest", "benchmark", "report", "contract")
            },
        }
