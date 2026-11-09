#!/usr/bin/env python3
"""Root-cause/technical inventory report for ludwig-cf/ludwig (OSS-INVENTORY).

Pipeline
--------
1. Resolve every ``(path, needle)`` reference in ``ludwig_evidence*.py``
   against a *real* source tree -> ``path:line``.  Unresolvable needle = hard
   failure (anti-fabrication gate).
2. Compute code statistics (LOC per subsystem, regression case counts) from
   the same tree -- no hand-typed numbers.
3. Render the eight-section markdown (report + machine blocks) and the PNG.
4. Optionally POST to ``{TENSOR_API_BASE}/tensor/reports``.

Usage
-----
    python3 tools/oss_inventory/ludwig_inventory.py --src /tmp/lud/ludwig-master \
        [--write] [--post] [--json out.json]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ludwig_evidence as ev_a          # noqa: E402
import ludwig_evidence_b as ev_b        # noqa: E402

TITLE = "【盘点】ludwig — 开源 LBM 技术盘点·ludwig-cf/ludwig（tier1 自由能/复杂流体）"
SCENARIO_KEY = "oss-inventory"


class PointerError(RuntimeError):
    pass


@dataclass
class Resolved:
    path: str
    line: int
    needle: str

    def __str__(self) -> str:  # file:line pointer used in the report
        return f"`{self.path}:{self.line}`"


def resolve(src: Path, path: str, needle: str) -> Resolved:
    f = src / path
    if not f.is_file():
        raise PointerError(f"missing file: {path}")
    for i, text in enumerate(f.read_text(errors="replace").splitlines(), start=1):
        if needle in text:
            return Resolved(path, i, needle)
    raise PointerError(f"needle not found: {path} :: {needle!r}")


def pointers(src: Path, refs) -> str:
    got = [str(resolve(src, p, n)) for p, n in refs]
    return "〔" + "；".join(got) + "〕"


def validate(src: Path) -> list[Resolved]:
    """Resolve all references; raises PointerError on the first bad one."""
    out: list[Resolved] = []
    for section, claims in list(ev_a.CLAIMS.items()) + [("8", ev_b.CLAIMS_8)]:
        for _text, refs in claims:
            for p, n in refs:
                out.append(resolve(src, p, n))
    for _slug, _tag, _text, refs in ev_b.MAPPING:
        for p, n in refs:
            out.append(resolve(src, p, n))
    return out


# ---------------------------------------------------------------------------
# Programmatic code statistics (no hand-typed numbers in the report)
# ---------------------------------------------------------------------------

PREFIX_GROUPS = [
    ("LBM 核心（碰撞/传播/格子/内存/内核）",
     ("collision", "lb_", "propagation", "stencil", "kernel", "memory", "distribution")),
    ("自由能与相场（fe_*/phi_*/symmetric/CH）",
     ("fe_", "symmetric", "brazovskii", "surfactant", "blue_phase", "angle_cosine",
      "leslie_ericksen", "polar_active", "cahn_hilliard", "phi_", "free_energy",
      "field", "gradient")),
    ("胶体/粒子-流体耦合（BBL/润滑/对势）",
     ("colloid", "bbl", "lubrication", "subgrid", "ewald", "pair_", "wall_ss_cut",
      "bond_fene", "driven_colloid", "interaction", "ran")),
    ("边界与几何（wall/map/lb_bc/hydro）",
     ("wall", "map", "lb_bc_", "hydro")),
    ("并行/IO/基础设施（coords/io/util/stats）",
     ("coords", "pe", "io_", "util", "timer", "runtime", "control", "physics", "noise",
      "visc", "leesedwards", "advection", "psi", "nernst_planck", "stats_",
      "ludwig", "main", "compiler", "model_le", "subgrid_")),
]


def code_stats(src: Path) -> dict:
    stats = {name: {"files": 0, "loc": 0} for name, _ in PREFIX_GROUPS}
    stats["其它"] = {"files": 0, "loc": 0}
    cfiles = sorted((src / "src").glob("*.c"))
    for f in cfiles:
        loc = len(f.read_text(errors="replace").splitlines())
        stem = f.stem
        for name, prefixes in PREFIX_GROUPS:
            if stem.startswith(prefixes):
                stats[name]["files"] += 1
                stats[name]["loc"] += loc
                break
        else:
            stats["其它"]["files"] += 1
            stats["其它"]["loc"] += loc
    out = {
        "groups": stats,
        "total_files": len(cfiles),
        "total_loc": sum(len(f.read_text(errors="replace").splitlines()) for f in cfiles),
        "unit_tests": len(list((src / "tests" / "unit").glob("test_*.c"))),
        "regression": {d.name: len(list(d.glob("*.inp")))
                       for d in sorted((src / "tests" / "regression").iterdir()) if d.is_dir()},
        "doc_tex": len(list((src / "docs").glob("*.tex"))),
        "configs": len(list((src / "config").glob("*.mk"))),
    }
    return out


ASCII_LABELS = {
    "LBM 核心（碰撞/传播/格子/内存/内核）": "LBM core\n(collision/propagation/lattice)",
    "自由能与相场（fe_*/phi_*/symmetric/CH）": "Free energy & phases\n(fe_*/phi_*/CH)",
    "胶体/粒子-流体耦合（BBL/润滑/对势）": "Colloid coupling\n(BBL/lubrication/pair)",
    "边界与几何（wall/map/lb_bc/hydro）": "Boundary & geometry\n(wall/map/lb_bc/hydro)",
    "并行/IO/基础设施（coords/io/util/stats）": "Parallel/IO/infra\n(coords/io/util/stats)",
    "其它": "other",
}


def make_figure(st: dict, out_png: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [k for k in st["groups"]]
    locs = [st["groups"][k]["loc"] for k in names]
    order = sorted(range(len(names)), key=lambda i: locs[i])

    fig, axes = plt.subplots(1, 2, figsize=(14.0, 4.8))
    ax = axes[0]
    ax.barh([ASCII_LABELS.get(names[i], names[i]) for i in order],
            [locs[i] for i in order], color="#3b6ea5")
    for y, i in enumerate(order):
        ax.text(locs[i] + 300, y, f"{locs[i]} loc / {st['groups'][names[i]]['files']} files",
                va="center", fontsize=8)
    ax.set_xlabel("source lines of C (.c)")
    ax.set_title(f"ludwig master: src/*.c total {st['total_loc']} LOC / {st['total_files']} files")
    ax.set_xlim(0, max(locs) * 1.4)

    ax = axes[1]
    reg = st["regression"]
    ks = sorted(reg)
    ax.bar(ks, [reg[k] for k in ks], color="#a5643b")
    for i, k in enumerate(ks):
        ax.text(i, reg[k] + 2, str(reg[k]), ha="center", fontsize=8)
    ax.set_ylabel("regression cases (.inp inputs per lattice)")
    ax.set_title(f"verification surface: {sum(reg.values())} regression cases + "
                 f"{st['unit_tests']} C unit tests + {st['configs']} build configs")
    ax.set_ylim(0, max(reg.values()) * 1.18)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return out_png


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(src: Path) -> tuple[str, dict]:
    st = code_stats(src)
    L: list[str] = []
    add = L.append

    add(f"## 一、概况\n")
    for text, refs in ev_a.CLAIMS["1"]:
        add(f"- {text} {pointers(src, refs)}")
    add("")
    add(f"- 代码体量（脚本统计）：`src/*.c` **{st['total_files']} 个文件 / "
        f"{st['total_loc']} 行**；LaTeX 文档 {st['doc_tex']} 篇；构建配置 {st['configs']} 套"
        f"（gcc / mpicc / nvcc / hipcc 等）。")
    add(f"- 验证面（脚本统计）：单元测试 {st['unit_tests']} 个 `test_*.c`，"
        f"回归 case {sum(st['regression'].values())} 个"
        f"（= `tests/regression/<格子>/*.inp` 输入文件数，逐格 "
        + "、".join(f"{k}:{v}" for k, v in sorted(st['regression'].items())) + "）。\n")

    add("## 二、方法谱系（格子 / 碰撞 / 力项 各自在哪、怎么组织）\n")
    for text, refs in ev_a.CLAIMS["2"]:
        add(f"- {text} {pointers(src, refs)}")
    add("")

    add("## 三、边界处理（BBL / 进出口 / 曲率-IBM）\n")
    for text, refs in ev_a.CLAIMS["3"]:
        add(f"- {text} {pointers(src, refs)}")
    add("")
    add("- **曲率/IBM 逐项定位结论**：壁面曲率由 link 细分 + Navier 滑移表达，"
        "胶体曲率由 link 命中 + 解析润滑修正表达；`grep -ri 'immersed\\|delta function' src` "
        "无 IBM 实现，故本仓**没有**插值型 IBM / 多重直接力（MDF）可抄。")
    add("")

    add("## 四、多相 / 多组元（重点：本仓路线与能力边界）\n")
    for text, refs in ev_a.CLAIMS["4"]:
        add(f"- {text} {pointers(src, refs)}")
    add("")
    add("### 4.1 与我们预期的差异（诚实记录）\n")
    add("- 派发单先验写「SCMP / gauge」，但代码本体里**既无 Shan-Chen 伪势，也无 gauge（规范）"
        "变换实现**；本仓多相=free-energy + Cahn-Hilliard。gauge 相关检索"
        "（`grep -rn gauge src`）零命中，故不编造。")
    add("- 派发单先验写「Fortran」，实测为 C（含 CUDA/HIP 宏），已在第一节更正。\n")

    add("## 五、GPU / 并行（布局 / 合并 / 分区 / 通信隐藏）\n")
    for text, refs in ev_a.CLAIMS["5"]:
        add(f"- {text} {pointers(src, refs)}")
    add("")
    add("### 5.1 代码体量分布（脚本统计，用于判断可复用代码的「重心」）\n")
    add("| 子系统 | .c 文件数 | 行数 |")
    add("|---|---:|---:|")
    for k, v in sorted(st["groups"].items(), key=lambda kv: -kv[1]["loc"]):
        add(f"| {k} | {v['files']} | {v['loc']} |")
    add(f"| **合计** | **{st['total_files']}** | **{st['total_loc']}** |")
    add("")
    add("见图 `figures/ludwig_inventory.png`（左：子系统行数；右：按格子的回归 case 数）。\n")

    add("## 六、可微 / 代码生成\n")
    for text, refs in ev_a.CLAIMS["6"]:
        add(f"- {text} {pointers(src, refs)}")
    add("")

    add("## 七、与我们功能点的映射（feature_ledger）\n")
    add("| feature_ledger slug | transfer | 本仓实现方式/证据 |")
    add("|---|---|---|")
    for slug, tag, text, refs in ev_b.MAPPING:
        add(f"| `{slug}` | `{tag}` | {text} {pointers(src, refs)} |")
    add("")
    add("计入 `fits`/`adapt`/`reference` 的统计："
        f"fits={sum(1 for m in ev_b.MAPPING if m[1] == 'fits')}、"
        f"adapt={sum(1 for m in ev_b.MAPPING if m[1] == 'adapt')}、"
        f"reference={sum(1 for m in ev_b.MAPPING if m[1] == 'reference')}；"
        f"其中 NEW 建议 {sum(1 for m in ev_b.MAPPING if m[0].startswith('NEW'))} 条。")
    add("")

    add("## 八、可吸收 Top3 + 风险\n")
    for text, refs in ev_b.CLAIMS_8:
        add(f"- {text} {pointers(src, refs)}")
    add("")
    add("### 8.1 明确不可吸收 / 需自建的清单\n")
    add("- 无 Shan-Chen 互作用势与 G_αβ 参数 → MCMP 互作用模型须自建（本仓只给自由能参照）。")
    add("- 无密度比机制 → 高密度比能力须自建（变密度平衡态 + ρ 非旁观）。")
    add("- 无 free-surface → 自由面须自建（可借 map 空单元 + 面通量无穿透思路）。")
    add("- 无 Allen-Cahn、无 KBC/cumulant 碰撞、无 IBM、无 AD/代码生成 → 均需自建或另找来源。")
    add("")

    add("## 九、方法与可复现性（机器块前的自证）\n")
    add(f"- 源码获取：`codeload.github.com` tarball（`git clone` 在 120 s 内未完成、"
        f"`api.github.com` 递归树遇到 403 限流；tarball 路线成功），"
        f"sha256=`{ev_a.SNAPSHOT_SHA256}`，解包于 /tmp（**未** clone 进 TensorPlatform 仓）。")
    add("- 每条结论的指针由脚本按 `(file, needle)` 现场解析成 `file:line`，"
        "needle 找不到即构建失败——报告里不存在手抄行号。")
    add(f"- 指针总数：{len(validate(src))} 条，全部解析成功。")
    add("- 体量/计数类数字（文件数、行数、case 数）由同一脚本统计后注入，零手抄。")
    add("")

    machine = ev_b.MACHINE
    add("```text")
    add("[OSS-INV]")
    for k in ("repo", "lang", "license", "methods", "multiphase", "boundary", "gpu", "summary"):
        add(f"{k}: {machine[k]}")
    add("[/OSS-INV]")
    add("```")
    add("")
    map_by_slug = {m[0]: m for m in ev_b.MAPPING}
    for slug, tag, transfer, method, files, conv in ev_b.SOLS:
        # files pointer is resolved from the evidence table, never hand-typed
        if slug in map_by_slug:
            files = ", ".join(str(r) for r in
                              (resolve(src, pp, nn) for pp, nn in map_by_slug[slug][3]))
        add("```text")
        add("[OSS-SOL]")
        add(f"repo: {machine['repo']}")
        add(f"slug: {slug}")
        add(f"tag: {tag}")
        add(f"method: {method}")
        add(f"files: {files}")
        add(f"conventions: {conv}")
        add(f"transfer: {transfer}")
        add("[/OSS-SOL]")
        add("")

    return "\n".join(L), st


def figure_caption(st: dict) -> str:
    return (f"ludwig master 源码结构与验证面统计：src/*.c 共 {st['total_loc']} 行/"
            f"{st['total_files']} 文件；tests/regression 共 {sum(st['regression'].values())} 个"
            f" case，单位测试 {st['unit_tests']} 个。全部数字由脚本统计注入。")


# ---------------------------------------------------------------------------
# Platform publication
# ---------------------------------------------------------------------------

def _post(path: str, payload: dict, token: str | None = None) -> dict:
    base = os.environ["TENSOR_API_BASE"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def publish(markdown: str, caption: str, png: Path, dry_run: bool = False) -> dict:
    token = _post("/auth/login", {
        "username": os.environ["TENSOR_AGENT_USER"],
        "password": os.environ["TENSOR_AGENT_PASS"],
    }).get("access_token")
    payload = {
        "title": TITLE,
        "summary": ("ludwig 单项目深盘：C 版自由能 LBM（复杂流体），BSD-3 可闭源复用；"
                    "无 Shan-Chen/无密度比/无自由面/无 IBM/无 AD；可吸收 free-energy vtable、"
                    "面通量守恒 CH、按域容差回归面。"),
        "scenario_key": SCENARIO_KEY,
        "mission_id": ev_a.MISSION_ID,
        "tags": ["oss-inventory", "lbm", "free-energy", "cahn-hilliard", "multiphase", "ludwig"],
        "markdown": markdown,
        "figures": [{
            "name": "ludwig_inventory.png",
            "caption": caption,
            "png_base64": base64.b64encode(png.read_bytes()).decode(),
        }],
    }
    if dry_run:
        return {"dry_run": True, "title": payload["title"], "bytes": len(markdown)}
    return _post("/tensor/reports", payload, token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="extracted ludwig source tree")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo-root", default=str(HERE.parent.parent))
    args = ap.parse_args()

    src = Path(args.src).resolve()
    markdown, st = render(src)
    caption = figure_caption(st)
    png = Path(args.repo_root) / "artifacts" / "oss_inventory" / "ludwig_inventory.png"
    make_figure(st, png)
    print(f"[ok] pointers resolved, md={len(markdown)} chars, figure={png}")

    if args.write:
        doc = Path(args.repo_root) / "docs" / "oss_inventory" / "OSS-INV-ludwig.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(markdown + "\n")
        (doc.parent / "ludwig_stats.json").write_text(json.dumps(st, indent=2) + "\n")
        print(f"[ok] wrote {doc}")

    res = publish(markdown, caption, png, dry_run=args.dry_run or not args.post)
    print("[ok] publish:", json.dumps(res, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())