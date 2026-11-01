"""报告语料审计器单测：确定性静态判定 + 六要素/误差判定/欠账分类。

判据（先于实现，可机器裁）：
- 每类缺陷至少一正一负样例，正样例必须命中、负样例必须不误报；
- 同一输入重复分析结果完全一致（可复算）；
- 语料聚合统计与逐篇判定自洽（计数守恒）。
"""
from __future__ import annotations

import json
import unittest

from tools.report_corpus_audit import (
    analyze_report,
    classify_debt_themes,
    corpus_sha256,
    corpus_stats,
    error_verdict,
    extract_debt_text,
    figure_exemption,
    numeric_table_count,
    parse_headings,
    render_markdown,
)

GOOD_REPORT = """# 算例：平板层流阻力对标 Blasius (Re=1e5)

## 1. 案例介绍
物理背景：二维平板层流边界层。几何：L=1.0 m，平板无厚度。
参数表：

| 参数 | 值 | 单位 |
|---|---|---|
| Re | 100000 | - |
| U | 1.0 | m/s |
| nu | 1e-5 | m2/s |

## 2. 方法与流程
工具链：TensorLBM 求解器 + 二阶插值，网格 200x120，步数 20000，收敛判据 1e-8。
流程：初始化 -> 演化 -> 采样壁面切应力 -> 沿板长积分得阻力系数 -> 与 Blasius 解析解比对。
关键设置：松弛时间由 nu 反算，入口为均匀来流，出口零梯度；壁面用半步反弹，
采样窗取后 20% 步数以避开启动瞬态；两次网格加密（200x120 -> 300x180）确认
Cd 变化 0.3%，故认为离散误差小于目标阈值。

## 3. 计算结果

| 量 | 本实现 | Blasius | 相对误差 |
|---|---|---|---|
| Cd | 0.01478 | 0.01480 | 0.14% |

## 4. 结果图
速度云图和 Cd 随 Re 的比较曲线见 figures。

## 5. 注意点
局限：网格 y+ 未做严格解析，极高 Re 不适用；适用范围 Re<5e6。
踩过的坑：出口若用零梯度会反射压力波，需加海绵层；启动瞬态前 2000 步必须丢弃。
后续：把壁面模型换成对数律壁函数后重跑高 Re 段。

## 6. 误差判定
关键量 Cd 对 Blasius 解析解的相对误差 0.14%，是否<3%：是。
"""


def make_report(**over) -> str:
    md = GOOD_REPORT
    for k, v in over.items():
        md = md.replace(k, v)
    return md


class TestPrimitives(unittest.TestCase):
    def test_parse_headings(self):
        hs = parse_headings(GOOD_REPORT)
        self.assertEqual(len(hs), 7)  # 一级 + 六节
        self.assertEqual(hs[0].level, 1)
        self.assertEqual(hs[1].line, 3)

    def test_numeric_table_count(self):
        self.assertEqual(numeric_table_count(GOOD_REPORT), 2)
        self.assertEqual(numeric_table_count("无表格文本"), 0)
        # 纯文字表格（无数值）不计
        self.assertEqual(numeric_table_count("| a | b |\n|---|---|\n| x | y |"), 0)

    def test_error_verdict_explicit(self):
        state, ev = error_verdict(GOOD_REPORT)
        self.assertEqual(state, "explicit")
        self.assertIn("L", ev)

    def test_error_verdict_no_baseline(self):
        md = "结果见下。因无可用基准数据且缺少实验数据，故不做误差判定。"
        state, ev = error_verdict(md)
        self.assertEqual(state, "no_baseline_declared")
        self.assertIn("基准", ev)

    def test_error_verdict_ambiguous(self):
        md = "计算结果与文献接近，误差较小，可供参考。"
        self.assertEqual(error_verdict(md)[0], "ambiguous")

    def test_error_verdict_missing(self):
        self.assertEqual(error_verdict("只有定性描述的一篇报告。")[0], "missing")

    def test_figure_exemption(self):
        self.assertIsNone(figure_exemption(GOOD_REPORT))
        ex = figure_exemption("本单为纯后端任务，无图（理由：无场量输出，仅接口改造）。")
        self.assertIsNotNone(ex)
        self.assertIn("纯后端任务", ex)

    def test_debt_theme_rules(self):
        themes = classify_debt_themes("局限：网格未做 y+ 敏感性分析；缺少实验数据对标；忽略自由面。")
        self.assertIn("网格/收敛性", themes)
        self.assertIn("实验/文献基准缺失", themes)
        self.assertIn("模型简化/忽略项", themes)
        self.assertEqual(classify_debt_themes(""), [])

    def test_extract_debt_text(self):
        txt = extract_debt_text(GOOD_REPORT, parse_headings(GOOD_REPORT))
        self.assertIn("y+", txt)
        self.assertNotIn("Blasius", txt)


