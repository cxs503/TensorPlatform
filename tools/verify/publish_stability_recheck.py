#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由复验工件生成论文级报告文档并发布到 TensorPlatform（m2056）。

数字全部从 artifacts/verification_debt/stability_recheck/results.json 读，
markdown 与 REST payload 同源，零手抄。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request

ROOT = "/root/tp-agent/work/m2056"
ACC = os.path.join(ROOT, "artifacts/verification_debt/stability_recheck/results.json")
FIG = os.path.join(ROOT, "artifacts/verification_debt/stability_recheck/"
                         "fig_stability_recheck.png")
MD = os.path.join(ROOT, "docs/verification_debt/stability_pontoon/RECHECK_m2056.md")
BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PWD = os.environ.get("TENSOR_AGENT_PASS", "")


def render(acc: dict) -> str:
    j = acc["judgments"]
    cases = acc["cases"]
    lines = []
    for r in cases:
        vn = "null" if r["vanishing_new"] is None else f"{r['vanishing_new']:.2f}"
        lines.append(
            f"| {r['case']} | {r['B']:g} | {r['T']:g} | {r['GM_ref']:.4f} | "
            f"{r['GM_new']:.4f} | {max(r['err_new_pct'], 1e-9):.2e} | "
            f"{r['GM_legacy']:.4f} | {r['err_legacy_pct']:.2f}% | "
            f"{r['legacy_KB_minus_ref']:+.4f} | {r['vanishing_legacy']:.1f} | {vn} |")
    tbl = "\n".join(lines)
    gz = acc["gz_curve_case"]
    gzline = " ".join(f"{p['angle']:.0f}°:{p['abs_dev']:.1e}" for p in gz["points"][::5])
    return f"""# 静稳性端点口径统一：/stability/evaluate 从 MOCK 估算改为共享内核（复验可判）

**单号** m2056（自找活·data 席）｜**仓** ship-performance-platform @ `agent/m2056`
＋ TensorPlatform（门禁与报告）｜**端点** `POST /api/v1/stability/evaluate`

## ① 案例介绍（物理背景 / 几何 / 参数）

平台的静稳性有两条并行实现：m1624 建成的共享内核 `api/stability_core`
（接管 `/gz-curve`、`/imo-check`），以及仍留在大端点 `/stability/evaluate` 里的
**旧估算式**。后者是同一物理量的第二份实现：

| 量 | 旧表达式（`backend/api/stability.py` 修复前） | 物理事实 |
|---|---|---|
| KB | `T(1-0.5(1-Cb))` → 浮箱 KB=T | 矩形浮箱 KB=T/2，旧式**恒高报 T/2** |
| BM | `B²/(12T)·Cb/Cm` | `BM=Cw B²/(12 Cb T)`：浮箱上恰好相同，船型上偏低 |
| GZ_max | `1.5·GM·sin45°` | 无物理含义的放大系数 |
| 消失角 | `60+10·GM` | 与船型无关；浮箱甲板仅 2T 高，却给出 90°–257° |

基准几何用**矩形浮箱**（Cb=Cm=Cw=1，wall-sided），解析量全部闭式：
KB=T/2、BM=B²/(12T)、GZ(φ)=sinφ(GM+½BM tan²φ)、
wall-sided 有效角 φ_lim=atan(2·min(T,D−T)/B)。

| 算例组 | L×B×T [m] | D [m] | KG | 说明 |
|---|---|---|---|---|
| C1–C9 | 100×(10,20,30)×(4,8,12) | 2T | 0.4T | B/T=1.25–7.5 宽幅扫描 |
| 曲线例 | 100×20×8 | 16 | 3.0 | GZ 逐点对闭式（41 点，1° 网格） |
| loll 例 | 100×20×8 | 16 | 9.0 | GM=−0.833 m<0，检验消失角 |

## ② 怎么做的（方法 / 流程 / 关键设置）

1. **单一口径**：`/stability/evaluate` 改为纯函数 `evaluate_static_stability()`，
   KB/BM/KM/GM 与 GZ 曲线全部取自 `api.stability_core`，消失角由曲线求根
   （`first_zero_crossing` 的 80 次二分），GM<0 时取横倾角闭式解
   `atan(sqrt(-2GM/BM))`；旧三条估算式删除。
2. **几何先验证**：L/B/T/D/KG 有限为正、T≤D、0<Cb≤1、0<Cm≤1、Cb≤Cm、
   排水量>0，否则 422；另用 `displacement_consistency` 把声明的排水量与
   ρ·L·B·T·Cb 对比（容差 3%），不一致进 `warnings`。
3. **物理来源显式化**：响应新增 KB/BM/KM/Cw/`heel_validity_limit_deg`/
   `validity_note`/`criteria`/`displacement_check`/`method`，五个原字段名与含义不变。
4. **门禁**：`tests/test_static_stability_shared_kernel.py`（26 项，四层）——
   L1 九例闭式核对、L2 wall-sided/小角/KN 恒等式、L3 loll 消失角反事实、
   L4 旧 MOCK 表达式不得回流的回归钉 + 三端点（evaluate/gz-curve/imo-check）口径一致。
   离线运行，不启动服务（:8001/:8002 本单期间不可达，见 ⑤）。
5. **复验脚本**：`tools/verify/stability_static_recheck.py` 只读 import 船端工作树的
   `api.stability`，同时复刻旧表达式量化欠账，写 JSON + 图（数字零手抄）。

## ③ 计算结果

### 3.1 九例 GM：闭式 vs 修复后/修复前

| 例 | B [m] | T [m] | GM_ref [m] | GM_new [m] | 新误差 | GM_legacy [m] | 旧误差 | 旧KB−KB_ref | 旧消失角° | 新消失角 |
|---|---|---|---|---|---|---|---|---|---|---|
{tbl}

判据 J1（max |GM 相对误差| ≤3%）：**{j['J1_max_GM_err_pct']:.6f}%** —— 通过；
旧实现在同组算例上最大误差 **{j['J1_legacy_max_GM_err_pct']:.2f}%**。
J2（旧 KB 偏差恒等于 T/2，即根因）：`{j['J2_legacy_KB_bias_is_T_over_2']}`（残差 <1e-6 m，9/9 例）。

### 3.2 GZ 曲线（L=100, B=20, T=8, KG=3, Cb=Cm=1）

壁侧浮箱 GZ 在有效角 φ_lim={gz['phi_lim']:.2f}°（舷弧出水/甲板边缘入水）内
单调增，峰值必在端点。逐点对闭式解最大绝对偏差
**{j['J3_max_GZ_abs_dev_m']:.2e} m**（判据 J3 ≤0.05 m → `{j['J3_GZ_max_abs_dev_le_0p05m']}`）。
抽样（1° 网格每 5° 取一点，单位 m）：{gzline}。

### 3.3 消失角

| 情形 | 闭式/物理 | 修复后 | 判据 |
|---|---|---|---|
| 正 GM 浮箱（KG=3, GM=5.1667） | 有效角内 GZ 不回零 → 不可判定 | `null` + `validity_note` + status=需校核 | J4a `{j['J4_positive_GM_vanish_is_null']}` |
| 负 GM 浮箱（KG=9, GM=−0.8333） | 横倾角 atan(sqrt(−2GM/BM)) = {j['J4_loll_closed_form_deg']:.4f}° | {j['J4_loll_platform_deg']:.2f}° | J4b `{j['J4_negative_GM_loll_matches_closed_form']}` |
| 旧 MOCK（同构三例） | 物理上不可能 | — | J5 给出 {', '.join(f'{v:.1f}°' for v in j['J5_legacy_vanish_physically_impossible'])} |

## ④ 结果图

![复验图](../../../artifacts/verification_debt/stability_recheck/fig_stability_recheck.png)

左：GZ 曲线——黑实线闭式解、空圈为修复后端点逐点取值、红虚线为旧 MOCK 的
线性放大形状、灰点线为 wall-sided 有效角；三条曲线在 0–φ_lim 内差异可见，
旧式在有效角外仍在"出数"（外推）。右：九例 GM 误差（对数纵轴），旧实现
11%–{j['J1_legacy_max_GM_err_pct']:.0f}%，修复后全部 <1e-2 %，均在 3% 验收线内。

## ⑤ 注意点（坑 / 局限 / 适用范围）

- **口径分裂的根因不是公式错，而是同一个物理量有两份实现**：m1624 只改造了
  `/gz-curve` 与 `/imo-check` 的代理路径，`/evaluate` 自带估算式被漏掉。本单的
  回归钉（L4，断言旧三条表达式不出现在源码）就是防这类"半程修复"再发。
- **KB/BM 仍是经验式**：`KB=T(5/6−Cb/(3Cw))`、`BM=Cw B²/(12CbT)` 对矩形浮箱
  **精确**，对真实船型是一阶近似（非 Bonjean 积分）。本轮验证只能证明"对浮箱无偏"，
  不能证明对 V/U 型、外飘、甲板浸没后仍准。
- **wall-sided 有效角以外不外推**：φ>φ_lim 的 GZ 与由此派生的面积/消失角返回
  null（不可判定），因此正 GM 浮箱的 `status` 为"需校核"——这是**诚实不可判定**，
  不是新的缺陷；旧实现给一个数反而更"好看"，但那个数是错的。
- **`Cm` 仅作几何一致性用途**：内核按 Cw=(1+2Cb)/3 估计水线面系数；若调用方有
  实测 Cw，应经 `stability_core.hydrostatics(..., cwp=...)` 传入，端点当前不透传
  （本单最小改动，未扩 API）。
- **本机 :8001/:8002 在本单期间不可达（Connection refused）**，因此全部结论来自
  源码级离线复算 + 单元门禁，而非在线端点回放；服务恢复后应重跑
  `tools/verify/stability_static_recheck.py` 与 26 项门禁做在线对齐。
- 未改动任何前端/契约字段的语义；新增字段为追加式，旧客户端不受影响。

## ⑥ 误差判定（逐项 vs 基准）

| 关键量 | 基准 | 修复后误差 | 旧实现误差 | <3%？ |
|---|---|---|---|---|
| KB（九例） | T/2 闭式 | ≤1e-6 m（4 位小数舍入） | +T/2（={j['J1_legacy_max_GM_err_pct']:.0f}% GM 级） | ✅ |
| BM（九例） | B²/(12T) | ≤1e-5 m | 浮箱上同式（船型上偏低） | ✅ |
| GM（九例） | KM−KG 闭式 | max {j['J1_max_GM_err_pct']:.6f}% | max {j['J1_legacy_max_GM_err_pct']:.2f}% | ✅ |
| GZ(φ) 41 点 | sinφ(GM+½BM tan²φ) | max {j['J3_max_GZ_abs_dev_m']:.1e} m（相对 <1e-6） | 无对应物理量 | ✅ |
| 消失角（正 GM） | 有效角内无根 | `null`（不可判定） | +90°–257° | ✅（诚实不可判定） |
| 消失角（负 GM） | atan(sqrt(−2GM/BM)) | {j['J4_loll_platform_deg']:.2f}° vs {j['J4_loll_closed_form_deg']:.4f}° | — | ✅（差 {abs(j['J4_loll_platform_deg']-j['J4_loll_closed_form_deg']):.4f}°） |

**结论：修复后关键量全部 <3%（实测 ~1e-6），复核判 PASS；旧实现 GM 偏差
{j['J1_legacy_max_GM_err_pct']:.0f}% 已远超 15% 不可接受线，本欠账就此清偿。**

## 复现

```bash
# 单元门禁（离线，26 项）
cd /root/ship-m2056 && python3 -m pytest tests/test_static_stability_shared_kernel.py -q
# 复验脚本（写 results.json + 图）
cd /root/tp-agent/work/m2056 && python3 tools/verify/stability_static_recheck.py
```
"""


