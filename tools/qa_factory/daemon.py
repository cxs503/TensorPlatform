#!/usr/bin/env python3
"""QA 产线 daemon —— 程序化三查扫描 + 机器授权清账（flock 单实例 + 心跳，autoheal 可纳管）。

一个循环：
  1. 取未分档的 pending 报告（`qa_machine_tier IS NULL`，存量优先 `created_at ASC`）一批 batch 篇；
  2. 三查分档（tools/qa_factory/gate.py），写 `qa_machine_*` 审计列（不改 qa_status）；
  3. 若存在新鲜且 gate_passed=true 的口径一致性裁决 → machine-pass 批清 qa_status='passed'
     （带 qa_audit_source='machine' 标记）；否则**不清账**（机器门未投用时不许自己给自己授权）；
  4. 心跳落到 state 文件 + 日志，供 autoheal/看门狗判活。

用法:
  python3 tools/qa_factory/daemon.py --once                 # 跑一批（CI/试点用）
  python3 tools/qa_factory/daemon.py --once --batches 20    # 跑 20 批后退出
  python3 tools/qa_factory/daemon.py                        # 常驻（autoheal 拉起）
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.qa_factory import consistency as cons  # noqa: E402
from tools.qa_factory.gate import EvidenceRoots, gate_report, summarize  # noqa: E402
from tools.qa_factory.source import DB  # noqa: E402

WORK_ROOT = Path(os.environ.get("TP_WORK_ROOT", "/root/tp-agent/work"))
STATE_DIR = Path(os.environ.get("QA_FACTORY_STATE", "/var/lib/tp-qa-factory"))
LOG_PATH = Path(os.environ.get("QA_FACTORY_LOG", "/var/log/tp-qa-factory.log"))
DEFAULT_CONSISTENCY = Path("artifacts/qa_factory/round1/consistency.json")
REQUIRED_COLUMNS = ("qa_machine_tier", "qa_machine_audit", "qa_machine_at", "qa_audit_source")


def log(msg: str, path: Path = LOG_PATH) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        sys.stderr.write(line)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_schema(db: DB) -> Tuple[bool, List[str]]:
    cp = __import__("subprocess").run(
        ["psql"] + (["-h", db.host] if db.host else []) + ["-U", db.user, "-d", db.db, "-tAc",
         "SELECT column_name FROM information_schema.columns "
         "WHERE table_name='tensor_reports'"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "PGPASSWORD": db.password or os.environ.get("PGPASSWORD", "")})
    cols = {c.strip() for c in cp.stdout.split()}
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    return (not missing), missing


def load_consistency_gate(path: Path, max_age_s: int, now_ts: Optional[float] = None) -> Dict[str, Any]:
    """返回 {available, fresh, gate_passed, ref, age_s}——清账授权判定。"""
    p = Path(path)
    if not p.exists():
        return {"available": False, "fresh": False, "gate_passed": False, "ref": "", "age_s": None}
    payload = json.loads(p.read_text(encoding="utf-8"))
    age = (now_ts or time.time()) - p.stat().st_mtime
    return {"available": True, "fresh": age <= max_age_s,
            "gate_passed": bool(payload.get("gate_passed")),
            "ref": f"{p.name}:{payload.get('gate_reason', '')}",
            "age_s": int(age)}


def make_roots(db: DB, mission_id: Optional[int], cache: Dict[str, Any]) -> EvidenceRoots:
    work_dir = (WORK_ROOT / f"m{mission_id}") if mission_id else None
    return EvidenceRoots(work_dir=work_dir,
                         report_exists=lambda rid: rid in cache.get("reports", set()),
                         mission_exists=lambda mid: mid in cache.get("missions", set()))


def run_batch(db: DB, *, batch: int, cache: Dict[str, Any], clear_batch: int,
              gate_info: Dict[str, Any], art_dir: Path, batch_index: int) -> Dict[str, Any]:
    rows = db.load_reports(where="r.qa_status = 'pending' AND r.qa_machine_tier IS NULL",
                           order="r.created_at ASC, r.id ASC", limit=batch)
    if not rows:
        return {"scanned": 0, "tiers": {}, "tiered": 0, "cleared": 0, "done": True}
    verdicts = []
    for r in rows:
        roots = make_roots(db, r["mission_id"], cache)
        v = gate_report(report_id=r["id"], markdown=r["markdown"] or "", title=r["title"] or "",
                        scenario_key=r["scenario_key"] or "", figures=r["figures"], roots=roots)
        verdicts.append(v.to_dict())
    stats = summarize([type("V", (), {"tier": v["tier"], "checks": v["checks"],
                                      "detail": v["detail"]})() for v in verdicts])
    written = db.record_tiers(verdicts)
    cleared = 0
    cleared_ids: List[int] = []
    if gate_info["available"] and gate_info["fresh"] and gate_info["gate_passed"] and clear_batch > 0:
        cleared, cleared_ids = db.clear_machine_pass(clear_batch, gate_ref=gate_info["ref"])
    elif clear_batch > 0:
        log(f"清账跳过：一致性门不可用/不新鲜/未通过 {gate_info}")
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / f"batch_{batch_index:04d}.json").write_text(json.dumps({
        "batch_index": batch_index, "generated_at_utc": utc_now(), "stats": stats,
        "cleared": cleared, "cleared_ids": cleared_ids, "gate": gate_info,
        "verdicts": verdicts}, ensure_ascii=False), encoding="utf-8")
    return {"scanned": len(rows), "tiers": stats["tiers"], "tiered": written,
            "cleared": cleared, "cleared_ids": cleared_ids, "done": False}


def cycle(db: DB, *, batch: int, clear_batch: int, consistency_path: Path, max_age_s: int,
          art_dir: Path, state: Dict[str, Any]) -> Dict[str, Any]:
    ok, missing = check_schema(db)
    if not ok:
        raise RuntimeError(f"schema 缺列 {missing}：先跑 backend/migrations/122_report_qa_machine.sql")
    if not state.get("id_cache") or state.get("cache_age_cycle", 0) >= 50:
        state["id_cache"] = {"reports": sorted(db.id_set("tensor_reports")),
                             "missions": sorted(db.id_set("tensor_dev_missions"))}
        state["cache_age_cycle"] = 0
    else:
        state["cache_age_cycle"] = state.get("cache_age_cycle", 0) + 1
    cache = {"reports": set(state["id_cache"]["reports"]),
             "missions": set(state["id_cache"]["missions"])}
    gate_info = load_consistency_gate(consistency_path, max_age_s)
    idx = state.get("batch_index", 0)
    res = run_batch(db, batch=batch, cache=cache, clear_batch=clear_batch, gate_info=gate_info,
                    art_dir=art_dir, batch_index=idx)
    state["batch_index"] = idx + 1
    state["cycles"] = state.get("cycles", 0) + 1
    state["last_cycle_utc"] = utc_now()
    state["last_result"] = {k: v for k, v in res.items() if k != "cleared_ids"}
    state["cleared_total"] = state.get("cleared_total", 0) + res.get("cleared", 0)
    state["tiered_total"] = state.get("tiered_total", 0) + res.get("tiered", 0)
    state["gate"] = gate_info
    log(f"cycle={state['cycles']} scanned={res['scanned']} tiers={res.get('tiers')} "
        f"cleared={res.get('cleared')} gate={gate_info['ref'] or 'none'} "
        f"tiered_total={state['tiered_total']} cleared_total={state['cleared_total']}")
    return res


def main(argv: Optional[List[str]] = None) -> int:
    global LOG_PATH
    ap = argparse.ArgumentParser(description="QA 产线 daemon（三查分档 + 机器授权清账）")
    ap.add_argument("--once", action="store_true", help="跑一轮就退出")
    ap.add_argument("--batches", type=int, default=0, help="最多跑多少批（0=不限）")
    ap.add_argument("--batch", type=int, default=500, help="每批扫描篇数")
    ap.add_argument("--clear-batch", type=int, default=2000, help="每批最多清账篇数（0=不清账）")
    ap.add_argument("--poll", type=int, default=120, help="常驻模式轮询秒数")
    ap.add_argument("--consistency", type=Path, default=DEFAULT_CONSISTENCY)
    ap.add_argument("--consistency-max-age", type=int, default=86400, help="裁决新鲜度上限（秒）")
    ap.add_argument("--art-dir", type=Path, default=Path("artifacts/qa_factory/round1/batches"))
    ap.add_argument("--state-dir", type=Path, default=STATE_DIR)
    ap.add_argument("--log", type=Path, default=LOG_PATH)
    ap.add_argument("--host", default=os.environ.get("TP_PG_HOST", ""))
    ap.add_argument("--user", default=os.environ.get("TP_PG_USER", "postgres"))
    ap.add_argument("--dbname", default=os.environ.get("TP_PG_DB", "tensorplatform"))
    ap.add_argument("--password", default=os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD"))
    args = ap.parse_args(argv)

    LOG_PATH = args.log
    args.state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.state_dir / "qa_factory.lock"
    state_path = args.state_dir / "state.json"
    hb_path = args.state_dir / "heartbeat.json"

    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("另一个实例在跑（flock 持有），本实例退出")
        return 0

    def _stop(signum, frame):  # noqa: ARG001
        log(f"收到信号 {signum}，优雅退出")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    state: Dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    db = DB(host=args.host, user=args.user, db=args.dbname, password=args.password)
    log(f"启动 once={args.once} batch={args.batch} clear_batch={args.clear_batch} "
        f"consistency={args.consistency}")
    ran = 0
    while True:
        try:
            res = cycle(db, batch=args.batch, clear_batch=args.clear_batch,
                        consistency_path=args.consistency, max_age_s=args.consistency_max_age,
                        art_dir=args.art_dir, state=state)
            ran += 1
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            hb_path.write_text(json.dumps({"alive_utc": utc_now(), "cycles": state.get("cycles", 0),
                                           "cleared_total": state.get("cleared_total", 0),
                                           "tiered_total": state.get("tiered_total", 0),
                                           "gate_passed": state.get("gate", {}).get("gate_passed")},
                                          ensure_ascii=False), encoding="utf-8")
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 —— 不吞：记日志后按 --once 语义决定退出码
            log(f"cycle 失败：{type(e).__name__}: {e}")
            if args.once or (args.batches and ran >= args.batches):
                return 1
            time.sleep(args.poll)
            continue
        if res.get("done"):
            log("pending 且未分档的报告已清空，本轮结束")
            return 0
        if args.once and (args.batches == 0 or ran >= args.batches):
            return 0
        if args.batches and ran >= args.batches:
            return 0
        if not args.once:
            time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())