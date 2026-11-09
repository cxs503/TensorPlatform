#!/usr/bin/env python3
"""gate_content_criteria_demo —— 门判据内容化的 A/B 对照产物（可离线复算）。

对 8 个**合成**门场景（零领域语义）同时跑两条判定路径：

* **旧门**（``gate_auto_release.evaluate_gate`` 未声明 ``gate_criteria.checks``）：
  只看工件存在 / 非空 / JSON 可解析 —— 空洞 ``{}``、陈旧数字、摘要漂移都能放门；
* **新门**（同一门声明 ``gate_criteria.checks``）：逐条求值内容判据，三态裁决。

产物（全部程序化生成，数字零手抄）：

    artifacts/gate_content_criteria/ab_matrix.json  逐场景 旧/新 判定 + 证据
    artifacts/gate_content_criteria/ab_matrix.png   对照矩阵 + 三态计数
    artifacts/gate_content_criteria/ab_matrix.md    论文用表

用法::

    python3 ops/taskplane/gate_content_criteria_demo.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ops.taskplane import gate_content_criteria as cc  # noqa: E402
from ops.taskplane.gate_auto_release import (  # noqa: E402
    ACTION_RELEASE,
    ACTION_WAIT,
    FROM_STATUS,
    evaluate_gate,
)

OUT = REPO / "artifacts" / "gate_content_criteria"

BASE_SPEC = {"id": "residual", "kind": "json_pointer",
             "path": "artifacts/demo/verdict.json",
             "pointer": "/checks/residual", "op": "<=", "value": 0.03}
FIGURE_SPEC = {"id": "fig_bytes", "kind": "min_bytes",
               "path": "artifacts/demo/field.png", "value": 64}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate(meta: dict) -> dict:
    return {"id": 1, "plan_id": 1, "name": "G-CONTENT", "node_type": "gate",
            "status": FROM_STATUS, "dependencies": [2], "metadata": meta}


def _done() -> dict:
    return {"id": 2, "plan_id": 1, "name": "upstream", "node_type": "task",
            "status": "done", "dependencies": [], "metadata": {}}


def _fixtures() -> list[dict]:
    """8 个场景：每个带独立 setup(root)，互不污染（合成夹具，零领域语义）。"""
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 2
    good = {"verdict": "PASS", "checks": {"residual": 0.012}}

    def demo(root: Path) -> Path:
        d = root / "artifacts" / "demo"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_json(root: Path, payload: Any) -> None:
        (demo(root) / "verdict.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def with_png(root: Path) -> None:
        (demo(root) / "field.png").write_bytes(png)

    def with_report(root: Path, text: str) -> None:
        docs = root / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "report.md").write_text(text, encoding="utf-8")

    png_sha = hashlib.sha256(png).hexdigest()

    def s_content_ok(root):
        write_json(root, good); with_png(root)

    def s_hollow(root):
        write_json(root, {})

    def s_stale(root):
        write_json(root, {"verdict": "PASS", "checks": {"residual": 0.412}})

    def s_corrupt(root):
        demo(root)  # 目录在、文件半写
        (demo(root) / "verdict.json").write_text("{not json", encoding="utf-8")

    def s_empty(root):
        demo(root)
        (demo(root) / "verdict.json").write_text("", encoding="utf-8")

    def s_digest_drift(root):
        write_json(root, good); with_png(root)

    def s_traversal(root):
        write_json(root, good)
        (root / "verdict.json").write_text(json.dumps(good), encoding="utf-8")

    def s_placeholder(root):
        write_json(root, good); with_report(root, "residual: TODO\n")

    specs = [
        dict(name="content_ok", desc="判据数字齐备且达标 (0.012 <= 0.03)，摘要相符",
             setup=s_content_ok, expect_new=cc.VERDICT_PASS,
             checks=[BASE_SPEC, {"id": "digest", "kind": "sha256",
                                 "path": "artifacts/demo/field.png", "digest": png_sha}]),
        dict(name="hollow_object", desc="空洞工件 {}（旧门放行；没有任何数字）",
             setup=s_hollow, expect_new=cc.VERDICT_WITHHELD, checks=[BASE_SPEC]),
        dict(name="stale_number", desc="数字陈旧/超标 (0.412 > 0.03)",
             setup=s_stale, expect_new=cc.VERDICT_FAIL, checks=[BASE_SPEC]),
        dict(name="corrupt_json", desc="JSON 半写/损坏",
             setup=s_corrupt, expect_new=cc.VERDICT_WITHHELD, checks=[BASE_SPEC]),
        dict(name="empty_file", desc="0 字节工件",
             setup=s_empty, expect_new=cc.VERDICT_WITHHELD, checks=[BASE_SPEC]),
        dict(name="digest_drift", desc="数字合规但图摘要不符声明的 sha256",
             setup=s_digest_drift, expect_new=cc.VERDICT_FAIL,
             checks=[{"id": "digest", "kind": "sha256",
                      "path": "artifacts/demo/field.png", "digest": "0" * 64}]),
        dict(name="path_traversal", desc="判据路径逃逸 roots（../ 上游目录）",
             setup=s_traversal, expect_new=cc.VERDICT_FAIL,
             checks=[dict(BASE_SPEC, path="../verdict.json")]),
        dict(name="placeholder_text", desc="报告里还留着 TODO 占位（数字未落）",
             setup=s_placeholder, expect_new=cc.VERDICT_FAIL,
             checks=[BASE_SPEC, {"id": "no_todo", "kind": "text_contains",
                                 "path": "docs/report.md", "pattern": "TODO",
                                 "negate": True}]),
    ]
    for spec in specs:
        spec["artifacts"] = ["artifacts/demo/verdict.json"]
        if spec["name"] in ("content_ok", "digest_drift"):
            spec["artifacts"].append("artifacts/demo/field.png")
        if spec["name"] == "placeholder_text":
            spec["artifacts"].append("docs/report.md")
    return specs


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for spec in _fixtures():
            root = base / spec["name"]          # 每场景独立根目录，杜绝夹具串味
            root.mkdir(parents=True, exist_ok=True)
            spec["setup"](root)
            legacy_meta = {"gate_criteria": {"artifacts": list(spec["artifacts"])}}
            new_meta = {"gate_criteria": {"artifacts": list(spec["artifacts"]),
                                          "checks": spec["checks"]}}
            node_old = _gate(legacy_meta)
            node_new = _gate(new_meta)
            old = evaluate_gate(node_old, {2: _done(), 1: node_old}, [str(root)])
            new = evaluate_gate(node_new, {2: _done(), 1: node_new}, [str(root)])
            content = cc.evaluate_content(new_meta, [str(root)])
            rows.append({
                "scenario": spec["name"],
                "description": spec["desc"],
                "old_action": old.action,
                "old_reason": old.reason,
                "new_action": new.action,
                "new_reason": new.reason,
                "content_verdict": content.verdict,
                "content_digest": content.digest(),
                "check_details": [{"id": c.spec.id, "verdict": c.verdict,
                                   "observed": c.observed, "detail": c.detail}
                                  for c in content.checks],
                "expected_verdict": spec["expect_new"],
                "files": sorted(str(p.relative_to(root))
                                for p in (root / "artifacts").rglob("*") if p.is_file()),
            })

    payload = {
        "schema": "gate_content_criteria/ab_matrix/v1",
        "generated_by": "ops/taskplane/gate_content_criteria_demo.py",
        "roots_note": "每个场景独立临时根目录 + 合成夹具，零领域语义",
        "counts": {
            "scenarios": len(rows),
            "old_released": sum(1 for r in rows if r["old_action"] == ACTION_RELEASE),
            "old_waited": sum(1 for r in rows if r["old_action"] == ACTION_WAIT),
            "new_released": sum(1 for r in rows if r["new_action"] == ACTION_RELEASE),
            "new_waited": sum(1 for r in rows if r["new_action"] == ACTION_WAIT),
            "new_verdict_match": sum(1 for r in rows
                                     if r["content_verdict"] == r["expected_verdict"]),
            "false_release_prevented": sum(
                1 for r in rows
                if r["old_action"] == ACTION_RELEASE and r["new_action"] != ACTION_RELEASE),
        },
        "rows": rows,
    }
    (OUT / "ab_matrix.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")
    (OUT / "ab_matrix.md").write_text(_markdown(payload), encoding="utf-8")
    _figure(payload)
    return payload


def _markdown(payload: dict) -> str:
    lines = [
        "# 门判据内容化 A/B 对照（合成夹具）",
        "",
        f"- 场景数: {payload['counts']['scenarios']}",
        f"- 旧门放行: {payload['counts']['old_released']}  "
        f"；其中 {payload['counts']['false_release_prevented']} 例为空洞/陈旧/漂移工件的**误放行**",
        f"- 新门放行: {payload['counts']['new_released']}",
        f"- 三态裁决与预期一致: {payload['counts']['new_verdict_match']}/"
        f"{payload['counts']['scenarios']}",
        "",
        "| 场景 | 说明 | 旧门 action | 新门 action | 新门 reason | 内容裁决 | digest(前12) |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in payload["rows"]:
        lines.append("| {s} | {d} | {oa} | {na} | {nr} | {cv} | `{dg}` |".format(
            s=row["scenario"], d=row["description"], oa=row["old_action"],
            na=row["new_action"], nr=row["new_reason"], cv=row["content_verdict"],
            dg=row["content_digest"][:12]))
    lines += ["", "复算命令: `python3 ops/taskplane/gate_content_criteria_demo.py`", ""]
    return "\n".join(lines)


def _figure(payload: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    rows = payload["rows"]
    names = [r["scenario"] for r in rows]
    # 编码：0=放行 1=等待 2=... 用颜色区分「是否拦下」
    old_code = [0 if r["old_action"] == ACTION_RELEASE else 1 for r in rows]
    new_code = [{"PASS": 0, "WITHHELD": 2, "FAIL": 3}[r["content_verdict"]] for r in rows]

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(13.5, 5.2),
                                   gridspec_kw={"width_ratios": [2.6, 1]})
    grid = [[old_code[i], new_code[i]] for i in range(len(rows))]
    cmap = ListedColormap(["#4caf50", "#9e9e9e", "#ffb300", "#e53935"])
    ax0.imshow(grid, aspect="auto", cmap=cmap, vmin=0, vmax=3)
    ax0.set_xticks([0, 1], ["legacy gate\n(path exists only)", "content gate\n(declared checks)"])
    ax0.set_yticks(range(len(names)), names)
    labels = {0: "release", 1: "wait (artifact)", 2: "wait (WITHHELD)", 3: "wait (FAIL)"}
    for i, line in enumerate(grid):
        for j, code in enumerate(line):
            ax0.text(j, i, labels[code], ha="center", va="center", fontsize=8.5,
                     color="white" if code in (0, 3) else "black")
    ax0.set_title("Gate decision per synthetic scenario (A/B)")
    ax0.grid(False)

    counts = {"PASS": 0, "FAIL": 0, "WITHHELD": 0}
    for r in rows:
        counts[r["content_verdict"]] = counts.get(r["content_verdict"], 0) + 1
    bars = ax1.bar(list(counts), list(counts.values()),
                   color=["#4caf50", "#e53935", "#ffb300"])
    ax1.bar_label(bars)
    ax1.set_title("content verdict distribution")
    ax1.set_ylabel("scenarios")
    ax1.set_ylim(0, max(counts.values()) + 1.2)
    fig.suptitle("gate_content_criteria: existence-only gate vs content-bound gate "
                 f"(n={len(rows)} synthetic scenarios)", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "ab_matrix.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":  # pragma: no cover
    result = run()
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))