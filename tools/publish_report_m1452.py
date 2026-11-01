#!/usr/bin/env python3
"""Publish the mission-1452 report to the Tensor console (mission_id=1452)."""
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ["TENSOR_API_BASE"].rstrip("/")
USER, PASS = os.environ["TENSOR_AGENT_USER"], os.environ["TENSOR_AGENT_PASS"]
ROOT = Path("/srv/tensor/agent/work/m1452")
ART = ROOT / "artifacts" / "resistance_validation_audit"


def post(path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


token = None
for path, body in (("/auth/login", {"username": USER, "password": PASS}),
                   ("/auth/login", {"email": USER, "password": PASS}),
                   ("/auth/token", {"username": USER, "password": PASS})):
    st, res = post(path, body)
    if st == 200 and isinstance(res, dict):
        token = res.get("access_token") or res.get("token")
        if token:
            print("login ok via", path)
            break
    print("login attempt", path, st, str(res)[:120])
if not token:
    raise SystemExit("no token")

base = json.loads((ART / "baseline.json").read_text())
after = json.loads((ART / "after_fix.json").read_text())
live = base["live"]
live_l = {c["invariant"]: c for c in live["checks"]}

markdown = f"""## ① 案例介绍

平台 `calc.resistance` 标为 REAL，其唯一量化证据是 `POST /calculations/benchmark_validate`：
把 Holtrop/ITTC-57 结果与 `backend/data/benchmark_reference.json` 的 12 个标模工况
（DARPA SUBOFF ×4、DTMB-5415 ×4、KVLCC2 ×4，来源 Roddy 1990 / ITTC V&V 2010 / SIMMAN 2008）
比对，误差 ≤15% 判 pass。本单审计这条验证链本身。

| 标模 | L (m) | B/T (m) | Cb | 引用 |
|---|---|---|---|---|
| SUBOFF（回转体） | 4.356 | D=0.508 | 几何反推 | Roddy 1990 |
| DTMB-5415 | 5.720 | 0.759/0.246 | 0.508 | ITTC V&V 2010 |
| KVLCC2 | 320.0 | 58.0/20.8 | 0.8098 | SIMMAN 2008 |

## ② 怎么做的

用物理恒等式而非"看起来合理"判真伪，5 条不变量：
I1 `Fn=V/√(gL)`（±0.01）；I2 `Rt=½ρSV²Ct`（±2%）；I3 隐含湿表面积
S/(L·B)∈[0.6,3.0]、S/(πDL)∈[0.5,1.05]；I4 `Ct/Cf_ITTC57`∈[1.1,3.0]；I5 `Cb` vs `∇/(LBT)`（±0.02）。
工具 `tools/audit_resistance_validation.py`（可 `--live-url` 对照部署服务），
修复 `tools/fix_benchmark_reference_dataset.py`（幂等、留原值轨迹），
物理核心抽到无依赖模块 `backend/services/ship_resistance_core.py`，22 项 pytest 守护。

## ③ 计算结果

**修复前：12/12 工况违背至少一条不变量。**

| 工况族 | 失败项 | 量化 |
|---|---|---|
| SUBOFF ×4 | I2 | 表列 Rt 比自身 Ct 隐含值小 7.1–9.1×（12.5 N vs 92.5 N @3 m/s） |
| DTMB5415 ×4 | I1+I3+I5 | Fn 表列 0.061/0.122/0.244/0.366，应为 0.343/0.687/1.374/2.060；反推 S=0.46–0.57 m²（应≈4.5 m²）；Cb=0.508 与 466.3 kg 互斥 |
| KVLCC2 ×4 | I3 | 反推 S=7025–7589 m²，SIMMAN 值 27194 m²，Rt 偏小≈3.7× |

部署服务（`:8001`）闭式对照：Re {live_l['L1_reynolds']['got']:.0f} vs 期望 {live_l['L1_reynolds']['expected']:.0f}（比值 {live_l['L1_reynolds']['ratio']:.3f}）；
Fn {live_l['L2_froude']['got']:.4f} vs 0.4589；Rt {live_l['L3_force_closure']['rt_reported_n']} N vs 由自身 Ct 得 {live_l['L3_force_closure']['rt_from_ct_n']:.1f} N（力未乘 ½ρSV²）。

**修复后：0/12 违规**，9 条可计分工况（3 条 5415 超包络工况标注排除）。

| 量 | 修复前 | 修复后 |
|---|---|---|
| SUBOFF Rt @3 m/s | 12.5 N | 92.5 N |
| KVLCC2 Rt @7.202 m/s | 550 kN | 2.024 MN |
| 校验器能否给出有效判决 | 否（ct 恒 0） | 是 |

修复机制：保留可信的 Ct 列，Fn 重算，Rt 由文献湿表面积重导（SUBOFF 5.42 m²、KVLCC2 27194 m²），
5415 无文献 S → 置 null 不编造；排水量由 Cb·LBT·ρ 重导（541.5 kg）；同时修复校验器
"读不到 ct 导致全体 100% 假失败"的契约缺陷与船型预设串用（SUBOFF 曾沿用 5415 的 Cb）。

## ④ 注意点

- 5415 的 10/20/30 kn 工况 Fn 达 0.687–2.060，远超模型试验包络（Fn≤0.45），
  疑为实船节速与模型几何混记 → 保留记录但标注 `validation_eligible=false`；
- 部署在 `:8001` 的阻力实现与工作树代码是两套实现，其 Re/Fn/力闭合三项均错，
  **本单未修**（部署件不在仓 diff 范围），已留证并写入后续建议；
- I3/I4 带是工程数量级判据，不是标准强制值。

## ⑤ 误差判定（逐项 vs 基准）

| 关键量 | 修复前偏差 | 修复后 | <3%? |
|---|---|---|---|
| SUBOFF Rt vs ½ρSV²Ct | 86–89% | 0（构造闭合） | 是 |
| KVLCC2 Rt 同上 | 73% | 0 | 是 |
| 5415 Fn vs V/√(gL) | 82% | 0 | 是 |
| 5415 Cb vs ∇/(LBT) | 0.071 绝对（14%） | 0 | 是 |
| 部署服务 Re vs VL/ν | 57% | 未修 | **否** |
| 部署服务 Rt 力闭合 | 99.97% | 未修 | **否** |

算法相对实验值的精度本轮**不声称改善**（Holtrop 类方法对 Cb<0.55、Fn>0.4 固有超差）；
本轮让这条链第一次能给出可信判决。

## ⑥ 复现

`audit_resistance_validation.py` → `fix_benchmark_reference_dataset.py` → `figures_resistance_audit.py`
→ `cd backend && pytest tests/feat/test_benchmark_reference_consistency.py -q --noconftest`（22 passed）。
commit `68ac885`，分支 `agent/m1452`。
"""

figures = []
for name, caption in (
    ("fig1_reference_rt_consistency.png",
     "修复前表列 Rt 与同一记录 Ct 隐含 Rt 的对数柱对比（差距 3.7–9.1×）"),
    ("fig2_ct_over_cf.png",
     "Ct/Cf(ITTC-57) 合理性带：保留的无量纲列 12/12 落在 1.1–3.0 内"),
    ("fig3_live_service_invariants.png",
     "部署服务 :8001 的 Re/Fn/力闭合三项与闭式解偏差"),
):
    p = ART / "figures" / name
    figures.append({"name": name, "caption": caption,
                    "png_base64": base64.b64encode(p.read_bytes()).decode()})

payload = {
    "title": "阻力验证链欠账清偿：参考数据集一致性审计（12/12→0/12）与校验器 ct 契约修复",
    "summary": "12 个标模参考工况全部违背物理恒等式（Rt 与自身 Ct 差 3.7–9.1×，Fn/湿表面积/排水量互斥），"
               "benchmark_validate 因读不到 ct 恒报 100% 误差；已按文献湿表面积重导并标注排除域，"
               "审计后 0/12 违规，新增 22 项守护测试；部署服务 Re/Fn/力闭合三项仍错，留证待办。",
    "scenario_key": "ship_resistance_validation",
    "mission_id": 1452,
    "tags": ["阻力", "验证与确认", "数据一致性", "Holtrop", "ITTC-57"],
    "markdown": markdown,
    "figures": figures,
}
st, res = post("/tensor/reports", payload, token)
print("report status", st, str(res)[:300])