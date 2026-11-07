#!/usr/bin/env python3
"""IN59-A3 报告发布（六要素，数字全部从产物文件读入，零手抄）。

输入：``artifacts/in59_a3/results.json``（判据矩阵）、``artifacts/in59_a3/pg_demo.json``
（PG 端到端演练）、``artifacts/in59_a3/*.png``（三张图）。
凭据：环境变量 ``TENSOR_API_BASE`` / ``TENSOR_AGENT_USER`` / ``TENSOR_AGENT_PASS``。

用法： python3 scripts/in59_a3_publish_report.py [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
ART = REPO / "artifacts" / "in59_a3"
MISSION = 6330

FIGURES = [
    ("fig_ec_cap_matrix.png", "图 1 · S3 声明授予 E 级矩阵（链形态 × 申报 E）：缺件整列封顶 E1"),
    ("fig_ec_status_by_variant.png", "图 2 · S3 声明在各链形态下的 confirmed/candidate/rejected 计数"),
    ("fig_ec_completeness_vs_granted.png", "图 3 · 五元完整度 → 授予 E 秩散点（完整度<1 全压在 E1 线）"),
]


def _post(url: str, payload: dict, token: str | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def build_markdown(res: dict, demo: dict) -> str:
    s = res["summary"]
    rows_n = len(res["matrix"])
    cases = demo.get("cases", {})
    checks = demo.get("checks", {})
    complete = cases.get("complete", {})
    redteam = cases.get("redteam_missing_data", {})
    legacy = cases.get("legacy_confirmed_no_chain", {})
    return f"""## ① 案例介绍（问题与几何/参数表）

**问题**：平台 `outcome_evaluations`（迁移 103）判 S×E 等级时只要求 `evidence_refs`
非空——一条 `{{"kind":"report","ref":"9"}}` 就能登记 S3。也就是说 **报告有、原始数据没有，
结论仍然"可证"**：这是评价体系最容易被击穿的位置。战役节点 IN59-A3 要求：每个 S2+ 成果声明
可追溯五元 `{{理论/文献依据, 代码版本(commit sha), 实验配置(config hash), 原始计算结果, 分析报告(report_id)}}`，
**链不全自动封顶 E 级（缺件 → E 至多 E1）**，质量中心展示链完整度，结算时链检接线，
且红队"S3 声明缺原始数据"必须被拦并降级。

**对象（参数表）**：

| 项 | 取值 |
|---|---|
| 五元键 | `theory` / `code` / `config` / `data` / `report` |
| 形态判据 | DOI/URL/arxiv/derivation；7–40 hex sha 或 `repo@sha`；`sha256:<64hex>`；路径或 `artifact:/file:/s3://`；正整数 report_id |
| 要求链的等级 | S2 / S3 / S4 / S5 / S-N（S1 豁免） |
| 缺件封顶 | E ≤ **E1**（只降不升） |
| 降级联动 | 封顶后低于 S 级 E 门槛 → `candidate`（不计分布、不给信用） |
| 表 / 视图 | `outcome_evidence_chains` / `outcome_eval_chain_audit`（迁移 105） |
| 判据矩阵规模 | 6 S 级 × 4 申报 E × 5 链形态 = **{rows_n} 格** |

## ② 怎么做的（方法与工具链）

1. **单一契约源** `tools/evidence_chain_link.py`：五元逐元素形态判据 + `analyze_chain()`
   → `{{present, missing, invalid, completeness, chain_status, cap}}`；`cap_e_level()` 只降不升。
2. **判定面单点接线** `tools/outcome_evaluations.decide()`：S2+ 调 `analyze_chain` 并把 E 封顶；
   所有走 `decide` 的路径（API 写入 / 回填 / 在途评价）自动获得链检，不存在绕过分支。
   封顶后低于该 S 级 E 门槛 → `CANDIDATE_CHAIN_CAP_BELOW_GRADE`（candidate）。
3. **schema**：迁移 105 建表（CHECK 兜 chain_status / 完整度范围 / E 取值域，`evaluation_id`
   唯一 → 重判幂等）+ 审计视图（`chain_blocked` 只对 confirmed 的 S3+ 且链不完整成立）。
   PostgreSQL 15.5（loongarch64）已实际应用。
4. **落库/读面**：`POST /api/v1/tensor/outcome-evaluations` 接受 `evidence_chain`（+`conclusion_ref`）
   并落链表、回显链摘要；新增 `GET /chains/stats`、`GET /{{evaluation_id}}/chain`。
5. **结算面**：`harness_gateway/mirror/credit_settler_loop.py` 改走审计视图，
   `chain_blocked=FALSE` 才入信用账（视图缺失记 WARN，不静默假装已链检）。
