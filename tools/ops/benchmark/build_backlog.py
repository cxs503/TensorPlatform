#!/usr/bin/env python3
"""生成 benchmark 闭环 backlog + 每条目 goal 文件。

单一事实源：TensorLBM origin/main 的 benchmarks/pending/README.md 案例总表
（状态/卡点/晋级动作/预算列直读嵌入 goal，零手抄）+ 本文件内的ANNOTATIONS
（owner 指令后的波次/模型/预算/动作细化）。跑法：
    /srv/tensor/shared/venvs/tensor_eco/bin/python build_backlog.py
输出：backlog.json、goals/<key>.txt、state.json（仅当不存在）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

LBM = "/srv/tensor/shared/TensorLBM"
WORK = Path("/srv/tensor/shared/tensor_eco/agent_work/benchmark_closure")
RUNS = Path("/srv/tensor/shared/runs/bm_close")

DOC = "160,14400,6000000"          # 文档/裁定类
COMPUTE = "240,28800,8000000"      # 计算类
IMPL, VERIF = "qwen3.7-plus", "qwen3.6-plus"

# (key, case, action, model, budget, gpu, deliverables, wait_for)
A = [
    # ── B0：零计算/轻计算闭环（先打信任） ──
    ("cavity-3d-full-cleanup", "cavity_3d_full",
     "清理归档：superseded 诊断稿处理（删除或标记 superseded 并指向 "
     "verified/cavity_3d_full），同步 pending/README.md 索引行与统计",
     VERIF, DOC, False, None, None),
    ("sphere-re100-mem-promotion", "sphere_re100_mem",
     "晋级归档 PR：pending/sphere_re100_mem → verified/；README 建议同批处理 "
     "sphere_re100（并入大域路线）——按两案 README 晋级路径执行，索引统计同步",
     VERIF, DOC, False, None, None),
    ("permeability3d-promotion", "permeability3d",
     "晋级 φ=0.343 档归档 PR：按该案 README 晋级路径（双规则 PASS 单调降），"
     "索引统计同步",
     VERIF, DOC, False, None, None),
    ("annulus-clause", "poiseuille_3d_annulus",
     "条款修订 PR（零计算）：δ_i spread 0.05 预注册界按内壁楼梯曲率 O(1/R) "
     "真实漂移的证据修订；或按 README 备选补 R_o=160 档复核（选补跑须先冻结 prereg）",
     VERIF, DOC, False, None, None),
    ("taylor-aris-verdict", "taylor_aris_dispersion",
     "口径终裁：单调门 err=负 O(1/H²)+正地板 H≈50 变号——口径重锁或正式判死，"
     "把裁定写成终态条目并更新索引",
     VERIF, DOC, False, None, None),
    ("forchheimer-verdict", "forchheimer",
     "参考终裁：锁弱惯性精确参考后重判，或按 README 菜单关闭该案（终裁文档）",
     VERIF, DOC, False, None, None),
    ("naca-0012-refrelock", "naca_0012",
     "参考重锁：图文自不一致已判受限 → 按该案 README 二选一路径重锁参考，"
     "然后全档重判（秒级/档）",
     VERIF, DOC, False, None, None),
    ("couette-3d-verdict", "couette_3d",
     "稳态口径裁定：跑 fp64 四档踪迹（≤0.15 GPU·h）重锁稳态判据或噪声带，"
     "按结果出晋级 PR 或终裁",
     IMPL, DOC, True, None, None),
    ("collision-kernels-rejudge", "collision_kernels_3d_tg",
     "修复后核重判：KBC 黏度修复后 patched 阶梯侧证据已在，按修复后核正式重判，"
     "出终态（晋级或终裁）",
     IMPL, DOC, True, None, None),
    ("stokes-second-prereg", "stokes_second_problem",
     "从骨架到闭环：Phase-0 停批 → 冻结 prereg（H∈{40,80}，≤0.1 GPU·h）→ 跑 → "
     "按四门判",
     IMPL, DOC, True, None, None),
    # ── B1：CPU 日内 / 中小 GPU ──
    ("blasius-extension", "blasius_flat_plate",
     "延长板长放大有效 Re_x（保持直接口径）重跑；旧档 FD 权重 bug 已修",
     IMPL, COMPUTE, False, None, None),
    ("backward-step-l4", "backward_step",
     "参考裁决 + L4 细档（外推证据在案）或改判 Re=150/200 点（CPU 日内）",
     IMPL, COMPUTE, False, None, None),
    ("cavity-natural-conv-rerun", "cavity_natural_convection",
     "库 thermal 壁 BC 已修复（2D 六案已 verified）→ 对齐 thermal_cavity 按"
     "新库重跑预注册",
     IMPL, COMPUTE, True, None, None),
    ("ellipse-highgrades", "poiseuille_3d_ellipse",
     "加 a=40/80 高档（两档全 ≤3% 即达标），先冻结 prereg",
     IMPL, COMPUTE, True, None, None),
    ("bstep-3d-remake", "bstep_3d",
     "按冻结协议重制 re200/re400（记录损坏两档）或新工况预注册",
     IMPL, COMPUTE, True, None, None),
    ("sphere-re200-launch", "sphere_re200",
     "launch 段：按冻结协议（BFL+wet-node+域形条款）冻结 prereg → nohup 分离"
     "启动（仅 GPU5）→ 写 GPU5_BUSY 与 DONE.json 完成清单（≈10-20 GPU·h）",
     IMPL, DOC, True,
     "prereg 冻结件 + 分离运行清单（PID/GPU5_BUSY/DONE.json 路径与预计时长）", None),
    ("sphere-re200-harvest", "sphere_re200",
     "harvest 段：验收 DONE.json 对应运行的四门判据 → 晋级 PR 或终裁 → "
     "删除 GPU5_BUSY",
     VERIF, DOC, False, None, f"{RUNS}/sphere_re200/DONE.json"),
    ("cylinder-w11a2-launch", "cylinder_re40_st",
     "launch 段：W11-A2 扩上游 up≥40D 六档重跑（≈25-35 GPU·h）冻结 prereg → "
     "分离启动 → GPU5_BUSY/DONE.json",
     IMPL, DOC, True,
     "prereg 冻结件 + 分离运行清单（PID/GPU5_BUSY/DONE.json 路径与预计时长）", None),
    ("cylinder-w11a2-harvest", "cylinder_re40_st",
     "harvest 段：验收六档判据 → 晋级 PR 或终裁 → 删除 GPU5_BUSY",
     VERIF, COMPUTE, False, None, f"{RUNS}/cylinder_re40_st/DONE.json"),
    ("rayleigh-benard-launch", "rayleigh_benard",
     "launch 段：γ_ref 重锁格子算子特征值（新 prereg，~8 GPU·h/轮）→ 重锁 + "
     "首轮分离启动 → GPU5_BUSY/DONE.json",
     IMPL, DOC, True,
     "γ_ref 重锁证据 + prereg 冻结件 + 分离运行清单", None),
    ("rayleigh-benard-harvest", "rayleigh_benard",
     "harvest 段：验收首轮 → 晋级 PR 或终裁+下一轮建议 → 删除 GPU5_BUSY",
     VERIF, DOC, False, None, f"{RUNS}/rayleigh_benard/DONE.json"),
    # ── B2：结构性 / 引擎级长线 ──
    ("wb2-sphere-re150", "sphere_re150",
     "Wave-12 终裁唯一 letter PASS 待晋案：执行可选强化（D80 双网格+稳态延长）"
     "后出晋级归档 PR；强化若暴露问题则按证据终裁。该案档案在 "
     "suboff_sail 战役 prereg 与 PR #274 记录，先在仓库内定位其工件再动手",
     IMPL, COMPUTE, True, None, None),
    ("hadamard-reopen", "hadamard_rybczynski_droplet",
     "M1 相面重建已落库 → 重开 λ 阶梯：先小档预注册探路，NaN/正性失败即回终裁",
     IMPL, COMPUTE, True, None, None),
    ("two-phase-poiseuille-verdict", "two_phase_poiseuille",
     "SC 复活需先修库（稳定性+界面条带化）；注意问题类已由 "
     "verified/poiseuille_two_phase_cg 关闭——先评估「重复关闭终裁」vs「修库重开」"
     "并按证据执行其一",
     VERIF, COMPUTE, True, None, None),
    ("dam-break-sc-levers", "dam_break_sc",
     "未试杠杆逐一定源：压力张量 SC 力 / guest-host 耦合 / 高密度比 EOS"
     "（单一变量、先冻结 prereg 再试）",
     IMPL, COMPUTE, True, None, None),
    ("dit-turbulence-dsmag", "dit_turbulence",
     "三菜单评估（dynamic Smagorinsky / 系综 / 更长运行）+ 首选项执行",
     IMPL, COMPUTE, True, None, None),
    ("permeability-fibrous-menu", "permeability_fibrous",
     "菜单执行：cut-cell 自产表 / 门放宽 1% / 购精确表 / 维持 FAIL + "
     "sq 0.30-0.60 现成候选档",
     IMPL, COMPUTE, True, None, None),
    ("channel-turb-3d", "channel_turb",
     "3D 槽道新案例（高预算）：设计 prereg（转捩路径 Re≥5772）+ 首档探路；"
     "预算内探不完则分离运行交收割",
     IMPL, COMPUTE, True, None, None),
    ("capillary-washburn-libfix", "capillary_invasion_washburn",
     "修库优先：质量守恒 / σ 可测 / 抗负分布；修后新 prereg 重开（库修复 PR "
     "单独成 PR）",
     IMPL, COMPUTE, True, None, None),
    ("rayleigh-taylor-design", "rayleigh_taylor",
     "⛔ 引擎级：高密度比伪势引擎（或晋级引擎重设计）设计文档+预算评估；"
     "本轮不出大规模计算",
     IMPL, DOC, False, "引擎设计文档（含预算评估与重开条件）", None),
    ("dam-break-3d-mm-design", "dam_break_3d_mm",
     "引擎级五连根因在案：晋级引擎重设计（权重触发）设计文档+预算评估；"
     "本轮不出大规模计算",
     IMPL, DOC, False, "引擎设计文档（含预算评估与重开条件）", None),
]

GOAL_TMPL = """BENCH-CLOSE:{key} — TensorLBM benchmark 闭环：{case}（pending 表状态：{status}）

