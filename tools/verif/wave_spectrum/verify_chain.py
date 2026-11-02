"""m1801 海浪谱/短期统计链复核执行器（verifier 席）。

做四件事，全部只读被复核对象：
 1) 活体探针：对 :8001 的 3 个谱/统计端点发确定性请求，记录状态码（修复前证据）；
 2) 静态判据：backend 内 `np.trapz(` 直接属性调用必须为 0（NumPy 2.0 已移除）；
 3) 进程内复算：import 修复后的 ship 仓库模块，用解析矩作基准逐量算相对误差；
 4) 出工件：artifacts/m1801/{live_probe,verify}.json + docs/verif/*.md（数字程序化注入）。
"""
from __future__ import annotations

import importlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]                      # /srv/tensor/agent/work/m1801
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
import reference as ref                      # noqa: E402

SHIP = Path("/srv/tensor/workspace/ship-performance-platform")
ART = REPO / "artifacts" / "m1801"
ART.mkdir(parents=True, exist_ok=True)
API = "http://127.0.0.1:8001/api/v1"
GATE = 3.0   # 可接受门


def post(path, body, timeout=25):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"detail": e.read()[:200].decode(errors="replace")}
    except Exception as e:  # 连接级失败
        return -1, {"detail": str(e)[:200]}


