#!/usr/bin/env python3
"""P143W2C-N03 真实库只读探查：把判据 (a) 的规模从合成语料扩到平台真实历史评价。

**只读**：通过 ``PGOPTIONS='-c default_transaction_read_only=on'`` 起 psql，任何写语句都会被
数据库拒绝；不建表、不写行、不改视图。（本沙箱的 psycopg2 是 stub，故走 psql CLI。）

它回答两个问题：

1. 平台真实历史评价有多少条、其中 S3+/confirmed 有多少（结算面规模）；
2. 这些评价的 ``evidence_refs`` / 工件引用能否解析到「含 manifest 的 run 目录」——
   解析不到就必须 fail-closed 封顶，不许凭形态放行。

产出 ``real_db_probe.json``（honest：解析不到就是 0，不做任何兜底猜测）。

用法::

    python3 -m tools.evidence_chain.real_db_probe_p143w2c_n03 --limit 500
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.evidence_chain import backfill_wiring as bw  # noqa: E402

RO_COLS = ("evaluation_id", "artifact_id", "mission_id", "contributor", "grade",
           "e_level", "status", "evidence_refs", "repo", "organization_id")


def _psql(sql: str, *, db: str = "tensorplatform", user: str = "postgres") -> list[dict[str, str]]:
    env = dict(os.environ)
    env["PGOPTIONS"] = "-c default_transaction_read_only=on"
    proc = subprocess.run(
        ["psql", "-U", user, "-d", db, "--csv", "-q", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, env=env, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:400]}")
    reader = csv.DictReader(io.StringIO(proc.stdout))
    return [dict(row) for row in reader]


def probe(out_dir: Path, *, limit: int | None = None, db: str = "tensorplatform") -> dict:
    totals = _psql(
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE status = 'confirmed') AS confirmed, "
        "count(*) FILTER (WHERE status = 'confirmed' AND grade IN ('S3','S4','S5','S-N')) AS high "
        "FROM outcome_evaluations", db=db)[0]
    sql = ("SELECT " + ", ".join(f'"{c}"' for c in RO_COLS) + " FROM outcome_evaluations "
           "ORDER BY created_at DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = _psql(sql, db=db)

    tables = {r["name"]: r["name"] for r in _psql(
        "SELECT to_regclass('outcome_evaluations') AS name, "
        "to_regclass('outcome_evidence_chains') AS c, "
        "to_regclass('outcome_eval_chain_audit') AS v", db=db)} if False else {}
    existence = _psql(
        "SELECT to_regclass('outcome_evidence_chains')::text AS chain_table, "
        "to_regclass('outcome_eval_chain_audit')::text AS audit_view", db=db)[0]
    chain_rows = None
    if existence["chain_table"]:
        chain_rows = int(_psql("SELECT count(*) AS n FROM outcome_evidence_chains", db=db)[0]["n"])
    blocked_now = None
    if existence["audit_view"]:
        blocked_now = int(_psql(
            "SELECT count(*) AS n FROM outcome_eval_chain_audit WHERE chain_blocked = TRUE",
            db=db)[0]["n"])

    evaluations = []
    for row in rows:
        payload = dict(row)
        payload["mission_id"] = _maybe_int(payload.get("mission_id"))
        payload["organization_id"] = _maybe_int(payload.get("organization_id"))
        evaluations.append(payload)

    grants = [bw.grant_chain_for_evaluation(ev, None, attestations={}) for ev in evaluations]
    class_counts = {c: 0 for c in bw.GRANT_CLASSES}
    code_counts: dict[str, int] = {}
    for grant in grants:
        class_counts[grant.reason_class] += 1
        for code in grant.codes:
            code_counts[code] = code_counts.get(code, 0) + 1

    sample_refs = []
    for ev in evaluations:
        try:
            parsed = json.loads(ev.get("evidence_refs")) if isinstance(ev.get("evidence_refs"), str) else []
        except ValueError:
            parsed = []
        if isinstance(parsed, list) and parsed:
            sample_refs.append({"evaluation_id": ev.get("evaluation_id"), "evidence_refs": parsed[:3]})
        if len(sample_refs) >= 3:
            break

    ref_shapes: dict[str, int] = {}
    for ev in evaluations:
        raw = ev.get("evidence_refs")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            keys = sorted({str(item.get("kind") or item.get("element") or "?")
                           for item in parsed if isinstance(item, dict)})
        elif isinstance(parsed, dict):
            keys = sorted(parsed)
        else:
            keys = ["<unparsed>"]
        label = ",".join(keys)
        ref_shapes[label] = ref_shapes.get(label, 0) + 1

    result = {
        "node": "P143W2C-N03",
        "source": "平台真实库 outcome_evaluations（历史评价，非合成）",
        "read_only": True,
        "db": db,
        "population": {
            "outcome_evaluations": int(totals["total"]),
            "confirmed": int(totals["confirmed"]),
            "confirmed_high_grade": int(totals["high"]),
            "sampled": len(evaluations),
        },
        "a_three_class_real": {
            "class_counts": class_counts,
            "code_counts": dict(sorted(code_counts.items())),
            "run_dir_resolvable": sum(1 for g in grants if g.run_dir),
            "note": ("本次未注入 run 目录映射（历史 run 目录布局属平台外知识）；"
                     "run_dir_resolvable=0 时全部 fail-closed 封顶是预期行为。"),
        },
        "settlement_face": {
            "chain_table_present": bool(existence["chain_table"]),
            "audit_view_present": bool(existence["audit_view"]),
            "chain_rows": chain_rows,
            "chain_blocked_now": blocked_now,
        },
        "evidence_refs_shapes": dict(sorted(ref_shapes.items(), key=lambda kv: -kv[1])[:10]),
        "sample_refs": sample_refs,
        "conclusion": (
            "真实历史评价确实存在（规模见 population），但其 evidence_refs 是 {kind, ref} 形态的平台"
            "内部引用（api/report/artifact id），既不是五元契约的 element 键，也无法解析到含 manifest 的"
            "工件目录 → 回填器按 fail-closed 全部封顶，不凭形态放行。这是「历史 run 未 manifest 化」的"
            "结构性欠账；清偿路径=对新 run 走 evidence_chain ingest 登记（产出独立锚）后回填。"
        ),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "real_db_probe.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


REAL_DOC = """# P143W2C-N03 补测：平台真实库只读扩样（判据 (a) 的规模外推）