任务：把 benchmarks/pending/{case}/ 推进到闭环终态。指定动作：{action}

pending 总表该行原文（直读自 origin/main README，含卡点与晋级动作）：
  卡点：{blocker}
  晋级动作：{promo}
  预算：{budget_row}

必读（开工前，按序）：
1. /srv/tensor/shared/tensor_eco/agent_work/benchmark_closure/README.md（本线玩法手册）
2. 你 worktree 内 benchmarks/pending/{case}/README.md（卡点/晋级路径/PROVENANCE 全在
   里面；判决数字一律从工件直读，零手抄）

闭环标准（严格四门，与 verified/ 一致）：共性模块入口（src/tensorlbm 库路径）；
直接观测量对预注册锁定参考 ≤3%；≥2 档网格严格单调收敛；参考先锁定后跑；
修正/还原变换不算直接模拟。

闭环的两种合法终态（都算完成；禁止无证据改判）：
A. 晋级：补齐四门 → 归档 PR（pending/{case} → verified/{case}，同步索引与统计）；
B. 终裁：维持 FAIL/判死/口径裁定 → 更新该案 README（根因证据链+修订后的晋级路径）。

资源纪律：Python 用 /srv/tensor/shared/venvs/tensorlbm/bin/python；GPU 只准 GPU5
（CUDA_VISIBLE_DEVICES=5），GPU0-4 禁碰；新账本 /srv/tensor/shared/runs/bm_close/{case}/
（prereg append-only，先冻结后跑）；计算超墙钟时 nohup 分离运行 + GPU5_BUSY +
DONE.json 完成清单（手册有完整流程）；kill 仅精确 PID。

