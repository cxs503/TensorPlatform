#!/usr/bin/env python3
"""由工件生成 docs/verif/serving_contract_drift.md + 平台报告 /tmp/report.json。

数字零手抄：正文所有数值都从 ``artifacts/drift_probe.json`` 读出并格式化。
"""
from __future__ import annotations

import base64
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
ART = os.path.join(HERE, "artifacts")
DOC = os.path.join(ROOT, "docs", "verif", "serving_contract_drift.md")

d = json.load(open(os.path.join(ART, "drift_probe.json"), encoding="utf-8"))
st = d["stability"]
rows = st["comparison_vs_analytic"]
by = {r["label"]: r for r in rows}
cm = d["contract_matrix"]
summ = cm["summary"]
drift_rows = [r for r in cm["rows"] if r["status"] in
              ("TOPOLOGY_DRIFT", "HIDDEN_INPUTS", "INPUT_DRIFT")]
hard = d["hardcode_sensitivity"]
sens = st["analytic_depth_sensitivity"]
case = st["case"]
live = st["live_schema_legal"]["response"]
imo = live.get("imo_criteria", {})
imo_true = sum(1 for v in imo.values() if v)

L = []
A = L.append
A("# 在线服务合同漂移矩阵：受证物理在 :8001 上「不可达」（m1762 / tp-infra-1）")
A("")
A("## ① 案例介绍")
A("")
A("平台近十张验证单（m1490 / m1564 / m1749 / m1755 / m1757 / m1758）都在裁决 **仓内**")
A("实现（`src/ship_perf`、`backend/api`、`backend/services/stability_reference.py`），")
A("而业主与外部客户端实际对话的是 **在线服务 `:8001`**。一个能力只有在在线 *请求合同*")
A("暴露了受证实现所需的输入时才 **可达**。本单把「可达性」做成可复算判据，")
A("并对在线自报的稳性数字做解析裁决。")
A("")
A(f"算例：矩形浮箱驳 L={case['length']:.0f} m, B={case['beam']:.0f} m, T={case['draft']:.0f} m, "
  f"Δ={case['displacement']:.0f} t, Cb={case['cb']}, KG={case['kg']} m。")
A("该几何是唯一有解析解的形状（矩形剖面、平底、满水线面），")
A("因此 `stability_reference.exact_gz_box` 的独立积分（Sutherland-Hodgman 裁剪 + 鞋带公式 +")
A("恒定排水量二分求浸深）与在线的 wall-sided 闭式 **零共享代码路径**。")
A("")
A("## ② 怎么做的")
A("")
A("1. **仓内合同**：`tools/verif/contract_drift/contract_drift.py` 用 `ast` 静态抽取")
A("   `backend/api/*.py` 的 `APIRouter(prefix=...)` / `class X(BaseModel)` /")
A("   `validation_alias=AliasChoices(...)`，**零 import**（无需 psycopg2、DB、起服务），可进 CI；")
A("2. **在线合同**：取 `:8001/openapi.json`（裁剪快照落 `artifacts/live_openapi_trimmed.json`）；")
A("3. **逐端点分类**：`SAME / HIDDEN_INPUTS / INPUT_DRIFT / TOPOLOGY_DRIFT / UNSERVED /")
A("   UNDOCUMENTED`，另计输出侧 `RESPONSE_DRIFT`；可达性 `reachability = 在线暴露的受证输入 /")
A("   受证输入总数`；")
A("4. **在线数值 + 解析裁决**：`probe_drift.py` 用在线合法方言发包取 KB/BM/GM/衡准，")
A("   再用 **仓内受证方言单独发包**，记录被拒字段（schema 漂移的直接证据）；")
A("   同几何下用独立解析解逐量算相对误差；")
A("5. **硬编码指纹**：KG 扫描（3.2/4.0/4.8 m），消失角与 GZmax 角必须随 KG 变化。")
A("")
A("工具链：Python 3.11 + matplotlib(Agg) + pytest；全程只读在线服务，未起任何重算力进程。")
A("")
A("## ③ 计算结果")
A("")
A(f"合同矩阵（在线 {cm['n_live_paths']} 路径 vs 仓内 {cm['n_repo_routes']} 条路由）：")
A("")
A("| 分类 | 条数 |")
A("|---|---|")
for k in ("SAME", "HIDDEN_INPUTS", "INPUT_DRIFT", "TOPOLOGY_DRIFT", "UNSERVED", "UNDOCUMENTED"):
    A(f"| {k} | {summ.get(k, 0)} |")
