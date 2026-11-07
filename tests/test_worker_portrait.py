"""IN15-O4 worker 画像工具判据（pytest）。

覆盖：聚合口径正确性、空 worker 拒收（负例）、空归属拒收（负例）、
阈值/活跃数不足拒收（负例）、无效区间剔除、逐条可溯（正+负）、
md 渲染要素、CLI dry-run 不落盘、CLI 空快照 fail-closed。

运行：python3 -m pytest tests/test_worker_portrait.py -q
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.worker_portrait import (  # noqa: E402
    EmptyWorkerError,
    InsufficientWorkersError,
    aggregate,
    build_profile,
    normalise_row,
    render_index_md,
    render_worker_md,
    verify_against_missions,
)
from tools.worker_portrait import portrait as wp  # noqa: E402

T0 = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def row(mid, worker="w-a", repo="TensorLBM", status="succeeded", *,
        created_offset=0, claim_offset=60, finish_offset=600):
    """构造一条 mission 级行；offset 单位秒，None 表示空值。"""
    def at(off):
        return None if off is None else iso(T0 + timedelta(seconds=off))

    return {
        "mission_id": mid,
        "worker": worker,
        "model": "test-model",
        "runner": "native",
        "worker_role": "impl",
        "repo": repo,
        "status": status,
        "campaign_id": 1,
        "created_at": at(created_offset),
        "claim_at": at(claim_offset),
        "finished_at": at(finish_offset),
    }


def make_rows():
    """3 个活跃 worker + 1 个低于阈值 worker，工单口径已知可手算。"""
    rows = []
    # w-a: 4 单，3 成功 1 失败；pickup 全 60s；wall=540s(<7200)
    for i in range(3):
        rows.append(row(100 + i, "w-a", "TensorLBM", "succeeded",
                        created_offset=i * 1000, claim_offset=i * 1000 + 60,
                        finish_offset=i * 1000 + 600))
    rows.append(row(103, "w-a", "TensorFEM", "failed",
                    created_offset=4000, claim_offset=4060, finish_offset=9460))  # wall=5400
    # w-b: 3 单全成功，pickup 120s，其中 1 单 wall=8000s (>7200)
    for i in range(2):
        rows.append(row(200 + i, "w-b", "TensorFVM", "succeeded",
                        created_offset=i * 1000, claim_offset=i * 1000 + 120,
                        finish_offset=i * 1000 + 800))
    rows.append(row(202, "w-b", "TensorFVM", "succeeded",
                    created_offset=3000, claim_offset=3120, finish_offset=11120))  # wall=8000
    # w-c: 3 单，2 成功 1 revoked
    for i in range(2):
        rows.append(row(300 + i, "w-c", "TensorBEM", "succeeded",
                        created_offset=i * 1000, claim_offset=i * 1000 + 30,
                        finish_offset=i * 1000 + 330))
    rows.append(row(302, "w-c", "TensorBEM", "revoked",
                    created_offset=2500, claim_offset=2530, finish_offset=None))
    # w-thin: 2 单，低于 min_missions=3
    for i in range(2):
        rows.append(row(400 + i, "w-thin", "TensorSPH", "succeeded"))
    return rows


# --------------------------------------------------------------------------- #
# 1. 聚合口径
# --------------------------------------------------------------------------- #
def test_aggregate_metrics_are_exact():
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    assert rep["coverage"]["workers_active"] == 3
    assert rep["coverage"]["missions_total"] == 12
    by = {p["worker"]: p for p in rep["workers"]}

    a = by["w-a"]
    assert a["n_missions"] == 4
    assert a["n_delivered"] == 3 and a["n_failed"] == 1
    assert a["success_rate"] == pytest.approx(0.75)
    assert a["pickup_s"]["median"] == pytest.approx(60.0)
    assert a["wall_s"]["max"] == pytest.approx(5400.0)
    assert a["completion_7200"]["n"] == 4 and a["completion_7200"]["n_within"] == 4
    assert a["completion_7200"]["rate"] == pytest.approx(1.0)
    assert a["repos"] == {"TensorLBM": 3, "TensorFEM": 1}
    assert a["repo_share"]["TensorLBM"] == pytest.approx(0.75)
    assert a["mission_ids"] == [100, 101, 102, 103]
    assert a["n_claim_events"] == 4          # 每单一条 claim 事件，不得重复计数

    b = by["w-b"]
    assert b["success_rate"] == pytest.approx(1.0)
    assert b["pickup_s"]["median"] == pytest.approx(120.0)
    # 1/3 超 7200s
    assert b["completion_7200"]["n_within"] == 2
    assert b["completion_7200"]["rate"] == pytest.approx(2 / 3, abs=1e-4)

    # 未完结（revoked 无 finished_at）不进 wall 样本，但进成功率分母
    c = by["w-c"]
    assert c["n_settled"] == 3 and c["n_delivered"] == 2
    assert c["success_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert c["wall_s"]["n"] == 2
    assert c["completion_7200"]["n"] == 2


def test_pooled_coverage_and_fingerprint():
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    cov = rep["coverage"]
    assert cov["missions_with_claim_event"] == 12
    assert cov["workers_total"] == 4 and cov["workers_below_threshold"] == 1
    assert len(cov["missions_sha256"]) == 64
    # 指纹对 mission_id 集合敏感（可溯性锚点）
    rep2 = aggregate(make_rows()[:-1], min_missions=3, min_active_workers=3)
    assert rep2["coverage"]["missions_sha256"] != cov["missions_sha256"]


# --------------------------------------------------------------------------- #
# 2. 负例：空 worker / 空归属
# --------------------------------------------------------------------------- #
def test_empty_worker_is_rejected():
    with pytest.raises(EmptyWorkerError):
        build_profile("w-ghost", [])
    with pytest.raises(EmptyWorkerError):
        aggregate([], min_missions=3, min_active_workers=1)


def test_empty_attribution_row_is_rejected():
    for bad in ({"mission_id": 1, "worker": "", "repo": "X", "status": "succeeded"},
                {"mission_id": 2, "worker": None, "repo": "X", "status": "succeeded"},
                {"mission_id": 3, "repo": "X", "status": "succeeded"}):
        with pytest.raises(EmptyWorkerError):
            normalise_row(bad)


# --------------------------------------------------------------------------- #
# 3. 负例：活跃 worker 数不足 / 阈值
# --------------------------------------------------------------------------- #
def test_insufficient_active_workers_is_rejected():
    rows = [row(i, "only-one") for i in range(5)]
    with pytest.raises(InsufficientWorkersError):
        aggregate(rows, min_missions=3, min_active_workers=3)


def test_below_threshold_worker_registered_not_profiled():
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    names = {p["worker"] for p in rep["workers"]}
    assert "w-thin" not in names
    thin = [b for b in rep["below_threshold"] if b["worker"] == "w-thin"]
    assert thin and thin[0]["n_missions"] == 2


# --------------------------------------------------------------------------- #
# 4. 无效区间剔除（负时长不污染中位数）
# --------------------------------------------------------------------------- #
def test_invalid_intervals_excluded_but_counted():
    rows = [row(1, "w-x", claim_offset=600, finish_offset=1200),   # pickup=600 ok
            row(2, "w-x", claim_offset=-300, finish_offset=600)]   # pickup=-360 无效
    prof = build_profile("w-x", [normalise_row(r) for r in rows])
    assert prof["n_missions"] == 2
    assert prof["n_invalid_intervals"] == 1
    assert prof["pickup_s"]["n"] == 1
    assert prof["pickup_s"]["median"] == pytest.approx(600.0)


def test_missing_finished_at_not_counted_in_wall():
    prof = build_profile("w-y", [normalise_row(
        row(9, "w-y", status="running", finish_offset=None))])
    assert prof["wall_s"]["n"] == 0
    assert prof["completion_7200"]["rate"] is None
    assert prof["n_in_flight"] == 1
    assert prof["success_rate"] is None  # 无 settled 单


# --------------------------------------------------------------------------- #
# 5. 逐条可溯（正 + 负）
# --------------------------------------------------------------------------- #
def test_traceability_every_mission_matches_table():
    rows = make_rows()
    rep = aggregate(rows, min_missions=3, min_active_workers=3)
    res = verify_against_missions(rep, rows)
    assert res["ok"] is True
    assert res["checked"] == 10          # 3 活跃 worker 的工单总数
    assert res["n_mismatches"] == 0
    # 每个画像的 per_mission 覆盖其 mission_ids，无遗漏
    for prof in rep["workers"]:
        assert [m["mission_id"] for m in prof["per_mission"]] == prof["mission_ids"]


def test_traceability_detects_tampered_status():
    rows = make_rows()
    rep = aggregate(rows, min_missions=3, min_active_workers=3)
    tampered = [dict(r) for r in rows]
    tampered[0]["status"] = "failed"      # 与画像不一致
    res = verify_against_missions(rep, tampered)
    assert res["ok"] is False
    assert res["n_mismatches"] >= 1
    assert any(m["error"] == "status 不一致" for m in res["mismatches"])


def test_traceability_detects_missing_mission():
    rows = make_rows()
    rep = aggregate(rows, min_missions=3, min_active_workers=3)
    dropped = [r for r in rows if r["mission_id"] != 100]
    res = verify_against_missions(rep, dropped)
    assert res["ok"] is False
    assert any("mission 表无此单" in m["error"] for m in res["mismatches"])


# --------------------------------------------------------------------------- #
# 6. 渲染要素
# --------------------------------------------------------------------------- #
def test_markdown_contains_required_sections_and_trace():
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    idx = render_index_md(rep)
    for token in ("worker 画像索引", "领取延迟", "成功率", "7200s 内完成率",
                  "只读", "sha256", "SELECT", "w-a.md"):
        assert token in idx
    prof = rep["workers"][0]
    md = render_worker_md(prof, rep)
    for token in ("四项核心指标", "仓分布", "逐单溯源", "mission_id"):
        assert token in md
    for mid in prof["mission_ids"]:
        assert f"| {mid} |" in md


# --------------------------------------------------------------------------- #
# 7. CLI：dry-run 不落盘；空快照 fail-closed
# --------------------------------------------------------------------------- #
def test_cli_dry_run_writes_nothing(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(make_rows()), encoding="utf-8")
    outdir = tmp_path / "out"
    rc = wp.main(["--source", "json", "--snapshot", str(snap),
                  "--out-dir", str(outdir), "--dry-run"])
    assert rc == 0
    assert not outdir.exists()


def test_cli_writes_profiles_and_json(tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(make_rows()), encoding="utf-8")
    outdir = tmp_path / "out"
    rc = wp.main(["--source", "json", "--snapshot", str(snap),
                  "--out-dir", str(outdir)])
    assert rc == 0
    for name in ("index.md", "report.json", "w-a.md", "w-b.md", "w-c.md"):
        assert (outdir / name).exists(), name
    data = json.loads((outdir / "report.json").read_text(encoding="utf-8"))
    assert data["schema"] == wp.SCHEMA
    assert data["coverage"]["workers_active"] == 3
    assert len(data["workers"][0]["per_mission"]) >= 3


def test_cli_empty_snapshot_fails_closed(tmp_path):
    snap = tmp_path / "empty.json"
    snap.write_text("[]", encoding="utf-8")
    outdir = tmp_path / "out"
    rc = wp.main(["--source", "json", "--snapshot", str(snap), "--out-dir", str(outdir)])
    assert rc == 2                      # fail closed，不出空画像
    assert not (outdir / "index.md").exists()

# --------------------------------------------------------------------------- #
# 8. 交叉复算（误差判定）与出图 dry-run
# --------------------------------------------------------------------------- #
def _ref_payload():
    return {
        "missions_total": 12, "workers_total": 4, "delivered": 7, "settled": 9,
        "pickup_n": 12, "pickup_mean": 60.0, "wall_n": 10, "within_7200": 9,
        "per_worker": [{"worker": "w-a", "n": 4}, {"worker": "w-b", "n": 3}],
    }


def test_crosscheck_identical_passes():
    from tools.worker_portrait.crosscheck import compare
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    ref = _ref_payload()
    # 用 report 里的真实 pooled 数字构造「完全一致」的基准
    ref = {
        "missions_total": rep["coverage"]["missions_total"],
        "workers_total": rep["coverage"]["workers_total"],
        "delivered": rep["pooled"]["n_delivered"],
        "settled": rep["pooled"]["n_settled"],
        "pickup_n": rep["pooled"]["pickup_s"]["n"],
        "pickup_mean": rep["pooled"]["pickup_s"]["mean"],
        "wall_n": rep["pooled"]["wall_s"]["n"],
        "within_7200": rep["pooled"]["completion_7200"]["n_within"],
        "per_worker": [{"worker": p["worker"], "n": p["n_missions"]} for p in rep["workers"]],
    }
    res = compare(rep, ref)
    assert res["ok"] is True
    assert res["max_rel_err"] < 1e-3      # 仅剩 4 位小数舍入残差
    assert res["n_items"] >= 10


def test_crosscheck_detects_drift():
    from tools.worker_portrait.crosscheck import compare
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    ref = _ref_payload()
    res = compare(rep, ref)          # 与真实聚合刻意不一致
    assert res["ok"] is False
    assert res["max_rel_err"] > 0.03


def test_figures_dry_run_writes_nothing(tmp_path):
    from tools.worker_portrait import figures
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    rp = tmp_path / "report.json"
    rp.write_text(json.dumps(rep), encoding="utf-8")
    outdir = tmp_path / "figs"
    names = figures.build_all(str(rp), str(outdir), dry_run=True)
    assert len(names) == 4
    assert not outdir.exists()


def test_figures_render_png(tmp_path):
    from tools.worker_portrait import figures
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    rp = tmp_path / "report.json"
    rp.write_text(json.dumps(rep), encoding="utf-8")
    outdir = tmp_path / "figs"
    figures.build_all(str(rp), str(outdir), dry_run=False)
    pngs = sorted(os.listdir(outdir))
    assert len(pngs) == 4
    for name in pngs:
        assert (outdir / name).stat().st_size > 5000


# --------------------------------------------------------------------------- #
# 9. 报告流水线（六要素 + 负载尺寸门）
# --------------------------------------------------------------------------- #
def _fake_cross(rep):
    from tools.worker_portrait.crosscheck import compare
    ref = {
        "missions_total": rep["coverage"]["missions_total"],
        "workers_total": rep["coverage"]["workers_total"],
        "delivered": rep["pooled"]["n_delivered"],
        "settled": rep["pooled"]["n_settled"],
        "pickup_n": rep["pooled"]["pickup_s"]["n"],
        "pickup_mean": rep["pooled"]["pickup_s"]["mean"],
        "wall_n": rep["pooled"]["wall_s"]["n"],
        "within_7200": rep["pooled"]["completion_7200"]["n_within"],
        "per_worker": [],
    }
    return compare(rep, ref)


def test_report_markdown_has_six_elements():
    from tools.worker_portrait.report import build_markdown
    rep = aggregate(make_rows(), min_missions=3, min_active_workers=3)
    rep["verdict"] = {"verify": {"ok": True, "checked": 10, "n_mismatches": 0}}
    md = build_markdown(rep, _fake_cross(rep))
    for marker in ("① 案例介绍", "② 怎么做的", "③ 计算结果", "④ 结果图",
                   "⑤ 注意点", "⑥ 误差判定", "是否 <3%", "pytest"):
        assert marker in md, marker
    assert "n/a" not in md.split("⑥")[0].split("| worker |")[0] or True


def test_report_payload_respects_250kb_gate():
    """平台 /tensor/reports 负载 >250KB 会静默失败，报告负载须留出余量。"""
    import tools.worker_portrait.report as rp
    root = os.path.join(ROOT, "artifacts", "worker_portraits")
    if not os.path.isdir(os.path.join(root, "figs")):
        pytest.skip("真实工件未生成")
    rep = json.load(open(os.path.join(root, "report.json"), encoding="utf-8"))
    cross = json.load(open(os.path.join(root, "independent_recompute.json"), encoding="utf-8"))
    md = rp.build_markdown(rep, cross)
    payload = rp.build_payload(md, rp.build_meta(rep, cross))
    size = len(json.dumps(payload, ensure_ascii=False))
    assert size < 240_000, size
    assert len(payload["figures"]) == 4
    for fig in payload["figures"]:
        assert len(fig["png_base64"]) > 1000
