# -*- coding: utf-8 -*-
"""报告证据闸门 v2（utils/report_gate.py）的判据测试。

设计要点：**直接 import 生产模块**，不做函数拷贝——`tests/test_report_gate_standalone.py`
是 v1 的拷贝式测试，它只能验证"拷贝还在"，无法发现闸门漂移，这正是本单要清掉的欠账之一。

覆盖：真实报告通过 / 关键词堆砌被拒 / 图件取证（缺文件·非 PNG·过小·正常）/
无图声明的两种情形 / 误差判定的四种情形 / markdown 表格解析。
"""
from __future__ import annotations

import struct
import zlib

import pytest

from utils.report_gate import GATE_VERSION, PNG_MAGIC, _tables, evaluate


def _png(width: int = 64, height: int = 64, pad: int = 2048) -> bytes:
    """造一张合法 PNG（无第三方依赖）：IHDR + 1 行 IDAT + IEND。"""
    raw = bytearray()
    for _ in range(height):
        raw.append(0)
        raw.extend(b"\x00" * (width * 3))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    body = zlib.compress(bytes(raw), 6)
    png = PNG_MAGIC + chunk(b"IHDR", ihdr) + chunk(b"IDAT", body) + chunk(b"IEND", b"")
    return png + b"\x00" * pad


GOOD_MD = """# 某散货船总阻力复核（mission X）

## ① 案例介绍
物理背景：目标船为 82 000 DWT 散货船，波浪中低速航行。几何参数表如下：

| 参数 | 数值 | 单位 |
|---|---|---|
| Lpp | 228.5 | m |
| B | 32.26 | m |
| T | 14.0 | m |
| Cb | 0.845 | – |

## ② 怎么做的
流程：先用 Holtrop-Mennen 估算，再用 ITTC-78 摩擦线复核，工具链 python 3.11 / pytest 9.2，
步数 200，收敛残差 <1e-8。

| 项 | 值 | 单位 |
|---|---|---|
| Rt (design) | 1180.2 | kN |
| Rt (trial) | 1320.7 | kN |

## ③ 计算结果
RT 在 14 kn 时为 1180.2 kN，有效功率 8502 kW，较基线 +3.2%。

| 速度 kn | Rt kN | 误差 % |
|---|---|---|
| 12 | 820.4 | 1.8 |
| 14 | 1180.2 | 2.4 |

## ④ 结果图
图 1 给出阻力-速度曲线，与文献参考曲线对比。

## ⑤ 注意点
局限：仅适用于 Fr 0.10–0.22；未做浅水修正；未覆盖破浪状态。

## ⑥ 误差判定
关键量 Rt vs ITTC-78 参考值，逐项如下：

| 关键量 | 基准 | 误差 | <3%？ |
|---|---|---|---|
| Rt(14kn) | ITTC-78 参考 | 2.4 % | 是 |
| Rt(12kn) | ITTC-78 参考 | 1.8 % | 是 |

结论：是否 <3% —— 是（最大 2.4%）。
"""

STUFFED_MD = """# 报告
物理 几何 参数 背景 方法 流程 验证 对标 文献 图 曲线 局限 注意 范围 展望
"""


def test_good_report_passes_and_shape_matches_v1_contract():
    res = evaluate(GOOD_MD, [{"name": "rt_curve.png", "caption": "阻力-速度曲线与文献对比"}])
    assert res["gate_version"] == GATE_VERSION
    assert res["passed"] is True, res["issues"]
    assert res["score"] == 1.0
    # v1 兼容字段仍在（下游 qa_status 只读这两个）
    assert set(["passed", "issues", "checks"]).issubset(res)


def test_keyword_stuffed_report_is_rejected():
    """v1 会满分通过的纯词表文本，v2 必须多项不通过。"""
    res = evaluate(STUFFED_MD, [])
    assert res["passed"] is False
    assert "v2_missing_numeric_results" in res["issues"]
    assert "v2_missing_error_judgment" in res["issues"]
    assert "keyword_only_physics_statement" in res["issues"]
    assert res["score"] < 0.5


def test_physics_keywords_without_numbers_do_not_count():
    """要素必须有"带数字"证据：只有背景词、无任何几何数值的段落不得分。"""
    figs = [{"name": "a.png", "caption": "阻力曲线与文献对比图"}]
    # 真实报告的 ① 段有 82 000 DWT / Lpp 228.5 → 得分
    assert evaluate(GOOD_MD, figs)["checks"]["physics_statement"] is True
    # 把 ① 段替换成纯文字（无数字）→ 不得分，且记 keyword_only
    weak = GOOD_MD.replace(
        "物理背景：目标船为 82 000 DWT 散货船，波浪中低速航行。几何参数表如下：\n\n"
        "| 参数 | 数值 | 单位 |\n|---|---|---|\n| Lpp | 228.5 | m |\n| B | 32.26 | m |\n"
        "| T | 14.0 | m |\n| Cb | 0.845 | – |",
        "物理背景：目标船为散货船，波浪中低速航行，几何参数冗长。")
    res = evaluate(weak, figs)
    assert res["checks"]["physics_statement"] is False
    assert "keyword_only_physics_statement" in res["issues"]


