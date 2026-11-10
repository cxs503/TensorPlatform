"""知识蒸馏模块的域中立性测试（判例 #608）。

平台铁律：``backend/``/``tools/``/``tests/`` 内不得硬编码具体物理域词条；域内容一律经
报告/工件注入。本测试用 ``tools/audit/domain_leak_scan.py`` 对本模块的代码、测试与
夹具做机器裁决：命中即红。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.audit.domain_leak_scan import scan  # noqa: E402

TARGETS = [
    "tools/knowledge_distill",
    "tests/test_knowledge_distill_extract.py",
    "tests/test_knowledge_distill_features.py",
    "tests/test_knowledge_distill_novelty.py",
    "tests/test_knowledge_distill_pipeline.py",
    "tests/test_knowledge_distill_drivers.py",
    "tests/fixtures/knowledge_distill",
]


def test_zero_domain_words_in_module_and_fixtures() -> None:
    result = scan(TARGETS)
    assert result["files_scanned"], "扫描目标未命中任何文件，判据不成立"
    assert result["hit_count"] == 0, result["hits"][:5]


def test_denylist_covers_module_paths() -> None:
    """模块路径必须注册进 denylist 的 scan_paths，否则本测试只是自证。"""
    import json

    doc = json.loads((ROOT / "config" / "domain_word_denylist.json").read_text(
        encoding="utf-8"))
    for target in TARGETS:
        assert target in doc["scan_paths"], target