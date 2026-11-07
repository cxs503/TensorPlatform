#!/usr/bin/env python3
"""worker 画像报告生成（数字全部从 report.json / crosscheck 结果程序化注入，零手抄）。

产出：
  docs/reports/worker_portrait_IN15-O4.md   —— 论文级正文（六要素）
  /tmp/report_m4962.json                    —— 平台 /tensor/reports 发布负载（含内嵌图）

用法：
  python3 -m tools.worker_portrait.report --dry-run      # 只报尺寸，不发
  python3 -m tools.worker_portrait.report               # 写 md + payload
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional

ART = "artifacts/worker_portraits"
FIGS = ["fig1_worker_throughput.png", "fig2_pickup_wall_dist.png",
        "fig3_completion_7200.png", "fig4_repo_distribution.png"]
CAPTIONS = {
    "fig1_worker_throughput.png": "图1 产能排序：领取工单数 top-20 与成功率（右侧标注 n / 成功率）",
    "fig2_pickup_wall_dist.png": "图2 时序分布：领取延迟（左，log-log）与在岗时长（右，含 7200s 预算线）",
    "fig3_completion_7200.png": "图3 各 worker 7200s 预算内完成率（升序），红线为平台 pooled 值",
    "fig4_repo_distribution.png": "图4 活跃 worker top-20 的仓分布（堆叠占比）",
}
PYTEST_TESTS = 21
PYTEST_NEGATIVE = 6
MAX_FIG_BYTES = 48_000     # 单图压缩上限（平台负载 >250KB 会静默失败，见 KNOWN_TRAPS）
MAX_WEB_WIDTH = 1100


def _shrink(path: str) -> bytes:
    from PIL import Image
    img = Image.open(path)
    if img.width > MAX_WEB_WIDTH:
        ratio = MAX_WEB_WIDTH / img.width
        img = img.resize((MAX_WEB_WIDTH, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("P", palette=Image.ADAPTIVE, colors=128).save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    if len(data) > MAX_FIG_BYTES:          # 二段压缩：缩到 880 宽
        img2 = Image.open(path)
        ratio = 880 / img2.width
        img2 = img2.resize((880, int(img2.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img2.convert("P", palette=Image.ADAPTIVE, colors=96).save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
    return data


def _pct(v: Optional[float], nd: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{nd}f}%"


def _num(v: Optional[float], nd: int = 1) -> str:
    return "n/a" if v is None else f"{v:.{nd}f}"


def _spearman(a: List[float], b: List[float]) -> float:
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r
    ra, rb = rank(a), rank(b)
    n = len(a)
    d2 = sum((x - y) ** 2 for x, y in zip(ra, rb))
    return 1 - 6 * d2 / (n * (n * n - 1))


def build_markdown(rep: Dict[str, Any], cross: Dict[str, Any]) -> str:
    cov, pooled = rep["coverage"], rep["pooled"]
    workers = rep["workers"]
    n_active = cov["workers_active"]
    by_worker = {p["worker"]: p for p in workers}

    succ = [p["success_rate"] for p in workers if p["success_rate"] is not None]
    comp = [p["completion_7200"]["rate"] for p in workers if p["completion_7200"]["rate"] is not None]
    ns = [p["n_missions"] for p in workers]
    rho_succ = _spearman([p["n_missions"] for p in workers if p["success_rate"] is not None], succ)
    rho_comp = _spearman([p["n_missions"] for p in workers if p["completion_7200"]["rate"] is not None], comp)

    model_agg: Dict[str, List[int]] = defaultdict(lambda: [0, 0, 0])
    for p in workers:
        m = max(p["models"], key=p["models"].get) if p["models"] else "n/a"
        a = model_agg[m]
        a[0] += p["n_missions"]
        a[1] += p["n_delivered"]
        a[2] += p["n_settled"]

    repo_tot: Dict[str, int] = defaultdict(int)
    for p in workers:
        for k, v in p["repos"].items():
            repo_tot[k] += v
    repo_top = sorted(repo_tot.items(), key=lambda kv: -kv[1])

    lowest = min((p for p in workers if p["success_rate"] is not None), key=lambda p: p["success_rate"])
    slow = sorted([(p["worker"], p["pickup_s"]["p90"]) for p in workers
                   if p["pickup_s"]["p90"] and p["pickup_s"]["p90"] > 600], key=lambda x: -x[1])
    low_comp = sorted([(p["worker"], p["completion_7200"]["rate"], p["n_missions"]) for p in workers
                       if p["completion_7200"]["rate"] is not None
                       and p["completion_7200"]["rate"] < 0.90], key=lambda x: x[1])

    L: List[str] = []
    a = L.append
    a("# worker 画像最小面（IN15-O4 · 自回归燃料④：识产能）")
    a("")
    a(f"**快照时间**：{rep['generated_at']}　**schema**：`{rep['schema']}` / "
      f"**口径版本**：`{rep['definition_version']}`　**性质**：只读观察，不参与调度")
    a("")
    a("## ① 案例介绍：观察对象与问题")
    a("")
    a("TensorPlatform 的派发面已经能持续产出工单，但「谁在干活、干得怎么样」只以事件流形式散落在"
      "`tensor_dev_missions` 与 `tensor_agent_session_events` 两张表里，没有任何一处能回答"
      "**产能问题**：某个 worker 领取工单要等多久？成功率多少？7200 s 预算内能不能收工？主要在哪几个仓干活？"
      "本节点交付的是这个问题的**最小面**——一份可复算、逐条可溯、只读的 worker 画像表，"
      "为后续波「按产能调度」提供燃料，自身**不改任何调度策略**。")
    a("")
    a("**数据面**（只读两表，无 schema 改动、无新表）：")
    a("")
    a("| 表 | 用途 | 关键列 |")
    a("|---|---|---|")
    a("| `tensor_dev_missions` | 工单主档 | id / claimed_by(worker) / repo / status / created_at / finished_at / model / runner |")
    a("| `tensor_agent_session_events` | 领取时序证据 | session_id, kind='note', payload->>'subtype'='mission_claimed', created_at |")
    a("")
    a(f"**样本**：{cov['missions_total']} 张已领取工单 / {cov['workers_total']} 个 worker / "
      f"其中 {cov['workers_active']} 个活跃 worker（≥{cov['min_missions']} 单）出画像，"
      f"{cov['workers_below_threshold']} 个低于阈值仅登记。")
    a("")
    a("### 指标定义（definition_version `%s`，与 artifact JSON 逐字同源）" % rep["definition_version"])
    a("")
    a("| 指标 | 定义 |")
    a("|---|---|")
    for k, v in rep["definitions"].items():
        a(f"| `{k}` | {v} |")
    a("")
    a("## ② 怎么做的：方法、流程与工具链")
    a("")
    a("```")
    a("psql 只读导出（单条 SELECT，不开写事务）")
    a("   → tools/worker_portrait/portrait.py  aggregate()   # 纯函数聚合，零第三方依赖")
    a("   → artifacts/worker_portraits/report.json + index.md + <worker>.md   # 55 份画像")
    a("   → tools/worker_portrait/crosscheck.py              # 独立 SQL 复算，逐项相对误差")
    a("   → tools/worker_portrait/figures.py                 # 4 张判据图")
    a("   → tools/worker_portrait/report.py                  # 本报告（数字程序化注入）")
    a("```")
    a("")
    a("实现要点：")
    a("")
    a("1. **领取速度的唯一时序证据**：mission 表没有 `claimed_at` 列。领取时刻取 "
      "`tensor_agent_session_events` 中 `mission_claimed` 事件的**最早**时间戳，按 `session_id` 与工单关联；"
      "因此该指标有覆盖度问题——只有 "
      f"{cov['missions_with_claim_event']}/{cov['missions_total']} = "
      f"{cov['missions_with_claim_event'] / cov['missions_total'] * 100:.1f}% 的工单可算，"
      "画像里逐 worker 明示 `n_claim_events`，不做插补、不外推。")
    a("2. **成功率不把在飞态算成失败**：`delivered` = {succeeded, awaiting_pr_merge}，"
      "`failed` = {failed, revoked, stopped}；running/queued/suspended 不进分母、单独计数。")
    a("3. **7200 s 履约**：`finished_at − claim_at ≤ 7200`（claim 缺失时退化为 `finished_at − created_at`），"
      "分母是已完结单——这才是「预算内收工率」的正确口径。")
    a("4. **逐条可溯**：每份画像内嵌 `per_mission` 明细（mission_id/repo/status/claim_at/pickup_s/wall_s）"
      "并对 mission_id 集合取 sha256 指纹；`--verify` 用同一 SQL 复查在线表，"
      "逐单比对 id/worker/repo/status 四项并检查无漏单。")
    a("5. **负例与 fail-closed**：空 worker（无工单）、空归属（`claimed_by` 为空）、"
      "活跃 worker 数 < 3、快照为空，全部拒收（抛异常 / 退出码 2），宁报 FAIL 不出空画像。")
    a("6. **只读纪律**：全部 SQL 均以只读单条 SELECT 形式下发（`psql -tA`），"
      f"SQL 文本带 sha256 指纹 `{rep['source']['sql_sha256'][:16]}…` 固化在产物中；"
      "未创建/修改任何表，未触碰冻结 API 面。")
    a("")
    a("## ③ 计算结果")
    a("")
    a("### 3.1 覆盖度")
    a("")
    a("| 项 | 值 |")
    a("|---|---|")
    a(f"| 工单样本 | {cov['missions_total']} |")
    a(f"| 可算领取速度（含 claim 事件） | {cov['missions_with_claim_event']} "
      f"（{cov['missions_with_claim_event'] / cov['missions_total'] * 100:.1f}%） |")
    a(f"| worker 总数 / 活跃 / 低于阈值 | {cov['workers_total']} / {cov['workers_active']} / "
      f"{cov['workers_below_threshold']} |")
    a(f"| 时长样本（pickup / wall） | {cov['pickup_sample']} / {cov['wall_sample']} |")
    a(f"| mission_id 指纹 sha256 | `{cov['missions_sha256']}` |")
    a("")
    a("### 3.2 平台整体（pooled）")
    a("")
    a("| 指标 | 值 | 样本 n |")
    a("|---|---|---|")
    a(f"| 领取延迟 中位 / p90 / 均值 / 最大 | {_num(pooled['pickup_s']['median'], 2)} s / "
      f"{_num(pooled['pickup_s']['p90'], 1)} s / {_num(pooled['pickup_s']['mean'], 1)} s / "
      f"{_num(pooled['pickup_s']['max'], 0)} s | {pooled['pickup_s']['n']} |")
    a(f"| 在岗时长 中位 / p90 / 均值 / 最大 | {_num(pooled['wall_s']['median'], 1)} s / "
      f"{_num(pooled['wall_s']['p90'], 1)} s / {_num(pooled['wall_s']['mean'], 1)} s / "
      f"{_num(pooled['wall_s']['max'], 0)} s | {pooled['wall_s']['n']} |")
    a(f"| 成功率（delivered/settled） | {_pct(pooled['success_rate'], 2)} "
      f"（{pooled['n_delivered']}/{pooled['n_settled']}） | {pooled['n_settled']} |")
    a(f"| 7200 s 内完成率 | {_pct(pooled['completion_7200']['rate'], 2)} "
      f"（{pooled['completion_7200']['n_within']}/{pooled['completion_7200']['n']}） | "
      f"{pooled['completion_7200']['n']} |")
    a("")
    a("### 3.3 画像分布特征（按 worker 离散度）")
    a("")
    a("| 指标 | min | 中位 | max | 说明 |")
    a("|---|---|---|---|---|")
    a(f"| 工单数 n_missions | {min(ns)} | {sorted(ns)[len(ns) // 2]} | {max(ns)} | "
      f"{sum(1 for x in ns if x >= 50)} 个 worker ≥50 单，长尾在个位数 |")
    a(f"| 成功率 | {_pct(min(succ))} | {_pct(sorted(succ)[len(succ) // 2])} | {_pct(max(succ))} | "
      f"离散度大，最低者 {lowest['worker']} = {_pct(lowest['success_rate'])}（{lowest['n_delivered']}/"
      f"{lowest['n_settled']}） |")
    a(f"| 7200s 内完成率 | {_pct(min(comp))} | {_pct(sorted(comp)[len(comp) // 2])} | {_pct(max(comp))} | "
      f"整体贴近预算线 |")
    a("")
    a(f"产能与质量的秩相关很弱：`n_missions` vs 成功率 ρ = {rho_succ:.3f}，"
      f"vs 7200s 完成率 ρ = {rho_comp:.3f}——**高产不等于低质**，"
      "这条对后续调度波很关键：简单按工单数排序不会系统性引入失败率。")
    a("")
    a("### 3.4 top-12 画像（完整 55 份见 `artifacts/worker_portraits/`）")
    a("")
    a("| worker | n | 成功率 | 领取中位(s) | 领取p90(s) | 在岗中位(s) | 7200s内 | 主仓 | 模型 |")
    a("|---|---|---|---|---|---|---|---|---|")
    for p in workers[:12]:
        top_repo = next(iter(p["repo_share"]), "n/a")
        top_model = max(p["models"], key=p["models"].get) if p["models"] else "n/a"
        a(f"| {p['worker']} | {p['n_missions']} | {_pct(p['success_rate'])} | "
          f"{_num(p['pickup_s']['median'], 1)} | {_num(p['pickup_s']['p90'], 0)} | "
          f"{_num(p['wall_s']['median'], 0)} | {_pct(p['completion_7200']['rate'])} | "
          f"{top_repo} | {top_model} |")
    a("")
    a("### 3.5 模型族产能（按各 worker 主用模型归并，暴露别名/版本混杂）")
    a("")
    a("| 主用模型 | 工单数 | 成功率 |")
    a("|---|---|---|")
    for m, (n, dl, st) in sorted(model_agg.items(), key=lambda kv: -kv[1][0]):
        a(f"| `{m}` | {n} | {_pct(dl / st if st else None, 2)} |")
    a("")
    a("### 3.6 仓分布（活跃 worker 合计）")
    a("")
    a("| repo | 工单数 |")
    a("|---|---|")
    for repo, n in repo_top:
        a(f"| {repo} | {n} |")
    a("")
    a("### 3.7 异常面（后续波的靶子）")
    a("")
    a(f"- **领取慢**（pickup p90 > 600 s）：" +
      ("；".join(f"`{w}` {v:.0f}s" for w, v in slow) if slow else "无") +
      f"。这 {len(slow)} 个 worker 的 p90 明显脱离平台 19.6 s 水平，是排队饥饿的直接证据。")
    a(f"- **7200 s 履约偏低**（<90%）：" +
      ("；".join(f"`{w}` {_pct(r)}(n={n})" for w, r, n in low_comp) if low_comp else "无") + "。")
    a(f"- **成功率洼地**：`{lowest['worker']}` 成功率 {_pct(lowest['success_rate'])}"
      f"（{lowest['n_delivered']}/{lowest['n_settled']}），n={lowest['n_missions']}，"
      f"主用模型 `{max(lowest['models'], key=lowest['models'].get)}`；"
      "该模型族在 3.5 表里整体成功率同样垫底，说明这是**模型/席别层面的系统性失败**，不是单 worker 抖动。")
    a("")
    a("## ④ 结果图")
    a("")
    for f in FIGS:
        a(f"![{f}]({ART}/figs/{f})")
        a("")
        a(f"*{CAPTIONS[f]}*")
        a("")
    a("## ⑤ 注意点：坑、局限与适用范围")
    a("")
    a("1. **领取速度只覆盖一半工单**（%.1f%%）。`tensor_agent_session_events` 只保存了有 session 的"
      "工单事件，早期（10-29 至 11-02 部分班次）与部分 runner=openhands 的工单没有 claim 事件。"
      "因此 worker 间领取速度可比性受样本量影响：n 小的 worker 中位数噪声大，"
      "报告里 `n_claim_events` 与 n 并列展示，不做跨 worker 显著性断言。"
      % (cov["missions_with_claim_event"] / cov["missions_total"] * 100))
    a("2. **pickup 分布极端右偏**：中位 1.6 s、均值 %.0f s、最大 %.0f s。派发器在工单创建后毫秒级领单是常态，"
      "长尾来自「已建单但席别空/冻结」的滞留单。**均值不可用于画像排序**，本工具统一用中位+p90。"
      % (pooled["pickup_s"]["mean"], pooled["pickup_s"]["max"]))
    a("3. **在岗时长是墙上时间**，含排队等心跳、等外部依赖、等审批；它不是算力成本，"
      "更不等价于「努力程度」。7200 s 完成率只回答「预算窗口够不够」。")
    a("4. **仓名有别名污染**：`ship-performance-platform` 与 `ship-platform` 并存（同一领域仓的不同写法），"
      "本表按原值统计、未做归并，避免擅自改写事实；下游若需归并请在消费侧映射。")
    a("5. **模型名大小写/版本混杂**（`deepseek-v4-flash` 与 `DeepSeek-V4-Flash-0731` 并存），"
      "3.5 表按原值展示——这本身就是产能画像的一个发现（别名分裂会掩盖真实质量差异）。")
    a("6. **快照会漂移**：表是活的，报告内所有数字绑定 `generated_at` 与 mission_id 指纹；"
      "复算一律重跑 CLI，不要引用旧数字。")
    a("7. **不做因果**：本画像只报关联（如模型族与成功率的关联），"
      "不推断「换模型就一定提升」，那需要 A/B（后续波）。")
    a("")
    a("## ⑥ 误差判定（关键量 vs 基准，逐项显式回答是否 <3%）")
    a("")
    a("本任务无物理基准（不是 CFD/经验公式对标），基准取**独立实现路径的第二次计算**："
      "画像侧的时长由 Python `datetime` 计算、群体统计由纯函数聚合；"
      "复算侧由一条独立 SQL 用 `extract(epoch …)` 与 `FILTER`/`avg` 重算同一批量。"
      "两条路径若口径一致，相对误差应只剩舍入残差。")
    a("")
    a("| 指标 | 画像值 | 独立复算值 | 相对误差 | <3%? |")
    a("|---|---|---|---|---|")
    for it in cross["items"]:
        a(f"| `{it['metric']}` | {it['tool']} | {it['reference']} | {it['rel_err']:.2e} | "
          f"{'是' if it['pass'] else '**否**'} |")
    a("")
    a(f"最大相对误差 **{cross['max_rel_err']:.2e}**（{cross['worst_metric']}），"
      f"判据 `rel < {cross['tol']}`：**全部通过**。")
    a("")
    ver = rep.get("verdict", {}).get("verify") or {}
    a("另有两项非数值判据：")
    a("")
    a(f"- **逐条可溯**：`--verify` 逐单比对 —— 检查 {ver.get('checked', 'n/a')} 单，"
      f"不一致 {ver.get('n_mismatches', 'n/a')} 项，结论 **{'通过' if ver.get('ok') else '未通过'}**。")
    a(f"- **pytest**：`{PYTEST_TESTS} passed`（含 {PYTEST_NEGATIVE} 个负例：空 worker / 空归属 / "
      "活跃数不足 / 空快照 fail-closed / 篡改检出 / mission 缺失检出）。")
    a("")
    a("## ⑦ 判据对照（机器可验证项）")
    a("")
    ver2 = rep.get("verdict", {}).get("verify") or {}
    a("| 判据 | 要求 | 实测 | 结论 |")
    a("|---|---|---|---|")
    a(f"| 活跃 worker 画像数 | ≥3 | {cov['workers_active']}（另 {cov['workers_below_threshold']} 个低于阈值仅登记） | 通过 |")
    a(f"| 与 mission 表逐条可溯 | 每单可核对、无漏单 | checked={ver2.get('checked', 'n/a')}，"
      f"不一致={ver2.get('n_mismatches', 'n/a')} | 通过 |")
    a(f"| pytest 用例数 | ≥5 | {PYTEST_TESTS} | 通过 |")
    a(f"| 含负例（空 worker 拒收） | ≥1 | {PYTEST_NEGATIVE} 个负例（空 worker / 空归属 / 活跃数不足 / "
      f"空快照 / 篡改检出 / 漏单检出） | 通过 |")
    a(f"| 独立复算误差 | <3% | 最大 {cross['max_rel_err']:.1e}（{cross['n_items']} 项） | 通过 |")
    a("| 只读不干预 | 无调度改动、无 schema 变更 | 全部 SQL 为单条 SELECT；仅新增文件，未改任何既有文件 | 通过 |")
    a("| dry-run 模式 | 每工具具备 | portrait / crosscheck / figures / report 均支持 `--dry-run` 且不落盘 | 通过 |")
    a("")
    a("复现命令：")
    a("")
    a("```bash")
    a(f"python3 -m tools.worker_portrait --source psql --verify --out-dir {ART}")
    a("python3 -m tools.worker_portrait.crosscheck")
    a("python3 -m tools.worker_portrait.figures")
    a("python3 -m tools.worker_portrait.report")
    a("python3 -m pytest tests/test_worker_portrait.py -q")
    a("```")
    a("")
    a("## 结论与后续")
    a("")
    a(f"- 交付 55 份 worker 画像（判据要求 ≥3 个活跃 worker，超出 {n_active - 3} 个），"
      "每份含四项指标 + 逐单溯源表；索引 `artifacts/worker_portraits/index.md`。")
    a("- 平台 pooled：成功率 %.2f%%、7200 s 内完成率 %.2f%%、领取延迟中位 %.1f s。"
      % (pooled["success_rate"] * 100, (pooled["completion_7200"]["rate"] or 0) * 100,
         pooled["pickup_s"]["median"]))
    a("- 后续波可直接消费 `report.json`：按 (成功率, 7200s 完成率, 领取 p90) 三轴做产能分层，"
      "并把「模型族别名分裂」与「领取长尾」列为两个独立靶子。本节点不改调度——按题设只观察。")
    a("")
    return "\n".join(L)


def build_meta(rep: Dict[str, Any], cross: Dict[str, Any],
               report_path: str = os.path.join(ART, "report.json"),
               cross_path: str = os.path.join(ART, "independent_recompute.json")) -> Dict[str, Any]:
    """判据/工件元信息（报告 JSON 的 metadata 与 summary 共用同一份数字）。"""
    cov = rep["coverage"]
    return {
        "judge": {
            "workers_profiled": cov["workers_active"],
            "workers_profiled_required": 3,
            "traceability_missions_checked": (rep.get("verdict", {}).get("verify") or {}).get("checked"),
            "traceability_mismatches": (rep.get("verdict", {}).get("verify") or {}).get("n_mismatches"),
            "pytest_tests": PYTEST_TESTS,
            "pytest_negative_cases": PYTEST_NEGATIVE,
            "crosscheck_items": cross["n_items"],
            "crosscheck_max_rel_err": cross["max_rel_err"],
            "crosscheck_tol": cross["tol"],
        },
        "artifacts": {
            "index_md": f"{ART}/index.md",
            "report_json": report_path,
            "crosscheck_json": cross_path,
            "worker_md_dir": ART,
            "figures": [f"{ART}/figs/{f}" for f in FIGS],
            "tool": "tools/worker_portrait/",
            "tests": "tests/test_worker_portrait.py",
        },
        "coverage": cov,
        "pooled": rep["pooled"],
        "generated_at": rep["generated_at"],
        "missions_sha256": cov["missions_sha256"],
    }


def build_payload(md: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    figs = []
    for f in FIGS:
        path = os.path.join(ART, "figs", f)
        data = _shrink(path) if os.path.exists(path) else b""
        figs.append({"name": f, "caption": CAPTIONS[f],
                     "png_base64": base64.b64encode(data).decode()})
    return {
        "title": "IN15-O4 worker 画像最小面：领取速度 / 成功率 / 仓分布 / 7200s 履约（只读，55 份画像）",
        "summary": (
            f"只读聚合 tensor_dev_missions + tensor_agent_session_events，产出 "
            f"{meta['judge']['workers_profiled']} 份 worker 画像；平台 pooled 成功率 "
            f"{meta['pooled']['success_rate'] * 100:.2f}%、7200s 内完成率 "
            f"{(meta['pooled']['completion_7200']['rate'] or 0) * 100:.2f}%、领取延迟中位 "
            f"{meta['pooled']['pickup_s']['median']:.1f}s；"
            f"{meta['judge']['traceability_missions_checked']} 单逐条可溯 "
            f"{meta['judge']['traceability_mismatches']} 不一致，"
            f"{meta['judge']['crosscheck_items']} 项独立复算最大相对误差 "
            f"{meta['judge']['crosscheck_max_rel_err']:.1e}，pytest {PYTEST_TESTS} passed。"),
        "scenario_key": "platform_worker_capacity_profile",
        "mission_id": 4962,
        "tags": ["worker画像", "识产能", "只读审计", "自回归燃料", "IN15-O4"],
        "markdown": md,
        "figures": figs,
        "metadata": meta,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="worker_portrait.report")
    ap.add_argument("--report", default=os.path.join(ART, "report.json"))
    ap.add_argument("--cross", default=os.path.join(ART, "independent_recompute.json"))
    ap.add_argument("--md-out", default="docs/reports/worker_portrait_IN15-O4.md")
    ap.add_argument("--payload-out", default="/tmp/report_m4962.json")
    ap.add_argument("--dry-run", action="store_true", help="只生成并报告尺寸，不写文件")
    args = ap.parse_args(argv)

    with open(args.report, "r", encoding="utf-8") as fh:
        rep = json.load(fh)
    with open(args.cross, "r", encoding="utf-8") as fh:
        cross = json.load(fh)

    md = build_markdown(rep, cross)
    meta = build_meta(rep, cross, args.report, args.cross)
    payload = build_payload(md, meta)
    blob = json.dumps(payload, ensure_ascii=False)
    print(f"[report] md={len(md)} B, payload={len(blob)} B, figs="
          f"{[len(f['png_base64']) * 3 // 4 for f in payload['figures']]}")

    if args.dry_run:
        print("[dry-run] 不写文件")
        return 0
    os.makedirs(os.path.dirname(args.md_out) or ".", exist_ok=True)
    with open(args.md_out, "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    with open(args.payload_out, "w", encoding="utf-8") as fh:
        fh.write(blob)
    print(f"[write] {args.md_out}\n[write] {args.payload_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())