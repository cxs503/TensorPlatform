"""三源扫描器：代码（AST）、测试（AST）、文档（文本）。

每个扫描器只产出**结构信号 + 可复核证据**（文件、行号、片段、文件 sha256），
不做等级判定——判定集中在 :mod:`engine`，便于把规则写进单测。

零领域语义：这里的正则词表是**工程证据词表**（mock / stub / assert / tolerance /
误差 / 适用范围 …），用于识别「实现质量」与「文档元信息」，与任何物理学科无关。
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path
from typing import Any, Dict, List, Optional

from .resolver import file_sha256

# ── 阈值常量（随报告落盘，改动必须同步单测） ───────────────────────────────
THRESHOLDS = {
    "min_sloc_substantive": 6,         # 实质实现的最小有效代码行数
    "stub_ratio_stub": 0.5,            # 函数空壳占比 ≥ 该值 → STUB
    "constant_return_mock": 0.6,       # 常量直返函数占比 ≥ 该值 → MOCK 信号
    "marker_hits_mock": 2,             # 代码行上硬编码标记命中 ≥ 该值 → MOCK 信号
    "guards_per_function": 0.3,        # 边界检查密度阈值
    "skeleton_ratio_max": 0.5,         # 占位断言占比上限，超过则测试视为非实质
    "numeric_error_reject_pct": 15.0,  # 已验证工件判「不可接受」的误差阈值
    "numeric_error_pass_pct": 3.0,     # 已验证工件判「通过」的误差阈值
}

MOCK_MARKER_RE = re.compile(
    r"(?i)\b(mock|hard[\s_-]?coded|fake|dummy|placeholder|stub|todo|fixme|xxx)\b"
)
#: 只有这些词能作为「硬编码/假实现」的正面证据（TODO/FIXME 只是任务标记）。
HARDCODE_MARKER_RE = re.compile(r"(?i)\b(mock(?:ed|s)?|hard[\s_-]?coded|fake|dummy|placeholder)\b")
FAIL_WORD_RE = re.compile(r"(?i)(honest[\s_-]?fail|\bfail(?:ed|ure|s)?\b|not\s+pass|不通过|不可接受|未通过)")
ACCURACY_CLAIM_RE = re.compile(
    r"(?i)(误差|偏差|精度|准确度|error|deviation|accuracy|tolerance|rel\.?\s*err|max\.?\s*err|偏差率)[^。\n]{0,40}?(\d+(?:\.\d+)?)\s*(%|％)"
)
ERROR_PCT_RE = re.compile(
    r"(?i)(?:误差|偏差|deviation|error|rel\.?\s*err)[^。\n]{0,40}?(\d+(?:\.\d+)?)\s*(%|％)"
)
SCOPE_NOTE_RE = re.compile(
    r"(?i)(适用范围|适用域|局限|限制|仅适用|不适用|假设|前提|assumption|assumptions|"
    r"applies\s+to|valid\s+(?:for|range)|limitation|limitations|caveat)"
)
WEAK_GRADE_DECL_RE = re.compile(
    r"(?i)(?:判\s*(?:决|定)|grade|level|等级|结论)\s*[:：=为]?\s*\**\s*(NOT_SUPPORTED|STUB|MOCK|WITHHELD)"
)
GRADE_TOKEN_RE = re.compile(r"\b(NOT_SUPPORTED|WITHHELD|STUB|MOCK|REAL)\b")

NUMERIC_ASSERT_CALLS = {
    "approx",
    "assert_allclose",
    "assert_almost_equal",
    "isclose",
    "allclose",
    "assert_array_almost_equal",
}


def _non_code_lines(text: str) -> set:
    """返回「纯注释 / 纯字符串」（含 docstring）占据的行号集合。"""
    rows = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                rows.add(tok.start[0])
            elif tok.type == tokenize.STRING:
                rows.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError):
        return rows
    return rows


def _snippet(text: str, line: int, width: int = 160) -> str:
    lines = text.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:width]
    return ""


def _evidence(path: Path, rel: str, line: int, kind: str, snippet: str, sha: str) -> Dict[str, Any]:
    return {"path": rel, "line": int(line), "kind": kind, "snippet": snippet, "file_sha256": sha}


def _body(stmts: List[ast.stmt]) -> List[ast.stmt]:
    body = list(stmts)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def _is_stub_body(body: List[ast.stmt]) -> bool:
    if not body:
        return True
    if all(isinstance(s, ast.Pass) for s in body):
        return True
    if all(isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis for s in body):
        return True
    if len(body) == 1:
        s = body[0]
        if isinstance(s, ast.Raise):
            exc = s.exc
            name = ""
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name):
                name = exc.id
            elif isinstance(exc, ast.Attribute):
                name = exc.attr
            if name == "NotImplementedError":
                return True
        if isinstance(s, ast.Return):
            if s.value is None or (isinstance(s.value, ast.Constant) and s.value.value is None):
                return True
    return False


def _is_constant_return(body: List[ast.stmt]) -> bool:
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False

    def const(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(const(e) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(const(k) for k in node.keys if k is not None) and all(const(v) for v in node.values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return const(node.operand)
        return False

    return body[0].value is not None and const(body[0].value)


def _guard_signals(fn: ast.AST) -> int:
    """函数内的输入校验 / 边界检查信号计数（越界、None、NaN、断言、夹紧、抛错）。"""
    count = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.Raise):
            exc = node.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            name = exc.id if isinstance(exc, ast.Name) else (exc.attr if isinstance(exc, ast.Attribute) else "")
            if name in {"ValueError", "TypeError", "KeyError", "AssertionError", "IndexError", "ZeroDivisionError"}:
                count += 1
        elif isinstance(node, ast.Compare):
            operands = [node.left] + list(node.comparators)
            if any(isinstance(o, ast.Constant) and o.value is None for o in operands):
                count += 1
            elif len(operands) == 2 and isinstance(operands[1], ast.Constant) and isinstance(operands[1].value, (int, float)):
                count += 1
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if name in {"isnan", "isinf", "isfinite", "clip", "min", "max"}:
                count += 1
    return count


def _random_calls(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                if fn.value.id in {"random", "np", "numpy"} and fn.attr in {
                    "random", "uniform", "randint", "randn", "rand", "normal", "choice", "gauss", "random_sample", "permutation",
                }:
                    count += 1
            elif isinstance(fn, ast.Name) and fn.id in {"random", "uniform", "randint", "gauss"}:
                count += 1
    return count


def _has_seed(text: str) -> bool:
    return bool(re.search(r"(?i)(\.seed\(|default_rng\(|RandomState\(|set_seed\()", text))


def _random_calls_safe(text: str) -> int:
    try:
        return _random_calls(ast.parse(text))
    except SyntaxError:
        return 0


def scan_code(root: Path, rels: List[str]) -> Dict[str, Any]:
    """代码源扫描：存在性 / 实质实现量 / 空壳 / 硬编码·随机 / 输入校验。

    MOCK 信号按**文件级**判定（避免整仓里任意两处 "mock" 字样就判 MOCK）：
      * ``hard_marker``：同一文件内**代码行**上的 mock/hardcoded/fake/dummy/
        placeholder 标记 ≥2 处，或 ≥1 处且该文件常量直返函数占比 ≥0.5；
      * ``unseeded_random``：文件内有 random.* 调用且整文件无播种/显式 RNG。
    docstring 与注释里的 "placeholder" 是说明文字，不算硬编码证据。
    """
    files, evidence = [], []
    sloc = n_funcs = n_public = stub_funcs = const_returns = guards = markers = 0
    rand_calls = parse_errors = 0
    file_flags: List[Dict[str, Any]] = []
    for rel in rels:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        sha = file_sha256(path)
        files.append({"path": rel, "sha256": sha, "bytes": len(text.encode("utf-8"))})
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                sloc += 1
        f_markers = 0
        non_code = _non_code_lines(text)
        for m in HARDCODE_MARKER_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            if line in non_code:
                continue
            f_markers += 1
            if len(evidence) < 30:
                evidence.append(_evidence(path, rel, line, "code_marker", _snippet(text, line), sha))
        markers += f_markers
        f_rand = _random_calls_safe(text)
        seeded = _has_seed(text)
        if f_rand and not seeded:
            rand_calls += f_rand
            m = re.search(r"(?i)\b(random\.|np\.random\.|numpy\.random\.)", text)
            line = text.count("\n", 0, m.start()) + 1 if m else 1
            if len(evidence) < 30:
                evidence.append(_evidence(path, rel, line, "unseeded_random", _snippet(text, line), sha))
        f_funcs = f_stub = f_const = f_guards = 0
        try:
            tree = ast.parse(text)
        except SyntaxError:
            parse_errors += 1
            file_flags.append({"path": rel, "parse_error": True})
            continue
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            f_funcs += 1
            n_public += 0 if fn.name.startswith("_") else 1
            body = _body(fn.body)
            if _is_stub_body(body):
                f_stub += 1
                if len(evidence) < 30:
                    evidence.append(_evidence(path, rel, fn.lineno, "stub_body", f"def {fn.name}(...) -> placeholder", sha))
            elif _is_constant_return(body):
                f_const += 1
                if len(evidence) < 30:
                    evidence.append(_evidence(path, rel, fn.lineno, "constant_return", f"def {fn.name}(...) -> literal", sha))
            f_guards += _guard_signals(fn)
        n_funcs += f_funcs
        stub_funcs += f_stub
        const_returns += f_const
        guards += f_guards
        const_ratio_file = (f_const / f_funcs) if f_funcs else 0.0
        hard_marker = f_markers >= THRESHOLDS["marker_hits_mock"] or (
            f_markers >= 1 and const_ratio_file >= 0.5
        )
        file_flags.append(
            {
                "path": rel,
                "functions": f_funcs,
                "stub_functions": f_stub,
                "constant_return_functions": f_const,
                "markers": f_markers,
                "seeded": seeded,
                "random_calls": f_rand,
                "hard_marker": hard_marker,
                "unseeded_random": bool(f_rand and not seeded),
                "stub_ratio": round((f_stub / f_funcs) if f_funcs else 0.0, 4),
            }
        )
    stub_ratio = (stub_funcs / n_funcs) if n_funcs else (1.0 if not rels else 0.0)
    const_ratio = (const_returns / n_funcs) if n_funcs else 0.0
    guard_density = (guards / n_funcs) if n_funcs else 0.0
    mock_files = [f["path"] for f in file_flags if f.get("hard_marker") or f.get("unseeded_random")]
    mock_signal = bool(mock_files) or (n_funcs >= 3 and const_ratio >= THRESHOLDS["constant_return_mock"])
    return {
        "exists": bool(rels),
        "files": files,
        "file_count": len(files),
        "sloc": sloc,
        "functions": n_funcs,
        "public_functions": n_public,
        "stub_functions": stub_funcs,
        "constant_return_functions": const_returns,
        "marker_hits": markers,
        "random_calls": rand_calls,
        "stub_ratio": round(stub_ratio, 4),
        "constant_return_ratio": round(const_ratio, 4),
        "guard_signals": guards,
        "guard_density": round(guard_density, 4),
        "has_input_validation": guards >= 1 and guard_density >= THRESHOLDS["guards_per_function"],
        "mock_signal": mock_signal,
        "mock_files": mock_files,
        "parse_errors": parse_errors,
        "substantive": bool(files) and stub_ratio < THRESHOLDS["stub_ratio_stub"] and sloc >= THRESHOLDS["min_sloc_substantive"],
        "file_flags": file_flags,
        "evidence": evidence,
    }


def scan_tests(root: Path, rels: List[str], junit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """测试源扫描：存在性 / 断言密度 / 数值断言 / 解析锚 / 占位断言。"""
    files, evidence = [], []
    n_tests = asserts = numeric_asserts = trivial = anchors = parse_errors = 0
    for rel in rels:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        sha = file_sha256(path)
        files.append({"path": rel, "sha256": sha, "bytes": len(text.encode("utf-8"))})
        for m in re.finditer(r"(?i)\b(expected|reference|anchor|analytic|baseline|tolerance|rel_tol|abs_tol|benchmark)\b", text):
            anchors += 1
            if anchors <= 10:
                line = text.count("\n", 0, m.start()) + 1
                evidence.append(_evidence(path, rel, line, "test_anchor", _snippet(text, line), sha))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            parse_errors += 1
            continue
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]:
            n_tests += 1
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assert):
                    continue
                asserts += 1
                test_expr = node.test
                if isinstance(test_expr, ast.Constant) and test_expr.value is True:
                    trivial += 1
                    continue
                # 数值断言 = 比较式里出现数值常量，或一侧是算术/函数调用
                # （``abs(v - ref) < tol``、``sqrt(x) == approx(...)`` 都算）。
                has_compare_const = any(
                    isinstance(n2, ast.Compare)
                    and any(
                        (isinstance(o, ast.Constant) and isinstance(o.value, (int, float)))
                        or isinstance(o, (ast.Call, ast.BinOp))
                        for o in [n2.left] + list(n2.comparators)
                    )
                    for n2 in ast.walk(test_expr)
                )
                has_tol_call = any(
                    isinstance(n2, ast.Call)
                    and ((isinstance(n2.func, ast.Attribute) and n2.func.attr in NUMERIC_ASSERT_CALLS)
                         or (isinstance(n2.func, ast.Name) and n2.func.id in NUMERIC_ASSERT_CALLS))
                    for n2 in ast.walk(test_expr)
                )
                has_tol_kw = any(
                    kw.arg in {"rel", "abs", "atol", "rtol", "delta", "places"}
                    for n2 in ast.walk(test_expr)
                    if isinstance(n2, ast.Call)
                    for kw in n2.keywords
                )
                if has_compare_const or has_tol_call or has_tol_kw:
                    numeric_asserts += 1
    skeleton_ratio = (trivial / n_tests) if n_tests else 0.0
    junit_status = None
    if junit:
        tests_run = int(junit.get("tests", 0))
        fails = int(junit.get("failures", 0)) + int(junit.get("errors", 0))
        junit_status = {"tests": tests_run, "failures": fails, "passed": tests_run > 0 and fails == 0}
    numeric_ok = numeric_asserts >= 1
    meaningful = n_tests >= 1 and skeleton_ratio < THRESHOLDS["skeleton_ratio_max"] and numeric_ok
    if junit_status is not None and junit_status["passed"] is False:
        meaningful = False
    return {
        "exists": bool(rels),
        "files": files,
        "file_count": len(files),
        "test_functions": n_tests,
        "assertions": asserts,
        "numeric_assertions": numeric_asserts,
        "trivial_assertions": trivial,
        "skeleton_ratio": round(skeleton_ratio, 4),
        "anchor_hits": anchors,
        "has_numeric_assertions": numeric_ok,
        "meaningful": meaningful,
        "junit": junit_status,
        "parse_errors": parse_errors,
        "evidence": evidence,
    }


def scan_docs(root: Path, rels: List[str], cap_id: str, verification_rels: Optional[List[str]] = None) -> Dict[str, Any]:
    """文档源扫描：是否提到该能力 / 精度声明 / 适用范围 / 弱等级自述 / 失败语义。

    ``verdict_fail`` 只由**验证/审计工件**（``verification_rels``）里的失败语义触发：
    一般文档出现 "fail" 字样不构成否决证据。
    """
    files, evidence = [], []
    mentions = accuracy_claims = scope_notes = fail_words = weak_decl = 0
    min_error_pct: Optional[float] = None
    verify_min_error: Optional[float] = None
    verify_fail_words = 0
    verify_weak_decl = 0
    vset = set(verification_rels or [])
    for rel in rels:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        sha = file_sha256(path)
        files.append({"path": rel, "sha256": sha, "bytes": len(text.encode("utf-8"))})
        low = text.lower()
        if cap_id.lower() in low:
            mentions += 1
            idx = low.index(cap_id.lower())
            ln = text.count("\n", 0, idx) + 1
            evidence.append(_evidence(path, rel, ln, "doc_mention", _snippet(text, ln), sha))
        for m in ACCURACY_CLAIM_RE.finditer(text):
            accuracy_claims += 1
            val = float(m.group(2))
            min_error_pct = val if min_error_pct is None else min(min_error_pct, val)
            if accuracy_claims <= 10:
                line = text.count("\n", 0, m.start()) + 1
                evidence.append(_evidence(path, rel, line, "doc_accuracy_claim", _snippet(text, line), sha))
        for m in ERROR_PCT_RE.finditer(text):
            val = float(m.group(1))
            min_error_pct = val if min_error_pct is None else min(min_error_pct, val)
            if rel in vset:
                verify_min_error = val if verify_min_error is None else min(verify_min_error, val)
        for m in SCOPE_NOTE_RE.finditer(text):
            scope_notes += 1
            if scope_notes <= 5:
                line = text.count("\n", 0, m.start()) + 1
                evidence.append(_evidence(path, rel, line, "doc_scope_note", _snippet(text, line), sha))
        fail_words += len(FAIL_WORD_RE.findall(text))
        if rel in vset:
            verify_fail_words += len(FAIL_WORD_RE.findall(text))
            verify_weak_decl += len(WEAK_GRADE_DECL_RE.findall(text))
        for m in WEAK_GRADE_DECL_RE.finditer(text):
            weak_decl += 1
            line = text.count("\n", 0, m.start()) + 1
            if len(evidence) < 60:
                evidence.append(_evidence(path, rel, line, "doc_weak_grade_declaration", _snippet(text, line), sha))
    # 只有「验证/审计工件」里的失败语义才可否决 REAL（一般文档里的失败词不算）。
    verdict_fail = bool(
        (verify_min_error is not None and verify_min_error > THRESHOLDS["numeric_error_reject_pct"])
        and verify_fail_words > 0
    ) or verify_weak_decl > 0
    return {
        "exists": bool(rels),
        "files": files,
        "file_count": len(files),
        "mentions_capability": mentions > 0,
        "mention_count": mentions,
        "accuracy_claims": accuracy_claims,
        "min_error_pct": min_error_pct,
        "scope_notes": scope_notes,
        "fail_word_hits": fail_words,
        "weak_grade_declarations": weak_decl,
        "has_accuracy_claim": accuracy_claims > 0,
        "has_scope_note": scope_notes > 0,
        "verdict_fail": verdict_fail,
        "verification_min_error_pct": verify_min_error,
        "verification_fail_words": verify_fail_words,
        "verification_weak_decl": verify_weak_decl,
        "evidence": evidence[:60],
    }