#!/usr/bin/env python3
"""判据变异台账 / KPI / 准入门禁 的验收测试（P34-N7）。

为什么有这个文件
----------------
台账（`tools/audit/criteria_mutation.py`）自己也需要被判据钉住，否则它就是第四层
「静默失效」：一个永远输出 kill_rate=1.0 的台账看起来比谁都健康。

本文件钉住四件事：
1. **每条判据都有杀手**：对台账里每条判据跑「规范变异体」，判据必须亮红灯
   （这是 `pr_gate --run-killer-tests` 依赖的 node id，破坏性验证会把判据打成
   恒放行，此时这些测试必须转红）；
2. **KPI 数字可复算**：门数 ≥4、判据可实例化率 ≥90%、M1 单调性违例 = 0；
3. **负控**：恒真判据必须 0 kill 且被标成 UNFALSIFIABLE（否则台账的 kill rate
   没有意义）；
4. **门禁真的会拒**：新判据没有 killer test → `NO_KILLER_TEST` 拒；
   killer test 指向不存在的用例 → `KILLER_TEST_NOT_FOUND` 拒；
   killer test 绿着（不咬）→ `KILLER_TEST_TOOTHLESS` 拒。

运行
----
    python3 -m pytest tests/test_criteria_mutation.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "backend"), str(ROOT / "tools" / "paper_gate")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools.audit import criteria_mutation as cm  # noqa: E402

CRITERIA = cm.all_criteria()
REAL = [c for c in CRITERIA if not c.control]


def _canonical_mutant(crit: cm.Criterion) -> tuple[bool, str]:
    """该判据登记的**旗舰违规样本**（crit.canonical，默认 M3 常数替身）。"""
    cls = crit.canonical
    assert cls in crit.mutate, f"{crit.cid} 的 canonical={cls} 无对应变异体"
    eps = 0.15 if cls == "M1" else None
    return crit.check(crit.mutate[cls](eps))


@pytest.mark.parametrize("cid", [c.cid for c in REAL])
def test_criterion_kills_canonical_mutant(cid):
    """每条判据都必须抓住自己的规范变异体（killer test；被打成恒放行则转红）。"""
    crit = cm.get_criterion(cid)
    flagged, detail = _canonical_mutant(crit)
    assert flagged is True, f"{cid} 未抓到规范变异体：{detail}"


@pytest.mark.parametrize("cid", [c.cid for c in REAL])
def test_bench_samples_are_clean(cid):
    """基准样本必须放行——否则判据有假阳性（把合规稿件也毙掉）。"""
    crit = cm.get_criterion(cid)
    flagged, detail = crit.check(crit.base())
    assert flagged is False, f"{cid} 对基准样本误报：{detail}"


def test_ledger_is_not_tautological():
    """负控：恒真判据必须 0 kill，并被标成 UNFALSIFIABLE。"""
    ledger = cm.build_ledger(scratch_parent=ROOT / "artifacts" / "m5496")
    k = ledger["kpi"]
    assert k["control"]["killed"] == 0
    assert k["control"]["total"] > 0
    assert k["control"]["killed_classes"] == 0
    flagged = k["control"]["flagged_unfalsifiable"]
    assert "NEG-TAUTOLOGY" in flagged, "恒真判据必须被标成不可证伪"
    assert any(u["verdict"] == "UNFALSIFIABLE" for u in k["unfalsifiable_criteria"])


def test_kpi_coverage_and_monotonicity():
    """覆盖面 ≥4 门、判据可实例化率 ≥90%、M1 单调性违例 = 0。"""
    ledger = cm.build_ledger(scratch_parent=ROOT / "artifacts" / "m5496")
    k = ledger["kpi"]
    assert k["n_gates"] >= 4, k["gates"]
    assert k["instantiation_rate"] >= 0.90, k
    assert k["m1_monotonic"]["n_violations"] == 0, k["m1_monotonic"]
    seq = k["m1_monotonic"]["sequence"]
    assert seq == sorted(seq), seq
    # 每门都要有 kill_rate 与最弱清单（KPI 不能只有总分）
    for gate, info in k["per_gate"].items():
        assert "kill_rate" in info and info["n_criteria"] >= 1
    assert k["n_weakest"] == len(k["weakest_mutants"])
    # 新结构门（归属/工装）必须明显强于关键词门（这是本单的物理目标）
    assert k["per_gate"]["REPORT-OWNERSHIP"]["kill_rate"] >= 0.9
    assert k["per_gate"]["CLIENT-PAYLOAD"]["kill_rate"] >= 0.9


def test_gate_rejects_new_criterion_without_killer_test(monkeypatch):
    """C11：故意提交一条「无 killer test 的新判据」→ 门禁必须拒（exit 2）。"""
    orphan = cm.Criterion(
        cid="NEW-NO-KILLER", gate="REPORT-OWNERSHIP",
        statement="示例新判据（无杀手测试）",
        source="tests/test_criteria_mutation.py",
        check=lambda s: (False, "never"),
        base=lambda: {},
        mutate={"M3": lambda eps: {}},
        killer_tests=(),
    )
    monkeypatch.setattr(cm, "all_criteria", lambda: CRITERIA + [orphan])
    out = cm.pr_gate(["NEW-NO-KILLER"])
    assert out["accepted"] is False
    assert out["exit_code"] == 2
    assert out["rejected"][0]["reason"] == "NO_KILLER_TEST"


def test_gate_rejects_phantom_killer_test(monkeypatch):
    """killer test 指向不存在的用例 → KILLER_TEST_NOT_FOUND（不许自述式登记）。"""
    phantom = cm.Criterion(
        cid="NEW-PHANTOM", gate="REPORT-OWNERSHIP",
        statement="示例新判据（杀手测试不存在）",
        source="tests/test_criteria_mutation.py",
        check=lambda s: (False, "never"),
        base=lambda: {},
        mutate={"M3": lambda eps: {}},
        killer_tests=("tests/test_criteria_mutation.py::test_does_not_exist[NEW-PHANTOM]",),
    )
    monkeypatch.setattr(cm, "all_criteria", lambda: CRITERIA + [phantom])
    out = cm.pr_gate(["NEW-PHANTOM"])
    assert out["rejected"][0]["reason"] == "KILLER_TEST_NOT_FOUND"


def test_gate_rejects_toothless_killer(monkeypatch):
    """杀手不咬（判据恒放行时它依然绿）→ KILLER_TEST_TOOTHLESS。"""
    # 借口：把 killer test 指到一条与判据无关、永远为真的测试上
    toothless = cm.Criterion(
        cid="RO-MISSION", gate="REPORT-OWNERSHIP",
        statement="示例：杀手测试指向恒真用例",
        source="tests/test_criteria_mutation.py",
        check=lambda s: (False, "never"),
        base=lambda: {},
        mutate={"M3": lambda eps: {}},
        killer_tests=("tests/test_criteria_mutation.py::test_ledger_killer_is_biting",),
    )
    monkeypatch.setattr(cm, "all_criteria", lambda: [toothless])
    out = cm.pr_gate(["RO-MISSION"], run_killer_tests=True)
    assert out["accepted"] is False
    assert out["rejected"][0]["reason"] == "KILLER_TEST_TOOTHLESS", out


def test_ledger_killer_is_biting():
    """恒真夹具：给 toothless 负控用（它不依赖任何判据，因此永远绿）。"""
    assert True


def test_gate_accepts_registered_criterion_with_biting_killer():
    """正控：登记在册的判据，其 killer test 在「判据被打成恒放行」时必须转红。"""
    out = cm.pr_gate(["RO-PNG-MAGIC"], run_killer_tests=True)
    assert out["accepted"] is True, out
    assert out["killer_checks"] and out["killer_checks"][0]["bites"] is True


def test_parser_and_disable_hook():
    """`CRITERIA_MUTATION_DISABLE` 必须真的把判据打成恒放行（破坏性验证的前提）。"""
    import os

    crit = cm.get_criterion("RO-PNG-MAGIC")
    os.environ[cm.DISABLE_ENV] = "RO-PNG-MAGIC"
    try:
        flagged, _ = crit.check(crit.base.__self__ if False else crit.mutate["M2"](None))
        assert flagged is False, "被打成恒放行后仍亮红灯（破坏性验证无效）"
    finally:
        os.environ.pop(cm.DISABLE_ENV, None)