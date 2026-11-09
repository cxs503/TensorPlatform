#!/usr/bin/env python3
"""阈值入册器：从**现网活文件**里抽真实判据，生成 ``data/criteria/threshold_registry.json``。

为什么要自动抽，而不是手写
--------------------------
"现网抽样 ≥30 条真实判据入册"如果靠人手抄，抽到的必然是作者记得住的那几条，
而且抄完就无法复核。这里改成机器抽样：

* 每个候选都是**活文件里的一个数**（JSON 数值叶子 / Python 模块级常量），
  ``source_ref`` 直接写成 ``<路径>#sha256:<hex>#<指针>`` —— 复核者拿 sha 就能判定
  "这条判据的来源还是不是当初那一份"。
* 抽样是**确定性**的（固定 seed），重跑同树得同一份注册表；候选清单可完整重放。
* 分类规则显式、保守：判定不了的来源类别一律落到 ``heuristic``（= 不可裁）。
  宁可把一条其实有出处的判据判成"不可裁"，也不许把拍脑袋的阈值放进门裁人。

三条抽源
--------
``json``      ``config/*.json`` 与 ``schemas/*.json`` 里名字像阈值的数值叶子。
``py``        ``tools/**/*.py`` 模块级数值常量（名字像阈值的；资源上限类一律拒）。
``analytic``  ``tools/criteria/probes.py`` 的先验误差界 C·h^p —— 这批是唯一带
              ``derivation`` 的判据，供派生一致性引擎（C2）与重推导闭环（C5）实测。

零领域语义
----------
域词表**不硬编码**：从 ``config/domain_word_denylist.json`` 以数据形式读入，命中
路径或符号名的候选直接剔除（这条筛选本身也进审计：``n_domain_filtered``）。

用法
----
    python3 -m tools.criteria.harvest --out data/criteria/threshold_registry.json
    python3 -m tools.criteria.harvest --dry-run     # 只报候选分布，不写盘
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import glob
import json
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(ROOT))

from tools.criteria import probes as probes_mod  # noqa: E402
from tools.criteria.threshold_registry import (  # noqa: E402
    SCHEMA_VERSION,
    classify_entry,
    eval_derivation,
    make_source_ref,
)

DENYLIST = ROOT / "config" / "domain_word_denylist.json"
DEFAULT_OUT = ROOT / "data" / "criteria" / "threshold_registry.json"

SAMPLE_SEED = 7810
MAX_PY_ENTRIES = 40
MAX_JSON_ENTRIES = 12
ANALYTIC_REFERENCE_H = 0.125

JSON_KEY_RE = re.compile(
    r"(?i)(thresh|tol|ratio|limit|quota|_min|min_|_max|max_|maximum|minimum|floor|ceil|bound|cap$)"
)
PY_SYMBOL_RE = re.compile(
    r"(THRESH|TOL|TOLERANCE|LIMIT|FLOOR|CEIL|BOUND|QUOTA|RATIO|_MIN$|^MIN_|_MAX$|^MAX_)"
)
#: 资源上限 / 实现细节，不是验收判据（抽进来只会稀释审阅注意力）。
RESOURCE_TOKENS = re.compile(
    r"(LINES?|BYTES?|_LEN|LENGTH|SIZE|MEMORY|CHILDREN|PER_RUN|PER_ROUND|TIMEOUT|"
    r"RETRIES|WORKERS|JOBS|STEPS|DEPTH|COUNT|MAXLEN|SAMPLES|BATCH|PAGES|CACHE|"
    r"PREVIEW|SNIPPET|HISTORY|BUFFER|WORKERS|THREADS|FILES)"
)
#: 不扫的源：注册表自己、域词表（它含域词）、共享契约（避免自引用）
SKIP_FILES = {
    "config/domain_word_denylist.json",
    "schemas/threshold_registry.schema.json",
}
SKIP_PREFIXES = ("data/criteria/",)


# --------------------------------------------------------------------------- #
# 域词过滤（数据驱动）
# --------------------------------------------------------------------------- #
def load_domain_patterns(path: Path = DENYLIST) -> List[re.Pattern]:
    if not path.is_file():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    pats: List[re.Pattern] = []
    for word in doc.get("ascii_words", []):
        pats.append(re.compile(r"(?<![A-Za-z0-9_])" + re.escape(str(word)) + r"(?![A-Za-z0-9_])", re.I))
    for word in doc.get("cjk_words", []):
        pats.append(re.compile(re.escape(str(word))))
    return pats


def has_domain_word(text: str, patterns: Sequence[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


# --------------------------------------------------------------------------- #
# 比较符推断（保守、显式）
# --------------------------------------------------------------------------- #
def infer_comparator(name: str) -> str:
    up = name.upper()
    if "MIN" in up or "FLOOR" in up:
        return ">="
    # MAX / LIMIT / CAP / BOUND / THRESH / TOL / RATIO / QUOTA：作"上限"读
    return "<="


def slug(text: str, limit: int = 100) -> str:
    s = re.sub(r"[^a-z0-9_.:/#-]+", "_", text.lower().strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if len(s) > limit:
        import hashlib

        s = s[: limit - 9] + "#" + hashlib.sha1(s.encode()).hexdigest()[:8]
    return s


# --------------------------------------------------------------------------- #
# 分类规则
# --------------------------------------------------------------------------- #
CITATION_KEYS = ("doi", "reference", "source", "standard", "clause", "citation", "ref", "anchor")


def classify_json_key(path: str, leaf_key: str, parent_obj: Dict[str, Any]) -> Dict[str, Any]:
    """JSON 抽源的来源类别规则。"""
    for key in CITATION_KEYS:
        val = parent_obj.get(key)
        if isinstance(val, str) and val.strip():
            if "doi" in key or "doi" in val.lower():
                return {
                    "source_type": "literature",
                    "source_note": f"同层 {key!r} 给了文献标识：{val[:60]}",
                }
            return {
                "source_type": "standard",
                "source_note": f"同层 {key!r} 给了规范/条款出处：{val[:60]}",
            }
    if leaf_key in ("maximum", "minimum", "exclusiveMaximum", "exclusiveMinimum"):
        return {
            "source_type": "standard",
            "source_note": "JSON Schema 契约约束（规范条款类：契约即出处）",
        }
    return {
        "source_type": "heuristic",
        "source_note": "配置值，源文件内无文献/规范/解析出处 → 保守判 heuristic（不可裁）",
    }


def classify_py_symbol(module_path: str, symbol: str, lineno: int) -> Dict[str, Any]:
    """模块级常量的来源类别规则。默认 heuristic —— 宁可低估可裁面。"""
    if symbol.endswith("_PCT") or symbol.endswith("_FRAC"):
        return {
            "source_type": "heuristic",
            "source_note": f"{module_path}:{lineno} 以百分比/分数形式写死的容差，源文件内无出处引用",
        }
    return {
        "source_type": "heuristic",
        "source_note": f"{module_path}:{lineno} 模块级数值常量，源文件内无出处引用 → 不可裁",
    }


# --------------------------------------------------------------------------- #
# 抽取
# --------------------------------------------------------------------------- #
def _iter_json_numbers(obj: Any, pointer: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_json_numbers(v, f"{pointer}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_json_numbers(v, f"{pointer}/{i}")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield pointer, obj


def _parent_obj(obj: Any, pointer: str) -> Dict[str, Any]:
    """按 pointer 找回叶子所在的父对象（用于看同层有没有 citation 键）。"""
    node = obj
    parts = [p for p in pointer.split("/") if p]
    for part in parts[:-1]:
        if isinstance(node, dict):
            node = node.get(part, {})
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return {}
        else:
            return {}
    return node if isinstance(node, dict) else {}


def harvest_json(
    root: Path, patterns: Sequence[re.Pattern], review_date: str
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    stats = {
        "scanned_files": 0,
        "candidates": 0,
        "domain_filtered": 0,
        "resource_filtered": 0,
        "trivial_filtered": 0,
    }
    out: List[Dict[str, Any]] = []
    files = sorted(glob.glob(str(root / "config" / "*.json"))) + sorted(
        glob.glob(str(root / "schemas" / "*.json"))
    )
    for fp in files:
        rel = str(Path(fp).relative_to(root))
        if rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES):
            continue
        try:
            doc = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        stats["scanned_files"] += 1
        for pointer, value in _iter_json_numbers(doc):
            key = pointer.rsplit("/", 1)[-1]
            if not JSON_KEY_RE.search(key):
                continue
            stats["candidates"] += 1
            # 资源上限（字节/条数/页数…）不是验收判据：看**整条指针**而不只看叶子键
            if RESOURCE_TOKENS.search(key.upper()) or RESOURCE_TOKENS.search(pointer.upper()):
                stats["resource_filtered"] += 1
                continue
            # 平凡界（minimum=0 这类非负性约束）不构成"可裁"阈值
            if key in ("minimum", "exclusiveMinimum") and float(value) == 0.0:
                stats["trivial_filtered"] += 1
                continue
            if has_domain_word(rel + " " + pointer, patterns):
                stats["domain_filtered"] += 1
                continue
            cls = classify_json_key(rel, key, _parent_obj(doc, pointer))
            entry = {
                "criterion_id": slug(f"json/{rel}#{pointer}"),
                "quantity": f"{rel}{pointer}",
                "comparator": infer_comparator(key),
                "threshold": float(value),
                "source_type": cls["source_type"],
                "source_ref": make_source_ref(rel, pointer, root),
                "source_note": cls["source_note"],
                "depends_on": [],
                "derivation": None,
                "probe": None,
                "review_date": review_date,
                "notes": "自动入册（json 抽源）；比较符由键名规则推断",
            }
            entry["decidable"] = classify_entry(entry)["decidable"]
            out.append(entry)
    return out, stats


def harvest_py(
    root: Path, patterns: Sequence[re.Pattern], review_date: str
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    stats = {
        "scanned_files": 0,
        "candidates": 0,
        "domain_filtered": 0,
        "resource_filtered": 0,
        "trivial_filtered": 0,
    }
    cands: List[Dict[str, Any]] = []
    for fp in sorted(glob.glob(str(root / "tools" / "**" / "*.py"), recursive=True)):
        rel = str(Path(fp).relative_to(root))
        if rel.startswith(SKIP_PREFIXES) or rel.startswith("tools/criteria/"):
            continue
        try:
            src = Path(fp).read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:  # noqa: BLE001
            continue
        stats["scanned_files"] += 1
        for node in tree.body:
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ) or not isinstance(node.value, ast.Constant):
                continue
            symbol = node.targets[0].id
            value = node.value.value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not PY_SYMBOL_RE.search(symbol.upper()):
                continue
            stats["candidates"] += 1
            if RESOURCE_TOKENS.search(symbol.upper()):
                stats["resource_filtered"] += 1
                continue
            if has_domain_word(rel + " " + symbol, patterns):
                stats["domain_filtered"] += 1
                continue
            cls = classify_py_symbol(rel, symbol, node.lineno)
            entry = {
                "criterion_id": slug(f"py/{rel}#{symbol}"),
                "quantity": f"{rel}::{symbol}",
                "comparator": infer_comparator(symbol),
                "threshold": float(value),
                "source_type": cls["source_type"],
                "source_ref": make_source_ref(rel, f"{symbol}:{node.lineno}", root),
                "source_note": cls["source_note"],
                "depends_on": [],
                "derivation": None,
                "probe": None,
                "review_date": review_date,
                "notes": "自动入册（py 抽源）；比较符由符号名规则推断",
            }
            entry["decidable"] = classify_entry(entry)["decidable"]
            cands.append(entry)
    rng = random.Random(SAMPLE_SEED)
    picked = sorted(rng.sample(cands, min(MAX_PY_ENTRIES, len(cands))), key=lambda e: e["criterion_id"])
    return picked, stats


def harvest_analytic(root: Path, review_date: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pid, probe in sorted(probes_mod.PROBES.items()):
        expr = f"C*h**{probe.order}"
        deriv = {
            "expression": expr,
            "coefficient": probe.bound_coefficient,
            "reference_h": ANALYTIC_REFERENCE_H,
        }
        entry = {
            "criterion_id": f"analytic/{pid}/bound",
            "quantity": f"discretization error bound of probe {pid}",
            "comparator": "<=",
            "threshold": eval_derivation(deriv, ANALYTIC_REFERENCE_H),
            "source_type": "analytic",
            "source_ref": make_source_ref("tools/criteria/probes.py", f"probe:{pid}", root),
            "source_note": f"{probe.description}；常数 C 由该问题标准误差展开给出，可手算复核",
            "depends_on": [{"param": "h", "exponent": float(probe.order)}],
            "derivation": deriv,
            "probe": pid,
            "review_date": review_date,
            "notes": "解析来源：先验误差界，可重推导 threshold(h)",
        }
        entry["decidable"] = classify_entry(entry)["decidable"]
        out.append(entry)
    return out


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def build_registry(root: Path = ROOT, review_date: Optional[str] = None) -> Dict[str, Any]:
    review_date = review_date or _dt.date.today().isoformat()
    patterns = load_domain_patterns(Path(root) / "config" / "domain_word_denylist.json")
    json_entries, json_stats = harvest_json(root, patterns, review_date)
    py_entries, py_stats = harvest_py(root, patterns, review_date)
    analytic_entries = harvest_analytic(root, review_date)

    # JSON 抽源也做确定性限量
    json_entries = sorted(json_entries, key=lambda e: e["criterion_id"])
    if len(json_entries) > MAX_JSON_ENTRIES:
        rng = random.Random(SAMPLE_SEED)
        json_entries = sorted(
            rng.sample(json_entries, MAX_JSON_ENTRIES), key=lambda e: e["criterion_id"]
        )

    criteria = analytic_entries + json_entries + py_entries
    registry = {
        "schema_version": SCHEMA_VERSION,
        "purpose": (
            "验收阈值口径注册表：每条判据带出处/依赖量/可重推导表达式/复核日期/可裁标注。"
            "由 tools/criteria/harvest.py 从现网活文件确定性抽样生成；"
            "domain 词表以数据注入（config/domain_word_denylist.json），本文件只放数值与指针。"
        ),
        "harvest": {
            "generated_on": review_date,
            "sample_seed": SAMPLE_SEED,
            "json": json_stats,
            "py": py_stats,
            "n_analytic": len(analytic_entries),
        },
        "criteria": criteria,
    }
    return registry


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="现网阈值入册器")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--review-date", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    registry = build_registry(ROOT, args.review_date)
    criteria = registry["criteria"]
    summary = {
        "n_total": len(criteria),
        "by_source_type": {
            t: sum(1 for e in criteria if e["source_type"] == t)
            for t in ("analytic", "literature", "standard", "empirical", "heuristic")
        },
        "n_decidable": sum(1 for e in criteria if e["decidable"]),
        "harvest": registry["harvest"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(criteria)} criteria)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())