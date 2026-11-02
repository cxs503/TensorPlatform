"""m1870 — 判据资产可复现性守卫（gate-asset reproducibility guard）

背景：`tools/ref_probe_8001.py` 把平台回答裁成 PASS/FAIL/WITHHELD，是船舶域
唯一的机器可裁判据资产。该工具依赖 `artifacts/ref/anchors/*.json` 参考锚点。
一旦锚点被 CI/门禁过滤（真实发生过：commit 6d9d1883 加入的 4 个锚点未进 HEAD），
每个子系统探针都会抛 FileNotFoundError，被记成 `ERROR` —— **崩溃被当成证据**，
门禁整体失去区分「已修好」与「坏掉了」的能力。

本测试把这三条钉成机器判据：
  G1 锚点必须在位（缺失则整个门禁不可复现）；
  G2 判据工件里不得出现 `probe_crash`（崩溃 ≠ 判决）；
  G3 证据级聚合不得在平台不可达时给 PASS/REAL（不可达不得冒充通过）。
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(ROOT, "tools", "ref_probe_8001.py")
ANCHOR_DIR = os.path.join(ROOT, "artifacts", "ref", "anchors")
VERDICT_ARTIFACT = os.path.join(ROOT, "artifacts", "ship_endpoint_verdicts.json")
PROBE_ARTIFACT = os.path.join(ROOT, "artifacts", "ref", "probe_verdicts_r1.json")

# 探针读取的 4 个参考锚点（m1484 / 6d9d1883 建立）
REQUIRED_ANCHORS = (
    "spectra_ittc_issc.json",
    "propeller_wageningen_b4_70.json",
    "resistance_kvlcc2_kcs.json",
    "b_series_coefficients_crosscheck.json",
)


def _load_probe():
    spec = importlib.util.spec_from_file_location("ref_probe_8001", PROBE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ref_probe_8001"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    if not os.path.exists(PROBE):
        pytest.skip("probe tool absent")
    return _load_probe()


# --- G1：锚点在位 ---------------------------------------------------------
def test_G1_anchor_files_present():
    missing = [n for n in REQUIRED_ANCHORS
               if not os.path.exists(os.path.join(ANCHOR_DIR, n))]
    assert not missing, (
        "参考锚点缺失 → 探针会把崩溃记成 ERROR：%s" % missing)


def test_G1_anchor_files_are_json():
    for name in REQUIRED_ANCHORS:
        path = os.path.join(ANCHOR_DIR, name)
        if not os.path.exists(path):
            pytest.skip("anchor absent, covered by G1 presence test")
        with open(path, encoding="utf-8") as fh:
            json.load(fh)  # 必须可解析，否则探针同样崩


def test_G1_anchor_dir_wired_into_probe(probe):
    """探针的 ANCHOR_DIR 必须指向本仓 artifacts/ref/anchors（不能指到别处）。"""
    assert os.path.abspath(probe.ANCHOR_DIR) == os.path.abspath(ANCHOR_DIR)


# --- G2：崩溃不是证据 -----------------------------------------------------
def test_G2_no_probe_crash_in_verdict_artifact():
    if not os.path.exists(PROBE_ARTIFACT):
        pytest.skip("probe artifact not regenerated in this worktree")
    doc = json.load(open(PROBE_ARTIFACT, encoding="utf-8"))
    crash = [r for r in doc.get("verdicts", [])
             if str(r.get("error") or "").startswith("probe_crash")]
    assert not crash, (
        "判据工件含 %d 条 probe_crash（崩溃被当成判决）：%s"
        % (len(crash), [(r.get("subsystem"), r.get("error")) for r in crash[:3]]))


def test_G2_error_verdict_requires_recorded_reason():
    if not os.path.exists(PROBE_ARTIFACT):
        pytest.skip("probe artifact not regenerated in this worktree")
    doc = json.load(open(PROBE_ARTIFACT, encoding="utf-8"))
    bad = [r for r in doc.get("verdicts", [])
           if r.get("verdict") == "ERROR" and not r.get("error")]
    assert not bad, "ERROR 记录必须带 error 原因（便于归因），否则不可审计"


# --- G3：不可达不得冒充通过 ----------------------------------------------
def test_G3_verdict_document_never_passes_when_unreachable():
    if not os.path.exists(VERDICT_ARTIFACT):
        pytest.skip("verdict artifact absent")
    doc = json.load(open(VERDICT_ARTIFACT, encoding="utf-8"))
    if doc.get("platform_reachable", True):
        pytest.skip("platform was reachable when the artifact was written")
    offenders = [r for r in doc.get("records", [])
                 if r.get("verdict") in ("PASS", "REAL")]
    assert not offenders, "平台不可达却给出 PASS/REAL：%s" % offenders


def test_G3_self_check_recorded():
    if not os.path.exists(VERDICT_ARTIFACT):
        pytest.skip("verdict artifact absent")
    doc = json.load(open(VERDICT_ARTIFACT, encoding="utf-8"))
    sc = doc.get("self_check")
    assert sc is not None, "判据工件未记录 self_check（结论不可自证）"
    text = json.dumps(sc, ensure_ascii=False).upper()
    assert "FAIL" not in text and "MISMATCH" not in text, \
        "判据自检报错（工件与证据不一致）：%s" % sc


# --- 判据内核：常数表指纹与 RAO 依赖性（负向控制） ------------------------
def test_hardcoded_table_fingerprint_hits_known_table(probe):
    table = probe.KNOWN_BEM_RAO_TABLE["heave"]
    assert probe.constant_table_fingerprint(list(table), table)["hit"] is True


def test_hardcoded_table_fingerprint_misses_resolved_curve(probe):
    """20 点频率分辨曲线不得被判为硬编码表（防假阳）。"""
    table = list(probe.KNOWN_BEM_RAO_TABLE["heave"])
    resolved = [v * (1.0 + 0.05 * i) for i, v in enumerate(table * 4)][:20]
    assert probe.constant_table_fingerprint(resolved, table)["hit"] is False


def test_rao_dependence_rejects_identical_curves(probe):
    same = {"hull_corr": 1.0, "hull_max_rel": 0.0, "peak_shift_rel": 0.0,
            "omega_changed": False}
    assert probe.rao_dependence_ok(same) is False


def test_rao_dependence_accepts_frequency_response(probe):
    dep = {"hull_corr": 0.5, "hull_max_rel": 0.4, "peak_shift_rel": 0.0,
           "omega_changed": True}
    assert probe.rao_dependence_ok(dep) is True


def test_mock_table_maps_to_mock_evidence_level(probe):
    """C4/C5 修好但 C6 仍是常数表 → 证据级必须仍是 MOCK，不得被聚合洗白。"""
    assert probe.adjudicate_rao_from_criteria(probe.PASS, probe.PASS,
                                              probe.FAIL) == "MOCK"
