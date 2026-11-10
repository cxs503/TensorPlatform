#!/usr/bin/env python3
"""P143W2C-N03 验收测试：run-manifest 接进回填器（内容可追溯化）。

判据（先于实现，机器可裁；编号与战役节点一致）
------------------------------------------------
a  规模与三分类：N≥200 条历史评价试回填 → {完整链授权, 缺件封顶, 校验失败} 计数
   与夹具口径**精确相等**；完整链授权行在结算面 ``chain_blocked = 0``。
b  篡改必失败：改动单个工件一个字节 → 该 run 校验失败，不进完整链。
c  幂等：重跑第二次 0 行变动（两表指纹逐字节相等、报表摘要相等）。
d  一致性：链完整度统计与手写 SQL 精确相等。
e  不得借回填提级：回填前后 S3+/S4/S5/S-N ``confirmed`` 行数不增（行级指纹不变）。
f  pytest ≥20 项，含红队 5 例：缺 sha / 错 digest / 伪 anchor / 跨 run 混用 / 自签 manifest。

夹具全部合成域名（SyntheticRepo / claim-bf-* / agent-bf），零学科语义。
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from sqlalchemy import create_engine, text

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.evidence_chain import backfill_fixture as fx  # noqa: E402
from tools.evidence_chain import backfill_wiring as bw  # noqa: E402
from tools.evidence_chain import evidence_chain as ec  # noqa: E402
from tools.evidence_chain_link import CHAIN_COLUMNS, CHAIN_TABLE, VIEW_CHAIN_AUDIT  # noqa: E402


# ──────────────────────────────── 夹具 ──────────────────────────────── #

@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """240 条历史评价 × 9 类形态（只读使用：会改文件的测试自建小语料）。"""
    root = tmp_path_factory.mktemp("corpus_full")
    return fx.build_corpus(root)


@pytest.fixture()
def conn():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    connection = engine.connect()
    yield connection
    connection.close()


def _seed(conn, corpus) -> list[dict]:
    fx.seed_evaluations(conn, corpus.evaluations)
    rows = conn.execute(text("SELECT * FROM outcome_evaluations ORDER BY evaluation_id")).fetchall()
    return [dict(r._mapping) for r in rows]


def _backfill(conn, corpus, evaluations=None) -> dict:
    evaluations = evaluations or _seed(conn, corpus)
    return bw.apply_backfill(
        conn,
        evaluations,
        fx.run_dir_resolver(corpus),
        attestations=corpus.attestations,
    )


def _small_corpus(tmp_path, plan: dict[str, int]):
    return fx.build_corpus(tmp_path / "corpus", plan)


def _grant_for(report: dict, evaluation_id: str) -> dict:
    for grant in report["grants"]:
        if grant["evaluation_id"] == evaluation_id:
            return grant
    raise AssertionError(f"未找到裁决 {evaluation_id}")


def _first_of_kind(corpus, kind: str) -> str:
    for ev_id, k in corpus.kinds.items():
        if k == kind:
            return ev_id
    raise AssertionError(f"语料里没有 {kind} 形态")


# ═══════════════════════ a. 规模与三分类 + 结算面 ═══════════════════════ #

def test_a1_corpus_scale_at_least_200(corpus):
    """判据 a 规模：N≥200 条历史评价。"""
    assert len(corpus.evaluations) >= 200
    assert len(corpus.evaluations) == 240


def test_a2_three_class_counts_exact(conn, corpus):
    """判据 a 三分类计数与夹具口径精确相等。"""
    report = _backfill(conn, corpus)
    assert report["class_counts"] == corpus.expected_class_counts()
    assert report["class_counts"] == {
        bw.GRANT_GRANTED: 146,
        bw.GRANT_CAPPED: 55,
        bw.GRANT_FAILED: 39,
    }
    assert sum(report["class_counts"].values()) == len(corpus.evaluations)


def test_a3_granted_rows_chain_blocked_zero(conn, corpus):
    """判据 a 后半：完整链授权行在结算面 chain_blocked 全 0。"""
    report = _backfill(conn, corpus)
    settlement = report["settlement"]
    assert settlement["granted_rows"] == 146
    assert settlement["granted_blocked"] == 0
    assert settlement["missing_audit_rows"] == 0
    assert settlement["violations"] == []
    assert settlement["ok"] is True
    blocked = conn.execute(
        text(f"SELECT count(*) FROM {VIEW_CHAIN_AUDIT} WHERE chain_blocked = TRUE")
    ).scalar()
    expected_blocked = conn.execute(
        text(
            f"SELECT count(*) FROM {VIEW_CHAIN_AUDIT} a "
            f"JOIN {bw.BACKFILL_TABLE} b ON b.evaluation_id = a.evaluation_id "
            "WHERE b.granted = FALSE AND a.grade IN ('S3','S4','S5','S-N') "
            "AND a.status = 'confirmed'"
        )
    ).scalar()
    assert int(blocked) == int(expected_blocked)
    assert int(blocked) > 0


def test_a4_granted_chain_is_complete_with_full_completeness(conn, corpus):
    report = _backfill(conn, corpus)
    granted = [g["evaluation_id"] for g in report["grants"] if g["granted"]]
    rows = conn.execute(
        text(f"SELECT chain_status, completeness, capped FROM {CHAIN_TABLE} WHERE evaluation_id IN "
             f"({', '.join(':e%d' % i for i in range(len(granted)))})"),
        {f"e{i}": ev for i, ev in enumerate(granted)},
    ).fetchall()
    assert len(rows) == len(granted)
    for status, completeness, capped in rows:
        assert status == "complete"
        assert float(completeness) == 1.0
        assert bool(capped) is False


def test_a5_granted_rows_carry_data_anchor_and_commit(conn, corpus):
    """判据 a：授权链可含 data anchor + commit sha（内容可追溯的落地形态）。"""
    report = _backfill(conn, corpus)
    sample = next(g for g in report["grants"] if g["granted"])
    verdict = sample["verdict"]
    assert len(verdict["data_anchor"]) == 64
    assert len(verdict["commit_sha"]) == 7
    assert sample["refs"]["data"] == f"sha256:{verdict['data_anchor']}"
    assert verdict["commit_sha"] in sample["refs"]["code"]
    row = conn.execute(
        text(f"SELECT data_ref, code_ref, config_ref FROM {CHAIN_TABLE} WHERE evaluation_id = :e"),
        {"e": sample["evaluation_id"]},
    ).first()
    assert row[0] == f"sha256:{verdict['data_anchor']}"
    assert verdict["commit_sha"] in row[1]
    assert row[2].startswith("sha256:")


def test_a6_nongranted_capped_to_e1(conn, corpus):
    """fail-closed：未授权行一律封顶 E1，不继承申报 E。"""
    report = _backfill(conn, corpus)
    capped_ids = [g["evaluation_id"] for g in report["grants"] if not g["granted"]]
    rows = conn.execute(
        text(f"SELECT evaluation_id, e_level_granted, capped FROM {CHAIN_TABLE} "
             f"WHERE evaluation_id IN ({', '.join(':e%d' % i for i in range(len(capped_ids)))})"),
        {f"e{i}": ev for i, ev in enumerate(capped_ids)},
    ).fetchall()
    assert len(rows) == len(capped_ids)
    for _ev, e_level, capped in rows:
        assert e_level == "E1"
        assert bool(capped) is True


def test_a7_every_nongranted_row_has_structured_reason(conn, corpus):
    """结构化 reason：未授权行必须带可机读 code（词表内常量）。"""
    report = _backfill(conn, corpus)
    vocab = {v for k, v in vars(bw).items() if k.startswith("B_") and isinstance(v, str)}
    for grant in report["grants"]:
        if grant["granted"]:
            assert grant["codes"] == [bw.B_VERIFIED]
            continue
        assert grant["codes"], grant["evaluation_id"]
        assert any(code in vocab for code in grant["codes"]), grant["codes"]
        assert grant["reason_class"] in (bw.GRANT_CAPPED, bw.GRANT_FAILED)


def test_a8_manifest_digest_matches_canonical_recompute(conn, corpus):
    """digest = sha256(canonical_json(manifest))，与 evidence_chain 原语逐位一致。"""
    report = _backfill(conn, corpus)
    sample = next(g for g in report["grants"] if g["granted"])
    manifest = json.loads((pathlib.Path(sample["run_dir"]) / "run_manifest.json").read_text("utf-8"))
    assert sample["verdict"]["manifest_digest"] == ec.manifest_digest(manifest)
    assert sample["verdict"]["attested_digest"] == ec.manifest_digest(manifest)


# ═══════════════════════════ b. 篡改必失败 ═══════════════════════════ #

def test_b1_single_byte_tamper_blocks_grant(conn, tmp_path):
    """判据 b：改一个工件一个字节 → 该 run 校验失败，不进完整链。"""
    corpus = _small_corpus(tmp_path, {"intact": 4})
    before = _backfill(conn, corpus)
    assert before["class_counts"][bw.GRANT_GRANTED] == 4

    target = _first_of_kind(corpus, "intact")
    run_dir = corpus.run_dirs[target]
    data_file = run_dir / "result.json"
    raw = bytearray(data_file.read_bytes())
    raw[0] = raw[0] ^ 0x01
    data_file.write_bytes(bytes(raw))

    after = bw.apply_backfill(
        conn,
        [dict(r._mapping) for r in conn.execute(text("SELECT * FROM outcome_evaluations")).fetchall()],
        fx.run_dir_resolver(corpus),
        attestations=corpus.attestations,
    )
    grant = _grant_for(after, target)
    assert grant["granted"] is False
    assert grant["reason_class"] == bw.GRANT_FAILED
    assert bw.B_ARTIFACT_HASH in grant["codes"]
    assert after["class_counts"][bw.GRANT_GRANTED] == 3
    assert target not in [g["evaluation_id"] for g in after["grants"] if g["granted"]]
    # 链行同步降级（只降不升），结算面拦下
    row = conn.execute(
        text(f"SELECT chain_status, capped FROM {CHAIN_TABLE} WHERE evaluation_id = :e"),
        {"e": target},
    ).first()
    assert row[0] != "complete" and bool(row[1]) is True
    blocked = conn.execute(
        text(f"SELECT chain_blocked FROM {VIEW_CHAIN_AUDIT} WHERE evaluation_id = :e"),
        {"e": target},
    ).scalar()
    grade = conn.execute(
        text("SELECT grade FROM outcome_evaluations WHERE evaluation_id = :e"), {"e": target}
    ).scalar()
    assert bool(blocked) is (grade in ("S3", "S4", "S5", "S-N"))


def test_b2_tamper_size_only_detected(conn, tmp_path):
    """判据 b 补充：只改长度（追加一字节）也必须被 size 闸拦下。"""
    corpus = _small_corpus(tmp_path, {"intact": 2})
    _backfill(conn, corpus)
    target = _first_of_kind(corpus, "intact")
    (corpus.run_dirs[target] / "result.json").write_bytes(
        (corpus.run_dirs[target] / "result.json").read_bytes() + b"x"
    )
    after = bw.apply_backfill(
        conn,
        [dict(r._mapping) for r in conn.execute(text("SELECT * FROM outcome_evaluations")).fetchall()],
        fx.run_dir_resolver(corpus),
        attestations=corpus.attestations,
    )
    grant = _grant_for(after, target)
    assert grant["granted"] is False
    assert bw.B_ARTIFACT_HASH in grant["codes"]  # 追加字节同时改 sha；任一失败即封顶


def test_b3_artifact_outside_run_dir_rejected(conn, tmp_path):
    """判据 b 补充：工件路径逃出 run 目录 → 拒绝（防「借别处文件充数」）。"""
    corpus = _small_corpus(tmp_path, {"intact": 1})
    target = _first_of_kind(corpus, "intact")
    run_dir = corpus.run_dirs[target]
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    outside = run_dir.parent / "outside.json"
    outside.write_text('{"x": 1}\n', encoding="utf-8")
    manifest["outputs"][0]["uri"] = str(outside)  # 绝对路径指到 run 目录之外
    manifest["outputs"][0]["checksum_sha256"] = ec.sha256_file(outside)
    manifest["outputs"][0]["size_bytes"] = outside.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verdict = bw.verify_run_content(run_dir, attestations=corpus.attestations)
    assert verdict.codes and bw.B_ARTIFACT_ESCAPE in verdict.codes
    assert verdict.content_verified is False

    # 相对路径越界则由 evidence_chain 的路径解析器先拦（另一种越界形态）
    manifest["outputs"][0]["uri"] = f"../{outside.name}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verdict2 = bw.verify_run_content(run_dir, attestations=corpus.attestations)
    assert bw.B_ARTIFACT_PATH in verdict2.codes


# ═══════════════════════════ c. 幂等 ═══════════════════════════ #

def test_c1_second_run_zero_row_changes(conn, corpus):
    """判据 c：重跑第二次两表 0 行变动（内容幂等，不靠删了重插）。"""
    evaluations = _seed(conn, corpus)
    first = _backfill(conn, corpus, evaluations)
    fp1 = bw.table_fingerprint(conn)
    second = _backfill(conn, corpus, evaluations)
    fp2 = bw.table_fingerprint(conn)

    assert first["writes"]["chain"] == {"inserted": len(evaluations)}
    assert second["writes"]["chain"] == {"unchanged": len(evaluations)}
    assert second["writes"]["backfill"] == {"unchanged": len(evaluations)}
    assert fp1 == fp2
    assert first["report_digest"] == second["report_digest"]


def test_c2_report_bytes_identical_across_runs(conn, corpus, tmp_path):
    """判据 c：报表文件逐字节一致（diff 为空）。"""
    evaluations = _seed(conn, corpus)
    first = _backfill(conn, corpus, evaluations)
    p1 = bw.write_chain_report(first, tmp_path, "r1.json")
    second = _backfill(conn, corpus, evaluations)
    p2 = bw.write_chain_report(second, tmp_path, "r2.json")
    assert p1.read_bytes() == p2.read_bytes()


def test_c3_idempotent_after_tamper(conn, tmp_path):
    """判据 c 加强：篡改 → 回填 → 再回填，第二次仍 0 变动。"""
    corpus = _small_corpus(tmp_path, {"intact": 3})
    evaluations = _seed(conn, corpus)
    _backfill(conn, corpus, evaluations)
    target = _first_of_kind(corpus, "intact")
    data_file = corpus.run_dirs[target] / "result.json"
    raw = bytearray(data_file.read_bytes())
    raw[-1] = raw[-1] ^ 0xFF
    data_file.write_bytes(bytes(raw))

    _backfill(conn, corpus, evaluations)
    fp = bw.table_fingerprint(conn)
    again = _backfill(conn, corpus, evaluations)
    assert bw.table_fingerprint(conn) == fp
    assert again["writes"]["chain"] == {"unchanged": 3}
    assert again["writes"]["backfill"] == {"unchanged": 3}


# ═══════════════════════════ d. SQL 对拍 ═══════════════════════════ #

def test_d1_python_counts_equal_sql(conn, corpus):
    """判据 d：链完整度统计与手写 SQL 精确相等。"""
    report = _backfill(conn, corpus)
    assert report["sql_consistency"]["ok"] is True
    sql = report["sql_consistency"]["sql"]
    for key in ("class_counts_equal", "granted_equal", "chain_status_equal",
                "chain_rows_equal", "backfill_rows_equal"):
        assert report["sql_consistency"][key] is True, key
    assert sql["class_counts"] == report["class_counts"]


def test_d2_handwritten_sql_matches(conn, corpus):
    """判据 d：另写一份 SQL（不借工具函数）对拍三分类与链状态。"""
    report = _backfill(conn, corpus)
    granted = conn.execute(
        text(f"SELECT count(*) FROM {CHAIN_TABLE} WHERE chain_status = 'complete'")
    ).scalar()
    capped = conn.execute(
        text(f"SELECT count(*) FROM {bw.BACKFILL_TABLE} WHERE reason_class = 'capped_missing'")
    ).scalar()
    failed = conn.execute(
        text(f"SELECT count(*) FROM {bw.BACKFILL_TABLE} WHERE reason_class = 'verify_failed'")
    ).scalar()
    assert int(granted) == report["class_counts"][bw.GRANT_GRANTED]
    assert int(capped) == report["class_counts"][bw.GRANT_CAPPED]
    assert int(failed) == report["class_counts"][bw.GRANT_FAILED]
    partial = conn.execute(
        text(f"SELECT count(*) FROM {CHAIN_TABLE} WHERE chain_status = 'partial' AND capped = TRUE")
    ).scalar()
    assert int(partial) == report["chains"]["partial"]


def test_d3_completeness_stats_match(conn, corpus):
    """判据 d：完整度均值/总行数两口径相等。"""
    report = _backfill(conn, corpus)
    py_avg = sum(
        round(len(g["present"]) / 5, 4) for g in report["grants"]
    ) / len(report["grants"])
    sql_avg = conn.execute(text(f"SELECT avg(completeness) FROM {CHAIN_TABLE}")).scalar()
    assert abs(float(sql_avg) - py_avg) < 1e-9
    assert conn.execute(text(f"SELECT count(*) FROM {CHAIN_TABLE}")).scalar() == len(report["grants"])


# ══════════════════════ e. 不得借回填提级 ══════════════════════ #

def test_e1_no_promotion_of_confirmed_high_grade(conn, corpus):
    """判据 e：回填前后 S3+/S4/S5/S-N confirmed 行数不增，行级指纹不变。"""
    report = _backfill(conn, corpus)
    guard = report["promotion_guard"]
    assert guard["ok"] is True
    assert guard["confirmed_high_grade_delta"] == 0
    assert guard["fingerprint_unchanged"] is True
    assert guard["before"]["confirmed_high_grade"] == guard["after"]["confirmed_high_grade"]
    assert guard["before"]["confirmed_high_grade"] > 0

    statuses = conn.execute(
        text("SELECT status, count(*) FROM outcome_evaluations GROUP BY 1")
    ).fetchall()
    assert dict(statuses) == {"confirmed": len(corpus.evaluations)}


def test_e2_backfill_never_touches_grade_or_level(conn, corpus):
    """判据 e：回填不动评价表任何列（只写链表/回填表）。"""
    evaluations = _seed(conn, corpus)
    snapshot = {
        r["evaluation_id"]: (r["grade"], r["e_level"], r["status"]) for r in evaluations
    }
    _backfill(conn, corpus, evaluations)
    after = {
        r[0]: (r[1], r[2], r[3])
        for r in conn.execute(
            text("SELECT evaluation_id, grade, e_level, status FROM outcome_evaluations")
        ).fetchall()
    }
    assert after == snapshot


def test_e3_grant_upgrades_only_chain_not_grade(conn, corpus):
    """授权只把「链」补全，不把评价的 S 级/E 级抬上去。"""
    evaluations = _seed(conn, corpus)
    target = _first_of_kind(corpus, "intact")
    report = _backfill(conn, corpus, evaluations)
    grant = _grant_for(report, target)
    assert grant["granted"] is True
    row = conn.execute(
        text(f"SELECT e_level_requested, e_level_granted, grade FROM {CHAIN_TABLE} "
             f"WHERE evaluation_id = :e"),
        {"e": target},
    ).first()
    ev_e_level = conn.execute(
        text("SELECT e_level FROM outcome_evaluations WHERE evaluation_id = :e"), {"e": target}
    ).scalar()
    assert row[0] == ev_e_level
    assert row[1] == ev_e_level          # 授权不抬高 E
    assert grant["verdict"]["actual_run_id"] is not None
    grade = conn.execute(
        text("SELECT grade FROM outcome_evaluations WHERE evaluation_id = :e"), {"e": target}
    ).scalar()
    assert row[2] == grade


def test_e4_existing_form_only_chain_is_downgraded_when_content_fails(conn, corpus):
    """旧链（只验形态）遇到内容不可追溯 → 只降：complete → partial + capped。"""
    evaluations = _seed(conn, corpus)
    target = _first_of_kind(corpus, "tampered")
    from tools.evidence_chain_link import store_chain

    legacy = {c: None for c in CHAIN_COLUMNS}
    legacy.update({
        "chain_id": f"chain:{target}", "evaluation_id": target, "artifact_id": "artifact-legacy",
        "conclusion_ref": f"evaluation:{target}", "grade": "S3", "e_level_requested": "E3",
        "e_level_granted": "E3", "present_elements": json.dumps(list(bw.ELEMENT_KEYS)),
        "missing_elements": "[]", "invalid_elements": "[]", "completeness": 1.0,
        "chain_status": "complete", "capped": False, "codes": "[]",
        "created_at": "2026-11-01T00:00:00+00:00",
    })
    report = _backfill(conn, corpus, evaluations)  # 建表
    store_chain(conn, legacy, commit=True)

    row = conn.execute(
        text(f"SELECT chain_status, capped FROM {CHAIN_TABLE} WHERE evaluation_id = :e"), {"e": target}
    ).first()
    assert row[0] == "complete"  # 回填前：形态完整 → 旧链 complete

    report = _backfill(conn, corpus, evaluations)
    row = conn.execute(
        text(f"SELECT chain_status, capped, e_level_granted FROM {CHAIN_TABLE} WHERE evaluation_id = :e"),
        {"e": target},
    ).first()
    assert row[0] != "complete" and bool(row[1]) is True and row[2] == "E1"
    assert report["promotion_guard"]["ok"] is True


# ═══════════════════════ f. 红队 5 例 ═══════════════════════ #

def test_f1_redteam_missing_sha_rejected(conn, corpus):
    """红队①缺 sha：manifest 工件不声明 checksum_sha256 → 不授权。"""
    target = _first_of_kind(corpus, "redteam_no_sha")
    report = _backfill(conn, corpus)
    grant = _grant_for(report, target)
    assert grant["granted"] is False
    assert grant["reason_class"] == bw.GRANT_FAILED
    assert {bw.B_ARTIFACT_NO_SHA, bw.B_MANIFEST_STRUCT} & set(grant["codes"])


def test_f1b_strict_verifier_flags_missing_sha_directly(tmp_path):
    """红队①单元面：逐件严格复算把「缺 sha」判失败（不等价于通过）。"""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "a.json").write_text('{"a":1}\n', encoding="utf-8")
    code, detail = bw._verify_output_strict({"uri": "a.json", "size_bytes": 8}, run_dir)
    assert code == bw.B_ARTIFACT_NO_SHA and "checksum" in detail


def test_f2_redteam_wrong_digest_rejected(conn, corpus):
    """红队②错 digest：登记后改 manifest → 摘要与独立锚不符 → 不授权。"""
    target = _first_of_kind(corpus, "redteam_wrong_digest")
    report = _backfill(conn, corpus)
    grant = _grant_for(report, target)
    assert grant["granted"] is False
    assert grant["reason_class"] == bw.GRANT_FAILED
    assert bw.B_DIGEST_MISMATCH in grant["codes"]
    assert grant["verdict"]["manifest_digest"] != grant["verdict"]["attested_digest"]


def test_f3_redteam_fake_anchor_rejected(conn, corpus):
    """红队③伪 anchor：评价声明一个不存在的 data 摘要 → 内容复算对不上 → 不授权。"""
    target = _first_of_kind(corpus, "redteam_fake_anchor")
    report = _backfill(conn, corpus)
    grant = _grant_for(report, target)
    assert grant["granted"] is False
    assert grant["reason_class"] == bw.GRANT_FAILED
    assert bw.B_ANCHOR_MISMATCH in grant["codes"]
    assert grant["verdict"]["data_anchor"] is None


def test_f4_redteam_cross_run_rejected(conn, corpus):
    """红队④跨 run 混用：目录是本 run、manifest 却声称别的 run → 拒绝。"""
    target = _first_of_kind(corpus, "redteam_cross_run")
    report = _backfill(conn, corpus)
    grant = _grant_for(report, target)
    assert grant["granted"] is False
    assert grant["reason_class"] == bw.GRANT_FAILED
    assert bw.B_CROSS_RUN in grant["codes"]
    assert grant["verdict"]["expected_run_id"] != grant["verdict"]["actual_run_id"]


def test_f5_redteam_self_signed_rejected(conn, corpus):
    """红队⑤自签 manifest：无独立登记锚 → 自己给自己发证不算数。"""
    report = _backfill(conn, corpus)
    self_signed = [ev for ev, kind in corpus.kinds.items() if kind == "self_signed"]
    assert len(self_signed) == 15
    for ev_id in self_signed:
        grant = _grant_for(report, ev_id)
        assert grant["granted"] is False
        assert grant["reason_class"] == bw.GRANT_FAILED
        assert bw.B_NO_ATTESTATION in grant["codes"]
    assert report["class_counts"][bw.GRANT_FAILED] >= len(self_signed)


def test_f6_no_auto_build_manifest_even_if_layer1_would(tmp_path, conn):
    """红队⑤加强：Layer1 会 auto-build 并给 E3，Layer2 必须拒绝自签授权。"""
    corpus = _small_corpus(tmp_path, {"intact": 1})
    target = _first_of_kind(corpus, "intact")
    run_dir = corpus.run_dirs[target]
    (run_dir / "run_manifest.json").unlink()          # 工件在，manifest 没了

    layer1 = bw.backfill_single_run(run_dir)          # 旧口径：现场造 manifest
    assert layer1.manifest_built is True
    assert layer1.chain_level == bw.CHAIN_E3          # ← 这就是自签漏洞

    verdict = bw.verify_run_content(run_dir, attestations=corpus.attestations)
    assert verdict.codes == [bw.B_MANIFEST_MISSING]
    grant = bw.grant_chain_for_evaluation(corpus.evaluations[0], run_dir,
                                          attestations=corpus.attestations)
    assert grant.granted is False and grant.reason_class == bw.GRANT_CAPPED


# ═══════════════════════ 其它机器判据 ═══════════════════════ #

def test_g1_classify_codes_three_way():
    assert bw.classify_codes([bw.B_VERIFIED]) == bw.GRANT_GRANTED
    assert bw.classify_codes([bw.B_MANIFEST_MISSING]) == bw.GRANT_CAPPED
    assert bw.classify_codes([bw.B_ARTIFACT_MISSING, bw.B_DATA_MISSING]) == bw.GRANT_CAPPED
    assert bw.classify_codes([bw.B_ARTIFACT_MISSING, bw.B_ARTIFACT_HASH]) == bw.GRANT_FAILED
    assert bw.classify_codes(["CHAIN_MISSING_DATA"]) == bw.GRANT_CAPPED


def test_g2_chain_row_columns_match_contract(conn, corpus):
    report = _backfill(conn, corpus)
    sample = report["grants"][0]
    row = bw.chain_row_from_grant(
        bw.ChainGrant(evaluation_id=sample["evaluation_id"]), {"evaluation_id": sample["evaluation_id"]}
    )
    assert tuple(row) == CHAIN_COLUMNS


def test_g3_load_attestations_skips_bad_lines(tmp_path):
    path = tmp_path / "registry.local.jsonl"
    good = "a" * 64
    path.write_text(
        json.dumps({"run_id": "r1", "manifest_sha256": good}) + "\n"
        + "not-json\n"
        + json.dumps({"run_id": "", "manifest_sha256": good}) + "\n"
        + json.dumps({"run_id": "r2", "manifest_sha256": "short"}) + "\n"
        + json.dumps({"run_id": "r3", "manifest_sha256": "B" * 64}) + "\n",
        encoding="utf-8",
    )
    loaded = bw.load_attestations(path)
    assert loaded == {"r1": good, "r3": "b" * 64}
    assert bw.load_attestations(None) == {}


def test_g4_run_dir_missing_is_capped(conn, tmp_path):
    corpus = _small_corpus(tmp_path, {"intact": 1})
    evaluations = _seed(conn, corpus)
    report = bw.apply_backfill(
        conn, evaluations, lambda ev: None, attestations=corpus.attestations
    )
    assert report["class_counts"][bw.GRANT_CAPPED] == 1
    assert bw.B_NO_RUN_DIR in report["grants"][0]["codes"]


def test_g5_report_json_is_canonical_and_stable(conn, corpus, tmp_path):
    evaluations = _seed(conn, corpus)
    report = _backfill(conn, corpus, evaluations)
    path = bw.write_chain_report(report, tmp_path)
    text_body = path.read_text(encoding="utf-8")
    assert text_body.endswith("\n")
    assert json.loads(text_body)["report_digest"] == report["report_digest"]
    assert text_body == json.dumps(json.loads(text_body), indent=2, sort_keys=True,
                                  ensure_ascii=False) + "\n"


def test_g6_dry_run_writes_nothing(conn, corpus):
    evaluations = _seed(conn, corpus)
    report = bw.apply_backfill(
        conn, evaluations[:20], fx.run_dir_resolver(corpus),
        attestations=corpus.attestations, write=False,
    )
    assert report["totals"]["evaluations"] == 20
    assert conn.execute(text(f"SELECT count(*) FROM {bw.BACKFILL_TABLE}")).scalar() == 0
    assert conn.execute(text(f"SELECT count(*) FROM {CHAIN_TABLE}")).scalar() == 0

# ═══════════════════════ CLI 端到端 ═══════════════════════ #

def test_h1_cli_end_to_end_and_idempotent(tmp_path):
    """CLI 接线：--db-url × --run-map → 报表落盘；二次调用报表逐字节一致。"""
    corpus = _small_corpus(tmp_path, {"intact": 5, "tampered": 2, "self_signed": 1})
    db_path = tmp_path / "chain.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    conn = engine.connect()
    fx.seed_evaluations(conn, corpus.evaluations)
    conn.close()

    run_map = {ev_id: str(path) for ev_id, path in corpus.run_dirs.items()}
    map_path = tmp_path / "run_map.json"
    map_path.write_text(json.dumps(run_map), encoding="utf-8")
    out_dir = tmp_path / "out"

    argv = ["--db-url", f"sqlite+pysqlite:///{db_path}", "--run-root", str(corpus.run_root),
            "--attestations", str(corpus.registry_path), "--out-dir", str(out_dir),
            "--run-map", str(map_path)]
    assert bw.chain_backfill_main(argv) == 0
    first = (out_dir / "chain_backfill_report.json").read_bytes()
    report = json.loads(first)
    assert report["class_counts"] == {"full_chain_granted": 5, "capped_missing": 0,
                                      "verify_failed": 3}
    assert report["settlement"]["ok"] is True
    assert report["promotion_guard"]["ok"] is True
    assert report["sql_consistency"]["ok"] is True

    assert bw.chain_backfill_main(argv) == 0
    assert (out_dir / "chain_backfill_report.json").read_bytes() == first
