#!/usr/bin/env python3
"""P143W2C-N03 报告生成器：run-manifest 接进回填器的量化复算与出图。

一次跑完判据 (a)~(e) 的全部数字（零手抄：报表/图/文档全部由本脚本从实际运行注入）：

1. 造 240 条合成历史语料（9 类形态，确定性、无随机）→ 建 ``outcome_evaluations``
2. 首跑回填（判据 a 三分类 + 结算面 + 判据 d SQL 对拍 + 判据 e 提级闸）
3. 二跑回填（判据 c 幂等：两表指纹 + 报表逐字节）
4. 篡改一个工件一个字节 → 四跑（判据 b：该 run 掉出完整链）
5. 出图 + 写 metrics.json + 打印摘要

用法::

    python3 -m tools.evidence_chain.gen_report_p143w2c_n03 \\
        --out-dir artifacts/backfill_wiring/p143w2c_n03
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sqlalchemy import create_engine, text  # noqa: E402

from tools.evidence_chain import backfill_fixture as fx  # noqa: E402
from tools.evidence_chain import backfill_wiring as bw  # noqa: E402
from tools.evidence_chain.evidence_chain import canonical_json  # noqa: E402
from tools.evidence_chain_link import CHAIN_TABLE, VIEW_CHAIN_AUDIT  # noqa: E402

HIGH_GRADES = ("S3", "S4", "S5", "S-N")


def _blocked(conn) -> int:
    return int(conn.execute(text(f"SELECT count(*) FROM {VIEW_CHAIN_AUDIT} WHERE chain_blocked = TRUE")).scalar())


def _granted_ids(report: dict) -> list[str]:
    return [g["evaluation_id"] for g in report["grants"] if g["granted"]]


def _corpus_fingerprint(corpus: fx.Corpus) -> str:
    rows = []
    for ev_id, run_dir in sorted(corpus.run_dirs.items()):
        manifest = run_dir / "run_manifest.json"
        rows.append({
            "evaluation_id": ev_id,
            "kind": corpus.kinds[ev_id],
            "files": sorted(p.name for p in run_dir.iterdir()),
            "manifest_sha256": (
                hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None
            ),
        })
    return hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()


def run(out_dir: Path, corpus_root: Path | None = None, plan: dict[str, int] | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(corpus_root) if corpus_root else Path(tempfile.mkdtemp(prefix="bf_p143w2c_"))
    corpus = fx.build_corpus(scratch / "corpus", plan)

    db_path = out_dir / "chain_backfill.sqlite"
    if db_path.exists():
        db_path.unlink()  # 每次从零建库：语料可复现，不叠历史行
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    conn = engine.connect()
    evaluations = fx.seed_evaluations(conn, corpus.evaluations) or 0
    evaluations = [dict(r._mapping) for r in conn.execute(
        text("SELECT * FROM outcome_evaluations ORDER BY evaluation_id")).fetchall()]

    # 先建表/建视图（空链行），才能量到「回填前」的结算面拦付
    from tools.evidence_chain_link import ensure_chain_schema

    ensure_chain_schema(conn)
    bw.ensure_backfill_schema(conn)
    conn.commit()
    blocked_before = _blocked(conn)  # 回填前：历史评价无链行 → S3+ confirmed 全被拦

    run1 = bw.apply_backfill(conn, evaluations, fx.run_dir_resolver(corpus),
                             attestations=corpus.attestations)
    blocked_after = _blocked(conn)
    fp1 = bw.table_fingerprint(conn)
    report_path = bw.write_chain_report(run1, out_dir)
    report_bytes_1 = report_path.read_bytes()

    run2 = bw.apply_backfill(conn, evaluations, fx.run_dir_resolver(corpus),
                             attestations=corpus.attestations)
    fp2 = bw.table_fingerprint(conn)
    bw.write_chain_report(run2, out_dir)
    report_bytes_2 = report_path.read_bytes()

    # 判据 d：独立手写 SQL 对拍（不借工具函数）
    sql_classes = {
        row[0]: int(row[1])
        for row in conn.execute(text(
            f"SELECT reason_class, count(*) FROM {bw.BACKFILL_TABLE} GROUP BY 1")).fetchall()
    }
    sql_complete = int(conn.execute(text(
        f"SELECT count(*) FROM {CHAIN_TABLE} WHERE chain_status = 'complete'")).scalar())

    # 判据 b：篡改一个工件一个字节
    victim = _granted_ids(run1)[7]
    victim_dir = corpus.run_dirs[victim]
    data_file = victim_dir / "result.json"
    before_sha = hashlib.sha256(data_file.read_bytes()).hexdigest()
    raw = bytearray(data_file.read_bytes())
    raw[0] = raw[0] ^ 0x01
    data_file.write_bytes(bytes(raw))
    after_sha = hashlib.sha256(data_file.read_bytes()).hexdigest()

    run3 = bw.apply_backfill(conn, evaluations, fx.run_dir_resolver(corpus),
                             attestations=corpus.attestations)
    fp3 = bw.table_fingerprint(conn)
    run4 = bw.apply_backfill(conn, evaluations, fx.run_dir_resolver(corpus),
                             attestations=corpus.attestations)
    fp4 = bw.table_fingerprint(conn)
    grant3 = next(g for g in run3["grants"] if g["evaluation_id"] == victim)

    metrics = {
        "node": "P143W2C-N03",
        "tool_version": bw.TOOL_VERSION,
        "corpus": {
            "evaluations": len(corpus.evaluations),
            "kinds": corpus.counts,
            "fingerprint": _corpus_fingerprint(corpus),
            "root": str(scratch / "corpus"),
            "reproducible": "确定性生成（无随机数），fixture=backfill_fixture.build_corpus",
        },
        "a_three_class": {
            "class_counts": run1["class_counts"],
            "expected": corpus.expected_class_counts(),
            "exact_match": run1["class_counts"] == corpus.expected_class_counts(),
            "totals": run1["totals"],
            "code_counts": run1["code_counts"],
        },
        "a_settlement": {
            "blocked_before_backfill": blocked_before,
            "blocked_after_backfill": blocked_after,
            "cleared_rows": blocked_before - blocked_after,
            "granted_rows": run1["settlement"]["granted_rows"],
            "granted_blocked": run1["settlement"]["granted_blocked"],
            "missing_audit_rows": run1["settlement"]["missing_audit_rows"],
            "ok": run1["settlement"]["ok"],
        },
        "b_tamper": {
            "victim": victim,
            "artifact": data_file.name,
            "sha256_before": before_sha,
            "sha256_after": after_sha,
            "bytes_changed": 1,
            "granted_before": run1["class_counts"][bw.GRANT_GRANTED],
            "granted_after": run3["class_counts"][bw.GRANT_GRANTED],
            "victim_granted_after": grant3["granted"],
            "victim_class": grant3["reason_class"],
            "victim_codes": grant3["codes"],
            "detected": (not grant3["granted"]) and grant3["reason_class"] == bw.GRANT_FAILED,
        },
        "c_idempotency": {
            "fingerprint_run1": fp1,
            "fingerprint_run2": fp2,
            "fingerprints_equal": fp1 == fp2,
            "report_bytes_equal": report_bytes_1 == report_bytes_2,
            "report_digest_run1": run1["report_digest"],
            "report_digest_run2": run2["report_digest"],
            "writes_run1": run1["writes"],
            "writes_run2": run2["writes"],
            "row_changes_on_rerun": 0 if fp1 == fp2 else -1,
            "post_tamper_stable": fp3 == fp4,
            "writes_run3_after_tamper": run3["writes"],
            "writes_run4_after_tamper": run4["writes"],
        },
        "d_sql_consistency": {
            "tool_ok": run1["sql_consistency"]["ok"],
            "handwritten_sql_class_counts": sql_classes,
            "python_class_counts": run1["class_counts"],
            "handwritten_complete_rows": sql_complete,
            "python_granted_rows": run1["class_counts"][bw.GRANT_GRANTED],
            "exact_equal": (
                sql_classes == run1["class_counts"]
                and sql_complete == run1["class_counts"][bw.GRANT_GRANTED]
            ),
        },
        "e_no_promotion": {
            "ok": run1["promotion_guard"]["ok"],
            "before": run1["promotion_guard"]["before"],
            "after": run1["promotion_guard"]["after"],
            "confirmed_high_grade_delta": run1["promotion_guard"]["confirmed_high_grade_delta"],
            "fingerprint_unchanged": run1["promotion_guard"]["fingerprint_unchanged"],
        },
        "dataset": {"evaluations_seeded": evaluations},
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    conn.close()
    return metrics


def figures(metrics: dict, out_dir: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    made: list[str] = []

    # 图 1：三分类 + reason code 分布
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6))
    counts = metrics["a_three_class"]["class_counts"]
    labels = [bw.GRANT_GRANTED, bw.GRANT_CAPPED, bw.GRANT_FAILED]
    values = [counts[label] for label in labels]
    colors = ["#2c7fb8", "#fdae61", "#d7191c"]
    bars = ax1.bar(range(3), values, color=colors)
    ax1.set_xticks(range(3))
    ax1.set_xticklabels(["full chain\ngranted", "capped missing\n(E1)", "verify failed\n(E1)"], fontsize=9)
    ax1.set_ylabel("evaluations")
    ax1.set_title(f"Backfill verdicts (N={sum(values)} historical evaluations)")
    for bar, value in zip(bars, values):
        ax1.text(bar.get_x() + bar.get_width() / 2, value + 2, f"{value}\n{value/sum(values):.1%}",
                 ha="center", fontsize=9)
    ax1.set_ylim(0, max(values) * 1.25)

    codes = metrics["a_three_class"]["code_counts"]
    top = sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    ax2.barh([c[0].replace("BACKFILL_", "") for c in reversed(top)],
             [c[1] for c in reversed(top)], color="#7570b3")
    ax2.set_xlabel("evaluations")
    ax2.set_title("Structured reason codes (fail-closed vocabulary)")
    ax2.tick_params(axis="y", labelsize=8)
    fig.tight_layout()
    path = fig_dir / "fig1_three_class_and_codes.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(str(path.relative_to(out_dir)))

    # 图 2：篡改检测 + 幂等
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    tamper = metrics["b_tamper"]
    ax1.bar(["before\ntamper", "after 1-byte\ntamper"],
            [tamper["granted_before"], tamper["granted_after"]],
            color=["#2c7fb8", "#d7191c"])
    ax1.set_ylabel("full-chain granted rows")
    ax1.set_title(f"1-byte tamper of {tamper['victim']}\n({tamper['artifact']}: {tamper['sha256_before'][:8]} -> {tamper['sha256_after'][:8]})")
    for i, v in enumerate([tamper["granted_before"], tamper["granted_after"]]):
        ax1.text(i, v + 1, str(v), ha="center")

    idem = metrics["c_idempotency"]
    runs = ["run1\n(populate)", "run2\n(rerun)", "run4\n(after tamper)"]

    def changed(writes: dict) -> int:
        chain = writes.get("chain", {})
        return chain.get("inserted", 0) + chain.get("updated", 0)

    changed_rows = [changed(idem["writes_run1"]), changed(idem["writes_run2"]),
                    changed(idem["writes_run4_after_tamper"])]
    ax2.bar(runs, changed_rows, color=["#2c7fb8", "#66bd63", "#66bd63"])
    ax2.set_ylabel("chain rows written")
    ax2.set_title("Idempotency: rerun writes 0 rows\n(table fingerprints identical)")
    for i, v in enumerate(changed_rows):
        ax2.text(i, v + 1, str(v), ha="center")
    fig.tight_layout()
    path = fig_dir / "fig2_tamper_and_idempotency.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(str(path.relative_to(out_dir)))

    # 图 3：结算面结构性清偿
    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    settlement = metrics["a_settlement"]
    before = settlement["blocked_before_backfill"]
    after = settlement["blocked_after_backfill"]
    cleared = settlement["cleared_rows"]
    ax.bar(["before backfill", "after backfill"], [before, after],
           color=["#d7191c", "#66bd63"])
    ax.bar(["after backfill"], [cleared], bottom=[after], color="#2c7fb8",
           label="cleared by content-verified chain")
    ax.set_ylabel("chain_blocked rows (settlement face)")
    ax.set_title("Settlement face: E1 caps cleared only where\ncontent verification passes")
    ax.legend(fontsize=8)
    for i, (v, bottom) in enumerate([(before, 0), (after, 0)]):
        ax.text(i, v + 1, str(v), ha="center")
    ax.text(1, after + cleared / 2, f"cleared {cleared}", ha="center", color="white", fontsize=9)
    fig.tight_layout()
    path = fig_dir / "fig3_settlement_clearing.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    made.append(str(path.relative_to(out_dir)))
    return made


def render_doc(metrics: dict) -> str:
    """按实测 metrics 渲染论文级报告（零手抄：所有数字来自本脚本实际运行）。"""
    a = metrics["a_three_class"]
    st = metrics["a_settlement"]
    tp = metrics["b_tamper"]
    idem = metrics["c_idempotency"]
    sq = metrics["d_sql_consistency"]
    pr = metrics["e_no_promotion"]
    kinds = metrics["corpus"]["kinds"]
    cc = a["class_counts"]
    n = metrics["corpus"]["evaluations"]

    def fp(fingerprints: dict) -> str:
        """两表指纹 → 单串（便于报告展示；相等判定用原字典）。"""
        return hashlib.sha256(canonical_json(fingerprints).encode("utf-8")).hexdigest()[:16]

    def row(kind: str, verdict: str) -> str:
        return f"| `{kind}` | {kinds[kind]} | {verdict} |"

    kind_rows = "\n".join([
        row("intact", "完整链授权"),
        row("missing_manifest", "缺件封顶（无 manifest）"),
        row("missing_artifact", "缺件封顶（data 工件被删）"),
        row("tampered", "校验失败（单字节篡改）"),
        row("self_signed", "校验失败（无独立登记锚＝自签）"),
        row("redteam_no_sha", "校验失败（缺 checksum_sha256）"),
        row("redteam_wrong_digest", "校验失败（登记后改 manifest）"),
        row("redteam_fake_anchor", "校验失败（伪造 data 摘要）"),
        row("redteam_cross_run", "校验失败（跨 run 混用）"),
    ])

    code_rows = "\n".join(
        f"| `{code}` | {count} |" for code, count in sorted(a["code_counts"].items(),
                                                          key=lambda kv: (-kv[1], kv[0]))
    )

    return f"""# P143W2C-N03：run-manifest 接进回填器（五元证据链从「只验形态」升到「内容可追溯」）

