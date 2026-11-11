"""合成负控：三种符号迁移的最小复现仓（不含任何领域词条）。

为什么要有它：事故重放（git 历史）给的是"真事故"但只有一例；判据要求 3 种符号迁移
检出率 3/3，就得有三种**已知答案**的合成输入。这里三种分别是：

=================  =========================================================
``module_rename``  ``from pkg.old_mod import f``，模块已改名 ``pkg.new_mod``
``symbol_rename``  ``from pkg.mod import old_f``，模块在、符号已改名 ``new_f``
``reexport_removed`` ``from pkg.geo import Volume``，符号搬到 ``pkg.shape`` 且再导出被删
=================  =========================================================

造件与巡检分离：造件只写文件，判决全部来自巡检本身（否则就是自己判自己）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from .patrol import patrol

MIGRATIONS: Dict[str, Dict[str, str]] = {
    "module_rename": {
        "pkg/__init__.py": "",
        "pkg/new_mod.py": "def f():\n    return 1\n",
        "tests/test_case.py": ("from pkg.old_mod import f\n\n\n"
                               "def test_case():\n    assert f() == 1\n"),
    },
    "symbol_rename": {
        "pkg/__init__.py": "",
        "pkg/mod.py": "def new_f():\n    return 1\n",
        "tests/test_case.py": ("from pkg.mod import old_f\n\n\n"
                               "def test_case():\n    assert old_f() == 1\n"),
    },
    "reexport_removed": {
        "pkg/__init__.py": "",
        "pkg/shape.py": "class Volume:\n    value = 1.0\n",
        "pkg/geo.py": "from pkg.shape import Volume as _V\n\n\ndef displaced():\n    return _V\n",
        "tests/test_case.py": ("from pkg.geo import Volume\n\n\n"
                               "def test_case():\n    assert Volume.value == 1.0\n"),
    },
}

REGISTRY: Dict[str, Any] = {
    "schema_version": "synthetic/1.0",
    "capabilities": [{"capability_id": "synthetic.surface",
                      "test_patterns": [r"tests/test_case\.py"]}],
}


def build_migration_repos(root: Path) -> List[Tuple[str, Path]]:
    """把三种迁移各造成一个独立小仓库，返回 ``[(name, path)]``。"""
    import json

    root = Path(root)
    out: List[Tuple[str, Path]] = []
    for name, files in MIGRATIONS.items():
        repo = root / name
        for rel, content in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        cfg = repo / "config" / "capabilities.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(REGISTRY, ensure_ascii=False, indent=1), encoding="utf-8")
        out.append((name, repo))
    return out


def run_negative_control(root: Path, *, timeout: float = 90.0) -> Dict[str, Any]:
    """跑三种合成迁移，统计检出率（判据：3/3）。"""
    cases: List[Dict[str, Any]] = []
    for name, repo in build_migration_repos(Path(root)):
        report = patrol(repo, registry_path=repo / "config" / "capabilities.json",
                        timeout=timeout)
        dead = [s for s in report["surfaces"] if s["status"] == "dead"]
        static_kinds = sorted({f["kind"] for s in report["surfaces"]
                               for f in s["evidence"]["static"]
                               if f["kind"] in ("module_missing", "symbol_missing")})
        collect_kinds = sorted({(s["evidence"]["collect"] or {}).get("error_kind", "")
                                for s in dead if s["evidence"]["collect"]})
        cases.append({
            "case": name,
            "dead_units": [s["unit"] for s in dead],
            "static_kinds": static_kinds,
            "collect_error_kinds": [k for k in collect_kinds if k],
            "collect_exit_code": report["collect"]["exit_code"],
            "capability_status": [c["status"] for c in report["capabilities"]],
            "detected": bool(dead) and report["collect"]["exit_code"] != 0,
        })
    detected = sum(1 for c in cases if c["detected"])
    return {
        "schema": "verification-vitality-negative-control/1.0",
        "cases": cases,
        "total": len(cases),
        "detected": detected,
        "detection_rate": round(detected / len(cases), 4) if cases else 0.0,
        "passed": detected == len(cases) == 3,
    }