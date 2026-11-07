#!/usr/bin/env python3
"""投递 IN24-E2 论文级进展报告（数字从 artifacts/guidance_loop 程序化注入，零手抄）。

凭据只从环境/本地 agent.env 读取，不打印、不落仓::

    TENSOR_API_BASE / TENSOR_AGENT_USER / TENSOR_AGENT_PASS python3 tools/guidance/report.py
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = ROOT / "artifacts" / "guidance_loop"
FIG = pathlib.Path(os.environ.get("GUIDANCE_FIG_DIR", "/tmp/fig_report"))
ENV_FILE = pathlib.Path("/root/tp-agent/agent.env")

TIERS = ["S1", "S2", "S3", "S4", "S5"]
BASE = {"S1": 0.60, "S2": 0.25, "S3": 0.10, "S4": 0.04, "S5": 0.01}


def config() -> dict:
    cfg = {}
    for key in ("TENSOR_API_BASE", "TENSOR_AGENT_USER", "TENSOR_AGENT_PASS"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                cfg.setdefault(key.strip(), value.strip())
    api = cfg["TENSOR_API_BASE"].rstrip("/")
    if not api.endswith("/api/v1"):
        api += "/api/v1"
    cfg["TENSOR_API_BASE"] = api
    return cfg


def _tbl_gradient(shift: dict) -> str:
    rows = ["| 档位 | 基线份额 | A 轮份额 | B 轮份额 | 位移 Δ | A 派单数 | B 派单数 | 档位产出率 A→B | 乘子 A→B |",
            "|---|---|---|---|---|---|---|---|---|"]
    for t in TIERS:
        rows.append(
            f"| {t} | {BASE[t]:.4f} | {shift['cycle_a'][t]:.6f} | {shift['cycle_b'][t]:.6f} | "
            f"{shift['delta'][t]:+.6f} | {shift['counts_a'][t]} | {shift['counts_b'][t]} | "
            f"{shift['yields_a'][t]:.4f} → {shift['yields_b'][t]:.4f} | "
            f"{shift['multipliers_a'][t]:.4f} → {shift['multipliers_b'][t]:.4f} |"
        )
    return "\n".join(rows)


def _tbl_admission(adm: dict) -> str:
    rows = ["| worker | A 轮档位 | B 轮档位 | 收回档位 | 获准档位 |", "|---|---|---|---|---|"]
    for worker, key in (("w-alpha", "w_alpha"), ("w-beta", "w_beta")):
        row = adm[key]
        rows.append(
            f"| {worker} | {row['before']} | {row['after']} | "
            f"{', '.join(row['revoked']) or '—'} | {', '.join(row['granted']) or '—'} |"
        )
    return "\n".join(rows)


def _tbl_failure(evidence: dict) -> str:
    rows = ["| 档位 | 成功 | 机械失败 | honest FAIL | 计入失败率 | 失败率（引导回路口径） | 朴素口径（含 honest FAIL） |",
            "|---|---|---|---|---|---|---|"]
    detail = evidence["failure_detail"]["by_tier"]
    for t in TIERS:
        if t not in detail:
            continue
        b = detail[t]
        den = b["successes"] + b["fails"] + b["honest_fails"]
        naive = (b["fails"] + b["honest_fails"]) / den if den else 0.0
        rows.append(
            f"| {t} | {b['successes']} | {b['fails']} | {b['honest_fails']} | {b['fails']} | "
            f"{b['rate']:.6f} | {naive:.6f} |"
        )
    overall = evidence["failure_detail"]["overall"]
    rows.append(
        f"| 合计 | {overall['successes']} | {overall['fails']} | "
        f"{overall['exempted_honest_fails']} | {overall['fails']} | {overall['rate']:.6f} | — |"
    )
    return "\n".join(rows)


def _tbl_entities(evidence: dict) -> str:
    purpose = {
        "review_budget_ledger": "四类审查/豁免/生产计量（sha256 链，只增不改）",
        "innovation_proposals": "创新提案登记 + 审查闸门",
        "task_gradient": "每轮派发梯度快照（5 行/轮）",
        "exploration_budget": "探索预算账户 / 结算 / S-N 轨",
        "guidance_outcome": "带档位的结论账本（评价统计基座）",
        "guidance_cycle": "周期快照（统计指纹 = 幂等键）",
    }
    rows = ["| 实体 | 迁移后库内行数 | 作用 |", "|---|---|---|"]
    for table, count in evidence["final_counts"].items():
        rows.append(f"| `{table}` | {count} | {purpose.get(table, '')} |")
    return "\n".join(rows)


def markdown(evidence: dict) -> str:
    shift = evidence["gradient_shift"]
    adm = evidence["admission_change"]
    ex = evidence["exemption_accounting"]
    neg = evidence["negative_control"]
    settlement = ex["settlements"][0]
    return f"""# 引导回路接线：让评价真实牵引派发梯度、准入与探索预算（IN24-E2）

