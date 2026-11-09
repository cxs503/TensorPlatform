"""Anti-fabrication tests for the ludwig OSS-INVENTORY report.

These tests guard the claim "every conclusion carries a verified code
pointer".  They need an extracted copy of the analysed source tree; when it is
absent the pointer tests skip (they never silently pass), while the structural
tests on the committed markdown always run.

Set ``LUDWIG_SRC`` to the extracted tree, e.g.::

    tar xzf ludwig.tar.gz -C /tmp && LUDWIG_SRC=/tmp/ludwig-master \
        pytest tests/test_oss_inventory_ludwig.py -q
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools" / "oss_inventory"
DOC = REPO_ROOT / "docs" / "oss_inventory" / "OSS-INV-ludwig.md"
PNG = REPO_ROOT / "artifacts" / "oss_inventory" / "ludwig_inventory.png"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def inventory():
    return _load("ludwig_inventory")


@pytest.fixture(scope="module")
def evidence():
    return _load("ludwig_evidence")


@pytest.fixture(scope="module")
def evidence_b():
    return _load("ludwig_evidence_b")


def test_doc_exists_and_has_eight_sections():
    text = DOC.read_text()
    heads = re.findall(r"^## (.+)$", text, flags=re.M)
    assert len(heads) >= 8, heads
    for kw in ("概况", "方法谱系", "边界处理", "多相", "GPU", "可微", "映射", "Top3"):
        assert any(kw in h for h in heads), f"missing section: {kw}"


def test_machine_blocks_parse():
    text = DOC.read_text()
    inv = re.findall(r"\[OSS-INV\]\n(.*?)\n\[/OSS-INV\]", text, flags=re.S)
    assert len(inv) == 1
    fields = {
        k.strip(): v.strip()
        for k, v in (line.split(":", 1) for line in inv[0].splitlines() if ":" in line)
    }
    for key in ("repo", "lang", "license", "methods", "multiphase", "boundary",
                "gpu", "summary"):
        assert fields.get(key, "").strip(), f"empty machine field {key}"
    assert fields["repo"] == "ludwig-cf/ludwig"
    assert "C" in fields["lang"]
    assert "BSD-3-Clause" in fields["license"]
    # honest negatives must be present, not glossed over
    assert "NO Shan-Chen" in fields["multiphase"]
    assert "no density ratio" in fields["multiphase"].lower()

    sols = re.findall(r"\[OSS-SOL\]\n(.*?)\n\[/OSS-SOL\]", text, flags=re.S)
    assert len(sols) >= 10
    for block in sols:
        f = {k.strip(): v.strip()
             for k, v in (line.split(":", 1) for line in block.splitlines() if ":" in line)}
        assert f["repo"] == "ludwig-cf/ludwig"
        assert f["slug"].strip()
        assert f["transfer"] in ("fits", "adapt", "reference")
        assert re.search(r"\.(c|h|tex|yml|sh|md|inp|mk)(:\d+)?", f["files"]), f["files"]


def test_expected_slugs_are_covered(evidence_b):
    wanted = {
        "GAP-LBM-MCMP-POISEUILLE", "MCMP 高密度比", "自由面（free surface）",
        "相场 Allen-Cahn", "GPU 融合 kernel", "分布式多卡", "稀疏/多孔",
        "案例组织", "测试与验证面", "CI",
    }
    slugs = {m[0] for m in evidence_b.MAPPING}
    assert wanted <= slugs, wanted - slugs
    # gaps must be proposed as NEW items
    assert any(s.startswith("NEW") for s in slugs)


def test_no_hand_typed_line_numbers_in_machine_blocks():
    """OSS-SOL ``files`` must be resolved pointers or bare paths, never guesses."""
    text = DOC.read_text()
    for block in re.findall(r"\[OSS-SOL\]\n(.*?)\n\[/OSS-SOL\]", text, flags=re.S):
        f = {k.strip(): v.strip()
             for k, v in (line.split(":", 1) for line in block.splitlines() if ":" in line)}
        for item in f["files"].split(","):
            item = item.strip()
            # only ``path:line`` in backticks (resolved) or a bare path
            assert re.fullmatch(r"(`[^`]+:\d+`|\S+\.(c|h|tex|yml|sh|md|inp|mk))", item), item


def test_figure_written_with_content():
    assert PNG.is_file() and PNG.stat().st_size > 20_000


SRC = os.environ.get("LUDWIG_SRC")


@pytest.mark.skipif(not SRC, reason="set LUDWIG_SRC to an extracted ludwig tree")
def test_every_pointer_resolves(inventory, evidence, evidence_b):
    """Hard gate: no needle may be missing -> no invented file:line."""
    src = Path(SRC)
    assert (src / "src" / "collision.c").is_file()
    resolved = inventory.validate(src)
    assert len(resolved) >= 100
    for r in resolved:
        assert (src / r.path).is_file()
        line = (src / r.path).read_text(errors="replace").splitlines()[r.line - 1]
        assert r.needle in line, (r.path, r.line, r.needle)


@pytest.mark.skipif(not SRC, reason="set LUDWIG_SRC to an extracted ludwig tree")
def test_committed_doc_matches_generator(inventory):
    """The committed markdown must be exactly what the generator produces."""
    markdown, _stats = inventory.render(Path(SRC))
    assert DOC.read_text().strip() == markdown.strip()


@pytest.mark.skipif(not SRC, reason="set LUDWIG_SRC to an extracted ludwig tree")
def test_code_stats_are_derived_not_typed(inventory):
    st = inventory.code_stats(Path(SRC))
    assert st["total_files"] > 100
    assert st["total_loc"] > 50_000
    assert st["unit_tests"] > 50
    assert sum(st["regression"].values()) > 150
    assert sum(v["loc"] for v in st["groups"].values()) == st["total_loc"]