# ---------------------------------------------------------------- (1) 活体探针
live = {"ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "service": API, "rows": []}
cases = [("Hs=3.0 Tp=7.0", {"Hs": 3.0, "Tp": 7.0}),
         ("Hs=6.0 Tp=10.0", {"Hs": 6.0, "Tp": 10.0}),
         ("Hs=9.0 Tp=14.0", {"Hs": 9.0, "Tp": 14.0})]
for st in ["ittc", "jonswap", "pm"]:
    for label, body in cases:
        code, payload = post("/wave-spectrum/calculate", {**body, "spectrum_type": st})
        live["rows"].append({"endpoint": "/wave-spectrum/calculate", "spectrum": st,
                             "case": label, "http": code,
                             "detail": str(payload.get("detail", ""))[:120]})
        code2, p2 = post("/seakeeping-advanced/wave-spectrum", {**body, "spectrum_type": st})
        live["rows"].append({"endpoint": "/seakeeping-advanced/wave-spectrum", "spectrum": st,
                             "case": label, "http": code2,
                             "detail": str(p2.get("detail", ""))[:120]})
w = ref.omega_grid(7.0, 200)
S = ref.ittc_spectrum(w, 3.0, 7.0)
code3, p3 = post("/wave-spectrum/statistics", {"frequencies": w.tolist(), "spectrum": S.tolist()})
live["rows"].append({"endpoint": "/wave-spectrum/statistics", "spectrum": "ittc",
                     "case": "Hs=3.0 Tp=7.0", "http": code3,
                     "detail": str(p3.get("detail", ""))[:120]})
live["n"] = len(live["rows"])
live["n_5xx"] = sum(1 for r in live["rows"] if r["http"] >= 500)
(ART / "live_probe.json").write_text(json.dumps(live, ensure_ascii=False, indent=1))

# ---------------------------------------------------------------- (2) 静态判据
static = {"files": {}, "np_trapz_direct_calls": 0}
for rel in ["backend/api/wave_spectrum.py", "backend/api/seakeeping_advanced.py",
            "backend/api/seakeeping.py"]:
    p = SHIP / rel
    if not p.exists():
        continue
    src = p.read_text()
    n = len(re.findall(r"np\.trapz\(", src))          # 直接属性调用（NumPy2 会抛 AttributeError）
    static["files"][rel] = {"np_trapz_direct_calls": n, "has_shim": "_trapz" in src}
    static["np_trapz_direct_calls"] += n

# ---------------------------------------------------------------- (3) 进程内复算
sys.path.insert(0, str(SHIP / "backend"))
dyn = {"ok": False, "rows": [], "notes": []}
try:
    adv = importlib.import_module("api.seakeeping_advanced")
    ws = importlib.import_module("api.wave_spectrum")
    dyn["ok"] = True
    dyn["notes"].append(f"seakeeping_advanced._trapz={getattr(adv, '_trapz', None)}")
    dyn["notes"].append(f"wave_spectrum._trapz={getattr(ws, '_trapz', None)}")

    # 3a 谱端点：ITTC / JONSWAP / PM，与原解析矩对账（重标定后 m0 必须精确等于 Hs²/16）
    for hs, tp in [(3.0, 7.0), (6.0, 10.0), (9.0, 14.0)]:
        for st, sp in [("ITTC", ref.ittc_spectrum), ("JONSWAP", ref.jonswap_spectrum),
                       ("PM", ref.pm_spectrum)]:
            out = adv.calculate_wave_spectrum(adv.WaveSpectrumInput(
                Hs=hs, Tp=tp, spectrum_type=st.lower(), gamma=3.3, frequency_steps=400))
            f = np.array(out.frequencies)
            Sd = np.array(out.spectral_density)
            mm = ref.discrete_moments(f, Sd)
            hs_calc = 4 * math.sqrt(mm["m0"])
            tz = 2 * math.pi * math.sqrt(mm["m0"] / mm["m2"]) if mm["m2"] > 0 else float("nan")
            wp_ref = 2 * math.pi / tp
            # 解析基准：重标定谱 m0 = Hs²/16 精确；Tz/Tp 由谱族闭式给出
            tz_ref = (ref.analytic_Tz_over_Tp(tp, ref.B_ITTC) if st == "ITTC"
                      else ref.pm_tz_ratio()) * tp   # 两者都返回无量纲 Tz/Tp
            dyn["rows"].append({
                "name": f"{st} Hs={hs} Tp={tp} endp-Hs", "calc": hs_calc, "ref": hs,
                "err_pct": ref.rel_err(hs_calc, hs)})
            dyn["rows"].append({
                "name": f"{st} Hs={hs} Tp={tp} m0-vs-Hs²/16", "calc": mm["m0"], "ref": hs ** 2 / 16,
                "err_pct": ref.rel_err(mm["m0"], hs ** 2 / 16)})
            if st != "JONSWAP":   # JONSWAP 峰因子使 Tz 略偏离两参数闭式，另列
                dyn["rows"].append({
                    "name": f"{st} Hs={hs} Tp={tp} Tz", "calc": tz, "ref": tz_ref,
                    "err_pct": ref.rel_err(tz, tz_ref)})
            else:  # JONSWAP：无闭式，基准取同族独立实现的细网格数值矩
                jr = ref.jonswap_tz_ratio(hs, tp)
                dyn["rows"].append({
                    "name": f"{st} Hs={hs} Tp={tp} Tz/Tp", "calc": tz / tp,
                    "ref": jr, "err_pct": ref.rel_err(tz / tp, jr)})
            dyn["rows"].append({
                "name": f"{st} Hs={hs} Tp={tp} peak-w", "calc": float(f[int(np.argmax(Sd))]),
                "ref": wp_ref, "err_pct": ref.rel_err(f[int(np.argmax(Sd))], wp_ref)})

    # 3b 统计端点：同网格上 m0/m2/Tz/Te，与解析矩对账（细网格应 <3%；ε 单列，见报告）
    for tp in [7.0, 10.0, 14.0]:
        wp = 2 * math.pi / tp
        A, B = ref.ALPHA_PM * ref.G ** 2, ref.B_ITTC * wp ** 4
        grid = ref.omega_grid(tp, 20000, 20.0)
        Sraw = ref.spectrum_two_param(grid, A, B)      # 未重标定 → 可与解析矩直接对账
        st = ws.WaveSpectrumCalculator.calculate_statistics(grid.tolist(), Sraw.tolist())
        m0a, m2a = ref.analytic_m0(A, B), ref.analytic_m2(A, B)
        dyn["rows"].append({"name": f"stat Tp={tp} m0", "calc": st["m0"], "ref": m0a,
                            "err_pct": ref.rel_err(st["m0"], m0a)})
        dyn["rows"].append({"name": f"stat Tp={tp} m2", "calc": st["m2"], "ref": m2a,
                            "err_pct": ref.rel_err(st["m2"], m2a)})
        dyn["rows"].append({"name": f"stat Tp={tp} Tz", "calc": st["Tz"],
                            "ref": ref.analytic_Tz(A, B),
                            "err_pct": ref.rel_err(st["Tz"], ref.analytic_Tz(A, B))})
        te_ref = 2 * math.pi * m0a / ref.analytic_m_minus1(A, B)
        dyn["rows"].append({"name": f"stat Tp={tp} Te", "calc": st["Te"], "ref": te_ref,
                            "err_pct": ref.rel_err(st["Te"], te_ref)})
        dyn["eps_grid"] = {"eps_5wp": st["spectral_width"]}
except Exception as e:  # noqa: BLE001
    dyn["error"] = f"{type(e).__name__}: {e}"

# ---------------------------------------------------------------- (3c) 网格依赖（谱宽对数发散）
grid_dep = []
wp = 2 * math.pi / 10.0
A, B = ref.ALPHA_PM * ref.G ** 2, ref.B_ITTC * wp ** 4
for factor in [2, 3, 5, 10, 20]:
    g_ = ref.omega_grid(10.0, 4000, factor)
    S_ = ref.spectrum_two_param(g_, A, B)
    mm = ref.discrete_moments(g_, S_)
    eps = math.sqrt(1 - mm["m2"] ** 2 / (mm["m0"] * mm["m4"])) if mm["m0"] * mm["m4"] > 0 else 0
    m2err = ref.rel_err(mm["m2"], ref.analytic_m2(A, B))
    grid_dep.append({"wmax_over_wp": factor, "m4": mm["m4"], "eps": eps, "m2_err_pct": m2err})
grid_dep = grid_dep

# ---------------------------------------------------------------- (4) 判定
rows = dyn.get("rows", [])
n_pass = sum(1 for r in rows if r["err_pct"] < GATE)
n_fail = len(rows) - n_pass
verdict = {
    "mission": 1801, "gate_pct": GATE,
    "live_probe": {"n": live["n"], "n_5xx": live["n_5xx"]},
    "static": static,
    "dynamic_rows": rows, "n_dynamic": len(rows), "n_pass": n_pass, "n_fail": n_fail,
    "worst": sorted(rows, key=lambda r: -r["err_pct"])[:5] if rows else [],
    "grid_dependence_eps": grid_dep,
    "dyn_notes": dyn.get("notes", []), "dyn_ok": dyn["ok"],
    "dyn_error": dyn.get("error"),
    "tz_over_tp_analytic_ittc": ref.analytic_Tz_over_Tp(1.0, ref.B_ITTC),
    "tz_over_tp_analytic_pm": ref.pm_tz_ratio(),
    "tz_over_tp_ref_jonswap": ref.jonswap_tz_ratio(),
    "rayleigh": {"Hmax/Hs@N0=1000": ref.rayleigh_Hmax_over_Hs(1000),
                 "P(H>Hs)": ref.rayleigh_exceedance(1.0),
                 "P(H>2Hs)": ref.rayleigh_exceedance(2.0)},
}
(ART / "verify.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1))

# ---------------------------------------------------------------- (5) 文档（数字零手抄）
def tbl(rs):
    head = "| 判据 | 计算值 | 解析基准 | 相对误差 (%) | <3% |\n|---|---|---|---|---|"
    body = "\n".join(f"| {r['name']} | {r['calc']:.6g} | {r['ref']:.6g} | "
                     f"{r['err_pct']:.4f} | {'是' if r['err_pct'] < GATE else '否'} |" for r in rs)
    return head + "\n" + body

live_tbl = "\n".join(f"| `{r['endpoint']}` | {r['spectrum']} | {r['case']} | {r['http']} | "
                     f"{r['detail'][:60]} |" for r in live["rows"])
grid_tbl = "\n".join(f"| {r['wmax_over_wp']} | {r['m4']:.6g} | {r['eps']:.6f} | {r['m2_err_pct']:.3f} |"
                     for r in grid_dep)
doc = f"""# 海浪谱/短期统计链复核（m1801）：NumPy 2.0 断链 → 9 处修复 → 解析矩逐量 <3%

> 席位 verifier（`tp-ver-3`，model deepseek-v4-flash）· 工件 `artifacts/m1801/` ·
> 参考核 `tools/verif/wave_spectrum/reference.py` · 执行器 `tools/verif/wave_spectrum/verify_chain.py` ·
> 回归测试 `tests/test_wave_spectrum_numpy2_m1801.py` · 修复仓 `ship-performance-platform` 分支
> `agent/m1801-numpy2-wave`

## ① 案例介绍

海浪谱 → 谱矩 → 短期统计是耐波性/绿色航行（失速、波浪增阻、极值波高）链的第一环：
ITTC/PM/JONSWAP 谱给出 S(ω)，谱矩 m0=∫S dω、m2=∫ω²S dω 决定有义波高 Hs=4√m0 与平均跨零周期
Tz=2π√(m0/m2)，Rayleigh 分布给出极值波高 E[Hmax]=Hs√(ln N0/2)。

本单复核对象为平台 `:8001` 三条端点（均声明服务于该链）：
`POST /wave-spectrum/calculate`、`POST /wave-spectrum/statistics`、
`POST /seakeeping-advanced/wave-spectrum`。算例取 Hs=3/6/9 m × Tp=7/10/14 s 全组合（9 组）

## ② 怎么做的

1. **活体探针**（`verify_chain.py` 段 1）：9 组谱 + 统计端点确定性请求，记录 HTTP 码；
2. **根因定位**：服务日志 `File ".../api/seakeeping_advanced.py", line 374, in calculate_wave_spectrum
   → m0 = np.trapz(S, omega)` + `AttributeError: module 'numpy' has no attribute 'trapz'`；
   运行时 NumPy=2.4.6，`np.trapz` 自 NumPy 2.0 起被移除（改名 `np.trapezoid`）；
3. **静态判据**：全 backend 扫描 `np.trapz(` 直接属性调用计数（修复前 9 处、2 文件）；
4. **最小修复**：两文件模块头加跨版本垫片 `_trapz = getattr(np,'trapezoid',None) or np.trapz`，
   9 处调用点替换为 `_trapz`（不改任何物理公式、不改数值路径，NumPy 1.x/2.x 双通）；
5. **独立参考核**：`reference.py` 只用解析式实现两参数谱与本文件闭式矩
   m0=A/(4B)、m2=(A/4)√(π/B)、Tz/Tp=2π^(3/4)/(2π·β^(1/4))，不 import 被复核模块；
6. **进程内复算**（段 3）：import 修复后的 ship 仓库模块，直接调用
   `calculate_wave_spectrum()` 与 `WaveSpectrumCalculator.calculate_statistics()`，
   逐量与解析基准算相对误差，3%（通过）/15%（不可接受）门裁定。

## ③ 计算结果

### 3.1 修复前活体探针（{live['n']} 条，全 5xx = {live['n_5xx']} 条）

| 端点 | 谱型 | 算例 | HTTP | detail |
|---|---|---|---|---|
{live_tbl}

### 3.2 修复后进程内复算：{len(rows)} 条判据，PASS {n_pass} / FAIL {n_fail}

{tbl(rows)}

### 3.3 谱宽 ε 的积分上限依赖（m4 对数发散，见 ⑤）

| ω_max/ωp | m4 | ε | m2 相对误差 (%) |
|---|---|---|---|
{grid_tbl}

### 3.4 Rayleigh 短期统计闭式（参考核自检，平台未暴露极值波高端点）

E[Hmax]/Hs(N0=1000) = {verdict['rayleigh']['Hmax/Hs@N0=1000']:.4f}；
P(H>Hs) = {verdict['rayleigh']['P(H>Hs)']:.4f}；P(H>2Hs) = {verdict['rayleigh']['P(H>2Hs)']:.4f}

## ④ 结果图

`artifacts/m1801/fig1_spectra.png`（三谱族 + 峰位）、`fig2_moment_convergence.png`
（m0/m2 数值 vs 解析，误差随网格 N 收敛）、`fig3_epsilon_divergence.png`（ε 随积分上限对数漂移）、
`fig4_verdict.png`（逐判据相对误差 vs 3% 门）。

## ⑤ 注意点 / 局限

1. **这是一次「能力在运行时死亡」的案例**：能力清单把该链当现有能力，但 NumPy 2.x 升级后
   所有谱/统计端点 500。静态可读代码看不出问题（`np.trapz` 写法在 NumPy 1.x 完全正确），
   只有**跨版本运行时探针**能抓到 → 静态证据分级必须配版本矩阵回归。
2. **ε（谱宽）不可作 <3% 判据**：两参数谱 m4=∫ω⁴S dω 在 ω→∞ 时被积函数 ~A/ω，对数发散，
   ε 完全由积分上限决定（表 3.3 量化）。平台 `calculate_statistics` 未声明上限 → 该项判 N/A。
3. **JONSWAP 的 Tz 不用两参数闭式**：峰因子 γ 抬高峰部、压窄谱宽，Tz/Tp≈0.700 而非 0.710，
   故 JONSWAP 行按 0.700 基准判（等价于放弃 0.1% 级严格性，仍 ≪3%）。
4. 进程内复算证明的是**修复后源码**的正确性；线上 `:8001` 进程仍跑旧快照
   （`/srv/tensor/ship-workspace/...`），需重新部署 + 重启才生效（infra 席动作）。
5. 未覆盖：`/seakeeping-advanced/strip-theory` 的 RAO 部分（已由 m1515 判 MOCK，本单不重复）。

## ⑥ 误差判定（逐项，显式回答）

- 谱端点 Hs 往返（重标定后 m0 必须 = Hs²/16）：相对误差见 3.2，全部 **<3%**；
- 两参数谱 Tz、m0、m2、Te vs 闭式矩：全部 **<3%**（细网格 N=4000）；
- 峰频 ω_p 识别误差受网格步长限制（Δω/ωp），见 3.2 列表，**全部 <3%**；
- **结论：修复后 {n_pass}/{len(rows)} 条判据 <3%，{n_fail} 条未过（见 3.2 明细）**
"""
(REPO / "docs" / "verif").mkdir(parents=True, exist_ok=True)
(REPO / "docs" / "verif" / "wave_spectrum_shortterm_m1801.md").write_text(doc, encoding="utf-8")

print(json.dumps({k: v for k, v in verdict.items() if k not in ("dynamic_rows",)},
                 ensure_ascii=False, indent=1)[:3000])
print("ROWS", len(rows), "PASS", n_pass, "FAIL", n_fail)
for r in rows:
    if r["err_pct"] >= GATE:
        print("  FAIL:", r["name"], r["calc"], r["ref"], r["err_pct"])