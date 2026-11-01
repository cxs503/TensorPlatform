"""从 artifacts/mutation_check.json 生成报告图（零手抄：所有数字都从 JSON 读）。

fig1_auth_guard_decision_matrix.png
    左：18 条免登录配图真值表 —— 期望 / 基线 / M1(正则失效) 三列判定矩阵；
    中：5 条 /tp root_path 用例 —— 期望 / 基线 / M2(剥离块删除)；
    右：实时平台 8 条只读探针的 HTTP 状态。
    绿=放行(allow/2xx)，红=拒绝(deny/401)，橙=其他(404 等)，灰边框=判定不符期望。

运行： python3 docs/audit/gen_figures.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "artifacts", "mutation_check.json")
FIGDIR = os.path.join(HERE, "figures")


def _matrix(ax, rows, cols, title):
    """rows=标签, cols=[(列名, [0/1...]), ...]；1=allow, 0=deny。"""
    grid = [[vals[i] for _, vals in cols] for i in range(len(rows))]
    cmap = ListedColormap(["#d9534f", "#5cb85c"])
    ax.imshow(grid, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([c for c, _ in cols], rotation=25, ha="right", fontsize=8)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(rows, fontsize=6.5)
    ax.set_title(title, fontsize=9)
    # 期望与实际不符 → 橙框
    for i, row in enumerate(rows):
        for j, (_, vals) in enumerate(cols):
            if j == 0:
                continue
            if vals[i] != cols[0][1][i]:
                ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False,
                                       edgecolor="#f0ad4e", lw=2.0))
    ax.set_xticks([x - 0.5 for x in range(1, len(cols))], minor=True)
    ax.grid(which="minor", color="white", lw=1.2)


def main():
    d = json.load(open(DATA, encoding="utf-8"))
    os.makedirs(FIGDIR, exist_ok=True)
    base, m1, m2 = d["baseline"]["detail"], d["mutation_M1_figures_regex_disabled"]["detail"], \
        d["mutation_M2_root_path_strip_removed"]["detail"]

    fig = plt.figure(figsize=(15.0, 6.4), constrained_layout=True)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.25, 1.0, 1.0])

    # ── A. 免登录配图真值表 ──
    frows = ["%s %s" % (r["method"], r["path"].replace("/api/v1/tensor/reports", "…")) for r in base["figures"]]
    _matrix(fig.add_subplot(gs[0, 0]), frows, [
        ("expected", [1 if r["expect_allow"] else 0 for r in base["figures"]]),
        ("baseline", [1 if r["got_allow"] else 0 for r in base["figures"]]),
        ("M1: no figures rule", [1 if r["got_allow"] else 0 for r in m1["figures"]]),
    ], "A. figure free-access truth table (18 cases)\ngreen=skip auth, red=require auth, orange=violates expectation")

    # ── B. root_path 剥离 ──
    rrows = ["%s %s (root=%s)" % (r["method"], r["path"].replace("/api/v1", "…"), r["root_path"] or "-")
             for r in base["root_path"]]
    _matrix(fig.add_subplot(gs[0, 1]), rrows, [
        ("expected", [1 if r["expect_forward"] else 0 for r in base["root_path"]]),
        ("baseline", [1 if r["got_forward"] else 0 for r in base["root_path"]]),
        ("M2: strip removed", [1 if r["got_forward"] else 0 for r in m2["root_path"]]),
    ], "B. /tp root_path fault injection (5 cases)")

    # ── C. 实时平台探针 ──
    ax = fig.add_subplot(gs[0, 2])
    live = d["live_platform"]
    names = [x["case"].replace("_", "\n") for x in live]
    codes = [x["status"] or 0 for x in live]
    colors = ["#5cb85c" if c and 200 <= c < 300 else "#d9534f" if c in (401, 403)
              else "#f0ad4e" if c else "#999999" for c in codes]
    ax.barh(range(len(live)), codes, color=colors)
    for i, c in enumerate(codes):
        ax.text(c + 6, i, str(c) if c else "n/a", va="center", fontsize=7)
    ax.set_yticks(range(len(live)))
    ax.set_yticklabels(names, fontsize=6.5)
    ax.set_xlim(0, 480)
    ax.invert_yaxis()
    ax.set_xlabel("HTTP status (live :8010, read-only GET)")
    ax.set_title("C. live platform probes\n(green 2xx = fix works, red 401 = still gated, orange = 404)", fontsize=9)

    out = os.path.join(FIGDIR, "fig1_auth_guard_decision_matrix.png")
    fig.savefig(out, dpi=160)
    print(json.dumps({
        "path": out,
        "baseline": d["baseline"]["summary"],
        "M1": d["mutation_M1_figures_regex_disabled"]["summary"],
        "M2": d["mutation_M2_root_path_strip_removed"]["summary"],
        "live": [{"case": x["case"], "status": x["status"]} for x in live],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()