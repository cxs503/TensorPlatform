#!/usr/bin/env python3
"""Publish the m1609 verification report (numbers injected from artefacts)."""
import base64
import json
import os
import urllib.request
from pathlib import Path

REPO = Path("/srv/tensor/agent/work/m1609")
ART = REPO / "artifacts" / "m1609"
BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
USER = os.environ.get("TENSOR_AGENT_USER", "")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")

audit = json.loads((ART / "ship_benchmark_audit.json").read_text())
geo = audit["geometry"]["hulls"]
acc = audit["model_accuracy"]
rows = {r["case_id"]: r for r in acc["rows"]}
cert = acc["certifiable_subset"]


def post(path, payload, token=None):
    req = urllib.request.Request(
        f"{BASE}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# --- token ---
token = os.environ.get("TOKEN")
if not token:
    for creds in ({"username": USER, "password": PASS},
                  {"email": USER, "password": PASS}):
        try:
            tok = post("auth/login", creds)
            token = tok.get("access_token") or tok.get("token") or (tok.get("data") or {}).get("access_token")
            if token:
                break
        except Exception as exc:  # noqa: BLE001
            print("login attempt failed:", exc)
if not token:
    raise SystemExit("no token: set TOKEN or TENSOR_AGENT_USER/PASS")


def f(x, n=4):
    return f"{x:.{n}f}"


md = f"""# 船体几何验证与阻力基准链复核：从"结构性假 FAIL"到可认证精度 5.33%/5.78%

## 1. 案例介绍

平台阻力基准链由三部分构成：`POST /api/v1/calculations/resistance`（Holtrop 简化式：ITTC-57
摩擦 + 半经验 Froude 残差）、`POST /api/v1/calculations/benchmark_validate`（对实验 Ct 打分）、
`backend/data/benchmark_reference.json`（12 个参考点）。三型船公布主尺度与参考点如下：

| 船型 | 形式 | L (m) | B/D (m) | T (m) | ∇ (m³) | Cb | S (m²) | 来源 |
|------|------|-------|---------|-------|--------|-----|--------|------|
| KVLCC2 | 单体 | 320.0 | 58.0 | 20.8 | 312 622 | 0.8098 | 27 194 | SIMMAN 2008 / MOERI |
| DTMB-5415 | 单体 | 5.720 | 0.760 | 0.248 | 0.5492 | 0.506 | 4.874 | INSEAN 2000 m2340 / ITTC V&V |
| SUBOFF | 回转体 | 4.356 | D=0.508 | — | — | 表面丰满度 0.7796 | 5.42 | Groves et al. 1989 / Roddy DTRC-90/012 |

参考数据 12 例，Fr 覆盖 0.129–2.060，实验 Ct 0.0028–0.0045。

## 2. 怎么做的

1. **只读扫描**：提案池 p1–p9、报告局限节、guide_tracker 状态、跨仓未合并分支；确认无重复在飞单。
2. **判据先于动手**：平台纪律"几何必须先验证 Cb/L/B"。据此建立三道机器判据：
   (a) Cb 声明 vs 由 ∇/(L·B·T) 反推（单体，容差 2%）；回转体用 **Cauchy–Schwarz 体积下界**
   `Cb_cyl ≥ (S/πDL)²`（仅由公布 L,D,S 推出，不依赖型值）；
   (b) 湿表面积：Holtrop & Mennen (1982) 公式 vs 公布值（容差 15%，KVLCC2 期望 <1%）；
   (c) 参考点自洽：力值 ≡ ½ρ_S·Ct·V²；Fr 存储值 ≡ V/√(gL)；单体 Fr ≤ 0.45（拖曳水池排水型区）。
3. **零依赖重构**：把阻力物理抽到 `api/domain/resistance_core.py`，使审计可在无
   psycopg2/cryptography 的镜像内运行真实算法（非 mock）。
4. **审计器** `tools/verify_ship_benchmark.py` 一次跑完几何闸门 + 12 例精度 + 出图；
   `--regenerate` 以幂等方式重写参考表（实验 Ct 绝不修改）。

## 3. 计算结果

### 3.1 几何验证（修复前 → 修复后）

| 船型 | Cb 反推 | 相对偏差 | Holtrop S vs 公布 | 旧占位式 S 偏差 |
|------|---------|----------|-------------------|-----------------|
| KVLCC2 | {f(geo['KVLCC2']['derived']['cb_from_volume'])} | {f(100*abs(geo['KVLCC2']['derived']['cb_from_volume']-0.8098)/0.8098,2)}% | {f(geo['KVLCC2']['derived']['s_holtrop_m2'],1)} vs 27 194 m² ({f(100*(geo['KVLCC2']['derived']['s_holtrop_m2']-27194)/27194,2)}%) | {f(geo['KVLCC2']['legacy_wetted_surface'][0]['err_vs_published_pct'],1)}% |
| DTMB-5415 | {f(geo['DTMB-5415']['derived']['cb_from_volume'])} | {f(100*abs(geo['DTMB-5415']['derived']['cb_from_volume']-0.506)/0.506,2)}% | {f(geo['DTMB-5415']['derived']['s_holtrop_m2'],3)} vs 4.874 m² ({f(100*(geo['DTMB-5415']['derived']['s_holtrop_m2']-4.874)/4.874,2)}%) | {f(geo['DTMB-5415']['legacy_wetted_surface'][0]['err_vs_published_pct'],1)}% |
| SUBOFF | 0.7796 (下界 0.6078) | 旧预设 0.508 **可证不可能** | 回转体，公式不适用 | {f(geo['SUBOFF']['legacy_wetted_surface'][0]['err_vs_published_pct'],1)}% |

### 3.2 修复后模型精度（Ct 对实验值）

| 算例 | Fr | Ct 计算 | Ct 参考 | 误差 | 包络内 | 参考点自洽 |
|------|-----|---------|---------|------|--------|-----------|
""" + "\n".join(
    f"| {cid} | {f(r['froude_number'],3)} | {f(r['ct_calc'],6)} | {f(r['ct_ref'],6)} | "
    f"{f(r['error_pct'],2)}% | {'是' if r['in_model_envelope'] else '否'} | "
    f"{'是' if r['reference_integrity_ok'] else '否'} |"
    for cid, r in rows.items()
) + f"""

可认证子集（模型包络内 ∧ 参考点自洽）：n={cert['n']}（{', '.join(cert['case_ids'])}），
mean |err| = {f(cert['mean_abs_error_pct'],2)}%，max = {f(cert['max_abs_error_pct'],2)}%，
{cert['n_within_15pct']}/{cert['n']} ≤ 15%。

### 3.3 修复前为何全错

旧代码读 `calc_data["ct"]`，而响应模型无 `ct` 字段 → 每例 `|0−Ct_ref|/Ct_ref = 100%`；
且 payload 用 `speed`/`displacement`，请求模型声明 `speed_ms`/`displacement_t`，多余键被忽略 →
全部按默认 1.0 m/s 计算。故历史"全部 HONEST-FAIL"是契约缺陷，不是物理结论。

## 4. 结果图

图 1：三型船 Ct–Fr 曲线（平台 vs 参考 vs ITTC-57 摩擦线）；
图 2：逐例相对误差（蓝=≤15% 绿区，红=超限）；
图 3：湿表面积三方对比（公布 / Holtrop / 旧占位式，对数轴）。

## 5. 注意点

- **Ct = Cf + Cr，湿表面积在系数中相消**：占位式 S 只污染力值（KVLCC2 +50.6%），不污染系数比较；
  基准失效的真正根因是缺 `ct` 字段与键名不匹配。此辨析避免了错误的修复方向。
- 残差式 `Cr=0.002(1+2Cb)Fn^2.5` 无形状因子亦无兴波项，商船型系统性偏低 49–56%；
  本单**不改算法**（当前无独立判据自证改进），只把有效性包络显式返回并记 honest-FAIL。
- DTMB-5415 三个参考点（Fr 0.687/1.37/2.06）为"模型尺度长度 + 实船航速"，超出拖曳水池排水型区，
  只标记 `provenance_flag`，不修改实验值。
- 陷阱：`api/domain/platform.py` 与标准库 `platform` 同名，把 `api/domain` 加入 `sys.path`
  会遮蔽标准库并令 numpy/fastapi 立即 ImportError；审计器改用按路径 `importlib` 加载。
- 局限：本镜像缺 psycopg2/cryptography，仓库 `conftest.py` 导入 `main.app` 失败，
  整包测试集无法导入；新增测试以 `--noconftest` + 按路径加载纯模块执行。

## 6. 误差判定

| 关键量 | 基准 | 误差 | 是否 <3% |
|--------|------|------|----------|
| SUBOFF-01 Ct (Fr 0.459) | Roddy 1990 实验 Ct 0.0038 | **{f(rows['SUBOFF-01']['error_pct'],2)}%** | **否**（<15% 达标） |
| SUBOFF-02 Ct (Fr 0.612) | Roddy 1990 实验 Ct 0.0040 | **{f(rows['SUBOFF-02']['error_pct'],2)}%** | **否**（<15% 达标） |
| KVLCC2-01..04 Ct | SIMMAN 2008 | {f(min(rows[c]['error_pct'] for c in rows if c.startswith('KVLCC2')),1)}–{f(max(rows[c]['error_pct'] for c in rows if c.startswith('KVLCC2')),1)}% | **否**（超出包络，honest-FAIL） |
| KVLCC2 Cb（几何） | ∇/(LBT) 反推 | {f(100*abs(geo['KVLCC2']['derived']['cb_from_volume']-0.8098)/0.8098,3)}% | **是** |
| KVLCC2 S（Holtrop） | 公布 27 194 m² | {f(abs(100*(geo['KVLCC2']['derived']['s_holtrop_m2']-27194)/27194),2)}% | **是** |
| DTMB-5415 S（Holtrop） | 公布 4.874 m² | {f(abs(100*(geo['DTMB-5415']['derived']['s_holtrop_m2']-4.874)/4.874),2)}% | 否（Cwp 为估计值） |

结论：**几何与湿表面积判据满足 <3%（KVLCC2）；阻力系数精度不满足 <3%**，可认证区（2 例）为
5.33%/5.78%，满足 15% 验收线；商船型与包络外算例如实记为不达标。
"""

figs = []
for name, cap in (("ct_vs_fr_comparison.png", "图1 Ct–Fr：平台 vs 实验参考 vs ITTC-57 摩擦线（三型船）"),
                  ("error_per_case.png", "图2 逐例相对误差与 15% 验收线"),
                  ("wetted_surface_verification.png", "图3 湿表面积：公布值 vs Holtrop(1982) vs 旧占位式")):
    p = ART / name
    if p.exists():
        figs.append({"name": name, "caption": cap,
                     "png_base64": base64.b64encode(p.read_bytes()).decode()})

payload = {
    "title": "船体几何验证与阻力基准链复核：结构性假 FAIL 修复与 5.33%/5.78% 可认证精度 (m1609)",
    "summary": (
        "修复阻力基准链三处结构性缺陷（响应缺 ct 字段、payload 键名不匹配、占位湿表面积 +50%/+75%），"
        "以公布主尺度重建三型船几何并加入 Cb/L/B 机器闸门；"
        f"可认证子集 n={cert['n']}，Ct 误差 {f(cert['mean_abs_error_pct'],2)}%（max {f(cert['max_abs_error_pct'],2)}%），"
        "商船型 KVLCC2 系统偏低 49–56% 记为 honest-FAIL；3 个 DTMB-5415 参考点尺度不自洽已标记。"
    ),
    "scenario_key": "ship_resistance_benchmark_verification",
    "mission_id": 1609,
    "tags": ["verify", "resistance", "geometry", "benchmark", "honest-fail", "m1609"],
    "markdown": md,
    "figures": figs,
}
res = post("tensor/reports", payload, token)
print("report ok:", json.dumps(res)[:400])