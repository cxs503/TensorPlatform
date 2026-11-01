"""参考值语料一致性审计（CLI 入口）+ 平台公式对解析基准复核。

审计对象
--------
A. ``backend/data/benchmark_reference.json``——被 ``benchmark_validate`` 直接消费的基准数据
B. ``backend/api/domain/ship_benchmark.py`` 的 ``SHIP_GEOMETRY`` 几何预设
C. ``backend/api/calculations.py`` 的阻力/稳性公式（对 ``tools/refs/corpus.json`` 解析基准）

门定义见 ``backend/services/benchmark_integrity.py``（G1–G3/G4b，纯算术、可被运行时复用）
；本模块补充 G4a（溯源完整）与 G5（平台公式对解析基准）。

运行::

    python3 -m tools.refs.audit          # 写 artifacts/verifier/refs_audit_m1396.json + 图
"""
from __future__ import annotations

import ast
import asyncio
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

from . import schema

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
CORPUS_PATH = os.path.join(HERE, "corpus.json")
BENCHMARK_PATH = os.path.join(REPO_ROOT, "backend", "data", "benchmark_reference.json")
SHIP_BENCHMARK_SRC = os.path.join(REPO_ROOT, "backend", "api", "domain", "ship_benchmark.py")
ARTIFACT_DIR = os.path.join(REPO_ROOT, "artifacts", "verifier")

BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import benchmark_integrity as bi  # noqa: E402  (路径注入后再导入)

G = bi.G_STANDARD
FR_TOL_REL = bi.FR_TOL_REL
CB_TOL_REL = bi.CB_TOL_REL
RHO_WATER_RANGE = bi.RHO_WATER_RANGE
_finding = bi.finding
_import_calculations = None  # 见下方定义


# ---------------------------------------------------------------------------
# 数据装载
# ---------------------------------------------------------------------------
def load_benchmark(path: str = BENCHMARK_PATH) -> Dict[str, Any]:
    return bi.load_benchmark(path)


def load_ship_geometry_presets(path: str = SHIP_BENCHMARK_SRC) -> Dict[str, Dict[str, Any]]:
    """AST 静态读取 ship_benchmark.py 的 SHIP_GEOMETRY 字面量（不 import 模块）。"""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SHIP_GEOMETRY":
                    return ast.literal_eval(node.value)
    raise LookupError("SHIP_GEOMETRY not found")


