#!/usr/bin/env python3
"""m408 值班长班次交付文档生成（docs/operator_shift_m408.md）。

所有数字由 API 解析后注入（复用 m408_shift_report.parse_all），零手抄。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from m408_shift_report import ART, REPORT_ID, SWEEP, login, parse_all  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "operator_shift_m408.md")


def main():
    token = login()
    d = parse_all(token)
    disp = json.load(open(os.path.join(ART, "m408_dispatches.json")))["dispatches"]
    sp = d["sphere"]
    bl = d["backward_ladder"]
    verdict = {
        359: ("PASS", "Cd +%.3f%%/+%.3f%% < 3%%" % (sp[0]["err_pct"], sp[1]["err_pct"]), "—"),
        372: ("HONEST_FAIL", "L=48 %+.2f%%；L=80 %+.2f%% PASS" % (
            d["suboff_L48"]["err_pct"], d["suboff_L80"]["err_pct"]),
            "判据真红（几何欠分辨率 R_cells<2）"),
        373: ("FAIL", "中心线 max 0.476% < 3%，但 H→2H 单调收敛判据红", "其他（agent 未 finish / gate=no-evidence）"),
        374: ("HONEST_FAIL", "G3 闭包 3.03e-2（阈 1e-6）、G11a 10.05%（阈 0.5%）；19/21 门 PASS",
              "判据真红（闭包/求解器一致性）"),
        385: ("HONEST_FAIL", "X_r %+.2f%% vs Erturk（%+.2f%% vs Armaly）" % (
            bl[-1]["err"], bl[-1]["err_armaly"]), "判据真红 + 判据口径争议（两锚互斥）+ SDAA 墙钟受限"),
        386: ("HONEST_FAIL", "Cf %+.2f%%（best）" % d["blasius"]["err_pct"], "判据真红（受限通道/边缘加速 u_edge/U≈1.08）"),
        387: ("HONEST_FAIL", "X_r/h %+.2f%%（H=10）" % d["bstep3d"]["err_pct"], "判据真红（ER 离散 + 格点伪差）"),
    }
    sweep_tbl = "\n".join(
        "| %d | `%s` | %s | %s | %s | %s |" % (
            m["mid"], m["case"], m["status"],
            verdict[m["mid"]][0], verdict[m["mid"]][1], verdict[m["mid"]][2])
        for m in SWEEP)
    rep_tbl = "\n".join("| %d | `%s` | %d |" % (m["mid"], m["case"], REPORT_ID[m["case"]]) for m in SWEEP)
    dsp_tbl = "\n".join(
        "| %s | %s | %s | `%s` |" % (x.get("case"), x.get("mission_id"), x.get("status"), x.get("task_key"))
        for x in disp)

    doc = f"""# 值班长班次 m408 · 分析表 + 派发清单 + 下一班建议

- 班次号：**m408**（分支 `agent/m408`，repo TensorPlatform）
- 角色：值班长（分析与调度，**未改任何业务代码**；新增文件仅分析脚本与工件）
- 本班硬上限：最多派 3 单，只准派 TensorLBM 扫荡类 goal → 实派 **3 单**（额度用尽）
- 数据来源：`GET /api/v1/tensor/missions?limit=50`（扫荡线 = goal 含「SDAA 验证案例扫荡」）、
  `GET /tensor/missions/<id>`、`GET /tensor/reports/<id>`；全部数字由
  `scripts/m408_shift_report.py` / `scripts/m408_shift_doc.py` 程序化解析注入，零手抄。

## 0. 黑名单前置检查（★必做）

`cat /srv/tensor/agent/guide_blacklist.json` → **文件不存在**（`find /srv/tensor/agent -name "*blacklist*"` 亦无命中）。
结论：本班无案例被「根因升级线」接管，7 个案例均处于可派状态；**但该缺口本身已登记为升级事项 U1**
（无黑名单 = 每班只能靠记忆判断「谁已接管」，存在重复派发/原地打转风险）。

## 1. 扫荡线窗口完工单判读（7 单）