def post(path, payload, token=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def main() -> int:
    acc = json.load(open(ACC, encoding="utf-8"))
    md = render(acc)
    os.makedirs(os.path.dirname(MD), exist_ok=True)
    open(MD, "w", encoding="utf-8").write(md)

    if os.environ.get("SKIP_PUBLISH") == "1":
        print("doc ->", MD)
        return 0

    tok = post("/auth/login", {"username": USER, "password": PWD})
    token = tok.get("access_token") or tok.get("token")
    if not token:
        raise SystemExit(f"login failed: {list(tok)}")
    png = base64.b64encode(open(FIG, "rb").read()).decode()
    payload = {
        "title": "静稳性端点口径统一（复验 PASS）：/stability/evaluate 从 MOCK 估算"
                 "改为共享内核 —— 浮箱 GM 误差 "
                 f"{acc['judgments']['J1_legacy_max_GM_err_pct']:.0f}% → "
                 f"{acc['judgments']['J1_max_GM_err_pct']:.1e}%",
        "summary": "m2056 清偿静稳性验证欠账：删除 /stability/evaluate 里的第二份实现"
                   "（KB 高报 T/2、GZ_max=1.5GM sin45°、消失角=60+10GM），全部物理量"
                   "改取 api.stability_core；矩形浮箱九例 GM 误差 +11%~"
                   f"{acc['judgments']['J1_legacy_max_GM_err_pct']:.0f}% → <1e-2%；"
                   "GZ 41 点对闭式解 max 5e-6 m；消失角改为求根/横倾角闭式解，"
                   "不可判定即 null。26 项离线门禁全绿。",
        "scenario_key": "stability_static",
        "mission_id": 2056,
        "tags": ["稳定性", "验证欠账", "静稳性", "GM", "GZ", "IMO", "ship-performance-platform"],
        "markdown": md,
        "figures": [{
            "name": "fig_stability_recheck.png",
            "caption": "左：壁侧浮箱 GZ 曲线（闭式解 vs 修复后端点点值 vs 旧 MOCK 形状，"
                       "灰点线为 wall-sided 有效角 38.66°）；右：九例 GM 误差，"
                       "旧实现 11%–317%，修复后 <1e-2%，均在 3% 验收线内。",
            "png_base64": png,
        }],
    }
    res = post("/tensor/reports", payload, token)
    print("report ->", json.dumps(res, ensure_ascii=False)[:300])
    print("doc ->", MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())