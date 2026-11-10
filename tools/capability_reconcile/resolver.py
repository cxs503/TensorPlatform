"""台账规范化与三源文件解析（code / test / doc）。

零领域语义：能力 id、域标签、源路径全部来自输入，本模块只有**结构性**启发式
（路径命名、AST 结构、文件内引用扇出），不含任何物理域词条。
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TEXT_SUFFIXES = (".py", ".md", ".rst", ".json", ".yml", ".yaml", ".txt", ".cfg", ".toml")
SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
}
CODE_SUFFIX = ".py"
DOC_SUFFIXES = (".md", ".rst", ".txt")
MAX_FILE_BYTES = 512 * 1024

#: 单文件引用能力 id 数 ≥ 该阈值 → 视作「注册表/聚合器」，不算实质实现代码。
REGISTRY_FANOUT = 8

#: 判定路径是否为验证/审计工件的路径词（结构性词，非域词）。
VERIFICATION_PATH_RE = re.compile(r"(verif|validat|audit|benchmark|regression)", re.I)

#: 能力 id 分词的通用停用词（结构词，非域词）。用于「按名字找实现文件」。
STOPWORDS = {
    "calc", "api", "core", "dev", "gov", "app", "apps", "util", "utils", "lib", "libs",
    "main", "test", "tests", "type", "types", "base", "help", "status", "list", "get",
    "set", "has", "and", "the", "for", "from", "with", "info", "src", "data",
}

#: 名字匹配可接受的实现文件数上限（防一次命中整个目录）。
MAX_IMPL_FILES = 14


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 台账规范化 ─────────────────────────────────────────────────────────────
def _grade_of(row: Any) -> Optional[str]:
    if isinstance(row, str):
        return row.strip().upper()
    if isinstance(row, dict):
        for key in ("claimed_level", "declared_level", "grade", "level", "evidence"):
            val = row.get(key)
            if isinstance(val, str):
                return val.strip().upper()
    return None


def normalize_ledger(payload: Any) -> List[Dict[str, Any]]:
    """把常见台账形状压成 ``[{id, declared_level, domain}]``，顺序按 id 排序。"""
    caps = payload.get("capabilities", payload) if isinstance(payload, dict) else payload
    rows: List[Tuple[str, Optional[str], Optional[str]]] = []
    if isinstance(caps, dict):
        for cid, val in caps.items():
            domain = val.get("domain") if isinstance(val, dict) else None
            rows.append((str(cid), _grade_of(val), domain))
    elif isinstance(caps, list):
        for row in caps:
            if not isinstance(row, dict):
                continue
            cid = row.get("id") or row.get("capability_id") or row.get("name")
            if not cid:
                continue
            rows.append((str(cid), _grade_of(row), row.get("domain")))
    else:  # pragma: no cover - defensive
        raise ValueError("unsupported ledger shape: expected mapping or list under 'capabilities'")
    out = []
    for cid, grade, domain in sorted(rows, key=lambda r: r[0]):
        out.append(
            {
                "id": cid,
                "declared_level": grade,
                "domain": domain,
                "ledger_grade_is_known": grade in {"NOT_SUPPORTED", "STUB", "MOCK", "WITHHELD", "REAL"},
            }
        )
    return out


def normalize_bindings(payload: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, List[str]]]:
    """绑定配置：``{capability_id: {code:[relpath...], test:[...], doc:[...]}}``。"""
    if not payload:
        return {}
    out: Dict[str, Dict[str, List[str]]] = {}
    raw = payload.get("bindings", payload) if isinstance(payload, dict) else {}
    for cid, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        entry = {"code": [], "test": [], "doc": []}
        for key in entry:
            vals = spec.get(key) or []
            if isinstance(vals, str):
                vals = [vals]
            entry[key] = [str(v).replace("\\", "/").lstrip("./") for v in vals if isinstance(v, str)]
        out[str(cid)] = entry
    return out


# ── 工作树遍历 + 自动解析 ──────────────────────────────────────────────────
def _iter_text_files(root: Path, excludes: Iterable[str]) -> Iterable[Path]:
    ex = tuple(str(Path(e).resolve()) for e in excludes if e)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
        for name in sorted(filenames):
            if not name.endswith(TEXT_SUFFIXES):
                continue
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            resolved = str(path.resolve())
            if any(resolved.startswith(e) for e in ex):
                continue
            yield path


def classify_path(rel: str) -> str:
    """``test`` / ``doc`` / ``code`` —— 纯路径结构判定。"""
    parts = rel.split("/")
    name = parts[-1]
    if any(p in {"tests", "test", "tests_broken_ice"} for p in parts[:-1]) or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if name.endswith(DOC_SUFFIXES) or any(p in {"docs", "doc"} for p in parts[:-1]):
        return "doc"
    return "code"


def resolve_sources(
    root: Path,
    cap_ids: Iterable[str],
    bindings: Optional[Dict[str, Dict[str, List[str]]]] = None,
    excludes: Iterable[str] = (),
) -> Dict[str, Dict[str, Any]]:
    """为每项能力定位三源文件。

    优先级：显式绑定 > 自动解析（在工作树里搜能力 id 字面量，按路径结构分桶）。
    返回 ``{cid: {code:[], test:[], doc:[], resolution: EXPLICIT|AUTO|NONE,
    registry_files:[], verification_docs:[]}}``。
    """
    bindings = bindings or {}
    ids = list(cap_ids)
    hits: Dict[str, Dict[str, List[str]]] = {cid: {"code": [], "test": [], "doc": []} for cid in ids}
    lowered = {cid: cid.lower() for cid in ids}
    fanout: Dict[str, int] = {}
    for path in _iter_text_files(root, excludes):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low = text.lower()
        found = [cid for cid in ids if lowered[cid] in low]
        if not found:
            continue
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        bucket = classify_path(rel)
        if bucket == "code":
            fanout[rel] = len(found)
        for cid in found:
            hits[cid][bucket].append(rel)

    # 名字 token 索引：capability id 的实义片段 → 代码文件名命中（通用启发式，
    # 片段来自 id 自身，不依赖任何领域词表）。用 idf 抑制 "generate"/"models" 这类
    # 高频泛词，只留下区分度高的片段。
    code_index: List[Tuple[str, str]] = []  # (stem_lower, rel)
    for path in _iter_text_files(root, excludes):
        if path.suffix != CODE_SUFFIX:
            continue
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        if classify_path(rel) != "code":
            continue
        code_index.append((path.stem.lower(), rel))
    n_code = len(code_index) or 1

    def _idf(tok: str) -> float:
        df = sum(1 for stem, _ in code_index if tok in stem)
        if df == 0 or df > 0.2 * n_code:
            return 0.0
        return math.log(n_code / df)

    def name_matched(cid: str) -> List[str]:
        tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", cid) if len(t) >= 4 and t.lower() not in STOPWORDS]
        weights = {t: _idf(t) for t in tokens}
        weights = {t: w for t, w in weights.items() if w > 0}
        if not weights:
            return []
        scored: List[Tuple[float, str]] = []
        for stem, rel in code_index:
            score = sum(w for tok, w in weights.items() if tok in stem)
            if score > 0:
                scored.append((score, rel))
        if not scored:
            return []
        scored.sort(key=lambda r: (-r[0], r[1]))
        best = scored[0][0]
        keep = [rel for score, rel in scored if score >= 0.5 * best][:MAX_IMPL_FILES]
        return keep

    out: Dict[str, Dict[str, Any]] = {}
    for cid in ids:
        entry = hits[cid]
        spec = bindings.get(cid)
        if spec and any(spec.get(k) for k in ("code", "test", "doc")):
            merged = {k: sorted(set(spec.get(k) or []) | set(entry[k])) for k in ("code", "test", "doc")}
            resolution = "EXPLICIT"
        else:
            merged = {k: sorted(set(entry[k])) for k in ("code", "test", "doc")}
            resolution = "AUTO" if any(merged.values()) else "NONE"
        registry_files = [p for p in merged["code"] if fanout.get(p, 0) >= REGISTRY_FANOUT]
        if resolution == "EXPLICIT":
            impl_files = [p for p in merged["code"] if p not in registry_files]
        else:
            # 自动模式：实现文件 = 文件名 token 命中（当前能力 id 片段），引用命中
            # 只作旁证（许多文件仅在注释/表格里提到 id，不代表实现）。
            impl_files = name_matched(cid)
        reference_only = [p for p in merged["code"] if p not in impl_files and p not in registry_files]
        docs = merged["doc"]
        out[cid] = {
            **merged,
            "resolution": resolution,
            "registry_files": registry_files,
            "impl_files": impl_files,
            "reference_code": reference_only,
            "verification_docs": [p for p in docs if VERIFICATION_PATH_RE.search(p)],
            "has_any_source": bool(merged["code"] or merged["test"] or merged["doc"]),
        }
    return out