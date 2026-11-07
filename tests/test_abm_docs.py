"""ABM 规范族（docs/abm/）结构完整性测试。

战役 plan 143（ABM 建设军）节点 IN59-A0 验收件：
- 8 文件成 PR（ABM-00..07）
- ABM-00 与 owner 原文语义对照表（逐节：owner 原文要点→落库位置）无缺漏

判据全部机器可裁：文件存在性 + 关键条款标记 + 逐节覆盖。
"""
import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs" / "abm"

FILES = {
    "ABM-00": "ABM-00_总纲.md",
    "ABM-01": "ABM-01_攻关方法论.md",
    "ABM-02": "ABM-02_任务分解规范.md",
    "ABM-03": "ABM-03_多Agent协作规范.md",
    "ABM-04": "ABM-04_实验与证据规范.md",
    "ABM-05": "ABM-05_独立审查规范.md",
    "ABM-06": "ABM-06_能力成长规范.md",
    "ABM-07": "ABM-07_成果分级规范.md",
}
SEMANTIC_MAP = "ABM-00_语义对照表.md"


def _read(name: str) -> str:
    p = DOCS / name
    assert p.exists(), f"缺少规范文件: {p}"
    return p.read_text(encoding="utf-8")


def test_all_eight_files_exist():
    """验收件 1：8 文件齐（ABM-00..07）。"""
    missing = [k for k, f in FILES.items() if not (DOCS / f).exists()]
    assert not missing, f"缺文件: {missing}"
    assert (DOCS / SEMANTIC_MAP).exists(), "缺语义对照表"


def test_abm00_seven_required_themes():
    """ABM-00 七项全覆盖：三智能/七环/八能力/五机制/六支架/三原则/三试点。"""
    t = _read(FILES["ABM-00"])
    for marker in [
        "单体智能", "群体智能", "组织智能",          # 三智能判断
        "问题定义", "现状调查", "提出假设", "实施实验", "独立挑战", "结果验收", "知识沉淀",  # 七环
        "问题拆解", "建立基线", "假设驱动", "交叉验证", "对照实验", "失败分析", "分层验收", "知识复用",  # 八能力
        "任务准入", "证据驱动", "独立审查", "动态升级", "能力评价",  # 五机制
        "专业知识包", "标准攻关流程", "专业工具", "专家示范案例", "独立质量审查", "组织知识库",  # 六支架
        "允许失败", "允许分歧", "评最终贡献",          # 三原则
        "TensorLBM", "TensorDEM", "TensorCAD",        # 三试点
    ]:
        assert marker in t, f"ABM-00 缺覆盖项: {marker}"


def test_abm01_seven_rings_and_eight_capabilities():
    """ABM-01：七环操作定义 + 八能力操作定义。"""
    t = _read(FILES["ABM-01"])
    for ring in ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]:
        assert ring in t, f"ABM-01 缺环 {ring}"
    for cap in ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]:
        assert cap in t, f"ABM-01 缺能力 {cap}"
    assert "失败返回" in t, "ABM-01 缺失败返回路径"


def test_abm01_baseline_four_questions_and_hypothesis_five_parts():
    """ABM-01：基线四问 + 假设五件套表格。"""
    t = _read(FILES["ABM-01"])
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        assert q in t, f"ABM-01 基线四问缺 {q}"
    for part in ["理论依据", "代码位置", "证伪实验设计", "修改前后预测", "数据裁决"]:
        assert part in t, f"ABM-01 假设五件套缺: {part}"
    assert "假设被证伪也是有价值的成果" in t


def test_abm02_problem_tree_and_acceptance_table():
    """ABM-02：问题树模板（六层）+ 子问题验收问题表 + 准入门。"""
    t = _read(FILES["ABM-02"])
    for layer in ["M1", "M2", "M3", "M4", "M5", "M6"]:
        assert layer in t, f"ABM-02 问题树缺层 {layer}"
    for field in ["验收问题", "参考值", "容差", "预期 S 级", "负责角色"]:
        assert field in t, f"ABM-02 验收问题表缺字段: {field}"
    assert "准入门" in t and "基线四问" in t


