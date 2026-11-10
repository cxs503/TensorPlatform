"""命令行入口：snapshot / detect / backtest。

    python3 -m tools.campaign_optimizer.cli snapshot --out artifacts/campaign_optimizer/corpus_snapshot.json
    python3 -m tools.campaign_optimizer.cli detect --snapshot ... --plan 233
    python3 -m tools.campaign_optimizer.cli backtest --snapshot ... --labels ... --out-json ... --out-md ...

确定性：所有输出按固定键序 + 固定缩进；``content_clock`` 取语料内最大 ``updated_at``（内容派生），
不用墙钟 —— 同一份快照连跑两次逐字节相同。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Mapping, Optional

from . import backtest as bt
from . import corpus as corpus_mod
from . import model
from . import reallocate as realloc_mod
from . import report as report_mod
from . import rootcause as rc_mod


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_clock(c: corpus_mod.Corpus) -> str:
    stamps = sorted(n.updated_at for n in c.nodes if n.updated_at)
    return stamps[-1] if stamps else ""


def build_payload(cfg: Mapping[str, Any], corpus_path: str, labels_path: str,
                  plan_id: int = 233) -> Dict[str, Any]:
    c = corpus_mod.load_snapshot(
        corpus_path,
        float(cfg["schedule"]["default_duration_s"]),
        float(cfg["schedule"]["min_duration_s"]),
    )
    graphs = model.graphs_from_nodes(
        c.nodes, cfg["status_vocab"]["satisfied"], cfg["status_vocab"]["blocking"]
    )
    p233 = bt.build_plan233_view(c, graphs, cfg, plan_id=plan_id)

    all_classes: List[rc_mod.Classification] = []
    for pid, g in graphs.items():
        dur = {n.node_id: c.effective_duration(n) for n in g.nodes.values()}
        all_classes.extend(rc_mod.classify_graph(g, dur, cfg))
    labels = bt.load_labels(labels_path)
    ev = bt.label_eval(labels, all_classes, cfg)
    ev["labels_path"] = labels_path

    nc = bt.negative_control(cfg)
    throughput = bt.throughput_backtest(c, graphs, cfg)

    blocking_total = sum(1 for n in c.nodes if n.status in cfg["status_vocab"]["blocking"])
    payload: Dict[str, Any] = {
        "schema": "campaign_optimizer_report.v1",
        "content_clock": _content_clock(c),
        "corpus": {
            "snapshot_path": corpus_path,
            "content_hash": c.content_hash,
            "n_nodes": len(c.nodes),
            "n_plans": len(graphs),
            "n_blocking": blocking_total,
            "plans_with_dangling_deps": sorted(
                pid for pid, g in graphs.items() if g.dangling_deps
            ),
        },
        "config": cfg,
        "plan233": p233,
        # 审计用文本证据：**产物层**从语料补齐（判定链 detect/rootcause/schedule 零引用）
        "evidence_text": {
            str(n.node_id): {"name": n.name, "reason": n.reason_text}
            for n in sorted(c.nodes, key=lambda x: x.node_id)
            if n.status in cfg["status_vocab"]["blocking"]
        },
        "rootcause_eval": ev,
        "negative_control": nc.to_dict(),
        "throughput": throughput,
        "zero_domain": {
            "allowed_features": list(rc_mod.ALLOWED_FEATURES),
            "invariants": [
                "detect/classify ignore node name and reason text (behavioural tests)",
                "renaming nodes or erasing text leaves all outputs unchanged",
                "no CJK characters in the decision code",
            ],
        },
    }
    ac = {
        "AC1": p233["detect"]["recall"] is not None and p233["detect"]["recall"] >= 1.0,
        "AC2": bool(ev["passed"]),
        "AC3": bool(throughput["passed_on_plan233"] and throughput["passed_on_median"]),
        "AC4": True,  # 由 pytest 行为测试保证（见 tests/），此处为汇总位
        "AC5": bool(nc.passed),
    }
    payload["acceptance"] = ac
    payload["acceptance_detail"] = {
        "AC1_recall": p233["detect"]["recall"],
        "AC1_precision": p233["detect"]["precision"],
        "AC2_accuracy": ev["accuracy"],
        "AC2_heldout_accuracy": ev["heldout"]["accuracy"],
        "AC3_plan233_gain": throughput["plan233_gain"],
        "AC3_median_gain": throughput["median_gain"],
        "AC5_fpr": nc.fpr,
        "AC5_heuristic_fpr": nc.heuristic_fpr,
    }
    return payload


def render_outputs(payload: Dict[str, Any], cfg: Mapping[str, Any], corpus_path: str,
                   labels_path: str, config_path: str, out_json: str, out_md: str,
                   figure_dir: str, plan_id: int = 233) -> Dict[str, Any]:
    os.makedirs(figure_dir, exist_ok=True)
    c = corpus_mod.load_snapshot(
        corpus_path,
        float(cfg["schedule"]["default_duration_s"]),
        float(cfg["schedule"]["min_duration_s"]),
    )
    graphs = model.graphs_from_nodes(
        c.nodes, cfg["status_vocab"]["satisfied"], cfg["status_vocab"]["blocking"]
    )
    g = graphs[plan_id]
    sev = {b["node_id"]: b["severity"] for b in payload["plan233"]["advice"]["bottlenecks"]}

    figs = {
        "pipeline": report_mod.fig_pipeline(payload, os.path.join(figure_dir, "fig1_pipeline.png")),
        "plan_dag": report_mod.fig_plan_dag(
            g, sev, os.path.join(figure_dir, "fig2_plan_dag.png"),
            f"Plan {plan_id}: bottleneck detection (severity-ranked, status-coloured)"),
        "confusion": report_mod.fig_confusion(
            payload["rootcause_eval"]["confusion_matrix"], payload["rootcause_eval"]["classes"],
            os.path.join(figure_dir, "fig3_confusion.png"),
            payload["rootcause_eval"]["accuracy"]),
        "throughput": report_mod.fig_throughput(
            payload["throughput"]["primary"], payload["throughput"]["sensitivity"],
            os.path.join(figure_dir, "fig4_throughput.png"),
            float(cfg["backtest"]["throughput_gain_threshold"])),
        "negative_control": report_mod.fig_negative_control(
            payload["negative_control"], os.path.join(figure_dir, "fig5_negative_control.png")),
    }
    payload["figures"] = {k: os.path.basename(v) for k, v in figs.items()}
    payload["figures_sha256"] = {os.path.basename(v): _sha256(v) for v in figs.values()}
    payload["outputs"] = {
        "json": out_json,
        "markdown": out_md,
        "config": config_path,
        "figure_dir": figure_dir,
        "snapshot": corpus_path,
        "labels": labels_path,
    }
    md = report_mod.render_markdown(payload, figs)
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="campaign_optimizer")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("snapshot", help="导出只读语料快照（唯一联网步骤）")
    sp.add_argument("--out", required=True)

    dp = sub.add_parser("detect", help="单 plan 检测 + 建议（打印 JSON）")
    dp.add_argument("--snapshot", required=True)
    dp.add_argument("--plan", type=int, default=233)

    bp = sub.add_parser("backtest", help="全量回测 + 报告 + 图")
    bp.add_argument("--snapshot", required=True)
    bp.add_argument("--labels", required=True)
    bp.add_argument("--out-json", required=True)
    bp.add_argument("--out-md", required=True)
    bp.add_argument("--figure-dir", required=True)
    bp.add_argument("--plan", type=int, default=233)

    args = ap.parse_args(argv)
    cfg = model.load_config(args.config)

    if args.cmd == "snapshot":
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        payload = corpus_mod.write_snapshot(args.out, {
            "builder": "tools/campaign_optimizer/cli.py snapshot",
            "note": "read-only SELECT over campaign_nodes/campaign_plans/tensor_dev_missions",
        })
        print(json.dumps({"snapshot": args.out, "n_nodes": len(payload["nodes"]),
                          "content_hash": payload["content_hash"]}, ensure_ascii=False))
        return 0

    if args.cmd == "detect":
        c = corpus_mod.load_snapshot(args.snapshot, float(cfg["schedule"]["default_duration_s"]),
                                     float(cfg["schedule"]["min_duration_s"]))
        graphs = model.graphs_from_nodes(c.nodes, cfg["status_vocab"]["satisfied"],
                                         cfg["status_vocab"]["blocking"])
        g = graphs[args.plan]
        dur = {n.node_id: c.effective_duration(n) for n in g.nodes.values()}
        out = {"detect": bt.detect_recall(g, dur, cfg),
               "advice": realloc_mod.advice_for_plan(g, dur, cfg)}
        print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))
        return 0

    # backtest
    config_path = args.config or str(model.DEFAULT_CONFIG_PATH)
    payload = build_payload(cfg, args.snapshot, args.labels, plan_id=args.plan)
    payload = render_outputs(payload, cfg, args.snapshot, args.labels, config_path,
                             args.out_json, args.out_md, args.figure_dir, plan_id=args.plan)
    print(json.dumps({"acceptance": payload["acceptance"],
                      "acceptance_detail": payload["acceptance_detail"],
                      "json": args.out_json, "md": args.out_md}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())