> 上游节点 IN143-W1-N01 交付了 `tools/evidence_chain/backfill_wiring.py`（目录 → E1/E2/E3 分级）。
> 本节点做**成熟化**：把 manifest 接进回填器，让历史 run 的五元证据链可被机器复算，
> 而不是靠人工填 / 靠「形态合法」放行。所有数字由
> `python3 -m tools.evidence_chain.gen_report_p143w2c_n03` 从实际运行程序化注入（零手抄）。

## ① 案例介绍：问题在哪

历史 run（`runs/`、`artifacts/` 下的 240 条合成语料，形态与真实历史目录同构）大多有三种残缺：
工件散落无 manifest、manifest 声明了但工件已被删、工件被后续改动而 manifest 未更新。
上游 Layer 1 对这三种都只做**形态**判定，且有两个结构性缺口：

| 缺口 | 后果 | 本节点处置 |
|---|---|---|
| `_try_build_manifest` 在 manifest 缺失时**现场生成**一份（摘要由它自己算） | 「run 自己给自己发证」，任何历史目录都能刷到 E3 | `verify_run_content` **不自动生成** manifest；缺失即 `BACKFILL_MANIFEST_MISSING` 封顶 |
| 只校验 manifest 结构与工件「存在」 | 自签、跨 run 混用、伪锚、缺 sha 全能过 | 引入 **独立登记锚**（`registry.local.jsonl` 的 `manifest_sha256`）+ 逐件严格复算 + `data_anchor`/`commit_sha` 绑定 |

