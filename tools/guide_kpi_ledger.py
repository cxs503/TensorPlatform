#!/usr/bin/env python3
"""指南 KPI 台账 ETL（data 席 m1596）：把 12 项硬考核指标落成**可追溯快照序列**。

背景（审计事实，2026-11-01）：
  * 常设线 /root/tp-agent/guide_tracker.py 每 30min 重算 KPI，但只写
    /root/tp-agent/guide_dashboard.json（单文件覆盖写、无历史）；后端的
    tensor_guide_tracker 表仅由 /tensor/repro/kpi 端点写 4 个复现类 key，
    指南 12 项 KPI **从未入库**（审计时表内 0 行）→ 无趋势、无谱系。
  * 12 项里有 6 项是纯 SQL 可复算的（DB），另 6 项在 tracker 代码里是
    **硬编码字符串**（no-sql literal），无法由数据复核。

本工具做的事（最小、可验证）：
  1. 每项 KPI 都用显式「口径」记录：分子/分母/公式/SQL 或台账字段；
  2. DB 类：直接 SQL 直取（零手抄）；台账类：从 docs/kpi/literal_ledger.json
     读，并带 owner/date/证据指针，字面量必须留痕；
  3. 数值型 KPI 幂等 upsert 进 tensor_guide_tracker（按小时桶去重）；
     非数值（定性）KPI 不入库——metric_value 是 double NOT NULL，
     塞 0 等于说谎，故只进 artifacts JSON 并在报告中显式标 "qualitative"；
  4. --reconcile：把本次复算结果与 guide_dashboard.json 的 'cur' 字符串
     对账，输出漂移表（看板数字 vs 现场重算）。

用法：
  python3 tools/guide_kpi_ledger.py --dry-run            # 只打印，不写库
  python3 tools/guide_kpi_ledger.py --persist            # 幂等落库 + 生成工件
  python3 tools/guide_kpi_ledger.py --reconcile --json artifacts/.../kpi.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ORG_ID = int(os.environ.get("GUIDE_KPI_ORG_ID", "1"))
DASH_PATH = pathlib.Path(os.environ.get("GUIDE_DASH_PATH", "/root/tp-agent/guide_dashboard.json"))
REGISTRY_PATH = pathlib.Path(os.environ.get("GUIDE_REGISTRY_PATH",
                                           str(pathlib.Path(__file__).resolve().parent.parent
                                               / "artifacts" / "benchmark_settlement" / "audit.json")))
LEDGER_PATH = pathlib.Path(os.environ.get("GUIDE_LEDGER_PATH",
                                           str(pathlib.Path(__file__).resolve().parent.parent
                                               / "docs" / "kpi" / "literal_ledger.json")))
PG = dict(host=os.environ.get("TP_PG_HOST", "127.0.0.1"),
          user=os.environ.get("TP_PG_USER", "postgres"),
          db=os.environ.get("TP_PG_DB", "tensorplatform"))
DRIFT_REL_TOL = 0.02          # 相对漂移阈值：2%
LEDGER_STALE_DAYS = 14        # 字面量台账超过 14 天未复核 → 标 stale

# ─────────────────────────── 纯函数层（可单测，无 IO） ───────────────────────────

QR_OK = "succeeded"


def hour_bucket(ts: datetime | None = None) -> datetime:
    """UTC 小时桶（秒/微秒清零）：保证同一小时内重复跑是幂等 upsert。"""
    ts = ts or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def ratio(num, den, ndigits: int = 4):
    """安全比率。分母为 0/None → None（**不返回 0**，0 会被误读成"实测为零"）。"""
    try:
        n, d = float(num), float(den)
    except (TypeError, ValueError):
        return None
    if d == 0:
        return None
    return round(n / d, ndigits)


def pct(value, ndigits: int = 1):
    return None if value is None else round(100.0 * value, ndigits)


# KPI 规格：kind = 'db'（SQL 直取）| 'ledger'（字面量台账）
#   numeric=True → 有数值（可入库）；False → 定性（不入库，只报状态）
KPI_SPECS = [
    dict(key="qr_pass_rate", label="已合并功能复核通过率", kind="db", numeric=True,
         target=1.0, unit="ratio",
         formula="QR-P 前缀命中 succeeded / QR-P 终态单数",
         sql="""SELECT count(*) FILTER (WHERE status='succeeded') AS succ, count(*) AS term
                FROM tensor_dev_missions
                WHERE goal LIKE '%【QR-P%' AND status IN ('succeeded','failed')""",
         nums=("succ", "term"), how="ratio"),
    dict(key="tool_categories", label="工具接入 ≥5 类", kind="ledger", numeric=True,
         target=5.0, unit="count", ledger_field="tool_categories",
         formula="台账：已真实实现工具类计数（CFD/FEA/DEM/势流切片/经验公式/规范）"),
    dict(key="unattended_cases", label="无人值守 ≥1000 工况", kind="db", numeric=True,
         target=1000.0, unit="count",
         formula="扫荡线成功案例去重数（案例级代理口径，每案例≈1-3 工况）",
         sql="""SELECT count(DISTINCT substring(goal from '案例 ([a-zA-Z0-9_]+)（')) AS cases
                FROM tensor_dev_missions
                WHERE goal LIKE '%案例 %（Tensor%' AND status='succeeded'""",
         nums=("cases",), how="identity"),
    dict(key="success_rate", label="成功率 ≥90%+自动归因", kind="db", numeric=True,
         target=0.9, unit="ratio",
         formula="全程 succeeded / 终态单（succeeded+failed）",
         sql="""SELECT count(*) FILTER (WHERE status='succeeded') AS succ, count(*) AS term
                FROM tensor_dev_missions WHERE status IN ('succeeded','failed')""",
         nums=("succ", "term"), how="ratio"),
    dict(key="lineage_coverage", label="谱系追溯 100%", kind="db", numeric=True,
         target=1.0, unit="ratio",
         formula="有报告工件的 mission_id 去重数 / 终态单数（代理口径）",
         sql="""SELECT (SELECT count(DISTINCT mission_id) FROM tensor_reports
                        WHERE mission_id IS NOT NULL) AS covered,
                       (SELECT count(*) FROM tensor_dev_missions
                        WHERE status IN ('succeeded','failed')) AS term""",
         nums=("covered", "term"), how="ratio"),
    dict(key="repro_rate", label="复现 ≥95%", kind="db", numeric=True,
         target=0.95, unit="ratio",
         formula="(pass+partial) / tensor_repro_verdicts 总数；0 裁决 → None（未实测）",
         sql="""SELECT count(*) FILTER (WHERE verdict IN ('pass','partial')) AS ok,
                       count(*) AS total FROM tensor_repro_verdicts""",
         nums=("ok", "total"), how="ratio"),
    dict(key="gate_coverage", label="门禁覆盖 100%", kind="ledger", numeric=False,
         target=None, unit="ratio", ledger_field="gate_coverage",
         formula="台账（定性）：新线是否全接机器判据门；存量单未回填"),
    dict(key="kb_samples", label="知识库 ≥3学科/3型船/≥1万条", kind="db", numeric=True,
         target=10000.0, unit="count",
         formula="ship_samples 行数（报告 ≠ 样本，不可混算）",
         sql="SELECT count(*) AS samples FROM ship_samples",
         nums=("samples",), how="identity"),
    dict(key="benchmark_cases", label="对标算例 ≥10（SUBOFF/DTMB/KVLCC2）", kind="registry",
         numeric=True, target=10.0, unit="count", registry_field="benchmark_cases",
         formula="registry：正典 reference_status=DERIVED 且 |误差|≤15% 的已决算算例数"
                 "（tools/benchmark_settlement.py 复算，WITHHELD 行不计）"),
    dict(key="resistance_accuracy", label="静水阻力 ≤5%/冰区 ≤10%", kind="registry",
         numeric=True, target=1.0, unit="ratio", registry_field="resistance_accuracy",
         formula="registry：可判决（DERIVED）已决算算例中 |误差|≤5% 的比例；"
                 "无样本 → None（未实测，不塞 0）"),
    dict(key="cycle_reduction", label="周期 −50%/试验 −30%", kind="ledger", numeric=False,
         target=None, unit="ratio", ledger_field="cycle_reduction",
         formula="台账（定性）：无计时基线，指标不可算"),
    dict(key="ip_assets", label="标准≥3/专利≥5/论文≥5/软著≥5", kind="ledger", numeric=True,
         target=5.0, unit="count", ledger_field="ip_assets",
         formula="台账：计数口径取「已立项/骨架」数（论文骨架 1）"),
]


def spec_index(specs=KPI_SPECS) -> dict:
    return {s["key"]: s for s in specs}


def merge_rows(spec: dict, row: dict) -> float | None:
    """把一行 SQL 结果按 spec 的口径折算成标量。"""
    nums = spec["nums"]
    vals = [row.get(n) for n in nums]
    if any(v is None for v in vals):
        return None
    if spec["how"] == "ratio":
        return ratio(vals[0], vals[1])
    return float(vals[0])


def build_records(db_metrics: dict, ledger: dict, specs=KPI_SPECS, now: datetime | None = None,
                  registry: dict | None = None):
    """合并 DB / 台账 / registry 工件 → 统一记录列表。三类 kind 互斥（规格表内不相交）。

    registry = tools/benchmark_settlement.py 落盘的决算工件；缺失 → 该 KPI 记 None
    （未实测），并标 stale=True，绝不退回手写字面量冒充证据。
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for s in specs:
        key = s["key"]
        rec = dict(key=key, label=s["label"], kind=s["kind"], numeric=s["numeric"],
                   target=s["target"], unit=s["unit"], formula=s["formula"])
        if s["kind"] == "db":
            rec["value"] = db_metrics.get(key)
            rec["source"] = "db:" + re.sub(r"\s+", " ", s["sql"]).strip()
            rec["provenance"] = "live SQL（零手抄）"
            rec["stale"] = False
        elif s["kind"] == "registry":
            fld = s["registry_field"]
            rec["source"] = f"registry:{fld}"
            kpi_block = (registry or {}).get("kpi") or {}
            rec["value"] = kpi_block.get(fld)
            rec["provenance"] = ("registry 决算工件（零手抄复算）" if registry
                                 else "registry 工件缺失 → 未实测")
            recs_summary = (registry or {}).get("summary") or {}
            rec["status_text"] = (f"{int(recs_summary.get('qualified_cases', 0))} 合格 / "
                                  f"{recs_summary.get('settled_admissible', 0)} 已决算 / "
                                  f"{recs_summary.get('policy_violations', 0)} 越权判决"
                                  if registry else "无决算工件")
            rec["stale"] = registry is None
            rec["age_days"] = 0 if registry else None
        else:
            fld = s["ledger_field"]
            if fld not in ledger.get("values", {}):
                raise KeyError(f"台账缺字段 {fld}（KPI {key}）")
            cell = ledger["values"][fld]
            rec["value"] = cell.get("value") if s["numeric"] else None
            rec["source"] = "ledger:" + fld
            rec["provenance"] = f"{cell.get('owner', '?')} @ {cell.get('as_of', '?')}"
            rec["evidence"] = cell.get("evidence", "")
            rec["status_text"] = cell.get("status_text", "")
            try:
                as_of = datetime.strptime(cell.get("as_of", ""), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                rec["stale"] = (now - as_of) > timedelta(days=LEDGER_STALE_DAYS)
                rec["age_days"] = (now - as_of).days
            except ValueError:
                rec["stale"] = True
                rec["age_days"] = None
        if rec["numeric"]:
            rec["progress"] = ratio(rec["value"], rec["target"]) if rec["target"] else None
        else:
            rec["progress"] = None
        out.append(rec)
    return out


_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%?")


def parse_dashboard_cur(cur: str) -> dict:
    """从看板 'cur' 字符串里抠出可比值（用于对账），保留模式与分子/分母。

      "71.7%（358/499；48h …）"  → {mode:'pct',   value:71.7, num:358, den:499}
      "4/4（在飞 0）"             → {mode:'count', value:4,    num:4,   den:4}
      "454/499 有报告工件"        → {mode:'count', value:454,  num:454, den:499}
      "样本 0（报告 270 篇≠样本）" → {mode:'count', value:0,    num:None,den:None}
      "机制就绪" / "无基线"        → {mode:'unparsable', value:None}
    """
    out = dict(mode="unparsable", value=None, num=None, den=None, raw=cur or "")
    if not cur:
        return out
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", cur)
    if m:
        out.update(mode="pct", value=float(m.group(1)))
        frac = re.search(r"(\d+)\s*/\s*(\d+)", cur)
        if frac:
            out.update(num=float(frac.group(1)), den=float(frac.group(2)))
        return out
    frac = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", cur)
    if frac:
        out.update(mode="count", value=float(frac.group(1)),
                   num=float(frac.group(1)), den=float(frac.group(2)))
        return out
    m = re.search(r"(\d+(?:\.\d+)?)", cur)
    if m:
        out.update(mode="count", value=float(m.group(1)), num=float(m.group(1)))
    return out


def normalize_to_unit(parsed: dict, unit: str):
    """把看板解析结果折算到 KPI 记录的单位（ratio/count）。

    ★ 单位错配是本工具的初版真 bug：看板把"4/4"写成计数、把"454/499"写成计数，
      而 KPI 记录是比率；直接比 4 vs 1.0 会误报漂移。
    """
    mode, val = parsed.get("mode"), parsed.get("value")
    if val is None:
        return None
    if unit == "ratio":
        if mode == "pct":
            return round(val / 100.0, 4)
        if mode == "count" and parsed.get("den"):
            return round(parsed["num"] / parsed["den"], 4)
        return None
    if unit == "count":
        return val if mode == "count" else None
    return None


def reconcile(records, dashboard: dict, tol: float = DRIFT_REL_TOL):
    """对账：本工具复算值 vs 看板 'cur' 字符串（单位对齐后比较）。"""
    by_label = {r["label"]: r for r in records}
    rows = []
    for k in dashboard.get("kpis", []):
        rec = by_label.get(k.get("kpi"))
        if rec is None:
            rows.append(dict(kpi=k.get("kpi"), cur=k.get("cur"), status="unmapped",
                             note="看板有、规格表无此 KPI（口径未登记）"))
            continue
        parsed = parse_dashboard_cur(k.get("cur"))
        live = rec.get("value")
        dash_unit_val = normalize_to_unit(parsed, rec["unit"])
        item = dict(kpi=rec["label"], metric_key=rec["key"], cur=k.get("cur"),
                    dash_mode=parsed.get("mode"), dash_raw_value=parsed.get("value"),
                    dash_in_unit=dash_unit_val, live=live, unit=rec["unit"],
                    kind=rec["kind"])
        if not rec["numeric"] or live is None or dash_unit_val is None:
            item["status"] = "skipped"
            item["note"] = "定性/未实测/不可折算 → 无比对基准"
            rows.append(item)
            continue
        base = abs(dash_unit_val)
        drift = abs(live - dash_unit_val)
        item["abs_drift"] = round(drift, 4)
        item["rel_drift"] = 0.0 if base == 0 else round(drift / base, 4)
        item["status"] = "ok" if (drift <= 1e-9 or (base > 0 and drift / base <= tol)) else "drift"
        if item["status"] == "drift":
            item["note"] = (f"看板 {dash_unit_val} vs 现场 {live}"
                            f"（相对 {pct(item['rel_drift'])}%）；看板快照 "
                            f"{dashboard.get('ts', '?')} 已过期，需重新快照")
        rows.append(item)
    return rows


def snapshot_rows(records, org_id: int = ORG_ID, bucket: datetime | None = None):
    """数值型记录 → tensor_guide_tracker 行（定性 KPI 不入库：metric_value 非空 double）。"""
    bucket = bucket or hour_bucket()
    rows = []
    for r in records:
        if not r["numeric"] or r["value"] is None:
            continue
        rows.append(dict(org=org_id, key=f"guide.{r['key']}", val=float(r["value"]),
                         target=None if r["target"] is None else float(r["target"]),
                         snap=bucket.isoformat(),
                         notes=(f"{r['label']} | {r['formula']} | src={r['source']}"
                                f" | progress={r['progress']}")))
    return rows


UPSERT_SQL = """
INSERT INTO tensor_guide_tracker (organization_id, metric_key, metric_value, target_value, snapshot_at, notes)
VALUES (:org, :key, :val, :target, CAST(:snap AS timestamptz), :notes)
ON CONFLICT (organization_id, metric_key, snapshot_at)
DO UPDATE SET metric_value = EXCLUDED.metric_value,
              target_value = EXCLUDED.target_value,
              notes = EXCLUDED.notes
"""


def _sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def render_upsert(row) -> str:
    """把 upsert 渲染成可执行 SQL（psql 通道，环境无 psycopg2）。"""
    stmt = UPSERT_SQL
    for k, v in row.items():
        stmt = stmt.replace(":" + k, _sql_literal(v))
    return stmt + ";"


# ─────────────────────────── IO 层 ───────────────────────────

def q(sql: str) -> list[dict]:
    """psql 直取，返回 list[dict]（对齐 guide_tracker.py 的 subprocess 通道）。"""
    env = dict(os.environ, PGPASSWORD=os.environ.get("TP_PG_PASSWORD", ""))
    cmd = ["psql", "-h", PG["host"], "-U", PG["user"], "-d", PG["db"],
           "-tAF", "\x1f", "-c", sql]
    cp = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    if cp.returncode != 0:
        raise RuntimeError(f"psql 失败: {cp.stderr.strip()[:400]}")
    rows = []
    for line in cp.stdout.strip().split("\n"):
        if not line.strip():
            continue
        rows.append(line.split("\x1f"))
    if not rows:
        return []
    return rows


def one(sql: str) -> dict:
    """单行多列查询 → {列序号: 值}，由调用方按 spec['nums'] 顺序取。"""
    rows = q(sql)
    if not rows:
        return {}
    return dict(enumerate(rows[0]))


def fetch_metrics(specs=KPI_SPECS) -> tuple[dict, dict]:
    """跑全部 DB 类 KPI。返回 ({key: value}, {key: 原始分子分母})。

    原始分子分母一并留档：报告里的 "821/1027" 这类数字也必须程序化注入，不许手抄。
    """
    out, raws = {}, {}
    for s in specs:
        if s["kind"] != "db":
            continue
        cell = one(s["sql"])
        row = {name: cell.get(i) for i, name in enumerate(s["nums"])}
        out[s["key"]] = merge_rows(s, row)
        raws[s["key"]] = row
    return out, raws


def load_ledger(path: pathlib.Path = LEDGER_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry(path: pathlib.Path = REGISTRY_PATH) -> dict | None:
    """读决算工件（tools/benchmark_settlement.py 产出）。不存在 → None。"""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_dashboard(path: pathlib.Path = DASH_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"kpis": []}


def persist(rows) -> int:
    if not rows:
        return 0
    sql = "\n".join(render_upsert(r) for r in rows)
    cp = subprocess.run(["psql", "-h", PG["host"], "-U", PG["user"], "-d", PG["db"],
                         "-v", "ON_ERROR_STOP=1", "-f", "-"],
                        input=sql, capture_output=True, text=True,
                        env=dict(os.environ, PGPASSWORD=os.environ.get("TP_PG_PASSWORD", "")),
                        timeout=180)
    if cp.returncode != 0:
        raise RuntimeError(f"落库失败: {cp.stderr.strip()[:400]}")
    return len(rows)


# ─────────────────────────── 主流程 ───────────────────────────

def run(persist_rows: bool = False, do_reconcile: bool = True, json_out: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    bucket = hour_bucket(now)
    ledger = load_ledger()
    metrics, db_raw = fetch_metrics()
    records = build_records(metrics, ledger, now=now, registry=load_registry())
    drift = reconcile(records, load_dashboard()) if do_reconcile else []
    rows = snapshot_rows(records, bucket=bucket)
    written = persist(rows) if persist_rows else 0
    payload = dict(
        generated_at=now.isoformat(),
        bucket=bucket.isoformat(),
        org_id=ORG_ID,
        ledger_path=str(LEDGER_PATH),
        n_kpi=len(records),
        n_db=sum(1 for r in records if r["kind"] == "db"),
        n_ledger=sum(1 for r in records if r["kind"] == "ledger"),
        n_numeric=sum(1 for r in records if r["numeric"]),
        n_qualitative=sum(1 for r in records if not r["numeric"]),
        n_persistable=len(rows),
        n_written=written,
        db_raw=db_raw,
        records=records,
        rows=rows,
        drift=drift,
        drift_summary=dict(
            ok=sum(1 for d in drift if d.get("status") == "ok"),
            drift=sum(1 for d in drift if d.get("status") == "drift"),
            skipped=sum(1 for d in drift if d.get("status") == "skipped"),
            unmapped=sum(1 for d in drift if d.get("status") == "unmapped"),
        ),
    )
    if json_out:
        p = pathlib.Path(json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="指南 KPI 台账 ETL")
    ap.add_argument("--persist", action="store_true", help="幂等落库到 tensor_guide_tracker")
    ap.add_argument("--dry-run", action="store_true", help="只计算不落库（默认行为）")
    ap.add_argument("--no-reconcile", action="store_true", help="跳过与看板 json 的对账")
    ap.add_argument("--json", default=None, help="把完整结果写到该 JSON 路径")
    a = ap.parse_args(argv)
    p = run(persist_rows=a.persist, do_reconcile=not a.no_reconcile, json_out=a.json)
    print(json.dumps({k: p[k] for k in ("generated_at", "bucket", "n_kpi", "n_db", "n_ledger",
                                        "n_numeric", "n_qualitative", "n_persistable",
                                        "n_written", "drift_summary")},
                     ensure_ascii=False, indent=2))
    for r in p["records"]:
        val = "—" if r["value"] is None else f"{r['value']:.4g}"
        prog = "—" if r["progress"] is None else f"{pct(r['progress'])}%"
        flag = " [STALE]" if r.get("stale") else ""
        print(f"  {r['key']:<22} {val:>10}  target={r['target']}  prog={prog:>8}  "
              f"src={r['kind']}{flag}")
    for d in p["drift"]:
        if d.get("status") in ("drift", "unmapped"):
            print(f"  DRIFT {d.get('kpi')}: dash={d.get('cur')} live={d.get('live')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())