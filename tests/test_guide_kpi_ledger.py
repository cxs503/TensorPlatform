"""指南 KPI 台账 ETL 单测（data 席 m1596）。

覆盖点（不依赖数据库，纯函数层）：
  - 小时桶幂等：同小时内重复跑落到同一 snapshot_at（不被 24h trend 污染）
  - 比率口径：分母 0 → None，绝不返回 0（0 会被误读成"实测为零"）
  - 规格表完整性：12 项全覆盖、key 唯一、db/ledger 互斥、db 必有 SQL+nums
  - 台账完整性：每个 ledger 字段在 literal_ledger.json 里存在且带 owner/as_of/evidence
  - 对账解析：'71.7%（358/499…）'/'4/5'/'样本 0（…）'/'机制就绪' 四类
  - 漂移判定：超 2% 相对容差 → drift；定性 KPI → skipped
  - 落库渲染：定性 KPI 不入库；SQL 含 ON CONFLICT（幂等）；引号转义安全
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import os
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("kpi_ledger", ROOT / "tools" / "guide_kpi_ledger.py")
kpi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kpi)

LEDGER = json.loads((ROOT / "docs" / "kpi" / "literal_ledger.json").read_text(encoding="utf-8"))


# ── 小时桶 ────────────────────────────────────────────────────────────────
def test_hour_bucket_truncates_and_is_utc():
    ts = datetime(2026, 11, 1, 7, 53, 3, 129143, tzinfo=timezone.utc)
    b = kpi.hour_bucket(ts)
    assert (b.hour, b.minute, b.second, b.microsecond) == (7, 0, 0, 0)
    assert b.tzinfo is not None


def test_hour_bucket_naive_input_treated_as_utc():
    b = kpi.hour_bucket(datetime(2026, 11, 1, 23, 59, 59))
    assert b == datetime(2026, 11, 1, 23, 0, 0, tzinfo=timezone.utc)


def test_hour_bucket_same_hour_is_idempotent():
    a = kpi.hour_bucket(datetime(2026, 11, 1, 7, 0, 1, tzinfo=timezone.utc))
    b = kpi.hour_bucket(datetime(2026, 11, 1, 7, 59, 59, tzinfo=timezone.utc))
    assert a == b


# ── 比率口径 ──────────────────────────────────────────────────────────────
def test_ratio_zero_denominator_returns_none_not_zero():
    assert kpi.ratio(0, 0) is None
    assert kpi.ratio(5, 0) is None
    assert kpi.ratio(None, 10) is None


def test_ratio_values():
    assert kpi.ratio(358, 499) == round(358 / 499, 4)
    assert kpi.ratio(1, 3) == 0.3333
    assert kpi.pct(0.7174) == 71.7


def test_merge_rows_ratio_and_identity():
    ratio_spec = kpi.spec_index()["success_rate"]
    assert kpi.merge_rows(ratio_spec, {"succ": "842", "term": "1027"}) == round(842 / 1027, 4)
    idt_spec = kpi.spec_index()["unattended_cases"]
    assert kpi.merge_rows(idt_spec, {"cases": "65"}) == 65.0


def test_merge_rows_missing_column_is_none():
    spec = kpi.spec_index()["success_rate"]
    assert kpi.merge_rows(spec, {"succ": "842", "term": None}) is None


# ── 规格表完整性 ──────────────────────────────────────────────────────────
def test_twelve_kpis_unique_keys():
    keys = [s["key"] for s in kpi.KPI_SPECS]
    assert len(keys) == 12 == len(set(keys)), keys
    assert set(keys) == set(kpi.spec_index())


def test_specs_are_self_describing():
    for s in kpi.KPI_SPECS:
        assert s["formula"].strip(), s["key"]
        assert s["kind"] in ("db", "ledger", "registry"), s["key"]
        if s["kind"] == "db":
            assert s["sql"].strip().upper().startswith("SELECT"), s["key"]
            assert s["nums"] and all(isinstance(n, str) for n in s["nums"]), s["key"]
            assert s["how"] in ("ratio", "identity"), s["key"]
            if s["how"] == "ratio":
                assert len(s["nums"]) == 2, s["key"]
        elif s["kind"] == "ledger":
            assert s["ledger_field"] in LEDGER["values"], s["key"]
        else:  # registry：可复算工件口径（m2054 把 KPI#9/#10 从字面量降级）
            assert s["registry_field"], s["key"]
            assert s["registry_field"] in ("benchmark_cases", "resistance_accuracy"), s["key"]
        if s["numeric"]:
            assert s["target"] is not None and s["target"] > 0, s["key"]
        else:
            assert s["target"] is None, s["key"]


def test_db_and_ledger_kinds_are_disjoint_and_counts():
    """m2054：benchmark_cases / resistance_accuracy 由 ledger 降级为 registry（6/4/2）。"""
    kinds = [s["kind"] for s in kpi.KPI_SPECS]
    assert kinds.count("db") == 6 and kinds.count("ledger") == 4 and kinds.count("registry") == 2
    assert sum(1 for s in kpi.KPI_SPECS if s["numeric"]) == 10
    assert sum(1 for s in kpi.KPI_SPECS if not s["numeric"]) == 2


# ── 台账完整性（字面量必须留痕） ──────────────────────────────────────────
def test_every_ledger_cell_has_provenance():
    for fld, cell in LEDGER["values"].items():
        assert cell.get("owner"), fld
        assert cell.get("as_of"), fld
        assert cell.get("evidence"), fld
        datetime.strptime(cell["as_of"], "%Y-%m-%d")  # 格式可解析


def test_numeric_ledger_cells_have_value_and_target_consistency():
    for s in kpi.KPI_SPECS:
        if s["kind"] != "ledger":
            continue
        cell = LEDGER["values"][s["ledger_field"]]
        if s["numeric"]:
            assert isinstance(cell["value"], (int, float)), s["key"]
            assert cell["target"] == s["target"], s["key"]
        else:
            assert cell["value"] is None, s["key"]


def test_build_records_marks_staleness():
    now = datetime(2026, 12, 1, tzinfo=timezone.utc)  # 台账 as_of=2026-11-01 → 30 天
    recs = kpi.build_records({}, LEDGER, now=now)
    ledger_recs = [r for r in recs if r["kind"] == "ledger"]
    assert ledger_recs and all(r["stale"] for r in ledger_recs)
    assert all(r["age_days"] >= kpi.LEDGER_STALE_DAYS for r in ledger_recs)


def test_build_records_missing_ledger_field_raises():
    bad = {"values": {}}
    try:
        kpi.build_records({}, bad)
    except KeyError as e:
        assert "tool_categories" in str(e)
    else:
        raise AssertionError("缺台账字段应抛 KeyError")


def test_build_records_db_source_has_sql_provenance():
    recs = kpi.build_records({"success_rate": 0.8}, LEDGER)
    rec = kpi.spec_index()["success_rate"]["key"]
    r = next(x for x in recs if x["key"] == rec)
    assert r["value"] == 0.8 and r["source"].startswith("db:SELECT")
    assert r["progress"] == round(0.8 / 0.9, 4)
    assert not r["stale"]


# ── 对账 ─────────────────────────────────────────────────────────────────
def test_parse_dashboard_cur_shapes():
    p = kpi.parse_dashboard_cur("71.7%（358/499；48h 356/496）")
    assert (p["mode"], p["value"], p["num"], p["den"]) == ("pct", 71.7, 358.0, 499.0)
    p = kpi.parse_dashboard_cur("4/4（在飞 0）")
    assert (p["mode"], p["value"], p["num"], p["den"]) == ("count", 4.0, 4.0, 4.0)
    p = kpi.parse_dashboard_cur("454/499 有报告工件")
    assert (p["mode"], p["value"]) == ("count", 454.0)
    p = kpi.parse_dashboard_cur("样本 0（报告 270 篇≠样本）")
    assert (p["mode"], p["value"]) == ("count", 0.0)
    assert kpi.parse_dashboard_cur("机制就绪")["mode"] == "unparsable"
    assert kpi.parse_dashboard_cur("")["mode"] == "unparsable"


def test_normalize_to_unit_fixes_dash_vs_ratio_mismatch():
    """回归：看板写 "4/4"（计数），KPI 是比率 → 必须折成 1.0 再比，否则误报漂移。"""
    assert kpi.normalize_to_unit(kpi.parse_dashboard_cur("4/4（在飞 0）"), "ratio") == 1.0
    assert kpi.normalize_to_unit(kpi.parse_dashboard_cur("454/499 有报告工件"), "ratio") == 0.9098
    assert kpi.normalize_to_unit(kpi.parse_dashboard_cur("71.7%（358/499）"), "ratio") == 0.717
    assert kpi.normalize_to_unit(kpi.parse_dashboard_cur("4/5"), "count") == 4.0
    assert kpi.normalize_to_unit(kpi.parse_dashboard_cur("71.7%"), "count") is None


def _dash(pairs, ts="2026-11-01 07:55:10"):
    return {"ts": ts, "kpis": [{"kpi": k, "cur": c} for k, c in pairs]}


def test_reconcile_ratio_kpi_vs_count_string_is_ok_not_false_drift():
    recs = kpi.build_records({"qr_pass_rate": 1.0}, LEDGER)
    rows = kpi.reconcile(recs, _dash([("已合并功能复核通过率", "4/4（在飞 0）")]))
    assert rows[0]["status"] == "ok", rows[0]


def test_reconcile_detects_drift_and_ok():
    recs = kpi.build_records({"success_rate": 0.80}, LEDGER)
    rows = kpi.reconcile(recs, _dash([("成功率 ≥90%+自动归因", "71.7%（358/499）")]))
    # 统一到 ratio 单位后比较：0.80 vs 0.717
    assert rows[0]["status"] == "drift"
    assert rows[0]["abs_drift"] == round(0.80 - 0.717, 4)
    assert rows[0]["dash_in_unit"] == 0.717
    recs = kpi.build_records({"success_rate": 0.717}, LEDGER)
    rows = kpi.reconcile(recs, _dash([("成功率 ≥90%+自动归因", "71.7%（358/499）")]))
    assert rows[0]["status"] == "ok"


def test_reconcile_lineage_ratio_from_fraction_string():
    recs = kpi.build_records({"lineage_coverage": 0.9098}, LEDGER)
    rows = kpi.reconcile(recs, _dash([("谱系追溯 100%", "454/499 有报告工件")]))
    assert rows[0]["status"] == "ok" and rows[0]["dash_in_unit"] == 0.9098
    recs = kpi.build_records({"lineage_coverage": 0.8199}, LEDGER)
    rows = kpi.reconcile(recs, _dash([("谱系追溯 100%", "454/499 有报告工件")]))
    assert rows[0]["status"] == "drift"
    assert rows[0]["rel_drift"] == round((0.9098 - 0.8199) / 0.9098, 4)


def test_reconcile_count_mode():
    # m2054：benchmark_cases 走 registry 口径，喂一个与看板同值的工件验证对账链路
    reg = {"kpi": {"benchmark_cases": 1.0, "resistance_accuracy": 0.0}, "summary": {}}
    recs = kpi.build_records({}, LEDGER, registry=reg)
    rows = kpi.reconcile(recs, _dash([("对标算例 ≥10（SUBOFF/DTMB/KVLCC2）", "1 完成+2 在飞")]))
    assert rows[0]["status"] == "ok" and rows[0]["dash_in_unit"] == 1.0   # registry=1 与看板一致
    rows = kpi.reconcile(recs, _dash([("工具接入 ≥5 类", "4/5")]))
    assert rows[0]["status"] == "ok"


def test_reconcile_count_drift_when_ledger_advances():
    recs = kpi.build_records({}, LEDGER)
    for r in recs:
        if r["key"] == "benchmark_cases":
            r["value"] = 3.0
    rows = kpi.reconcile(recs, _dash([("对标算例 ≥10（SUBOFF/DTMB/KVLCC2）", "1 完成+2 在飞")]))
    assert rows[0]["status"] == "drift" and rows[0]["abs_drift"] == 2.0


def test_all_spec_labels_match_dashboard_names():
    """标签必须与 guide_dashboard.json 逐字一致，否则对账假 unmapped（m1596 初版踩过）。"""
    dash = json.loads(pathlib.Path(os.environ.get("GUIDE_DASH_PATH",
                                                 "/root/tp-agent/guide_dashboard.json")).read_text(encoding="utf-8")) \
        if pathlib.Path("/root/tp-agent/guide_dashboard.json").exists() else None
    if not dash:
        import pytest
        pytest.skip("看板 json 不在本机，跳过逐字对齐检查")
    specs = {s["label"] for s in kpi.KPI_SPECS}
    dash_labels = {k["kpi"] for k in dash.get("kpis", [])}
    assert dash_labels - specs == set(), dash_labels - specs


def test_reconcile_qualitative_skipped():
    recs = kpi.build_records({"repro_rate": None}, LEDGER)
    rows = kpi.reconcile(recs, _dash([("复现 ≥95%", "机制就绪")]))
    assert rows[0]["status"] == "skipped"


def test_reconcile_unmapped_kpi_flagged():
    recs = kpi.build_records({}, LEDGER)
    rows = kpi.reconcile(recs, _dash([("不存在的指标", "1/2")]))
    assert rows[0]["status"] == "unmapped"


# ── 落库渲染 ─────────────────────────────────────────────────────────────
def test_snapshot_rows_excludes_qualitative():
    now = datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)
    recs = kpi.build_records({"repro_rate": None, "success_rate": 0.8, "lineage_coverage": 0.82},
                             LEDGER, now=now)
    rows = kpi.snapshot_rows(recs, org_id=1, bucket=kpi.hour_bucket(datetime(2026, 11, 1, 7, 5)))
    keys = [r["key"] for r in rows]
    assert "guide.repro_rate" not in keys          # 未实测 → 不入库
    assert "guide.gate_coverage" not in keys       # 定性 → 不入库
    assert "guide.success_rate" in keys
    assert all(r["key"].startswith("guide.") for r in rows)
    assert all(r["snap"].endswith("07:00:00+00:00") for r in rows)
    # m2054：registry 工件缺失 → KPI#9/#10 记 None（未实测，不落库）；DB 未取到值 3 项
    assert "guide.benchmark_cases" not in keys
    assert "guide.resistance_accuracy" not in keys
    assert len(rows) == 4
    # 工件在位时两项恢复数值行（0.0 是实测值，不是「塞 0 说谎」）
    reg = {"kpi": {"benchmark_cases": 0.0, "resistance_accuracy": 0.0},
           "summary": {"qualified_cases": 0, "settled_admissible": 4, "policy_violations": 8}}
    recs2 = kpi.build_records({"repro_rate": None, "success_rate": 0.8, "lineage_coverage": 0.82},
                              LEDGER, now=now, registry=reg)
    keys2 = [r["key"] for r in kpi.snapshot_rows(recs2, org_id=1,
                                                 bucket=kpi.hour_bucket(now))]
    assert "guide.benchmark_cases" in keys2 and "guide.resistance_accuracy" in keys2


def test_render_upsert_is_idempotent_and_escaped():
    sql = kpi.render_upsert(dict(org=1, key="guide.success_rate", val=0.842,
                                 target=0.9, snap="2026-11-01T07:00:00+00:00",
                                 notes="it's ok"))
    assert "ON CONFLICT (organization_id, metric_key, snapshot_at)" in sql
    assert "DO UPDATE SET metric_value" in sql
    assert "'it''s ok'" in sql                     # 单引号转义
    assert "guide.success_rate" in sql and "0.842" in sql


def test_render_upsert_handles_null_target():
    sql = kpi.render_upsert(dict(org=1, key="k", val=1.0, target=None,
                                 snap="2026-11-01T07:00:00+00:00", notes=""))
    assert ", NULL," in sql