# 服务侧承接单（:8001 ship-performance-platform）

> 在线服务源码位于 `ship-performance-platform/backend`，**不在本工作树**。本单给出逐函数替换与验收命令：
> 替换后 `python3 tests/audit/ship_api_conformance.py --target live` 应由
> `required 5/12 PASS` 转为 `12/12 PASS`（判据阈值不变）。替换代码均取自 `src/ship_perf/`（已通过 18 项 pytest）。

## 0. 接线（推荐）

把 `src/ship_perf/` 作为包放进服务仓（例如 `backend/methods/ship_perf/`），
`import` 后由 API 层薄封装。三处 API 只做 I/O 适配，物理一律下沉到该包。

## 1. D1 阻力（`api/resistance_advanced.py:203-226`、`api/resistance.py:74`）

**现状**：`Cr_base` 为四段线性式（Fn<0.2/<0.3/<0.4/else），水下回转体照常代入 ⇒ Cr=0.0256、Ct=0.0288；
组合式 `Rt = Rf + Rr + …` 缺 (1+k)。

**替换**：

```python
# api/resistance_advanced.py
from ship_perf import resistance as spr   # 见第 0 节

def calculate_holtrop_full(input_data):
    h = spr.HullForm(
        lwl=input_data.Lwl, lpp=input_data.Lpp, b=input_data.B, t=input_data.T,
        displacement=input_data.displacement, cb=input_data.Cb, cm=input_data.Cm,
        cwp=input_data.Cwp, bulb_area=input_data.bulb_area,
        transom_area=input_data.transom_area,
    )
    v = input_data.speed * 0.5144444
    try:
        p = spr.predict_resistance(
            h, v,
            # 附体面积若有则传入：appendages=[{"area":A,"cd":Cd,"interference":Q}, ...]
        )
    except spr.OutOfDomainError as e:
        # 越域必须显式拒绝：HTTP 422 + 明确原因，而不是返回一个数
        raise HTTPException(status_code=422, detail={"domain_error": e.reasons})
    return HoltropOutput(Rt=p.rt / 1000.0, Rf=p.rf / 1000.0,
                         Rr=p.rr / 1000.0, Pe=p.rt * v / 1000.0,
                         reynolds_number=p.re, froude_number=p.fn,
                         domain_warning=p.warnings)
```

要点：
1. **水下回转体判定**：`Cm≥0.995 且 B/T≈2 且 L/B≥6` ⇒ 走 `submerged_body_resistance`（无兴波项）；
2. 水面船改用 Holtrop(1984) 的 `Rw = c1c2c5∇ρg·exp(m1Fn^d+m2cos(λFn^-2))` 与 `(1+k)`（见 `src/ship_perf/resistance.py`）；
3. 越域（Cb∉[0.5,0.9]、L/B∉[3.9,14]、B/T∉[2.1,4]、Cp∉[0.55,0.85]）一律 422，不再静默外推。

**验收**：`A-CT` 由 FAIL(在线 Ct=0.02880/0.02465 vs 基准 0.00457) 转 PASS；`A-CR-SURF` 由 +417% 转 <30%。

## 2. D2 稳性 KB（`api/stability.py:50`、`api/stability_imo.py: calculate_kb`、`api/calculations.py: calculate_stability`）

**现状**：`evaluate` 用 `KB=T(1-0.5(1-Cb))`（Cb=1 ⇒ KB=T，+100%）；`calculations`/`stability_imo` 用 `KB=2T/3`（+33.3%）。
同一物理量两条路径两个错值。

**替换**（唯一实现，三处共用）：

```python
def kb_from_cb(t: float, cb: float) -> float:
    """梯形剖面一阶矩解析解：KB = T(1 + 1/(2Cb))/3。
    极限自洽：Cb=1（矩形浮箱）→ T/2；Cb=0.5（V 形楔）→ 2T/3。"""
    cb_eff = max(cb, 0.5)
    return t * (1.0 + 1.0 / (2.0 * cb_eff)) / 3.0

def bm_transverse(b: float, t: float, cb: float) -> float:      # I_wl/∇
    return b * b / (12.0 * t * cb)
```

`api/stability.py` 的 `evaluate` 与 `api/calculations.py` 的 `gm` 均改为
`GM = kb_from_cb(T,Cb) + bm_transverse(B,T,Cb) - KG`。

**验收**：`S-KB-BOX` 由 +33.34% 转 0%（KB=10.667→8.000 m @T=16）；`S-GM`（dGM/dKG=−1）保持 PASS。

## 3. D3 大倾角 GZ 与消失角（`api/stability_imo.py:119-196`、`api/calculations.py: calculate_stability`）

**现状**：`KN≈(BM sinφcosφ+KB sinφ)(1+0.001φ)` + `(1-0.002(φ-30))` + `0.95` 折减 + `max(0,GZ)` 截断 ⇒ 无 GZ=0 根；
消失角 `60+10·GM`。

**替换**：直接调用剖面裁剪积分（无需 KN 表）：

