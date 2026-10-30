import json, sys
sys.path.insert(0, ".")
from tphttp import req
tok = open("token.txt").read().strip()

BUDGET = {"max_tokens": 3000000, "max_tool_calls": 80, "max_wall_seconds": 7200}

def sweep_goal(case, extra):
    base = f"""SDAA 验证案例扫荡——案例 {case}（TensorLBM benchmarks/verified/{case}）。步骤：
1) 读该案例目录的 README/运行入口，弄清其既有判据口径（这些案例在 NVIDIA 侧已有成熟判据，按原口径）。
2) 写独立运行脚本 scripts/sdaa_sweep_{case}.py（不改业务代码）：计算张量放 'sdaa' 设备；若入口依赖 triton，用 TRITON_INTERPRET=1 并如实记录绕行。网格/步数按案例文档推荐值或可行较小档，墙钟 ≤20 分钟。
3) 运行并把机器输出存 artifacts/sdaa_{case}.json：设备证据行（torch.sdaa.get_device_name(0) 输出）、判据数值、逐条与门的比对 PASS/FAIL。
4) 交付 docs/sdaa_sweep/{case}.md：案例/配置/结果表/判据逐条 PASS/FAIL/墙钟/绕行记录。
判据：artifacts JSON 存在且含设备证据行；判据数值全部脚本算出零手抄。
honest-FAIL：任何判据不过或跑不通，如实记 FAIL 与原因；禁止降判据、禁止用 CPU 结果冒充 sdaa。
收尾：完成或确认无法完成时，必须调用 finish 提交总结（上一单即因未调用 finish 被判 failed，本单务必避免）。"""
    return base + "\n" + extra

g1 = sweep_goal("cavity_natural_convection",
    "【诊断+修复绕行】失败证据：前序 m16（同案例）status=failed，result headline='未完成：模型未调用 finish'（model=MiniMax-M2.5-AWQ，60 次工具调用后未收尾），该案例至今无判据判定。诊断方向：先确认案例入口在 SDAA 上是否可跑通（设备证据行必须真实来自 torch.sdaa.get_device_name(0)）；若跑不通，如实记录具体错误（OOM/超时/kernel 错误/依赖缺失）与设备证据，记 honest-FAIL；若判据不过，如实记 FAIL 与原因，禁止降判据。")

g2 = sweep_goal("laplace_droplet",
    "【诊断+修复绕行】失败证据：前序 m25（同案例）status=failed，result headline='未完成：模型未调用 finish'（model=Qwen3.8-27B，13 次工具调用后未收尾），该案例至今无判据判定。诊断方向：先确认案例入口在 SDAA 上是否可跑通（设备证据行必须真实来自 torch.sdaa.get_device_name(0)）；若跑不通，如实记录具体错误（OOM/超时/kernel 错误/依赖缺失）与设备证据，记 honest-FAIL；若判据不过，如实记 FAIL 与原因，禁止降判据。")

g3 = f"""SDAA 验证案例扫荡——案例 cylinder_3d（TensorLBM benchmarks/verified/cylinder_3d）【诊断+修复绕行】。
失败证据（来自 m20 完工 result，零手抄）：G1 FAIL（+6.62%）、G2 FAIL（+16.25%，超过 15% 不可接受红线）、G3 FAIL（9.62% span）；mass conservation drift <0.04%（求解器在 SDAA 上稳定）；墙钟 1220s；设备 /dev/tcaicard0。
诊断方向：
1) 同网格同步数在 SDAA 与 CUDA 各跑一次，对比逐判据偏差，判定偏差是 SDAA 设备数值特性还是求解器问题；
2) 定位偏差集中的物理量（Cd/Cl/阻力系数）与时间段（起始瞬态 vs 稳态）；
3) 若入口依赖 triton，用 TRITON_INTERPRET=1 并如实记录绕行。
交付：artifacts/sdaa_cylinder_3d_diag.json（设备证据行+判据数值+逐条 PASS/FAIL）与 docs/sdaa_sweep/cylinder_3d_diag.md（诊断结论：设备问题/求解器问题/步数不足，附证据）。
判据：数值全部脚本算出零手抄；禁止降判据、禁止用 CPU 结果冒充 sdaa；诊断结论为判据真红则如实记 FAIL。
收尾：必须调用 finish 提交总结。"""

missions = [
    ("cavity_natural_convection", g1),
    ("laplace_droplet", g2),
    ("cylinder_3d_diag", g3),
]
out = []
for name, goal in missions:
    body = {"goal": goal, "repo": "TensorLBM", "base_ref": "main",
            "runner": "native", "budget": BUDGET}
    st, d = req("POST", "tensor/missions", body, token=tok)
    print(name, "->", st, json.dumps(d, ensure_ascii=False)[:300])
    out.append({"case": name, "status": st, "resp": d})
json.dump(out, open("dispatch_results.json", "w"), ensure_ascii=False, indent=1)
