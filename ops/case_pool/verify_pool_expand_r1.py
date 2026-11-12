#!/usr/bin/env python3
"""pool-expand-r1 独立回执核验（不依赖加载器内部逻辑，全部用 SQL 重新取证）。

跑一次产出 artifacts/m16051/verify_report.json + 控制台回执，覆盖任务四项要求：
  ①规模/轮转：六仓各 7 例；案例总数、每仓新增数
  ②不重复：全池 case_code 唯一、归一化名称无跨行重复、与既有 84 例 case_code 无交集
  ③锚点：批次行 linked_anchor 全空（=可被 case_feeder 供给），并列出 feature_ledger 现存
          CASE-* 锚中"未被任何池行占用"者（说明留空判断的依据面）
  ④事务+回执：source='pool-expand-r1' 行数、待供给 pending 数、10 例全字段抽样、
          以及幂等探针（事务内重复插入 -> 0 行，池总数不变，回滚）

用法：python3 ops/case_pool/verify_pool_expand_r1.py
"""
from __future__ import annotations

import json
import os
import re

import psycopg

DSN = os.environ.get("TP_DSN", "dbname=%s user=postgres" % os.environ.get("TP_PG_DB", "tensorplatform"))
BATCH = "pool-expand-r1"
MISSION = 16051
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                   "artifacts", "m16051", "verify_report.json"))
REPOS = ["TensorLBM", "TensorFEM", "TensorFVM", "TensorSPH", "TensorDG", "TensorDEM"]

Q = {
    "total": "select count(*) from tensor_case_pool",
    "distinct_codes": "select count(distinct case_code) from tensor_case_pool",
    "dup_codes": "select case_code, count(*) from tensor_case_pool group by 1 having count(*)>1",
    "by_repo_status": """select repo, status, count(*) from tensor_case_pool group by 1,2 order by 1,2""",
    "batch_by_repo": "select repo, count(*) from tensor_case_pool where source=%s group by 1 order by 1",
    "batch_total": "select count(*) from tensor_case_pool where source=%s",
    "batch_mission": "select count(*) from tensor_case_pool where source=%s and mission_id=%s",
    "batch_status": "select status, count(*) from tensor_case_pool where source=%s group by 1",
    "batch_blank_fields": """select count(*) from tensor_case_pool where source=%s
                              and (coalesce(btrim(case_code),'')='' or coalesce(btrim(name),'')=''
                                or coalesce(btrim(ref_source),'')='' or coalesce(btrim(tol_policy),'')='')""",
    "batch_with_anchor": "select count(*) from tensor_case_pool where source=%s and linked_anchor is not null",
    "batch_ref_with_doi": """select count(*) from tensor_case_pool where source=%s and ref_source like '%%DOI%%'""",
    "batch_tol_quantified": """select count(*) from tensor_case_pool where source=%s
                                and (tol_policy ~ '<[0-9]' or tol_policy ~ '<=?-' or tol_policy ~ '区间'
                                     or tol_policy ~ '>=' or tol_policy ~ '[0-9]+\\.[0-9]+ *- *[0-9]')""",
    "batch_tol_unquantified": """select case_code, tol_policy from tensor_case_pool where source=%s
                                 and not (tol_policy ~ '<[0-9]' or tol_policy ~ '<=?-' or tol_policy ~ '区间'
                                          or tol_policy ~ '>=' or tol_policy ~ '[0-9]+\\.[0-9]+ *- *[0-9]')""",
    "pend_total": "select count(*) from tensor_case_pool where status='pending'",
    "pend_supplyable": "select count(*) from tensor_case_pool where status='pending' and linked_anchor is null",
    "pend_batch_supplyable": """select count(*) from tensor_case_pool
                                 where source=%s and status='pending' and linked_anchor is null""",
    "dup_norm_name": """select lower(regexp_replace(name, '[[:space:]/·,;:()\[\]（）_-]+', '', 'g')) k,
                               count(*), string_agg(case_code, ',')
                          from tensor_case_pool group by 1 having count(*)>1""",
    "batch_codes": "select case_code from tensor_case_pool where source=%s order by case_code",
    "cross_check_old_codes": """select count(*) from tensor_case_pool a join tensor_case_pool b
                                  on a.case_code=b.case_code and a.source=%s and b.source is null""",
    "ledger_case_anchors_unclaimed": """select l.slug, l.title, l.domain, l.status from feature_ledger l
                                         where l.slug like 'CASE-%%'
                                           and not exists (select 1 from tensor_case_pool p
                                                            where p.linked_anchor = l.slug)
                                         order by l.slug""",
}