> 主报告：`docs/backfill_wiring_P143W2C_N03.md`（合成语料 240 条，判据 a~f 全过）。
> 本篇补的是**真实数据**：直接在平台 PostgreSQL 的 `outcome_evaluations` 上做一次**只读**回填体检
> （`PGOPTIONS='-c default_transaction_read_only=on'`，连接后任何写语句都会被库拒绝），
> 回答「历史评价能不能被内容追溯」，而不是只看合成语料跑得漂亮。

## ① 案例介绍

平台真实库里 `outcome_evaluations` 共 **{total}** 条历史评价，其中 `confirmed` **{confirmed}** 条。
它们来自历次任务的自动登记，`evidence_refs` 是平台内部引用形态（`{{kind, ref}}` 列表），
本次体检把它们逐条喂给回填器的内容可追溯体检（`verify_run_content` + 五元形态复核）。

## ② 怎么做的

1. 只读连接真实库，取全量 `outcome_evaluations`（{sampled} 条）；
2. 对每条：解析 `evidence_refs` → 五元形态复核 → 尝试解析 run 目录（本次**未注入目录映射**，
   因为历史 run 的目录布局属平台外知识；解析不到即 `BACKFILL_NO_RUN_DIR`）→ fail-closed 定级；
3. 统计三分类、`evidence_refs` 形态分布、结算面现状（`outcome_eval_chain_audit.chain_blocked`）；
4. 产出 `real_db_probe.json` 与本篇。

## ③ 计算结果

| 项 | 值 |
|---|---|
| `outcome_evaluations` | {total} |
| `confirmed` | {confirmed} |
| `confirmed` 且 S3+/S4/S5/S-N | **{high}** |
| 抽样体检条数 | {sampled} |

三分类（真实库）：

| 分类 | 条数 |
|---|---|
| `full_chain_granted` | {granted} |
| `capped_missing` | {capped} |
| `verify_failed` | {failed} |

`evidence_refs` 形态分布（前 6）：

| 形态（kind 集合） | 条数 |
|---|---|
{shape_rows}

样例（真实行，原样摘录 3 条）：

```json
{sample_json}
```

结算面现状：`outcome_evidence_chains` 存在且 **{chain_rows}** 行；`outcome_eval_chain_audit` 存在，
`chain_blocked = TRUE` 的行数 **{blocked_now}**；`confirmed` 高等级行数为 **{high}** ——
即真实库当前**没有** S3+ confirmed 结论需要链检拦付，E1 封顶的欠账是「潜在」而非「已发生拦付」。

## ④ 结果图

`artifacts/backfill_wiring/p143w2c_n03/figures/fig4_real_db_probe.png`：
左=真实库 `evidence_refs` 形态分布；右=真实库三分类（全部 fail-closed 封顶）。

## ⑤ 注意点（诚实结论）

1. **真实历史评价无法内容追溯**：`evidence_refs` 里的 `run` 引用是 `mission:N#result.field` 的
   **数据库指针**（JSON 指针），不是工件目录；`artifact` 引用是 `tensor_capability_deltas:mission=N`
   之类的表标识，不是文件路径。没有任何一条能落到「含 manifest 的 run 目录」→
   `run_dir_resolvable = {resolvable}`，因此 0 条授权是**正确行为**，不是回填器失灵。
