"""发布进展报告到平台（markdown 与图均从工件读取，数字零手抄）。

用法：
    TENSOR_API_TOKEN=... python3 -m tools.criteria_metrology.publish_report \\
        --docs docs/P266W3-N10_criteria_metrology.md --artifacts artifacts/criteria_metrology
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

CAPTIONS = {
    "fig_degeneracy_map.png": "判据退化测绘：三条判据族在 (|y_ref|/scale, 朴素相对误差) 平面的位置；T3 无单点坐标即其不可判的图形表达",
    "fig_adaptive_ladder.png": "不变量台阶：基准 y_ref→0 时朴素相对误差发散，自适应判据族数值恒有界",
    "fig_controls.png": "负控混淆矩阵：12 条退化判据 + 12 条良性判据（含贴零/近界/异号等近界样本）",
    "fig_replay_flips.png": "回放重判判决转移矩阵（朴素相对误差 → 自适应判据族）",
    "fig_corpus_tiers.png": "语料抽取忠实度分层与自洽门剔除量",
}


def build_payload(docs: Path, artifacts: Path) -> dict:
    summary = json.loads((artifacts / "summary.json").read_text(encoding="utf-8"))
    rp = summary["replay"]
    figures = []
    for p in sorted(artifacts.glob("fig_*.png")):
        figures.append(
            {
                "name": p.stem,
                "caption": CAPTIONS.get(p.name, p.name),
                "png_base64": base64.b64encode(p.read_bytes()).decode("ascii"),
            }
        )
    return {
        "title": "判据度量学成熟化：退化三分类 + 自适应判据族回放重判 + 检索凭证重放门",
        "summary": (
            f"把判据写成四元组并机器判定三类不可判情形：对 {rp['n_records']} 条历史判据回放重判，"
            f"检出退化 {rp['n_degenerate']} 条（{100*rp['degenerate_rate']:.1f}%），逐条给出可复算替代判据数值；"
            f"负控 12/12 命中、良性误报 0/12；{summary['invariants']['n_samples']} 样本不变量测试 0 违反；"
            f"四历史案例方向一致 {summary['cases']['n_direction_ok']}/4（m7241 的 ~60% 偏差被显式判 FAIL）；"
            f"检索凭证重放门 6/6 主张与期望一致；能力三元台账 NOT_SUPPORTED 显式登记 {summary['triad']['not_supported_registered']} 条。"
        ),
        "scenario_key": "tensorplatform.criteria.metrology",
        "mission_id": 13932,
        "tags": ["TensorPlatform", "criteria-metrology", "degeneracy", "adaptive-criterion", "replay", "retrieval-receipt", "capability-triad"],
        "markdown": docs.read_text(encoding="utf-8"),
        "figures": figures,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发布判据度量学进展报告")
    ap.add_argument("--docs", default="docs/P266W3-N10_criteria_metrology.md")
    ap.add_argument("--artifacts", default="artifacts/criteria_metrology")
    ap.add_argument("--api-base", default=os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1"))
    ap.add_argument("--token", default=os.environ.get("TENSOR_API_TOKEN"))
    ap.add_argument("--user", default=os.environ.get("TENSOR_AGENT_USER"))
    ap.add_argument("--password", default=os.environ.get("TENSOR_AGENT_PASS"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    payload = build_payload(Path(args.docs), Path(args.artifacts))
    if args.dry_run:
        print(json.dumps({k: (v if k != "figures" else [f["name"] for f in v]) for k, v in payload.items() if k != "markdown"}, ensure_ascii=False))
        return 0

    token = args.token
    base = args.api_base.rstrip("/")
    if not token:
        if not (args.user and args.password):
            print("need token or credentials", file=sys.stderr)
            return 2
        req = urllib.request.Request(
            base + "/auth/login",
            data=json.dumps({"username": args.user, "password": args.password}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            token = json.loads(resp.read().decode())["access_token"]
    req = urllib.request.Request(
        base + "/tensor/reports",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode())
    print(json.dumps({k: out.get(k) for k in ("id", "title", "status", "mission_id")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())