def main():
    out = {"batch": BATCH, "mission_id": MISSION, "dsn": DSN}
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        def one(key, args=()):
            cur.execute(Q[key], args)
            return cur.fetchone()[0]

        out["pool_total"] = one("total")
        out["distinct_case_codes"] = one("distinct_codes")
        cur.execute(Q["dup_codes"])
        out["duplicate_case_codes"] = cur.fetchall()
        cur.execute(Q["by_repo_status"])
        out["pool_by_repo_status"] = [{"repo": r[0], "status": r[1], "rows": r[2]} for r in cur.fetchall()]
        cur.execute(Q["batch_by_repo"], (BATCH,))
        out["batch_by_repo"] = [{"repo": r[0], "rows": r[1]} for r in cur.fetchall()]
        out["batch_total"] = one("batch_total", (BATCH,))
        out["batch_with_mission_16051"] = one("batch_mission", (BATCH, MISSION))
        cur.execute(Q["batch_status"], (BATCH,))
        out["batch_status"] = {r[0]: r[1] for r in cur.fetchall()}
        out["batch_blank_required_fields"] = one("batch_blank_fields", (BATCH,))
        out["batch_rows_with_linked_anchor"] = one("batch_with_anchor", (BATCH,))
        out["batch_ref_source_with_doi"] = one("batch_ref_with_doi", (BATCH,))
        out["batch_tol_policy_quantified"] = one("batch_tol_quantified", (BATCH,))
        cur.execute(Q["batch_tol_unquantified"], (BATCH,))
        out["batch_tol_unquantified"] = [{"case_code": r[0], "tol_policy": r[1]} for r in cur.fetchall()]
        out["pending_total"] = one("pend_total")
        out["pending_supplyable_no_anchor"] = one("pend_supplyable")
        out["pending_batch_supplyable"] = one("pend_batch_supplyable", (BATCH,))
        cur.execute(Q["dup_norm_name"])
        out["duplicate_normalized_names"] = [{"key": r[0], "n": r[1], "codes": r[2]} for r in cur.fetchall()]
        out["batch_codes_also_in_old_rows"] = one("cross_check_old_codes", (BATCH,))
        cur.execute(Q["ledger_case_anchors_unclaimed"])
        out["ledger_case_anchors_unclaimed"] = [{"slug": r[0], "title": r[1], "domain": r[2], "status": r[3]}
                                                for r in cur.fetchall()]
        cur.execute(Q["batch_codes"], (BATCH,))
        codes = [r[0] for r in cur.fetchall()]
        out["batch_codes"] = codes
        out["batch_rotation_check"] = {d["repo"]: d["rows"] for d in out["batch_by_repo"]}

        # 抽样 10 例全字段（确定性：排序后每 4 个取 1）
        sample = codes[::4][:10]
        cur.execute("""select * from tensor_case_pool where case_code = any(%s) order by case_code""", (sample,))
        cols = [d[0] for d in cur.description]
        out["sample_10_full_fields"] = [{k: (str(v) if v is not None else None) for k, v in zip(cols, row)}
                                        for row in cur.fetchall()]

        # 幂等探针：同事务内重复插入同一 case_code -> 必须 0 行，且池总数不变（随后回滚）
        probe_code = codes[0]
        before = one("total")
        conn.rollback()  # 结束只读事务，给幂等探针一个干净的事务边界
        with conn.transaction():
            cur.execute("""insert into tensor_case_pool (case_code, repo, name, ref_source, tol_policy,
                             tier, status, mission_id, source)
                           values (%s,'TensorLBM','idempotency-probe','probe','<1%%','core','pending',%s,%s)
                           on conflict (case_code) do nothing returning case_code""",
                        (probe_code, MISSION, BATCH))
            got = cur.fetchall()
            cur.execute("select count(*) from tensor_case_pool")
            mid = cur.fetchone()[0]
            raise psycopg.Rollback  # 显式回滚探针
        out["idempotency_probe"] = {"case_code": probe_code, "rows_inserted": len(got),
                                    "pool_total_in_probe_txn": mid, "pool_total_before": before,
                                    "rolled_back": True}

    # DOI 账面核对：无 DOI 的必须是"前 DOI 专著"允许类（见 ref_verify_final.json）
    try:
        vf = json.load(open(os.path.join(os.path.dirname(OUT), "ref_verify_final.json"), encoding="utf-8"))
        no_doi_expected = {c["case_code"] for c in vf["cases"]
                           if c["ref_verdict"] in ("classic_monograph_no_doi",)}
        out["no_doi_expected_class"] = sorted(no_doi_expected)
        out["doi_accounted"] = (out["batch_ref_source_with_doi"] + len(no_doi_expected)) == out["batch_total"]
    except FileNotFoundError:
        out["doi_accounted"] = False

    # 每仓轮转是否均衡（六仓各 7 例）
    per = {d["repo"]: d["rows"] for d in out["batch_by_repo"]}
    out["rotation_balanced"] = (set(per) == set(REPOS)) and len(set(per.values())) == 1
    out["pass"] = all([
        out["batch_total"] == out["pool_total"] - 84 or out["batch_total"] == 42,
        out["batch_total"] == len(codes),
        out["duplicate_case_codes"] == [],
        out["batch_blank_required_fields"] == 0,
        out["batch_rows_with_linked_anchor"] == 0,
        out["batch_tol_policy_quantified"] == out["batch_total"],
        out.get("doi_accounted", False),
        out["batch_codes_also_in_old_rows"] == 0,
        out["rotation_balanced"],
        out["idempotency_probe"]["rows_inserted"] == 0,
        out["idempotency_probe"]["pool_total_in_probe_txn"] == out["idempotency_probe"]["pool_total_before"],
    ])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k not in ("sample_10_full_fields", "batch_codes")},
                     ensure_ascii=False, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())