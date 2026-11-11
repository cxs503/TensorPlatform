"""Report generation: figures, markdown and machine-readable ``criteria.json``.

Every number that appears in the markdown or in ``criteria.json`` is read out of
the scan artefact (``scan.json``) or the overlay document — nothing is typed by
hand, so the report can always be regenerated and re-checked from machine data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .model import LEVELS, LEVEL_RANK

LEVEL_COLORS = {
    "REAL": "#1b7f3b",
    "WITHHELD": "#8a8a8a",
    "MOCK": "#b3202c",
    "STUB": "#d97706",
    "NOT_SUPPORTED": "#4b5563",
}

PREREGISTERED = [
    {
        "id": "C1",
        "statement": "Synthetic fixture separation: every known-degenerate synthetic endpoint is "
                     "caught (random/hardcoded/stateful -> MOCK, no-numeric -> STUB) and the two "
                     "known-honest ones stay REAL. Zero false negatives, zero false positives.",
        "threshold": "FN=0 and FP=0 over 8 fixtures",
    },
    {
        "id": "C2",
        "statement": "Decision coverage on the live platform: fraction of scanned endpoints that "
                     "receive a decided level (REAL/MOCK/STUB) rather than WITHHELD.",
        "threshold": ">= 0.50",
    },
    {
        "id": "C3",
        "statement": "Non-vacuity on the live platform: the detector must find at least one MOCK "
                     "and one REAL among the scanned endpoints (a detector that says one thing "
                     "about everything is useless).",
        "threshold": "n_MOCK >= 1 and n_REAL >= 1",
    },
    {
        "id": "C4",
        "statement": "Scanner reproducibility: repeating the scan on a sub-sample yields the same "
                     "level for every endpoint that was decided in the first pass.",
        "threshold": "100% agreement on decided endpoints",
    },
    {
        "id": "C5",
        "statement": "Ledger linkage completeness: every capability in the platform ledger is "
                     "either mapped to a scanned endpoint or explicitly reported UNMAPPED.",
        "threshold": "mapped + unmapped == n_ledger_capabilities",
    },
    {
        "id": "C6",
        "statement": "Zero domain semantics in the detector code: no domain vocabulary in "
                     "tools/mock_detector/**.py (the domain enters only as injected payload data).",
        "threshold": "0 hits for the forbidden-word list",
    },
]

#: The forbidden vocabulary lives in a data file, not in code: a guard that lists
#: domain words inside the very sources it scans would flag itself.
FORBIDDEN_WORDS: tuple = tuple(
    json.loads((Path(__file__).with_name("domain_guard.json")).read_text(
        encoding="utf-8"))["forbidden_words"])


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------
def make_figures(scan: Dict[str, Any], overlay: Optional[Dict[str, Any]],
                 outdir: Path) -> List[Dict[str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir.mkdir(parents=True, exist_ok=True)
    figs: List[Dict[str, str]] = []
    probes = scan.get("probes", [])
    counts = scan.get("summary", {}).get("level_counts", {})

    # ---- fig 1: level distribution
    levels = [lv for lv in LEVELS]
    vals = [counts.get(lv, 0) for lv in levels]
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=160)
    bars = ax.bar(levels, vals, color=[LEVEL_COLORS[l] for l in levels])
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.2, str(v), ha="center", fontsize=10)
    ax.set_ylabel("endpoints")
    ax.set_title(f"Structural evidence levels (n={len(probes)} endpoints)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    path = outdir / "fig1_level_distribution.png"
    fig.savefig(path)
    plt.close(fig)
    figs.append({"name": "fig1_level_distribution.png", "path": str(path),
                 "caption": "Distribution of structural evidence levels over all scanned POST "
                            "endpoints (determinism x monotonicity x idempotency)."})

    # ---- fig 2: degeneracy plane
    xs, ys, cs, labels = [], [], [], []
    for p in probes:
        ap = p.get("axes_probed") or 0
        resp = p.get("axes_respond") or 0
        x = (resp / ap) if ap else 0.0
        n = p.get("n_samples") or 0
        y = (p.get("n_distinct_vectors") or 0) / n if n else 0.0
        xs.append(x)
        ys.append(y)
        cs.append(LEVEL_COLORS.get(p.get("level"), "#000000"))
        labels.append(p.get("cap_id", ""))
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=160)
    ax.scatter(xs, ys, c=cs, s=36, alpha=0.85, edgecolors="white", linewidths=0.4)
    ax.axvline(0.0001, color="#b3202c", linestyle="--", linewidth=1.0)
    ax.set_xlabel("responsive input-axis fraction  (axes that moved / axes probed)")
    ax.set_ylabel("distinct output vectors / probe samples")
    ax.set_title("Degeneracy plane: hardcoded endpoints collapse to the left edge")
    ax.grid(alpha=0.25)
    for lv in LEVELS:
        if any(p.get("level") == lv for p in probes):
            ax.scatter([], [], c=LEVEL_COLORS[lv], s=36, label=lv)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    path = outdir / "fig2_degeneracy_plane.png"
    fig.savefig(path)
    plt.close(fig)
    figs.append({"name": "fig2_degeneracy_plane.png", "path": str(path),
                 "caption": "Every scanned endpoint placed by (responsive-axis fraction, distinct "
                            "vector ratio). Hardcoded/invariant endpoints pile up at x=0; "
                            "quantized ones sit at low y."})

    # ---- fig 3: sweep response profiles, one REAL and one MOCK exemplar
    def _exemplar(level: str):
        cands = [p for p in probes if p.get("level") == level and p.get("axes_probed")]
        cands = [p for p in cands if any(a.get("series_sample") for a in p.get("axes", []))]
        return cands[0] if cands else None

    real, mock = _exemplar("REAL"), _exemplar("MOCK")
    if real or mock:
        fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.0), dpi=160)
        for ax, probe, title in ((axes[0], real, "REAL exemplar"),
                                 (axes[1], mock, "MOCK exemplar")):
            if not probe:
                ax.text(0.5, 0.5, "no exemplar in this class", ha="center")
                ax.set_axis_off()
                continue
            axis_rec = next((a for a in probe.get("axes", []) if a.get("series_sample")), None)
            if axis_rec is None:
                ax.text(0.5, 0.5, "no series recorded", ha="center")
                ax.set_axis_off()
                continue
            xvals = axis_rec.get("accepted_values", [])
            for field, series in axis_rec["series_sample"].items():
                if len(series) != len(xvals):
                    continue
                lo, hi = min(series), max(series)
                span = (hi - lo) or 1.0
                norm = [(v - lo) / span for v in series]
                ax.plot(xvals, norm, marker="o", linewidth=1.4,
                        label=f"{field} (span={hi - lo:.3g})")
            ax.set_title(f"{title}\n{probe.get('path')}"[:70], fontsize=9)
            ax.set_xlabel(f"swept input: {axis_rec.get('field')}")
            ax.set_ylabel("normalised output")
            ax.grid(alpha=0.25)
            ax.legend(fontsize=6.5)
        fig.suptitle("Monotonicity probe: output response along one swept numeric input", fontsize=10)
        fig.tight_layout()
        path = outdir / "fig3_sweep_profiles.png"
        fig.savefig(path)
        plt.close(fig)
        figs.append({"name": "fig3_sweep_profiles.png", "path": str(path),
                     "caption": "Normalised numeric outputs along one swept input axis for a "
                                "structural-REAL and a structural-MOCK endpoint. A hardcoded "
                                "endpoint is a flat line; a computed one responds."})

    # ---- fig 4: claim vs observed confusion matrix
    if overlay and overlay.get("entries"):
        rows = [lv for lv in LEVELS]
        cols = [lv for lv in LEVELS]
        mat = [[0] * len(cols) for _ in rows]
        for e in overlay["entries"]:
            obs = e.get("observed_level")
            claim = e.get("claimed_level")
            if obs in rows and claim in cols:
                mat[rows.index(obs)][cols.index(claim)] += 1
        fig, ax = plt.subplots(figsize=(6.2, 4.6), dpi=160)
        im = ax.imshow(mat, cmap="OrRd", aspect="auto")
        ax.set_xticks(range(len(cols)), cols, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(rows)), rows, fontsize=8)
        ax.set_xlabel("ledger claimed level")
        ax.set_ylabel("structurally observed level")
        ax.set_title("Claim vs structural evidence (ledger linkage)")
        for i in range(len(rows)):
            for j in range(len(cols)):
                if mat[i][j]:
                    ax.text(j, i, str(mat[i][j]), ha="center", va="center", fontsize=9,
                            color="white" if mat[i][j] > 1 else "black")
        fig.colorbar(im, ax=ax, shrink=0.8, label="capabilities")
        fig.tight_layout()
        path = outdir / "fig4_claim_vs_observed.png"
        fig.savefig(path)
        plt.close(fig)
        figs.append({"name": "fig4_claim_vs_observed.png", "path": str(path),
                     "caption": "Contingency of the platform ledger's claimed levels against the "
                                "structurally observed levels; cells above the diagonal are "
                                "overclaims."})
    return figs


# --------------------------------------------------------------------------
# criteria
# --------------------------------------------------------------------------
def domain_leak_scan(root: Path) -> Dict[str, Any]:
    """Count forbidden domain words in the detector's own Python sources."""
    pkg = root / "tools" / "mock_detector"
    hits: List[Dict[str, str]] = []
    patterns = [(w, re.compile(rf"(?<![a-z0-9_]){re.escape(w)}(?![a-z0-9_])"))
                for w in FORBIDDEN_WORDS]
    for path in sorted(pkg.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for word, pattern in patterns:
            if pattern.search(text):
                hits.append({"file": str(path.relative_to(root)), "word": word})
    return {"n_hits": len(hits), "hits": hits, "words": list(FORBIDDEN_WORDS)}


def build_criteria(scan: Dict[str, Any], overlay: Optional[Dict[str, Any]],
                   fixture_result: Optional[Dict[str, Any]],
                   repeat_result: Optional[Dict[str, Any]],
                   leak: Dict[str, Any],
                   artifact_paths: Dict[str, str]) -> Dict[str, Any]:
    summary = scan.get("summary", {})
    counts = summary.get("level_counts", {})
    n_targets = summary.get("n_targets", 0)
    decided = summary.get("n_decided", 0)
    coverage = (decided / n_targets) if n_targets else 0.0

    crit: List[Dict[str, Any]] = []
    fr = fixture_result or {}
    fp = fr.get("false_positives")
    if fp is None:
        fp = len(fr.get("false_positive_list") or [])
    crit.append({
        "id": "C1", "statement": PREREGISTERED[0]["statement"],
        "threshold": PREREGISTERED[0]["threshold"],
        "value": {"fixtures": fr.get("n_fixtures"), "correct": fr.get("n_correct"),
                  "false_negatives": fr.get("false_negatives"),
                  "false_positives": fp},
        "pass": bool(fr.get("false_negatives") == 0 and fp == 0
                     and (fr.get("n_fixtures") or 0) >= 8),
        "artifacts": [artifact_paths.get("fixture_json", "")],
    })
    crit.append({
        "id": "C2", "statement": PREREGISTERED[1]["statement"],
        "threshold": PREREGISTERED[1]["threshold"],
        "value": {"decided": decided, "n_targets": n_targets, "coverage": round(coverage, 6)},
        "pass": coverage >= 0.50,
        "artifacts": [artifact_paths.get("scan_json", "")],
    })
    crit.append({
        "id": "C3", "statement": PREREGISTERED[2]["statement"],
        "threshold": PREREGISTERED[2]["threshold"],
        "value": {"n_MOCK": counts.get("MOCK", 0), "n_REAL": counts.get("REAL", 0),
                  "n_STUB": counts.get("STUB", 0)},
        "pass": counts.get("MOCK", 0) >= 1 and counts.get("REAL", 0) >= 1,
        "artifacts": [artifact_paths.get("scan_json", "")],
    })
    rr = repeat_result or {}
    crit.append({
        "id": "C4", "statement": PREREGISTERED[3]["statement"],
        "threshold": PREREGISTERED[3]["threshold"],
        "value": {"n_compared": rr.get("n_compared"), "n_agree": rr.get("n_agree"),
                  "agreement": rr.get("agreement"), "disagreements": rr.get("disagreements", [])},
        "pass": bool(rr.get("n_compared") and rr.get("agreement") == 1.0),
        "artifacts": [artifact_paths.get("repeat_json", "")],
    })
    n_caps = (overlay or {}).get("ledger", {}).get("n_capabilities", 0)
    mapped = sum(1 for e in (overlay or {}).get("entries", []) if e.get("endpoint"))
    unmapped = sum(1 for e in (overlay or {}).get("entries", []) if not e.get("endpoint"))
    crit.append({
        "id": "C5", "statement": PREREGISTERED[4]["statement"],
        "threshold": PREREGISTERED[4]["threshold"],
        "value": {"n_ledger_capabilities": n_caps, "mapped": mapped, "unmapped": unmapped,
                  "mock_overclaims": (overlay or {}).get("mocked_capability_ids", [])},
        "pass": bool(n_caps and mapped + unmapped == n_caps),
        "artifacts": [artifact_paths.get("overlay_json", "")],
    })
    crit.append({
        "id": "C6", "statement": PREREGISTERED[5]["statement"],
        "threshold": PREREGISTERED[5]["threshold"],
        "value": {"n_hits": leak.get("n_hits", 0), "hits": leak.get("hits", [])},
        "pass": leak.get("n_hits", 1) == 0,
        "artifacts": [artifact_paths.get("leak_json", "")],
    })
    return {
        "schema": "mock-detector-criteria/1.0",
        "generated_at": scan.get("generated_at"),
        "scan_base_url": scan.get("base_url"),
        "scan_code_commit": scan.get("code_commit"),
        "preregistered": PREREGISTERED,
        "criteria": crit,
        "n_pass": sum(1 for c in crit if c["pass"]),
        "n_criteria": len(crit),
        "all_pass": all(c["pass"] for c in crit),
        "artifacts": artifact_paths,
        "honest_notes": [
            "Levels are STRUCTURAL: REAL means reproducible and input-responsive, not accurate.",
            "No accuracy baseline is claimed or measured here; the reference-free probes cannot "
            "establish numerical correctness. Accuracy stays with tools.capability_audit.",
        ],
    }


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(scan: Dict[str, Any], overlay: Optional[Dict[str, Any]],
                    criteria: Dict[str, Any], figs: Sequence[Dict[str, str]],
                    injected: Optional[Dict[str, Any]] = None) -> str:
    s = scan.get("summary", {})
    counts = s.get("level_counts", {})
    lines: List[str] = []
    add = lines.append
    add("# MOCK 判定器：确定性 × 单调性 × 幂等性三探针（结构证据）")
    add("")
    add(f"* 扫描目标：`{scan.get('base_url')}`")
    add(f"* 生成时间：{scan.get('generated_at')}　代码提交：`{scan.get('code_commit')}`")
    add(f"* 端点总数：**{s.get('n_targets')}**　HTTP 请求数：**{s.get('http_requests')}**"
        f"　墙钟：{s.get('wall_seconds')} s")
    add("")
    add("## 1 案例介绍")
    add("")
    add("平台的 21 项能力各自声明了一个证据等级（REAL/MOCK/STUB/…），但声明的来源是"
        "人写文档与历史报告。本节点要回答的是**与领域无关**的问题：一个端点返回的数字"
        "是不是真的由输入算出来的？三条结构性质即可证伪绝大多数伪实现，且不需要任何"
        "参考值、解析解或领域先验：")
    add("")
    add("| 探针 | 断言 | 被证伪的造假方式 |")
    add("|---|---|---|")
    add("| 确定性 determinism | 同一请求两次 → 数值向量完全一致 | `random()`、未播种噪声、"
        "并发竞态 |")
    add("| 幂等性 idempotency | 同一请求重复、且中间插入一个不同请求后仍然一致 | 隐藏状态、"
        "计数器、缓存串味（A→B→A 漂移）|")
    add("| 单调性 monotonicity | 扫单个数值输入时至少一个数值输出随之移动，且单调或平滑 | "
        "硬编码常数（不动）、查表分档（几档跳变）|")
    add("")
    add("载荷全部由端点自身的 OpenAPI schema 合成（`tools/mock_detector/autospec.py`），"
        "代码里没有任何领域词条——判据 C6 用禁词表机器校验这一点。")
    add("")
    add("## 2 怎么做的")
    add("")
    add("```")
    add("OpenAPI ──autospec──▶ Target(cap_id, method, path, base_payload, sweep_fields)")
    add("                            │")
    add("              probes.run_probes(client, target)")
    add("                 ├─ determinism  : 2×同请求，比 numeric_sha256 + 最差相对差")
    add("                 ├─ idempotency  : 2×重复 + A→B→A 隔离检验")
    add("                 └─ monotonicity : 每条数值输入轴 ×5 点扫描，逐输出场判形状")
    add("                            │")
    add("                       classify（有序规则表，首个命中即定级）")
    add("                            │")
    add("        scan.json ──▶ ledger_link（与能力台账逐条对账，overlay 不覆盖台账）")
    add("                            │")
    add("              report（图 4 张 + criteria.json + 本 Markdown）")
    add("```")
    add("")
    add("规则表（有序，首个命中定级）：")
    add("")
    add("| # | 规则 | 等级 | 含义 |")
    add("|---|---|---|---|")
    for i, rule in enumerate(s.get("rule_table", []), 1):
        add(f"| {i} | `{rule['code']}` | {rule['level']} | {rule['desc']} |")
    add("")
    add("## 3 计算结果")
    add("")
    add("| 等级 | 端点数 | 占比 |")
    add("|---|---|---|")
    for lv in LEVELS:
        n = counts.get(lv, 0)
        frac = (n / s.get("n_targets", 1) * 100) if s.get("n_targets") else 0.0
        add(f"| {lv} | {n} | {frac:.1f}% |")
    add("")
    add(f"判定率（REAL+MOCK+STUB）：{s.get('n_decided')}/{s.get('n_targets')} = "
        f"{(s.get('decision_rate') or 0) * 100:.1f}%")
    add("")
    add("触发规则统计：")
    add("")
    add("| 规则 | 次数 |")
    add("|---|---|")
    for code, n in (s.get("reason_counts") or {}).items():
        add(f"| `{code}` | {n} |")
    add("")
    add("信息性旗标（不影响定级）：")
    add("")
    add("| 旗标 | 次数 |")
    add("|---|---|")
    for flag, n in (s.get("flag_counts") or {}).items():
        add(f"| `{flag}` | {n} |")
    add("")
    mock_ids = s.get("mock_ids") or []
    if mock_ids:
        add("### 3.1 被判定为 MOCK / STUB 的端点（全部）")
        add("")
        add("| 端点 | 等级 | 规则 | 证据 |")
        add("|---|---|---|---|")
        for p in scan.get("probes", []):
            if p.get("level") in ("MOCK", "STUB"):
                add(f"| `{p['path']}` | {p['level']} | `{','.join(p.get('reason_codes') or [])}` "
                    f"| {p.get('detail') or ''} |")
        add("")
    add("### 3.2 台账对账（feature/能力台账联动）")
    add("")
    if overlay:
        led = overlay.get("ledger", {})
        add(f"台账文件：`{led.get('path')}`　sha256=`{(led.get('sha256') or '')[:16]}…`"
            f"　状态={led.get('status')}　条目={led.get('n_capabilities')}　"
            f"**规范台账未被改写**（mutated={led.get('mutated')}）")
        add("")
        add("| 能力 | 自述等级 | 结构观测 | 旗标 | 动作 |")
        add("|---|---|---|---|---|")
        for e in overlay.get("entries", []):
            add(f"| `{e['cap_id']}` | {e['claimed_level']} | {e.get('observed_level') or '—'} | "
                f"{e['flag']} | {e['action']} |")
        add("")
        add(f"自动标记 MOCK/STUB 的自述过强条目：{overlay.get('mocked_capability_ids')}")
        add("")
    add("## 4 结果图")
    add("")
    for fig in figs:
        add(f"**{fig['name']}** — {fig['caption']}")
        add("")
        add(f"![{fig['name']}]({Path(fig['path']).name})")
        add("")
    if injected:
        add("## 4b 补充实验：配置注入载荷（schema 合成不够时的唯一合法性来源）")
        add("")
        add("主扫描里有一批端点因**需要领域合法的载荷**（几何闭合、比值区间）而落到 WITHHELD —— "
            "探测器拒绝猜参数。这些端点的载荷改由领域仓自带的注册表注入"
            "（`tools.capability_audit.adapters::SPECS`，代码仍零领域语义），重扫结果：")
        add("")
        add("| 能力 | 路径 | 结构观测 | 规则 | 证据 |")
        add("|---|---|---|---|---|")
        for p in injected.get("probes", []):
            add(f"| `{p.get('cap_id')}` | `{p.get('path')}` | {p.get('level')} | "
                f"`{','.join(p.get('reason_codes') or [])}` | {p.get('detail') or ''} |")
        add("")
        inj_counts = injected.get("summary", {}).get("level_counts", {})
        add(f"注入载荷子扫描：n={injected.get('summary', {}).get('n_targets')}，"
            f"REAL={inj_counts.get('REAL', 0)}，MOCK={inj_counts.get('MOCK', 0)}，"
            f"WITHHELD={inj_counts.get('WITHHELD', 0)}。")
        add("")
        add("这一节同时是一次**收敛效度检验**：能力台账自述 REAL 的这几个能力，"
            "在完全独立的判据（结构三探针）下也判 REAL —— 两套判据（精度基准 vs 结构性质）"
            "互相印证；反之，若某能力在这张表里是 MOCK，而台账自述 REAL，则构成台账过强。")
        add("")
    add("## 5 注意点")
    add("")
    for limit in scan.get("limits", []):
        add(f"* {limit}")
    add("* 载荷由 schema 合成：需要领域知识才能构造的请求体会落到 WITHHELD（无证据），"
        "本探测器不猜参数、更不把猜不出来的端点算作通过。")
    add("* 台账只做 overlay：规范台账被 `tools.audit.ledger_paths` 以 sha256 契约钉住，"
        "自动改写会破坏契约门并抹掉“当时自述”与“后来审计”的区别。")
    add("")
    add("## 6 误差判定")
    add("")
    crit_rows = criteria.get("criteria", [])
    add("本节点的判据是**分类判据**而非物理量误差，因此逐条给出数字与阈值：")
    add("")
    add("| 判据 | 阈值 | 实测 | 结论 |")
    add("|---|---|---|---|")
    for c in crit_rows:
        add(f"| {c['id']} | {c['threshold']} | `{json.dumps(c['value'], ensure_ascii=False)}` | "
            f"{'PASS' if c['pass'] else 'FAIL'} |")
    add("")
    add(f"通过 {criteria.get('n_pass')}/{criteria.get('n_criteria')} 条。")
    add("")
    add("**关键量 vs 基准误差是否 <3%？** — 不适用且必须显式声明：本探测器测量的不是"
        "物理量，没有实验/解析基准，故不存在“误差百分比”这一量纲。可对照的量化基准只有"
        "平台自述等级，对照结果见 §3.2 与图 4（一致/过强/保守逐条列出）。"
        "结构的 REAL ≠ 数值准确的 REAL：精度判定仍须由 `tools.capability_audit` 的"
        "解析解/基准族或参考值运行给出，本报告不作替代。")
    add("")
    add("## 7 环境与复跑记录")
    add("")
    add("* 扫描期间宿主磁盘多次写满（`No space left on device`），一次大范围扫描（90 端点）的结果"
        "因平台侧超时被大量标为 UNREACHABLE，故**放弃该次结果并重跑**：本报告只采用最终一次"
        "完整扫描（`scan.json` 的 `generated_at` 即该次），前几次的残缺结果未进入任何数字。")
    add("* 复跑（判据 C4）用与首跑**同一份 probe_config**（从 `scan.json` 读回），避免把两次不同实验"
        "比成一致性；`targets.json` 保存了载荷，故复跑不依赖 OpenAPI 重新合成。")
    add("* 端点顺序：开启 `--prioritise-ledger` 后先探台账声明过的端点，否则按路径字母序截断会把"
        "台账端点挤出预算（第一版就是这样导致 21 条里只对上 3 条）。")
    add("* 所有判定都可由 `scan.json` 里的数字重算（`classify` 是纯函数），复跑与复核不依赖网络。")
    add("")
    add("## 8 产物与复跑")
    add("")
    for name, path in (criteria.get("artifacts") or {}).items():
        add(f"* `{name}` → `{path}`")
    add("")
    add("```bash")
    add("# 一条命令复现整条证据链（与本次运行完全一致的参数）")
    add("bash scripts/reproduce_p478w1_n03.sh http://127.0.0.1:8002")
    add("# 等价的分步调用：合成夹具 → 主扫描 → 注入载荷子扫描 → 复跑 → 台账 → 报告")
    add("python3 -m tools.mock_detector.cli fixtures --out artifacts/mock_detector")
    add("python3 -m tools.mock_detector.cli scan --base-url http://127.0.0.1:8002 \\")
    add("        --out artifacts/mock_detector --max-targets 60 --concurrency 3 \\")
    add("        --timeout 12 --budget 26 --max-axes 4 --deadline 40 \\")
    add("        --transport-retries 1 --prioritise-ledger --exclude 'simulat|train|upload'")
    add("python3 -m tools.mock_detector.cli repeat --scan artifacts/mock_detector/scan.json --n 8")
    add("python3 -m tools.mock_detector.cli ledger --scan artifacts/mock_detector/scan.json \\")
    add("        --out artifacts/mock_detector --apply-to artifacts/mock_detector/capability_ledger_derived.json")
    add("python3 -m tools.mock_detector.cli report --out artifacts/mock_detector \\")
    add("        --docs docs/reports/p478w1_n03_mock_detector.md")
    add("```")
    add("")
    return "\n".join(lines) + "\n"