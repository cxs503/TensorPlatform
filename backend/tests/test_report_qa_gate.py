"""报告 QA 常设门 v2 单测（owner 2026-11-11 FEAT-TK-GOV-INFRA）。

四查 + S1/S2/S3 分级：断言的是**不变量**（分级判据、结构化拒绝、
悬空引用必拒），不是执行路径。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.report_qa_gate import (  # noqa: E402
    check_evidence_first,
    check_traceability,
    extract_anchors,
    grade_report,
    qa_gate_v2,
    title_alignment,
)

VERIFY_OK = {
    "report_id": lambda r: r in (100, 200),
    "path": lambda p: p.startswith("artifacts/ok"),
    "commit": lambda c: len(c) == 40,
}
VERIFY_EMPTY = {}

MD_S3 = """# KCS 阻力对标
## ① 案例介绍
物理背景：KCS 船模阻力。几何参数：L=24.384 m, B=4.0 m, T=1.35 m, Cb=0.67。
## ② 怎么做的
方法：Holtrop-Mennen 经验公式 + 自研 BEM 求解，工具链 TensorBEM。
## ③ 计算结果
新增 KCS 全工况阻力表，见 artifacts/ok/kcs_ct.json。
## ④ 结果图
图1：CT 沿船长分布（对标 ITTC-1957 试验值）。
## ⑤ 注意点
局限：仅 Re=9e6 单工况。
## ⑥ 误差判定
CT 相对误差 1.2%，<3%，与 ITTC-1957 试验一致。
"""


def _report(md=MD_S3, title="KCS 阻力对标", figures=None):
    return {"id": 1, "title": title, "markdown": md,
            "figures": figures if figures is not None else [{"caption": "CT 分布"}]}


def test_s3_external_anchor_reproducible_increment():
    r = qa_gate_v2(_report(), VERIFY_OK)
    assert r["qa_status"] == "passed"
    assert r["grade"] == "S3"
    assert "外部锚" in r["grade_reason"] and "可复现" in r["grade_reason"]


def test_s2_internal_validation_no_external_anchor():
    # 内部验证（解析解）但无外部锚 → S2
    md = """# 内部算例收敛
## ① 案例介绍
物理背景：方腔流。几何参数：L=1.0 m, B=1.0 m。
## ② 怎么做的
方法：自研 LBM D2Q9，工具链 TensorLBM。
## ③ 计算结果
新增方腔 Re=100 速度剖面，见 artifacts/ok/cavity_u.json。
## ④ 结果图
图1：u 剖面（对标解析解）。
## ⑤ 注意点
局限：仅 Re=100。
## ⑥ 误差判定
u 相对误差 0.8%，<3%，与解析解一致。
"""
    r = qa_gate_v2(_report(md=md, title="方腔流收敛"), VERIFY_OK)
    assert r["grade"] == "S2"
    assert "缺外部锚" in r["grade_reason"]


def test_s1_no_validation():
    r = qa_gate_v2(_report(md="# 随便\n今天天气不错。\n", title="随便"), VERIFY_OK)
    assert r["qa_status"] == "rejected"
    assert r["grade"] == "S1"
    assert r["first_cause"].startswith("six_element:")


def test_rejection_reason_is_structured_json():
    r = qa_gate_v2(_report(md="# 随便\n无验证。\n", title="随便"), VERIFY_OK)
    assert r["qa_status"] == "rejected"
    doc = json.loads(r["qa_rejection_reason"])
    assert doc["gate"] == "qa_gate_v2"
    assert doc["first_cause"]
    assert doc["failed_checks"]
    assert doc["grade"] in ("S1", "S2", "S3")


def test_passed_report_has_no_rejection_reason():
    r = qa_gate_v2(_report(), VERIFY_OK)
    assert r["qa_rejection_reason"] is None


def test_dangling_report_ref_fails_traceability():
    md = MD_S3 + "\n参考 report #99999 的结论。\n"
    r = qa_gate_v2(_report(md=md), VERIFY_OK)
    assert r["qa_status"] == "rejected"
    assert "traceability" in r["failed_checks"]
    assert any("99999" in p for p in r["audit"]["trace_problems"])


def test_dangling_artifact_path_fails_traceability():
    md = MD_S3.replace("artifacts/ok/kcs_ct.json", "artifacts/missing/out.json")
    r = qa_gate_v2(_report(md=md), VERIFY_OK)
    assert r["qa_status"] == "rejected"
    assert "traceability" in r["failed_checks"]
    assert any("artifacts/missing/out.json" in p for p in r["audit"]["trace_problems"])


def test_title_misalignment_fails_evidence_first():
    r = qa_gate_v2(_report(title="冰区航行 破冰船 冰阻力 极限强度"), VERIFY_OK)
    assert r["qa_status"] == "rejected"
    assert "evidence_first" in r["failed_checks"]
    assert r["audit"]["checks"]["evidence_first"] is False


def test_title_alignment_short_code_title():
    ok, missing = title_alignment("XVAL-LBM-MOVING-WALL", "moving wall 验证")
    assert ok  # 纯代号题目放宽：≥1 命中


def test_extract_anchors():
    a = extract_anchors(
        "见 report #1234 与 报告 567，工件 runs/exp1/out.json，"
        "commit 0123456789abcdef0123456789abcdef01234567，对标 ITTC-1957。"
    )
    assert 1234 in a["report_ids"] and 567 in a["report_ids"]
    assert "runs/exp1/out.json" in a["artifact_paths"]
    assert "0123456789abcdef0123456789abcdef01234567" in a["commits"]
    assert any("ITTC" in p for p in a["external_anchors"])


def test_grade_reason_written_for_all_grades():
    for md, title in ((MD_S3, "KCS 阻力对标"),
                     ("# 随便\n无验证。\n", "随便")):
        r = qa_gate_v2(_report(md=md, title=title), VERIFY_OK)
        assert r["grade_reason"], (r["grade"], "grade_reason 必须非空")


def test_six_element_reused_not_forked():
    """六要素判据必须复用 report_gate 唯一实现（防 fork 纪律）。"""
    from services import report_gate, report_qa_gate
    assert report_qa_gate.check_report_quality is report_gate.check_report_quality