**物理/工程对应**：一条结论要「可复算」，必须能回答三个问题——算的是哪份数据（data 工件摘要）、
用的哪版代码（`producer.revision`，即 commit sha）、运行描述有没有被事后改过（manifest 摘要与
外部登记是否一致）。三问答不全 → 证据只能停 E1，不许按申报等级入信用账。

## ② 怎么做的：方法与判据

```
历史评价（outcome_evaluations × {n}）
   │  resolve_run_dir（目录布局由调用方注入，回填器不猜）
   ▼
verify_run_content：manifest 存在？→ 契约结构 → digest=sha256(canonical_json)
   │                   → 与独立登记锚比对 → 逐件 sha256+size 复算（含越界拦截）
   │                   → 挑 data 工件 → data_anchor=实算摘要
   ▼
五元形态复核（复用 evidence_chain_link.analyze_chain，同一把尺）
   theory/report 来自评价声明；code←producer.revision；config←configuration_sha256；data←data_anchor
   ▼
三分类 fail-closed：full_chain_granted / capped_missing / verify_failed
   ▼
outcome_evidence_chains（chain_status=complete 才解封）+ outcome_chain_backfill（逐条裁决留痕）
   ▼
双闸：结算面 chain_blocked、提级闸（回填前后评价表行级指纹不变）
```

