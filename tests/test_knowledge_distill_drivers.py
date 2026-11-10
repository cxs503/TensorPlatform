"""m9107 验收驱动与报告生成的冒烟测试（合成夹具，零域词）。

被测：``tools/knowledge_distill/m9107_acceptance.py``（`--skip-real --skip-figures`
路径）与 ``tools/knowledge_distill/m9107_report.py``（读已提交的运行产物）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.knowledge_distill import m9107_acceptance as driver  # noqa: E402
from tools.knowledge_distill import m9107_report as report  # noqa: E402

RUN = ROOT / "runs" / "m9107"


def test_driver_synthetic_only(tmp_path: Path) -> None:
    code = driver.main(["--skip-real", "--skip-figures", "--out", str(tmp_path / "run")])
    assert code == 0
    acc = json.loads((tmp_path / "run" / "acceptance.json").read_text(encoding="utf-8"))
    assert acc["synthetic"]["acceptance"]["all_ok"] is True
    assert acc["synthetic"]["acceptance"]["d_seed_yield"]["value"] >= 3
    assert acc["real"]["skipped"] is True
    assert acc["synthetic_calibration"]["eps_sweep"]
    assert acc["synthetic_calibration"]["similarity_margin"]["within_pairs"] > 0
    # 合成链产物也落盘
    assert (tmp_path / "run" / "synthetic" / "seeds.json").is_file()


def test_driver_eps_sweep_monotone_assignment() -> None:
    rows = driver.eps_sweep(driver.REPORTS, driver.DistillParams(),
                            [0.4, 0.6, 0.8], truth=driver.TRUTH)
    assert len(rows) == 3
    assert [r["eps"] for r in rows] == sorted(r["eps"] for r in rows)
    assert rows[-1]["assigned"] >= rows[0]["assigned"]


def test_spotcheck_sample_and_figures(tmp_path: Path) -> None:
    from tools.knowledge_distill.pipeline import distill
    from tools.knowledge_distill.extract import dedupe_items, parse_reports

    syn = distill(driver.REPORTS, driver.REFS, driver.DistillParams(),
                  truth_by_item=driver.TRUTH)
    items = dedupe_items(parse_reports(driver.REPORTS, driver.DistillParams())["items"])["items"]
    spot = driver.spotcheck_sample({"clustering": syn["clustering"],
                                    "clusters_text": [i["text"] for i in items]}, k=3)
    assert spot["sampled"] == 3
    assert all(c["members"] for c in spot["clusters"])
    assert spot["verdicts"] == {}

    margin = {"within": [0.4, 0.3], "cross": [0.01, 0.02]}
    made = driver.figures(syn, syn, syn["clustering"] and driver.eps_sweep(
        driver.REPORTS, driver.DistillParams(), [0.5, 0.7], truth=driver.TRUTH),
        [], margin, tmp_path / "fig")
    assert len(made) == 5
    for fig in made:
        assert (tmp_path / "fig" / Path(fig["path"]).name).is_file()
        assert len(fig["sha256"]) == 64


def test_two_tier_equivalence_on_fixture(tmp_path: Path) -> None:
    eq = driver.two_tier_equivalence(driver.REPORTS, driver.REFS,
                                     driver.DistillParams())
    assert eq["queries"] > 0
    assert eq["exact_matches"] == eq["queries"]
    assert eq["max_abs_diff"] == 0.0


def test_report_build_reads_artifacts(tmp_path: Path) -> None:
    if not (RUN / "acceptance.json").exists():
        pytest.skip("运行产物未提交")
    md_path = tmp_path / "r.md"
    payload_path = tmp_path / "p.json"
    md, payload = report.build(md_path, payload_path)
    assert md_path.is_file() and payload_path.is_file()
    for section in ("## 0 摘要", "## 2 怎么做的", "## 3 计算结果", "## 4 结果图",
                    "## 5 注意点", "## 6 误差判定", "## 7 审计链"):
        assert section in md
    assert payload["mission_id"] == 9107
    assert payload["figures"] and all(f["png_base64"] for f in payload["figures"])
    assert "TBD" not in md and "TODO" not in md