#!/usr/bin/env python3
"""2026-10-20 质量门升级落地：backlog 插图档补强条目 + 未派发条目升 paper 档。

幂等：重复跑不重复插入（按 key 查重）、目标文件按标记查重。
"""
from __future__ import annotations

import json
from pathlib import Path

W = Path("/srv/tensor/shared/tensor_eco/agent_work/benchmark_closure")
NEW_BASE = "c86554df5599910fe601ccb674abbe14571c580d"

FIGDOC_BLOCK = """

图档与细节交付（owner 2026-10-20 质量门升级，本条目按 paper 六门验收）：
- 案例细节 README（晋级=verified/<case>/README.md；终裁=pending/<case>/README.md）
  独立成篇：物理问题与几何、控制参数、python 运行命令、判据与参考（锚）、
  PROVENANCE 块。门禁机械五查：≥1200B、≥2 个 ## 小节、含 PROVENANCE、
  含 python、含 参考/锚 字样。
- 图件 <case_dir>/figures/*.png + figures.json manifest：
  {"figures":[{"file":"figures/x.png","kind":"field|curve|diagram",
  "caption":"≥8字","source":"数据工件+plot脚本"}],"field_waiver":""}
  硬门：≥1 曲线（curve）+ ≥1 云图（field；无空间场量的案填 field_waiver
  豁免理由）；有云图时 ≥2 图；真 PNG ≥6KB；plot 脚本入库；图从工件生成零手抄。
- gates.json 增加两门声明 {"gate":"case_readme","pass":true,"evidence":[]} 与
  {"gate":"figures","pass":true,"evidence":[]}——这两门由 gates_check 读文件
  本体机械检查，不信声明。派发器 push 前 --profile paper 复算，未过不推。
- report_<case>.md 嵌入上述图件与案例细节（平台报告自动带案例目录 figures/ 图）。
"""

RETRO_TMPL = """BENCH-CLOSE:{key} — TensorLBM benchmark 图档补强：{case}（已入库，缺云图/曲线/案例细节）

背景：owner 2026-10-20 质量门升级——已闭环案例缺三样：案例细节介绍（README 深度）、
云图（场量分布）、曲线（收敛/误差/剖面）。本 mission 为 {case} 补齐，并按新两门
（case_readme + figures，profile=paper）重造 gates.json。派发器 push 前用
gates_check.py --profile paper 六门机械复算，未过不推。

任务：
1. 案例细节 README：{readme_task}
   门禁机械五查（≥1200B、≥2 个 ## 小节、含 PROVENANCE、含 python 运行命令、
   含 参考/锚 字样）。
2. 图件 <case_dir>/figures/*.png + figures.json manifest（schema 见手册附录）：
   - 云图：{field_task}
   - 曲线：{curve_task}
   - 可小网格重跑 dump 场数据后绘图（plot 脚本入库，图从工件生成，零手抄；
     真实跑放 /srv/tensor/shared/runs/bm_close/{case}/figdump/）；每张 PNG ≥6KB。
   {waiver_line}
3. gates.json 重造：既有四门数值三元组**原样保留、不得改动任何判决数字**；
   新增两门声明 case_readme 与 figures（pass=true，evidence 可空——门禁机械
   检查文件本体，不信声明）。
4. report_{case}.md 更新：嵌入新图件与案例细节（平台报告自动带 figures/ 图）。

必读（开工前）：
1. /srv/tensor/shared/tensor_eco/agent_work/benchmark_closure/README.md（本线玩法手册，
   含 figures.json schema 与两新门说明）
2. 你 worktree 内该案既有 README/result.json/NOTES（判决数字一律直读工件，零手抄）

资源纪律：Python 用 /srv/tensor/shared/venvs/tensorlbm/bin/python；本档 CPU 足够（不占
GPU5）；kill 仅精确 PID。
交付：commit 到本 mission 分支（中文提交信息）→ run_tests 全绿 →
request_acceptance（branch=agent/m<id>，summary 给证据摘要，evidence_files 填
figures.json/README/gates.json 路径）。派发线代你发布报告并自动 push+开 PR；
merge 留 owner。
红线：不 git push；不动本案判定数字与既有四门数值；不动其他案例；不动 verified/
其他案例与 marine 五案/icing；kill 仅精确 PID。
"""

