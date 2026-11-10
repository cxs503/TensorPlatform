"""证据路径解析：显式绑定优先，模式匹配兜底；解析结果带 sha256，可复算。

纪律（决定了一致率从哪里来）：

1. spec 给了 ``bindings`` ⇒ **只信绑定**，不再模式匹配（绑定是人审过的证据面）；
2. 未给绑定 ⇒ 按 ``patterns`` 匹配**相对路径名**（不匹配文件内容）——
   内容匹配会把 "stats"/"GM" 之类的短词放大成整仓噪音，是同类工具一致率崩到 38% 的主因；
3. 什么都没解析到 ⇒ ``UNBOUND``：证据未绑定，判 WITHHELD（不是 NOT_SUPPORTED，
   缺失绑定只说明"没找过"，不构成"不存在"的正面反证）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

#: 默认扫描根（相对仓库根）；全部为代码/测试目录，避免扫 artifacts/docs 造成噪音
DEFAULT_CODE_ROOTS = ("backend", "api", "src", "scripts", "tools")
DEFAULT_TEST_ROOTS = ("tests", "backend/tests")
SKIP_DIRS = frozenset({".git", ".recycle", "__pycache__", "node_modules", ".mypy_cache",
                       ".pytest_cache", ".venv", "dist", "build"})


class FileIndex:
    """仓库内文件索引（按相对路径），用于路径名匹配。"""

    def __init__(self, root: str | Path, code_roots=DEFAULT_CODE_ROOTS,
                 test_roots=DEFAULT_TEST_ROOTS):
        self.root = Path(root).resolve()
        self.code_roots = tuple(code_roots)
        self.test_roots = tuple(test_roots)
        self.code_files: list[str] = []
        self.test_files: list[str] = []
        self._walk()

    def _walk(self) -> None:
        for base in self.code_roots:
            self.code_files.extend(self._collect(base))
        for base in self.test_roots:
            self.test_files.extend(self._collect(base))
        self.code_files = sorted(set(self.code_files))
        self.test_files = sorted(set(self.test_files))

    def _collect(self, base: str) -> list[str]:
        out: list[str] = []
        base_path = self.root / base
        if not base_path.exists():
            return out
        for dirpath, dirnames, filenames in os.walk(base_path):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                p = Path(dirpath) / name
                out.append(p.relative_to(self.root).as_posix())
        return out

    def exists(self, rel: str) -> bool:
        return (self.root / rel).is_file()


def _match(rel: str, patterns: list[str]) -> bool:
    """**词元级**匹配（不是任意子串）。

    任意子串会把 ``stats``/``generate``/``predict`` 这类短词放大到整仓（12 个不相关文件），
    聚合成"未播种随机"之类的假信号，是同类工具一致率崩到 38% 的主因之一。
    这里按路径词元（``/`` 与非字母数字切分）精确比对；含连字符的模式（``open-water``）
    允许在路径里按原样出现。
    """
    tokens = {t for t in re.split(r"[^a-z0-9]+", rel.lower()) if t}
    low = rel.lower()
    for pat in patterns:
        if not pat:
            continue
        plow = pat.lower()
        if plow in tokens:
            return True
        if any(ch in plow for ch in "-. ") and plow in low:
            return True
    return False


def resolve_capability(cap: dict, index: FileIndex, thresholds: dict) -> dict:
    """返回 ``{code: [...], tests: [...], source: BINDING|PATTERN|UNBOUND, missing: [...]}``。"""
    bindings = cap.get("bindings") or {}
    if bindings.get("code") or bindings.get("tests"):
        code = [p for p in bindings.get("code") or [] if index.exists(p)]
        tests = [p for p in bindings.get("tests") or [] if index.exists(p)]
        missing = [p for p in (bindings.get("code") or []) + (bindings.get("tests") or [])
                   if not index.exists(p)]
        return {"code": code, "tests": tests, "missing": missing,
                "source": "BINDING", "patterns": {}}

    patterns = cap.get("patterns") or {}
    code = [p for p in index.code_files if _match(p, patterns.get("code") or [])]
    tests = [p for p in index.test_files if _match(p, patterns.get("tests") or [])]
    code = code[: int(thresholds.get("max_impl_files", 12))]
    tests = tests[: int(thresholds.get("max_test_files", 12))]
    source = "PATTERN" if (code or tests) else "UNBOUND"
    return {"code": code, "tests": tests, "missing": [], "source": source,
            "patterns": {k: list(v) for k, v in patterns.items()}}