"""m5217 杂交重组与知识回流 — 验收测试

判据：
1. PLAYBOOK条目 ≥ 3
2. pytest用例 ≥ 6
3. 重组goal含双亲要素可溯
4. S×E评价在账
"""
import os
import json
import pytest

WORKTREE = '/root/tp-agent/work/m5217'
DOCS_DIR = os.path.join(WORKTREE, 'docs')
PLAYBOOKS_DIR = os.path.join(DOCS_DIR, 'playbooks')
ARTIFACTS_DIR = os.path.join(WORKTREE, 'artifacts')


# ========== 测试1: PLAYBOOK条目数量 ==========
class TestPlaybookCount:
    """验证PLAYBOOK条目数量≥3"""

    def test_pb_count_gte_3(self):
        """PLAYBOOK条目数≥3"""
        pb_files = [f for f in os.listdir(PLAYBOOKS_DIR) if f.startswith('PB-01')]
        assert len(pb_files) >= 3, f"PLAYBOOK条目数{len(pb_files)} < 3"

    def test_pb_files_exist(self):
        """PB-010, PB-011, PB-012 均存在"""
        expected = ['PB-010-evaluation-drift-protection.md',
                     'PB-011-truth-sql-verification.md',
                     'PB-012-batch-execution-pattern.md']
        for pb in expected:
            path = os.path.join(PLAYBOOKS_DIR, pb)
            assert os.path.exists(path), f"PLAYBOOK {pb} 不存在"


# ========== 测试2: PLAYBOOK内容质量 ==========
class TestPlaybookQuality:
    """验证PLAYBOOK条目内容质量"""

    def test_pb_has_symptom(self):
        """每个PLAYBOOK都有症状描述"""
        for pb_file in os.listdir(PLAYBOOKS_DIR):
            if pb_file.startswith('PB-01'):
                path = os.path.join(PLAYBOOKS_DIR, pb_file)
                content = open(path).read()
                assert '症状' in content, f"{pb_file} 缺少症状描述"

    def test_pb_has_root_cause(self):
        """每个PLAYBOOK都有根因分析"""
        for pb_file in os.listdir(PLAYBOOKS_DIR):
            if pb_file.startswith('PB-01'):
                path = os.path.join(PLAYBOOKS_DIR, pb_file)
                content = open(path).read()
                assert '根因' in content, f"{pb_file} 缺少根因分析"

    def test_pb_has_rule(self):
        """每个PLAYBOOK都有设计规则"""
        for pb_file in os.listdir(PLAYBOOKS_DIR):
            if pb_file.startswith('PB-01'):
                path = os.path.join(PLAYBOOKS_DIR, pb_file)
                content = open(path).read()
                assert '设计规则' in content, f"{pb_file} 缺少设计规则"


# ========== 测试3: 重组goal双亲要素可溯 ==========
class TestRecombinationTraceability:
    """验证重组goal含上代双亲要素可溯"""

    def test_report_exists(self):
        """主报告存在"""
        report_path = os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')
        assert os.path.exists(report_path), "主报告不存在"

    def test_report_has_parent_refs(self):
        """报告引用了上代赢家"""
        report_path = os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')
        content = open(report_path).read()
        parent_missions = ['m5180', 'm5182', 'm642', 'm5023', 'm5018']
        found = sum(1 for m in parent_missions if m in content)
        assert found >= 3, f"报告仅引用{found}个上代赢家，应≥3"


# ========== 测试4: S×E评价在账 ==========
class TestSxEEvaluation:
    """验证S×E赛级评价在账"""

    def test_se_in_report(self):
        """S×E评价在主报告中"""
        report_path = os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')
        content = open(report_path).read()
        assert 'S×E' in content or 'SxE' in content or 'S E' in content, \
            "报告中无S×E评价"

    def test_se_has_s_grade(self):
        """S×E评价包含S级"""
        report_path = os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')
        content = open(report_path).read()
        assert 'S3' in content or 'S2' in content or 'S1' in content, \
            "S×E评价无S级"

    def test_se_has_e_grade(self):
        """S×E评价包含E级"""
        report_path = os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')
        content = open(report_path).read()
        assert 'E2' in content or 'E1' in content or 'E0' in content, \
            "S×E评价无E级"


# ========== 测试5: 报告六要素 ==========
class TestReportSixElements:
    """验证报告包含六要素"""

    def test_has_case_intro(self):
        """案例介绍"""
        content = open(os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')).read()
        assert '案例介绍' in content or '物理背景' in content, "缺少案例介绍"

    def test_has_method(self):
        """方法"""
        content = open(os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')).read()
        assert '怎么做的' in content or '方法' in content, "缺少方法描述"

    def test_has_results(self):
        """计算结果"""
        content = open(os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')).read()
        assert '计算结果' in content or '结果' in content, "缺少计算结果"


# ========== 测试6: 知识回流架构 ==========
class TestKnowledgeFlow:
    """验证知识回流架构"""

    def test_extraction_phase(self):
        """提取阶段存在"""
        content = open(os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')).read()
        assert '提取' in content or 'Extraction' in content, "缺少提取阶段"

    def test_recombination_phase(self):
        """重组阶段存在"""
        content = open(os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')).read()
        assert '重组' in content or 'Recombination' in content, "缺少重组阶段"

    def test_distillation_phase(self):
        """蒸馏阶段存在"""
        content = open(os.path.join(DOCS_DIR, 'm5217_hybrid_recombination.md')).read()
        assert '蒸馏' in content or 'Distillation' in content, "缺少蒸馏阶段"
