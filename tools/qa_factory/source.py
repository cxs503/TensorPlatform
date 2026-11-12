#!/usr/bin/env python3
"""QA 产线 · 数据面（PG 读取与写账）。

约定：只碰 `public.tensor_reports`；扫描只读（SELECT），写账只写 qa_machine_* / qa_audit_source
与 qa_status 清账，且每一批写入前后都有计数回执（可核对、可回滚：旧值全在审计 JSON 里）。
psql 走 `-tA -F \\x1f -R \\x1e` 行协议，避免 markdown 里的换行/竖线破坏解析。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

FIELD_SEP = "\x1f"
REC_SEP = "\x1e"

_SELECT_COLS = ("r.id, coalesce(r.mission_id,-1), r.title, coalesce(r.scenario_key,''), "
                "jsonb_array_length(coalesce(r.figures,'[]'::jsonb)), "
                "to_char(r.created_at,'YYYY-MM-DD\"T\"HH24:MI:SSOF'), r.markdown")


def _psql(args: Sequence[str], *, host: str, user: str, db: str,
          password: Optional[str] = None, timeout: int = 900) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin",
           "PGPASSWORD": password if password is not None else os.environ.get("PGPASSWORD", "")}
    cmd = ["psql"] + (["-h", host] if host else []) + ["-U", user, "-d", db,
                                                       "-v", "ON_ERROR_STOP=1"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


class DB:
    def __init__(self, host: str = os.environ.get("TP_PG_HOST", ""),
                 user: str = os.environ.get("TP_PG_USER", "postgres"),
                 db: str = os.environ.get("TP_PG_DB", "tensorplatform"),
                 password: Optional[str] = None):
        # host='' → 走本地 socket + peer 认证（脱离密码环境也能跑；CI 里可显式给 host/password）
        self.host, self.user, self.db, self.password = host, user, db, password

    # ---------------------------------------------------------------- read
    def load_reports(self, *, where: str = "TRUE", order: str = "r.created_at ASC",
                     limit: int = 0) -> List[Dict[str, Any]]:
        sql = (f"SELECT {_SELECT_COLS} FROM tensor_reports r WHERE {where} "
               f"ORDER BY {order}" + (f" LIMIT {int(limit)}" if limit else ""))
        cp = _psql(["-tA", "-F", FIELD_SEP, "-R", REC_SEP, "-c", sql], host=self.host,
                   user=self.user, db=self.db, password=self.password)
        if cp.returncode != 0:
            raise RuntimeError(f"psql load failed: {cp.stderr[:400]}")
        rows: List[Dict[str, Any]] = []
        for rec in cp.stdout.split(REC_SEP):
            rec = rec.strip("\n")
            if not rec.strip():
                continue
            parts = rec.split(FIELD_SEP)
            if len(parts) < 7:
                raise RuntimeError(f"bad record ({len(parts)} fields): {rec[:120]!r}")
            rid, mid, title, scen, nfig, created, md = parts[0], parts[1], parts[2], parts[3], \
                parts[4], parts[5], FIELD_SEP.join(parts[6:])
            rows.append({"id": int(rid), "mission_id": int(mid) if int(mid) > 0 else None,
                         "title": title, "scenario_key": scen, "figures": int(nfig or 0),
                         "created_at": created, "markdown": md})
        return rows

    def load_pending(self, limit: int = 0) -> List[Dict[str, Any]]:
        return self.load_reports(where="r.qa_status = 'pending'", limit=limit)

    def id_set(self, table: str, column: str = "id", limit: int = 2_000_000) -> set:
        cp = _psql(["-tA", "-c", f"SELECT {column} FROM {table} LIMIT {int(limit)}"],
                   host=self.host, user=self.user, db=self.db, password=self.password)
        if cp.returncode != 0:
            raise RuntimeError(f"psql id_set failed: {cp.stderr[:300]}")
        return {int(x) for x in cp.stdout.split() if x.strip().lstrip("-").isdigit()}

    def counts(self) -> Dict[str, int]:
        cp = _psql(["-tA", "-F", "|", "-c",
                    "SELECT coalesce(qa_machine_tier,'(null)'), count(*) FROM tensor_reports "
                    "GROUP BY 1 ORDER BY 2 DESC"], host=self.host, user=self.user, db=self.db,
                   password=self.password)
        if cp.returncode != 0:
            raise RuntimeError(f"psql counts failed: {cp.stderr[:300]}")
        out = {}
        for line in cp.stdout.splitlines():
            if "|" in line:
                k, v = line.rsplit("|", 1)
                out[k.strip()] = int(v)
        return out

    # ---------------------------------------------------------------- write
    def record_tiers(self, verdicts: Iterable[Dict[str, Any]]) -> int:
        """批量写 qa_machine_tier / qa_machine_audit / qa_machine_at（不改 qa_status）。

        audit JSON 走 base64 过 COPY：text 格式会把反斜杠当转义符，JSON 里的转义引号会被吃掉。
        """
        import base64
        rows = [(int(v["report_id"]), v["tier"],
                 base64.b64encode(json.dumps(v, ensure_ascii=False, sort_keys=True).encode()).decode())
                for v in verdicts if v.get("report_id") is not None]
        if not rows:
            return 0
        fd, tsv = tempfile.mkstemp(suffix=".tsv")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for rid, tier, b64 in rows:
                fh.write(f"{rid}\t{tier}\t{b64}\n")
        script = f"""