class TestAnalyzeVerdict(unittest.TestCase):
    def test_good_report_passes(self):
        v = analyze_report(GOOD_REPORT, report_id=1, title="算例 1e5", figures=2)
        self.assertTrue(v.six_element_complete, v.element_evidence)
        self.assertEqual(v.verdict, "PASS", [d.code for d in v.defects])
        self.assertEqual(v.numeric_tables, 2)

    def test_missing_figure_flagged(self):
        v = analyze_report(GOOD_REPORT, figures=0)
        codes = {d.code for d in v.defects}
        self.assertIn("FIGURE_MISSING_UNDECLARED", codes)
        self.assertEqual(v.verdict, "RESUBMIT")

    def test_declared_figure_exemption_ok(self):
        md = GOOD_REPORT + "\n本单为纯文档任务，无图（理由：仅整理规范条款，无场量）。\n"
        v = analyze_report(md, figures=0)
        self.assertEqual(v.elements["figures"], "ok")
        self.assertNotIn("FIGURE_MISSING_UNDECLARED", {d.code for d in v.defects})

    def test_weak_exemption_is_minor(self):
        md = GOOD_REPORT + "\n无图\n"
        v = analyze_report(md, figures=0)
        codes = {d.code for d in v.defects}
        self.assertIn("FIGURE_EXEMPT_UNDECLARED_REASON", codes)
        self.assertEqual(v.verdict, "MINOR")

    def test_missing_error_verdict_flagged(self):
        # 保留"误差"字样但去掉显式判定
        md = GOOD_REPORT.replace("关键量 Cd 对 Blasius 解析解的相对误差 0.14%，是否<3%：是。",
                                 "误差情况良好。")
        v = analyze_report(md, title="算例 1e5", figures=2)
        self.assertEqual(v.error_verdict, "ambiguous")
        self.assertIn("ERROR_VERDICT_AMBIGUOUS", {d.code for d in v.defects})
        self.assertEqual(v.verdict, "RESUBMIT")

    def test_results_without_table_flagged(self):
        md = GOOD_REPORT.replace(
            "| 量 | 本实现 | Blasius | 相对误差 |\n|---|---|---|---|\n| Cd | 0.01478 | 0.01480 | 0.14% |",
            "阻力系数与解析解吻合。")
        v = analyze_report(md, title="算例 1e5", figures=2)
        self.assertIn("RESULTS_NO_NUMERIC_TABLE", {d.code for d in v.defects})

    def test_missing_caveats_flagged(self):
        md = GOOD_REPORT.replace("## 5. 注意点", "## 5. 其他")
        v = analyze_report(md, title="算例 1e5", figures=2)
        self.assertIn("MISSING_ELEMENT_CAVEATS", {d.code for d in v.defects})

    def test_no_headings_flagged(self):
        v = analyze_report("一句话结论：都很好。", figures=1)
        codes = {d.code for d in v.defects}
        self.assertIn("MISSING_HEADINGS", codes)
        self.assertIn("THIN_MARKDOWN", codes)
        self.assertEqual(v.verdict, "RESUBMIT")

    def test_determinism(self):
        a = json.dumps(analyze_report(GOOD_REPORT, report_id=7, figures=2).to_dict(), sort_keys=True)
        b = json.dumps(analyze_report(GOOD_REPORT, report_id=7, figures=2).to_dict(), sort_keys=True)
        self.assertEqual(a, b)


class TestCorpusStats(unittest.TestCase):
    def setUp(self):
        self.vs = [
            analyze_report(GOOD_REPORT, report_id=1, title="a 1", figures=2),
            analyze_report(GOOD_REPORT, report_id=2, title="b 2", figures=0),          # RESUBMIT: 图缺
            analyze_report("## 结果\n无表格", report_id=3, title="c 3", figures=0),     # RESUBMIT 多项
        ]
        self.stats = corpus_stats(self.vs)

    def test_counts_conserved(self):
        s = self.stats
        self.assertEqual(s["n_reports"], 3)
        self.assertEqual(sum(s["verdict_counts"].values()), 3)
        self.assertEqual(s["six_element_complete"], 1)

    def test_rates_in_range(self):
        for k, r in self.stats["element_ok_rate"].items():
            self.assertGreaterEqual(r, 0.0, k)
            self.assertLessEqual(r, 1.0, k)

    def test_defect_counts_match_verdicts(self):
        manual = sum(1 for v in self.vs for d in v.defects if d.code == "FIGURE_MISSING_UNDECLARED")
        self.assertEqual(self.stats["defect_counts"].get("FIGURE_MISSING_UNDECLARED", 0), manual)

    def test_empty_corpus(self):
        self.assertEqual(corpus_stats([])["n_reports"], 0)

    def test_sha_is_order_sensitive_and_stable(self):
        rows = [{"id": 1, "markdown": "x"}, {"id": 2, "markdown": "y"}]
        self.assertEqual(corpus_sha256(rows), corpus_sha256(rows))
        self.assertNotEqual(corpus_sha256(rows), corpus_sha256(list(reversed(rows))))

    def test_render_markdown_contains_tables(self):
        md = render_markdown(self.stats, self.vs, sha="abc", source="test")
        self.assertIn("六要素覆盖率", md)
        self.assertIn("缺陷类别频次", md)
        self.assertIn("验证欠账主题", md)


if __name__ == "__main__":
    unittest.main()