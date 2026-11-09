"""campaign_distill 机制测试（合成域夹具，零域词）。

被测：``tools/distill/campaign_distill.py`` 的五个纯机制
（覆盖矩阵 / 知识图谱 / 缺口识别 / 报告校验 / 归档校验和）。

为何用合成域
------------
平台铁律：backend/tools/tests 内不得出现具体物理域词条（判例 #608）。本文件用
``synthetic/alpha``、``synthetic/beta``、``synthetic/gamma`` 三个**合成节点**，
断言只验证"对任意战役数据包成立"的机制性质；真实战役数据经
``artifacts/**/campaign_bundle.json`` 工件注入，不落进代码/测试。

变异体判负（每条机制至少一个 mutant）
------------------------------------
* 结论缺来源 → 必须 FAIL_CLAIMED（不可采信），而不是 PASS；
* 删章节 / 插占位符 / 悬空引用 / 数字不符 / 少一行覆盖 → 校验器各自报对应 rule；
* 归档后篡改文件 → ``verify_manifest`` 必须判不通过。

域词零命中由 ``tools/audit/domain_leak_scan.py`` 机器裁决（TestDomainNeutrality）。
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "distill"))

import campaign_distill as cd  # noqa: E402


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _write(tmp_path: Path, name: str, text: str) -> dict:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {
        "path": str(p),
        "rel_path": name,
        "kind": "test" if name.startswith("test") else "impl",
        "bytes": p.stat().st_size,
        "sha256": cd.sha256_file(p),
    }


@pytest.fixture()
def bundle(tmp_path: Path) -> dict:
    f_alpha_impl = _write(tmp_path, "src/alpha_impl.py", "VALUE = 1\n")
    f_alpha_test = _write(tmp_path, "tests/test_alpha_impl.py", "def test_ok():\n    assert True\n")
    f_beta_impl = _write(tmp_path, "src/beta_impl.py", "VALUE = 2\n")
    return {
        "bundle_version": "1.0",
        "campaign": {"plan_id": 9001, "title": "Synthetic Wave Summary", "label": "plan 9001", "wave": 2},
        "snapshot_utc": "2026-01-01T12:00:00Z",
        "generated_by": "pytest",
        "bundle_path": "synthetic://bundle",
        "nodes": [
            {
                "node_key": "NA",
                "mission_id": 1,
                "repo": "synthetic/alpha",
                "title": "alpha node",
                "physics_domain": "synthetic",
                "status": "in-flight",
                "actual_start": "2026-01-01T03:00:00Z",
                "depends_on": [],
                "criteria": [{"id": "C1", "text": "alpha criterion one"}, {"id": "C2", "text": "alpha criterion two"}],
                "evidence": {
                    "files": [f_alpha_impl, f_alpha_test],
                    "probes": [{"name": "pytest::tests/test_alpha_impl.py", "exit_code": 0, "summary": "1 passed"}],
                    "claims": [
                        {"criterion": "C1", "metric": "rel_err", "value": 0.01, "ok": True,
                         "grade": "REAL", "source": "logs/c1.json#rel_err"},
                        {"criterion": "C2", "metric": "rel_err", "value": 0.22, "ok": False,
                         "grade": "REAL", "source": "logs/c2.json#rel_err"},
                    ],
                },
            },
            {
                "node_key": "NB",
                "mission_id": 2,
                "repo": "synthetic/beta",
                "title": "beta node",
                "physics_domain": "synthetic",
                "status": "in-flight",
                "actual_start": "2026-01-01T11:00:00Z",
                "depends_on": ["NA"],
                "criteria": [{"id": "C1", "text": "beta criterion one"}],
                "evidence": {"files": [f_beta_impl], "probes": [], "claims": []},
            },
            {
                "node_key": "NC",
                "mission_id": 3,
                "repo": "synthetic/gamma",
                "title": "gamma node",
                "physics_domain": "synthetic",
                "status": "failed",
                "actual_start": "2026-01-01T01:00:00Z",
                "depends_on": [],
                "criteria": [{"id": "C1", "text": "gamma criterion one"}],
                "evidence": {"files": [], "probes": [], "claims": []},
            },
        ],
    }


@pytest.fixture()
def ctx(bundle: dict, tmp_path: Path) -> dict:
    # 合成夹具规模小，阈值由 spec（数据）下调；真实战役跑默认阈值 20/30
    spec = {**cd.DEFAULT_SPEC, "graph_min_entities": 10, "graph_min_edges": 12}
    narrative = {
        "summary": ["合成域战役汇总：本段为夹具叙述，不含任何结论性数字。"],
        "layout": ["结构说明由叙述注入。"],
        "coverage": ["覆盖矩阵逐条列出判据状态。"],
        "graph": ["图谱由节点/判据/工件/缺口构成。"],
        "gaps": ["缺口规则见上表。"],
        "manifest": ["工件逐件 sha256 归档。"],
        "limits": ["局限：快照时点切片，结论不外推。"],
        "limits_bullets": ["- 快照语义：仅代表采集时点。"],
    }
    coverage = cd.build_coverage(bundle)
    gaps = cd.find_gaps(bundle, coverage, spec)
    graph = cd.build_graph(bundle, coverage, gaps)
    stats = cd.graph_stats(graph)
    items = [{"path": f["path"], "kind": f["kind"]} for n in bundle["nodes"] for f in n["evidence"]["files"]]
    manifest = cd.archive_artifacts(items, tmp_path / "verified" / "case", tmp_path / "verified" / "case" / "manifest.json")
    return {
        "bundle": bundle, "coverage": coverage, "gaps": gaps, "graph": graph,
        "graph_stats": stats, "manifest": manifest, "spec": spec, "narrative": narrative,
        "self_checks": [],
    }


# --------------------------------------------------------------------------- #
# 1. 覆盖矩阵
# --------------------------------------------------------------------------- #
def test_coverage_statuses_follow_evidence_only(bundle: dict) -> None:
    cov = cd.build_coverage(bundle)
    rows = {(r["node_key"], r["criterion"]): r for r in cov["rows"]}
    assert cov["total"] == 4
    assert rows[("NA", "C1")]["status"] == "PASS_CLAIMED"
    assert rows[("NA", "C2")]["status"] == "FAIL_CLAIMED"       # 结论存在但不达成
    assert rows[("NB", "C1")]["status"] == "EVIDENCE_ONLY"      # 有工件无结论映射
    assert rows[("NC", "C1")]["status"] == "NO_EVIDENCE"
    # 每条 PASS 必须有来源引用；无引用不得进 PASS
    for row in cov["rows"]:
        if row["status"] == "PASS_CLAIMED":
            assert row["evidence_refs"] and all(r.startswith("claim:") for r in row["evidence_refs"])
    assert cov["counts"]["PASS_CLAIMED"] == 1


def test_rollup_requires_all_criteria_pass(bundle: dict) -> None:
    cov = cd.build_coverage(bundle)
    roll = {n["node_key"]: n for n in cov["nodes"]}
    assert roll["NA"]["verdict"] == "PARTIAL"        # 1/2 通过
    assert roll["NB"]["verdict"] == "UNVERIFIED_EVIDENCE"
    assert roll["NC"]["verdict"] == "WITHHELD"
    assert roll["NA"]["coverage_ratio"] == 0.5


def test_mutant_claim_without_source_is_not_pass(bundle: dict) -> None:
    m = copy.deepcopy(bundle)
    # 抹掉来源：结论依旧"ok=True"，但按纪律不可采信
    m["nodes"][0]["evidence"]["claims"][0].pop("source")
    cov = cd.build_coverage(m)
    row = next(r for r in cov["rows"] if r["node_key"] == "NA" and r["criterion"] == "C1")
    assert row["status"] == "FAIL_CLAIMED"
    assert "缺来源" in row["reason"]


def test_mutant_unknown_grade_fails(bundle: dict) -> None:
    m = copy.deepcopy(bundle)
    m["nodes"][0]["evidence"]["claims"][0]["grade"] = "PROBABLY_FINE"
    cov = cd.build_coverage(m)
    row = next(r for r in cov["rows"] if r["node_key"] == "NA" and r["criterion"] == "C1")
    assert row["status"] == "FAIL_CLAIMED"


# --------------------------------------------------------------------------- #
# 2. 知识图谱
# --------------------------------------------------------------------------- #
def test_graph_scale_and_integrity(ctx: dict) -> None:
    stats = ctx["graph_stats"]
    assert stats["entities"] >= ctx["spec"]["graph_min_entities"]
    assert stats["edges"] >= ctx["spec"]["graph_min_edges"]
    ids = {e["id"] for e in ctx["graph"]["entities"]}
    for edge in ctx["graph"]["edges"]:
        assert edge["source"] in ids and edge["target"] in ids, "悬空边"
    assert stats["entities"] == len(ctx["graph"]["entities"])
    assert stats["edges"] == len(ctx["graph"]["edges"])
    assert stats["entities_by_type"]["node"] == 3


def test_graph_is_deterministic(bundle: dict) -> None:
    cov = cd.build_coverage(bundle)
    gaps = cd.find_gaps(bundle, cov)
    g1 = cd.build_graph(bundle, cov, gaps)
    g2 = cd.build_graph(bundle, cov, gaps)
    assert cd.canonical_json(g1) == cd.canonical_json(g2)


# --------------------------------------------------------------------------- #
# 3. 缺口识别
# --------------------------------------------------------------------------- #
def test_gap_rules_fire_on_fixture(ctx: dict) -> None:
    rules = {g["rule"] for g in ctx["gaps"]}
    assert "unverified_criteria" in rules
    assert "failed_node" in rules
    assert "dependency_open" in rules      # NB 依赖 NA，而 NA 仅 PARTIAL
    assert "stale_inflight" in rules       # 快照 12:00 vs actual_start 06:00（>6h）
    assert len(ctx["gaps"]) >= ctx["spec"]["gaps_min"]


def test_every_gap_has_complete_proposal(ctx: dict) -> None:
    required = {"title", "repo", "physics_domain", "why_frontier", "goal",
                "acceptance_criteria", "dependencies", "metric"}
    for gap in ctx["gaps"]:
        assert required <= set(gap["proposal"]), gap["id"]
        assert gap["proposal"]["acceptance_criteria"], gap["id"]
        assert gap["affected_nodes"], gap["id"]


def test_mutant_missing_source_raises_gap(bundle: dict) -> None:
    m = copy.deepcopy(bundle)
    m["nodes"][1]["evidence"]["claims"] = [{"criterion": "C1", "value": 1.0, "ok": True, "grade": "REAL"}]
    cov = cd.build_coverage(m)
    rules = {g["rule"] for g in cd.find_gaps(m, cov)}
    assert "claim_without_source" in rules


def test_no_gap_when_node_fully_verified(bundle: dict) -> None:
    m = copy.deepcopy(bundle)
    for claim in m["nodes"][0]["evidence"]["claims"]:
        claim["ok"] = True
        claim["grade"] = "REAL"
    cov = cd.build_coverage(m)
    gaps = [g for g in cd.find_gaps(m, cov) if g["rule"] == "unverified_criteria" and "NA" in g["affected_nodes"]]
    assert gaps == []


# --------------------------------------------------------------------------- #
# 4. 报告渲染与校验
# --------------------------------------------------------------------------- #
def test_report_renders_and_validates_clean(ctx: dict) -> None:
    md = cd.render_report(ctx)
    for sec in ctx["spec"]["required_sections"]:
        assert sec in md
    assert cd.validate_report_md(md, ctx, ctx["spec"]) == []
    # 覆盖矩阵逐行出现（逐项对照表的机器形式）
    for row in ctx["coverage"]["rows"]:
        assert f"| {row['node_key']} | {row['criterion']} |" in md
    assert "[E1]" in md


@pytest.mark.parametrize(
    "mutate, rule",
    [
        (lambda md: md.replace("## 3 知识图谱", ""), "missing_section"),
        (lambda md: md + "\nTBD\n", "placeholder_token"),
        (lambda md: md + "\n见 [E99]\n", "dangling_citation"),
        (lambda md: md.replace("判据条数：4", "判据条数：5"), "number_mismatch"),
        (lambda md: md.replace("| NA | C2 |", "| NA | C9 |"), "coverage_row_missing"),
    ],
)
def test_report_validator_catches_mutants(ctx: dict, mutate, rule: str) -> None:
    md = mutate(cd.render_report(ctx))
    rules = {v["rule"] for v in cd.validate_report_md(md, ctx, ctx["spec"])}
    assert rule in rules


# --------------------------------------------------------------------------- #
# 5. 归档与校验和
# --------------------------------------------------------------------------- #
def test_archive_manifest_matches_disk(ctx: dict) -> None:
    man = ctx["manifest"]
    assert man["verify_status"] == "verified"
    assert len(man["entries"]) == 3
    assert cd.verify_manifest(man)["ok"] is True
    sums = Path(man["sha256sums_path"]).read_text(encoding="utf-8")
    for ent in man["entries"]:
        assert f"{ent['sha256']}  {ent['rel_path']}" in sums


def test_mutant_tampered_archive_is_detected(ctx: dict, tmp_path: Path) -> None:
    man = copy.deepcopy(ctx["manifest"])
    target = Path(man["archive_dir"]) / man["entries"][0]["rel_path"]
    target.write_text("tampered\n", encoding="utf-8")
    res = cd.verify_manifest(man)
    assert res["ok"] is False
    assert res["mismatches"][0]["error"] == "sha256"


def test_archive_conflict_refuses_overwrite(ctx: dict, tmp_path: Path) -> None:
    man = ctx["manifest"]
    src = Path(man["entries"][0]["source_path"])
    dest = Path(man["archive_dir"])
    clash = dest / src.name
    clash.write_text("different\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        cd.archive_artifacts([{"path": str(src), "kind": "impl"}], dest)


# --------------------------------------------------------------------------- #
# 6. CLI 端到端 + 确定性
# --------------------------------------------------------------------------- #
def test_cli_end_to_end_and_determinism(bundle: dict, ctx: dict, tmp_path: Path) -> None:
    bpath = tmp_path / "bundle.json"
    bpath.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    npath = tmp_path / "narrative.json"
    npath.write_text(json.dumps(ctx["narrative"], ensure_ascii=False), encoding="utf-8")
    spath = tmp_path / "spec.json"
    spath.write_text(json.dumps(ctx["spec"], ensure_ascii=False), encoding="utf-8")
    outs = []
    for tag in ("a", "b"):
        out = tmp_path / f"out_{tag}"
        rc = cd.main([
            "--bundle", str(bpath), "--narrative", str(npath), "--spec", str(spath),
            "--out", str(out), "--archive", str(tmp_path / f"verified_{tag}"),
            "--report", str(out / "report.md"),
        ])
        assert rc == 0, "报告自检应零违规"
        outs.append((out / "coverage_matrix.json").read_text(encoding="utf-8"))
    assert outs[0] == outs[1], "同一输入两次运行必须逐字节相同"
    summary = json.loads((tmp_path / "out_a" / "distill_summary.json").read_text(encoding="utf-8"))
    assert summary["violations"] == []
    assert all(chk["ok"] for chk in summary["self_checks"])


# --------------------------------------------------------------------------- #
# 7. 域中性
# --------------------------------------------------------------------------- #
class TestDomainNeutrality:
    def test_zero_domain_hits(self, tmp_path: Path) -> None:
        paths = [
            "tools/distill/campaign_distill.py",
            "tests/test_campaign_distill.py",
        ]
        json_out = tmp_path / "scan.json"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools/audit/domain_leak_scan.py"),
             "--paths", *paths, "--json", str(json_out)],
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # 反空转：扫描器对不存在的路径静默跳过（只打印"扫描 N 个文件"），
        # 若路径列表写错/文件被删，命中 0 是假绿。这里必须证明每个路径都真的被扫到。
        report = json.loads(json_out.read_text(encoding="utf-8"))
        assert report["hits"] == []
        assert report["files_missing"] == [], f"存在未扫到的路径（假绿风险）: {report['files_missing']}"
        assert len(report["files_scanned"]) == len(paths), report["files_scanned"]