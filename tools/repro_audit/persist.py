"""repro_audit.persist —— 把实测 ``repro_rate`` 落 ``tensor_guide_tracker``（三级降级）。

落库路径（按可用性依次尝试，全部留痕）：
  1. **平台 API**：``POST /tensor/repro/audit-kpi``（本仓 ``backend/api/tensor_repro.py``
     新增端点；端点自己按逐项判决复算 rate，与提交值不一致即 422 拒绝 —— 防「KPI 与明细脱钩」）。
  2. **psql**：``TP_PG_DSN`` + ``TP_PG_PASSWORD``（部署机上有凭据时走这条）。
  3. **pending 工件**：两者都不可用时写 ``artifacts/repro_audit/kpi_pending.json`` 与
     ``kpi_upsert.sql``（幂等、可直接由值守席位 `psql -f` 重放），``status='pending'``——
     **绝不**在没有真实写入的情况下报 ``persisted``。

写后校验：读回 ``tensor_guide_tracker`` 的 ``repro_rate``，要求
``|db_value − json_value| <= 1e-9``（判据 b），否则报 ``mismatch``。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from . import core

METRIC_KEY = "repro_rate"
BLOCKED_KEY = "repro_audit_blocked_share"
TARGET = 0.95
TOL = 1e-9


def hour_bucket(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()


def build_payload(summary: dict[str, Any]) -> dict[str, Any]:
    """从审计 JSON 造 KPI 载荷：数值只从逐项判决复算，**不接受外部传入**。"""
    items = sorted(({"mission_id": i["mission_id"], "verdict": i["verdict"]} for i in summary["items"]),
                   key=lambda x: (x["mission_id"], x["verdict"]))
    kpi = core.kpi_from_items(summary["items"])
    if kpi["repro_rate"] is None:
        raise ValueError("no countable items: repro_rate undefined (refuse to write 0)")
    digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    return {
        "metric_key": METRIC_KEY,
        "metric_value": kpi["repro_rate"],
        "target_value": TARGET,
        "items": items,
        "items_sha256": digest,
        "blocked_share": kpi["blocked_share"],
        "counts": kpi["counts"],
        "source": "tools.repro_audit",
        "captured_at": hour_bucket(),
    }


def upsert_sql(payload: dict[str, Any], org_id: int = 1) -> str:
    """幂等 upsert SQL（小时桶冲突即覆盖，与 guide_kpi_ledger 的口径一致）。"""
    snap = payload["captured_at"]
    rows = [(METRIC_KEY, payload["metric_value"], payload["target_value"]),
            (BLOCKED_KEY, payload["blocked_share"] or 0.0, None)]
    stmts = ["BEGIN;"]
    for key, value, target in rows:
        tv = "NULL" if target is None else f"{float(target):.10f}"
        stmts.append(
            "INSERT INTO tensor_guide_tracker (organization_id, metric_key, metric_value, "
            "target_value, snapshot_at) VALUES "
            f"({int(org_id)}, '{key}', {float(value):.10f}, {tv}, '{snap}') "
            "ON CONFLICT (organization_id, metric_key, snapshot_at) DO UPDATE "
            "SET metric_value = EXCLUDED.metric_value;")
    stmts.append("COMMIT;")
    return "\n".join(stmts) + "\n"


def _try_api(payload: dict[str, Any]) -> dict[str, Any]:
    base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
    try:
        req = urllib.request.Request(base + "/auth/login",
                                     data=json.dumps({"username": os.environ["TENSOR_AGENT_USER"],
                                                      "password": os.environ["TENSOR_AGENT_PASS"]}).encode(),
                                     headers={"Content-Type": "application/json"})
        token = json.load(urllib.request.urlopen(req, timeout=60))["access_token"]
        body = {"metric_value": payload["metric_value"], "items": payload["items"],
                "items_sha256": payload["items_sha256"], "captured_at": payload["captured_at"]}
        req = urllib.request.Request(base + "/tensor/repro/audit-kpi", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {token}"})
        resp = json.load(urllib.request.urlopen(req, timeout=60))
        db_value = resp.get("repro_rate")
        ok = db_value is not None and abs(float(db_value) - payload["metric_value"]) <= TOL
        return {"status": "persisted" if ok else "mismatch", "transport": "api",
                "db_value": db_value, "detail": resp}
    except urllib.error.HTTPError as exc:
        return {"status": "unavailable", "transport": "api",
                "detail": f"HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}"}
    except Exception as exc:                                     # 网络/鉴权缺失
        return {"status": "unavailable", "transport": "api", "detail": str(exc)[:300]}


def _try_psql(payload: dict[str, Any], sql: str) -> dict[str, Any]:
    dsn = os.environ.get("TP_PG_DSN")
    pwd = os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD")
    if not dsn or not pwd:
        return {"status": "unavailable", "transport": "psql",
                "detail": "TP_PG_DSN/TP_PG_PASSWORD absent in this sandbox"}
    env = dict(os.environ, PGPASSWORD=pwd)
    proc = subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", sql],
                          capture_output=True, text=True, env=env, timeout=120)
    if proc.returncode != 0:
        return {"status": "unavailable", "transport": "psql", "detail": proc.stderr[-300:]}
    read = subprocess.run(["psql", dsn, "-tAc",
                           "SELECT metric_value FROM tensor_guide_tracker WHERE metric_key='repro_rate' "
                           "ORDER BY snapshot_at DESC LIMIT 1"],
                          capture_output=True, text=True, env=env, timeout=120)
    try:
        db_value = float(read.stdout.strip())
    except ValueError:
        return {"status": "mismatch", "transport": "psql", "detail": read.stderr[-300:]}
    ok = abs(db_value - payload["metric_value"]) <= TOL
    return {"status": "persisted" if ok else "mismatch", "transport": "psql", "db_value": db_value}


def persist_kpi(summary: dict[str, Any], sql_out: str = "artifacts/repro_audit/kpi_upsert.sql",
                org_id: int = 1) -> dict[str, Any]:
    """先落 pending 工件（任何情况下都留痕），再尝试真实写入；返回状态字典（诚实标注）。"""
    payload = build_payload(summary)
    sql = upsert_sql(payload, org_id)
    core.dump_json(os.path.join(os.path.dirname(sql_out), "kpi_pending.json"), payload)
    os.makedirs(os.path.dirname(os.path.abspath(sql_out)) or ".", exist_ok=True)
    with open(sql_out, "w", encoding="utf-8") as fh:
        fh.write(sql)
    for attempt in (_try_api, lambda p: _try_psql(p, sql)):
        result = attempt(payload)
        if result["status"] in ("persisted", "mismatch"):
            return {**result, "metric_value": payload["metric_value"],
                    "items_sha256": payload["items_sha256"], "sql_path": sql_out,
                    "consistent": result["status"] == "persisted"}
    result = {"status": "pending", "metric_value": payload["metric_value"],
              "items_sha256": payload["items_sha256"], "sql_path": sql_out,
              "consistent": False,
              "detail": "no writable transport in this environment; SQL+payload staged for replay",
              "attempts": []}
    api = _try_api(payload)
    result["attempts"].append({"transport": "api", "status": api["status"], "detail": api.get("detail")})
    result["attempts"].append({"transport": "psql", "status": "unavailable",
                               "detail": "TP_PG_DSN/TP_PG_PASSWORD absent"})
    return result