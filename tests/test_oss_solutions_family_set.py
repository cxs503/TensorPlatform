"""FEAT-OSS-INVENTORY-LBM「方式集」数据契约回归。

守护 `artifacts/oss-solutions-v1/` 下的 SQL 导出快照与归集/推荐规则的一致性：
任何人在改 `families.py` / `reco.py` 或刷新 `oss_solutions.tsv` 后，本测试必须仍然通过，
否则说明「方式集」报告里的谱系表/推荐引用出现了悬空条目。

不联网、不读 DB，只校验已落盘的导出快照（可离线复现）。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ART = os.path.join(ROOT, "artifacts", "oss-solutions-v1")
sys.path.insert(0, ART)

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(ART, "oss_solutions.tsv")),
    reason="oss_solutions.tsv snapshot missing",
)

import load_data as L  # noqa: E402
from families import FAMILIES, FAMILY_NAME, SLUG_TO_FAMILY  # noqa: E402
from reco import RECO, LEDGER_SEEDS, license_class  # noqa: E402


@pytest.fixture(scope="module")
def data():
    rows, inv, fam_rows, per_family, licenses, tiers = L.mains()
    return dict(rows=rows, inv=inv, fam_rows=fam_rows, per_family=per_family,
                licenses=licenses, tiers=tiers)


def test_every_solution_row_assigned_to_family(data):
    rows = data["rows"]
    assert rows, "空快照"
    unmapped = sorted({r["slug"] for r in rows if r["family"] not in FAMILY_NAME})
    assert unmapped == [], f"未归集 slug: {unmapped}"


def test_slug_map_has_no_dead_entries(data):
    seen = {r["slug"] for r in data["rows"]}
    dead = sorted(set(SLUG_TO_FAMILY) - seen)
    assert dead == [], f"families.py 中登记但快照里不存在（拼写漂移）: {dead}"


def test_every_recommendation_resolves_to_a_solution_row(data):
    fam_rows = data["fam_rows"]
    dangling = []
    for fam, spec in RECO.items():
        tags = {(r["repo"], r["tag"]) for r in fam_rows[fam]}
        for repo, tag, _t, _why in spec["picks"]:
            if (repo, tag) not in tags:
                dangling.append(f"{fam}:{repo}:{tag}")
    assert dangling == [], f"推荐引用了不存在的谱系行: {dangling}"


def test_transfer_verdict_is_canonical(data):
    canon = {"fits", "adapt", "reference", "none", "reject"}
    bad = sorted({r["transfer"] for r in data["rows"]} - canon)
    assert bad == [], f"非规范 transfer: {bad}"


def test_recommended_fits_implies_permissive_license(data):
    """本报告的推荐若给 `fits`（=可搬码），必须持 permissive 许可证 —— 报告自身零违规。"""
    violations = [f"{fam}:{repo}:{tag}" for fam, spec in RECO.items()
                  for repo, tag, t, _w in spec["picks"]
                  if t == "fits" and license_class(data["licenses"].get(repo)) != "permissive"]
    assert violations == [], f"推荐 fits 但许可证不可搬: {violations}"


def test_ledger_fits_on_copyleft_is_a_known_ruling(data):
    """台账原表的 `fits` 表达技术可迁移、不含许可证判定：快照里固定 21 条落在非 permissive 仓。

    这是缺口 E-10 的回归护栏：若数字变化说明盘点口径被改过，报告里的双轴统计需同步刷新。
    """
    n = sum(1 for r in data["rows"]
            if r["transfer"] == "fits" and license_class(data["licenses"].get(r["repo"])) != "permissive")
    assert n == 21, f"台账 fits⊗许可证 不合规条目数由 21 变为 {n}，需刷新 E-10 与 C7 统计"


def test_ledger_acceptance_thresholds(data):
    """FEAT-OSS-INVENTORY-LBM 验收：≥24 仓盘点、≥40 条谱系行（每条带指针 + 迁移评估）。"""
    rows = data["rows"]
    inventoried = {i["repo"] for i in data["inv"] if i.get("status") == "inventoried"}
    covered = {r["repo"] for r in rows}
    assert len(inventoried) >= 24, f"已盘点仓仅 {len(inventoried)}"
    assert len(covered) >= 24, f"有谱系产出的仓仅 {len(covered)}"
    assert len(rows) >= 40, f"谱系行仅 {len(rows)}"
    assert all(r["files"] for r in rows), "存在 files 空指针行"
    assert all(r["transfer"] for r in rows), "存在 transfer 缺失行"


def test_every_family_has_rows_and_recommendation(data):
    for k, name, _crit in FAMILIES:
        assert data["per_family"][k]["n_rows"] > 0, f"{k} {name} 无谱系行"
        assert RECO[k]["picks"], f"{k} {name} 无推荐"
        assert RECO[k]["route"], f"{k} {name} 无路线"


def test_ledger_seeds_reference_known_families():
    keys = {k for k, _, _ in FAMILIES}
    for slug, fams, step in LEDGER_SEEDS:
        assert slug and step
        for f in fams:
            assert f == "ALL" or f in keys, f"{slug} 引用未知功能点 {f}"

def test_report_payload_passes_six_element_gate():
    """报告 payload 必须过平台六要素门禁（与 tensor_reports /qa/check 同一实现）。"""
    backend = os.path.join(ROOT, "backend")
    if not os.path.isdir(os.path.join(backend, "services")):
        pytest.skip("backend/services not present")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from services.report_gate import check_report_quality  # noqa: E402

    payload = json.load(open(os.path.join(ART, "report_payload.json"), encoding="utf-8"))
    res = check_report_quality(payload["markdown"], payload["figures"])
    assert res["passed"], res["issues"]
    assert len(payload["figures"]) >= 1