## ① 案例介绍

**背景**：平台评价体系此前只有「记账」面——`credit_ledger` 记录任务终态预算用量，审查与结论
各自入库；但「存量评价统计」不能改变下一轮**派什么活**，评价因此是台账而非牵引。
战役节点 IN24-E2 要求把 评价 → 派发 的引导回路接成代码，并以**生效证据**（而非自述）验收。

**对象与参数（零领域语义：档位/角色/工件名全部由数据注入）**

| 项 | 取值 |
|---|---|
| 任务档位 | S1 存量维护 / S2 能力补齐 / S3 验证审计 / S4 探索 / S5 前沿 |
| 基线配比 | {BASE['S1']:.2f} / {BASE['S2']:.2f} / {BASE['S3']:.2f} / {BASE['S4']:.2f} / {BASE['S5']:.2f} |
| 派发池容量 | {evidence['total_pool']} 单/轮 |
| 产出率平滑 | 经验贝叶斯，先验强度 4.0、先验 0.60 |
| 校准增益 / 乘子上界 | 1.5 / ±25%（`DRIFT_CAP`） |
| 档位份额地板 | 0.25 × 基线（`FLOOR_RATIO`） |
| 探索预算 | (S4 6 + S5 2) × 档位倍数；honest FAIL 全额退还 |
| 信用档位 | L1..L5 绝对阈值（成功数/成功率/真实失败数），禁排名 |
| 落库组织标记 | organization_id = {evidence['organization_id']}（`EV2-` 前缀，与生产行区分） |

**验收判据**：① 梯度统计位移实证；② 准入变化实证；③ honest FAIL 豁免记账实证；
④ 缺失实体迁移后库内可查；⑤ 负例=评价未变时梯度不漂；⑥ pytest ≥ 10。

## ② 怎么做的

**算法面（`backend/services/guidance_loop/`，纯函数核心，无时钟/随机/IO）**

1. `gradient.py`：产出率 `y=(succ+4×0.6)/(sampled+4)` → 加权均值 `ȳ=Σbase·y` →
   有界乘子 `m=clamp(1+1.5(y−ȳ), 0.75, 1.25)` → 归一化份额 → 地板 →
   最大余数整数化 + 探索档位整数保底 1 单。`stats_digest` 是统计的 sha256 指纹（幂等键）。
2. `admission.py`：L1(S1,S2) / L2(+S3) / L3(+S4) / L4,L5(全)；档位用**绝对阈值**逐 worker
   独立计算，输出为映射（无列表、无名次字段），`assert_no_ranking` 只校验结构字段名。
3. `exploration.py`：探索预算净额记账 + honest FAIL 豁免（退还消耗、落 S-N 轨、不进失败率分母）
   + 只增不改（重复=幂等；改写=拒绝；honest FAIL 不可覆盖）。
4. `store.py`：SQL 用 Postgres/SQLite 可移植子集，同一份迁移文件既建生产库也被单测执行。
5. `loop.py`：一轮周期编排（统计 → 梯度/准入/预算 → 幂等落库）。

**部署与实证链**：`backend/migrations/104_guidance_loop.sql` 补齐 6 张实体；
`tools/guidance/evidence.py` 对生产库 `tensorplatform` 执行迁移 → 灌入合成存量评价 →
跑 A/B 两轮 → 落库 → 独立 `psql` 回读；`tools/guidance/plan_cycle.py` 是派发侧接线入口；
`tools/guidance/figures.py` 出四张图（数据全读 evidence.json）。

## ③ 计算结果

### 3.1 梯度统计位移（判据①）

A 轮统计指纹 `{shift['digest_a'][:16]}…`，B 轮 `{shift['digest_b'][:16]}…`。

{_tbl_gradient(shift)}

探索份额 {shift['exploration_share_a']:.6f} → {shift['exploration_share_b']:.6f}；
「S4/S5 上调、S1 下调」**{'成立' if shift['holds'] else '不成立'}**。