def test_abm03_five_roles_six_dims():
    """ABM-03：五角色职责表 + 挑战者独立判断权 + 六维评价。"""
    t = _read(FILES["ABM-03"])
    for role in ["攻关负责人", "理论研究者", "实现工程师", "挑战者", "独立验证者"]:
        assert role in t, f"ABM-03 缺角色: {role}"
    assert "独立判断权" in t
    for dim in ["正确性", "精度", "稳定性", "计算效率", "可复现性", "适用范围"]:
        assert dim in t, f"ABM-03 六维缺: {dim}"
    assert "不按 PR 先后" in t, "ABM-03 缺『证据定胜负，不按 PR 先后』"


def test_abm04_hypothesis_fields_blind_rule_five_chain():
    """ABM-04：假设登记字段 + 预测盲规则 + 证据五元链。"""
    t = _read(FILES["ABM-04"])
    for field in ["hypothesis_id", "statement", "theory_basis", "code_location",
                  "falsification_design", "prediction_before", "prediction_after",
                  "registered_at", "verdict"]:
        assert field in t, f"ABM-04 假设登记缺字段: {field}"
    assert "先登记后实验" in t and "append-only" in t, "ABM-04 缺预测盲规则"
    for elem in ["理论或文献依据", "代码版本", "实验配置", "原始计算结果", "分析报告"]:
        assert elem in t, f"ABM-04 五元链缺: {elem}"


def test_abm05_s3plus_independent_review_model_family():
    """ABM-05：S3+（L3+）强制独立审查 + 不同模型族。"""
    t = _read(FILES["ABM-05"])
    assert "S3+" in t and "L3+" in t
    assert "不同模型族" in t, "ABM-05 缺不同模型族强制条款"
    assert "不得自批" in t, "ABM-05 缺『执行 Agent 不得自批』"
    assert "硬门" in t


def test_abm06_three_assets_failure_kb():
    """ABM-06：三资产 + 失败知识库字段 + 七指标 + 二期预留。"""
    t = _read(FILES["ABM-06"])
    for asset in ["知识资产", "工具资产", "经验资产"]:
        assert asset in t, f"ABM-06 缺资产: {asset}"
    for field in ["initial_hypothesis", "what_failed", "which_stage",
                  "error_class", "ruled_out_paths", "avoid_next"]:
        assert field in t, f"ABM-06 失败知识库缺字段: {field}"
    for metric in ["首次解决率", "结果有效率", "假设命中率", "缺陷发现率",
                   "复现成功率", "知识贡献率", "攻关贡献度"]:
        assert metric in t, f"ABM-06 七指标缺: {metric}"
    assert "二期" in t and "强弱" in t, "ABM-06 缺强弱传递二期预留"


def test_abm07_l_s_mapping_complete():
    """ABM-07：L≡S 映射表五行齐 + 不立第二标尺声明。"""
    t = _read(FILES["ABM-07"])
    for i in range(1, 6):
        assert f"L{i}" in t and f"S{i}" in t, f"ABM-07 映射表缺 L{i}/S{i}"
    assert "不立第二" in t, "ABM-07 缺『不立第二套标尺』声明"
    assert "SPEC v1.2" in t, "ABM-07 须引 SPEC v1.2 S×E"


def test_semantic_map_covers_all_abm00_sections():
    """验收件 2：对照表逐节覆盖 ABM-00 §1-§11，无缺漏。"""
    abm00 = _read(FILES["ABM-00"])
    sections = set(re.findall(r"^## (\d+)\.", abm00, flags=re.M))
    assert sections == {str(i) for i in range(1, 12)}, f"ABM-00 节结构异常: {sections}"
    smap = _read(SEMANTIC_MAP)
    for i in range(1, 12):
        assert f"§{i} " in smap or f"§{i}｜" in smap or f"| §{i} " in smap, \
            f"对照表未覆盖 ABM-00 §{i}"
    for theme in ["三智能判断", "七环闭环", "八能力", "五机制", "六支架", "三原则", "三试点"]:
        assert theme in smap, f"对照表覆盖矩阵缺: {theme}"
    assert "无缺漏" in smap or "无悬空条款" in smap, "对照表缺无缺漏结论"


def test_semantic_map_discrepancy_registered():
    """对照表须登记 plan ID 勘误（43→143），不改 owner 原文语义。"""
    smap = _read(SEMANTIC_MAP)
    assert "143" in smap and "43" in smap, "对照表缺 plan ID 勘误记录"
    abm00 = _read(FILES["ABM-00"])
    assert "plan 43" in abm00, "ABM-00 owner 原文不得改写（勘误只能追加）"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
