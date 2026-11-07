"""pytest 判据：tools/contracts/vocab_contract_audit.py（G-A/G-B/G-C 逐项 diff 审计器）。

覆盖三件事：
1. 正向：现网契约三组 19 项全 diff=0（PASS），且 dry-run 不落盘、落盘 schema 完整（含
   canon sha256 审计链）。
2. 反向（负控）：把契约文件改坏/加一个未登记词后，审计器必须判红并指出具体词——
   证明它不是「永远 PASS」的橡皮图章。
3. 判据本体：``derives_symbol`` 的 AST 判据能区分「从契约派生」与「手写字面量」。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contracts import vocab_contract_audit as audit_mod  # noqa: E402


def test_audit_all_rows_diff_zero():
    audit = audit_mod.run_audit()
    assert audit.errors == []
    assert len(audit.rows) == 19
    assert audit.diff_total == 0
    assert audit.ok
    # 三组各自有行（不是某一组被静默跳过）
    for group in ("G-A", "G-B", "G-C"):
        assert sum(r.contract == group for r in audit.rows) >= 5, group


def test_dry_run_writes_nothing(tmp_path, capsys):
    out = tmp_path / "should_not_exist.json"
    code = audit_mod.main(["--dry-run", "--verbose", "--json-out", str(out)])
    assert code == 0
    assert not out.exists(), "dry-run 不得落盘"
    stdout = capsys.readouterr().out
    assert "DIFF=0" in stdout and "verdict=PASS" in stdout


def test_json_artifact_carries_audit_chain(tmp_path):
    out = tmp_path / "audit.json"
    code = audit_mod.main(["--json-out", str(out)])
    assert code == 0
    payload = json.loads(out.read_text())
    assert payload["summary"]["verdict"] == "PASS"
    assert payload["summary"]["diff_total"] == 0
    assert payload["summary"]["by_contract"]["G-A"]["diff_total"] == 0
    assert payload["summary"]["by_contract"]["G-B"]["diff_total"] == 0
    assert payload["summary"]["by_contract"]["G-C"]["diff_total"] == 0
    assert set(payload["canon_sha256"]) == set(audit_mod.CANON_FILES)
    assert all(len(v) == 64 for v in payload["canon_sha256"].values())
    assert payload["git_head"]
    for row in payload["rows"]:
        assert {"contract", "item", "canon_items", "runtime_items",
                "canon_only", "runtime_only", "diff", "verdict"} <= set(row)


def test_plot_artifact_rendered(tmp_path):
    """--plot-out 落 PNG（报告图的可复核来源），dry-run 下则完全不落盘。"""
    out_json = tmp_path / "audit.json"
    out_png = tmp_path / "fig.png"
    code = audit_mod.main(["--json-out", str(out_json), "--plot-out", str(out_png),
                           "--plot-dpi", "70"])
    assert code == 0
    assert out_png.exists() and out_png.stat().st_size > 5_000
    assert out_png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_negative_control_extra_canon_word_is_red(tmp_path):
    """负控：正典多一个未登记词 → 必须判红并点名该词。"""
    fake = tmp_path / "contracts"
    shutil.copytree(BACKEND / "contracts", fake)
    vocab_path = fake / "mission_status_vocab.v1.json"
    vocab = json.loads(vocab_path.read_text())
    vocab["statuses"] = vocab["statuses"] + ["ghost_state"]
    vocab_path.write_text(json.dumps(vocab, ensure_ascii=False))

    audit = audit_mod.run_audit(contracts_dir=fake)
    assert not audit.ok
    rows = [r for r in audit.rows if r.item == "statuses"]
    assert rows and rows[0].runtime_only == [] and rows[0].canon_only == ["ghost_state"]
    assert rows[0].diff == 1


def test_negative_control_handwritten_pattern_is_red(tmp_path):
    """负控：删掉事件词表正典 → 派生项与 diff 项必须红（不能静默通过）。"""
    fake = tmp_path / "contracts"
    shutil.copytree(BACKEND / "contracts", fake)
    (fake / "session_event_kinds.v1.json").unlink()

    audit = audit_mod.run_audit(contracts_dir=fake)
    assert not audit.ok
    assert audit.errors, "缺正典文件必须记错误而不是跳过"


def test_regex_tokens_roundtrip():
    tokens = audit_mod.pattern_tokens("^(alpha|beta|gamma)$")
    assert tokens == ["alpha", "beta", "gamma"]


@pytest.mark.parametrize(
    ("source", "symbol", "expected"),
    [
        ("from api.domain.status_contracts import foo as _f\n"
         "X = tuple(_f()[\"statuses\"])\n", "X", True),
        ("X = (\"queued\", \"running\")\n", "X", False),
        ("from api.domain.status_contracts import foo\n"
         "Y = 1\nX = (\"a\", \"b\")\n", "X", False),
    ],
)
def test_derives_symbol_ast_criterion(source: str, symbol: str, expected: bool):
    assert audit_mod.derives_symbol(source, symbol) is expected