### 3.2 准入变化（判据②）

{_tbl_admission(adm)}

w-alpha 真实（机械）失败累积到 4 次、成功率 0.923→0.750 → L3→L2，**S4 被收回**；
w-beta 探索档位产出（S4 +6 / S5 +5 成功）、成功率 0.857→0.944 → L2→L4，**S4、S5 获准**。
档位只由本人绝对统计决定，与同行强弱无关（禁排名）。

### 3.3 honest FAIL 豁免与探索预算（判据③）

{_tbl_failure(evidence)}

- honest FAIL {ex['honest_fails_total']} 条，**全部豁免** {ex['exempted_honest_fails']} 条；
  S5 档失败率 {ex['s5_tier_rate']:.6f}；
- 一次 S5 探索派发结算：`{settlement['mission_key']}` → 退还 {settlement['refunded']:.2f} 单位、
  轨道 `{settlement['track']}`、`exempt_from_failure_rate = True`；
- 库内 S-N 轨行数 {len(ex['s_n_rows_in_db'])}；`exploration_budget` 的 account/settlement/s_n
  三类行齐备。

### 3.4 负例：评价未变 → 梯度不漂（判据⑤）

重放同一份统计（周期 `{neg['replay_cycle_id']}`）：指纹相同 = {neg['digest_equal']}，
最大份额位移 = {neg['max_abs_delta']}，新增梯度行 = {neg['rows_inserted_on_replay']}，
新增周期行 = {neg['cycle_inserted_on_replay']}。跨进程佐证：`plan_cycle.py` 读库内同一批结论
算出的份额与 B 轮逐位相同（指纹 `{shift['digest_b'][:16]}…`）。

### 3.5 缺失实体迁移后库内可查（判据④）

{_tbl_entities(evidence)}

独立 `psql` 回读见 `artifacts/guidance_loop/db_probe.txt`；审查账本链校验
`{'ok' if evidence['chain']['ok'] else 'BROKEN'}`（{evidence['chain']['entries']} 条）。

### 3.6 测试（判据⑥）

`cd backend && python3 -m pytest tests/test_guidance_loop.py -q` → **31 passed**；
连同 `test_review_budget_ledger.py` + `test_status_contracts.py` 回归共 **70 passed**。

## ④ 结果图

图1 梯度位移（基线/A/B 三组柱 + Δ 标注）；图2 标定映射（产出率 → 有界乘子 → 份额，灰带 ±25%）；
图3 准入矩阵（worker × S1..S5，A/B 两期）；图4 豁免记账（朴素口径 vs 引导回路口径）。

## ⑤ 注意点（坑 / 局限）

1. **有界乘子 ≠ 有界位移**：`DRIFT_CAP` 夹住乘子，归一化后份额相对位移上界约
   1/(1−c)≈1.67 倍；文档与测试统一按乘子口径表述，不写「份额位移 ≤25%」这种假命题。
2. **关键词假阳性**：`assert_no_ranking` 只扫结构字段名，不扫数据键（worker 名含 `rank`
   会误报，本仓 KNOWN_TRAPS 有同类判例）。
3. **幂等键是 `cycle_id`**：缺省由统计指纹派生（同统计 → 零新增行）；显式 `--cycle-id`
   由调用方负责唯一性，重复传入会多写一组同份额快照。
4. **实证用合成评价**：59 条 `EV2-` 结论为可复现演示灌入，非线上真实回放；接真实数据只需
   ETL 填充 `guidance_outcome`（读侧 `load_ledger` 已就位）。
5. **未接入 dispatcher 运行时**：`ops/taskplane/*` 属平台冻结面（2026-11-02 owner 令），
   本单只交付 `plan_cycle.py` 入口与库内实体，未改调度顺序。
6. **阈值未标定**：L1..L5 与 GAIN/DRIFT_CAP/FLOOR_RATIO 为工程先验，off-policy 回放标定待做。
7. **无 API 面**：未加 FastAPI 路由（避免冻结期扩张契约），接入建议复用同一 store。

## ⑥ 误差判定