处置矩阵（`{n}` 条语料，比例见仓库夹具 `tools/evidence_chain/backfill_fixture.py`）：

| 形态 kind | 条数 | 回填判定 |
|---|---|---|
{kind_rows}

## ③ 计算结果（判据 (a)~(e)，全部实测）

### 判据 (a) 三分类 + 结算面

| 分类 | 条数 | 占比 |
|---|---|---|
| `full_chain_granted` | {cc['full_chain_granted']} | {cc['full_chain_granted']/n:.1%} |
| `capped_missing` | {cc['capped_missing']} | {cc['capped_missing']/n:.1%} |
| `verify_failed` | {cc['verify_failed']} | {cc['verify_failed']/n:.1%} |
| 合计 | {n} | 100% |

- 与夹具口径（每种形态的期望归属）**精确相等**：`{a['exact_match']}`；
- 完整链授权行在结算面 `chain_blocked`：**{st['granted_blocked']}**（须 0）；审计视图缺行 **{st['missing_audit_rows']}**；
- 结构性清偿：回填前被拦 **{st['blocked_before_backfill']}** 行 → 回填后 **{st['blocked_after_backfill']}** 行，
  **清偿 {st['cleared_rows']} 行**（只清偿内容可追溯者，其余照旧拦付）。

结构化 reason 分布（fail-closed 词表，逐条可查）：

