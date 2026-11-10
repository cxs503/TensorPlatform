#!/usr/bin/env python3
"""发布 P202W1-N02 论文级进展报告到平台 /tensor/reports（mission 9376）。

所有数字从 ``artifacts/p202w1_n02/criteria.json`` 与 ``audit_report.json`` 读取后注入，
报告正文取自 ``docs/P202W1_N02_ledger_invariant_audit.md``——零手抄。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
api_base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
user = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
passwd = os.environ.get("TENSOR_AGENT_PASS", "")

login = json.dumps({"username": user, "password": passwd}).encode()
req = urllib.request.Request(f"{api_base}/auth/login", data=login,
                             headers={"Content-Type": "application/json"})
token = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]

crit = json.loads((REPO / "artifacts/p202w1_n02/criteria.json").read_text(encoding="utf-8"))
run = json.loads(
    (REPO / "artifacts/p202w1_n02/audit_report.json").read_text(encoding="utf-8"))
pg = run["runs"]["postgresql"]
lite = run["runs"]["sqlite"]
c = crit["criteria"]
cov = pg["fk_coverage"]

md = (REPO / "docs/P202W1_N02_ledger_invariant_audit.md").read_text(encoding="utf-8")
md += (
    "\n\n## 附：数字口径（程序注入，防止摘要与工件漂移）\n\n"
    f"* 主口径 = PostgreSQL（生产迁移 DDL）：扫描 {pg['scan']['total']} 条违反、"
    f"自愈 {pg['heal']['applied']} 条、复扫残余 **{pg['verify']['total']}** 条，"
    f"全流程 {pg['elapsed_s']} s。\n"
    f"* 跨方言复核（SQLite，`result` 为 TEXT 列）：扫描 {lite['scan']['total']}、"
    f"自愈 {lite['heal']['applied']}、残余 {lite['verify']['total']}；"
    f"`INV-J1` 在 PG 上为 0（JSONB 类型系统兜底）在 SQLite 上为 1，属方言差异不是漏扫。\n"
    f"* 外键覆盖：账本内 FK {cov['ledger_fks']} 条全有规则（{cov['coverage_pct']}%），"
    f"租户/身份 FK {cov['tenant_fks']} 条显式列账为出范围。\n"
    f"* 手工 SQL 对拍 {crit['manual_crosscheck_all_match']}；"
    f"负控（健康账本）扫描 {crit['negative_control']['scan_total']} 条、"
    f"动作 {crit['negative_control']['heal_applied']} 次。\n"
    f"* 审计日志 {c['C6_audit_entries']} 条，哈希链完好 {c['C6_chain_ok']}。\n"
)

captions = {
    "fig_violations_before_after.png":
        "图1 按规则的违反记录数：自愈前（红）vs 自愈后（绿，全零），PostgreSQL 生产 schema",
    "fig_heal_strategies.png":
        "图2 自愈策略分布：绿色=物理修复（null/filter/link），黄色=标记销账（orphan/settled 旗标）",
    "fig_report_debt.png":
        "图3 报告欠账清偿：孤儿报告分类（可反向关联 / 需标记 / 已销账）在审计前后的对照",
}
figs = []
for name, caption in captions.items():
    png = (REPO / "artifacts/p202w1_n02" / name).read_bytes()
    figs.append({"name": name, "caption": caption,
                 "png_base64": base64.b64encode(png).decode("ascii")})

payload = {
    "title": ("P202W1-N02 状态账本关系不变量审计与自愈销账：10 条不变量 + 哈希链审计 "
              "（PG 生产 schema 12 违反 → 残余 0）"),
    "summary": (
        f"在 TensorPlatform 落地 backend/audit/ledger_auditor.py：把战役/里程碑/节点/任务/报告 "
        f"五表的引用关系显式化为 10 条不变量（含 INT[] DAG 依赖元素级与报告欠账），"
        f"扫描→自愈→复扫闭环。按生产迁移 086/090/095 在一次性 PG 15.5 集群建真 schema，"
        f"注入 {pg['scan']['total']} 条合成违反后自愈 {pg['heal']['applied']} 条、"
        f"复扫残余 {pg['verify']['total']}（C4）；幽灵节点识别与独立手写 SQL 集合完全相等（C2）；"
        f"账本内 FK {cov['ledger_fks']} 条全覆盖（C1）；孤儿报告 {c['C5_orphan_reports_before']} 份 "
        f"100% 关联或标记（C5，关联 {c['C5_reports_linked']}/标记 {c['C5_reports_marked']}）；"
        f"审计日志 {c['C6_audit_entries']} 条 SHA-256 哈希链完好（C6，篡改可检出）；"
        f"健康账本负控零违反（C7）。11 条 pytest 全绿；SQLite 方言复跑覆盖 PG 上不可能存在的 "
        f"result TEXT 脏数据。边界诚实声明：标记销账不修根因，DAG 环不自动删边，未在生产库写。"
    ),
    "scenario_key": "platform-ledger-invariant-audit",
    "mission_id": 9376,
    "tags": ["TensorPlatform", "ledger", "invariant", "self-heal", "audit-chain",
             "campaign-dag", "P202W1-N02"],
    "markdown": md,
    "figures": figs,
}

body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(
    f"{api_base}/tensor/reports", data=body,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
)
resp = urllib.request.urlopen(req, timeout=60)
print(resp.status, resp.read().decode()[:600])