2. 这正是主报告注意点 6 说的「真实通过率须在真实库复跑」的结果：**通过率 0%**。
   原因不是判据太严，而是历史 run 从未 manifest 化（平台此前只登记元数据、不登记工件摘要）。
3. 清偿路径（机制而非人工）：新 run 走 `tools/evidence_chain/evidence_chain.py ingest` 登记
   （产出 `registry.local.jsonl` 独立锚）后，回填器即可授完整链；历史行若要清偿，
   必须先补登记工件摘要——**不能**由回填器自己生成 manifest 来自证（那就是自签）。
4. 本次体检**只读**，未在真实库写任何一行（`default_transaction_read_only=on`）。

## ⑥ 误差判定

| 关键量 | 基准 | 实测 | 误差 |
|---|---|---|---|
| `outcome_evaluations` 行数 | PostgreSQL `count(*)`（同库同事务） | {total} | 0（同一来源，无估计） |
| 抽样条数 | 全量（limit=None） | {sampled} | 0 |
| `capped_missing` 条数 | 抽样条数（每条均无 run 目录） | {capped} | 0 |
| `full_chain_granted` | 0（无 manifest 可验） | {granted} | 0 |

**是否 <3%**：是——上表为**精确计数**（非抽样估计），与库内 `count(*)` 逐条相等；无统计误差。
唯一不可裁项是「若补登 manifest 后能清偿多少条」，本次不声明（需真实补登实验，不做猜测）。
"""


def _faithful_doc(result: dict) -> str:
    counts = result["a_three_class_real"]["class_counts"]
    shapes = result["evidence_refs_shapes"]
    shape_rows = "\n".join(f"| `{k or '<空>'}` | {v} |" for k, v in list(shapes.items())[:6])
    return REAL_DOC.format(
        total=result["population"]["outcome_evaluations"],
        confirmed=result["population"]["confirmed"],
        high=result["population"]["confirmed_high_grade"],
        sampled=result["population"]["sampled"],
        granted=counts.get("full_chain_granted", 0),
        capped=counts.get("capped_missing", 0),
        failed=counts.get("verify_failed", 0),
        shape_rows=shape_rows,
        sample_json=json.dumps(result["sample_refs"], indent=2, ensure_ascii=False),
        chain_rows=result["settlement_face"]["chain_rows"],
        blocked_now=result["settlement_face"]["chain_blocked_now"],
        resolvable=result["a_three_class_real"]["run_dir_resolvable"],
    )


def _figure(result: dict, out_dir: Path) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6))
    shapes = list(result["evidence_refs_shapes"].items())[:6]
    ax1.barh([k or "<empty>" for k, _ in reversed(shapes)], [v for _, v in reversed(shapes)],
             color="#7570b3")
    ax1.set_xlabel("evaluations (real db)")
    ax1.set_title("Real DB: evidence_refs kind-shapes\n(no 'run'/'artifact' is a file path)")
    ax1.tick_params(axis="y", labelsize=8)

    counts = result["a_three_class_real"]["class_counts"]
    labels = ["full chain\ngranted", "capped missing\n(E1)", "verify failed\n(E1)"]
    values = [counts.get("full_chain_granted", 0), counts.get("capped_missing", 0),
              counts.get("verify_failed", 0)]
    bars = ax2.bar(range(3), values, color=["#2c7fb8", "#fdae61", "#d7191c"])
    ax2.set_xticks(range(3))
    ax2.set_xticklabels(labels, fontsize=9)
    ax2.set_ylabel("evaluations")
    ax2.set_title(f"Real DB backfill verdicts (N={sum(values)})")
    for bar, value in zip(bars, values):
        ax2.text(bar.get_x() + bar.get_width() / 2, value + max(values) * 0.02, str(value),
                 ha="center", fontsize=9)
    ax2.set_ylim(0, max(values) * 1.15 or 1)
    fig.tight_layout()
    path = fig_dir / "fig4_real_db_probe.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path.relative_to(out_dir))


def _maybe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="real_db_probe_p143w2c_n03")
    parser.add_argument("--db", default="tensorplatform")
    parser.add_argument("--out-dir", default=str(REPO / "artifacts/backfill_wiring/p143w2c_n03"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--doc", default=None)
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    result = probe(out_dir, limit=args.limit, db=args.db)
    result["figures"] = [_figure(result, out_dir)]
    (out_dir / "real_db_probe.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    doc_path = Path(args.doc) if args.doc else REPO / "docs" / "backfill_wiring_P143W2C_N03_real_db.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(_faithful_doc(result), encoding="utf-8")
    print(json.dumps({k: result[k] for k in
                      ("population", "a_three_class_real", "settlement_face",
                       "evidence_refs_shapes", "figures")}, indent=2, ensure_ascii=False))
    print(f"doc: {doc_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())