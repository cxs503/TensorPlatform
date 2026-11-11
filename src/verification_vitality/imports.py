"""AST 静态导入解析：检出验证面因符号迁移而静默失效。

设计取向（决定误报率）：

* **只判本地模块**。导入目标的第一段若不属于本仓库的本地包/模块（例如 ``numpy``、
  ``pytest``），一律视为外部依赖、不判。这样装没装依赖不会污染活性结论。
* **可选依赖不算死**。写在 ``try: ... except ImportError`` 里的导入是"有则用之"的
  常见写法，缺模块不代表验证面失效，记 ``guarded=True`` 且不参与 dead 判定。
* **``TYPE_CHECKING`` 块内导入**同样不参与 dead 判定（仅类型标注用），但记录。
* **符号级检查才是符号迁移的指纹**：``from pkg.mod import old_name`` 里模块在、符号
  不在，正是"符号搬家/改名"的形态；只查模块会漏掉这一类。

返回值全部是数据结构，不做任何 I/O 副作用。
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

DEAD_KINDS = ("module_missing", "symbol_missing", "relative_unresolved")


@dataclass
class ImportFinding:
    """一条静态导入发现。``kind`` 为 DEAD_KINDS 之一时该导入无法在仓库内解析。"""

    file: str
    lineno: int
    kind: str
    module: str
    name: Optional[str] = None
    guarded: bool = False
    type_checking: bool = False
    detail: str = ""

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)

    @property
    def lethal(self) -> bool:
        """是否应判该验证面 dead。可选导入（try/except ImportError）与类型导入不算。"""
        return self.kind in DEAD_KINDS and not (self.guarded or self.type_checking)


# ---------------------------------------------------------------------------
# 本地模块索引
# ---------------------------------------------------------------------------

DEFAULT_EXTRA_ROOTS = ("src", "backend", "tensorphysics", "domains", "ops")


def local_roots(repo_root: Path, extra: Iterable[str | Path] = ()) -> List[Path]:
    """候选 sys.path 根：仓库根 + 既有 src/backend 布局 + 调用方补充的根。"""
    repo_root = Path(repo_root)
    roots: List[Path] = []
    for cand in [repo_root, *[repo_root / p for p in DEFAULT_EXTRA_ROOTS], *[Path(p) for p in extra]]:
        if cand.is_dir() and cand not in roots:
            roots.append(cand)
    return roots


def _top_level_index(roots: Sequence[Path]) -> Dict[str, List[Path]]:
    """``{顶层包名: [可导入它的根]}``——用来区分本地模块与外部依赖。"""
    index: Dict[str, List[Path]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            name = None
            if entry.is_dir() and (entry / "__init__.py").exists():
                name = entry.name
            elif entry.is_file() and entry.suffix == ".py" and entry.name != "__init__.py":
                name = entry.stem
            if name and name.isidentifier():
                index.setdefault(name, [])
                if root not in index[name]:
                    index[name].append(root)
    return index


def resolve_module(module: str, roots: Sequence[Path]) -> Optional[Path]:
    """把点分模块名解析到仓库内的文件；不可解析返回 ``None``。"""
    if not module:
        return None
    parts = [p for p in module.split(".") if p]
    if not parts:
        return None
    for root in roots:
        base = root.joinpath(*parts)
        py = base.with_suffix(".py")
        if py.is_file():
            return py
        init = base / "__init__.py"
        if init.is_file():
            return init
    return None


def _is_submodule(mod_path: Path, name: str) -> bool:
    """``from pkg import submod`` 里的名字可能是**子模块**而非包内符号。

    不判这一条会误报真仓库里的 ``from pkg import ac_verify``（ac_verify 是
    ``pkg/ac_verify.py``，`__init__.py` 里当然没有同名符号）。
    """
    if mod_path.name != "__init__.py":
        return False
    pkg_dir = mod_path.parent
    return ((pkg_dir / name).with_suffix(".py").is_file()
            or (pkg_dir / name / "__init__.py").is_file())


def _module_defined_names(tree: ast.Module) -> Tuple[Set[str], bool]:
    """模块顶层可被 ``from mod import X`` 拿到的名字；第二返回值=存在星导入。"""
    names: Set[str] = set()
    star = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
                elif isinstance(tgt, ast.Tuple):
                    for elt in tgt.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
            # __all__ 是再导出契约：其中的名字一定可导入
            val = node.value
            if isinstance(val, (ast.List, ast.Tuple)):
                for elt in val.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.add(elt.value)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    star = True
                else:
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names.add(alias.asname)
                else:
                    names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.If):
            # if TYPE_CHECKING: / if sys.version_info... 内的定义同样可导入
            for sub in node.body:
                for sub_node in ast.walk(sub):
                    if isinstance(sub_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.add(sub_node.name)
    return names, star


class _ImportWalker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.refs: List[Tuple[ast.AST, bool, bool]] = []
        self._guarded = 0
        self._type_checking = 0

    # 可选导入：try/except ImportError
    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        guards = False
        for handler in node.handlers:
            names = []
            if isinstance(handler.type, ast.Tuple):
                names = [getattr(e, "id", "") for e in handler.type.elts]
            else:
                names = [getattr(handler.type, "id", "")]
            if {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"} & set(names):
                guards = True
        if guards:
            self._guarded += 1
        for child in node.body:
            self.visit(child)
        if guards:
            self._guarded -= 1
        for handler in node.handlers:
            for child in handler.body:
                self.visit(child)
        for child in node.orelse + node.finalbody:
            self.visit(child)

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        is_tc = False
        test = node.test
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            is_tc = True
        elif isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
            is_tc = True
        if is_tc:
            self._type_checking += 1
        for child in node.body:
            self.visit(child)
        if is_tc:
            self._type_checking -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.refs.append((node, self._guarded > 0, self._type_checking > 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.refs.append((node, self._guarded > 0, self._type_checking > 0))


def _relative_base(file_path: Path, level: int, root_hint: Sequence[Path]) -> Optional[Path]:
    """相对导入的基准目录；``level=1`` 为当前文件所在包。"""
    pkg_dir = file_path.parent
    for _ in range(max(level - 1, 0)):
        parent = pkg_dir.parent
        if parent == pkg_dir:
            return None
        pkg_dir = parent
    return pkg_dir


def scan_file(path: Path, roots: Sequence[Path], index: Optional[Dict[str, List[Path]]] = None,
              rel_to: Optional[Path] = None) -> List[ImportFinding]:
    """静态扫描单个 Python 文件，返回不可解析的导入（含非致命的可选导入）。"""
    path = Path(path)
    roots = list(roots)
    index = index if index is not None else _top_level_index(roots)
    rel = str(path.relative_to(rel_to)) if rel_to and path.is_relative_to(rel_to) else str(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError as exc:  # 语法错误本身也是死面，交给 collector 报
        return [ImportFinding(rel, exc.lineno or 0, "module_missing", "", None,
                              detail=f"SyntaxError: {exc.msg}")]

    walker = _ImportWalker()
    walker.visit(tree)
    findings: List[ImportFinding] = []
    for node, guarded, type_checking in walker.refs:
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # 相对导入：以文件所属包为根解析
                base = _relative_base(path, node.level, roots)
                if base is None:
                    findings.append(ImportFinding(rel, node.lineno, "relative_unresolved",
                                                  "." * node.level + (node.module or ""),
                                                  None, guarded, type_checking,
                                                  "相对导入超出仓库边界"))
                    continue
                mod_path = resolve_module(node.module or "", [base])
                mod_name = "." * node.level + (node.module or "")
            else:
                module = node.module or ""
                top = module.split(".")[0]
                # pytest 的 prepend 导入模式会把**测试文件自身目录**插进 sys.path，
                # 所以 `from conftest import x` 这类同目录兄弟模块导入必须按
                # "文件自身目录"再解析一次，否则会把合法的兄弟导入误判 symbol_missing。
                cand_roots = [path.parent] + [r for r in index.get(top, []) if r != path.parent]
                local_hit = resolve_module(module, [path.parent])
                if top not in index and local_hit is None:
                    continue  # 外部依赖，不判
                # 文件自身目录优先（pytest prepend 把测试 basedir 插在 sys.path[0]，
                # 会遮蔽同名模块，例如 backend/conftest.py），否则误报 symbol_missing。
                mod_path = local_hit or resolve_module(module, cand_roots)
                mod_name = module
                if mod_path is None:
                    findings.append(ImportFinding(rel, node.lineno, "module_missing", mod_name,
                                                  None, guarded, type_checking,
                                                  f"本地模块 {module} 在 {[str(r) for r in cand_roots]} 下不可解析"))
                    continue
                for alias in node.names:
                    if alias.name == "*" or mod_path is None:
                        continue
                    try:
                        sub_tree = ast.parse(mod_path.read_text(encoding="utf-8", errors="replace"),
                                             filename=str(mod_path))
                    except SyntaxError:
                        continue
                    defined, star = _module_defined_names(sub_tree)
                    if star:
                        continue
                    if alias.name not in defined and not _is_submodule(mod_path, alias.name):
                        findings.append(ImportFinding(
                            rel, node.lineno, "symbol_missing", mod_name, alias.name,
                            guarded, type_checking,
                            f"{mod_name} 中不存在顶层符号 {alias.name}（疑似符号改名/搬家/再导出被删）"))
                continue
            if mod_path is None:
                findings.append(ImportFinding(rel, node.lineno, "module_missing", mod_name, None,
                                              guarded, type_checking, "相对导入模块不可解析"))
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                try:
                    sub_tree = ast.parse(mod_path.read_text(encoding="utf-8", errors="replace"),
                                         filename=str(mod_path))
                except SyntaxError:
                    continue
                defined, star = _module_defined_names(sub_tree)
                if star:
                    continue
                if alias.name not in defined and not _is_submodule(mod_path, alias.name):
                    findings.append(ImportFinding(
                        rel, node.lineno, "symbol_missing", mod_name, alias.name,
                        guarded, type_checking,
                        f"{mod_name} 中不存在顶层符号 {alias.name}（疑似符号迁移）"))
        else:  # ast.Import
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in index:
                    continue
                if resolve_module(alias.name, index[top]) is None:
                    findings.append(ImportFinding(rel, node.lineno, "module_missing", alias.name,
                                                  None, guarded, type_checking,
                                                  f"本地模块 {alias.name} 不可解析"))
    return findings


def scan_tree(repo_root: Path, files: Iterable[Path], roots: Optional[Sequence[Path]] = None,
              rel_to: Optional[Path] = None) -> Dict[str, List[Dict[str, object]]]:
    """批量静态扫描；返回 ``{相对路径: [findings]}``（只保留有发现的文件）。"""
    repo_root = Path(repo_root)
    roots = list(roots) if roots else local_roots(repo_root)
    index = _top_level_index(roots)
    out: Dict[str, List[Dict[str, object]]] = {}
    for f in files:
        fp = Path(f)
        if not fp.is_absolute():
            fp = repo_root / fp
        if not fp.is_file():
            continue
        findings = scan_file(fp, roots, index, rel_to=rel_to or repo_root)
        if findings:
            rel = str(fp.relative_to(rel_to)) if rel_to and fp.is_relative_to(rel_to) else str(fp)
            out[rel] = [fd.as_dict() for fd in findings]
    return out