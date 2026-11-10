#!/usr/bin/env python3
"""Post the p228w1-N10 sustainment-matrix report to the tensor platform."""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
ART = WORKTREE / "artifacts" / "sustainment"
REPORT = WORKTREE / "docs" / "reports" / "P228W1-N10_sustainment_matrix.md"

api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

login = json.dumps({"username": user, "password": passwd}).encode()
req = urllib.request.Request(f"{api_base}/auth/login", data=login,
                             headers={"Content-Type": "application/json"})
token = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]

md = REPORT.read_text(encoding="utf-8")
drill = json.loads((ART / "backup_drill.json").read_text())
gc = json.loads((ART / "worktree_gc.json").read_text())
apply_gc = json.loads((ART / "worktree_gc_apply.json").read_text())
lin = json.loads((ART / "lineage.json").read_text())
ver = json.loads((ART / "lineage_verify.json").read_text())
audit = json.loads((ART / "evidence_audit.json").read_text())
stray = gc["stray_dirs"]

md += (
    "\n\n## 附：数字口径（投稿时自动注入，防止摘要与工件漂移）\n\n"
    f"* 全部数字取自本 commit 内 `artifacts/sustainment/*.json`；本报告自身的证据链审计 "
    f"（`tools/sustainment/evidence_audit.py`）= **{'PASS' if audit['ok'] else 'FAIL'}**："
    f"六要素 {audit['checks']['C0_sections']['found']}/6、图问题 {audit['checks']['C1_figures']['problems']}、"
    f"数字溯源覆盖率 {audit['checks']['C2_numbers']['coverage']:.4f}（未溯源 {audit['checks']['C2_numbers']['untraced']}）、"
    f"判据 {audit['checks']['C3_criteria']['passed']}/{audit['checks']['C3_criteria']['total']} 通过、"
    f"判据不可核 {audit['checks']['C3_criteria']['unverified']}、判决冲突 {audit['checks']['C3_criteria']['inconsistent']}。\n"
    f"* 血缘：{lin['summary']['nodes']} 节点 / {lin['summary']['edges']} 边，sha256 覆盖率 "
    f"{lin['summary']['sha256_coverage']}，原始环 {lin['cycles_raw']} → 未解 "
    f"{lin['summary']['cycles_unresolved']}（丢弃弱边 {len(lin['cycle_resolution']['dropped_edges'])} 条，逐条入审计链）；"
    f"冻结后 verify 漂移 {sum(ver['drift']['counts'].values())}（受检 {ver['drift']['checked']} 文件）。\n"
    f"* 备份演练：{len(drill['tables'])} 表 × {drill['tables_detail'][0]['source']['row_count']} 行，"
    f"三重对拍通过 {drill['checks']['triple_cross_check']['tables_matched']}/{len(drill['tables'])}，"
    f"坏备份负控检出 {sum(1 for n in drill['negative_controls'] if n['detected'])}/"
    f"{len(drill['negative_controls'])}，RTO {drill['rto_s']} s / 预算 {drill['budgets']['rto_s']:.0f} s。\n"
    f"* 工作树：普查 {gc['summary']['worktrees']} 个（保护 {gc['summary']['protected']}、可回收 "
    f"{apply_gc['summary']['removable']}），apply 归档并移除 {sum(1 for a in apply_gc['actions'] if a.get('removed'))} 个，"
    f"释放 {apply_gc['summary']['bytes_by_class']['finalized']} B，归档 "
    f"{apply_gc['summary']['reclaimed_bytes']} B（该字段首轮语义有误，已修正并见报告 ⑤-5）；"
    f"磁盘 {gc['disk']['used_pct']}% → 门限 {gc['gate_verdict']['level']}。\n"
    f"* 盲区更正：未注册目录 {stray['unregistered']} 个 = 其它仓活动工作树 {stray['foreign_worktrees']} "
    f"+ 真残留 {stray['stray']}（{stray['estimated_stray_bytes']} B，全量枚举）；"
    "首版无条件把未注册目录当残留、外推 241 GB，加入 `.git` 判别后被证伪（工件 "
    "`artifacts/sustainment/stray_dirs_naive_estimate.json` 保留作否定证据）。\n"
    "* honest-FAIL / WITHHELD 保留：PostgreSQL 实库演练 `"
    + json.loads((ART / "backup_drill_postgres.json").read_text())["verdict"]
    + "`（无 DB 凭据），线上 PG 元数据可恢复性仍为 WITHHELD；R1 声明边为 "
    f"{lin['summary']['by_rule'].get('R1_declared', 0)}（平台尚无 provenance sidecar）。\n"
)