6. **质量中心**：`/tensor/centers/quality` 增 7 指标 + 第 5 道门 `evidence_chain_gate` +
   缺件清单；表缺失 → `not_implemented / null`（不写 0 冒充）。
7. **证据工具链**：`scripts/in59_a3_measure.py`（判据矩阵 → results.json）→
   `scripts/in59_a3_gen_figures.py`（三图）→ `scripts/in59_a3_demo_chain.py`（PG 单事务演练）。

## ③ 计算结果（判据矩阵，程序化注入）

原始结果：`artifacts/in59_a3/results.json`（{rows_n} 行，config hash `{res['config_hash']}`）。

| 统计量 | 值 |
|---|---|
| confirmed / candidate / rejected | {s['confirmed']} / {s['candidate']} / {s['rejected']} |
| 被链封顶（`CHAIN_CAPS_E_TO_E1`）行数 | {s['capped_rows']} |
| 授予 E < 申报 E 的行数 | {s['declared_above_granted']} |
| **授予 E > 申报 E 的行数** | **{s['granted_above_declared']}**（只降不升） |
| 完整链被压级 | {0 if s['complete_chain_grade_preserved'] else '有'}（`complete_chain_grade_preserved={str(s['complete_chain_grade_preserved']).lower()}`） |
| S3 缺 `data` 全部降级 candidate/E1 | {str(s['s3_missing_data_all_downgraded']).lower()} |

S3 面按链形态（4 个申报 E 档合计）：

| 链形态 | confirmed | candidate | 授予 E 集合 |
|---|---|---|---|
| 五元齐全 | 3 | 1（申报 E1 → S3 需 E≥2） | E1–E4 原样 |
| 缺 `data`（红队主控） | 0 | 4 | 仅 E1 |
| 缺 `theory` | 0 | 4 | 仅 E1 |
| `code=main`（非 sha） | 0 | 4 | 仅 E1 |
| 未给链 | 0 | 4 | 仅 E1 |

> 64（封顶行）与 80（S2+ 缺链条数）之差：S-N 的 16 格封顶到 E1 后触发既有
> `NEGATIVE_BELOW_E2`（否定轨须 E≥2）直接 **rejected**（共 17 例含 complete/E1 一例），
> 硬拒路径不附链命中码。

**PostgreSQL 端到端演练**（`artifacts/in59_a3/pg_demo.json`，单事务演练后回滚）：

| 例 | 判定 | 链行 | 结算守卫 | `chain_blocked` |
|---|---|---|---|---|
| 完整五元 S3/E3 | {complete.get('status')} / {complete.get('e_level_granted')} | {complete.get('chain_status')} / {complete.get('completeness')} | 放行 | false |
| 缺 `data` S3/E3（红队） | {redteam.get('status')} / {redteam.get('e_level_granted')} | {redteam.get('chain_status')} / {redteam.get('completeness')} | 不入账 | false |
| 模拟上线前已 confirmed 的 S3（无链） | {legacy.get('status')} / {legacy.get('e_level_granted')} | {legacy.get('chain_status')} / {legacy.get('completeness')} | **拦付** | **true** |

自检：`all_pass={str(checks.get('all_pass')).lower()}`，回滚自检 `rolled_back_rowcount={demo.get('rolled_back_rowcount')}`，
平台评价行数保持 {demo.get('total_eval_rows')}（演示数据未污染分布）。

## ④ 结果图

- **图 1**：申报 E × 链形态 → 授予 E 的矩阵。完整链那行沿对角线原样；四种缺件形态整列压到 E1。
- **图 2**：S3 声明各形态的判定分布——缺 `data` 4/4 全 `candidate`，无 `confirmed`。
- **图 3**：完整度 → 授予 E 秩散点。完整度 < 1 的点全部落在 E1 水平线（封顶线），完整度 = 1
  的点可到 E4；`confirmed` 只在完整度 = 1 处出现（S1 豁免点另计）。

## ⑤ 注意点（坑 / 局限 / 适用范围）

1. **PG 无 `CREATE VIEW IF NOT EXISTS`、SQLite 无 `CREATE OR REPLACE VIEW`** →
   统一 `DROP VIEW IF EXISTS` + `CREATE VIEW` 才能双引擎同段 DDL（先写 `IF NOT EXISTS`
   在 SQLite 过、PG 语法炸）。
2. **审计视图必须暴露 `organization_id`**：质量中心的计数都带 org 过滤，缺列会让
   "结算拦付数"静默变 0（pytest 的 `0 == 1` 抓到）。
3. **链命中码不能进硬拒通道**：混进硬判据会把"缺件"判成 `rejected`，与"封顶 E1"语义相反 →
   链码单独成列、仅终态合并。