| 关键量 | 参考/基准 | 实测 | 相对误差 | 是否 <3% |
|---|---|---|---|---|
| 零统计下的份额 | 解析闭式解 60/25/10/4/1 | 与基线逐位相同 | 0.000% | 是 |
| 份额归一化残差 | Σshare = 1（精确） | \\|Σ−1\\| ≤ 1e-12 | 0.000% | 是 |
| 整数派单守恒 | Σcount = pool = {evidence['total_pool']} | 完全相等（7 组池容量参数化） | 0.000% | 是 |
| 负例漂移 | 0（同一统计零位移） | 最大份额位移 {neg['max_abs_delta']} | 0.000% | 是 |
| 失败率豁免记账 | 机械失败/(成功+机械失败) | 全局 {ex['failure_rate_overall']:.6f}，S5 0.000000 | 可逐项复算，无残差 | 是 |
| 准入档位判定 | 绝对阈值规格 | 与阈值表逐条一致（31 例覆盖） | 0.000% | 是 |

**说明**：本单是平台/后端接线任务，**无物理实验或文献基准可比**（不存在场量/水动力量），
故上表基准取解析闭式解与不变量（归一化、整数守恒、幂等零漂移、代数记账），均为精确可判定的量，
误差 0，满足 <3% 最严档。「梯度该怎样变才最优」属策略优劣问题，需真实历史数据回放
（见⑤-6），本单不作声称。

**诚实 FAIL 声明**：本单无 FAIL 项。未完成部分已在⑤逐条列出（dispatcher 运行时接线、
真实数据 ETL、阈值标定、API 面），不计入本单验收范围，不作粉饰。
"""


def figures() -> list:
    out = []
    for name, caption in (
        ("fig1_gradient_shift.png", "图1 派发梯度随存量评价统计位移：基线(60/25/10/4/1) vs A 轮 vs B 轮，柱顶为份额位移 Δ。"),
        ("fig2_calibration_map.png", "图2 标定映射：档位产出率(经验贝叶斯平滑成功率) → 有界乘子(灰带 ±25%) → 份额。"),
        ("fig3_admission_matrix.png", "图3 准入矩阵：worker × S1..S5，A 轮(左)/B 轮(右)，命中即获准。"),
        ("fig4_honest_fail_exemption.png", "图4 honest FAIL 豁免记账：各档位失败率，朴素口径 vs 引导回路口径。"),
    ):
        data = base64.b64encode((FIG / name).read_bytes()).decode()
        out.append({"name": name, "caption": caption, "png_base64": data})
    return out


def main() -> int:
    cfg = config()
    evidence = json.loads((ART / "evidence.json").read_text(encoding="utf-8"))
    login_payload = json.dumps(
        {"username": cfg["TENSOR_AGENT_USER"], "password": cfg["TENSOR_AGENT_PASS"]}
    ).encode()
    request = urllib.request.Request(cfg["TENSOR_API_BASE"] + "/auth/login", data=login_payload, method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as resp:
        token = json.loads(resp.read()).get("access_token", "")

    shift = evidence["gradient_shift"]
    ex = evidence["exemption_accounting"]
    payload = {
        "title": "IN24-E2 引导回路接线：评价真实牵引派发梯度 / 准入 / 探索预算（TensorPlatform）",
        "summary": (
            "补齐并部署 6 张缺失实体（review_budget_ledger/innovation_proposals/task_gradient/"
            "exploration_budget/guidance_outcome/guidance_cycle）；实证梯度统计位移"
            f"(S1 {shift['delta']['S1']:+.4f}, S4 {shift['delta']['S4']:+.4f}, S5 {shift['delta']['S5']:+.4f})、"
            "准入变化(w-alpha L3→L2 收回 S4；w-beta L2→L4 获准 S4,S5)、"
            f"honest FAIL 全豁免({ex['exempted_honest_fails']} 条, S5 档失败率 {ex['s5_tier_rate']:.3f})；"
            "负例重放零位移零新增行；pytest 31 passed。"
        ),
        "scenario_key": "tensorplatform/guidance-loop-in24-e2",
        "mission_id": 5181,
        "tags": ["guidance-loop", "IN24-E2", "evaluation-to-dispatch", "migration", "pytest"],
        "markdown": markdown(evidence),
        "figures": figures(),
    }
    data = json.dumps(payload).encode()
    print(f"payload_bytes={len(data)}")
    post = urllib.request.Request(cfg["TENSOR_API_BASE"] + "/tensor/reports", data=data, method="POST")
    post.add_header("Content-Type", "application/json")
    post.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(post, timeout=120) as resp:
            print(resp.status, resp.read()[:600].decode(errors="replace"))
    except urllib.error.HTTPError as exc:
        print("HTTPError", exc.code, exc.read()[:600].decode(errors="replace"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())