"""派发前可行性先验校准器（pre-dispatch feasibility prior）。

两个入口
--------
* ``python3 -m tools.pre_dispatch_prior run``     —— 从历史战役节点背测校准器，落产物+图+报告。
* ``python3 -m tools.pre_dispatch_prior predict`` —— 给「待派发节点规格」算校准后验收概率
  （派发前路径：只吃历史 + 计划侧信息，不吃任何结局信息）。

零领域语义：所有特征都是平台结构量（判据条数/依赖拓扑/仓成熟度/文本新颖度），
不识别任何物理域词条；域判据只当 JSON 结构数数。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from typing import Any, Sequence

import numpy as np

from . import dataset as ds
from . import features as ft
from . import report as rp
from .backtest import (DIAGNOSTIC_VARIANTS, Pipeline, VARIANTS,
                       ablation_calibration_source, evaluate, evaluate_indices,
                       pending_priors, shuffle_control, time_split)


# ───────────────────────────── 公共 ─────────────────────────────

def _load_nodes(source: str, fixture: str | None, db: str = "") -> list[ds.RawNode]:
    if source == "fixture":
        if not fixture:
            raise SystemExit("--source fixture 需要 --fixture PATH")
        return ds.load_fixture(fixture)
    if source == "pg":
        return ds.load_pg(db)
    if source == "auto":
        if fixture and os.path.exists(fixture):
            return ds.load_fixture(fixture)
        return ds.load_pg(db)
    raise SystemExit(f"unknown source: {source}")


def _dataset_digest(nodes: Sequence[ds.RawNode]) -> str:
    h = hashlib.sha256()
    for n in sorted(nodes, key=lambda x: x.sort_key):
        payload = json.dumps({
            "node_id": n.node_id, "plan_id": n.plan_id, "created_at": n.created_at,
            "plan_created_at": n.plan_created_at, "status": n.status,
            "node_type": n.node_type, "dependencies": list(n.dependencies),
            "metadata": {k: v for k, v in n.metadata.items() if k != "_block_reason"},
        }, ensure_ascii=False, sort_keys=True)
        h.update(payload.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _leakage_prefix_check(nodes: Sequence[ds.RawNode], cut: float = 0.7) -> dict:
    """因果性自检：前 k 个节点的特征必须与「全集算完后取前 k」逐值相同。"""
    ordered = sorted(nodes, key=lambda n: n.sort_key)
    k = max(1, int(len(ordered) * cut))
    full = ft.extract_features(ordered)
    prefix = ft.extract_features(ordered[:k])
    max_dev = 0.0
    for i in range(k):
        for a, b in zip(full.rows[i], prefix.rows[i]):
            max_dev = max(max_dev, abs(float(a) - float(b)))
    return {"cut": cut, "k": k, "max_abs_dev": max_dev, "pass": bool(max_dev == 0.0)}


def _label_for(nodes: Sequence[ds.RawNode], scheme: str) -> ds.FeatureSet:
    """按口径重算标签（features 内建的是 primary；strict 口径在此覆盖）。"""
    fs = ft.extract_features(nodes)
    if scheme == "primary":
        return fs
    if scheme != "strict":
        raise SystemExit(f"unknown label scheme: {scheme}")
    fn = ds.LABEL_FUNCS[scheme]
    ordered = sorted(nodes, key=lambda n: n.sort_key)
    labels = [fn(n) for n in ordered]
    return ds_dataclass_replace(fs, labels)


def ds_dataclass_replace(fs: ft.FeatureSet, labels: list[int | None]) -> ft.FeatureSet:
    import dataclasses
    return dataclasses.replace(fs, labels=labels)


# ───────────────────────────── run ─────────────────────────────

def cmd_run(args: argparse.Namespace) -> int:
    nodes = _load_nodes(args.source, args.fixture, args.db)
    if not nodes:
        raise SystemExit("no campaign nodes loaded")
    census = ds.census(nodes)
    leakage = _leakage_prefix_check(nodes)
    fs = _label_for(nodes, args.label)

    if not fs.labeled_indices():
        raise SystemExit("no labeled samples")
    split = time_split(fs, train_frac=args.train_frac, calib_frac=args.calib_frac)
    pipe = Pipeline.fit(fs, split, l2=args.l2)
    bt = {"split": split.to_dict(),
          "test": evaluate(pipe, fs, split, n_bins=args.bins),
          "monitor": evaluate_indices(pipe, fs, split.calib, n_bins=args.bins),
          "oof": pipe.oof,
          "n_train": len(split.train), "n_calib": len(split.calib)}
    shuffle = shuffle_control(fs, bt["test"], n_perm=args.perm, seed=args.seed,
                              jobs=args.jobs)
    ablation = ablation_calibration_source(fs, split, l2=args.l2)
    bt["pending_priors"] = pending_priors(pipe, nodes)
    checks = rp.criteria_verdict(bt, shuffle)

    # 敏感性：strict 标签口径（运维事故剔除）下的同一套流程
    sensitivity: dict[str, Any] = {}
    if args.sensitivity:
        try:
            fs_s = _label_for(nodes, "strict")
            split_s = time_split(fs_s, train_frac=args.train_frac, calib_frac=args.calib_frac)
            pipe_s = Pipeline.fit(fs_s, split_s, l2=args.l2)
            ev_s = evaluate(pipe_s, fs_s, split_s, n_bins=args.bins)
            sensitivity["strict"] = {
                "sizes": split_s.to_dict()["sizes"],
                "class_counts": split_s.detail["class_counts"],
                "test": {k: {kk: vv for kk, vv in ev_s["variants"][k].items()
                             if kk.startswith(("brier", "auc", "log_loss"))}
                         for k in VARIANTS},
                "note": ("strict 口径剔除 infra 运维事故（agent 丢失/LLM 报错），只剩「任务本身没过验收」，"
                         "负例极少、方差大——只作敏感性对照，不作主判据"),
            }
        except ValueError as exc:      # 样本不足两类时如实记录，不粉饰
            sensitivity["strict"] = {"error": str(exc)}

    payload: dict[str, Any] = {
        "schema": "pre_dispatch_prior/report/v1",
        "campaign": {
            "label_scheme": args.label,
            "census": census,
            "leakage_prefix_check": leakage,
            "dataset_digest": _dataset_digest(nodes),
            "source": args.source,
            "hyperparameters": {"l2": args.l2, "train_frac": args.train_frac,
                                "calib_frac": args.calib_frac, "bins": args.bins,
                                "perm": args.perm, "seed": args.seed},
        },
        "backtest": bt,
        "shuffle": shuffle,
        "criteria": checks,
        "model": {
            "feature_names": list(ft.FEATURE_NAMES),
            "feature_families": {k: list(v) for k, v in ft.FEATURE_FAMILIES.items()},
            "coefficients": pipe.coefficients(),
            "prior_train": pipe.prior,
            "platt": pipe.platt.to_dict(),
            "isotonic_knots": len(pipe.isotonic.x),
            "iterations": {"base": pipe.base.iterations_run,
                           "platt": pipe.platt.iterations_run},
        },
    }
    if sensitivity:
        payload["sensitivity"] = sensitivity

    # 产物
    out_dir = pathlib.Path(args.out_dir)
    fig_dir = pathlib.Path(args.fig_dir) if args.fig_dir else out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    model_payload = {
        "schema": "pre_dispatch_prior/model/v1",
        "pipeline": pipe.to_dict(),
        "label_scheme": args.label,
        "dataset_digest": payload["campaign"]["dataset_digest"],
        "trained_on": {"n_train": len(split.train), "n_calib": len(split.calib),
                       "train_window": split.detail["window"]["train"]},
        "policy_hint": {
            "note": ("阈值策略不是统计结论：p<0.35 视为「派发前需缩小范围/降判据」建议区，"
                     "p>0.75 为常规放行区；仅作排程参考，验收门仍以判据为准"),
        },
    }
    (out_dir / "model.json").write_text(rp.dumps(model_payload), encoding="utf-8")
    (out_dir / "backtest.json").write_text(rp.dumps(payload), encoding="utf-8")

    # 特征矩阵（不含任何节点文本，只有数值列 + 溯源列）
    feat_rows = []
    for i in range(len(fs)):
        feat_rows.append({
            "node_id": fs.node_ids[i], "plan_id": fs.plan_ids[i],
            "created_at": fs.created_at[i], "plan_created_at": fs.plan_created_at[i],
            "status": fs.statuses[i],
            "label": fs.labels[i],
            "features": {n: round(fs.rows[i][k], 6)
                         for k, n in enumerate(fs.feature_names)},
        })
    feat_blob = rp.dumps({"schema": "pre_dispatch_prior/features/v1",
                          "feature_names": list(fs.feature_names), "rows": feat_rows})
    # 特征矩阵数 MB 级：gzip 落盘（mtime=0 → 同输入逐字节相同，幂等不被压缩时间戳破坏）
    import gzip
    with open(out_dir / "features.json.gz", "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=6) as gz:
            gz.write(feat_blob.encode("utf-8"))

    # 图
    rp.figure_reliability(bt["test"], str(fig_dir / "fig_reliability.png"))
    rp.figure_roc(fs, pipe, split, bt["test"], str(fig_dir / "fig_roc.png"))
    rp.figure_null(shuffle, str(fig_dir / "fig_null_control.png"))
    rp.figure_variants(bt["test"], str(fig_dir / "fig_variants.png"))
    payload["model"]["coefficients"] = rp.figure_coefficients(
        pipe, str(fig_dir / "fig_coefficients.png"))
    (out_dir / "backtest.json").write_text(rp.dumps(payload), encoding="utf-8")
    (out_dir / "REPORT.md").write_text(rp.markdown_report(payload), encoding="utf-8")
    (out_dir / "ablation_window_platt.json").write_text(rp.dumps(ablation), encoding="utf-8")
    paper = rp.markdown_paper(payload, ablation)
    (out_dir / "PAPER.md").write_text(paper, encoding="utf-8")
    if args.docs_dir:
        docs = pathlib.Path(args.docs_dir)
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "REPORT.md").write_text(paper, encoding="utf-8")

    # 摘要
    print(f"[pre_dispatch_prior] nodes={census['nodes']} plans={census['plans']} "
          f"labeled={len(fs.labeled_indices())} "
          f"(pos={census['label_' + args.label]['pos']})")
    print(f"[pre_dispatch_prior] split {split.to_dict()['sizes']}")
    for name in VARIANTS:
        v = bt["test"]["variants"][name]
        print(f"  {name:12s} brier={v['brier']:.4f} bss={v['brier_skill_vs_train_prior']:+.4f} "
              f"auc={v['auc']:.4f} ece={v['ece_uniform10']['ece']:.4f}")
    for name in DIAGNOSTIC_VARIANTS:
        v = bt["test"]["diagnostic_variants"].get(name)
        if v:
            print(f"  [diag] {name:14s} brier={v['brier']:.4f} auc={v['auc']:.4f}")
    print(f"[pre_dispatch_prior] monitor window n={bt['monitor']['n']} "
          f"raw_auc={bt['monitor']['variants']['raw']['auc']:.4f}")
    print(f"[pre_dispatch_prior] shuffle: null_auc={shuffle['null_auc']['mean']:.4f} "
          f"p_bss={shuffle['p_value_bss']:.4f}")
    print(f"[pre_dispatch_prior] criteria {checks['ALL']['n_pass']}/{checks['ALL']['n_total']} "
          f"-> {'PASS' if checks['ALL']['pass'] else 'FAIL'}")
    for k, v in checks.items():
        if k != "ALL" and not v["pass"]:
            print(f"  FAIL {k}: value={v['value']} op={v['op']} thr={v['threshold']}")
    return 0 if checks["ALL"]["pass"] else 1


# ───────────────────────────── predict ─────────────────────────────

def cmd_predict(args: argparse.Namespace) -> int:
    """派发前路径：历史 → HistoryState → 待派发节点规格 → 校准后验收概率（不读任何结局）。"""
    model = json.loads(pathlib.Path(args.model).read_text(encoding="utf-8"))
    pipe = Pipeline.from_dict(model["pipeline"])
    history = _load_nodes(args.source, args.fixture, args.db)
    state = ft.HistoryState()
    for n in sorted(history, key=lambda x: x.sort_key):
        state.observe(n)

    specs = json.loads(pathlib.Path(args.nodes).read_text(encoding="utf-8"))
    rows = specs["nodes"] if isinstance(specs, dict) else specs
    out: list[dict[str, Any]] = []
    for k, spec in enumerate(rows):
        node = ft.spec_to_node(spec, node_id=spec.get("node_id", 10 ** 9 + k),
                               plan_id=spec.get("plan_id", 10 ** 9))
        x = np.asarray(state.features_for(node), dtype=float)
        preds = pipe.predict(x.reshape(1, -1))
        out.append({
            "node_id": node.node_id,
            "name": str(spec.get("name") or spec.get("title") or "")[:120],
            "features": {n: float(v) for n, v in zip(ft.FEATURE_NAMES, x)},
            "p_accept": {k2: float(v[0]) for k2, v in preds.items()},
            "band": _band(float(preds["platt"][0]), args.low, args.high),
        })
    payload = {"schema": "pre_dispatch_prior/predict/v1",
               "model_dataset_digest": model.get("dataset_digest"),
               "history_nodes": len(history), "predictions": out}
    text = rp.dumps(payload)
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_ablate(args: argparse.Namespace) -> int:
    """对照实验：校准数据来源「中段单窗」vs「训练窗 OOF」——同一测试窗比（可复现证据）。"""
    nodes = _load_nodes(args.source, args.fixture, args.db)
    fs = _label_for(nodes, args.label)
    split = time_split(fs, train_frac=args.train_frac, calib_frac=args.calib_frac)
    payload = ablation_calibration_source(fs, split, l2=args.l2)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rp.dumps(payload), encoding="utf-8")
    print(rp.dumps(payload))
    return 0


def _band(p: float, low: float, high: float) -> str:
    if p < low:
        return "rescale"      # 建议缩小范围/降判据后派发
    if p > high:
        return "normal"       # 常规放行
    return "watch"            # 正常派发但派发后优先复核


# ───────────────────────────── main ─────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pre_dispatch_prior", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="时间留出背测 + shuffle 负控 + 产物")
    r.add_argument("--source", default="auto", choices=["auto", "pg", "fixture"])
    r.add_argument("--fixture", default="", help="离线 fixture JSON 路径")
    r.add_argument("--db", default="", help="PG 库名（默认 TP_PG_DB 或 tensorplatform）")
    r.add_argument("--label", default="primary", choices=["primary", "strict"])
    r.add_argument("--out-dir", default="artifacts/pre_dispatch_prior")
    r.add_argument("--fig-dir", default="")
    r.add_argument("--train-frac", type=float, default=0.6)
    r.add_argument("--calib-frac", type=float, default=0.2)
    r.add_argument("--l2", type=float, default=1.0)
    r.add_argument("--bins", type=int, default=10)
    r.add_argument("--perm", type=int, default=50)
    r.add_argument("--jobs", type=int, default=1,
                   help="置换并行进程数（默认串行；单线程 BLAS 下 100 次置换 ~秒级）")
    r.add_argument("--seed", type=int, default=20261110)
    r.add_argument("--sensitivity", action="store_true", help="附带 strict 标签口径敏感性")
    r.add_argument("--docs-dir", default="", help="论文级报告落地目录（典型 docs/pre_dispatch_prior）")
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("predict", help="派发前：待派发节点 → 校准后验收概率")
    p.add_argument("--model", default="artifacts/pre_dispatch_prior/model.json")
    p.add_argument("--nodes", required=True, help="待派发节点规格 JSON")
    p.add_argument("--source", default="auto", choices=["auto", "pg", "fixture"])
    p.add_argument("--fixture", default="")
    p.add_argument("--db", default="")
    p.add_argument("--low", type=float, default=0.35)
    p.add_argument("--high", type=float, default=0.75)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_predict)

    a = sub.add_parser("ablate", help="对照实验：单窗校准 vs OOF 校准")
    a.add_argument("--source", default="auto", choices=["auto", "pg", "fixture"])
    a.add_argument("--fixture", default="")
    a.add_argument("--db", default="")
    a.add_argument("--label", default="primary", choices=["primary", "strict"])
    a.add_argument("--train-frac", type=float, default=0.6)
    a.add_argument("--calib-frac", type=float, default=0.2)
    a.add_argument("--l2", type=float, default=1.0)
    a.add_argument("--out", default="artifacts/pre_dispatch_prior/ablation_window_platt.json")
    a.set_defaults(func=cmd_ablate)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())