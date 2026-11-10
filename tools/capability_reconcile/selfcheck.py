"""包内自检：合成能力语料 + 金标准 + 准确率度量（供 CI / 报告复用）。

合成语料是**声明式**的（能力 id 与域标签均为 ``syn.*`` / ``zone-*`` 合成名），
不含任何真实学科词条；它的作用是把判定规则的每一分支都钉在一条可复核的用例上，
并给出机器可算的覆盖率 / 过度声明准确率 / 等级一致率 / 审计锚可复现性。

用法::

    python3 -m tools.capability_reconcile.selfcheck            # 打印度量
    python3 -m tools.capability_reconcile.selfcheck --json out.json

注意：本模块内的 ``syn.*`` 字样是**自检夹具数据**，不是领域语义硬编码；
``tests/test_capability_reconcile.py`` 的零域扫描对本文件只做「真实台账 id /
域标签字面量」检查。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

from . import core, engine

# ── 合成工作树 ──────────────────────────────────────────────────────────────
GOLD = {
    "syn.alpha_ok": ("REAL", "REAL", "CONSISTENT", None),
    "syn.beta_stub": ("REAL", "STUB", "OVERCLAIM", "HARD"),
    "syn.gamma_mock": ("REAL", "MOCK", "OVERCLAIM", "HARD"),
    "syn.delta_absent": ("REAL", "NOT_SUPPORTED", "OVERCLAIM", "HARD"),
    "syn.epsiloncase_notest": ("REAL", "WITHHELD", "OVERCLAIM", "SOFT"),
    "syn.zeta_trivial": ("REAL", "WITHHELD", "OVERCLAIM", "SOFT"),
    "syn.etacase_under": ("STUB", "REAL", "UNDERCLAIM", None),
    "syn.theta_absent_ok": ("NOT_SUPPORTED", "NOT_SUPPORTED", "CONSISTENT", None),
    "syn.iota_docconflict": ("REAL", "WITHHELD", "OVERCLAIM", "SOFT"),
    "syn.kappa_verifail": ("REAL", "WITHHELD", "OVERCLAIM", "SOFT"),
    "syn.lambda_noacc": ("REAL", "WITHHELD", "OVERCLAIM", "SOFT"),
    "syn.nucase_junitfail": ("REAL", "WITHHELD", "OVERCLAIM", "SOFT"),
}
GOLD_OVERCLAIMS = {cid for cid, g in GOLD.items() if g[2] == "OVERCLAIM"}
DOMAINS = {cid: f"zone-{cid.split('.')[1][:4]}" for cid in GOLD}

_KERNEL = '''"""Synthetic analytic kernel (no domain semantics)."""
import math


def compute(x, scale=1.0):
    """Deterministic dimensionless response."""
    if x is None:
        raise ValueError("x is required")
    if x <= 0:
        raise ValueError("x must be positive")
    return scale * math.sqrt(x) + 0.5 * x
'''
_OK_DOC = """# alpha capability

`syn.alpha_ok` 提供无量纲响应。

- 精度：与闭式解对比，最大误差 1.2%。
- 适用范围：x > 0 的正值输入，scale ∈ (0, 10]。
"""
_VERIFY_OK_DOC = """# verification of syn.alpha_ok

参考值对比：误差 1.2%，全部判据通过。
"""


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _w(repo / "src" / "alpha_impl.py", _KERNEL)
    _w(repo / "tests" / "test_alpha.py", '''import math

CAP = "syn.alpha_ok"


def test_compute_matches_reference():
    tol = 1e-9
    assert abs(compute(4.0) - 4.0) < tol
''')
    _w(repo / "docs" / "alpha.md", _OK_DOC)
    _w(repo / "docs" / "verification" / "alpha_verify.md", _VERIFY_OK_DOC)

    # beta：空壳实现（声明 REAL）
    _w(repo / "src" / "beta_impl.py", '''CAP = "syn.beta_stub"


def run(payload):
    """Synthetic shell."""
    raise NotImplementedError("not implemented yet")
''')

    # gamma：未播种随机（声明 REAL）
    _w(repo / "src" / "gamma_impl.py", '''import random

CAP = "syn.gamma_mock"


def _span(n):
    """Clamp helper."""
    return float(max(1, int(n)))


def sample(n):
    """Random response, no deterministic basis."""
    span = _span(n)
    return [random.uniform(0.0, span) for _ in range(int(n))]
''')
    _w(repo / "tests" / "test_gamma.py", '''CAP = "syn.gamma_mock"


def test_shape():
    assert len(sample(3)) == 3
''')
    _w(repo / "docs" / "gamma.md", """# gamma

`syn.gamma_mock` 误差 0.8%。适用范围：任意 n。
""")

    # epsilon：有代码有文档无测试（声明 REAL）
    _w(repo / "src" / "epsiloncase_impl.py", _KERNEL.replace("Synthetic analytic kernel", "epsilon kernel"))
    _w(repo / "docs" / "epsiloncase.md", """# epsilon

`syn.epsiloncase_notest` 误差 0.9%。适用范围：x > 0。
""")

    # zeta：只有占位断言（声明 REAL）
    _w(repo / "src" / "zeta_impl.py", _KERNEL)
    _w(repo / "tests" / "test_zeta.py", '''CAP = "syn.zeta_trivial"


def test_placeholder():
    assert True
''')
    _w(repo / "docs" / "zeta.md", """# zeta