captions = {
    "lineage_dag.png": "图1 工件血缘 DAG（节点按 kind 着色，边按证据规则着色；全量见 lineage.json）",
    "backup_drill.png": "图2 备份恢复演练：各表源/恢复行数、三重对拍矩阵（全绿）、RTO/RPO vs 预算（对数轴）",
    "worktree_gc.png": "图3 工作树六类普查 + 保护/可回收 + 磁盘压力门（实测 vs 85%/90%）",
}
figs = []
for name, caption in captions.items():
    png = (ART / "figures" / name).read_bytes()
    figs.append({"name": name, "caption": caption,
                 "png_base64": base64.b64encode(png).decode("ascii")})

report = {
    "title": "p228w1-N10 平台自主续航运维军：工件血缘 DAG · 证据链校验 · 备份恢复演练 · 工作树治理",
    "summary": (
        "把「元数据可恢复/工作树可治理」变成机器裁决，交付四个零领域语义可独立调用的机制（65 回归用例）："
        f"(1)血缘 DAG：真实树 {lin['summary']['nodes']} 文件全量 sha256、{lin['summary']['edges']} 证据分级边，"
        f"原始环 {lin['cycles_raw']}→未解 {lin['summary']['cycles_unresolved']}，冻结后 verify 零漂移；"
        f"(2)证据链：图 sha256/数字溯源/判据重算，本报告自检 "
        f"{audit['checks']['C3_criteria']['passed']}/{audit['checks']['C3_criteria']['total']} 通过、零未溯源数字；"
        f"(3)备份演练：隔离命名空间+三重对拍 {drill['checks']['triple_cross_check']['tables_matched']}/{len(drill['tables'])} 表+"
        f"三类坏备份负控 3/3 检出+RPO/RTO 账本；"
        f"(4)工作树 GC：六类保护优先，force 级磁盘门 {gc['disk']['used_pct']}% 下归档移除 "
        f"{sum(1 for a in apply_gc['actions'] if a.get('removed'))} 棵 finalized 树释放 "
        f"{apply_gc['summary']['bytes_by_class']['finalized'] / 1e9:.3f} GB，保护类零误删。"
        "自查修正两处真实缺陷（释放量指标语义、verifier 未重放环消解），并证伪自身 241 GB 残留估计"
        "（真残留 0.33 GB）。PostgreSQL 实库演练无凭据保持 WITHHELD。"
    ),
    "scenario_key": "platform-sustainment-ops-matrix",
    "mission_id": 9213,
    "tags": ["p228w1-N10", "sustainment", "artifact-lineage", "evidence-chain",
             "backup-restore-drill", "rpo-rto", "negative-control", "worktree-gc",
             "disk-pressure-gate", "platform-autonomy"],
    "markdown": md,
    "figures": figs,
}

req = urllib.request.Request(
    f"{api_base}/tensor/reports",
    data=json.dumps(report, ensure_ascii=False).encode("utf-8"),
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    method="POST",
)
try:
    out = json.loads(urllib.request.urlopen(req, timeout=120).read())
    print("Report submitted:", json.dumps(out, ensure_ascii=False)[:500])
except Exception as exc:  # pragma: no cover
    print("Report submission failed:", exc, file=sys.stderr)
    if hasattr(exc, "read"):
        print(exc.read().decode()[:1500], file=sys.stderr)
    sys.exit(1)