```python
from ship_perf import hydrostatics as sph

def calculate_gz_curve(input_data):
    return sph.evaluate_stability(
        b=input_data.B, t=input_data.T,
        d=getattr(input_data, "D", None) or 1.25 * input_data.T,   # 新增 depth 入参优先
        cb=input_data.Cb, kg=input_data.KG, cm=input_data.Cm,
        angles=[5.0 * i for i in range(19)],                        # 0…90°
    )   # 返回 kb/bm/gm/gz/vanishing_angle/areas；GZ 可为负
```

要点：
1. `GZ = -y_B·cosθ + (z_B - KG)·sinθ`，`(y_B,z_B)` 由**等体积水线**裁剪剖面求得（甲板入水、舭部出水、浮心横移全含）；
2. **不得** `max(0, GZ)`：稳定角之后必须返回负值，`vanishing_angle` 取首个 GZ=0 根；
3. 干舷 `depth` 为必需入参：无干舷时物理上无消失角，应返回 `null` 并说明，**不得**回落到 60°；
4. API schema 增加可选字段 `depth`（向后兼容：缺省按 `1.25T` 并置 `assumed_depth: true`）。

**验收**：`S-GZMAX` 由 +137.15% 转 0.00%（GZ_max 2.748→1.1588 m）；`S-AREA-3040` 由 +151.79% 转 0.00%
（0.4220→0.16760 m·rad）；`S-VANISH` 由 60.0°（常数）转 65.98°/75.39°（随 KG 变化，误差 <3°）。

## 4. D5/D6 推进（`api/calculations.py: calculate_propulsion`、`api/propulsion.py: predict`）

**现状**：`Kt=0.3-0.2J`、`Kq=0.04-0.02J`；转速由 `n=V/(0.7D)` 拍定，与阻力需求无关 ⇒ η0=1.545、KT=0.995、
跨端点推力/需求差 2.8×。服务内 `methods/propulsion/wageningen.py` 的合成系数表不可用（Z=4、P/D=1 时 KT(0)=−1.67）。

**替换**：

```python
from ship_perf import propeller as spp

def calculate_propulsion(inp):
    Rt_kN = <调用第 1 节阻力实现，同一算例与同一水物性>   # 必须与阻力端点同源
    pt = spp.solve_n_for_thrust(
        rt_n=Rt_kN * 1000.0, v_ms=inp.speed * 0.5144444, d=inp.prop_diameter,
        cb=inp.cb, z=inp.num_blades, pd=inp.prop_pitch / inp.prop_diameter,
        ae_ao=inp.blade_area_ratio,
    )
    return {
        "thrust": pt.thrust_N / 1000.0,            # kN
        "torque": pt.kq * RHO * pt.n_rps**2 * pt.d**5 / 1000.0,   # kN·m
        "delivered_power": pt.delivered_power_kw,  # kW == 2πnQ
        "open_water_eff": pt.eta0,                 # ≤ min(0.998, η_I)
        "thrust_deduction": pt.t, "wake_fraction": pt.w,
        "J": pt.j, "KT": pt.kt, "KQ": pt.kq, "P_D": pt.pd,
        "prop_rpm": pt.rpm, "equilibrium_error": pt.equilibrium_error,
    }
```

要点：
1. `prop_rpm` 若为**输入**，其语义应改为"给定转速下的**工况点**（check point）"：先算该转速下的 T、再与 Rt 比较并返回
   `required_rpm`／`equilibrium_error`，而不是让它与推力解耦；`prop_rpm` 为可选时按 `T(1-t)=Rt` 反解；
2. 伴流/推力减额采用 Taylor 单桨式 `w=0.5Cb-0.05`、`t=0.5Cb-0.12`，并在响应中回显，便于跨端点对账；
3. 幂次顺序不可省：`P_D = 2πnQ` 与 `torque` 必须出自同一 `KQ`。

**验收**：`P-ETA-BOUND` 由 1.545 转 0.5277（≤0.7553）；`P-KT` 由 0.995 转 0.2372；`P-THRUST` 由 +177.98% 转 0.00%；
`P-PD` 保持 PASS（P_D=2πnQ 自洽）。

## 5. 判据命中表（承接前后）

| 判据 | 承接前（在线） | 承接后（本仓参考实现） |
|---|---|---|
| A-CT | FAIL +529.9% | PASS |
| S-KB-BOX | FAIL +33.34% | PASS 0.00% |
| S-GZMAX | FAIL +137.15% | PASS 0.00% |
| S-AREA-3040 | FAIL +151.79% | PASS 0.00% |
| P-ETA-BOUND | FAIL 1.545 > 0.5189 | PASS 0.5277 ≤ 0.7553 |
| P-KT | FAIL 0.9950 | PASS 0.2372 |
| P-THRUST | FAIL +177.98% | PASS 0.00% |
| R-CF / R-RE / R-FN / S-GM / P-PD | PASS（须保持） | PASS |

## 6. 验收命令

```bash
python3 -m pytest tests/audit/test_ship_perf_physics.py -q          # 18 passed
python3 tests/audit/ship_api_conformance.py --target ref            # exit 0，12/12
python3 tests/audit/ship_api_conformance.py --target live --json docs/m1490/conformance_live.json
```