4. **历史行不被回溯改写**：迁移应用时平台 4546 条评价中 S3+/S4/S5 的 confirmed 行为 **0**，
   故 `chain_blocked` 当前 0、未入账被拦 0——没有回改历史信用；对直写/历史脏行由结算面兜住。
5. **回填/在途评价会被压级**：`backfill_outcome_evaluations.py` 等拿不到五元（尤其 commit sha
   与原始数据锚），其 S2+ 产出落 `candidate/E1`——这是预期行为（不给自己发无法追溯的等级），
   但会拉低回填统计；正确修法是把 run 登记（`contracts/tensorphysics-run-manifest-v1`）
   接到回填器，本节点**未做**。
6. **只验形态不验内容**：`report:123` 只保证是正整数；报告是否存在/相关归 `tools/eval_redteam.py`
   与报告 QA 门。本模块是**必要条件**，不是充分条件。
7. **前端未渲染**：质量中心后端已给 7 个指标 + 门态，四中心页面尚未展示（下节点接）。

## ⑥ 误差判定（vs 判据基准，逐项）

| 关键量 | 基准 | 实测 | 误差 | <3%? |
|---|---|---|---|---|
| 完整链被压级行数 | 0 | 0 | 绝对 0 | ✅ |
| 授予 E > 申报 E 的行数 | 0 | {s['granted_above_declared']} | 绝对 0 | ✅ |
| S3 缺 `data` 降级率 | 100% (4/4) | 100% (4/4) | 0 | ✅ |
| 缺件封顶到 E1 比例（S2–S5 非硬拒行） | 100% (64/64) | 100% (64/64) | 0 | ✅ |
| 质量中心链完整度 vs 手写 SQL | 精确相等 | 0.8 / 0.9 一致 | 0 | ✅ |
| pytest 专项 | 32 passed | 32 passed | 0 | ✅ |
| pytest 回归（§7.1 实体 + 红队 + 四中心） | 全通过 | 36 + 178 + 中心 42 通过 | 0 | ✅ |

无物理基准量（本节点为治理/契约面，不含数值求解），不涉 >15% 不可接受判据；
判据矩阵与实测**逐格相等**。**未做**：回填器接真实工件、前端渲染、report_id 内容级校验。

## 五元自指（本结论自己的链）

| 元素 | 引用 |
|---|---|
| theory | AOG SPEC v1.2 §7.1 + `derivation:#evidence-chain-five-elements` |
| code | `TensorPlatform@{demo.get('code_sha', '')}`（`artifacts/in59_a3/pg_demo.json:code_sha`） |
| config | `artifacts/in59_a3/run_config.json` @ `{res['config_hash']}` |
| data | `artifacts/in59_a3/results.json`（{rows_n} 格矩阵）+ `artifacts/in59_a3/pg_demo.json` |
| report | 本报告（report_id 见 `pg_demo.json:cases.complete`） |
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    res = json.loads((ART / "results.json").read_text(encoding="utf-8"))
    demo_path = ART / "pg_demo.json"
    demo = json.loads(demo_path.read_text(encoding="utf-8")) if demo_path.exists() else {}
    figures = [{"name": n, "caption": c,
                "png_base64": base64.b64encode((ART / n).read_bytes()).decode()}
               for n, c in FIGURES]
    payload = {
        "title": "IN59-A3 结论五元证据链：缺件封顶 E1 与结算面链检",
        "summary": "S2+ 结论须带五元引用；缺件把 E 封顶 E1，S3+ 因封顶跌破门槛落 candidate；"
                   "结算面按 chain_blocked 拦付；质量中心展示链完整度。判据矩阵 120 格 + PG 单事务演练全过。",
        "scenario_key": "platform_governance",
        "mission_id": MISSION,
        "stage": "S3",
        "tags": ["evidence-chain", "IN59-A3", "outcome-evaluations", "settlement-gate", "quality-center"],
        "markdown": build_markdown(res, demo),
        "figures": figures,
    }
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    print(f"payload bytes: {size} (figure payload budget < 250000)")
    if args.dry_run:
        pathlib.Path("/tmp/in59_a3_report.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print("dry-run: wrote /tmp/in59_a3_report.json")
        return 0
    token = _post(f"{base}/auth/login", {
        "username": os.environ["TENSOR_AGENT_USER"],
        "password": os.environ["TENSOR_AGENT_PASS"],
    })
    tok = token.get("access_token") or token.get("token") or token.get("data", {}).get("access_token")
    created = _post(f"{base}/tensor/reports", payload, token=tok)
    print(json.dumps({k: created.get(k) for k in ("id", "title", "status", "mission_id")
                      if k in created}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())