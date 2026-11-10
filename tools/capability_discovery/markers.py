"""capability marker 解析器（AST 静态扫描）。

只做一件事：把测试文件里**书写形式**的 capability marker 解析成
:class:`~tools.capability_discovery.model.DiscoveryRecord`。不做领域判断，
不执行被测代码，不依赖任何域词条 —— 因此它是纯语法层的能力发现。

支持的书写格式（C1）
--------------------
1. ``function_kwargs``        ``@pytest.mark.capability(id="x.y", level="REAL", ...)``
2. ``function_positional_id`` ``@pytest.mark.capability("x.y", level="REAL")``
3. ``function_bare``          ``@pytest.mark.capability``（id 由测试节点名派生）
4. ``class_kwargs``           类级 marker，类内测试方法继承
5. ``module_pytestmark``      ``pytestmark = [pytest.mark.capability(...)]``
6. ``param_marks``            ``pytest.param(..., marks=pytest.mark.capability(...))``

另识别**结局修饰符** ``skip`` / ``skipif`` / ``xfail``（它们不改变能力身份，
只改变证据强度：被跳过或预期失败的测试不能把等级撑到 REAL）。

元数据必须是字面量（常量 / 列表 / 字典）。非字面量不会被猜测，而是记入
``diagnostics``，交由人审 —— 宁可漏报，不可臆造。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .model import (
    DiscoveryRecord,
    MARKER_FORMATS,
    SCOPE_CLASS,
    SCOPE_FUNCTION,
    SCOPE_MODULE,
    sha256_text,
)

CAPABILITY_MARKER = "capability"
MODIFIER_MARKERS = ("skip", "skipif", "xfail")
# marker 关键字别名（全部为通用词，无领域含义）
_ID_KEYS = ("id", "capability", "capability_id", "name")
_LABEL_KEYS = ("label", "description", "title")
_LEVEL_KEYS = ("level", "claim", "claimed_level")
_ANCHOR_KEYS = ("anchor", "anchors", "refs", "references")
_PARAM_KEYS = ("params", "param")
_TAG_KEYS = ("tags", "tag")

TEST_PREFIX = "test"
DEFAULT_GLOBS = ("test_*.py", "*_test.py")


class MarkerError(ValueError):
    """marker 元数据非法（非字面量 / 字段类型不符 / 缺 id）。"""


@dataclass
class Diagnostics:
    """扫描期诊断：不中断扫描，只记录可疑点供人审。"""

    entries: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, source_file: str, lineno: int, message: str, severity: str = "warning") -> None:
        self.entries.append(
            {"source_file": source_file, "lineno": lineno, "message": message, "severity": severity}
        )

    def to_list(self) -> List[Dict[str, Any]]:
        return list(self.entries)


# ── AST 小工具 ───────────────────────────────────────────────────────────────
def _dotted(node: ast.AST) -> List[str]:
    """把 ``a.b.c`` 形式的属性链拍平成 ['a','b','c']。"""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return parts


def marker_name(node: ast.AST) -> str:
    """若 ``node`` 形如 ``<pytest>.mark.<name>`` 或 ``mark.<name>``，返回 name。"""
    parts = _dotted(node)
    if len(parts) == 3 and parts[0] == "pytest" and parts[1] == "mark":
        return parts[2]
    if len(parts) == 2 and parts[0] == "mark":
        return parts[1]
    return ""


def _literal(node: ast.AST, where: str) -> Any:
    """取字面量；非字面量抛 MarkerError（拒绝臆造）。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_literal(e, where) for e in node.elts]
    if isinstance(node, ast.Dict):
        out: Dict[str, Any] = {}
        for k, v in zip(node.keys, node.values):
            if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                raise MarkerError(f"{where}: 字典键必须为字符串字面量")
            out[k.value] = _literal(v, where)
        return out
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = _literal(node.operand, where)
        if isinstance(val, (int, float)):
            return -val
    raise MarkerError(f"{where}: 元数据必须为字面量（常量/列表/字典），得到 {type(node).__name__}")


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _as_str_list(value: Any, where: str) -> List[str]:
    out: List[str] = []
    for item in _as_list(value):
        if not isinstance(item, str):
            raise MarkerError(f"{where}: 期望字符串列表，得到 {type(item).__name__}")
        if item:
            out.append(item)
    return out