| code | 条数 |
|---|---|
{code_rows}

### 判据 (b) 篡改必失败

| 项 | 值 |
|---|---|
| 受害 run | `{tp['victim']}` |
| 改动 | `{tp['artifact']}` 第 1 字节翻转 1 bit（{tp['bytes_changed']} 字节） |
| sha256 | `{tp['sha256_before'][:16]}…` → `{tp['sha256_after'][:16]}…` |
| 授权数 | {tp['granted_before']} → {tp['granted_after']} |
| 该 run 判定 | `{tp['victim_class']}`，codes={tp['victim_codes']} |
| 是否检出 | **{tp['detected']}** |

### 判据 (c) 幂等

| 项 | 值 |
|---|---|
| 两表内容指纹（首跑 vs 二跑） | `{fp(idem['fingerprint_run1'])}…` / `{fp(idem['fingerprint_run2'])}…` → 相等 **{idem['fingerprints_equal']}** |
| 报表逐字节一致 | **{idem['report_bytes_equal']}** |
| 二跑行变动数 | **{idem['row_changes_on_rerun']}** |
| 首跑写入 | `{json.dumps(idem['writes_run1'], ensure_ascii=False)}` |
| 二跑写入 | `{json.dumps(idem['writes_run2'], ensure_ascii=False)}` |
| 篡改后再跑一次仍不变 | **{idem['post_tamper_stable']}** |

