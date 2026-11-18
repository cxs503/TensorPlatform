#!/usr/bin/env python3
"""报告 QA 常设门守护（owner 2026-11-11 FEAT-TK-GOV-INFRA 令）。

四查 + S1/S2/S3 分级 + 结构化拒绝理由；存量按 mission_id 倒序消化。
- 门逻辑：backend/services/report_qa_gate.py（纯函数，与 API 同源，禁 fork）
- 查② result 复核 API 链：引用报告 id → GET /tensor/reports/{id}；
  工件路径 → 生产树 fs；git commit → git cat-file
- 落库：qa_status / qa_rejection_reason / qa_checked_at / grade / grade_reason
  / qa_machine_tier / qa_machine_audit
- 常设：tp_autoheal REGISTRY 纳管（ps 可见）；永不崩溃（异常吞掉记日志重试）

用法：python3 ops/report_qa_gate.py [--once] [--batch 500]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
USER = os.environ.get("TENSOR_AGENT_USER", "eco_admin")
PASS = os.environ.get("TENSOR_AGENT_PASS", "")
STATE_F = pathlib.Path(os.environ.get("QA_GATE_STATE", "/root/tp-agent/report_qa_gate_state.json"))
LOG_F = pathlib.Path(os.environ.get("QA_GATE_LOG", "/var/log/tp-report-qa-gate.log"))
PG = ["psql", "-h", "127.0.0.1", "-U", "foundation", "-d", "tensorplatform", "-tAc"]
PGENV = {**os.environ, "PGPASSWORD": os.environ.get("TP_PG_PASS", "uGTN-KwuQv_7l8Tw0MMWUx_kf-nF2Rzz")}

#: 门模块候选根（生产树优先=合并后正典；工作树次之；稳定副本兜底）
GATE_ROOTS = [
    "/root/tensor-ecosystem/TensorPlatform",
    "/root/tp-agent/work/m14354",
    "/root/tp-agent/qa_gate_lib",
]
POLL_S = 30
BATCH = 500
MAX_VERIFY_PER_BATCH = 400


def log(m: str) -> None:
    try:
        with open(LOG_F, "a") as f:
            f.write(time.strftime("[%m-%d %H:%M:%S] ") + m + "\n")
    except Exception:
        pass


def q(sql: str) -> str:
    return subprocess.run(PG + [sql], capture_output=True, text=True,
                          env=PGENV, timeout=120).stdout.strip()


def qrows(sql: str) -> list[str]:
    return [l for l in q(sql).splitlines() if l.strip()]


def api(method: str, path: str, payload=None, token: str | None = None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


def login() -> str:
    s, d = api("POST", "/auth/login", {"username": USER, "password": PASS})
    if s != 200:
        raise RuntimeError(f"login {s}")
    return d["access_token"]


def load_gate_module():
    """按候选根 import 门模块（生产树→工作树→稳定副本）。"""
    for root in GATE_ROOTS:
        cand = pathlib.Path(root) / "backend" / "services" / "report_qa_gate.py"
        if cand.exists():
            if root not in sys.path:
                sys.path.insert(0, str(pathlib.Path(root) / "backend"))
            import importlib
            import services.report_qa_gate as mod
            importlib.reload(mod)
            log(f"门模块加载自 {root}")
            return mod
    raise RuntimeError("找不到 backend/services/report_qa_gate.py（候选根均无）")


class Verifier:
    """查② result 复核 API 链：报告 id 走 API；路径走 fs；commit 走 git。"""

    def __init__(self, token: str):
        self.token = token
        self._rid_cache: dict[int, bool] = {}
        self._path_cache: dict[str, bool] = {}
        self._commit_cache: dict[str, bool] = {}
        self._roots = [r for r in GATE_ROOTS if pathlib.Path(r).exists()]

    def report_id(self, rid: int) -> bool:
        if rid in self._rid_cache:
            return self._rid_cache[rid]
        s, _ = api("GET", f"/tensor/reports/{rid}", token=self.token)
        ok = s == 200
        self._rid_cache[rid] = ok
        return ok

    def path(self, p: str) -> bool:
        if p in self._path_cache:
            return self._path_cache[p]
        ok = any((pathlib.Path(r) / p).exists() for r in self._roots)
        self._path_cache[p] = ok
        return ok

    def commit(self, c: str) -> bool:
        if c in self._commit_cache:
            return self._commit_cache[c]
        ok = False
        for r in self._roots:
            try:
                rc = subprocess.run(
                    ["git", "-C", r, "cat-file", "-e", c + "^{commit}"],
                    capture_output=True, timeout=10)
                if rc.returncode == 0:
                    ok = True
                    break
            except Exception:
                continue
        self._commit_cache[c] = ok
        return ok


def fetch_pending_batch(limit: int) -> list[dict]:
    """存量按 mission_id 倒序（owner 令）；NULL 最后。"""
    sql = (
        "SELECT id, coalesce(title,''), coalesce(markdown,''), "
        "coalesce(figures,'[]'::jsonb)::text, mission_id "
        "FROM tensor_reports WHERE qa_status='pending' "
        "ORDER BY mission_id DESC NULLS LAST, id DESC LIMIT " + str(int(limit))
    )
    out = []
    for line in qrows(sql):
        try:
            rid, title, md, figs, mid = line.split("|", 4)
        except ValueError:
            continue
        out.append({
            "id": int(rid),
            "title": title,
            "markdown": md,
            "figures": json.loads(figs) if figs else [],
            "mission_id": int(mid) if mid and mid != "NULL" else None,
        })
    return out


def bulk_update(rows: list[dict]) -> None:
    """批量落库：qa_status/qa_rejection_reason/grade/grade_reason/qa_machine_*。"""
    if not rows:
        return
    stmts = []
    for r in rows:
        reason = r["qa_rejection_reason"]
        reason_sql = "NULL" if reason is None else "'" + reason.replace("'", "''") + "'"
        audit = json.dumps(r["audit"], ensure_ascii=False).replace("'", "''")
        stmts.append(
            f"UPDATE tensor_reports SET qa_status='{r['qa_status']}', "
            f"qa_rejection_reason={reason_sql}, qa_checked_at=now(), "
            f"grade='{r['grade']}', grade_reason='{r['grade_reason'].replace(chr(39), chr(39)*2)}', "
            f"qa_machine_tier='qa_gate_v2', qa_machine_audit='{audit}'::jsonb, "
            f"qa_machine_at=now(), qa_audit_source='qa_gate_v2' WHERE id={r['id']}"
        )
    for i in range(0, len(stmts), 200):
        q("; ".join(stmts[i:i + 200]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只跑一批就退出（冒烟用）")
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args()

    log("报告 QA 常设门启动（四查+S1/S2/S3 分级+mission_id 倒序消化）")
    while True:
        try:
            tok = login()
            gate = load_gate_module()
            ver = Verifier(tok)
            batch = fetch_pending_batch(args.batch)
            if not batch:
                log("pending 已清空，休眠")
                if args.once:
                    return
                time.sleep(POLL_S)
                continue
            verdicts: list[dict] = []
            grades: dict[str, int] = {}
            for rep in batch:
                try:
                    v = gate.qa_gate_v2(rep, ver)
                except Exception as e:
                    log(f"report {rep['id']} 门异常: {type(e).__name__}: {e}")
                    continue
                v["id"] = rep["id"]
                verdicts.append(v)
                grades[v["grade"]] = grades.get(v["grade"], 0) + 1
            bulk_update(verdicts)
            pending = int(q("SELECT count(*) FROM tensor_reports WHERE qa_status='pending'") or 0)
            total = int(q("SELECT count(*) FROM tensor_reports") or 0)
            log(f"批 {len(verdicts)}/{len(batch)} 分级={grades} pending={pending}/{total} "
                f"({pending / max(total, 1):.1%})")
            if args.once:
                return
            time.sleep(POLL_S)
        except Exception as e:
            log(f"异常: {type(e).__name__}: {e}")
            time.sleep(POLL_S)


if __name__ == "__main__":
    main()