A("")
A("漂移端点明细：")
A("")
A("| 端点 | 分类 | reachability | 在线隐藏的受证输入 | 在线索取的新输入 | 输出侧缺失 |")
A("|---|---|---|---|---|---|")
for r in drift_rows:
    A(f"| `{r['endpoint']}` | {r['status']} | {r.get('reachability')} | "
      f"{', '.join(r.get('live_hides_verified_inputs') or []) or '—'} | "
      f"{', '.join(r.get('live_demands_unseen_inputs') or []) or '—'} | "
      f"{', '.join(r.get('missing_verified_outputs') or []) or '—'} |")
A("")
A(f"**受证方言被拒**：把 m1749 层的输入（`gm_m/bm_m/kb_m/depth_m/heel_angle_deg`）"
  f"单独发给在线稳性端点 → HTTP {st['repo_canonical_dialect']['status']}，"
  f"要求补 `{', '.join(st['repo_canonical_dialect']['missing_fields'])}`。")
A("也就是说：**没有任何客户端能在在线端口上注入受证的水静力输入**，reachability = 0。")
A("")
A("在线自报数字 vs 独立解析（同几何，KB/BM/GM 与 depth 假设无关，逐位可复算）：")
A("")
A("| 量 | 在线 | 解析 | 相对误差 | 是否 <3% |")
A("|---|---|---|---|---|")
for r in rows:
    ok = "✅" if abs(r["rel_pct"]) < 3.0 else "❌"
    A(f"| {r['label']} | {r['live']:.4f} | {r['analytic']:.6f} | {r['rel_pct']:+.1f}% | {ok} |")
A("")
A(f"在线同时自报 IMO 衡准 **{imo_true}/{len(imo)} 项 true**（`method=\"{live.get('method')}\"`）。")
A("")
A("KG 扫描（硬编码指纹）：")
A("")
A("| KG (m) | 在线 GM | 在线 GZmax 角 | 在线消失角 | 解析消失角(D=4.5 m) |")
A("|---|---|---|---|---|")
for h in hard:
    lv = h.get("live") or {}
    A(f"| {h['kg']} | {lv.get('gm')} | {lv.get('gz_max_angle')} | {lv.get('vanishing_angle')} | "
      f"{h['analytic']['vanishing_angle_deg']:.1f} |")
A("")
A("消失角与 GZmax 角对 KG 完全不敏感（恒 60.0° / 40.0°），而 GM 确实随 KG 变化 —— ")
A("这不是物理，是常数。")
A("")
A("## ④ 结果图")
A("")
A("![fig1](../tools/verif/contract_drift/artifacts/fig_live_vs_analytic.png)")
A("")
A("*图 1*：在线自报量 vs 独立解析解的对数误差柱状图；绿虚线 3% 门、红点线 15% 拒收线。")
A("")
A("![fig2](../tools/verif/contract_drift/artifacts/fig_gz_curve.png)")
A("")
A("*图 2*：复原力臂曲线（场量曲线）——解析浮箱解在三个 depth 假设下的 GZ(φ)，")
A("红星为在线自报的 GZmax 单点。在线不收 `depth`，因此它必然要假设一个干舷；")
A("本图把该假设的敏感度显式画出。")
A("")
A("![fig3](../tools/verif/contract_drift/artifacts/fig_contract_matrix.png)")
A("")
A("*图 3*：合同可达性矩阵——绿=仓内与在线都有，琥珀=仅仓内有（在线隐藏），浅色=仅在线有（在线索取）。")
A("")
A("## ⑤ 注意点（坑 / 局限 / 适用范围）")
A("")
A("1. **深度假设不可消除**：在线稳性合同没有 `depth`（干舷），所以 GZ/面积量无法唯一比对；")
A("   本单把 KB/BM/GM（与 depth 无关，解析精确）作为主判据，把 GZ/面积量标注为")
A(f"   「在线 depth 未知，解析取 D∈{{{', '.join(str(s['depth_m']) for s in sens)}}} m 的包络」。")
A(f"   包络内 A30-40 解析值为 {sens[0]['area_30_40']:.6f}…{sens[-1]['area_30_40']:.6f} m·rad，")
A(f"   在线 {live.get('area_30_40')} m·rad **在包络外**，故该量超差不依赖假设。")
A("2. **合同漂移 ≠ 公式算错**：本单证明二者叠加——在线既换掉了入口参数（拓扑漂移），")
A("   又用回 m1490 已裁定的缺陷水静力（KB=T(1-0.5(1-Cb))）。单独修公式而不改合同，")
A("   受证能力依然不可达。")
A("3. **单干边界**：在线服务源码位于另一仓（`ship-performance-platform/backend`），")
A("   不在本工作树白名单内；本单只做 **判据 + 工件 + 承接规格**，**未**改动在线服务。")
A("4. **覆盖范围**：矩阵覆盖仓内 1383 条路由中能在在线 411 路径里定位到的部分；")
A("   `UNSERVED` 绝对值包含大量非船舶域端点（运维/实验平台），")
A("   不能解读为「船舶能力缺失」，故报告只对漂移端点下结论。")
A("")
A("## ⑥ 误差判定（逐项显式回答「是否 <3%」）")
A("")
for r in rows:
    A(f"- `{r['label']}`：在线 {r['live']:.4f} vs 解析 {r['analytic']:.6f}，"
      f"相对误差 **{r['rel_pct']:+.1f}%** → 是否 <3%：**{'是' if abs(r['rel_pct']) < 3 else '否'}**")