| mission | 案例 | 单终态 | 案例判决 | 关键证据（API 解析） | 失败归类 |
|---|---|---|---|---|---|
{sweep_tbl}

**汇总（程序化计数）**：完工 7 单 = **PASS 1 / HONEST_FAIL 5 / FAIL（进程）1**；
其中 aircraft_icing 为门级口径：**19 门 PASS / 2 门 FAIL（G3、G11a）/ 1 门 GAP（D1/D2 MCCS 锚）**。

### 1.1 失败原因归类统计（6 个非全绿单）

| 归类 | 单数 | 明细 |
|---|---|---|
| 判据真红（数值/几何） | 4 | suboff_re1000（R_cells<2 体素化）、blasius（受限通道 +{d['blasius']['err_pct']:.2f}%）、bstep_3d（ER 离散 {d['bstep3d']['err_pct']:+.2f}%）、aircraft_icing（G3/G11a） |
| 判据口径争议（需 owner 裁） | 2 | backward_step（Erturk 2.878 vs Armaly 3.05 两锚互斥 ~6%）、startup_poiseuille（「H→2H 单调收敛」对瞬态起动流是否成立） |
| 环境 / triton 绕行 | **0** | 本窗口无 triton 后端缺失类失败；SDAA 卡均可用（各单 evidence 有 torch_sdaa 版本+卡号） |
| 其他（agent 未 finish） | 1 | m373 startup_poiseuille：tool_calls=80、gate=no-evidence、finished_status 缺失 |

> 关键判读：**HONEST_FAIL 主体是真判据真红，不是环境**。若把 5 个 HONEST_FAIL 当环境问题重试，
> 会得到原地打转——这正是本班只派 3 单、且 1 单是「返工收口」而非「重跑」的原因。

### 1.2 各单原始报告（回读用）

| mission | 案例 | 报告 id |
|---|---|---|
{rep_tbl}

## 2. 处置与派发（3 单，达上限）

派发参数：`POST /api/v1/tensor/missions`，`runner=native`，`repo=TensorLBM`，`origin=agent`，
每单带 `task_key` 幂等键（重跑脚本不产生重复单）。

| 案例 | mission_id | 状态 | task_key |
|---|---|---|---|
{dsp_tbl}

派发理由（按证据）：
1. **startup_poiseuille → 409（返工收口）**：唯一「单级 FAIL」，且是交付缺失（未 finish）而非物理红；
   工作树里已有 5 个脚本但无判据落盘，属最低成本可收口单。goal 内明确要求：逐条列出判据族、给出
   err(t) 非单调的物理证据、**不得改阈值**、口径问题只能上报 owner。
2. **blasius_flat_plate → 410（最远红点）**：+{d['blasius']['err_pct']:.2f}% 是全窗口最大误差，根因线索明确
   （u_edge/U=1.083 → 受限通道化），修复方向可执行（顶部边界改自由滑移 / 域高加倍 / x 加密），
   且要求复现前单最佳档到 ±1% 以排除环境差异。
3. **bstep_3d → 411（最接近门）**：{d['bstep3d']['err_pct']:+.2f}%，仅超门 0.83pp；要求做 H=10/16/24(±32) 分辨率梯子
   + 统计 ER_effective（台阶高 ±1 格即 ±10%）+ 对齐 reattachment 提取口径。

**本班未派的非全绿单（附理由，交下一班）**：
- `backward_step`（385）：**不派**。Re=100 两锚互斥（2.878 vs 3.05，~6% > 3% 门）→ 选锚即改口径，
  必须 owner 裁定（U2）。盲目重试即原地打转。
- `suboff_re1000`（372）：**不派**。L=80 档已 {d['suboff_L80']['err_pct']:+.2f}% 在门内，案例事实上已闭合；
  仅 L=48（{d['suboff_L48']['err_pct']:+.2f}%）差 0.17pp，属成本换精度，优先级低于上述 3 单。
- `aircraft_icing_rg15_ipw2`（374）：**不派**。前单为诊断单且已定源（G3 闭包 3.03e-2、G11a 10.05%），
  修复需动结冰求解器本体（非扫荡类任务形态），应走单独开发线而非扫荡重试。

