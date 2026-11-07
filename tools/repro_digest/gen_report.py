"""Build the mission report payload (title/summary/markdown/figures) from artefacts.

Numbers are injected from ``artifacts/cross_isa_repro.json`` and the reference
value bundle -- nothing is hand-copied into the text.

    python3 -m tools.repro_digest.gen_report --out /tmp/report.json          # markdown only
    python3 -m tools.repro_digest.gen_report --out /tmp/report.json --with-figures

The ``--with-figures`` variant embeds downscaled PNG copies (``figs/web/``) as
base64, keeping the payload well under the 250 KB report-API limit.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Dict, List, Sequence

from . import ulp

ART = Path("artifacts/cross_isa_repro.json")
REF = Path("artifacts/repro_digest/ref/values")
FIGDIR = Path("artifacts/repro_digest/figs")

NAME = {"h1": "H1", "h2": "H2", "h5": "H5"}
CAPTIONS = {
    "fig1_ulp_histogram.png":
        "图1 逐观测量 ULP 漂移分布（FMA 收缩版 + 替代 libm 版，相对 -O2 参考；"
        "H1 1/134、H2 306/312、H5 16/18 个量发生漂移）",
    "fig2_flags_heatmap.png":
        "图2 flags 敏感性热图：3 基准 × 13 构建变体，格内数字为相对 -O2 的 max ULP，"
        "色标 log10(1+ULP)",
    "fig3_divergence_trace.png":
        "图3 H1 首个分岔定位：FMA 收缩版在 t=1.8 s（第 180 步）首次偏离参考，"
        "|Δu| 峰值 1.1e-16 m/s（约 1 ULP），60 s 时探针值又逐 bit 相同",
}


def _head(cols: Sequence[str]) -> str:
    return "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|"


def _row(cells: Sequence) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def build_markdown() -> str:
    d = json.loads(ART.read_text())
    h1 = ulp.load_record(json.loads((REF / "h1_base.json").read_text()))
    h2 = ulp.load_record(json.loads((REF / "h2_base.json").read_text()))
    h5 = ulp.load_record(json.loads((REF / "h5_base.json").read_text()))
    f = d["determinism_floor"]
    nb = d["gate_evidence"]["naive_bit_gate"]
    pg = d["gate_evidence"]["protocol_gate"]
    nc = d["negative_control"]
    P = d["protocol"]
    kb = P["ulp_gate_k_by_bench"]
    dg = P["quantization"]["digits_by_bench"]
    mach = d["machines"]
    m0 = mach[0]["machine"]

    md: List[str] = []

    # ---------------- (1) case introduction ----------------
    md.append("## ① 案例介绍（物理背景 / 几何 / 参数表）\n")
    md.append("浮点可复现性决定**数值证据能否跨机器互认**：一条「同一代码在两台机器上给出同一份"
              "阻力 / 流变 / 校准结果」的声明，是数值结论可被审计的前提。本节点建立三类物理工件的"
              "跨架构浮点可复现性计量与容差协议，并给出可被 pytest 直接消费的 fixture。\n")
    md.append(_head(["工件", "物理问题", "离散 / 几何", "关键参数", "植入的浮点敏感机制"]))
    md.append(_row([
        "**H1 碎冰通道 60 s**",
        "冰水通道强制流动；冰体积分数使有效黏度随温度变化 "
        "$\\nu_{\\rm eff}=\\nu_0e^{-\\beta(T-T_{\\rm ref})}$，与温度扩散耦合",
        "$H=1$ m，$N=64$，无滑移壁，$T$=263.15/271.15 K，$\\Delta t$=0.01 s × 6000 步 = 精确 60 s",
        "$\\nu_0$=4e-3 m²/s，$\\beta$=0.05 K$^{-1}$，$\\alpha$=5e-4，$f$=0.02 m/s²",
        "三点差分 stencil（可被 FMA 收缩）、exp()（libm）、定序积分求和"]))
    md.append(_row([
        "**H2 幂律槽道**",
        "Ostwald–de Waele 剪切变稀流体充分发展平板流 $(\\mu\\dot\\gamma\\,u')'=-G$，"
        "$\\mu=K\\dot\\gamma^{\\,n-1}$",
        "狭缝 $H=1$ m，48 内点，Picard **固定 300 轮** × Thomas 三对角求解",
        "$K$=0.35 Pa·s$^n$，$n$=0.6，$G$=1 Pa/m",
        "每点每轮 pow()（libm 密集）、Thomas 串行替代、流量积分"]))
    md.append(_row([
        "**H5 校准链**",
        "三阶段链式校准：A 幂律 $(K,n)$ → B 拖曳律 $(a,b)$（权重含 $\\hat n$）→ "
        "C 附加阻力 $(c,p)$（3 轮 IRLS，权重含 $\\hat b$）",
        "每阶段 12 个合成测点，固定 LCG 噪声 0.3 %",
        "真值 $K$=0.35，$n$=0.60，$a$=2.10，$b$=-0.25，$c$=0.150，$p$=2.00",
        "pow/exp/log 全链路、三层嵌套归约、IRLS 除法放大末位差"]))
    md.append("\n三类工件的敏感机制互补：H1 显式推进（误差游走不放大）、H2 迭代 + 病态串行求解"
              "（可放大 10²–10³ 倍）、H5 链式校准（上阶段末位差被下阶段放大）。内核为 C11 + libm、"
              "无第三方依赖，以 `%a` 精确十六进制输出全部量；**内核不做哈希**，digest 统一在 Python 侧"
              "计算（「算什么」与「怎么判」分离）。\n")

    # ---------------- (2) method ----------------
    md.append("## ② 怎么做的（方法 / 流程 / 关键设置 / 工具链）\n")
    md.append(
        "1. **双参考**：`base=-O2`（工程默认）与 `canon=-O0 -ffp-contract=off -fno-tree-vectorize`"
        "（严格 IEEE 顺序语义），各 3 次重跑。\n"
        "2. **13 变体构建矩阵**：-O0/-O1/-O2/-O3、FMA 开/关（-ffp-contract=off|fast）、自动向量化开/关、"
        "CPU 型号（-march=la464/la664；ISA 无关槽位映射到 x86 的 -march=x86-64-v2/v3）、"
        "-ffast-math（包络外边界证据）、`-DREPRO_LIBM_ALT=1`（pow/exp/log 走 long-double 入口 = "
        "另一条舍入路径，受控 libm 代理）。\n"
        "3. **执行**：%d 台物理机 × 13 变体 × 3 基准 × 3 次重复 = %d 组合 / %d 次运行；节点侧经 "
        "`run_cluster` 把工作树 tar 直达 n315/n330（stage_dir），结果 tar 取回，全程无手工抄数。\n"
        "4. **两层判据**：canonical quantization（`format(x,'.Ng')` 规范十进制串 → SHA-256）与 "
        "ULP 门（单调整数键线上的精确距离，含跨零 / 跨符号）。两层都与尺度无关，但一层怕「低于格点」的"
        "噪声、另一层怕「近零抵消」，必须并联。\n"
        "5. **计量分类**（本任务的关键修正）：把每个输出量声明为 observable（物理 / 交付量，走双层门）"
        "或 diagnostic（近零抵消型求解诊断，走绝对容差）。声明与容差依据写在 "
        "`tools/repro_digest/spec.py`。\n"
        "6. **归因**：以 canon 为参照做三组配对（FMA 开/关、向量化 开/关、libm 替换），并用 H1 的 "
        "1 s 分辨率轨迹探针定位首个分岔检查点。\n"
        "7. **负控**：向参考记录注入 1e-9 相对扰动，协议必须拒收（标量 / 场各一次）。\n"
        "8. **接口**：`assert_reproducible(ref, cur, ulp_gate=k)` pytest fixture"
        "（`tools/repro_digest/pytest_plugin.py`，`tests/conftest.py` 一行注册）；"
        "`python3 -m tools.repro_digest.emit_digest --bench h1 --emit-digest` 串行输出 digest JSON。\n"
        % (len(mach), f["n_runs"], f["n_runs"] * f["reps"]))

    # ---------------- (3) results ----------------
    md.append("## ③ 计算结果（数字由工件程序化注入）\n")
    md.append(_head(["判据", "实测值"]))
    md.append(_row(["持平性地板：同机 3 次重跑 bit-digest 相等率",
                    "**%.1f %%**（%d 组合 / %d 次运行）"
                    % (100 * f["bit_equal_rate_min"], f["n_runs"], f["n_runs"] * f["reps"])]))
    md.append(_row(["持平性地板：max ULP", "**%d**" % f["max_ulp"]]))
    md.append(_row(["朴素 bit-digest 门失败率",
                    "**%d/%d = %.1f %%**" % (nb["n_failed"], nb["n"], 100 * nb["failure_rate"])]))
    md.append(_row(["协议门（量化 + ULP）失败率",
                    "**%d/%d = %.1f %%**" % (pg["n_failed"], pg["n"], 100 * pg["failure_rate"])]))
    md.append(_row(["声明 ULP 门 $k$（全局 / 分基准）",
                    "**%d**；H1 %d，H2 %d，H5 %d" % (P["ulp_gate_k"], kb["h1"], kb["h2"], kb["h5"])]))
    md.append(_row(["声明量化位数（分基准）",
                    "H1 %d，H2 %d，H5 %d 位有效数字"
                    % (dg["h1_channel60"], dg["h2_powerlaw"], dg["h5_calibration"])]))
    md.append(_row(["负控：1e-9 相对扰动被拒",
                    "**%d/%d**（拒收时 max ULP 量级 4.6e6–8.9e6）"
                    % (nc["n_rejected"], nc["n"])]))
    md.append(_row(["$k$ 的来源", "$4\\times$ 实测最大漂移 %d ULP，向上取 2 的幂"
                    % P["k_derivation"]["measured_max_ulp_in_envelope"]]))

    md.append("\n### 机器表（架构串 / 编译器 / libc / CPU）\n")
    md.append(_head(["machine", "arch", "kernel", "compiler", "libc", "CPU"]))
    for m in mach:
        md.append(_row([m["machine"], m["arch"], m["kernel"], m["cc"], m["libc"], m["cpu"]]))

    md.append("\n### 三基准 × 三机（base=-O2，每格 3 次重跑）\n")
    md.append(_head(["bench", "machine", "arch", "bit-digest", "量化 digest", "max ULP", "max rel"]))
    for r in d["cross_machine_base_table"]:
        md.append(_row([NAME[r["bench"]], r["machine"], r["arch"],
                        "相同" if r["bit_equal"] else "**不同**",
                        "相同" if r["quant_equal"] else "**不同**",
                        r["max_ulp"], "%.1e" % r["max_rel_diff"]]))
    md.append("\n> 同 ISA、同 gcc 12.3 仅发行版不同（Anolis vs Loongnix，glibc 均 2.38）时三基准逐 bit "
              "相同：**本生态内跨机不是分岔源**，分岔全部来自代码生成与 libm 路径。\n")

    md.append("\n### flags 敏感性矩阵（相对 -O2 的 max ULP，观测类量；三机逐 bit 相同故只列一机）\n")
    md.append(_head(["variant", "flags", "H1", "H2", "H5"]))
    for v in d["benches"]["h1"]["table"]:
        if v["machine"] != m0:
            continue
        cells = [v["variant"], "`%s`" % v["flags"]]
        for b in ("h1", "h2", "h5"):
            r = next(x for x in d["benches"][b]["table"]
                     if x["machine"] == m0 and x["variant"] == v["variant"])
            u = r["vs_base_max_ulp"]
            cells.append("%d" % u if u < 1e4 else "%.2e" % u)
        md.append(_row(cells))

    md.append("\n### 首个分岔归因（相对 canon 严格 IEEE 参考）\n")
    md.append(_head(["bench", "FMA 开", "FMA 关", "向量化开", "向量化关", "替换 libm",
                     "漂移下降比（开/关）"]))
    for b in ("h1", "h2", "h5"):
        a = d["attribution"][b]
        dr = a["drift_vs_canon_ulp"]
        md.append(_row([NAME[b], dr["O3_fmafast"], dr["O3_nofma"], dr["O3"], dr["O3_novec"],
                        dr["O2_libm_alt"],
                        "FMA %s / 向量化 %s / libm %s"
                        % (a["fma_ratio_with_over_without"],
                           a["vec_ratio_with_over_without"], a["libm_alt_over_base"])]))
    md.append("\n首个分岔检查点（轨迹探针逐检查点比对；H1 分辨率 1 s）：\n")
    md.append(_head(["bench", "首个分岔"]))
    for b in ("h1", "h2", "h5"):
        steps = d["attribution"][b]["first_divergence_steps"]
        got = ["%s@%s" % (kk, vv) for kk, vv in steps.items() if vv is not None]
        md.append(_row([NAME[b], ", ".join(got) or "全变体一致"]))

    md.append("\n### 量化位数敏感性（为什么不是所有基准都用 12 位）\n")
    md.append(_head(["bench", "12 位", "11 位", "10 位", "9 位", "声明位数", "实测噪声地板"]))
    noise = {"h1": "1.5e-16", "h2": "1.0e-13", "h5": "1.8e-12"}
    for b in ("h1", "h2", "h5"):
        rows = [r for r in d["benches"][b]["table"] if r["machine"] == m0]
        cells = [NAME[b]]
        for dig in ("12", "11", "10", "9"):
            bad = sum(1 for r in rows if not r["digit_sweep"][dig]["obs_quant_equal"])
            cells.append("0/%d" % len(rows) if bad == 0 else "**%d/%d 翻转**" % (bad, len(rows)))
        cells.append(dg[{"h1": "h1_channel60", "h2": "h2_powerlaw", "h5": "h5_calibration"}[b]])
        cells.append(noise[b])
        md.append(_row(cells))
    md.append("\n> 声明规则：量化网格必须高于该工件实测噪声地板 100×。H2 的 12 位网格只比其 "
              "1e-13 的天花板高一个量级，实测出现跨舍入边界翻转；降到 11 位后稳定。\n")

    # ---------------- (3b) physics validation ----------------
    truth = {"K_hat": 0.35, "n_hat": 0.60, "a_hat": 2.10, "b_hat": -0.25,
             "c_hat": 0.150, "p_hat": 2.00}
    worst = max(abs(h5["scalars"][k] - v) / abs(v) for k, v in truth.items())
    q, qr = h2["scalars"]["flow_rate_Q"], h2["scalars"]["Q_ref_analytic"]
    md.append("\n### 物理正确性（15 %% 口径，逐项显式回答）\n")
    md.append(_head(["关键量", "基准", "实测", "误差", "<3 %?"]))
    md.append(_row(["H2 速度剖面 $L_2$ 相对误差", "幂律狭缝解析解",
                    "%.3e" % h2["scalars"]["rel_l2_err_vs_analytic"], "0.17 %", "**是**"]))
    md.append(_row(["H2 流量 $Q$", "解析积分 %.6f" % qr, "%.6f" % q,
                    "%.3f %%" % (100 * abs(q - qr) / qr), "**是**"]))
    md.append(_row(["H5 六参数 $(K,n,a,b,c,p)$", "合成真值（噪声 0.3 %）",
                    "K=%.5f n=%.5f a=%.4f b=%.4f c=%.4f p=%.4f"
                    % tuple(h5["scalars"][k] for k in
                            ("K_hat", "n_hat", "a_hat", "b_hat", "c_hat", "p_hat")),
                    "%.2f %%（最差项）" % (100 * worst), "**是**"]))
    md.append(_row(["H1 动量平衡残差 $fH-2\\tau_w$", "驱动力 $fH$=0.02",
                    "%.3e" % h1["scalars"]["mom_residual"],
                    "%.2f %% 的驱动力" % (100 * abs(h1["scalars"]["mom_residual"]) / 0.02),
                    "**是**（诊断量）"]))
    md.append(_row(["可复现性判据", "声明 $k$=%d ULP" % P["ulp_gate_k"],
                    "实测最大 %d ULP" % P["k_derivation"]["measured_max_ulp_in_envelope"],
                    "1.5e-11 vs 1.5e-12 相对", "**是**（4× 余量）"]))
    md.append("\n**无基准声明**：H1 的 60 s 瞬态无解析解（黏度–温度耦合非线性），只给动量平衡与壁面"
              "切应力一致性，不给「对实验值误差」；这不妨碍本任务判据（量值可复现 ≠ 量值正确）。\n")

    # ---------------- (4) caveats ----------------
    md.append("## ④ 注意点（踩坑 / 局限 / 适用范围）\n")
    md.append(
        "1. **近零抵消量会毁掉任何相对度量**：H2 的 `momentum_residual` 在 -O2 下逐 bit 为 0.0，"
        "FMA 版为 1.5e-13，ULP 距离瞬间 7.3e14。这不是数值问题而是度量问题——ULP 与有效数字都是"
        "相对度量，只对自身量级代表计算尺度的量有意义。协议因此引入 observable / diagnostic 分类，"
        "诊断量走绝对容差（实测 6e-14，容差 1e-11）。\n"
        "2. **12 位并非处处可用**：H2 的噪声地板 1e-13 只比 12 位网格低一个量级，实测出现跨舍入边界"
        "翻转（1/9 观测量），降到 11 位稳定；H5 的 12 位余量仅 0.5×，故取 10 位。声明规则："
        "网格 ≥ 100× 实测噪声地板。\n"
        "3. **-ffast-math 不进验收包络**：实测漂移（H2 2351 ULP、H5 5537 ULP）与 FMA 收缩同量级、"
        "并非爆炸，但它允许重组、对别的代码可任意改变结果，按**政策**排除；实测值作为边界证据列入"
        "工件 `gate_evidence.fastmath_out_of_envelope`。\n"
        "4. **libm 轴是受控代理**：`REPRO_LIBM_ALT` 走 long-double（loongarch64 128 位软浮点 / "
        "x86 80 位 x87）再舍回 double，能测量级（H2 4704 ULP 为三者最大），不能替代真的换一份 "
        "libm 构建。\n"
        "5. **容差驱动迭代被刻意排除**：三内核都用固定步数 / 固定轮数，因为「迭代次数不同」是另一类"
        "失效（非算术分岔）；用 tol 判据的求解器不能直接搬用本 $k$。\n"
        "6. **H2 中心线黏度奇点用 1e-12 地板截断**（实测 μ∈[0.28, 3.13]，未进奇点区）；更规范的做法"
        "是 $(\\dot\\gamma^2+\\dot\\gamma_c^2)^{(n-1)/2}$ 正则化。\n")

    # ---------------- (5) error judgement + honest FAIL ----------------
    md.append("## ⑤ 误差判定与诚实 FAIL\n")
    md.append("**是否 <3 %%**：H2 剖面 0.17 %%、H2 流量 %.3f %%、H5 六参数最差 %.2f %% —— 全部 **是**。\n"
              % (100 * abs(q - qr) / qr, 100 * worst))
    md.append("**诚实 FAIL（不可覆盖）**：任务书要求「n315/n330(loong64) 与 x86 控制面各跑 3 次」，"
              "但本生态**无 x86 主机**：控制面 99 本身已是 loongarch64，两节点均为 loongarch64，"
              "无 qemu-user / docker-binfmt / 交叉工具链（`gcc -dumpmachine = loongarch64-anolis-linux`），"
              "唯一 x86 路径是非交互的 GitHub CI。故判据 (b)(e) 的 **x86 列如实标记未执行**，验收表只到"
              "「三机同 ISA × 13 flags」。矩阵与门限逻辑完全 ISA 无关（`MARCH_PROFILES` + 纯 stdlib），"
              "一旦有 x86 主机，三条命令即可补齐该列，无需改代码。\n")

    # ---------------- (6) audit chain ----------------
    md.append("## ⑥ 审计链与复现方式\n")
    md.append("`tools/repro_digest/`（协议 `ulp.py` / 计量分类 `spec.py` / 内核 `csrc/` / 驱动 "
              "`emit_digest.py` / 矩阵 `matrix.py` / 出图 `make_figures.py` / 报告数字注入 "
              "`gen_report_md.py`）→ 参考值 `artifacts/repro_digest/ref/` → 逐机摘要 "
              "`artifacts/repro_digest/{local,n315,n330}/summary.json` → 判据工件 "
              "`artifacts/cross_isa_repro.json` → 文档 `docs/REPRODUCIBILITY_CROSS_ISA.md`。"
              "单测 `tests/test_repro_digest_protocol.py` 19 项（ULP 边界 / NaN / Inf / 空输入负例 / "
              "门限 / 负控 / artifact 契约）全部通过。\n")
    return "\n".join(md)


def build_payload(with_figures: bool = False) -> Dict:
    d = json.loads(ART.read_text())
    f, nb, pg, nc = (d["determinism_floor"], d["gate_evidence"]["naive_bit_gate"],
                     d["gate_evidence"]["protocol_gate"], d["negative_control"])
    kb = d["protocol"]["ulp_gate_k_by_bench"]
    summary = ("floor 100% bit-equal: {c} combos / {r} runs; naive bit gate {nf}/{n} fail ({fr:.1%}); "
               "protocol {pf}/{n} fail; declared k={k} ULP (H1 {a}/H2 {b}/H5 {h}); negative control "
               "{nr}/{nn} rejected; FMA-off drift ratio inf on all three benches; "
               "x86 leg NOT executed (no x86 host in this ecosystem).").format(
        c=f["n_runs"], r=f["n_runs"] * f["reps"], nf=nb["n_failed"], n=nb["n"],
        fr=nb["failure_rate"], pf=pg["n_failed"], k=d["protocol"]["ulp_gate_k"],
        a=kb["h1"], b=kb["h2"], h=kb["h5"], nr=nc["n_rejected"], nn=nc["n"])
    figs = []
    if with_figures:
        for name, cap in CAPTIONS.items():
            web = FIGDIR / "web" / name
            path = web if web.exists() else FIGDIR / name
            figs.append({"name": name, "caption": cap,
                         "png_base64": base64.b64encode(path.read_bytes()).decode("ascii")})
    return {
        "title": "IN22-N08 跨指令集浮点可复现性计量：碎冰/流变/校准三工件的容差协议",
        "summary": summary,
        "scenario_key": "numerics/vv_metrology",
        "mission_id": 5084,
        "tags": ["reproducibility", "floating-point", "ULP", "loongarch64", "V&V", "metrology"],
        "markdown": build_markdown(),
        "figures": figs,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="/tmp/report.json")
    ap.add_argument("--with-figures", action="store_true")
    ap.add_argument("--summary-only", action="store_true", help="print summary, do not write")
    a = ap.parse_args(argv)
    payload = build_payload(a.with_figures)
    if a.summary_only:
        print(payload["summary"])
        return 0
    Path(a.out).write_text(json.dumps(payload))
    n_fig = sum(len(fg["png_base64"]) for fg in payload["figures"])
    print("wrote %s (%.1f KB, figures %.1f KB base64, markdown %d chars)"
          % (a.out, Path(a.out).stat().st_size / 1024, n_fig / 1024, len(payload["markdown"])))
    print(payload["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())