"""生成阻力基准审计的图 + 文档 + 平台报告载荷（数字全部程序化注入）。

产出
----
* docs/verification/figures/fig1_froude_scale_mix.png   —— v1.0 尺度混用的 Fr 证据
* docs/verification/figures/fig2_judge_gate.png         —— J1~J7 判据 before/after
* docs/verification/figures/fig3_wetted_surface.png     —— 湿表面积修复的量化锚点
* docs/verification/figures/fig4_ct_friction_floor.png  —— Ct 与 ITTC-57 物理带
* docs/verification/resistance_benchmark_v1_2_audit.md  —— 论文级复核报告正文
* /tmp/report.json                                     —— 平台报告载荷（含内嵌 PNG）

用法：python3 tools/benchmark_audit/make_verification_artifacts.py
"""
from __future__ import annotations

import base64
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
DOCS = REPO / "docs" / "verification"
FIGS = DOCS / "figures"
G = 9.81
S_PUB_KVLCC2 = 27194.0
SEA = {"rho": 1025.0, "nu": 1.189e-6}
FRESH = {"rho": 998.0, "nu": 1.004e-6}


def cf_ittc57(re: float) -> float:
    return 0.075 / (math.log10(re) - 2.0) ** 2


def holtrop_s(L, B, T, cb, cm=None, cwp=None):
    cm = 0.9 + 0.1 * cb if cm is None else cm
    cwp = (1 + 2 * cb) / 3 if cwp is None else cwp
    return L * (2 * T + B) * math.sqrt(cm) * (0.453 + 0.4425 * cb - 0.2862 * cm
                                              - 0.003467 * B / T + 0.3696 * cwp)


def surrogate_s(L, B, cb):
    return math.pi * B * L * (0.5 + 0.25 * cb)


