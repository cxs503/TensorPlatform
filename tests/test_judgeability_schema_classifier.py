"""E1 判据可裁决性 schema + 分类器（P151W1-N10）。

判据先于实现，机器可裁：
1. 三维 schema 可序列化且自洽（P1/P2 必填、P3 仅 advisory；模式表覆盖四档）；
2. 合成语料（弱/强各 ≥20 条，标签即真值）分类 F1 ≥ 0.95；
3. 近邻对（near-boundary pair）逐对反向：删掉一维必须由 strong 翻 weak（证明判据不是关键词命中）；
4. 形状边界：全角比较符/科学计数/±/单位字母/口头阈值——按 schema 定义给出确定判定；
5. 判据段切分：任务描述里的数字不得冒充 P1（scope=criteria 与 whole 的差异必须可见）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.quality.judgeability import classifier as C
from backend.quality.judgeability import schema as S

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "quality" / "judgeability" / "synthetic_corpus.json"


def _corpus():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def test_schema_is_complete_and_serializable():
    js = S.as_json_schema()
    keys = {d["key"] for d in js["dimensions"]}
    assert keys == {"P1", "P2", "P3"}
    required = {d["key"] for d in js["dimensions"] if d["required_for_strong"]}
    assert required == {"P1", "P2"}          # 硬维只有 P1/P2，P3 仅 advisory
    assert set(js["gate_modes"]) == {"off", "advisory", "enforce", "block"}
    assert set(js["mode_actions"]) == set(js["gate_modes"])
    # schema 必须可 JSON 往返（报告/审计要内嵌它）
    assert json.loads(json.dumps(js, ensure_ascii=False))["schema"] == S.SCHEMA_ID
    assert "解析解" in js["reference_kinds"] and len(js["reference_kinds"]) == 6


def test_corpus_quota_and_f1_threshold():
    corpus = _corpus()
    labels = [c["label"] for c in corpus["cases"]]
    assert labels.count("strong") >= 20 and labels.count("weak") >= 20
    rows = C.classify_batch(corpus["cases"], scope="whole")
    conf = C.confusion(rows)
    assert conf["f1_positive"] >= 0.95, conf
    assert conf["macro_f1"] >= 0.95, conf
    assert conf["error_ids"] == [], f"误判样例：{conf['error_ids']}"


def test_near_boundary_pairs_flip_on_single_dimension():
    """近邻对：同一措辞删一维 → 判定翻转（strong↔weak），证明判据是结构不是词汇）。"""
    pairs = [("与解析解比较，相对误差 <= 1e-10", "strong"),
             ("与解析解结果一致", "weak"),                      # 删 P1
             ("F1 >= 0.95（实验核对）", "strong"),
             ("F1 >= 0.95", "weak"),                            # 删 P2
             ("误差 ±0.5%（对文献区间）", "strong"),
             ("阈值 0.5%（对文献区间）", "weak")]               # ± → 裸数值
    for text, want in pairs:
        assert C.classify_text(text, scope="whole").verdict == want, text


@pytest.mark.parametrize("text,has_p1", [
    ("误差 ≤ 3%", True),          # 单字符比较符
    ("误差 ≤= 3%", True),         # 比较符 + 等号
    ("误差 ＜ 5%", True),         # 全角
    ("漂移 ±2%", True),           # 正负号形态
    ("残差 == 0", True),          # 等号形态
    ("误差 <= 1e-10", True),      # 科学计数
    ("内存峰值 ≤ 1.2GB", False),  # 单位字母紧跟 → 形状判据不认（已知假阴面，见报告局限）
    ("误差在 3% 以内", False),    # 口头阈值 → 不认（已知假阴面）
    ("F1 ≥ 第 90 百分位", False), # 比较符后非数字
    ("阈值：0.95", False),        # 裸数值无不比较符
])
def test_p1_shape_boundaries(text, has_p1):
    assert bool(C.classify_text(text, scope="whole").dims["P1"]) is has_p1, text


def test_criteria_segment_ignores_prose_numbers():
    """任务描述里的数字不得冒充 P1：整篇扫描与判据段扫描的结论必须不同。"""
    goal = ("【战役 DAG 节点|X1|示例】物理目标：在 Ma 2–10 参数空间内复算 3 个算例。\n"
            "机器验收判据：结果与既有实现一致，效果良好。\n"
            "纪律：worktree 内提交。")
    assert C.classify_text(goal, scope="whole") is not None  # 不抛异常
    j = C.classify_goal(goal)
    assert j.scope == "criteria"
    assert j.verdict == "weak" and not j.dims["P1"] and not j.dims["P2"]
    # 同一文本按整篇扫：描述里的 "2–10" 之类仍不构成阈值，但 scope 不同必须可见
    assert C.classify_text(goal, scope="whole").segment_chars >= j.segment_chars


def test_p2_is_closed_vocabulary():
    assert C.classify_text("与解析方法对比，误差 ≤ 1%", scope="whole").verdict == "weak"
    assert C.classify_text("与解析解对比，误差 ≤ 1%", scope="whole").verdict == "strong"
    for kind in S.REFERENCE_KINDS:
        assert kind in C.classify_text(f"{kind} 对照，误差 <= 1%", scope="whole").dims["P2"][0].text