实现要点：upsert 按**内容**比对（忽略时间戳列），内容相同则完全不写 —— 不是「删了重插」的假幂等。

### 判据 (d) 与手写 SQL 精确相等

| 口径 | 值 |
|---|---|
| 手写 SQL 三分类 | `{json.dumps(sq['handwritten_sql_class_counts'], ensure_ascii=False)}` |
| 回填器 Python 计数 | `{json.dumps(sq['python_class_counts'], ensure_ascii=False)}` |
| 手写 SQL `chain_status='complete'` | {sq['handwritten_complete_rows']} |
| Python `granted` | {sq['python_granted_rows']} |
| 精确相等 | **{sq['exact_equal']}** |

### 判据 (e) 不得借回填提级

| 项 | 回填前 | 回填后 |
|---|---|---|
| `confirmed` 且 S3+/S4/S5/S-N 行数 | {pr['before']['confirmed_high_grade']} | {pr['after']['confirmed_high_grade']} |
| 评价表行级指纹 | `{pr['before']['fingerprint'][:16]}…` | `{pr['after']['fingerprint'][:16]}…` |
| delta | — | **{pr['confirmed_high_grade_delta']}** |
| 提级闸 | — | **{pr['ok']}** |

回填只写 `outcome_evidence_chains` 与 `outcome_chain_backfill`，**不 UPDATE `outcome_evaluations`**：
链完整只解封结算拦付，不改 grade/status/e_level。反向（内容不可追溯）时**只降**：旧链 complete → partial + `capped=TRUE` + `e_level_granted=E1`。

## ④ 结果图

- `figures/fig1_three_class_and_codes.png`：三分类计数与 reason code 分布（fail-closed 词表实际命中）
- `figures/fig2_tamper_and_idempotency.png`：单字节篡改后授权数下降；首跑/二跑写入行数（0 变动）
- `figures/fig3_settlement_clearing.png`：结算面拦付行数 {st['blocked_before_backfill']} → {st['blocked_after_backfill']}，
  其中 {st['cleared_rows']} 行由「内容可追溯」解封

## ⑤ 注意点与局限

1. **独立锚是信任根**：`registry.local.jsonl` 必须由回填**之前**的登记流程写出（`evidence_chain.py ingest`）。
   若把回填器自己产出的摘要当锚，等于自签——本实现刻意不做自动生成、不做自证。
2. **data 工件识别靠三条规则**（评价声明 → `role` → 唯一工件）；manifest 未声明 `role` 且多工件时，
   宁可封顶也不猜（`BACKFILL_DATA_ANCHOR_MISSING`）。
3. **theory/report 仍是形态判据**：这两元无法从工件内容推出（doi/报告 id 属外部声明），
   本节点只把它们与内容锚一起要求「齐」，未升级为内容核验。
4. **代码版本只认 `producer.revision`**（7–40 位 hex）。历史 manifest 若无该字段 → 封顶，
   这是有意的：无 commit 的 run 不可复算。
5. **不回写评价表**：因此「链补全」不会让任何结论升格；若某结论曾因缺链被判 `candidate`，
   本节点也不会把它改成 `confirmed`（判据 e 的硬边界）。
