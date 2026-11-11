#!/usr/bin/env python3
"""真实预测数据采集：把「平台历史里的概率预测 + 真实结局」采成计分账本的输入。

数据从哪来、为什么是它
======================
计分需要成对的 ``(承诺概率, 事后二元结局)``。凭空造一对数就是造假，所以本模块
只接一个**确实存在、且已有时间留出背测**的来源：平台的派发前可行性先验
（``tools/pre_dispatch_prior``）。它把战役节点（``campaign_nodes`` 行）的结构化
元数据映射成「节点能否按时通过验收」的概率，并且：

* 特征严格因果（只用**更早**的节点算特征，``test_features_are_causal`` 钉死）；
* 切分按**计划创建时刻**分窗（训练 → 校准 → 测试），三窗计划不相交，
  故测试窗里每个概率都由「没见过该窗口标签」的模型产生（无标签泄漏）；
* 结局取数据库里的真实状态（``done``=1 / ``blocked|failed``=0；右删失剔除）。

于是本模块产出的是**真实的样本外概率预测**，不是模拟器反事实、也不是事后补写。
它**不是**「预测盲卡」：没有承诺-揭示时序。盲卡路的接入见
``ledger.forecasts_from_blind_cards``（要求封存前登记置信度）。

诚实边界
========
* 只有**一个**模型变体被当作「工具交付的预测」送进账本（默认 ``platt_prior``）；
  其余变体留在 meta 里作敏感性对照，不进账本，避免事后挑最好的那个。
* 结局口径 ``primary`` 把运维事故（agent 丢失/LLM 报错）也算失败；``strict``
  口径的负例太少（个位数），只作对照。
* 采集是**回溯**的：账本链锚定的是「本次采集时的预测内容」，不是当年的承诺。
  这一点在报告里必须写明，不许拿 ``ledger_chain_ok`` 冒充「预测当年就盲」。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from .ledger import ForecastRecord

DEFAULT_VARIANT = "platt_prior"
SUPPORTED_LABELS = ("primary",)


def _dataset_digest(nodes: Sequence[Any]) -> str:
    h = hashlib.sha256()
    for n in sorted(nodes, key=lambda x: x.sort_key):
        payload = json.dumps({
            "node_id": n.node_id, "plan_id": n.plan_id, "created_at": n.created_at,
            "status": n.status, "node_type": n.node_type,
            "metadata": {k: v for k, v in n.metadata.items() if k != "_block_reason"},
        }, ensure_ascii=False, sort_keys=True)
        h.update(payload.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def harvest(*, source: str = "pg", fixture: str | None = None, db: str = "",
            label: str = "primary", variant: str = DEFAULT_VARIANT,
            train_frac: float = 0.6, calib_frac: float = 0.2,
            l2: float = 1.0) -> dict[str, Any]:
    """采一批真实样本外概率预测。返回 ``{"forecasts": [...], "meta": {...}}``。"""
    from tools.pre_dispatch_prior import dataset as ds
    from tools.pre_dispatch_prior import features as ft
    from tools.pre_dispatch_prior import backtest as bt

    if label not in SUPPORTED_LABELS:
        raise ValueError(f"仅支持 {SUPPORTED_LABELS}: {label}")

    if source == "fixture":
        if not fixture:
            raise ValueError("--source fixture 需要 --fixture")
        nodes = ds.load_fixture(fixture)
    elif source == "pg":
        nodes = ds.load_pg(db)
    else:
        raise ValueError(f"未知 source: {source}")
    if not nodes:
        raise RuntimeError("没有采到任何节点（数据源为空）")

    fs = ft.extract_features(nodes)
    if not fs.labeled_indices():
        raise RuntimeError("没有带结局（标签）的样本")
    split = bt.time_split(fs, train_frac=train_frac, calib_frac=calib_frac)
    pipe = bt.Pipeline.fit(fs, split, l2=l2)
    preds = pipe.predict(fs.matrix(split.test))
    if variant not in preds:
        raise ValueError(f"变体不存在: {variant}; 可选 {sorted(preds)}")

    probs = preds[variant]
    records: list[ForecastRecord] = []
    per_variant_brier: dict[str, float] = {}
    ys = [int(fs.labels[i]) for i in split.test]
    for name, arr in sorted(preds.items()):
        per_variant_brier[name] = sum((float(a) - y) ** 2
                                      for a, y in zip(arr, ys)) / max(1, len(ys))
    for i, p in zip(split.test, probs):
        records.append(ForecastRecord(
            case_id=f"node:{fs.node_ids[i]}",
            prob=float(p),
            outcome=int(fs.labels[i]),
            source=f"pre_dispatch_prior/{variant}",
            group="test_window",
            evidence_refs=(f"plan:{fs.plan_ids[i]}", f"created_at:{fs.created_at[i]}"),
        ))

    census = ds.census(nodes)
    meta = {
        "schema": "blind_calibration/harvest/v1",
        "source": source,
        "label_scheme": label,
        "variant": variant,
        "hyperparameters": {"train_frac": train_frac, "calib_frac": calib_frac,
                            "l2": l2, "n_bins_default": 10},
        "n_nodes_total": len(nodes),
        "split": split.to_dict(),
        "n_forecasts": len(records),
        "positive_rate_test": (sum(ys) / len(ys)) if ys else None,
        "per_variant_brier_test": dict(sorted(per_variant_brier.items())),
        "dataset_digest": _dataset_digest(nodes),
        "census": census,
        "windows": {
            "test_created_at": [fs.created_at[i] for i in split.test][:1] and
                               [min(fs.created_at[i] for i in split.test),
                                max(fs.created_at[i] for i in split.test)],
        },
        "honesty": {
            "leakage": "特征严格因果 + 按计划创建时刻切窗（沿用 pre_dispatch_prior 的时序纪律）",
            "blindness": "非承诺-揭示盲卡：这是回溯采集的样本外预测，链只锚定采集时内容",
            "variant_choice": f"只用 {variant} 入账；其余变体仅在 meta 里作对照，不事后挑选",
        },
    }
    return {"forecasts": records, "meta": meta}