CREATE TEMP TABLE _qa_tmp(id int PRIMARY KEY, tier varchar(20), audit_b64 text);
\\copy _qa_tmp FROM '{tsv}' WITH (FORMAT text)
UPDATE tensor_reports r SET qa_machine_tier = t.tier,
       qa_machine_audit = convert_from(decode(t.audit_b64, 'base64'), 'UTF8')::jsonb,
       qa_machine_at = now()
  FROM _qa_tmp t WHERE r.id = t.id;
"""
        script_path = Path(tsv + ".sql")
        script_path.write_text(script, encoding="utf-8")
        try:
            cp = _psql(["-q", "-f", str(script_path)], host=self.host, user=self.user,
                       db=self.db, password=self.password)
        finally:
            script_path.unlink(missing_ok=True)
            Path(tsv).unlink(missing_ok=True)
        if cp.returncode != 0:
            raise RuntimeError(f"psql record_tiers failed: {cp.stderr[:400]}")
        return len(rows)

    def clear_machine_pass(self, batch: int, *, gate_ref: str = "") -> Tuple[int, List[int]]:
        """machine-pass + pending → passed（带 machine_audit 标记），存量优先老报告。

        返回 (清账条数, 清账的 report_id 列表)。审计标记写 qa_audit_source='machine'，
        qa_rejection_reason 记为审计引用（便于前端/质检席区分人工审与机器审）。
        """
        note = f"machine_audit:pass gate={gate_ref or 'unknown'}"
        sql = f"""
WITH pick AS (
  SELECT id FROM tensor_reports
   WHERE qa_status = 'pending' AND qa_machine_tier = 'pass'
   ORDER BY created_at ASC, id ASC LIMIT {int(batch)}
)
UPDATE tensor_reports r SET qa_status = 'passed', qa_checked_at = now(),
       qa_audit_source = 'machine', qa_rejection_reason = '{note}'
  FROM pick WHERE r.id = pick.id
RETURNING r.id;
"""
        cp = _psql(["-tA", "-c", sql], host=self.host, user=self.user, db=self.db,
                   password=self.password)
        if cp.returncode != 0:
            raise RuntimeError(f"psql clear failed: {cp.stderr[:400]}")
        ids = [int(x) for x in cp.stdout.split() if x.strip().isdigit()]
        return len(ids), ids

    def pending_stratum(self, tier: str, limit: int = 50) -> List[int]:
        cp = _psql(["-tA", "-c",
                    f"SELECT id FROM tensor_reports WHERE qa_status='pending' "
                    f"AND qa_machine_tier = '{tier}' ORDER BY created_at ASC, id ASC LIMIT {int(limit)}"],
                   host=self.host, user=self.user, db=self.db, password=self.password)
        if cp.returncode != 0:
            raise RuntimeError(f"psql stratum failed: {cp.stderr[:300]}")
        return [int(x) for x in cp.stdout.split() if x.strip().isdigit()]

    def load_tiered(self, *, where: str = "TRUE", limit: int = 0) -> List[Dict[str, Any]]:
        """读回已分档的审计列（分档结果的持久化视图，供抽样/一致性与报告复核）。"""
        sql = ("SELECT r.id, r.qa_machine_tier, coalesce(r.qa_audit_source,''), "
               "coalesce(r.qa_machine_audit,'{}'::jsonb)::text FROM tensor_reports r "
               f"WHERE r.qa_machine_tier IS NOT NULL AND ({where}) ORDER BY r.id"
               + (f" LIMIT {int(limit)}" if limit else ""))
        cp = _psql(["-tA", "-F", FIELD_SEP, "-R", REC_SEP, "-c", sql], host=self.host,
                   user=self.user, db=self.db, password=self.password)
        if cp.returncode != 0:
            raise RuntimeError(f"psql load_tiered failed: {cp.stderr[:300]}")
        out: List[Dict[str, Any]] = []
        for rec in cp.stdout.split(REC_SEP):
            rec = rec.strip("\n")
            if not rec.strip():
                continue
            rid, tier, src, audit = rec.split(FIELD_SEP, 3)
            try:
                payload = json.loads(audit)
            except json.JSONDecodeError:
                payload = {}
            out.append({"report_id": int(rid), "tier": tier, "audit_source": src,
                        "checks": payload.get("checks", {}), "detail": payload.get("detail", {})})
        return out

    def fetch(self, ids: Sequence[int]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        idlist = ",".join(str(int(i)) for i in ids)
        return self.load_reports(where=f"r.id IN ({idlist})", order="r.created_at ASC")