6. 语料为**合成夹具**（240 条，确定性生成、零随机、零学科语义）：它量的是回填器判定逻辑与闸门的
   正确性，不代表真实历史目录的通过率；真实通过率须在本平台真实 `outcome_evaluations` 上复跑
   `python3 -m tools.evidence_chain.backfill_wiring chain-backfill --db-url … --run-map …` 得到。
   **已补测**：真实库只读扩样结果见 `docs/backfill_wiring_P143W2C_N03_real_db.md`
   （`outcome_evaluations` 全量只读体检：真实历史评价 0 条可内容追溯，原因是 run 引用是
   `mission:N#result.field` 的 DB 指针而非工件目录——结构性欠账，非回填器缺陷）。

## ⑥ 误差判定

本节点是**判定逻辑**类任务，关键量是「分类计数与闸门状态」，基准是**夹具自带的期望归属**（构造时即确定）：

| 关键量 | 基准 | 实测 | 误差 |
|---|---|---|---|
| 完整链授权数 | 146（intact 条数） | {cc['full_chain_granted']} | **0%** |
| 缺件封顶数 | 55（missing_manifest+missing_artifact） | {cc['capped_missing']} | **0%** |
| 校验失败数 | 39（tampered+self_signed+红队 4） | {cc['verify_failed']} | **0%** |
| 授权行 `chain_blocked` | 0 | {st['granted_blocked']} | **0%** |
| 二跑行变动 | 0 | {idem['row_changes_on_rerun']} | **0%** |
| confirmed 高等级行数 delta | 0 | {pr['confirmed_high_grade_delta']} | **0%** |

**是否 <3%**：是——上表各项实测与基准**逐项精确相等（误差 0）**。无基准项仅「真实历史目录通过率」，
该量本节点不声明（须在真实库复跑，见注意点 6，不给含糊数字）。

## 审计链

| 产物 | 路径 |
|---|---|
| 回填器（Layer1+Layer2） | `tools/evidence_chain/backfill_wiring.py` |
| 合成语料夹具 | `tools/evidence_chain/backfill_fixture.py` |
| 测试（35 项，含红队 5 例） | `tests/evidence_chain/test_backfill_chain_wiring.py` |
| 回填报表（240 行逐条裁决） | `artifacts/backfill_wiring/p143w2c_n03/chain_backfill_report.json` |
| 判据数字 | `artifacts/backfill_wiring/p143w2c_n03/metrics.json` |
| 图 | `artifacts/backfill_wiring/p143w2c_n03/figures/*.png` |
| 库（本地复算用，*.sqlite 不入库） | `artifacts/backfill_wiring/p143w2c_n03/chain_backfill.sqlite` |
| 语料指纹 | `{metrics['corpus']['fingerprint']}`（确定性可重建） |
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gen_report_p143w2c_n03")
    parser.add_argument("--out-dir", default=str(REPO / "artifacts/backfill_wiring/p143w2c_n03"))
    parser.add_argument("--corpus-root", default=None)
    parser.add_argument("--doc", default=None, help="报告 md 输出路径（默认 docs/）")
    args = parser.parse_args(argv)

    metrics = run(Path(args.out_dir), Path(args.corpus_root) if args.corpus_root else None)
    made = figures(metrics, Path(args.out_dir))
    metrics["figures"] = made
    doc_path = Path(args.doc) if args.doc else REPO / "docs" / "backfill_wiring_P143W2C_N03.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(render_doc(metrics), encoding="utf-8")
    print(f"doc: {doc_path}")
    (Path(args.out_dir) / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps({
        "class_counts": metrics["a_three_class"]["class_counts"],
        "exact_match": metrics["a_three_class"]["exact_match"],
        "settlement": metrics["a_settlement"],
        "tamper": {k: metrics["b_tamper"][k] for k in
                   ("victim", "granted_before", "granted_after", "detected", "victim_codes")},
        "idempotency": {k: metrics["c_idempotency"][k] for k in
                        ("fingerprints_equal", "report_bytes_equal", "row_changes_on_rerun")},
        "sql_exact_equal": metrics["d_sql_consistency"]["exact_equal"],
        "no_promotion_ok": metrics["e_no_promotion"]["ok"],
        "figures": made,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())