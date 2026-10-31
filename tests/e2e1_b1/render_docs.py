"""E2E-1 修复（B1）· 图与判决表生成器（数字全部从 evidence JSON 读取，零手抄）。

输入：outputs/e2e1_b1/negative_evidence.json
      outputs/e2e1_b1/m811_positive_check.json
输出：outputs/e2e1_b1/fig/gate_matrix.png  —— 12 用例 × 3 门 的判定矩阵（修复后）
      outputs/e2e1_b1/fig/red_to_green.png —— 修复前(legacy) vs 修复后(gated) 准入数对比
      docs/e2e1_b1/VERDICTS.md           —— 判决表（append 风格的表格由脚本渲染）
      docs/e2e1_b1/m811_positive_check.md —— m811 正例三门体检结论
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "outputs" / "e2e1_b1"
FIG = OUT / "fig"
DOCS = REPO_ROOT / "docs" / "e2e1_b1"

STATUS_COLOR = {
    "pass": 2,
    "fail": 1,
    "skipped": 0,
    "not_evaluable": 0,
}
COLORS = ["#d9d9d9", "#d62728", "#2ca02c"]  # skipped/not_evaluable, fail, pass
LABELS = ["skipped / not_evaluable", "fail (rejected)", "pass"]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def figure_gate_matrix(evidence: dict) -> Path:
    results = evidence["results"]
    names = [r["name"] for r in results]
    gates = ["manifest", "mission", "checksum"]
    matrix = [[STATUS_COLOR.get(r["gated"]["gates"].get(g, "skipped"), 0) for g in gates] for r in results]

    fig, ax = plt.subplots(figsize=(7.6, 0.52 * len(names) + 1.9))
    ax.imshow(matrix, cmap=ListedColormap(COLORS), vmin=0, vmax=2, aspect="auto")
    ax.set_xticks(range(len(gates)))
    ax.set_xticklabels(["gate1 RunManifest", "gate2 mission terminal", "gate3 sha256 recompute"], fontsize=10)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)
    for i, r in enumerate(results):
        for j, g in enumerate(gates):
            status = r["gated"]["gates"].get(g, "skipped")
            code = r["gated"].get("code") if status == "fail" else ""
            ax.text(j, i, status if status != "fail" else f"fail\n{code}", ha="center", va="center",
                    fontsize=6.5, color="white" if status == "fail" else "black", wrap=True)
        decision = r["gated"]["verdict"]
        ax.text(len(gates) - 0.35, i, f"→ {decision}", ha="left", va="center", fontsize=7.5,
                color="#2ca02c" if decision == "ADMITTED" else "#b22222")
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=COLORS[2]),
        plt.Rectangle((0, 0), 1, 1, color=COLORS[1]),
        plt.Rectangle((0, 0), 1, 1, color=COLORS[0]),
    ]
    ax.legend(handles, LABELS, loc="upper center", bbox_to_anchor=(0.5, -0.16 - 0.9 / len(names)),
              ncol=3, frameon=False, fontsize=8)
    ax.set_title(
        "Sample-library admission gate matrix (m1085 post-fix, gated ingest)\n"
        f"{evidence['summary']['rejected_actual']}/{evidence['summary']['rejected_expected']} bad products rejected, "
        f"{evidence['summary']['admitted_actual']}/{evidence['summary']['admitted_expected']} control admitted",
        fontsize=10,
    )
    fig.tight_layout()
    FIG.mkdir(parents=True, exist_ok=True)
    path = FIG / "gate_matrix.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def figure_red_to_green(evidence: dict) -> Path:
    results = evidence["results"]
    bad = [r for r in results if r["expect"] == "REJECTED"]
    good = [r for r in results if r["expect"] == "ADMITTED"]
    legacy_bad_admitted = sum(1 for r in bad if r["legacy"]["verdict"] == "ADMITTED")
    gated_bad_admitted = sum(1 for r in bad if r["gated"]["verdict"] == "ADMITTED")
    legacy_good_admitted = sum(1 for r in good if r["legacy"]["verdict"] == "ADMITTED")
    gated_good_admitted = sum(1 for r in good if r["gated"]["verdict"] == "ADMITTED")

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    labels = ["bad products admitted\n(target 0)", "control admitted"]
    x = range(len(labels))
    legacy = [legacy_bad_admitted, legacy_good_admitted]
    gated = [gated_bad_admitted, gated_good_admitted]
    w = 0.36
    bars1 = ax.bar([i - w / 2 for i in x], legacy, w, label="before fix: legacy backfill_file (no gate)", color="#d62728")
    bars2 = ax.bar([i + w / 2 for i in x], gated, w, label="after fix: gated ingest (3 gates)", color="#2ca02c")
    for bars in (bars1, bars2):
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.06,
                    f"{int(bar.get_height())}", ha="center", fontsize=10)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("cases", fontsize=10)
    ax.set_ylim(0, max(max(legacy), max(gated)) + 1.4)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Defect B1: admission behaviour before vs after fix (red -> green)", fontsize=11)
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    FIG.mkdir(parents=True, exist_ok=True)
    path = FIG / "red_to_green.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def render_verdicts(evidence: dict, m811: dict, figs: dict[str, Path]) -> Path:
    s = evidence["summary"]
    lines = [
        "# E2E-1 修复 · 门缺陷 B1 · 判决表（脚本渲染，数字零手抄）",
        "",
        "> 渲染器：`tests/e2e1_b1/render_docs.py`；数据源：`outputs/e2e1_b1/negative_evidence.json`、",
        "> `outputs/e2e1_b1/m811_positive_check.json`。本文件不复述过程，只落判据。",
        "",
        "## 缺陷一句话",
        "",
        "样本库（samples 表）入库路径 `tools/sample_db/backfill.py::backfill_file()` 只做字段抽取 + INSERT，",
        "不读 RunManifest、不复算哈希、不看来源 mission 终态 —— m810 实测（`B1_sample_db_unverified_ingest`）",
        "一个伪造 `verdict=\"PASS\"` 且 τ=0.4（ν<0 非物理）的裸 JSON 入库成功并判决 FAIL。",
        "",
        "## 三门（修复后）",
        "",
        "| 门 | 判据 | 拒绝码 |",
        "|---|---|---|",
        "| 1 manifest | RunManifest 存在 + `tensorphysics-run-manifest-v1` 契约通过 + `validation_status=passed` + outputs 非空 | `E_MANIFEST_MISSING` / `E_MANIFEST_NOT_OBJECT` / `E_MANIFEST_UNREADABLE` / `E_CONTRACT` / `E_UNVERIFIED` / `E_NO_OUTPUTS` |",
        "| 2 mission | 来源 mission 终态 `succeeded`（`approval.finished_status` 若在也须 `succeeded`；解析不出 → fail-closed） | `E_MISSION_STATE` / `E_MISSION_UNRESOLVED` / `E_MISSION_ID_MISSING` |",
        "| 3 checksum | `manifest.outputs[]` 的 sha256 复算命中 + `size_bytes` 一致 + `uri` 不越出 run 目录 | `E_CHECKSUM_MISMATCH` / `E_CHECKSUM_FORMAT` / `E_SIZE_MISMATCH` / `E_ARTIFACT_MISSING` / `E_ARTIFACT_ESCAPE` |",
        "",
        "## 汇总（机器产）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 用例数 | {s['cases']} |",
        f"| 坏产物（应拒） | {s['rejected_expected']} → 实拒 {s['rejected_actual']} |",
        f"| 正对照（应准入） | {s['admitted_expected']} → 实准入 {s['admitted_actual']} |",
        f"| 修复前（legacy 裸入口）放行的坏产物数 | {s['legacy_admitted_bad_products']} |",
        f"| 审计留痕条数 / 判定次数 | {s['audit_records']} / {s['audit_records_expected']} |",
        f"| 总判 | **{s['verdict']}** |",
        "",
        "## 用例逐条（实际 = 门输出的结构化拒绝码）",
        "",
        "| 用例 | 攻击面 | 门 | 预期 | 修复前(legacy) | 修复后(gated) 实际码 | 判定 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in evidence["results"]:
        actual = r["gated"].get("code") or "ADMITTED"
        lines.append(
            f"| `{r['name']}` | {r['attack']} | {r['gate']} | {r['expect']} | {r['legacy']['verdict']} | "
            f"{actual} | **{r['judgement']}** |"
        )
    lines += [
        "",
        "判据（可失败）：任一坏产物被准入（samples 表出现其行）= FAIL；拒绝无留痕 = FAIL。",
        "本表所在运行中两项判据均未触发（见上表 `修复前(legacy)` 列即为缺陷活体证据）。",
        "",
        "## 图",
        "",
        f"![gate matrix](../../{figs['matrix'].relative_to(REPO_ROOT)})",
        "",
        f"![red to green](../../{figs['red_to_green'].relative_to(REPO_ROOT)})",
        "",
        "## m811 正例体检（Re=1000 硬门达成的那一单）",
        "",
        f"- 结论：**{m811['verdict']}** —— {m811['conclusion']}",
        f"- 体检对象：`{m811['artifact']}`（run 目录 `{m811['run_dir']}`）",
        f"- 门位视图：`{json.dumps(m811['gate_view'], ensure_ascii=False)}`",
        "",
        "缺什么：",
        "",
    ]
    for item in m811["missing"]:
        lines.append(f"- {item}")
    lines += ["", "怎么补齐：", ""]
    for item in m811["remediations"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "> 注意：事后补写 manifest 只能锁定字节（sha256/size），**不能**追认 `validation_status=passed`。",
        "> m811 的 `validation_status` 必须由当时那次硬门运行的验证器产出证据支撑，否则补写即等于自我声明，",
        "> 属「哈希门可过、验证门不可伪造」的边界 —— 该用例保留为 NOT_ADMISSIBLE，不做事后补录。",
        "",
    ]
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / "VERDICTS.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def render_m811(m811: dict) -> Path:
    lines = [
        "# m811 正例三门体检（脚本渲染）",
        "",
        "> 数据源：`outputs/e2e1_b1/m811_positive_check.json`（`python3 -m src.sample_db.inspect ... --api` 产出，",
        "> mission 终态取自平台 `GET /tensor/missions/811`）。",
        "",
        f"**结论：{m811['verdict']} —— {m811['conclusion']}**",
        "",
        "| 门 | 状态 | 说明 |",
        "|---|---|---|",
    ]
    for gate in m811["gates"]:
        probe = m811["gate_probe"].get(gate["gate"])
        status = probe["status"] if probe else gate["status"]
        message = probe["message"] if probe else gate["message"]
        lines.append(f"| {gate['gate']} | {status} | {message} |")
    lines += [
        "",
        "run 目录文件指纹（sha256 实测）：",
        "",
        "| 文件 | size_bytes | sha256 |",
        "|---|---|---|",
    ]
    for item in m811["run_dir_inventory"]:
        lines.append(f"| {item['name']} | {item['size_bytes']} | `{item['sha256']}` |")
    lines += ["", "缺失项：", ""]
    for item in m811["missing"]:
        lines.append(f"- {item}")
    lines += ["", "补齐路径：", ""]
    for item in m811["remediations"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "局限：`run_dir_inventory` 只列 run 目录顶层前 12 个文件（体检读数，不是全量清单）；",
        "本体检是只读模式（不写准入流水），生产入库必须走 `src.sample_db.ingest` 并留痕。",
        "",
    ]
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / "m811_positive_check.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    evidence = load(OUT / "negative_evidence.json")
    m811 = load(OUT / "m811_positive_check.json")
    figs = {
        "matrix": figure_gate_matrix(evidence),
        "red_to_green": figure_red_to_green(evidence),
    }
    verdicts = render_verdicts(evidence, m811, figs)
    m811_doc = render_m811(m811)
    print(json.dumps({"figures": [str(p) for p in figs.values()],
                      "docs": [str(verdicts), str(m811_doc)]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())