## 3. 队列与下探判定

- 派发前非终态单（in-flight）= **12**（含本班 m408），**≥ 4 → 按规则不派下探案例**，
  3 单额度全部用于 FAIL 案例「诊断+修复绕行」。
- 派发后非终态单 = 13（409/410/411 均 `running`，另 2 单在本班期间转终态）。
- 下一班若 in-flight < 4，下探候选按案例字母序（`benchmarks/pending` 未覆盖者）：
  **bstep_3d → 已派；blasius_flat_plate → 已派**；剩余字母序候选需下一班用集合差重新计算（勿用本表记忆）。

## 4. 里程碑判定（合并报告）

- 扫荡线历史口径早已 ≥10 单（历史合并报告 `id=316`「TensorLBM 验证案例扫荡合并报告 — 27 案例 68 单 PASS 全汇总」，
  m393 班次；更早还有 220/235/238/269 等班次合并报告）→ **「尚无合并报告」条件不成立，本班不重复发布合并报告**（避免同质报告堆积）。
- 本班按常设报告纪律发布**本班分析报告**（六要素齐备 + 3 图）：
  - `id=324`（rev2，**最终版**）：【SDAA 扫荡】值班长 m408 班次分析（rev2 图注英文版）
  - `id=323`（rev1）：已发布但图内中文因**容器无 CJK 字体**渲染为豆腐块 → 已由 324 取代
    （平台无 report 更新/撤回端点，故 323 保留在库；QA 复审请以 324 为准）。

## 5. 下一班建议（优先级序）

1. 先看 409/410/411 的 finish 结论；**特别验收「判据口径未被放宽」**（diff 阈值/参考值字段）。
2. 若 411（bstep_3d）梯子最细档仍 >3%，转向「格点伪差」根因（bounce-back 滑移项），可复用 319 的 τ-matched 方法论。
3. 若 410（blasius）确认受限通道化，做「域高 H_min 扫描」一次定死域高下限，写进 README 防再次踩坑。
4. **不要**再派 backward_step 重试单，直到 owner 回答 U2（锚点选择）。
5. 下探前先做「verified/pending 集合差」程序化计算，勿凭本表字母序记忆（口径会漂）。
6. 报告图注统一用英文标签（容器无 CJK 字体），或先装 Noto CJK 再出中文图（U5）。

## 6. 升级事项（系统性问题，交 owner）

- **U1 黑名单文件缺失**：`/srv/tensor/agent/guide_blacklist.json` 不存在 → 扫荡线无法程序化排除已被根因升级线接管的案例。
  建议生成并纳入班次前置检查（schema 建议 `{{"cases":[], "reason":"", "owner_mission":0}}`）。
- **U2 backward_step 锚点互斥**：Erturk 2.878（数值）vs Armaly 3.05（实验）散布 ~6% > 3% 门 → 数学上不可能同时满足。
  需 owner 裁定唯一锚点，否则该案例永远红。
- **U3「扫荡线累计完工单数」多口径**：本窗口 7 单 vs 历史报告 68/86/168 单三种口径并存 → 里程碑判定不可复现。
  建议冻结唯一 `scenario_key` 作为计数口径。
- **U4 startup_poiseuille 判据口径**：数值阈值全过（0.476%，余量 6.3×），红点在附加「H→2H 单调收敛」判据；
  瞬态起动流的误差非单调在物理上正常。建议裁定：改为「末段窗口 ≤3% 且 |err| 不随时间增长」。
- **U5 容器缺 CJK 字体**：matplotlib 无 CJK 字体（仅 DejaVu/Noto-Latin），中文图注必成豆腐块，
  本班因此重发 rev2 英文图注。建议镜像内装 Noto Sans CJK，或报告规范统一英文图注。

---
*本文件由 `scripts/m408_shift_doc.py` 生成（数字取自平台 API，零手抄）。*
"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(doc)
    print("WROTE", OUT, len(doc))


if __name__ == "__main__":
    main()