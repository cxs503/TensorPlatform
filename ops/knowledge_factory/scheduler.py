#!/usr/bin/env python3
"""Knowledge Factory 试点调度器（daemon）：闲位驱动派单 + 双门校验 + 独立审核 + draft 入库 + 看板。

机制全部复用平台既有面：Mission API 派单（POST /tensor/missions）、Agent 报告信道（tensor_reports）、
tensor_dev_missions 执行态、metered_usage 成本计量、tk_* 表存产线状态。不另建平台。

单实例：flock（参照 case_feeder 闲位驱动模式），可被 tp_autoheal 纳管（见 autoheal_entry.json）。

用法：
  python3 -m ops.knowledge_factory.scheduler --once            # 跑一轮
  python3 -m ops.knowledge_factory.scheduler --discover-only   # 只做发现/分层落表
  python3 -m ops.knowledge_factory.scheduler --daemon --interval 180
"""
import argparse
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.request

if __package__ in (None, ""):  # 允许直接脚本执行
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "knowledge_factory"

from . import contract as kc          # noqa: E402
from . import dashboard, discover, gates  # noqa: E402
from .store import Store, lit         # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_DIR = os.path.join(REPO_ROOT, "runs", "knowledge_factory")
AGENT_ENV = os.getenv("KF_AGENT_ENV", "/root/tp-agent/agent.env")
DEFAULT_LOCK = os.getenv("KF_LOCK", "/root/tp-agent/kf_scheduler.lock")


def _env_file(path=AGENT_ENV):
    env = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


