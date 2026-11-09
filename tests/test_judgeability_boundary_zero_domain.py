"""E4 零领域语义（P151W1-N10）：改动文件过现成边界门 G1–G3，域词命中 == 0。

判据先于实现，机器可裁：
1. ``tools/boundary/pr_gate_scan.py paths <改动文件>`` → violations == 0（域词命中 0）；
2. 判据词表成分可机检：P2 封闭词表里没有任何可被判为领域词条的项（负控：注入一个域词必须被抓到）；
3. 本包对外接口只暴露形状/方法论语义（无域名词出现在公开 API 名称里）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "boundary" / "pr_gate_scan.py"

CHANGED = [
    "backend/quality/judgeability/__init__.py",
    "backend/quality/judgeability/schema.py",
    "backend/quality/judgeability/classifier.py",
    "backend/quality/judgeability/gate.py",
    "backend/quality/judgeability/audit.py",
    "ops/taskplane/campaign_dispatcher.py",
    "tools/judgeability/export_missions.py",
    "tools/judgeability/run_audit.py",
    "tools/judgeability/render_figures.py",
    "data/quality/judgeability/synthetic_corpus.json",
    "tests/test_judgeability_schema_classifier.py",
    "tests/test_judgeability_audit_reproducible.py",
    "tests/test_judgeability_dispatcher_hook.py",
    "tests/test_judgeability_boundary_zero_domain.py",
    "tests/test_judgeability_report_gate_schema.py",
]


def _scan(paths, repo_root=None):
    extra = ["--repo-root", str(repo_root)] if repo_root else []
    r = subprocess.run([sys.executable, str(GATE), "paths", *paths, *extra],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode in (0, 1, 2), r.stderr[-800:]   # 非 0/1/2 = 门自身出错
    return json.loads(r.stdout)


def _existing():
    return [p for p in CHANGED if (ROOT / p).is_file()]


def test_changed_files_have_zero_domain_hits():
    files = _existing()
    assert len(files) >= 6, files
    rep = _scan(files)
    assert rep["totals"]["violations"] == 0, [
        (v.get("path"), v.get("line"), v.get("hits")) for v in rep["violations"][:5]]
    assert rep["verdict"] == "PASS"
    t = rep["totals"]
    assert t["files_touched"] == len(files)
    assert (t["files_scanned"] + t["files_whitelisted"] + t["files_out_of_scope"]
            + t["files_self_exempt"]) == len(files)


def test_boundary_gate_negative_control(tmp_path):
    """负控：把词表里的一个阻断词条塞进临时文件 → 门必须报违规（证明上面的 0 不是空跑）。"""
    lex = json.loads((ROOT / "data" / "boundary" / "pr_boundary_lexicon.json")
                     .read_text(encoding="utf-8"))
    term = None
    for t in lex.get("terms") or []:
        if isinstance(t, dict) and t.get("values"):
            term = str(t["values"][0])
            break
    if not term:
        pytest.skip("lexicon 结构不识别，跳过负控")
    f = tmp_path / "probe.py"
    f.write_text(f"# {term}\n", encoding="utf-8")
    rep = _scan([str(f)], repo_root=tmp_path)   # 独立 repo-root：仓根散件按代码域扫描
    assert rep["totals"]["violations"] >= 1, (term, rep["totals"])


def test_public_api_has_no_domain_vocabulary():
    """对外 API / schema 字段名只应是方法论词；域名词不应出现在公开符号里。"""
    import backend.quality.judgeability as J
    from backend.quality.judgeability import schema as S
    names = [n for n in getattr(J, "__all__", []) if not n.startswith("_")]
    assert names, "包未声明 __all__"
    js = json.dumps(S.as_json_schema(), ensure_ascii=False)
    for dim in ("P1", "P2", "P3"):
        assert dim in js
    # P2 封闭词表必须是方法论词（6 项，无数字、无长度单位、无几何量符号）
    for kind in S.REFERENCE_KINDS:
        assert kind.isalpha() or all(not c.isdigit() for c in kind)
        assert not any(ch in kind for ch in "/%±≤≥<>")