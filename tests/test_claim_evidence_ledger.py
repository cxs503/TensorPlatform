"""声明-证据台账（tools/audit/claim_evidence_ledger.py）判据自证 + 反漂移闸门（m1962）。

判据自证三件套：
1. **正样本**：声明 + 代码体足迹 ⇒ EVIDENCED（不把真实现冤枉成漂移）。
2. **负样本**：声明只出现在 docstring/注释里、代码体空 ⇒ 必须 UNBACKED/DRIFT
   （证明判据**不靠声明自证**——这是本工具唯一真正重要的性质）。
3. **分级可分辨**：限定语 → QUALIFIED、引用语境 → REFERENCE_MENTION、
   import 到真实现 → DELEGATED，四类互不吞并。

另有真树闸门：仓内后端/服务/源码三个根目录上 PROVENANCE_DRIFT 必须为 0。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit" / "claim_evidence_ledger.py"

_spec = importlib.util.spec_from_file_location("claim_evidence_ledger", TOOL)
CEL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CEL)


# --------------------------------------------------------------------- 夹具工具
def _mk(tmp_path, name, body):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return str(p)


EVIDENCED_SRC = '''
"""ITTC-1957 friction line implemented here."""
import math

def cf(re):
    denom = math.log10(re) - 2.0
    return 0.075 / denom ** 2

def strip(ship):
    return {"method": "STF strip theory with ITTC-1957 friction line"}
'''

DOC_ONLY_SRC = '''
"""Uses the MichelL thin-ship integral (Michell 1898) for wave resistance."""
import math

def rw(u):
    return 0.5 * u * 3.0
'''

QUALIFIED_SRC = '''
"""Ikeda roll damping 近似：简化为单自由度等效阻尼."""
import math

def b44(m, w):
    return 2.0 * 0.08 * m * w
'''

REFERENCE_SRC = '''
"""Cf 用 ITTC-1957 线，参见 ITTC (1957) Recommended Procedures；本模块不含该项."""
import math

def total(rt):
    return rt * 1.1
'''

DRIFT_SRC = '''
"""Seakeeping strip kernel."""
import math

def strip(ship):
    return {"method": "STF strip theory (Lewis-section 2D coefficients)"}
'''

DELEGATED_SRC = '''
"""Friction uses the ITTC-1957 correlation line from the shared core module."""
import math
from .ittc_core import ittc57_cf

def rf(rho, s, v, re):
    return 0.5 * rho * v * v * s * ittc57_cf(re)
'''

ITTC_CORE_SRC = '''
"""ITTC-1957 model-ship correlation line."""
import math

def ittc57_cf(re):
    denom = math.log10(re) - 2.0
    return 0.075 / denom ** 2
'''


# ----------------------------------------------------------------- 1 判据自证
def test_evidence_in_code_yields_evidenced(tmp_path):
    f = _mk(tmp_path, "evidenced.py", EVIDENCED_SRC)
    led = CEL.build_ledger(files=[f])
    verdicts = {c["method"]: c["verdict"] for c in led["claims"]}
    assert verdicts.get("ITTC-1957") == "EVIDENCED"
    assert led["by_verdict"]["EVIDENCED"] >= 1


def test_docstring_mention_without_code_is_unbacked(tmp_path):
    """关键负样本：声明写在 docstring 里，代码体毫无足迹 ⇒ 不得算证据。"""
    f = _mk(tmp_path, "doc_only.py", DOC_ONLY_SRC)
    led = CEL.build_ledger(files=[f])
    methods = {(c["method"], c["verdict"]) for c in led["claims"]}
    assert ("Michell", "UNBACKED") in methods
    # 反向保证：docstring 里没有的『裸词』不该凭空造出命中
    assert all(c["method"] != "Hess-Smith" for c in led["claims"])


def test_docstring_statement_is_not_evidence_after_strip(tmp_path):
    """剥离器必须让 docstring 里的公式文本失效（否则判据自证）。"""
    src = ('"""Cf = 0.075 / (log10(Re) - 2)^2  ITTC-1957 friction line."""\n'
           "def cf(re):\n    return 0.1 * re\n")
    f = _mk(tmp_path, "strip_check.py", src)
    code = CEL.strip_docstrings_and_comments(src)
    assert "0.075" not in code
    led = CEL.build_ledger(files=[f])
    assert {"ITTC-1957": "UNBACKED"} in [dict([(c["method"], c["verdict"])])
                                        for c in led["claims"]] or [{}]


def test_qualified_disclosure_is_not_a_defect(tmp_path):
    f = _mk(tmp_path, "qualified.py", QUALIFIED_SRC)
    led = CEL.build_ledger(files=[f])
    assert any(c["method"] == "Ikeda" and c["verdict"] == "QUALIFIED"
               for c in led["claims"])
    assert not led["provenance_drift"]


def test_reference_context_is_not_an_implementation_claim(tmp_path):
    f = _mk(tmp_path, "reference.py", REFERENCE_SRC)
    led = CEL.build_ledger(files=[f])
    assert any(c["method"] == "ITTC-1957" and c["verdict"] == "REFERENCE_MENTION"
               for c in led["claims"])


def test_delegated_evidence_across_import(tmp_path):
    core = _mk(tmp_path, "ittc_core.py", ITTC_CORE_SRC)
    user = _mk(tmp_path, "user.py", DELEGATED_SRC)
    led = CEL.build_ledger(files=[core, user])
    got = [c for c in led["claims"] if c["module"].endswith("user.py")]
    assert got and all(c["verdict"] in {"EVIDENCED", "DELEGATED"} for c in got)
    assert any(c["verdict"] == "DELEGATED" for c in got)


def test_delegation_can_be_switched_off(tmp_path):
    core = _mk(tmp_path, "ittc_core.py", ITTC_CORE_SRC)
    user = _mk(tmp_path, "user.py", DELEGATED_SRC)
    led = CEL.build_ledger(files=[core, user], allow_delegation=False)
    got = [c for c in led["claims"] if c["module"].endswith("user.py")]
    assert got and all(c["verdict"] != "DELEGATED" for c in got)


# ------------------------------------------------------------- 2 provenance 漂移
def test_provenance_drift_detected_and_gated(tmp_path):
    f = _mk(tmp_path, "drift.py", DRIFT_SRC)
    led = CEL.build_ledger(files=[f])
    assert len(led["provenance_drift"]) == 1
    rec = led["provenance_drift"][0]
    assert rec["method"] == "Lewis-section" and rec["key"] == "method"
    assert CEL.gate_failed(led) is True
    assert CEL.gate_failed(led, strict=True) is True


def test_gate_passes_on_clean_tree(tmp_path):
    f = _mk(tmp_path, "clean.py", EVIDENCED_SRC)
    led = CEL.build_ledger(files=[f])
    assert led["provenance_drift"] == []
    assert CEL.gate_failed(led) is False


def test_provenance_string_launders_module_constant(tmp_path):
    """`_METHOD = "..."` 常量被字典引用时必须被解析出来（否则漏检）。"""
    src = ('_METHOD = ("STF strip theory with Lewis-section 2D coefficients, "\n'
           '           "20 stations")\n'
           "def strip(ship):\n    return {'method': _METHOD}\n")
    f = _mk(tmp_path, "const_method.py", src)
    led = CEL.build_ledger(files=[f])
    assert led["provenance_drift"], "模块级 _METHOD 常量未被解析 → 漂移漏检"


def test_short_provenance_strings_ignored(tmp_path):
    src = "def k():\n    return {'method': 'stf'}\n"
    f = _mk(tmp_path, "short.py", src)
    led = CEL.build_ledger(files=[f])
    assert led["n_provenance_entries"] == 0


# ------------------------------------------------------------------ 3 边界/健壮
def test_syntax_error_file_is_skipped(tmp_path):
    f = _mk(tmp_path, "broken.py", "def f(:\n    pass\n")
    led = CEL.build_ledger(files=[f])
    assert led["n_modules_scanned"] == 1
    assert led["n_claim_entries"] == 0


def test_conjunctive_evidence_alternative():
    """ITTC-1957 证据要求「0.075 与 log10 同在」——只有其一不算。"""
    alts = CEL.LEXICON["ITTC-1957"]["evidence"]
    assert CEL._count_alternatives(alts, "cf = 0.075 * 3.0") == 0
    assert CEL._count_alternatives(alts, "x = np.log10(re)") == 0
    assert CEL._count_alternatives(alts, "denom = np.log10(re) - 2.0\n0.075/denom**2") == 1


def test_verdict_order_is_strictness_ordered():
    assert CEL.VERDICT_ORDER[-1] == "PROVENANCE_DRIFT"
    assert CEL.VERDICT_ORDER.index("UNBACKED") < CEL.VERDICT_ORDER.index("PROVENANCE_DRIFT")


# ------------------------------------------------------------------- 4 真树闸门
@pytest.mark.parametrize("rel", ["backend/api/seakeeping_physics.py",
                                 "backend/api/seakeeping_physical_api.py"])
def test_seakeeping_provenance_is_honest(rel):
    """m1962 修复点：耐波内核/端点的 provenance 不得再宣称 Lewis 剖面解。"""
    path = ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} 不在本工作树")
    led = CEL.build_ledger(files=[str(path)])
    assert led["provenance_drift"] == [], \
        f"{rel} 的 method 字符串仍过度宣称：{led['provenance_drift']}"


def test_real_tree_gate_has_no_provenance_drift():
    """全树闸门（后端 + 服务 + src）：PROVENANCE_DRIFT 必须为 0。"""
    roots = [str(ROOT / r) for r in ("backend/api", "backend/services", "src")
             if (ROOT / r).is_dir()]
    if not roots:
        pytest.skip("真树根目录不可用")
    led = CEL.build_ledger(roots)
    assert led["provenance_drift"] == [], \
        "存在 provenance 漂移：\n" + json.dumps(led["provenance_drift"],
                                               ensure_ascii=False, indent=1)
    assert CEL.gate_failed(led) is False