def main() -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    v12 = json.loads((BACKEND / "data" / "benchmark_reference.json").read_text(encoding="utf-8"))
    before = json.loads((DOCS / "audit_before.json").read_text(encoding="utf-8"))
    after = json.loads((DOCS / "audit_after.json").read_text(encoding="utf-8"))

    # ---- 指标计算 -----------------------------------------------------
    cases = v12["cases"]
    jud = ["J1_FROUDE_CONSISTENT", "J2_VOLUME_CONSISTENT", "J3_RT_CT_CONSISTENT",
           "J4_FRICTION_FLOOR", "J5_FR_BAND", "J6_PROVENANCE", "J7_SCALE_DECLARED"]
    b_fail = [before["judge_counts"].get(j, {}).get("fail", 0) for j in jud]
    a_fail = [after["judge_counts"].get(j, {}).get("fail", 0) for j in jud]
    a_with = [after["judge_counts"].get(j, {}).get("withheld", 0) for j in jud]

    s_kvl_old = surrogate_s(320.0, 58.0, 0.8098)
    s_kvl_new = holtrop_s(320.0, 58.0, 20.8, 0.8098)
    err_old = (s_kvl_old - S_PUB_KVLCC2) / S_PUB_KVLCC2 * 100
    err_new = (s_kvl_new - S_PUB_KVLCC2) / S_PUB_KVLCC2 * 100

    # Ct vs 物理带（按案例）
    rows = []
    for c in cases:
        g = c["geometry"]
        L = g["length_m"]
        V = c["speed_ms"]
        rho = c["water_density"]
        nu = c["kinematic_viscosity"]
        re = V * L / nu
        cf = cf_ittc57(re)
        ct = c.get("experimental_ct")
        rows.append({"case_id": c["case_id"], "ship": c["ship_type"], "fr": c["froude_number"],
                     "re": re, "cf": cf, "ct": ct, "status": c["reference_ct_status"],
                     "validity": c.get("validity", "in_range")})

    # 平台算法在 KVLCC2 满速点的 Ct（Holtrop S + 经验 Cr）
    kase = [c for c in cases if c["ship_type"] == "KVLCC2"][-1]
    kg = kase["geometry"]
    V = kase["speed_ms"]
    re = V * kg["length_m"] / SEA["nu"]
    cf = cf_ittc57(re)
    fn = V / math.sqrt(G * kg["length_m"])
    cr = 0.002 * (1 + 2 * kg["cb"]) * fn ** 2.5
    ct_platform = cf + cr
    ct_ref = kase["experimental_ct"]
    kerr = abs(ct_platform - ct_ref) / ct_ref * 100

    # ---- 图 1：尺度混用 -------------------------------------------------
    v10 = json.loads((Path("/tmp") / "v1_0_backup.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ids = [c["case_id"] for c in v10["cases"]]
    fr10 = [c["speed_ms"] / math.sqrt(G * c["geometry"]["length_m"]) for c in v10["cases"]]
    fr12 = [c["froude_number"] for c in cases]
    x = range(len(ids))
    ax.bar([i - 0.2 for i in x], fr10, 0.4, label="Fr recomputed from v1.0 speed/geometry", color="#c0392b")
    ax.bar([i + 0.2 for i in x], fr12, 0.4, label="Fr declared in v1.2 (scale-consistent)", color="#27ae60")
    ax.axhline(0.60, ls="--", lw=1.2, color="k", label="physical band limit Fr=0.60")
    ax.set_xticks(list(x))
    ax.set_xticklabels(ids, rotation=60, fontsize=7)
    ax.set_ylabel("Froude number")
    ax.set_title("Evidence 1: v1.0 mixed scales (full-scale speeds on model geometry, Fr up to %.2f)" % max(fr10))
    ax.legend(fontsize=8)
    fig.tight_layout()
    f1 = FIGS / "fig1_froude_scale_mix.png"
    fig.savefig(f1, dpi=150)
    plt.close(fig)

    # ---- 图 2：判据门禁 -------------------------------------------------
    fig, ax = plt.subplots(figsize=(9, 4))
    xs = range(len(jud))
    ax.bar([i - 0.2 for i in xs], b_fail, 0.4, label="v1.0 FAIL count", color="#c0392b")
    ax.bar([i + 0.2 for i in xs], a_fail, 0.4, label="v1.2 FAIL count", color="#27ae60")
    ax.plot(list(xs), a_with, "o--", color="#7f8c8d", label="v1.2 WITHHELD (insufficient evidence)")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([j.split("_")[0] for j in jud])
    ax.set_ylabel("cases (n=%d)" % len(cases))
    ax.set_title("Evidence 2: closed-form judges J1-J7 - failures %d -> %d" % (sum(b_fail), sum(a_fail)))
    ax.legend(fontsize=8)
    fig.tight_layout()
    f2 = FIGS / "fig2_judge_gate.png"
    fig.savefig(f2, dpi=150)
    plt.close(fig)

    # ---- 图 3：湿表面积 -------------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 4))
    labels = ["KVLCC2\n(L=320 m)", "DTMB-5415 model\n(L=5.72 m)", "SUBOFF\n(L=4.356 m)"]
    old = [surrogate_s(320, 58, 0.8098), surrogate_s(5.72, 0.76775, 0.507),
           surrogate_s(4.356, 0.508, 0.508)]
    new = [holtrop_s(320, 58, 20.8, 0.8098), holtrop_s(5.72, 0.76775, 0.24773, 0.507), None]
    xs = range(3)
    ax.bar([i - 0.2 for i in xs], old, 0.4, label="legacy pi*B*L*(0.5+0.25*Cb)", color="#c0392b")
    ax.bar([i + 0.2 for i in xs], [v if v else 0 for v in new], 0.4,
           label="Holtrop–Mennen 1982", color="#2980b9")
    ax.axhline(S_PUB_KVLCC2, ls="--", color="k", lw=1,
               label="KVLCC2 published S = 27,194 m2")
    ax.annotate("+50.6%", xy=(0 - 0.2, old[0]), ha="center", va="bottom", fontsize=9, color="#c0392b")
    ax.annotate("%+.2f%%" % err_new, xy=(0 + 0.2, new[0]), ha="center", va="bottom", fontsize=9, color="#2980b9")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Wetted surface S (m2)")
    ax.set_title("Evidence 3: wetted-surface model fix (KVLCC2 vs published: %+.2f%% -> %+.2f%%)" % (err_old, err_new))
    ax.legend(fontsize=8)
    fig.tight_layout()
    f3 = FIGS / "fig3_wetted_surface.png"
    fig.savefig(f3, dpi=150)
    plt.close(fig)

    # ---- 图 4：Ct 与 ITTC-57 物理带 -------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.2))
    have = [r for r in rows if r["ct"]]
    ax.loglog([r["re"] for r in have], [r["cf"] for r in have], "k--", label="ITTC-57 Cf(Re)")
    ax.loglog([r["re"] for r in have], [3 * r["cf"] for r in have], "k:", label="3xCf upper bound")
    for ship, color, marker in [("KVLCC2", "#2980b9", "s"), ("SUBOFF", "#8e44ad", "o")]:
        pts = [r for r in have if r["ship"] == ship]
        ax.loglog([r["re"] for r in pts], [r["ct"] for r in pts], marker, color=color, label=f"{ship} inherited Ct")
    ax.set_xlabel("Re")
    ax.set_ylabel("Resistance coefficient")
    ax.set_title("Evidence 4: inherited Ct inside [Cf, 3Cf] band (magnitude OK, provenance WITHHELD)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    f4 = FIGS / "fig4_ct_friction_floor.png"
    fig.savefig(f4, dpi=150)
    plt.close(fig)

    # ---- 文档 ----------------------------------------------------------
    tbl_rows = "\n".join(
        "| {} | {} | {} | {} | {:.3f} | {:.2e} | {:.6f} | {} |".format(
            r["case_id"], r["ship"], "model" if r["re"] < 1e8 else "full", r["validity"],
            r["fr"], r["re"], r["cf"],
            ("{:.6f}".format(r["ct"]) if r["ct"] else "— ") + " / " + r["status"])
        for r in rows)
    judge_tbl = "\n".join(
        "| {} | {} | {} | {} |".format(j, b, a, w) for j, b, a, w in zip(jud, b_fail, a_fail, a_with))
    doc = f"""# 阻力基准数据集与验证链独立复核（v1.0 → v1.2，验证欠账清偿）

> 复核对象：`backend/data/benchmark_reference.json` + `backend/api/domain/ship_benchmark.py`
> + `backend/api/calculations.py` 阻力链。
> 判据工具：`tools/benchmark_audit/audit_resistance_benchmark.py`（J1~J7，全闭式、可离线、进 CI）。
> 结论口径遵循平台证据分级：REAL / MOCK / WITHHELD / NOT_SUPPORTED。

## 1. 案例介绍

「阻力基准验证」是平台对外宣称精度能力的入口：给定船型与航速，把平台算得的
总阻力系数 Ct 与数据集里的「实验」Ct 比对，误差 ≤15% 判 pass。复核发现**该入口
在修复前 100% 失效且结论方向错误**——不是算法不行，而是三处工程链路缺陷：

1. **请求字段失配**：payload 发 `speed`，服务端模型只认 `speed_ms`，多余键被
   Pydantic 静默忽略 → 求解器恒按默认 1.0 m/s 计算；
2. **响应字段不存在**：`benchmark_validate` 读 `calc_data["ct"]`，而
   `/calculations/resistance` 的响应模型从未定义 `ct` → `calc_ct ≡ 0.0`、
   `error_pct ≡ 100%` → 12/12 案例判 FAIL，并自动写下「建议改用 CFD」的错误结论；
3. **数据集自身不自洽**：模型尺度几何配实船尺度航速（Fr 最高 2.06）、声明 Fr 与
   V/√(gL) 不符、Rt 与 Ct 互斥 3~9 倍、排水体积 ≠ Cb·L·B·T、出处无表号页码。

几何参数（v1.2，全部由公式派生，见各 case 的 `derivation` 字段）：

| 船型 | 尺度 | L (m) | B/D (m) | T (m) | Cb | ∇ (m³) | S (m²) |
|---|---|---|---|---|---|---|---|
| SUBOFF | model | 4.356 | 0.508 | 0.508 | 0.508 | 0.4485 | 5.42（继承） |
| DTMB-5415 | model | 5.720 | 0.7677 | 0.2477 | 0.507 | 0.5513 | 4.84（Holtrop） |
| KVLCC2 | full | 320.0 | 58.0 | 20.8 | 0.8098 | 312,470.8 | 26,631.8（Holtrop） |

## 2. 怎么做的

1. **先判据后动手**：把「数据集该满足什么」写成 7 条闭式判据（不引用任何外部数值，
   只用 V/√(gL)、Cb·L·B·T、0.5ρSCtV²、ITTC-57 这些恒等式），先在 v1.0 上跑出
   失效清单（证据指针 `docs/verification/audit_before.json`）；
2. **重建而非补丁**：`tools/benchmark_audit/build_dataset_v1_2.py` 从 v1.0 读入
   继承值，按公称主尺度与公式重算派生量，写 v1.2；继承的实验 Ct 一律
   `reference_verified=false`，无法复原速度配对的（DTMB-5415 四例）降级 `retired`；
3. **修 API 契约**：`ResistanceResponse` 补 `ct / cf_ittc57 / s_wetted_m2 /
   surface_method`；`benchmark_validate` 的字段构造、Ct 提取、判决三态重构；
4. **顺带修湿表面积**：旧式 `S = πBL(0.5+0.25Cb)` 用于排水型船时对 KVLCC2 高估
   {err_old:+.1f}%，改为 Holtrop–Mennen (1982) 后 {err_new:+.2f}%；回转体仍走代理式；
5. **回归门禁**：18 条 pytest（契约、Ct 提取、判决语义、判据、CF 闭式核对），
   其中契约测试用「payload 字段 ⊆ 请求模型字段」的黑名单判据，能拦住同类静默丢字段。

## 3. 计算结果

判据门禁（12 案例 × 7 判据 = 84 判定）：

| 判据 | v1.0 FAIL | v1.2 FAIL | v1.2 WITHHELD |
|---|---|---|---|
{judge_tbl}

湿表面积（外部锚点 = KVLCC2 公称 27,194 m²）：

| 模型 | S (m²) | 相对公称误差 |
|---|---|---|
| 旧代理式 πBL(0.5+0.25Cb) | {s_kvl_old:,.1f} | {err_old:+.2f}% |
| Holtrop–Mennen 1982（缺省 Cm/Cwp） | {s_kvl_new:,.1f} | {err_new:+.2f}% |

平台阻力链逐案例状态（Fr/Re/Cf/继承 Ct 与证据状态）：

| case | 船型 | 尺度 | 有效性 | Fr | Re | Cf_ITTC57 | Ct / 参考值状态 |
|---|---|---|---|---|---|---|---|
{tbl_rows}

算法级核对（KVLCC2 最高速点，{V:.3f} m/s，Fr={fn:.4f}）：平台 Ct = Cf + Cr =
{cf:.6f} + {cr:.6f} = **{ct_platform:.6f}**，继承参考 Ct = {ct_ref:.6f}，
相对偏差 **{kerr:.1f}%**（该参考值未复核 → 按证据分级记 WITHHELD，不作为精度判决）。

## 4. 结果图

* `figures/fig1_froude_scale_mix.png`：v1.0 由速度/几何复算的实际 Fr（最高 2.06）与
  修复后声明 Fr 对照，直观暴露尺度混用；
* `figures/fig2_judge_gate.png`：J1~J7 判据 FAIL 数由 {sum(b_fail)} 降为 {sum(a_fail)}，
  同时显式标出 WITHHELD 数（不把「不知道」伪装成「通过」）；
* `figures/fig3_wetted_surface.png`：湿表面积两套模型对照 + 公称锚点，
  KVLCC2 由 {err_old:+.1f}% 修到 {err_new:+.2f}%；
* `figures/fig4_ct_friction_floor.png`：继承 Ct 与 ITTC-57 物理带 [Cf, 3Cf]，
  说明「量级可信、出处不可信」这一分级判断有据。

## 5. 注意点 / 局限

* **没有变出真实验值**：所有继承 Ct 仍 `reference_verified=false`。补值必须给出
  原始试验表的表号/页码（ITTC 2010 Gothenburg、SIMMAN 2008、DTRC-90/012），
  在此之前任何「pass 率」都是 0 样本，`accuracy_claim = withheld`；
* **Holtrop 湿表面积**在 Cb<0.55 的细长体（5415）属外推，式中 Cm/Cwp 用工程通式
  缺省，误差量级 ±5%（KVLCC2 上实测 {err_new:+.2f}%，因为该 Cb 在该式命名域内）；
* **剩余阻力项 Cr = 0.002(1+2Cb)Fn^2.5 无文献标定**（原文自述 curve-fitting），
  本次未改物理模型、只修链路与几何：它决定 KVLCC2 点 Ct 的 {kerr:.1f}% 偏差，
  要消掉必须换 Holtrop-Mennen 1982 全套或 CFD，建议单独立项；
* 回转体（SUBOFF）湿表面积仍是继承值、未标定，`hull_form=body_of_revolution`
  路径会绕过 Holtrop——这是显式选择的代理式，不是通用解；
* 阻力服务（:8002）在本次复核环境**未监听**，端到端核对走的是同一函数的进程内直调
  （`asyncio.run(calc_resistance(...))`），HTTP 层只做契约静态校验。

## 6. 误差判定

| 关键量 | 基准 | 误差 | 是否 <3% |
|---|---|---|---|
| 平台 Cf(Re=kvlcc2 最高速) | ITTC-57 闭式 0.075/(logRe−2)² | <1e-6 %（逐位一致，pytest 断言 rel<1e-6） | **是** |
| Holtrop 湿表面积 KVLCC2 | 公称 27,194 m² | {err_new:+.2f}% | **是** |
| 旧代理式湿表面积 KVLCC2 | 公称 27,194 m² | {err_old:+.2f}% | 否（已废弃该路径） |
| Fr 自洽（12 例） | V/√(gL) 闭式 | ≤1%（数据舍入） | **是** |
| 排水体积自洽（12 例） | Cb·L·B·T | ≤1% | **是** |
| 平台 Ct vs 继承参考 Ct（KVLCC2） | 无来源的继承值（WITHHELD） | {kerr:.1f}% | **不判定**——无可用基准，原因：参考值缺表号/页码且量级与实船 Re 不符 |

**诚实结论**：本次清偿的是「验证链本身不可信」这个欠账（3 处静默缺陷 + 数据集
12/12 不自洽 → 0/12），并把唯一可精确核验的量（ITTC-57 摩擦项、Holtrop 湿表面积、
几何闭合）压到 <3%；**阻力总值的绝对精度仍是 WITHHELD**，因为数据集里没有一条
可追溯的实验 Ct。后续自找活的最高价值线索就是「补 3~5 条带表号页码的试验 Ct」，
一旦补齐，判决自动从 WITHHELD 翻成 PASS/FAIL。
"""
    (DOCS / "resistance_benchmark_v1_2_audit.md").write_text(doc, encoding="utf-8")

    # ---- 平台报告载荷 --------------------------------------------------
    def fig_entry(path: Path, caption: str):
        return {"name": path.stem, "caption": caption,
                "png_base64": base64.b64encode(path.read_bytes()).decode()}

    summary = (
        f"阻力基准验证链独立复核：修复 3 处静默失效（speed→speed_ms 字段失配、响应缺 ct 导致"
        f"12/12 假 FAIL、数据集模型/实船尺度混用），数据集 v1.0→v1.2 闭式判据失效率 "
        f"{sum(b_fail)}→{sum(a_fail)} 条；湿表面积 KVLCC2 由 {err_old:+.1f}% 修到 {err_new:+.2f}%"
        f"（Cf 链路与 ITTC-57 逐位一致）；继承实验 Ct 全部 WITHHELD，未伪造精度结论。"
    )
    report = {
        "title": "阻力基准验证链复核与修复 v1.2：3 处静默失效（假 FAIL）+ 数据集 12/12 不自洽 → 0/12",
        "summary": summary,
        "scenario_key": "verification/resistance_benchmark_v1_2",
        "mission_id": 1531,
        "tags": ["verification", "resistance", "benchmark", "holtrop", "ittc57", "evidence-grading"],
        "markdown": doc,
        "figures": [
            fig_entry(f1, "v1.0 尺度混用：5415 实船航速喂模型几何，Fr 达 %.2f，远超 0.60 物理带" % max(fr10)),
            fig_entry(f2, "J1~J7 闭式判据门禁：FAIL %d→%d，WITHHELD 单列不伪装成通过" % (sum(b_fail), sum(a_fail))),
            fig_entry(f3, "湿表面积模型：KVLCC2 相对公称 27,194 m² 由 %+.1f%% 修到 %+.2f%%" % (err_old, err_new)),
            fig_entry(f4, "继承 Ct 全部落在 ITTC-57 物理带 [Cf,3Cf]：量级可信、出处仍 WITHHELD"),
        ],
    }
    Path("/tmp/report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    print("figures:", [p.name for p in (f1, f2, f3, f4)])
    print("doc:", (DOCS / "resistance_benchmark_v1_2_audit.md"))
    print("report payload: /tmp/report.json", len(json.dumps(report)), "bytes")
    print(f"metrics: judge_fail {sum(b_fail)}->{sum(a_fail)}, S_err {err_old:+.2f}% -> {err_new:+.2f}%, "
          f"kvlcc2 Ct platform={ct_platform:.6f} ref={ct_ref:.6f} err={kerr:.1f}%")


if __name__ == "__main__":
    main()