交付（全部满足才算完成）：
1. {deliverables}
2. 机器门禁 gates.json：写 <worktree>/benchmarks/{{verified|pending}}/{case}/gates.json
   ——每个判决数字（晋级与终裁都要，失败的数字也要）声明为 值-文件-键 三元组，
   schema 与复算规则见手册附录；派发器 push 前用 gates_check.py 逐一重取工件复算，
   未过不推（三元组对不上=手抄=退回）。
3. commit 到本 mission 分支（中文提交信息，聚焦该案）；run_tests 全绿后
   request_acceptance（branch=agent/m<id>，summary 给证据摘要，evidence_files
   填判据工件路径）。
4. 论文级平台报告：写 <worktree>/report_{case}.md + 同目录 fig_*.png（物理问题
   表述+示意、结果云图/曲线、判据表、审计溯源、局限与缓解；纯文档裁定类可豁免
   云图但判据表+审计必须齐全；数字全部来自工件）。派发线代你发布并自动 push+开 PR；
   merge 留 owner。

红线：不 git push；不动 verified/ 既有案例与判定；不动 marine sweep 五案与
aircraft_icing_rg15_ipw2（各自战役线）；kill 仅精确 PID；不碰 GPU0-4 与跳板机
端口；yueyue 的 /tmp 不动。预算纪律：交付优先，预算过半未开写交付物即停新诊断
先落交付。"""


EXTRA_COVERS = {"sphere_re100_mem": ["sphere_re100"]}   # 并入大域路线一并处理


def table_rows() -> list[dict]:
    out = subprocess.run(
        ["git", "-C", LBM, "show", "origin/main:benchmarks/pending/README.md"],
        capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        m = re.match(r"^\|\s*\[([a-z0-9_]+)\]\(\1/\)\s*\|(.+)\|\s*$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(2).split("|")]
        if len(cells) < 4:
            continue
        rows.append({"case": m.group(1), "status": cells[0],
                     "blocker": cells[1], "promo": cells[2], "budget": cells[3]})
    return rows


def main() -> int:
    rows = table_rows()
    by_case = {r["case"]: r for r in rows}
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "goals").mkdir(exist_ok=True)

    sha = subprocess.run(["git", "-C", LBM, "rev-parse", "origin/main"],
                         capture_output=True, text=True, check=True).stdout.strip()

    items, covered = [], set()
    for (key, case, action, model, budget, gpu, deliverables, wait_for) in A:
        r = by_case.get(case)
        if r is None and key != "wb2-sphere-re150":
            print(f"FATAL: 注解案例 {case} 不在 pending 总表", file=sys.stderr)
            return 1
        if r:
            covered.add(case)
        covered.update(EXTRA_COVERS.get(case, []))
        d = deliverables or "闭环产物（晋级归档 PR 或终裁文档）"
        goal = GOAL_TMPL.format(
            key=key, case=case, action=action,
            status=r["status"] if r else "Wave-12 终裁待晋（不入 pending 总表）",
            blocker=r["blocker"] if r else "见该案档案与战役终裁记录",
            promo=r["promo"] if r else "可选强化（D80 双网格+稳态延长）后晋级",
            budget_row=r["budget"] if r else "强化档约 10-20 GPU·h",
            deliverables=d)
        (WORK / "goals" / f"{key}.txt").write_text(goal, encoding="utf-8")
        items.append({"key": key, "case": case, "model": model,
                      "budget": budget, "gpu": gpu, "wait_for": wait_for,
                      "goal_file": f"goals/{key}.txt"})

    # 覆盖检查：总表每行必须被至少一个条目覆盖（防漏案）
    missing = [r["case"] for r in rows if r["case"] not in covered]
    for case in missing:                      # 兜底条目：默认动作，不静默丢案
        key = case.replace("_", "-") + "-auto"
        r = by_case[case]
        goal = GOAL_TMPL.format(
            key=key, case=case, action="按该案 README 晋级路径执行闭环",
            status=r["status"], blocker=r["blocker"], promo=r["promo"],
            budget_row=r["budget"], deliverables="闭环产物（晋级归档 PR 或终裁文档）")
        (WORK / "goals" / f"{key}.txt").write_text(goal, encoding="utf-8")
        items.append({"key": key, "case": case, "model": IMPL,
                      "budget": COMPUTE, "gpu": True, "wait_for": None,
                      "goal_file": f"goals/{key}.txt"})
        print(f"NOTE: 总表行 {case} 无注解 → 追加兜底条目 {key}")

    backlog = {"base_ref": sha, "repo": "TensorLBM",
               "memory_key": "benchmark-closure",
               "scope_note": "pending 总表全案 + WB2；marine 五案与 icing 不在范围",
               "items": items}
    (WORK / "backlog.json").write_text(
        json.dumps(backlog, ensure_ascii=False, indent=1), encoding="utf-8")
    if not (WORK / "state.json").exists():
        (WORK / "state.json").write_text(
            json.dumps({"items": {}}, ensure_ascii=False), encoding="utf-8")
    print(f"OK: {len(items)} 条目（总表 {len(rows)} 行，未覆盖 {len(missing)} 行 "
          f"已兜底）；base_ref={sha[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
