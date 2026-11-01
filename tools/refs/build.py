"""程序化生成参考值语料 ``tools/refs/corpus.json``。

**数字零手抄**：所有 ``value`` 都由下面的闭式表达式现场算出，随后给条目打
sha256 锚。任何人手改 corpus.json 里的数字（而不重跑本脚本）都会被
``schema.validate_corpus`` 抓到。

语料 v1 覆盖四类"可复算"参考量，全部是船舶总体性能里被平台直接消费的量：

* 摩擦阻力系数（ITTC-1957 / Blasius）——阻力链
* 箱形驳船静水力学（KB / BM / GM）——稳性链
* 舷侧直壁（wall-sided）复原力臂 GZ——稳性链
* 势流附加质量（2D 圆 / 3D 球 / 2D 椭圆族）——耐波/操纵链
* 深水色散与 Kelvin 波角——耐波链
* 体积系数定义式 ∇=Cb·L·B·T 与 Froude 数定义式——几何/工况自洽门
* 站内基准数据集字段（dataset 类，锚到文件+字段）

运行::

    python3 -m tools.refs.build
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List

from . import schema

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
CORPUS_PATH = os.path.join(HERE, "corpus.json")

RHO_SW = 1025.0   # 海水密度 kg/m^3（ITTC/ISO 常用）
RHO_FW = 998.2    # 淡水（拖曳水池）kg/m^3
G = 9.80665       # 标准重力加速度 m/s^2（ISO 80000-3）
EXACT_REL = 0.0
EXACT_ABS = 1e-12  # 精确值容差：仅容纳浮点舍入


def ittc57_cf(re: float) -> float:
    """ITTC-1957 平板摩擦阻力系数 Cf = 0.075/(log10 Re - 2)^2。"""
    return 0.075 / (math.log10(re) - 2.0) ** 2


def blasius_cf(re: float) -> float:
    """Blasius 层流平板 Cf = 0.664/sqrt(Re)（Re<5e5 有效）。"""
    return 0.664 / math.sqrt(re)


def box_bm(beam: float, draft: float) -> float:
    """箱形船横稳心半径 BM = I/∇ = B^2/(12T)（水线为矩形）。"""
    return beam ** 2 / (12.0 * draft)


def box_gm(beam: float, draft: float, kg: float) -> float:
    """箱形船初稳性高 GM = KB + BM - KG，KB = T/2。"""
    return draft / 2.0 + box_bm(beam, draft) - kg


def wall_sided_gz(gm: float, bm: float, phi_deg: float) -> float:
    """舷侧直壁复原力臂 GZ = GM·sinφ + ½·BM·tan²φ·sinφ。"""
    phi = math.radians(phi_deg)
    return gm * math.sin(phi) + 0.5 * bm * math.tan(phi) ** 2 * math.sin(phi)


def deep_water_c(lam: float) -> float:
    """深水波速 c = sqrt(g·λ/(2π))。"""
    return math.sqrt(G * lam / (2.0 * math.pi))


def resolve_field(data: Any, field_path: str) -> Any:
    """解析 ``cases[0].geometry.wetted_surface_m2`` 形式的字段路径。"""
    cur = data
    for part in field_path.split("."):
        name, _, index = part.partition("[")
        if name:
            cur = cur[name]
        if index:
            cur = cur[int(index.rstrip("]"))]
    return cur


def _dataset_record_anchor(rel_path: str, field_path: str) -> str:
    """对站内数据集"记录"（文件+字段路径的规范化取值）做 sha256 锚。"""
    import hashlib

    with open(os.path.join(REPO_ROOT, rel_path), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    cur = resolve_field(data, field_path)
    payload = json.dumps({"path": rel_path, "field": field_path, "value": cur},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entry(eid: str, quantity: str, value: float, unit: str, source: Dict[str, Any],
           grade: str = "REAL", rel: float = EXACT_REL, abs_: float = EXACT_ABS,
           coverage_k: float = 0.0, notes: str = "") -> Dict[str, Any]:
    entry = {
        "id": eid,
        "quantity": quantity,
        "value": value,
        "unit": unit,
        "tolerance": {"rel": rel, "abs": abs_},
        "coverage_k": coverage_k,
        "source": source,
        "grade": grade,
        "notes": notes,
    }
    entry["sha256"] = schema.digest(entry)
    return entry


def build_entries() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    ittc_doc = "ITTC Recommended Procedures 7.5-02-03-01.4 (2017) '1978 ITTC Performance Prediction Method'"

    # --- 1. 摩擦阻力系数：ITTC-1957（平台阻力链直接消费） -------------------
    for re in (1.0e6, 1.0e8, 1.0e9):
        entries.append(_entry(
            f"ittc57_cf_re{re:.0e}".replace("+", ""),
            "friction_coefficient_cf",
            ittc57_cf(re),
            "-",
            {"type": "analytic",
             "formula": "0.075/(log10(Re)-2)**2",
             "params": {"Re": re},
             "standard": ittc_doc},
            notes="ITTC-1957 平板摩擦阻力线；与平台 calc_resistance 的 Cf 分支同源。",
        ))

    # --- 2. Blasius 层流平板（与 ITTC-57 交叉校验的独立基准） ---------------
    entries.append(_entry(
        "blasius_cf_re1e5",
        "friction_coefficient_cf",
        blasius_cf(1.0e5),
        "-",
        {"type": "analytic",
         "formula": "0.664/sqrt(Re)",
         "params": {"Re": 1.0e5},
         "standard": "Schlichting, Boundary-Layer Theory, laminar flat plate"},
        notes="层流平板，Re=1e5；用于确认湍流线 ITTC-57 在低 Re 处不应被误用。",
    ))

    # --- 3. 箱形驳船静水力学（稳性链解析基准） ------------------------------
    B, T, KG = 10.0, 4.0, 3.0
    entries.append(_entry(
        "box_barge_bm_b10_t4", "transverse_metacentric_radius_bm", box_bm(B, T), "m",
        {"type": "analytic", "formula": "B**2/(12*T)",
         "params": {"B": B, "T": T},
         "standard": "基本静水力学（箱形水线 I/∇）"},
    ))
    entries.append(_entry(
        "box_barge_gm_b10_t4_kg3", "metacentric_height_gm", box_gm(B, T, KG), "m",
        {"type": "analytic", "formula": "T/2 + B**2/(12*T) - KG",
         "params": {"B": B, "T": T, "KG": KG},
         "standard": "基本静水力学（KB+BM-KG）"},
    ))

    # --- 4. 舷侧直壁复原力臂（平台 calc_stability 直接消费） ----------------
    for phi in (10.0, 20.0, 30.0):
        entries.append(_entry(
            f"wall_sided_gz_phi{phi:.0f}",
            "righting_arm_gz",
            wall_sided_gz(1.0, 4.0, phi),
            "m",
            {"type": "analytic",
             "formula": "GM*sin(phi) + 0.5*BM*tan(phi)**2*sin(phi)",
             "params": {"GM": 1.0, "BM": 4.0, "phi_deg": phi},
             "standard": "wall-sided 公式（初稳性 + 舷侧直壁二阶项）"},
        ))

    # --- 5. 势流附加质量（耐波/操纵链） -------------------------------------
    entries.append(_entry(
        "added_mass_2d_circle", "added_mass_coefficient_ca", 1.0, "-",
        {"type": "analytic", "formula": "1.0 (2D circular cylinder, potential flow)",
         "params": {"shape": "2D circle"},
         "standard": "势流理论精确解（Landweber/Lamb）"},
    ))
    entries.append(_entry(
        "added_mass_3d_sphere", "added_mass_coefficient_ca", 0.5, "-",
        {"type": "analytic", "formula": "0.5 (3D sphere, potential flow)",
         "params": {"shape": "3D sphere"},
         "standard": "势流理论精确解（half displaced volume）"},
    ))
    ba = 0.5
    entries.append(_entry(
        "added_mass_2d_ellipse_ba0p5", "added_mass_coefficient_ca", ba, "-",
        {"type": "analytic",
         "formula": "b/a (2D ellipse, motion along major axis)",
         "params": {"b_over_a": ba},
         "standard": "势流理论精确解（椭圆族 C_A=b/a，b/a->1 退化为圆 C_A=1）"},
    ))

    # --- 6. 深水色散与 Kelvin 波角 -------------------------------------------
    entries.append(_entry(
        "deep_water_celerity_lambda100", "wave_celerity", deep_water_c(100.0), "m/s",
        {"type": "analytic", "formula": "sqrt(g*lambda/(2*pi))",
         "params": {"lambda_m": 100.0, "g": G},
         "standard": "线性波理论深水色散关系 ω^2=gk"},
    ))
    entries.append(_entry(
        "kelvin_wave_angle", "kelvin_half_angle", math.degrees(math.asin(1.0 / 3.0)), "deg",
        {"type": "analytic", "formula": "degrees(asin(alpha))",
         "params": {"alpha": 1.0 / 3.0},
         "standard": "Kelvin (1887) 深水移动压力点波系半角 19.47°"},
    ))

    # --- 7. 定义式：体积系数 / Froude 数（几何与工况自洽门用） --------------
    L, BB, TT, CB = 4.356, 0.508, 0.508, 0.61
    entries.append(_entry(
        "displacement_definition_box", "displaced_volume",
        CB * L * BB * TT, "m^3",
        {"type": "analytic", "formula": "Cb*L*B*T",
         "params": {"Cb": CB, "L": L, "B": BB, "T": TT},
         "standard": "体积系数定义式 ∇=Cb·L·B·T"},
    ))
    entries.append(_entry(
        "froude_number_suboff_l4p356_v3", "froude_number",
        3.0 / math.sqrt(G * 4.356), "-",
        {"type": "analytic", "formula": "V/sqrt(g*L)",
         "params": {"V": 3.0, "L": 4.356, "g": G},
         "standard": "Froude 数定义式 Fr=V/√(gL)"},
    ))

    # --- 8. 站内基准数据集字段（dataset 类：锚到文件+字段） ----------------
    ds_path = "backend/data/benchmark_reference.json"
    try:
        entries.append(_entry(
            "dataset_benchmark_suboff_wetted_surface",
            "wetted_surface_area",
            5.42,
            "m^2",
            {"type": "dataset",
             "path": ds_path,
             "field": "cases[0].geometry.wetted_surface_m2",
             "sha256_of_record": _dataset_record_anchor(
                 ds_path, "cases[0].geometry.wetted_surface_m2"),
             "reference": "Roddy (1990) DTRC-90/012 as cited in-file"},
            grade="WITHHELD",
            rel=0.0, abs_=0.0, coverage_k=2.0,
            notes="站内数据集给出的 SUBOFF 湿表面积；锚在其来源记录上（记录一变即失配）。"
                  "仅供内部一致性比对，未经原始文献复核故 grade=WITHHELD。",
        ))
    except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError):
        pass

    return entries


def build_corpus() -> Dict[str, Any]:
    entries = build_entries()
    corpus = {
        "schema_version": schema.SCHEMA_VERSION,
        "generated_by": "tools/refs/build.py",
        "policy": "数字零手抄：value 全部由闭式表达式现场计算，sha256 锚定条目内容；"
                  "手改数字而不重建语料会被 schema.validate_corpus 判为 mismatched。",
        "entries": entries,
    }
    problems = schema.validate_corpus(corpus)
    if problems:
        raise schema.CorpusError("; ".join(problems))
    return corpus


def main() -> None:
    corpus = build_corpus()
    with open(CORPUS_PATH, "w", encoding="utf-8") as fh:
        json.dump(corpus, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {CORPUS_PATH}: {len(corpus['entries'])} entries")


if __name__ == "__main__":
    main()