RETROS = [
    dict(key="figdoc-cavity-3d-full", case="cavity_3d_full",
         readme_task="verified/cavity_3d_full/ 目前无 README——以 pending/cavity_3d_full/"
                     "README.md 为底，写成独立成篇的案例介绍（3D 顶盖驱动方腔流物理、"
                     "几何与控制参数、运行命令、判据与 Ku 1987 参考、PROVENANCE）。",
         field_task="中剖面速度幅值/涡结构云图（N64 小网格重跑 dump 场即可）。",
         curve_task="中心线速度剖面 vs 参考曲线 + 误差-网格阶梯曲线"
                    "（1.338%→0.259% 收敛链，数值直读工件）。",
         waiver_line=""),
    dict(key="figdoc-sphere-re100-mem", case="sphere_re100_mem",
         readme_task="verified/sphere_re100_mem/README.md 偏薄——补成独立成篇"
                     "（绕球流动 Re=100 物理与尾流、Schiller–Naumann 锚、内存受限"
                     "档位设计 rationale、运行命令、PROVENANCE）。",
         field_task="球中剖面速度幅值+尾流结构云图（小域重跑 dump）。",
         curve_task="Cd(t) 收敛史曲线 + 档位误差曲线（2.7708%/2.5401%，直读工件）。",
         waiver_line=""),
    dict(key="figdoc-couette-3d", case="couette_3d",
         readme_task="verified/couette_3d/README.md 已有深度——校核五查项齐备即可"
                     "（物理问题/运行命令/参考锚/PROVENANCE 已在，补缺项）。",
         field_task="中剖面 u(y) 速度场云图（线性剖面可视化，H=32 小档重跑 dump）。",
         curve_task="u(y) 剖面 vs 解析线性解曲线（fp64 四档）+ l2 误差地板曲线。",
         waiver_line=""),
    dict(key="figdoc-annulus", case="poiseuille_3d_annulus",
         readme_task="verified/poiseuille_3d_annulus/README.md 校核补深（环隙螺旋流"
                     "物理、Ro 档设计、运行命令、参考锚、PROVENANCE 五查齐备）。",
         field_task="环隙横截面角向速度/二次流结构云图（Ro20 小档重跑 dump）。",
         curve_task="Ro 档误差曲线 + 剖面 vs 解析解曲线（数值直读工件）。",
         waiver_line=""),
    dict(key="figdoc-permeability3d", case="permeability3d",
         readme_task="verified/permeability3d/README.md 校核补深（Darcy 渗透物理、"
                     "φ 阶梯设计、运行命令、Kozeny–Carman 参考锚、PROVENANCE 五查）。",
         field_task="孔隙结构切片（固体几何 φ=0.343）+ 渗透速度场云图（N64 档复用"
                    "既有 case json 重跑 dump）。",
         curve_task="k vs N 收敛曲线（φ=0.343 四档 4.6253%→1.4469%，直读工件）。",
         waiver_line=""),
    dict(key="figdoc-collision-kernels", case="collision_kernels_3d_tg",
         readme_task="collision_kernels_3d_tg 为 terminal 案——pending/ 侧 README 校核"
                     "补深（碰撞核弛豫谱验证物理、KBC/D3Q27 判决链、PROVENANCE）。",
         field_task="本案为碰撞核弛豫谱验证，无空间场量——figures.json 填 field_waiver"
                    "豁免理由（\"碰撞核弛豫谱验证无空间场量，图件为弛豫谱曲线\"）。",
         curve_task="gamma(tau) 弛豫谱曲线（KBC 过 + 五 D3Q27 核 FAIL 同图对照，"
                    "数值直读工件）。",
         waiver_line="注意：terminal 案在 paper 档同样要过 case_readme+figures 两门"
                    "（曲线必须，云图走豁免）。"),
]


def main() -> int:
    state = json.loads((W / "state.json").read_text(encoding="utf-8"))
    bl = json.loads((W / "backlog.json").read_text(encoding="utf-8"))
    have = state["items"]
    changed = []

    # 1) 未派发/待重派条目升 paper 档 + goal 文件补图档交付块
    for it in bl["items"]:
        k = it["key"]
        rec = have.get(k)
        if rec is not None and rec.get("mission"):
            continue          # 在飞——classic 档不追溯，落定后另行补强
        if not it.get("paper"):
            it["paper"] = True
            changed.append(f"paper+ {k}")
        g = W / "goals" / f"{k}.txt"
        if g.exists() and "质量门升级" not in g.read_text(encoding="utf-8"):
            g.write_text(g.read_text(encoding="utf-8") + FIGDOC_BLOCK,
                         encoding="utf-8")
            changed.append(f"goal-append {k}")

    # 2) 图档补强条目插到队首（幂等按 key 查重）
    keys = {i["key"] for i in bl["items"]}
    retros = []
    for r in RETROS:
        if r["key"] in keys:
            continue
        (W / "goals" / f"{r['key']}.txt").write_text(
            RETRO_TMPL.format(**r), encoding="utf-8")
        retros.append({"key": r["key"], "case": r["case"],
                       "model": "qwen3.7-plus", "budget": "200,21600,8000000",
                       "gpu": False, "wait_for": None,
                       "goal_file": f"goals/{r['key']}.txt", "paper": True})
        changed.append(f"retro {r['key']}")
    bl["items"] = retros + bl["items"]

    # 3) base_ref 前进到当前 main（含 #367-#373 + Triton 修复）
    if bl.get("base_ref") != NEW_BASE:
        changed.append(f"base_ref {bl.get('base_ref', '')[:8]} → {NEW_BASE[:8]}")
        bl["base_ref"] = NEW_BASE

    (W / "backlog.json").write_text(
        json.dumps(bl, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"items total: {len(bl['items'])}")
    for c in changed:
        print(" ", c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
