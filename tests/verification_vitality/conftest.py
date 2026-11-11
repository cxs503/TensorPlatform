"""合成仓库夹具：巡检的测试单元必须是**可控的小仓库**，不能拿真仓库当夹具。

三个造件函数：

* ``make_repo``      —— 通用：给定文件树 + 注册表，返回临时仓库根
* ``healthy_repo``   —— 正控：导入全部可解析、测试全部可收集 ⇒ 期望 0 误报
* ``migrated_repo``  —— 负控：三种符号迁移（模块改名/符号改名/再导出被删）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def make_repo(root: Path, files: dict[str, str],
              registry: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if registry is not None:
        cfg = root / "config" / "capabilities.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    return root


def registry_of(*caps: tuple[str, str]) -> dict:
    return {"schema_version": "test/1.0",
            "capabilities": [{"capability_id": cid, "test_patterns": [pat]}
                             for cid, pat in caps]}


@pytest.fixture
def repo_factory(tmp_path):
    def _factory(files: dict[str, str], registry: dict | None = None, name: str = "repo") -> Path:
        return make_repo(tmp_path / name, files, registry)
    return _factory


@pytest.fixture
def healthy_repo(tmp_path) -> Path:
    """正控仓库：模块/符号/相对导入全可解析，测试可收集。"""
    files = {
        "pkg/__init__.py": "",
        "pkg/base_mod.py": "def span_value(b, t, l):\n    return b * t * l * 0.6\n",
        "pkg/derive_mod.py": "from pkg.base_mod import span_value\n\n\ndef total_metric(speed):\n    return span_value(1, 1, 1) * speed ** 2\n",
        "tests/test_base.py": ("from pkg.base_mod import span_value\n\n\n"
                                   "def test_volume_positive():\n    assert span_value(2, 1, 10) > 0\n"),
        "tests/test_derive.py": ("from pkg.derive_mod import total_metric\n\n\n"
                                     "def test_metric_monotone():\n"
                                     "    assert total_metric(2) > total_metric(1)\n"),
        "tests/test_unregistered.py": "def test_free():\n    assert 1 + 1 == 2\n",
    }
    registry = registry_of(("synthetic.base", r"tests/test_base\.py"),
                           ("synthetic.derive", r"tests/test_derive\.py"))
    return make_repo(tmp_path / "healthy", files, registry)


from verification_vitality.controls import MIGRATIONS  # noqa: E402  (共享同一套合成迁移)


@pytest.fixture(params=sorted(MIGRATIONS))
def migrated_repo(request, tmp_path) -> tuple[Path, str]:
    """三种符号迁移各造一个仓库；返回 (仓库根, 迁移名)。"""
    name = request.param
    registry = registry_of(("synthetic.base", r"tests/test_case\.py"))
    return make_repo(tmp_path / name, MIGRATIONS[name], registry), name