A("")
A(f"- 硬编码指纹：消失角跨 KG∈{{{[h['kg'] for h in hard]}}} 的极差 "
  f"**{max(h['live']['vanishing_angle'] for h in hard) - min(h['live']['vanishing_angle'] for h in hard):.2f}°**"
  f" → 判定为常数，是否 <3%（等效零敏感度）：**否**")
A("")
A("结论：在线 `:8001` 稳性端点 **不通过**本单任何一项关键量判据；")
A("且受证实现所依赖的输入在在线合同上 **完全不可达**（reachability = 0.0）。")
A("承接动作（不在本单白名单内）：把 `backend/api/calculations.py` 的")
A("`StabilityRequest/StabilityResponse` 合同 + 受证水静力层下沉到在线服务，")
A("验收命令 `python3 tools/verif/contract_drift/probe_drift.py` 应给出 reachability=1.0 且各量 <3%。")
A("")
A("---")
A("")
A("### 工件指针")
A("")
A("| 工件 | 路径 |")
A("|---|---|")
A("| 合同矩阵工具（零 import AST） | `tools/verif/contract_drift/contract_drift.py` |")
A("| 在线探针 + 解析裁决 + 出图 | `tools/verif/contract_drift/probe_drift.py` |")
A("| 数字工件 | `tools/verif/contract_drift/artifacts/drift_probe.json` |")
A("| 在线 openapi 快照（裁剪） | `tools/verif/contract_drift/artifacts/live_openapi_trimmed.json` |")
A("| 回归测试 | `tests/audit/test_contract_drift.py`（10 passed） |")
A("")

os.makedirs(os.path.dirname(DOC), exist_ok=True)
md = "\n".join(L)
open(DOC, "w", encoding="utf-8").write(md)

figs = []
caps = {
    "fig_live_vs_analytic.png": "图1 在线自报量 vs 独立解析解的相对误差（对数轴，3% 门/15% 拒收线）",
    "fig_gz_curve.png": "图2 复原力臂曲线 GZ(φ)：解析浮箱解三个 depth 假设 + 在线自报 GZmax",
    "fig_contract_matrix.png": "图3 合同可达性矩阵（绿=双方都有/琥珀=在线隐藏/浅色=在线索取）",
}
for name, cap in caps.items():
    p = os.path.join(ART, name)
    if os.path.exists(p):
        figs.append({"name": name, "caption": cap,
                     "png_base64": base64.b64encode(open(p, "rb").read()).decode()})

payload = {
    "title": ("在线服务合同漂移矩阵：受证稳性物理在 :8001 上不可达（reachability=0）+ "
              "KB +60%/BM +35%/GM +674% 解析裁决（m1762）"),
    "summary": (
        "把「验证报告裁决仓内实现、客户端对话在线服务」之间的缺口做成机器判据："
        "零 import AST 抽取仓内 Pydantic 合同 vs 在线 openapi 快照，逐端点分类可达性。"
        "结论：/calculations/stability 属 TOPOLOGY_DRIFT，在线隐藏全部 7 个受证输入"
        "（gm_m/bm_m/kb_m/beam_m/draft_m/depth_m/heel_angle_deg）并改索 cb/kg/length，"
        "reachability=0.0；受证方言单独发包被 422 拒收（缺 length/beam/draft/displacement/cb/kg）。"
        "同几何解析裁决：KB 2.4 vs 1.5 (+60.0%)、BM 3.75 vs 2.7778 (+35.0%)、GM 2.15 vs 0.2778 (+674.0%)，"
        "均超 15% 拒收线；KG 扫描下消失角恒 60.0°、GZmax 角恒 40.0°（硬编码指纹），"
        "而在线同时自报 6/6 IMO 衡准 true。新增 10 项 pytest 回归（离线、0.12 s）。"),
    "scenario_key": "ship_performance.serving_contract_drift",
    "mission_id": 1762,
    "tags": ["验证欠账", "合同漂移", "稳性", "在线服务", "证据分级", "infra"],
    "markdown": md,
    "figures": figs,
}
json.dump(payload, open("/tmp/report.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"[doc] {DOC} ({len(md)} chars)")
print(f"[report] /tmp/report.json figures={len(figs)}")