def _as_params(value: Any, where: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MarkerError(f"{where}: params 必须为字典字面量")
    return dict(value)


def _apply_meta_dict(raw: Dict[str, Any], where: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "id" in raw or "capability_id" in raw:
        out["capability_id"] = str(raw.get("id") or raw.get("capability_id"))
    if "level" in raw:
        out["declared_level"] = str(raw["level"]).upper()
    if "label" in raw:
        out["label"] = str(raw["label"])
    if "anchors" in raw:
        out["anchors"] = _as_str_list(raw["anchors"], f"{where}:anchors")
    if "params" in raw:
        out["params"] = _as_params(raw["params"], f"{where}:params")
    if "tags" in raw:
        out["tags"] = _as_str_list(raw["tags"], f"{where}:tags")
    return out


def parse_capability_call(call: ast.Call, source_file: str, lineno: int, marker_format: str) -> Dict[str, Any]:
    """解析 ``pytest.mark.capability(...)`` 调用 → 元数据字典。"""
    where = f"{source_file}:{lineno}"
    meta: Dict[str, Any] = {
        "capability_id": "",
        "label": "",
        "declared_level": "",
        "anchors": [],
        "params": {},
        "tags": [],
        "extra": {},
        "marker_format": marker_format,
    }
    positional = list(call.args)
    if len(positional) > 1:
        raise MarkerError(f"{where}: capability 只接受 1 个位置参数（id 或字典）")
    if positional:
        first = positional[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            meta["capability_id"] = first.value.strip()
        elif isinstance(first, ast.Dict):
            merged = _literal(first, where)
            if not isinstance(merged, dict):
                raise MarkerError(f"{where}: 首参必须为字符串或字典")
            meta.update(_apply_meta_dict(merged, where))
        else:
            raise MarkerError(f"{where}: capability 首参必须为字符串字面量或字典")

    for kw in call.keywords:
        key = kw.arg
        if key is None:
            raise MarkerError(f"{where}: capability 不接受 **kwargs 展开")
        value = _literal(kw.value, f"{where}:{key}")
        if key in _ID_KEYS:
            if not isinstance(value, str) or not value.strip():
                raise MarkerError(f"{where}: {key} 必须为非空字符串")
            meta["capability_id"] = value.strip()
        elif key in _LABEL_KEYS:
            if not isinstance(value, str):
                raise MarkerError(f"{where}: {key} 必须为字符串")
            meta["label"] = value
        elif key in _LEVEL_KEYS:
            if not isinstance(value, str):
                raise MarkerError(f"{where}: {key} 必须为字符串")
            meta["declared_level"] = value.upper()
        elif key in _ANCHOR_KEYS:
            meta["anchors"] = _as_str_list(value, f"{where}:{key}")
        elif key in _PARAM_KEYS:
            meta["params"] = _as_params(value, f"{where}:{key}")
        elif key in _TAG_KEYS:
            meta["tags"] = _as_str_list(value, f"{where}:{key}")
        else:
            meta["extra"][key] = value
    return meta


def _decorator_markers(decorators: Sequence[ast.AST]) -> Tuple[List[ast.AST], List[str]]:
    """分离 capability marker 节点与结局修饰符名。"""
    caps: List[ast.AST] = []
    modifiers: List[str] = []
    for dec in decorators:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = marker_name(target)
        if name == CAPABILITY_MARKER:
            caps.append(dec)
        elif name in MODIFIER_MARKERS:
            modifiers.append(name)
    return caps, modifiers


def _param_marker_calls(decorators: Sequence[ast.AST]) -> List[Tuple[int, ast.AST]]:
    """从 ``@pytest.mark.parametrize(...)`` 里抠出 ``pytest.param(marks=...)``。

    返回 [(param 序号, capability marker 节点)]。
    """
    found: List[Tuple[int, ast.AST]] = []
    for dec in decorators:
        if not isinstance(dec, ast.Call):
            continue
        if marker_name(dec.func) != "parametrize":
            continue
        arglist = [a for a in dec.args if isinstance(a, (ast.List, ast.Tuple))]
        if not arglist:
            continue
        for idx, elt in enumerate(arglist[-1].elts):
            if not isinstance(elt, ast.Call):
                continue
            fname = _dotted(elt.func)
            # pytest.param(...) （也可能是 mark.param 别名导入）
            if not (len(fname) >= 2 and fname[-1] == "param" and fname[-2] in ("pytest", "mark")):
                continue
            for kw in elt.keywords:
                if kw.arg != "marks":
                    continue
                candidates = kw.value.elts if isinstance(kw.value, (ast.List, ast.Tuple)) else [kw.value]
                for cand in candidates:
                    cname = marker_name(cand.func if isinstance(cand, ast.Call) else cand)
                    if cname == CAPABILITY_MARKER:
                        found.append((idx, cand))
    return found


def derive_bare_id(nodeid: str) -> str:
    """裸 marker 的能力 id：由测试节点派生（可追溯、无领域猜测）。"""
    parts = [p for p in nodeid.split("::") if p]
    if not parts:
        return "derived.unnamed"
    file_stem = Path(parts[0]).stem
    tail = ".".join(parts[1:]) if len(parts) > 1 else file_stem
    return f"derived.{file_stem}.{tail}"


def _nodeid(rel_path: str, *parts: str) -> str:
    return "::".join([rel_path, *[p for p in parts if p]])


def _relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


class MarkerScanner:
    """AST 静态扫描器：测试源码 → DiscoveryRecord 列表。"""

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()
        self.diagnostics = Diagnostics()

    # -- 单文件 ---------------------------------------------------------------
    def scan_file(self, path: Path | str) -> List[DiscoveryRecord]:
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.diagnostics.add(str(path), 0, f"无法读取: {exc}", "error")
            return []
        rel = _relpath(path, self.root)
        file_sha = sha256_text(text)
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            self.diagnostics.add(rel, exc.lineno or 0, f"语法错误: {exc.msg}", "error")
            return []

        records: List[DiscoveryRecord] = []
        module_marks, module_mods = self._module_pytestmark(tree, rel, file_sha)

        for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if not fn.name.startswith(TEST_PREFIX):
                continue
            records.extend(
                self._from_function(fn, rel, file_sha, prefix=(), inherited=module_marks,
                                    inherited_mods=module_mods)
            )

        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            class_caps, class_mods = _decorator_markers(cls.decorator_list)
            class_marks = module_marks + self._parse_marks(
                class_caps, rel, file_sha, SCOPE_CLASS, cls.lineno
            )
            all_mods = list(dict.fromkeys([*module_mods, *class_mods]))
            for fn in [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                if not fn.name.startswith(TEST_PREFIX):
                    continue
                records.extend(
                    self._from_function(fn, rel, file_sha, prefix=(cls.name,),
                                        inherited=class_marks, inherited_mods=all_mods)
                )
        return records

    # -- 目录 ---------------------------------------------------------------
    def scan_paths(
        self, roots: Iterable[Path | str], globs: Sequence[str] = DEFAULT_GLOBS
    ) -> List[DiscoveryRecord]:
        files: List[Path] = []
        for root in roots:
            root = Path(root)
            if root.is_file():
                files.append(root)
                continue
            for pattern in globs:
                files.extend(sorted(root.rglob(pattern)))
        seen: set[Path] = set()
        records: List[DiscoveryRecord] = []
        for f in files:
            rp = f.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            records.extend(self.scan_file(rp))
        records.sort(key=lambda r: (r.capability_id, r.nodeid, r.lineno, r.marker_format))
        return records

    # -- 内部 ---------------------------------------------------------------
    def _module_pytestmark(
        self, tree: ast.Module, rel: str, file_sha: str
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        marks: List[Dict[str, Any]] = []
        mods: List[str] = []
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
                continue
            value = node.value
            elts = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
            for elt in elts:
                target = elt.func if isinstance(elt, ast.Call) else elt
                name = marker_name(target)
                if name == CAPABILITY_MARKER:
                    marks.extend(self._parse_marks([elt], rel, file_sha, SCOPE_MODULE, node.lineno))
                elif name in MODIFIER_MARKERS:
                    mods.append(name)
        return marks, mods

    def _parse_marks(
        self,
        nodes: Sequence[ast.AST],
        rel: str,
        file_sha: str,
        scope: str,
        lineno: int,
    ) -> List[Dict[str, Any]]:
        """把 marker 语法节点解析成元数据列表；错误进 diagnostics，不抛出。"""
        out: List[Dict[str, Any]] = []
        default_fmt = {SCOPE_MODULE: "module_pytestmark", SCOPE_CLASS: "class_kwargs"}.get(
            scope, "function_kwargs"
        )
        for node in nodes:
            fmt = default_fmt
            try:
                if isinstance(node, ast.Call):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        if scope == SCOPE_FUNCTION:
                            fmt = "function_positional_id"
                    meta = parse_capability_call(node, rel, lineno, fmt)
                else:
                    meta = {
                        "capability_id": "",
                        "label": "",
                        "declared_level": "",
                        "anchors": [],
                        "params": {},
                        "tags": [],
                        "extra": {},
                        "marker_format": "function_bare" if scope == SCOPE_FUNCTION else default_fmt,
                    }
                meta["_scope"] = scope
                out.append(meta)
            except MarkerError as exc:
                self.diagnostics.add(rel, lineno, str(exc), "error")
        return out

    def _from_function(
        self,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        rel: str,
        file_sha: str,
        prefix: Sequence[str],
        inherited: Sequence[Dict[str, Any]],
        inherited_mods: Sequence[str],
    ) -> List[DiscoveryRecord]:
        nodeid = _nodeid(rel, *prefix, fn.name)
        own_caps, own_mods = _decorator_markers(fn.decorator_list)
        own_marks = self._parse_marks(own_caps, rel, file_sha, SCOPE_FUNCTION, fn.lineno)
        param_marks: List[Dict[str, Any]] = []
        for idx, cand in _param_marker_calls(fn.decorator_list):
            for meta in self._parse_marks([cand], rel, file_sha, SCOPE_FUNCTION, fn.lineno):
                meta["extra"]["param_index"] = idx
                meta["marker_format"] = "param_marks"
                param_marks.append(meta)

        modifiers = list(dict.fromkeys([*inherited_mods, *own_mods]))
        records: List[DiscoveryRecord] = []
        for meta in [*own_marks, *param_marks, *inherited]:
            cap_id = meta.get("capability_id") or derive_bare_id(nodeid)
            fmt = meta.get("marker_format", "function_kwargs")
            records.append(
                DiscoveryRecord(
                    capability_id=cap_id,
                    nodeid=nodeid,
                    source_file=rel,
                    source_sha256=file_sha,
                    lineno=fn.lineno,
                    marker_format=fmt if fmt in MARKER_FORMATS else "function_kwargs",
                    scope=meta.get("_scope", SCOPE_FUNCTION),
                    label=meta.get("label", ""),
                    declared_level=meta.get("declared_level", ""),
                    anchors=list(meta.get("anchors") or []),
                    params=dict(meta.get("params") or {}),
                    tags=list(meta.get("tags") or []),
                    modifiers=modifiers,
                )
            )
        return records


def scan(roots: Iterable[Path | str], root: Path | str = ".") -> Tuple[List[DiscoveryRecord], List[Dict[str, Any]]]:
    """便捷入口：扫描若干路径，返回 (记录列表, 诊断列表)。"""
    scanner = MarkerScanner(root)
    records = scanner.scan_paths(roots)
    return records, scanner.diagnostics.to_list()