class Api:
    """Mission API 客户端（登录态复用 agent.env 的机器账号）。"""

    def __init__(self, base=None, user=None, password=None):
        env = _env_file()
        self.base = (base or os.getenv("TENSOR_API_BASE") or env.get("TENSOR_API_BASE")
                     or "http://127.0.0.1:8010/api/v1").rstrip("/")
        self.user = user or env.get("TENSOR_AGENT_USER")
        self.password = password or env.get("TENSOR_AGENT_PASS")
        self.token = None

    def _req(self, method, path, payload=None):
        req = urllib.request.Request(self.base + path, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            with urllib.request.urlopen(req, data=data, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except Exception:
                body = {}
            return e.code, body
        except urllib.error.URLError as e:
            return 0, {"error": str(e)}

    def login(self):
        c, d = self._req("POST", "/auth/login",
                         {"username": self.user, "password": self.password})
        if c == 200:
            self.token = d.get("access_token") or d.get("token")
        return self.token

    def create_mission(self, goal, repo, task_key, budget):
        if not self.token and not self.login():
            return 0, {"error": "login_failed"}
        return self._req("POST", "/tensor/missions",
                         {"goal": goal, "repo": repo, "runner": "native",
                          "origin": "agent", "task_key": task_key[:60], "budget": budget})


class Scheduler:
    def __init__(self, store=None, cfg=None, api=None, runs_dir=RUNS_DIR, verbose=True):
        self.store = store or Store()
        self.cfg = cfg or kc.load_config()
        self.contracts = kc.load_contracts(self.cfg)
        self.api = api if api is not None else Api()
        self.runs_dir = runs_dir
        self.verbose = verbose
        os.makedirs(self.runs_dir, exist_ok=True)
        self.log_path = os.path.join(self.runs_dir, "scheduler.log")
        self.enabled = set(kc.enabled_task_types(self.contracts))

    # ---------------- 基础 ----------------
    def log(self, msg):
        line = time.strftime("[%m-%d %H:%M:%S] ") + msg
        if self.verbose:
            print(line, flush=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def idle_workers(self):
        total = int(self.store.scalar("SELECT count(*) FROM factory_resident_workers", default=0))
        busy = int(self.store.scalar(
            "SELECT count(*) FROM tensor_dev_missions WHERE status IN ('claimed','running')",
            default=0))
        return max(0, total - busy), total, busy

    def inflight(self, task_type):
        return int(self.store.scalar(
            "SELECT count(*) FROM tk_work_packages WHERE task_type = %s "
            "AND status = 'dispatched'", (task_type,), default=0))

    # ---------------- ① Discover ----------------
    def discover(self, batch_size=None, manifest_path=None, persist=True):
        picked, report = discover.discover(self.store, self.cfg, batch_size, manifest_path)
        self.log(f"discover: mode={report['mode']} selected={report['selected']} "
                 f"strata={report.get('strata_covered')}/{report.get('strata_total')} "
                 f"unmatched={report.get('unmatched')}")
        if persist:
            created = 0
            for p in picked:
                if self.ensure_package(p["report_id"], self.cfg["lanes"]["C"]["stages"][0],
                                       lane="C", stratum=p["stratum"], create_only=True):
                    created += 1
            report["packages_created"] = created
            report["lane_split"] = self.rebalance_lanes()
        with open(os.path.join(self.runs_dir, "discovery.json"), "w", encoding="utf-8") as f:
            json.dump({**report, "picked": picked[:400]}, f, ensure_ascii=False, indent=1)
        return report

    def lane_for(self, index):
        """发现序 → 泳道（确定性，可复算）。"""
        split = self.cfg["pilot"].get("lane_split") or {"C": 1.0}
        x = index % 100
        acc = 0.0
        for lane in ("C", "B", "A"):
            acc += float(split.get(lane, 0.0)) * 100
            if x < acc:
                return lane
        return "C"

    def rebalance_lanes(self):
        """把 pending 包的泳道按 lane_split 摆正（在飞/已派不动，避免中途改契约）。"""
        rows = self.store.q("SELECT package_id FROM tk_work_packages "
                            "WHERE status = 'pending' ORDER BY report_id")
        moved = 0
        for i, (pid,) in enumerate(rows):
            lane = self.lane_for(i)
            self.store.execute("UPDATE tk_work_packages SET lane=%s, updated_at=now() "
                               "WHERE package_id=%s AND lane <> %s", (lane, pid, lane))
            moved += 1
        counts = self.store.q("SELECT lane, count(*) FROM tk_work_packages "
                              "WHERE status='pending' GROUP BY 1 ORDER BY 1")
        self.log("lane 摆正 " + " ".join(f"{r[0]}:{r[1]}" for r in counts))
        return dict(counts)

    def ensure_package(self, report_id, task_type, lane="C", stratum=None,
                       create_only=False, extra=None):
        pid = kc.package_id(report_id, task_type)
        if self.store.package(pid):
            return False
        tpl = self.contracts.get(task_type, {})
        status = "pending" if task_type in self.enabled else "blocked"
        fail = None if task_type in self.enabled else (
            "contract_pending:" + str(tpl.get("blocked_by") or "spec"))
        self.store.upsert_package({
            "package_id": pid, "report_id": int(report_id), "task_type": task_type,
            "lane": lane, "status": status,
            "skill_id": self.cfg["task_types"][task_type]["skill"],
            "stratum": stratum, "attempt": 0, "gates": extra or {},
            "cost": {}, "contract": None, "fail_reason": fail})
        return True

    # ---------------- ② Dispatch ----------------
    def dispatch(self, limit=None, lane=None):
        d = self.cfg["dispatch"]
        idle, total, busy = self.idle_workers()
        cap = min(d["cap_max"], idle // d["cap_divisor"])
        if idle < d["idle_floor"] or cap <= 0:
            self.log(f"dispatch: 闲位 {idle} < 地板 {d['idle_floor']}，本轮不派")
            return 0
        if limit is not None:          # 0 = 本轮不派（踩过：写成 if limit 时 0 被当无限制）
            cap = min(cap, int(limit))
        prio = {t: i for i, t in enumerate(d["priority_order"])}
        cands = self.store.packages(status="rework") + self.store.packages(status="pending")
        if lane:
            cands = [c for c in cands if c["lane"] == lane]
        cands.sort(key=lambda p: (prio.get(p["task_type"], 99), p["report_id"]))
        sent = 0
        for p in cands:
            if sent >= cap:
                break
            if p["task_type"] not in self.enabled:
                continue
            if self.inflight(p["task_type"]) >= d["max_in_flight_per_stage"]:
                continue
            if self._dispatch_one(p):
                sent += 1
        self.log(f"dispatch: 闲位 {idle}/{total}（在飞 {busy}）帽 {cap} → 派 {sent}")
        return sent

    def _dispatch_one(self, p):
        report = self.store.report(p["report_id"])
        if not report:
            self.store.set_status(p["package_id"], "failed", fail_reason="report_missing")
            return False
        lane_cfg = self.cfg["lanes"][p["lane"]]
        pkg = kc.build_package(report, p["task_type"], p["lane"], self.cfg, self.contracts,
                               mission_id=None, stratum=p["stratum"])
        prev = self.store.package(p["package_id"]) or {}
        if prev.get("contract") and prev["contract"].get("producer_package_id"):
            pkg["notes"] = (pkg["notes"] + " ｜审核对象=" +
                            prev["contract"]["producer_package_id"])
        goal = kc.render_goal(pkg, self.cfg)
        if p["attempt"] > 0 and prev.get("gates"):
            goal += ("\n\n=== 返工指令（上一轮门失败明细）===\n"
                     + json.dumps(prev["gates"].get("failed", []), ensure_ascii=False)
                     + "\n逐条修掉后重交；同一门不得二次失败（帽 %d 次，再败转 failed）。"
                     % self.cfg["rework"]["max_attempts"])
        repo = self.contracts[p["task_type"]]["delivery"]["repo"]
        status, data = self.api.create_mission(
            goal, repo, f"wp-{p['report_id']}-{p['task_type']}",
            self.cfg["dispatch"]["mission_budget"])
        if status != 200 or not data.get("id"):
            self.log(f"dispatch 失败 {p['package_id']}: {status} {str(data)[:120]}")
            return False
        self.store.execute(
            "UPDATE tk_work_packages SET status='dispatched', mission_id=%s, contract=%s, "
            "updated_at=now() WHERE package_id=%s",
            (int(data["id"]), pkg, p["package_id"]))
        self.log(f"派单 {p['package_id']} → m{data['id']}（lane {p['lane']}，"
                 f"{'返工' if p['attempt'] else '首派'}）")
        return True

    # ---------------- ③ Validate / ④ Review / ⑤ Ingest ----------------
    def collect(self):
        handled = 0
        for p in self.store.packages(status="dispatched"):
            res = self.store.find_result_report(p["package_id"])
            if not res:
                continue
            prev = self.store.package(p["package_id"]) or {}
            if prev.get("result_report_id") == res["id"]:
                continue
            if p["task_type"] == "04":
                handled += self._collect_review(p, res)
            else:
                handled += self._collect_work(p, res)
        if handled:
            self.log(f"collect: 处理 {handled} 个回执")
        return handled

    def _record_cost(self, package_id, mission_id):
        if not mission_id:
            return
        mt = self.store.mission_metering(mission_id)
        ms = self.store.mission(mission_id) or {}
        self.store.execute(
            "UPDATE tk_work_packages SET cost=%s, updated_at=now() WHERE package_id=%s",
            ({"tokens": mt["tokens"], "tool_calls": mt["tool_calls"],
              "compute_seconds": ms.get("wall_seconds", 0) or mt["wall_seconds"],
              "metered": mt["metered"]}, package_id))

    def _collect_work(self, p, res):
        prev = self.store.package(p["package_id"]) or {}
        report = self.store.report(p["report_id"]) or {}
        obj, err = gates.extract_payload(res["markdown"])
        sg, sok = gates.structure_gate(obj, pkg=prev)
        eg, eok, stats = gates.evidence_gate(
            obj, report.get("markdown", ""), self.store.seen_claim_ids(),
            self.cfg["gates"]["min_quote_chars"], expected_report_id=p["report_id"])
        all_gates = gates.summarize(sg + eg, sok and eok)
        all_gates.update(stats)
        all_gates["result_report_id"] = res["id"]
        all_gates["payload_error"] = err
        self._record_cost(p["package_id"], p["mission_id"])
        if all_gates["ok"]:
            self.store.execute(
                "UPDATE tk_work_packages SET status='validated', gates=%s, gates_ok=TRUE, "
                "result_report_id=%s, updated_at=now() WHERE package_id=%s",
                (all_gates, res["id"], p["package_id"]))
            self.log(f"双门通过 {p['package_id']}（claims={stats['n_claims']}，"
                     f"定位 {stats['locators_resolved']}/{stats['locators_checked']}）")
            self._after_validate(p)
        else:
            attempt = int(prev.get("attempt") or 0) + 1
            status = "rework" if attempt < self.cfg["rework"]["max_attempts"] else "failed"
            self.store.execute(
                "UPDATE tk_work_packages SET status=%s, gates=%s, gates_ok=FALSE, attempt=%s, "
                "result_report_id=%s, fail_reason=%s, updated_at=now() WHERE package_id=%s",
                (status, all_gates, attempt, res["id"],
                 "gates_failed:" + ",".join(all_gates["failed"]), p["package_id"]))
            self.log(f"门失败 {p['package_id']}（{','.join(all_gates['failed'])}）→ {status}"
                     f"（attempt {attempt}）")
        return 1

    def _after_validate(self, p):
        lane_cfg = self.cfg["lanes"][p["lane"]]
        report = self.store.report(p["report_id"]) or {}
        if lane_cfg.get("independent_review"):
            rid = kc.package_id(p["report_id"], "04")
            prev = self.store.package(p["package_id"]) or {}
            self.ensure_package(p["report_id"], "04", lane=p["lane"], stratum=p["stratum"],
                                extra={"producer_package_id": p["package_id"]})
            self.store.execute(
                "UPDATE tk_work_packages SET contract=%s, status=%s, updated_at=now() "
                "WHERE package_id=%s",
                ({"producer_package_id": p["package_id"],
                  "producer_mission_id": prev.get("mission_id")},
                 "pending" if "04" in self.enabled else "blocked", rid))
            self.store.set_status(p["package_id"], "review")
            self.log(f"→ 派独立审核单 {rid}（审核者上下文=原文+KO，不看生产者解释）")
            return
        self.ingest(p, report)

    def _collect_review(self, p, res):
        prev = self.store.package(p["package_id"]) or {}
        obj, err = gates.extract_payload(res["markdown"])
        producer_pid = (prev.get("contract") or {}).get("producer_package_id")
        producer_mid = (prev.get("contract") or {}).get("producer_mission_id")
        rg, rok, verdict = gates.review_gate(obj, prev, producer_mid, p["mission_id"])
        summary = gates.summarize(rg, rok)
        summary["review_verdict"] = verdict if rok else None
        summary["result_report_id"] = res["id"]
        summary["payload_error"] = err
        self._record_cost(p["package_id"], p["mission_id"])
        self.store.execute(
            "UPDATE tk_work_packages SET gates=%s, gates_ok=%s, result_report_id=%s, "
            "status=%s, updated_at=now() WHERE package_id=%s",
            (summary, rok, res["id"], "ingested" if rok and verdict == "pass" else
             ("validated" if rok else "failed"), p["package_id"]))
        parent = self.store.package(producer_pid) if producer_pid else None
        if not parent:
            return 1
        parent_gates = parent.get("gates") or {}
        parent_gates["review_verdict"] = verdict
        parent_gates["reviewer_mission_id"] = p["mission_id"]
        self.store.execute("UPDATE tk_work_packages SET gates=%s, updated_at=now() "
                           "WHERE package_id=%s", (parent_gates, parent_pid))
        if not rok:
            self.store.set_status(parent_pid, "rework", attempt=parent["attempt"] + 1,
                                  fail_reason="review_gate_failed")
        elif verdict == "pass":
            self.log(f"审核通过 {producer_pid}（reviewer m{p['mission_id']}）→ 入库")
            self.ingest(parent, self.store.report(parent["report_id"]) or {},
                        review_status="passed")
        elif verdict == "needs-fix":
            attempt = parent["attempt"] + 1
            status = "rework" if attempt < self.cfg["rework"]["max_attempts"] else "failed"
            self.store.set_status(parent_pid, status, attempt=attempt,
                                  fail_reason="review_needs_fix")
            self.log(f"审核 needs-fix {producer_pid} → {status}（attempt {attempt}）")
        else:
            self.store.set_status(parent_pid, "failed", fail_reason="review_fail")
            self.log(f"审核 fail {producer_pid} → failed（留痕不吞）")
        return 1

    def ingest(self, p, report, review_status=None):
        prev = self.store.package(p["package_id"]) or {}
        res = self.store.find_result_report(p["package_id"]) or {}
        obj, _ = gates.extract_payload(res.get("markdown", ""))
        claims = (obj or {}).get("claims") or []
        lane_cfg = self.cfg["lanes"][p["lane"]]
        ko_review = review_status or lane_cfg["ingest_review_status"]
        emitted = 0
        for c in claims:
            ko_id = f"{p['package_id']}:{c.get('claim_id')}"
            if self.store.draft_exists(ko_id):
                continue
            self.store.insert_draft({
                "ko_id": ko_id, "package_id": p["package_id"], "report_id": p["report_id"],
                "lane": p["lane"], "claim_text": str(c.get("text", ""))[:4000],
                "payload": {"claim": c, "provenance": (obj or {}).get("provenance", {}),
                            "outputs": (obj or {}).get("outputs", {}),
                            "package_id": p["package_id"]},
                "ko_review_status": ko_review,
                "science_verification_status": "not_verified", "mission_status": "done"})
            emitted += 1
        g = prev.get("gates") or {}
        g["ingest"] = {"claims": len(claims), "drafts_emitted": emitted,
                       "zero_claims": len(claims) == 0, "ko_review_status": ko_review}
        self.store.execute(
            "UPDATE tk_work_packages SET status='ingested', gates=%s, updated_at=now() "
            "WHERE package_id=%s", (g, p["package_id"]))
        if len(claims) == 0:
            self.log(f"入库 {p['package_id']}：零 claim（合法完整结果，不造知识）")
        else:
            self.log(f"入库 {p['package_id']}：{emitted} 条 draft（verification_status="
                     f"unverified，ko_review={ko_review}）")
        self._advance(p)

    def _advance(self, p):
        lane_cfg = self.cfg["lanes"][p["lane"]]
        stages = lane_cfg["stages"]
        if p["task_type"] not in stages:
            return
        i = stages.index(p["task_type"])
        if i + 1 >= len(stages):
            return
        nxt = stages[i + 1]
        if nxt == "04":
            return  # 审核单由 _after_validate 建
        if self.ensure_package(p["report_id"], nxt, lane=p["lane"], stratum=p["stratum"]):
            state = "pending" if nxt in self.enabled else "blocked"
            self.log(f"→ 生成下游工包 {kc.package_id(p['report_id'], nxt)}（{state}）")

    # ---------------- ⑥ 看板 ----------------
    def snapshot(self, round_label=None):
        m = dashboard.collect_metrics(self.store)
        round_label = round_label or time.strftime("%Y-%m-%dT%H:%M:%S")
        self.store.snapshot(round_label, m)
        self.log("看板 " + dashboard.one_line(m))
        with open(os.path.join(self.runs_dir, "dashboard_latest.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"round": round_label, "metrics": m,
                       "rendered": dashboard.render(m)}, f, ensure_ascii=False, indent=1)
        return m

    def cycle(self, do_discover=False, limit=None, lane=None):
        if do_discover:
            self.discover()
        self.collect()
        self.dispatch(limit=limit, lane=lane)
        return self.snapshot()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Knowledge Factory 试点调度器")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--interval", type=int, default=180)
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--discover-only", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="本轮最多派几单")
    ap.add_argument("--lane", default=None, help="只派指定泳道")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--lock", default=DEFAULT_LOCK)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    os.makedirs(os.path.dirname(a.lock), exist_ok=True)
    lock = open(a.lock, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("另一实例持锁，退出", flush=True)
        return 2

    store = Store()
    store.ensure_schema()
    api = None
    if a.dry_run:
        class _NoApi:
            def create_mission(self, *args, **kw):
                return 0, {"error": "dry_run"}
        api = _NoApi()
    sch = Scheduler(store=store, api=api)
    sch.log("=== kf_scheduler 启动（单实例 flock；零领域语义；契约工序 "
            f"{sorted(sch.enabled)}）===")

    if a.discover_only:
        sch.discover(batch_size=a.batch_size, manifest_path=a.manifest)
        sch.snapshot("discover-only")
        return 0
    if a.once or a.dry_run:
        sch.cycle(do_discover=a.discover, limit=a.limit, lane=a.lane)
        if a.discover and a.batch_size:
            sch.discover(batch_size=a.batch_size, manifest_path=a.manifest)
        return 0
    while True:
        try:
            sch.cycle(limit=a.limit, lane=a.lane)
        except Exception as e:  # 单轮异常不停机
            sch.log(f"cycle error: {type(e).__name__} {e}")
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())