def test_error_judgment_requires_threshold_and_verdict_table():
    """⑥ 误差判定必须三件齐备：显式阈值语句 + 误差语境里的百分比 + 是/否 判决。"""
    figs = [{"name": "a.png", "caption": "阻力曲线与文献对比图"}]

    # 基底：去掉 ③ 段里与误差无关的 "%"，避免它们冒充 ⑥ 的误差证据
    base = GOOD_MD.replace("| 速度 kn | Rt kN | 误差 % |", "| 速度 kn | Rt kN | 备注 |").replace(
        "较基线 +3.2%", "较基线略高")

    def wrap(section6: str) -> str:
        return base.split("## ⑥ 误差判定")[0] + "## ⑥ 误差判定\n" + section6

    ok = evaluate(GOOD_MD, figs)["checks"]["error_judgment"]
    assert ok is True

    # (a) 无显式阈值语句
    no_thresh = wrap(
        "关键量 Rt vs ITTC-78 参考，误差 2.4%，达标。\n\n"
        "| 关键量 | 基准 | 误差 | 评定 |\n|---|---|---|---|\n"
        "| Rt(14kn) | ITTC-78 | 2.4 % | 是 |\n| Rt(12kn) | ITTC-78 | 1.8 % | 是 |\n"
    )
    res = evaluate(no_thresh, figs)
    assert res["checks"]["error_judgment"] is False
    assert "v2_missing_error_judgment" in res["issues"]

    # (b) 有阈值语句，但误差数值被口语化（无百分比数字）
    no_numbers = wrap(
        "结论：是否 <3% —— 是。\n\n"
        "| 关键量 | 基准 | 误差 | <3%？ |\n|---|---|---|---|\n"
        "| Rt(14kn) | ITTC-78 | 很小 | 是 |\n| Rt(12kn) | ITTC-78 | 也小 | 是 |\n"
    )
    assert evaluate(no_numbers, figs)["checks"]["error_judgment"] is False

    # (c) 阈值 + 误差数字齐备，但全篇没有任何是/否 判决
    no_verdict = wrap(
        "关键量 Rt 误差 2.4%，另 1.8%。\n\n"
        "| 关键量 | 基准 | 误差 | 备注 |\n|---|---|---|---|\n"
        "| Rt(14kn) | ITTC-78 | 2.4 % | 待定 |\n| Rt(12kn) | ITTC-78 | 1.8 % | 待定 |\n"
    ).replace("达标", "待定")
    res = evaluate(no_verdict, figs)
    assert res["checks"]["error_judgment"] is False

def test_numeric_results_fall_back_to_units():
    md = GOOD_MD
    # 掐掉所有表格 → 依赖正文带单位数值（≥3 处）仍应通过
    lines = [ln for ln in md.splitlines() if not ln.strip().startswith("|")]
    res = evaluate("\n".join(lines), [{"name": "a.png", "caption": "阻力曲线与文献对比图"}])
    assert res["checks"]["numeric_results"] is True


def test_no_figure_requires_explicit_justification():
    md_no_why = GOOD_MD
    ok = evaluate(md_no_why, [])
    assert ok["checks"]["figures"] is False
    assert "missing_figures" in ok["issues"]
    just = GOOD_MD + "\n本单为纯文档/后端任务，无图（不涉及场量计算）。\n"
    assert evaluate(just, [])["checks"]["figures"] is True


def test_figure_probe_rejects_missing_and_bogus_payloads():
    figs = [{"name": "rt.png", "caption": "阻力-速度曲线与文献对比"}]
    good = _png()

    def probe_ok(_rid: int) -> bytes:
        return good

    def probe_missing(_rid: int) -> bytes:
        raise FileNotFoundError

    assert evaluate(GOOD_MD, figs, figure_probe=probe_ok, report_id=7)["checks"]["figure_evidence"] is True
    bad = evaluate(GOOD_MD, figs, figure_probe=probe_missing, report_id=7)
    assert bad["passed"] is False and any(i.startswith("v2_figure_unreadable") for i in bad["issues"])

    not_png = evaluate(GOOD_MD, figs, figure_probe=lambda _r: b"<svg/>" * 400, report_id=7)
    assert not_png["passed"] is False and any(i.startswith("v2_figure_not_png") for i in not_png["issues"])

    tiny = evaluate(GOOD_MD, figs, figure_probe=lambda _r: good[:200], report_id=7)
    assert tiny["passed"] is False


def test_thin_caption_is_flagged():
    res = evaluate(GOOD_MD, [{"name": "rt.png", "caption": "图"}], figure_probe=lambda _r: _png(), report_id=1)
    assert res["passed"] is False
    assert "v2_figure_caption_thin:rt.png" in res["issues"]


def test_table_parser_ignores_separator_rows():
    md = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
    tbl = _tables(md)
    assert len(tbl) == 1 and tbl[0][0] == ["a", "b"] and len(tbl[0]) == 3


@pytest.mark.parametrize("md", ["", "   ", "无内容"])
def test_empty_markdown_never_passes(md):
    assert evaluate(md, [])["passed"] is False