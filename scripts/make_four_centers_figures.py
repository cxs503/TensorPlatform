#!/usr/bin/env python3
"""四中心最小视图验收图（数据全部由后端载荷/对拍记录程序化注入，零手抄）。

图 1：四中心生产库实测面（战役节点状态 / 报告 QA 评价流 / 能力增量类别 / 关键比率）
图 2：44 项「端点值 × 独立 SQL 值」一致性散点（恒等线 = 零假数据）

坐标标签用英文：容器内 matplotlib 无 CJK 字体，避免豆腐块；图注在报告里用中文。
用法：
    python scripts/make_four_centers_figures.py --database-url postgresql+psycopg://... 
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
OUT = pathlib.Path(__file__).resolve().parents[1] / "artifacts" / "m5023"


def fetch_payloads(url: str) -> tuple[dict, int]:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from api.deps import require_auth
    from api.tensor_centers import router as centers_router
    from database.config import get_db

    engine = create_engine(url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, future=True)
    with engine.connect() as conn:
        org = int(conn.execute(text("SELECT MIN(id) FROM organizations")).scalar())
    app = FastAPI()
    app.include_router(centers_router)
    app.dependency_overrides[require_auth] = lambda: {
        "id": 1, "username": "figures", "role": "ADMIN", "organization_id": org}
    app.dependency_overrides[get_db] = lambda: Session()
    client = TestClient(app)
    payloads = {c: client.get(f"/tensor/centers/{c}").json()
                for c in ("organization", "quality", "capability", "innovation")}
    return payloads, org


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", required=True)
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    payloads, org = fetch_payloads(args.database_url)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "center_payloads_snapshot.json").write_text(
        json.dumps({"org_id": org, "payloads": payloads}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    metric = {c: {m["key"]: m for m in p["metrics"]} for c, p in payloads.items()}
    org_p = payloads["organization"]
    qual_p = payloads["quality"]
    cap_p = payloads["capability"]

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    ax = axes[0][0]
    nodes = org_p["lists"]["campaign_nodes_by_status"]
    keys = sorted(nodes)
    ax.bar(keys, [nodes[k] for k in keys], color="#2b5d8c")
    ax.set_title(f"Campaign nodes by status (n={sum(nodes.values())})")
    ax.set_ylabel("nodes")
    for i, k in enumerate(keys):
        ax.text(i, nodes[k], str(nodes[k]), ha="center", va="bottom", fontsize=9)

    ax = axes[0][1]
    qa = qual_p["lists"]["qa_by_status"]
    qa_labels = ["pending", "passed", "rejected", "unclassified"]
    qa_vals = [qa.get("pending", 0), qa.get("passed", 0),
               qa.get("rejected", 0), qa.get("__null__", 0)]
    ax.bar(qa_labels, qa_vals, color="#8c4a2b")
    ax.set_title("Evaluation flow: report qa_status")
    ax.set_ylabel("reports")
    ax.tick_params(axis="x", labelrotation=15)
    for i, v in enumerate(qa_vals):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)

    ax = axes[1][0]
    cats = cap_p["lists"]["by_category"]
    ax.barh(list(cats), list(cats.values()), color="#2b8c6a")
    ax.set_title("Capability ledger by category")
    ax.set_xlabel("deltas")
    for i, (k, v) in enumerate(cats.items()):
        ax.text(v, i, f" {v}", va="center", fontsize=9)

    ax = axes[1][1]
    ratio_keys = [
        ("capability:coverage_ratio", metric["capability"]["coverage_ratio"]["value"]),
        ("innovation:absorption_rate", metric["innovation"]["absorption_rate"]["value"]),
        ("quality:qa_pass_rate", metric["quality"]["reports_qa_pass_rate"]["value"]),
    ]
    labels = [k for k, _ in ratio_keys]
    vals = [(v or 0.0) * 100 for _, v in ratio_keys]
    ax.bar(labels, vals, color="#6a4a8c")
    ax.set_title("Key ratios (endpoint values, live DB)")
    ax.set_ylabel("%")
    ax.set_ylim(0, 100)
    ax.tick_params(axis="x", labelrotation=12)
    for i, (lab, (_, raw)) in enumerate(zip(labels, ratio_keys)):
        ax.text(i, vals[i], "n/a" if raw is None else f"{vals[i]:.2f}%",
                ha="center", va="bottom", fontsize=9)

    fig.suptitle("Four-centre minimal views — live platform SQL (org %d)" % org)
    fig.tight_layout()
    fig.savefig(OUT / "fig1_four_centers_live.png", dpi=78)
    plt.close(fig)

    # ---- 图 2：对拍恒等线 ----
    cross = json.loads((OUT / "center_sql_crosscheck.json").read_text(encoding="utf-8"))
    items = [i for i in cross["items"] if isinstance(i.get("sql_value"), (int, float))
             and isinstance(i.get("endpoint_value"), (int, float))]
    xs = [float(i["sql_value"]) for i in items]
    ys = [float(i["endpoint_value"]) for i in items]
    fig, ax = plt.subplots(figsize=(7, 6.5))
    ax.scatter(xs, ys, s=28, color="#2b5d8c", label=f"{len(items)} metrics")
    hi = max(max(xs), max(ys)) if xs else 1
    ax.plot([0, hi], [0, hi], "--", color="#b0740a", label="identity (endpoint == SQL)")
    ax.set_xscale("symlog", linthresh=1)
    ax.set_yscale("symlog", linthresh=1)
    ax.set_xlabel("independent SQL value")
    ax.set_ylabel("endpoint metric value")
    ax.set_title("Zero-mock cross-check: %d/%d matched" %
                 (cross["items_matched"], cross["items_total"]))
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "fig2_crosscheck_identity.png", dpi=78)
    plt.close(fig)
    print(f"figures → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())