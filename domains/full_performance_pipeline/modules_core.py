"""七域模块实现（上）：失速(N01)/污底(N02)/导航(N04)/风载参考点(N05)。

设计纪律
--------
* 每个模块 = 契约 `Contract`（字段/单位/类型）+ 逐字段证据登记
  `evidence[field] = (grade, rationale)`。证据等级：
  REAL=真实算法且本仓有解析/文献对照；WITHHELD=算法真实但绝对精度缺实验基准；
  MOCK=硬编码/占位；STUB=空壳。
* 物理式优先取**可自行推导**的形式（动量通量、附加质量率、球面几何、静水力学），
  经验系数一律外置为配置注入项并标 WITHHELD，避免把记不准的标准系数冒充“真实”。
* 内部量纲全 SI（N / m / s / kg）；kn / deg / h 由 contracts.ADAPTERS 显式换算。
* 坐标系（全包统一）：x 向船首，y 向左舷，z 向上；力矩按右手法则。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .contracts import REAL, WITHHELD, Contract, Field, GRADE_RANK

RHO_W = 1025.0     # kg/m^3 海水密度
NU_W = 1.19e-6     # m^2/s 运动黏度（~15 °C）
RHO_A = 1.225      # kg/m^3 空气密度
G = 9.80665        # m/s^2


class Module:
    """模块基类：契约 + 证据登记 + 计算入口。"""

    contract: Contract
    evidence: Dict[str, Tuple[str, str]] = {}

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def graded_provides(self) -> List[Dict[str, Any]]:
        out = []
        for f in self.contract.provides:
            grade, why = self.evidence.get(f.name, (WITHHELD, "未登记"))
            out.append({"module": self.contract.module_id, "field": f.name,
                        "unit": f.unit, "grade": grade, "rationale": why})
        return out

    def module_grade(self) -> str:
        """模块级证据 = 逐字段最弱项（保守取法，防单点 REAL 掩盖整体未验证）。"""
        grades = [g for g, _ in self.evidence.values()] or [WITHHELD]
        return min(grades, key=lambda g: GRADE_RANK[g])


# =====================================================================  N04 导航
class NavigationModule(Module):
    """大圆/等角航法几何 + 航时（球面解析几何，可对解析极限校验）。"""

    R_EARTH = 6371008.8  # m，平均地球半径

    contract = Contract(
        module_id="N04-navigation",
        title="导航：大圆/等角航法几何与航时",
        version="1.0.0",
        requires=(
            Field("lat_dep_deg", "deg", "float", "出发纬度"),
            Field("lon_dep_deg", "deg", "float", "出发经度"),
            Field("lat_arr_deg", "deg", "float", "到达纬度"),
            Field("lon_arr_deg", "deg", "float", "到达经度"),
            Field("speed_ms", "m/s", "float", "对地航速"),
        ),
        provides=(
            Field("gc_distance_nm", "nmi", "float", "大圆航程"),
            Field("rhumb_distance_nm", "nmi", "float", "等角（恒向线）航程"),
            Field("initial_course_deg", "deg", "float", "初始航向"),
            Field("eta_h", "h", "float", "航时"),
            Field("gc_distance_m", "m", "float", "大圆航程（SI）"),
            Field("speed_ms", "m/s", "float", "航速（透传）"),
        ),
        note="球面解析几何；与 Vincenty 椭球之差属地球模型差异（见 references.py）。",
    )
    evidence = {
        "gc_distance_nm": (REAL, "haversine 解析式，对赤道/子午线/对跖点解析极限校验"),
        "rhumb_distance_nm": (REAL, "Mercator 等角航法解析式；子午线情形应与大圆重合"),
        "initial_course_deg": (REAL, "方位角解析式，正交航向校验"),
        "eta_h": (REAL, "航程/航速定义式，对分段求和一致性校验"),
        "gc_distance_m": (REAL, "同 gc_distance_nm（SI 输出）"),
        "speed_ms": (REAL, "透传"),
    }

    @classmethod
    def gc_distance_m(cls, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp, dl = p2 - p1, math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2.0 * cls.R_EARTH * math.asin(min(1.0, math.sqrt(a)))

    @classmethod
    def initial_course_deg(cls, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dl = math.radians(lon2 - lon1)
        y = math.sin(dl) * math.cos(p2)
        x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    @classmethod
    def rhumb_distance_m(cls, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dl = math.radians(lon2 - lon1)
        if abs(dl) > math.pi:
            dl -= math.copysign(2 * math.pi, dl)
        dphi = p2 - p1
        if abs(dphi) > 1e-12:
            q = dphi / math.log(math.tan(math.pi / 4 + p2 / 2) / math.tan(math.pi / 4 + p1 / 2))
        else:
            q = math.cos(p1)
        return cls.R_EARTH * math.hypot(dphi, q * dl)

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        la1, lo1 = float(inputs["lat_dep_deg"]), float(inputs["lon_dep_deg"])
        la2, lo2 = float(inputs["lat_arr_deg"]), float(inputs["lon_arr_deg"])
        v = float(inputs["speed_ms"])
        d = self.gc_distance_m(la1, lo1, la2, lo2)
        return {
            "gc_distance_nm": d / 1852.0,
            "rhumb_distance_nm": self.rhumb_distance_m(la1, lo1, la2, lo2) / 1852.0,
            "initial_course_deg": self.initial_course_deg(la1, lo1, la2, lo2),
            "eta_h": d / v / 3600.0,
            "gc_distance_m": d,
            "speed_ms": v,
        }


# =====================================================================  N05 风载
class WindLoadModule(Module):
    """风载：视风矢量合成 + 三分量风载 + 参考点力矩（含平行轴换算）。

    力学骨架 REAL（视风合成、q=½ρVR²、平行轴力矩恒等式均有解析校验）；
    风压系数 C_X/C_Y/C_N 由配置注入的 Fourier 系数给出，未做模型试验标定 → WITHHELD。
    ψ = 视风“来向”相对船首角（0=正顶风，90=左舷来风）。
    """

    contract = Contract(
        module_id="N05-wind-load",
        title="风载：视风合成/三分量载荷/参考点力矩",
        version="1.0.0",
        requires=(
            Field("wind_speed_ms", "m/s", "float", "真风速（10 m 参考高度）"),
            Field("wind_from_dir_deg", "deg", "float", "真风来向（正北起顺时针）"),
            Field("ship_speed_ms", "m/s", "float", "对水航速"),
            Field("ship_heading_deg", "deg", "float", "船首向（正北起顺时针）"),
            Field("area_front_m2", "m2", "float", "水线以上正投影面积 A_T"),
            Field("area_lateral_m2", "m2", "float", "侧投影面积 A_L"),
            Field("lpp_m", "m", "float", "垂线间长（力矩系数参考长度）"),
            Field("x_ref_m", "m", "float", "参考点纵向位置（舯前为正）"),
            Field("z_ref_m", "m", "float", "参考点高度（水线以上）"),
            Field("x_centroid_m", "m", "float", "受风面积形心纵向位置"),
            Field("z_centroid_m", "m", "float", "受风面积形心高度"),
            Field("cx_coeff", "1", "array", "C_X(ψ) Fourier 系数"),
            Field("cy_coeff", "1", "array", "C_Y(ψ) Fourier 系数"),
            Field("cn_coeff", "1", "array", "C_N(ψ) Fourier 系数"),
            Field("ref_shift_m", "m", "float", "参考点平移量（平行轴校验用）"),
        ),
        provides=(
            Field("apparent_wind_speed_ms", "m/s", "float", "视风速"),
            Field("apparent_wind_dir_deg", "deg", "float", "视风来向（相对船首）"),
            Field("wind_x_force_N", "N", "float", "纵向风载（顶风为负=阻碍前进）"),
            Field("wind_y_force_N", "N", "float", "横向风载（左舷为正）"),
            Field("yaw_moment_Nm", "Nm", "float", "绕参考点艏摇力矩"),
            Field("heeling_moment_Nm", "Nm", "float", "横倾力矩（正=倾覆）"),
            Field("yaw_moment_shift_Nm", "Nm", "float", "绕平移后参考点力矩（平行轴恒等式）"),
        ),
        note="系数符号约定：C_X 顶风为负；C_Y 左舷为负；见模块 docstring。",
    )
    evidence = {
        "apparent_wind_speed_ms": (REAL, "视风矢量合成，对构造解析解校验（正交/对遇/静止船）"),
        "apparent_wind_dir_deg": (REAL, "同上"),
        "wind_x_force_N": (WITHHELD, "力学式真实但 C_X 为配置注入未标定系数（无模型试验拟合）"),
        "wind_y_force_N": (WITHHELD, "同上（C_Y）"),
        "yaw_moment_Nm": (WITHHELD, "C_N 同 C_Y；无试验标定"),
        "heeling_moment_Nm": (WITHHELD, "F_y·(z_c−z_ref)，依赖未标定 C_Y"),
        "yaw_moment_shift_Nm": (REAL, "平行轴力矩恒等式 N₂=N₁+Δx·F_y，机器精度校验"),
    }

    @staticmethod
    def apparent_wind(true_speed: float, from_dir: float, ship_speed: float,
                      heading: float) -> Tuple[float, float]:
        """返回 (视风速, 视风来向相对船首角 deg，0=正顶风，90=左舷来风)。"""
        r = math.radians
        wx, wy = -true_speed * math.sin(r(from_dir)), -true_speed * math.cos(r(from_dir))
        sx, sy = ship_speed * math.sin(r(heading)), ship_speed * math.cos(r(heading))
        rx, ry = wx - sx, wy - sy
        speed = math.hypot(rx, ry)
        from_x, from_y = -rx, -ry          # 来向 = 相对风速反向
        # 船首坐标系：x_b 向船首，y_b 向左舷（右手系，z 向上）
        xb = from_x * math.sin(r(heading)) + from_y * math.cos(r(heading))
        yb = -from_x * math.cos(r(heading)) + from_y * math.sin(r(heading))
        psi = (math.degrees(math.atan2(yb, xb)) + 360.0) % 360.0
        return speed, psi

    @staticmethod
    def _cos_series(psi_deg: float, c: Sequence[float]) -> float:
        p = math.radians(psi_deg)
        a = list(c) + [0.0] * 4
        return a[0] + a[1] * math.cos(p) + a[2] * math.cos(2 * p) + a[3] * math.cos(3 * p)

    @staticmethod
    def _sin_series(psi_deg: float, c: Sequence[float]) -> float:
        p = math.radians(psi_deg)
        a = list(c) + [0.0] * 3
        return a[0] * math.sin(p) + a[1] * math.sin(2 * p) + a[2] * math.sin(3 * p)

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        vr, psi = self.apparent_wind(float(inputs["wind_speed_ms"]),
                                     float(inputs["wind_from_dir_deg"]),
                                     float(inputs["ship_speed_ms"]),
                                     float(inputs["ship_heading_deg"]))
        q = 0.5 * RHO_A * vr * vr
        at, al = float(inputs["area_front_m2"]), float(inputs["area_lateral_m2"])
        lpp = float(inputs["lpp_m"])
        fx = q * at * self._cos_series(psi, inputs["cx_coeff"])
        fy = q * al * self._sin_series(psi, inputs["cy_coeff"])
        cn = q * al * lpp * self._sin_series(psi, inputs["cn_coeff"])
        dx = float(inputs["x_centroid_m"]) - float(inputs["x_ref_m"])
        dz = float(inputs["z_centroid_m"]) - float(inputs["z_ref_m"])
        yaw = cn + dx * fy            # 右手法则：M_z = (r × F)_z = Δx·F_y
        heeling = abs(dz * fy)        # 横倾（倾覆）力矩
        shift = float(inputs["ref_shift_m"])
        return {
            "apparent_wind_speed_ms": vr,
            "apparent_wind_dir_deg": psi,
            "wind_x_force_N": fx,
            "wind_y_force_N": fy,
            "yaw_moment_Nm": yaw,
            "heeling_moment_Nm": heeling,
            "yaw_moment_shift_Nm": yaw + shift * fy,
        }


# =====================================================================  N02 污底
class FoulingModule(Module):
    """污底：ITTC-57 摩擦线 + ITTC-78 粗糙度补贴；污损生长律为参数化占位（WITHHELD）。"""

    contract = Contract(
        module_id="N02-fouling",
        title="污底：摩擦阻力与粗糙度补贴",
        version="1.0.0",
        requires=(
            Field("lpp_m", "m", "float", "垂线间长"),
            Field("speed_ms", "m/s", "float", "航速"),
            Field("wetted_area_m2", "m2", "float", "湿表面积"),
            Field("form_factor_k", "1", "float", "形状因子 (1+k)"),
            Field("ks_hull_um", "um", "float", "新船涂层粗糙度 ks"),
            Field("days_in_service", "d", "float", "服役天数"),
            Field("fouling_coverage", "1", "float", "污损覆盖度 0..1"),
            Field("growth_rate_um_per_d", "um/d", "float", "污损生长率（配置注入）"),
        ),
        provides=(
            Field("reynolds", "1", "float", "雷诺数"),
            Field("cf_ittc57", "1", "float", "ITTC-57 摩擦系数"),
            Field("ks_effective_um", "um", "float", "有效粗糙度"),
            Field("dcf_roughness", "1", "float", "粗糙度补贴 ΔCF"),
            Field("friction_resistance_N", "N", "float", "摩擦阻力（含形状因子）"),
            Field("cf_total", "1", "float", "Cf+ΔCF"),
        ),
    )
    evidence = {
        "reynolds": (REAL, "Re=VL/ν 定义式"),
        "cf_ittc57": (REAL, "ITTC-57 相关线，对 Re=1e9 文献值 1.531e-3 与 Blasius 交叉校验"),
        "ks_effective_um": (WITHHELD, "ks(t)=ks0+γ·cov·t 线性律为占位，无实船污损时序数据标定"),
        "dcf_roughness": (REAL, "ITTC-78 ΔCF=(105(ks/L)^{1/3}−0.64)e-3，含解析适用下界"),
        "friction_resistance_N": (WITHHELD, "含未标定 ks 生长律；Cs=½ρSV²(1+k)(Cf+ΔCF) 部分为 REAL"),
        "cf_total": (REAL, "Cf+ΔCF 代数和"),
    }

    KS_VALID_COEF = 0.64 / 105.0  # ΔCF≥0 ⇔ ks/L ≥ (0.64/105)³

    @staticmethod
    def reynolds(speed_ms: float, lpp_m: float, nu: float = NU_W) -> float:
        return speed_ms * lpp_m / nu

    @staticmethod
    def cf_ittc57(re: float) -> float:
        return 0.075 / (math.log10(max(re, 1e3)) - 2.0) ** 2

    @classmethod
    def dcf_roughness(cls, ks_m: float, lpp_m: float) -> float:
        if ks_m <= 0:
            return 0.0
        return max(0.0, (105.0 * (ks_m / lpp_m) ** (1.0 / 3.0) - 0.64) * 1e-3)

    @classmethod
    def ks_min_valid_m(cls, lpp_m: float) -> float:
        return (cls.KS_VALID_COEF ** 3) * lpp_m

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        lpp, v = float(inputs["lpp_m"]), float(inputs["speed_ms"])
        re = self.reynolds(v, lpp)
        cf = self.cf_ittc57(re)
        ks_um = (float(inputs["ks_hull_um"]) +
                 float(inputs["growth_rate_um_per_d"]) * float(inputs["days_in_service"]) *
                 float(inputs["fouling_coverage"]))
        dcf = self.dcf_roughness(ks_um * 1e-6, lpp)
        rf = (0.5 * RHO_W * float(inputs["wetted_area_m2"]) * v * v *
              float(inputs["form_factor_k"]) * (cf + dcf))
        return {
            "reynolds": re,
            "cf_ittc57": cf,
            "ks_effective_um": ks_um,
            "dcf_roughness": dcf,
            "friction_resistance_N": rf,
            "cf_total": cf + dcf,
        }


# =====================================================================  N01 失速
class SpeedLossModule(Module):
    """失速（非自愿减速）：波浪增阻（动量通量渐近）+ 风增阻 + 功率平衡求可达航速。

    阻力分解：R(V) = R_friction(V) + a_res·V² + R_wave + R_wind，
    其中 R_friction(V) = R_f,ref · Cf(V)/Cf(V_ref)（ITTC-57 速度标定），R_f,ref 来自 N02。
    功率平衡 P_D·η_D = R(V)·V 用“解析三次根 + Cf(V) 固定点”求解。
    """

    contract = Contract(
        module_id="N01-speed-loss",
        title="失速：波浪/风增阻与功率平衡",
        version="1.1.0",
        requires=(
            Field("friction_resistance_N", "N", "float", "参考航速下的摩擦阻力（来自 N02）"),
            Field("residuary_coef_a", "1", "float", "剩余阻力系数 a：R_res=a·V²（配置注入）"),
            Field("available_power_kw", "kW", "float", "可用推进功率"),
            Field("propulsive_efficiency", "1", "float", "准推进效率 η_D"),
            Field("speed_reference_ms", "m/s", "float", "静水参考航速"),
            Field("lpp_m", "m", "float", "垂线间长（Cf 速度标定用）"),
            Field("wave_height_m", "m", "float", "有义波高 H_s"),
            Field("wave_peak_period_s", "s", "float", "谱峰周期 T_p"),
            Field("wave_rel_heading_deg", "deg", "float", "浪向相对船首（180=顶浪）"),
            Field("beam_m", "m", "float", "型宽"),
            Field("wind_x_force_N", "N", "float", "纵向风载（来自 N05，顶风为负）"),
            Field("reflection_efficiency", "1", "float", "波浪反射效率 κ（配置注入）"),
        ),
        provides=(
            Field("added_resistance_wave_N", "N", "float", "波浪增阻"),
            Field("added_resistance_wind_N", "N", "float", "风增阻"),
            Field("total_resistance_N", "N", "float", "总阻力"),
            Field("achievable_speed_ms", "m/s", "float", "功率平衡可达航速"),
            Field("speed_loss_pct", "%", "float", "失速百分比"),
            Field("required_power_kw", "kW", "float", "该航速所需功率"),
            Field("power_balance_residual", "1", "float", "功率平衡相对残差"),
        ),
    )
    evidence = {
        "added_resistance_wave_N": (WITHHELD,
                                    "短波动量通量渐近 R=∫2ρgBκ·w(ω)S(ω)dω + 长波截断参数化；"
                                    "无模型试验标定，绝对量级仅级次可信；m0 归一/线性性质可精确校验"),
        "added_resistance_wind_N": (WITHHELD, "继承 N05 未标定风压系数"),
        "total_resistance_N": (WITHHELD, "分项含未标定项"),
        "achievable_speed_ms": (REAL, "三次方程解析根 vs 二分法全非线性解，相对差 <1e-9"),
        "speed_loss_pct": (REAL, "定义式，随 achievable_speed 精度"),
        "required_power_kw": (REAL, "P=R·V/η_D 定义式，残差可校验"),
        "power_balance_residual": (REAL, "功率平衡残差，直接可算"),
    }

    @staticmethod
    def spectrum(omega: float, hs: float, tp: float) -> float:
        """Bretschneider 型谱，由 m0=Hs²/16 与谱峰条件自洽导出（非引用记忆系数）。"""
        if omega <= 0:
            return 0.0
        wp = 2.0 * math.pi / tp
        return (5.0 * hs * hs * wp ** 4 / 16.0) * omega ** -5 * \
            math.exp(-5.0 * wp ** 4 / (4.0 * omega ** 4))

    @classmethod
    def spectrum_m0(cls, hs: float, tp: float, n: int = 20000) -> float:
        wp = 2.0 * math.pi / tp
        lo, hi = 0.02 * wp, 20.0 * wp
        dw = (hi - lo) / (n - 1)
        return sum(cls.spectrum(lo + i * dw, hs, tp) for i in range(n)) * dw

    @classmethod
    def added_resistance_wave(cls, hs: float, tp: float, beam: float, lpp: float,
                              rel_heading_deg: float, kappa: float, n: int = 801) -> float:
        """R_AW = ∫ 2ρgB·κ·w(ω)·cos^1.5(Δθ)·S(ω)dω（顶浪渐近；λ≫L 分量被 w 抑制）。"""
        if hs <= 0 or tp <= 0:
            return 0.0
        head = max(0.0, math.cos(math.radians(180.0 - rel_heading_deg)))
        if head <= 1e-9:      # 横浪及顺浪段：无波浪反射增阻（本模型假设）
            return 0.0
        wp = 2.0 * math.pi / tp
        lo, hi = 0.05 * wp, 8.0 * wp
        dw = (hi - lo) / (n - 1)
        w_cut = math.sqrt(2.0 * math.pi * G / lpp)
        acc = 0.0
        for i in range(n):
            w = lo + i * dw
            wf = 1.0 / (1.0 + (w_cut / w) ** 4)      # 长波（λ≫L）不反射
            acc += 2.0 * RHO_W * G * beam * kappa * wf * cls.spectrum(w, hs, tp)
        return acc * dw * head ** 1.5

    @staticmethod
    def solve_speed(power_w: float, res_add_n: float, a: float, eta: float) -> float:
        """解 a·V³ + R_add·V − P·η = 0 的解析根（R(V)=aV²+R_add）。"""
        if a <= 0:
            return power_w * eta / max(res_add_n, 1e-9)
        c = max(res_add_n, 1e-9) / a
        d = power_w * eta / a
        return 2.0 * math.sqrt(c / 3.0) * math.sinh(
            math.asinh((3.0 * d / (2.0 * c)) * math.sqrt(3.0 / c)) / 3.0)

    @staticmethod
    def _cf_ratio(v: float, v_ref: float, lpp: float) -> float:
        if v <= 0.05 or v_ref <= 0.05:
            return 1.0
        return (FoulingModule.cf_ittc57(FoulingModule.reynolds(v, lpp)) /
                FoulingModule.cf_ittc57(FoulingModule.reynolds(v_ref, lpp)))

    @classmethod
    def resistance(cls, v: float, friction_ref: float, a_res: float, r_add: float,
                   v_ref: float, lpp: float) -> float:
        """R(V)=R_f,ref·Cf(V)/Cf(V_ref)+a_res·V²+R_add。"""
        return friction_ref * cls._cf_ratio(v, v_ref, lpp) + a_res * v * v + r_add

    def compute(self, inputs: Mapping[str, Any]) -> Dict[str, Any]:
        hs = float(inputs["wave_height_m"])
        lpp = float(inputs["lpp_m"])
        r_wave = self.added_resistance_wave(hs, float(inputs["wave_peak_period_s"]),
                                            float(inputs["beam_m"]), lpp,
                                            float(inputs["wave_rel_heading_deg"]),
                                            float(inputs["reflection_efficiency"]))
        r_wind = max(0.0, -float(inputs["wind_x_force_N"]))
        fric_ref = float(inputs["friction_resistance_N"])
        a_res = float(inputs["residuary_coef_a"])
        eta = float(inputs["propulsive_efficiency"])
        p_av = float(inputs["available_power_kw"]) * 1000.0
        v_ref = float(inputs["speed_reference_ms"])
        r_add = r_wave + r_wind
        v = self.solve_speed(p_av, fric_ref / max(self._cf_ratio(v_ref, v_ref, lpp), 1e-12) + r_add,
                             a_res, eta)
        for _ in range(60):                      # Cf(V) 固定点
            v_next = self.solve_speed(p_av, fric_ref * self._cf_ratio(v, v_ref, lpp) + r_add,
                                      a_res, eta)
            if abs(v_next - v) <= 1e-12 * max(1.0, v):
                v = v_next
                break
            v = v_next
        r_total = self.resistance(v, fric_ref, a_res, r_add, v_ref, lpp)
        p_req = r_total * v / eta / 1000.0
        return {
            "added_resistance_wave_N": r_wave,
            "added_resistance_wind_N": r_wind,
            "total_resistance_N": r_total,
            "achievable_speed_ms": v,
            "speed_loss_pct": 100.0 * (v_ref - v) / v_ref,
            "required_power_kw": p_req,
            "power_balance_residual": (p_req - p_av / 1000.0) / (p_av / 1000.0),
        }