`syn.zeta_trivial` 误差 2.5%。适用范围：x > 0。
""")

    # eta：三源齐全但声明 STUB（欠声明）
    _w(repo / "src" / "etacase_impl.py", _KERNEL.replace("Synthetic analytic kernel", "eta kernel"))
    _w(repo / "tests" / "test_etacase.py", '''import pytest

CAP = "syn.etacase_under"


def test_eta_reference():
    assert compute(9.0) == pytest.approx(7.5, rel=1e-9)
''')
    _w(repo / "docs" / "etacase.md", """# eta

`syn.etacase_under` 误差 0.5%。适用范围：x > 0。
""")

    # iota：文档反述弱等级（声明 REAL）
    _w(repo / "src" / "iota_impl.py", _KERNEL)
    _w(repo / "tests" / "test_iota.py", '''CAP = "syn.iota_docconflict"


def test_iota():
    assert abs(compute(1.0) - 1.5) < 1e-12
''')
    _w(repo / "docs" / "iota.md", """# iota

`syn.iota_docconflict` 的结论：MOCK —— 当前仅为占位实现。
""")

    # kappa：验证工件判失败（声明 REAL）
    _w(repo / "src" / "kappa_impl.py", _KERNEL)
    _w(repo / "tests" / "test_kappa.py", '''CAP = "syn.kappa_verifail"


def test_kappa():
    assert abs(compute(4.0) - 4.0) < 1e-9
''')
    _w(repo / "docs" / "kappa.md", """# kappa

`syn.kappa_verifail` 误差 1.0%。适用范围：x > 0。
""")
    _w(repo / "docs" / "verification" / "kappa_audit.md", """# audit of syn.kappa_verifail

复算误差 30%，判据 3/8 通过，honest FAIL：不得回填 REAL。
""")

    # lambda：文档只有定性描述，无精度数字（声明 REAL）
    _w(repo / "src" / "lambda_impl.py", _KERNEL)
    _w(repo / "tests" / "test_lambda.py", '''CAP = "syn.lambda_noacc"


def test_lambda():
    assert abs(compute(16.0) - 6.0) < 1e-9
''')
    _w(repo / "docs" / "lambda.md", """# lambda

`syn.lambda_noacc` 提供无量纲响应，实现可用。
""")

    # nu：三源齐全但 junit 显示用例失败（声明 REAL）
    _w(repo / "src" / "nucase_impl.py", _KERNEL)
    _w(repo / "tests" / "test_nucase.py", '''CAP = "syn.nucase_junitfail"


def test_nu():
    assert abs(compute(4.0) - 4.0) < 1e-9
''')
    _w(repo / "docs" / "nucase.md", """# nu

`syn.nucase_junitfail` 误差 0.7%。适用范围：x > 0。
""")
    return repo


JUNIT = {"syn.nucase_junitfail": {"tests": 3, "failures": 2, "errors": 0}}


def write_ledger(tmp_path: Path) -> Path:
    ledger = {
        "schema_version": "test/1.0",
        "capabilities": [
            {"id": cid, "claimed_level": gold[0], "domain": DOMAINS[cid]} for cid, gold in GOLD.items()
        ],
    }
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return path



def evaluate(tmpdir: Path | None = None) -> Dict[str, Any]:
    """在合成语料上跑一遍引擎，返回机器可算判据。"""
    tmp = Path(tmpdir) if tmpdir else Path(tempfile.mkdtemp(prefix="caprecon-"))
    repo = build_fixture_repo(tmp)
    ledger = write_ledger(tmp)
    report = engine.build_report(ledger_path=ledger, root=repo, junit=JUNIT, generated_at="2026-01-01T00:00:00+00:00")
    again = engine.build_report(ledger_path=ledger, root=repo, junit=JUNIT, generated_at="2030-01-01T00:00:00+00:00")
    got = {c["id"]: c for c in report["capabilities"]}
    pred_over = {cid for cid, c in got.items() if c["verdict"] == "OVERCLAIM"}
    tp = len(pred_over & GOLD_OVERCLAIMS)
    fp = len(pred_over - GOLD_OVERCLAIMS)
    fn = len(GOLD_OVERCLAIMS - pred_over)
    agree = sum(1 for cid in GOLD if got[cid]["derived_level"] == GOLD[cid][1])
    return {
        "capabilities": len(GOLD),
        "scan_coverage": report["summary"]["scan_coverage"],
        "grade_agreement": round(agree / len(GOLD), 4),
        "overclaim_precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "overclaim_recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "overclaim_accuracy": round(tp / (tp + fp + fn), 4) if (tp + fp + fn) else None,
        "overclaim_confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": len(GOLD) - tp - fp - fn},
        "schema_valid": report["validation"]["valid"],
        "anchor": report["audit_log"]["anchor"]["value"],
        "anchor_reproducible": report["audit_log"]["anchor"]["value"] == again["audit_log"]["anchor"]["value"],
        "chain_len": report["audit_log"]["chain_len"],
        "per_case": [
            {
                "id": cid,
                "gold_declared": GOLD[cid][0],
                "gold_derived": GOLD[cid][1],
                "gold_verdict": GOLD[cid][2],
                "derived_level": got[cid]["derived_level"],
                "verdict": got[cid]["verdict"],
                "reasons": got[cid]["reasons"],
            }
            for cid in sorted(GOLD)
        ],
    }


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="capability reconcile self-check on synthetic corpus")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    metrics = evaluate()
    text = json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if (metrics["schema_valid"] and metrics["anchor_reproducible"]) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