# ---------------------------------------------------------------------------
# G4a 溯源完整性
# ---------------------------------------------------------------------------
def gate_provenance(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for case in cases:
        cid = case.get("case_id", "?")
        src = case.get("source_citation")
        if not src or len(str(src)) < 20:
            out.append(_finding("G4a", cid, "WARN", "source_citation", src, None,
                                "缺可核对的来源引用（文献/报告编号）",
                                "补 DOI/报告号/页表位置，方可升 REAL"))
        else:
            out.append(_finding("G4a", cid, "INFO", "source_citation",
                                str(src).split(".")[0][:60], None, "来源引用齐备"))
    return out


# ---------------------------------------------------------------------------
# G5 平台公式 vs 语料解析基准
# ---------------------------------------------------------------------------
def _import_calculations():
    """导入 backend.api.calculations（stub psycopg2，避免测试期连库）。"""
    import importlib
    import types

    if "psycopg2" not in sys.modules:
        stub = types.ModuleType("psycopg2")
        stub.__version__ = "2.9.9"
        ext = types.ModuleType("psycopg2.extensions")
        ext.connection = None
        ext.cursor = None
        stub.extensions = ext
        sys.modules["psycopg2"] = stub
        sys.modules["psycopg2.extensions"] = ext
    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    return importlib.import_module("api.calculations")


def platform_wetted_surface(length: float, beam: float, cb: float) -> float:
    """平台 calc_resistance 内的湿表面积近似式（原样复刻，用于定位差异来源）。"""
    return math.pi * beam * length * (0.5 + 0.25 * cb)


def gate_platform_formulas(corpus: Dict[str, Any],
                           data: Optional[Dict[str, Any]] = None,
                           presets: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    idx = schema.index_by_id(corpus)
    L, Bm, cb, rho, nu = 4.356, 0.508, 0.61, 998.2, 1.0e-6

    # ---- 5a. ITTC-57：取 Re 与语料条目相同的工况做同 Re 取证 --------------
    # 平台响应把阻力取整到 4 位小数，故判据为 |ΔRf| <= 1e-4 + 1e-9·Rf。
    try:
        calcs = _import_calculations()
        req_cls = getattr(calcs, "ResistanceRequest")
        entry = idx["ittc57_cf_re1e06"]
        re_target = float(entry["source"]["params"]["Re"])
        speed = nu * re_target / L
        resp = asyncio.run(calcs.calc_resistance(req_cls(
            ship_type="SUBOFF", length=L, beam=Bm, draft=Bm,
            cb=cb, speed_ms=speed, water_density=rho, kinematic_viscosity=nu)))
        s_plat = platform_wetted_surface(L, Bm, cb)
        rf_plat = float(resp.frictional_resistance_n)
        rf_ref = 0.5 * rho * s_plat * float(entry["value"]) * speed * speed
        band = 1e-4 + 1e-9 * rf_ref
        d_rf = abs(rf_plat - rf_ref)
        cf_eff = rf_plat / (0.5 * rho * s_plat * speed * speed)
        out.append(_finding(
            "G5a", "platform:calc_resistance", "INFO" if d_rf <= band else "FAIL",
            "friction_coefficient_cf", entry["value"], cf_eff,
            f"同 Re=1e6 取证（V={speed:.6f} m/s，S={s_plat:.4f} m²）：平台 Rf={rf_plat:.6f} N "
            f"vs ITTC-57 解析基准 Rf={rf_ref:.6f} N，偏差 {d_rf:.2e} N（带宽 {band:.2e}）；"
            f"Cf 反解 {cf_eff:.9f} vs {entry['value']:.9f}，相对误差 "
            f"{schema.relative_error(cf_eff, float(entry['value'])):.2e}",
            residual_rel=d_rf / rf_ref if rf_ref else None))

        # ---- 5d. 平台自报 Froude 数 vs 定义式（独立 3 m/s 取证） --------
        resp_fr = asyncio.run(calcs.calc_resistance(req_cls(
            ship_type="SUBOFF", length=L, beam=Bm, draft=Bm,
            cb=cb, speed_ms=3.0, water_density=rho, kinematic_viscosity=nu)))
        fr_plat = float(resp_fr.froude_number)
        fr_ref = bi.froude(3.0, L)
        d_fr = abs(fr_plat - fr_ref)
        if d_fr <= 1e-6:
            sev, note = "INFO", f"平台自报 Fr={fr_plat:.6f} 与定义式一致"
        elif d_fr <= 1e-3:
            sev, note = "WARN", (f"平台自报 Fr={fr_plat:.6f} vs 定义式 {fr_ref:.6f}，"
                                 f"偏差 {schema.relative_error(fr_plat, fr_ref):.1e}——"
                                 f"源于 g 取 9.81 而非 9.80665 的常量口径漂移，"
                                 f"远小于工程容差但常量来源应统一")
        else:
            sev, note = "FAIL", (f"平台自报 Fr={fr_plat:.6f} vs 定义式 {fr_ref:.6f}，"
                                 f"相对偏差 {schema.relative_error(fr_plat, fr_ref):.1e}")
        out.append(_finding("G5d", "platform:calc_resistance", sev,
                            "froude_number", fr_ref, fr_plat, note,
                            residual_rel=schema.relative_error(fr_plat, fr_ref)))
    except Exception as exc:  # noqa: BLE001 - 取证失败如实记录，不静默跳过
        out.append(_finding("G5a", "platform:calc_resistance", "WARN",
                            "friction_coefficient_cf", None, None,
                            f"平台阻力端点取证失败：{type(exc).__name__}: {exc}",
                            "检查 backend 依赖/接口签名漂移"))

    # ---- 5b. 稳性 GZ vs wall-sided 解析基准 ------------------------------
    try:
        calcs = _import_calculations()
        req_cls = getattr(calcs, "StabilityRequest")
        fields = getattr(req_cls, "model_fields", {})
        kwargs = {"ship_type": "BOX", "heel_angle_deg": 20.0}
        for name, default in (("gm_m", 1.0), ("bm_m", 4.0), ("displacement_t", 1000.0),
                              ("beam", 10.0), ("draft", 4.0)):
            if name in fields:
                kwargs[name] = default
        resp = asyncio.run(calcs.calc_stability(req_cls(**kwargs)))
        gz_plat = float(resp.righting_arm_m)
        entry = idx["wall_sided_gz_phi20"]
        band = 5e-7 + 1e-12 * abs(float(entry["value"]))   # 响应取整到 6 位小数
        d_gz = abs(gz_plat - float(entry["value"]))
        out.append(_finding(
            "G5b", "platform:calc_stability", "INFO" if d_gz <= band else "FAIL",
            "righting_arm_gz", float(entry["value"]), gz_plat,
            f"平台 GZ(20°)={gz_plat:.6f} m vs wall-sided 解析基准 {entry['value']:.6f} m"
            f"（GM=1.0, BM=4.0），偏差 {d_gz:.2e} m（带宽 {band:.2e}），相对误差 "
            f"{schema.relative_error(gz_plat, float(entry['value'])):.2e}",
            residual_rel=schema.relative_error(gz_plat, float(entry["value"]))))
    except Exception as exc:  # noqa: BLE001
        out.append(_finding("G5b", "platform:calc_stability", "WARN",
                            "righting_arm_gz", None, None,
                            f"平台稳性端点取证失败：{type(exc).__name__}: {exc}",
                            "检查 backend 依赖/接口签名漂移"))

    # ---- 5c. 平台湿表面积式 vs 基准数据集自带 S --------------------------
    for case in (data or {}).get("cases", []):
        geo = case.get("geometry") or {}
        s_ds = geo.get("wetted_surface_m2")
        length = geo.get("length_m")
        beam = geo.get("beam_m") or geo.get("diameter_m")
        cb_val = geo.get("cb")
        cb_src = "case.geometry.cb"
        if cb_val is None:
            preset = (presets or {}).get(case.get("ship_type", ""), {})
            cb_val = preset.get("cb")
            cb_src = f"SHIP_GEOMETRY['{case.get('ship_type')}'].cb（预设，见 G4b）"
        if s_ds is None or length is None or beam is None or cb_val is None:
            continue
        s_plat = platform_wetted_surface(float(length), float(beam), float(cb_val))
        res = bi.relative_error(s_plat, float(s_ds))
        out.append(_finding(
            "G5c", case.get("case_id", "?"),
            "FAIL" if res > bi.WETTED_SURFACE_TOL_REL else "INFO",
            "wetted_surface_area", float(s_ds), round(s_plat, 4),
            f"平台 S=πBL(0.5+0.25Cb)={s_plat:.3f} m² vs 数据集 S={s_ds} m²，偏差 {res:.1%}"
            f"（Cb 取自 {cb_src}；Rf∝S，误差线性传入阻力）",
            "S 的拟合系数 (0.5+0.25Cb) 无文献出处，且与站内基准不自洽："
            "须以母型船型线或 ITTC 推荐式重锚，或声明 WITHHELD",
            residual_rel=res))
    return out


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def run_audit() -> Dict[str, Any]:
    corpus = schema.load_corpus(CORPUS_PATH)
    data = load_benchmark()
    presets = load_ship_geometry_presets()
    cases = data.get("cases", []) or []

    findings: List[Dict[str, Any]] = []
    findings += bi.gate_froude(cases)
    findings += bi.gate_ct_resistance(cases)
    findings += bi.gate_cb_displacement(cases)
    findings += gate_provenance(cases)
    findings += bi.preset_contamination(presets)
    findings += gate_platform_formulas(corpus, data, presets)

    summary = bi.summarise(findings)
    grades: Dict[str, int] = {}
    for e in corpus["entries"]:
        grades[e["grade"]] = grades.get(e["grade"], 0) + 1

    return {
        "schema_version": "1.0",
        "audit": "refs-corpus-consistency",
        "mission": "m1396",
        "corpus": {
            "path": os.path.relpath(CORPUS_PATH, REPO_ROOT),
            "entries": len(corpus["entries"]),
            "grades": grades,
        },
        "targets": {
            "benchmark_dataset": os.path.relpath(BENCHMARK_PATH, REPO_ROOT),
            "benchmark_cases": len(cases),
            "geometry_presets": sorted(presets),
            "platform_modules": ["backend/api/calculations.py",
                                 "backend/api/domain/ship_benchmark.py",
                                 "backend/services/benchmark_integrity.py"],
        },
        "thresholds": {"froude_tol_rel": FR_TOL_REL, "cb_tol_rel": CB_TOL_REL,
                       "wetted_surface_tol_rel": bi.WETTED_SURFACE_TOL_REL,
                       "water_density_range": list(RHO_WATER_RANGE),
                       "g": G},
        "summary": summary,
        "findings": findings,
    }


def write_artifact(result: Dict[str, Any], path: Optional[str] = None) -> str:
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    path = path or os.path.join(ARTIFACT_DIR, "refs_audit_m1396.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def make_figures(result: Dict[str, Any], outdir: Optional[str] = None) -> List[str]:
    """图1 各门严重度分布；图2 G1/G3 残差；图3 G5 平台-解析 parity。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir = outdir or ARTIFACT_DIR
    os.makedirs(outdir, exist_ok=True)
    paths: List[str] = []
    findings = result["findings"]

    gates = sorted({f["gate"] for f in findings})
    def _count(gate: str, sev: str) -> int:
        return sum(1 for f in findings if f["gate"] == gate and f["severity"] == sev)

    infos = [_count(g, "INFO") for g in gates]
    warns = [_count(g, "WARN") for g in gates]
    fails = [_count(g, "FAIL") for g in gates]

    fig, ax = plt.subplots(figsize=(8.0, 4.2), dpi=150)
    ax.bar(gates, infos, color="#3aa757", label="INFO (pass)")
    ax.bar(gates, warns, bottom=infos, color="#f2a33c", label="WARN (provenance)")
    ax.bar(gates, fails, bottom=[i + w for i, w in zip(infos, warns)],
           color="#d1473f", label="FAIL (inconsistent)")
    ax.set_ylabel("findings")
    ax.set_title("Refs corpus audit (m1396): findings per gate")
    ax.legend(fontsize=8)
    for i, (f_, w_, in_) in enumerate(zip(fails, warns, infos)):
        if f_:
            ax.text(i, in_ + w_ + f_ + 0.15, str(f_), ha="center",
                    color="#d1473f", fontsize=8)
    fig.tight_layout()
    p1 = os.path.join(outdir, "audit_per_gate.png")
    fig.savefig(p1)
    plt.close(fig)
    paths.append(p1)

    by_key: Dict[tuple, float] = {}
    for f in findings:
        if f["gate"] in ("G1", "G3") and f.get("residual_rel") is not None:
            by_key[(f["gate"], f["case_id"])] = float(f["residual_rel"])
    cases: List[str] = []
    for _, cid in sorted(by_key, key=lambda kv: kv[1]):
        if cid not in cases:
            cases.append(cid)
    if cases:
        g1 = [by_key.get(("G1", c), float("nan")) * 100 for c in cases]
        g3 = [by_key.get(("G3", c), float("nan")) * 100 for c in cases]
        fig, ax = plt.subplots(figsize=(9.2, 4.2), dpi=150)
        xs = list(range(len(cases)))
        ax.semilogy(xs, g1, "o-", label="G1 |Fr_decl-Fr_calc|/Fr")
        ax.semilogy(xs, g3, "s-", label="G3 |Cb_decl-Cb_implied|/Cb")
        ax.axhline(FR_TOL_REL * 100, color="#2f7d3a", ls="--", lw=1, label="G1 tol 1%")
        ax.axhline(CB_TOL_REL * 100, color="#7a7a22", ls=":", lw=1, label="G3 tol 2%")
        ax.set_xticks(xs)
        ax.set_xticklabels(cases, rotation=60, ha="right", fontsize=7)
        ax.set_ylabel("relative residual [%]")
        ax.set_title("Internal-consistency residuals: benchmark reference dataset")
        ax.legend(fontsize=7)
        fig.tight_layout()
        p2 = os.path.join(outdir, "audit_residuals.png")
        fig.savefig(p2)
        plt.close(fig)
        paths.append(p2)

    g5 = [f for f in findings if f["gate"].startswith("G5")
          and f.get("computed") is not None and f["severity"] != "WARN"]
    if g5:
        fig, ax = plt.subplots(figsize=(5.6, 4.8), dpi=150)
        for f in g5:
            ref, calc = float(f["declared"]), float(f["computed"])
            ax.plot([ref], [calc], "o", ms=7,
                    color="#3aa757" if f["severity"] == "INFO" else "#d1473f")
            ax.annotate(f"{f['gate']}:{f['quantity'][:12]}", (ref, calc),
                        textcoords="offset points", xytext=(6, -9), fontsize=7)
        vals = [v for f in g5 for v in (float(f["declared"]), float(f["computed"]))]
        lo, hi = min(vals), max(vals)
        ax.plot([lo, hi], [lo, hi], "k--", lw=1, label="y = x (exact)")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("analytic reference (corpus)")
        ax.set_ylabel("platform computed")
        ax.set_title("G5 platform vs analytic reference (parity)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        p3 = os.path.join(outdir, "audit_g5_parity.png")
        fig.savefig(p3)
        plt.close(fig)
        paths.append(p3)
    return paths




# ---------------------------------------------------------------------------
# 文档渲染（数字全部取自审计工件，零手抄）
# ---------------------------------------------------------------------------
DOCS_DIR = os.path.join(REPO_ROOT, "docs", "verifier")
LEDGER_MD = os.path.join(DOCS_DIR, "refs_debt_ledger.md")

_GATE_TITLES = {
    "G1": "G1 Froude 自洽（Fr=V/√gL）",
    "G2": "G2 Ct–Rt–ρ 自洽（反解隐含水密度）",
    "G3": "G3 Cb–排水量–L·B·T 自洽",
    "G4a": "G4a 溯源引用完整性",
    "G4b": "G4b 几何预设跨字段污染",
    "G5a": "G5a 平台 ITTC-57 Cf vs 解析基准（同 Re）",
    "G5b": "G5b 平台 wall-sided GZ vs 解析基准",
    "G5c": "G5c 平台湿表面积式 vs 基准数据集 S",
    "G5d": "G5d 平台自报 Fr vs 定义式",
}


def render_ledger_markdown(result: Dict[str, Any]) -> str:
    """把审计结果渲染成 Markdown 台账（全部数字来自 result，不手抄）。"""
    summary = result["summary"]
    counts = summary["counts"]
    lines: List[str] = []
    lines.append("# 参考值一致性欠账台账（自动生成，勿手改）")
    lines.append("")
    lines.append(f"- 任务：{result['mission']}　审计器：`tools/refs/audit.py`")
    lines.append(f"- 语料：`{result['corpus']['path']}`，{result['corpus']['entries']} 条"
                 f"（分级 {result['corpus']['grades']}）")
    lines.append(f"- 受审对象：`{result['targets']['benchmark_dataset']}`"
                 f"（{result['targets']['benchmark_cases']} 个工况）；"
                 f"几何预设 {result['targets']['geometry_presets']}")
    lines.append(f"- 门限：{json.dumps(result['thresholds'], ensure_ascii=False)}")
    lines.append("")
    lines.append(f"**总判定：{summary['verdict']}** — {summary['message']}")
    lines.append("")
    lines.append(f"计数：FAIL={counts.get('FAIL', 0)}　WARN={counts.get('WARN', 0)}　"
                 f"INFO={counts.get('INFO', 0)}")
    lines.append("")
    lines.append("| 门 | FAIL | WARN | INFO |")
    lines.append("|---|---|---|---|")
    for gate in sorted(summary["per_gate"]):
        row = summary["per_gate"][gate]
        lines.append(f"| {_GATE_TITLES.get(gate, gate)} | {row['FAIL']} | {row['WARN']} | {row['INFO']} |")
    lines.append("")
    lines.append("## FAIL 明细（参考记录内部矛盾，下游判决不可信）")
    lines.append("")
    lines.append("| 门 | 案例 | 量 | 声明值 | 计算/反解值 | 相对残差 | 说明 |")
    lines.append("|---|---|---|---|---|---|---|")
    for f in result["findings"]:
        if f["severity"] != "FAIL":
            continue
        res = f.get("residual_rel")
        res_s = f"{res:.4g}" if isinstance(res, (int, float)) else "—"
        decl = f.get("declared")
        comp = f.get("computed")
        decl_s = f"{decl:.6g}" if isinstance(decl, (int, float)) else str(decl)[:40]
        comp_s = f"{comp:.6g}" if isinstance(comp, (int, float)) else str(comp)[:40]
        note = str(f.get("note", "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {f['gate']} | {f['case_id']} | {f['quantity']} | {decl_s} | {comp_s} "
                     f"| {res_s} | {note} |")
    lines.append("")
    lines.append("## WARN 明细（证据不足，不可升 REAL）")
    lines.append("")
    lines.append("| 门 | 案例 | 量 | 说明 |")
    lines.append("|---|---|---|---|")
    for f in result["findings"]:
        if f["severity"] != "WARN":
            continue
        note = str(f.get("note", "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {f['gate']} | {f['case_id']} | {f['quantity']} | {note} |")
    lines.append("")
    lines.append("## 建议处置（按门聚合）")
    lines.append("")
    seen_actions = set()
    for f in result["findings"]:
        action = f.get("action") or ""
        if f["severity"] == "INFO" or not action or (f["gate"], action) in seen_actions:
            continue
        seen_actions.add((f["gate"], action))
        lines.append(f"- **{_GATE_TITLES.get(f['gate'], f['gate'])}**：{action}")
    lines.append("")
    lines.append("> 修数纪律：本台账只报矛盾、不猜哪个数对。修参考值必须引母型船资料/"
                 "文献页表，并在 `tools/refs/corpus.json` 里留下可机器复核的溯源字段；"
                 "修完重跑 `python3 -m tools.refs.audit` 与本台账。")
    lines.append("")
    return "\n".join(lines)


def write_ledger_markdown(result: Dict[str, Any], path: Optional[str] = None) -> str:
    os.makedirs(DOCS_DIR, exist_ok=True)
    path = path or LEDGER_MD
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_ledger_markdown(result))
    return path


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    result = run_audit()
    art = write_artifact(result)
    figs = make_figures(result)
    if "--no-docs" not in argv:
        print(f"ledger:   {write_ledger_markdown(result)}")
    print(f"artifact: {art}")
    for p in figs:
        print(f"figure:   {p}")
    print(f"counts:   {result['summary']['counts']}")
    print(f"verdict:  {result['summary']['verdict']} — {result['summary']['message']}")
    for f in result["findings"]:
        if f["severity"] == "FAIL":
            print(f"  FAIL [{f['gate']}] {f['case_id']}: {f['note']}")
    return 0 if result["summary"]["counts"]["FAIL"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())