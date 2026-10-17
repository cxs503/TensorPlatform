#!/usr/bin/env python3
"""Host executor daemon for the TensorPlatform research console.

Loop: claim -> run scenario stage script -> upload+register runs ->
complete -> request human approval (stage_promotion or stop_condition).

Protocol: POST /api/v1/tensor/executions{/claim,/start,/heartbeat,/complete}
Auth: real platform account (ENGINEER); re-login on 401.
Budget: wall-clock watchdog killpg(SIGKILL) -> failed/budget_exceeded.
Restart recovery: reclaim own stale rows; finished report replays
idempotently (upload+register), unfinished ones fail as executor_restarted.
"""
from __future__ import annotations

import csv
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TP_ROOT = Path("/srv/tensor/shared/tensor_eco/TensorPhysics")
S0_SCRIPT = TP_ROOT / "scenarios/wedge_icebreaking/s0.py"
PLATFORM_ENV = Path("/srv/tensor/shared/tensor_eco/TensorPlatform/.env")
RUNS_ROOT = Path("/srv/tensor/shared/tensor_eco/executor-runs")
ECO_ROOT = Path("/srv/tensor/shared/tensor_eco")
WORKSPACE_REPOS = ["TensorPhysics", "TensorPlatform", "TensorLBM",
                   "TensorFVM", "TensorFEM", "TensorDEM"]

EXECUTOR_ID = os.environ.get("TENSOR_EXECUTOR_ID", f"executor-{socket.gethostname()}")
POLL_SECONDS = 5
HEARTBEAT_SECONDS = 15
BUDGET_POLL_SECONDS = 5

MEDIA = {
    ".json": "application/json",
    ".csv": "text/csv",
    ".png": "image/png",
    ".pt": "application/octet-stream",
    ".txt": "text/plain",
    ".log": "text/plain",
}
LEGS = ["lbm", "fvm", "fem", "dem"]

API: str = ""
USERNAME: str = ""
PASSWORD: str = ""
S3_ENDPOINT: str = "http://127.0.0.1:8333"
S3_BUCKET: str = "open-foundation"
S3_AK: str = ""
S3_SK: str = ""

_token: str | None = None
_token_lock = threading.Lock()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_env() -> None:
    global API, USERNAME, PASSWORD, S3_ENDPOINT, S3_BUCKET
    env_file = Path(__file__).resolve().parent / "executor.env"
    cfg = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    API = cfg["TENSORPLATFORM_URL"].rstrip("/")
    USERNAME = cfg["TENSOR_EXECUTOR_USERNAME"]
    PASSWORD = cfg["TENSOR_EXECUTOR_PASSWORD"]
    S3_ENDPOINT = cfg.get("S3_ENDPOINT", "http://127.0.0.1:8333")
    S3_BUCKET = cfg.get("S3_BUCKET", "open-foundation")

    platform = {}
    for line in PLATFORM_ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            platform[k.strip()] = v.strip()
    global S3_AK, S3_SK
    S3_AK = platform["MINIO_ACCESS_KEY"]
    S3_SK = platform["MINIO_SECRET_KEY"]

    RUNS_ROOT.mkdir(parents=True, exist_ok=True)


def login() -> str:
    global _token
    body = json.dumps({"username": USERNAME, "password": PASSWORD}).encode()
    req = urllib.request.Request(
        f"{API}/api/v1/auth/login", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        _token = json.loads(resp.read())["access_token"]
    return _token


def api(method: str, path: str, body=None, timeout: float = 30.0):
    """Authenticated call; re-logins once on 401. Returns (status, json|text)."""
    with _token_lock:
        token = _token
    for attempt in (1, 2):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            API + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 1:
                token = login()
                continue
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"detail": raw[:500]}
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, OSError) as exc:
            # transient transport failures (backend restart etc.) must not kill the daemon
            return 0, {"detail": f"{type(exc).__name__}: {exc}"[:300]}
    return 0, {"detail": "unreachable"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ───────────────────────── run + register (S0) ─────────────────────────

def upload_file(run_id: str, path: Path, role: str) -> dict:
    import boto3

    key = f"tensorphysics/{run_id}/{path.name}"
    digest = sha256_file(path)
    s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, aws_access_key_id=S3_AK,
                      aws_secret_access_key=S3_SK, region_name="us-east-1")
    s3.upload_file(str(path), S3_BUCKET, key)
    return {
        "uri": f"s3://{S3_BUCKET}/{key}",
        "media_type": MEDIA.get(path.suffix.lower(), "application/octet-stream"),
        "checksum_sha256": digest,
        "size_bytes": path.stat().st_size,
        "role": role,
    }


def register(run_id: str, manifest: dict) -> bool:
    status, resp = api("POST", "/api/v1/tensorphysics/runs", {
        "run_id": run_id,
        "status": manifest["status"],
        "validation_status": manifest["validation_status"],
        "manifest": manifest,
        "organization_id": 1,
    })
    ok = status in (200, 201)
    log(f"register {run_id}: HTTP {status} {'ok' if ok else resp}")
    return ok


def collect_and_register(work_dir: Path) -> tuple[list[str], dict]:
    """Mirror of register_s0.py: read machine outputs, upload, register.

    Returns (registered_run_ids, scenario_metrics).
    """
    def load(p: Path):
        return json.loads(p.read_text(encoding="utf-8"))

    goal = load(work_dir / "research_goal.json")
    s0_report = load(work_dir / "s0_report.json")
    registered: list[str] = []

    for leg in LEGS:
        leg_dir = work_dir / leg
        leg_rm = load(leg_dir / "run_manifest.json")
        leg_vr = load(leg_dir / "validation_report.json")
        run_id = leg_rm["run_id"]

        outputs = [upload_file(run_id, leg_dir / "run_manifest.json", "run-manifest"),
                   upload_file(run_id, leg_dir / "validation_report.json", "validation-report")]
        store = leg_dir / "artifact-store" / run_id
        if store.is_dir():
            for art in sorted(store.iterdir()):
                if art.is_file():
                    outputs.append(upload_file(run_id, art, "solver-output"))

        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "status": leg_rm["status"],
            "validation_status": leg_rm["validation_status"],
            "goal": {
                "objective": goal["objective"],
                "hypothesis": f"wedge_icebreaking S0 {leg} leg pipeline integrity smoke",
                "acceptance_criteria": goal["acceptance_criteria"],
            },
            "producer": {
                "name": "TensorPhysics",
                "version": "0.1.0",
                "revision": leg_rm["code_revisions"]["cxs503/TensorPhysics"],
            },
            "inputs": leg_rm.get("inputs", []),
            "configuration_sha256": leg_rm["config_sha256"],
            "environment": leg_rm.get("environment", {}),
            "resources": leg_rm.get("resources", {}),
            "code_revisions": leg_rm["code_revisions"],
            "metrics": {
                "checks_passed": sum(c["passed"] for c in leg_vr["checks"]),
                "checks_total": len(leg_vr["checks"]),
            },
            "validation": {
                "status": leg_vr["status"],
                "validator": f"s0-{leg}-output-checks",
                "checks": [{"id": c["name"], "status": "passed" if c["passed"] else "failed"}
                           for c in leg_vr["checks"]],
            },
            "outputs": outputs,
            "parent_run_ids": [],
            "limitations": [
                "S0 is a pipeline integrity smoke (scientific_status=not_a_physics_benchmark); "
                "numeric values are not physical benchmarks.",
                f"Scenario decision: {s0_report['decision']}.",
            ],
        }
        if register(run_id, manifest):
            registered.append(run_id)

    leg_ids = [load(work_dir / f"{l}/run_manifest.json")["run_id"] for l in LEGS]
    scenario_id = load(work_dir / "lbm/run_manifest.json")["run_id"].rsplit("-", 1)[0]
    outputs = [upload_file(scenario_id, work_dir / "s0_report.json", "scenario-report"),
               upload_file(scenario_id, work_dir / "research_goal.json", "research-goal")]
    checks_all = [c for leg in s0_report["legs"] for c in leg["checks"]]
    # green = every gate passed (decision strings are free-form descriptors)
    green = bool(checks_all) and all(c["passed"] for c in checks_all)
    scenario_manifest = {
        "schema_version": 1,
        "run_id": scenario_id,
        "status": "succeeded" if green else "failed",
        "validation_status": "passed" if green else "failed",
        "goal": {
            "objective": goal["objective"],
            "hypothesis": "All four solver legs pass integrity gates under one fail-closed controller",
            "acceptance_criteria": goal["acceptance_criteria"],
        },
        "producer": {
            "name": "TensorPhysics",
            "version": "0.1.0",
            "revision": load(work_dir / "lbm/run_manifest.json")["code_revisions"]["cxs503/TensorPhysics"],
        },
        "configuration_sha256": sha256_file(work_dir / "s0_report.json"),
        "environment": load(work_dir / "lbm/run_manifest.json").get("environment", {}),
        "resources": {"backend": "tensor-executor"},
        "metrics": {
            "legs": len(s0_report["legs"]),
            "checks_passed": sum(c["passed"] for c in checks_all),
            "checks_total": len(checks_all),
        },
        "validation": {
            "status": "passed" if green else "failed",
            "validator": "s0-scenario-gates",
            "checks": [{"id": c["name"], "status": "passed" if c["passed"] else "failed"}
                       for c in checks_all],
        },
        "outputs": outputs,
        "parent_run_ids": leg_ids,
        "limitations": [
            f"scientific_status={s0_report['scientific_status']}; not eligible for "
            "Benchmark-verified promotion.",
            f"decision={s0_report['decision']}; next_stage={s0_report['next_stage']}.",
        ],
    }
    if register(scenario_id, scenario_manifest):
        registered.append(scenario_id)

    metrics = {
        "decision": s0_report["decision"],
        "checks_passed": sum(c["passed"] for c in checks_all),
        "checks_total": len(checks_all),
        "scenario_run_id": scenario_id,
    }
    return registered, metrics


# ───────────────────────── process supervision ─────────────────────────

class SupervisedRun:
    def __init__(self, cmd: list[str], cwd: Path, log_path: Path,
                 budget_seconds: int, execution_id: int, claim_token: str):
        self.cmd = cmd
        self.cwd = cwd
        self.log_path = log_path
        self.budget_seconds = budget_seconds
        self.execution_id = execution_id
        self.claim_token = claim_token
        self.proc: subprocess.Popen | None = None
        self.killed_reason: str | None = None
        self.kill_event = threading.Event()

    def tail(self, nbytes: int = 4096) -> str | None:
        try:
            with self.log_path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - nbytes))
                return fh.read().decode(errors="replace")
        except OSError:
            return None

    def kill(self, reason: str) -> None:
        if self.proc and self.proc.poll() is None:
            self.killed_reason = reason
            self.kill_event.set()
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def heartbeat_loop(self) -> None:
        while not (self.proc and self.proc.poll() is not None):
            time.sleep(HEARTBEAT_SECONDS)
            if self.proc and self.proc.poll() is not None:
                break
            status, resp = api("POST",
                               f"/api/v1/tensor/executions/{self.execution_id}/heartbeat",
                               {"claim_token": self.claim_token,
                                "log_tail": self.tail()},
                               timeout=15)
            if status == 200 and isinstance(resp, dict) and resp.get("action") == "kill":
                self.kill("cancel_requested")
                return
            if status == 0:
                log(f"heartbeat transport error: {resp}")

    def budget_loop(self) -> None:
        deadline = time.monotonic() + self.budget_seconds
        while not (self.proc and self.proc.poll() is not None):
            time.sleep(BUDGET_POLL_SECONDS)
            if self.proc and self.proc.poll() is not None:
                return
            if time.monotonic() > deadline:
                self.kill("budget_exceeded")
                return

    def run(self) -> tuple[int, str | None]:
        with self.log_path.open("wb") as log_fh:
            self.proc = subprocess.Popen(
                self.cmd, cwd=str(self.cwd), stdout=log_fh, stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "PYTHONPATH": f"{TP_ROOT / 'src'}:{os.environ.get('PYTHONPATH', '')}"},
            )
        hb = threading.Thread(target=self.heartbeat_loop, daemon=True)
        budget = threading.Thread(target=self.budget_loop, daemon=True)
        hb.start()
        budget.start()
        rc = self.proc.wait()
        hb.join(timeout=3)
        budget.join(timeout=3)
        return rc, self.killed_reason


# ───────────────────────── execution lifecycle ─────────────────────────

def request_approval(scenario_key: str, metrics: dict, log_path: Path) -> None:
    green = bool(metrics.get("checks_total")) and \
        metrics.get("checks_passed") == metrics.get("checks_total")

    if green:
        # promotion only applies while the scenario still sits at S0;
        # a green re-run after promotion is a regression check, not a gate.
        st, sc = api("GET", f"/api/v1/tensor/scenarios/{scenario_key}")
        if st == 200 and sc.get("current_stage") != "S0":
            log(f"green re-run with current_stage={sc.get('current_stage')}: "
                "no promotion approval needed")
            return

    kind = "stage_promotion" if green else "stop_condition"
    status, resp = api("POST", "/api/v1/tensor/approvals", {
        "scenario_key": scenario_key,
        "kind": kind,
        "to_stage": "S1" if green else None,
        "reason": (
            f"S0 executor finished: decision={metrics.get('decision')} "
            f"checks={metrics.get('checks_passed')}/{metrics.get('checks_total')} "
            f"scenario_run={metrics.get('scenario_run_id')}"
        ) if green else (
            f"S0 gates failed: decision={metrics.get('decision')} "
            f"checks={metrics.get('checks_passed')}/{metrics.get('checks_total')}; "
            "human review requested (fail-closed)."
        ),
        "evidence": {**metrics, "executor_log": str(log_path)},
    })
    log(f"approval request ({kind}): HTTP {status} {resp if status not in (200, 201) else resp.get('id')}")


def run_claimed(execution: dict, claim_token: str) -> None:
    eid = execution["id"]
    key = execution["execution_key"]
    work_dir = RUNS_ROOT / key
    log_path = work_dir / "executor.log"
    work_dir.mkdir(parents=True, exist_ok=True)
    budget_seconds = int((execution.get("budget") or {}).get("max_wall_seconds", 900))

    api("POST", f"/api/v1/tensor/executions/{eid}/start",
        {"claim_token": claim_token, "log_tail": f"starting {key}"})

    cmd = [sys.executable, str(S0_SCRIPT),
           "--work-dir", str(work_dir), "--scenario-id", key]
    sup = SupervisedRun(cmd, TP_ROOT, log_path, budget_seconds, eid, claim_token)
    rc, killed_reason = sup.run()
    log(f"{key}: exit={rc} killed_reason={killed_reason}")

    if killed_reason:
        api("POST", f"/api/v1/tensor/executions/{eid}/complete", {
            "claim_token": claim_token, "status": "failed",
            "stop_condition": killed_reason,
            "log_tail": sup.tail(),
            "result_summary": {"exit_code": rc},
        })
        return

    if rc != 0 or not (work_dir / "s0_report.json").exists():
        api("POST", f"/api/v1/tensor/executions/{eid}/complete", {
            "claim_token": claim_token, "status": "failed",
            "stop_condition": "scenario_script_failed",
            "log_tail": sup.tail(),
            "result_summary": {"exit_code": rc},
        })
        return

    registered, metrics = collect_and_register(work_dir)
    events = [{"kind": "validation_seen", "payload": metrics}]
    api("POST", f"/api/v1/tensor/executions/{eid}/complete", {
        "claim_token": claim_token, "status": "succeeded",
        "registered_run_ids": registered,
        "result_summary": metrics,
        "log_tail": sup.tail(),
        "events": events,
    })
    request_approval(execution["scenario_key"], metrics, log_path)


def _git(path: Path, *args: str) -> tuple[int, str]:
    try:
        out = subprocess.run(["git", "-C", str(path), *args],
                             capture_output=True, text=True, timeout=10)
        return out.returncode, out.stdout.strip()
    except Exception:  # noqa: BLE001 — snapshot must never kill the daemon
        return 1, ""


def snapshot_workspace() -> list[dict]:
    """Live git state of every repo in the six-library workspace."""
    repos: list[dict] = []
    for name in WORKSPACE_REPOS:
        path = ECO_ROOT / name
        rc, head = _git(path, "rev-parse", "HEAD")
        if rc != 0 or not head:
            repos.append({"name": name, "error": "unreadable"})
            continue
        _, branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        rc2, meta = _git(path, "log", "-1", "--format=%cI%x1f%s")
        commit_at, subject = "", ""
        if rc2 == 0 and "\x1f" in meta:
            commit_at, subject = meta.split("\x1f", 1)
        _, dirty = _git(path, "status", "--porcelain")
        repos.append({
            "name": name,
            "branch": branch or None,
            "head": head,
            "head_short": head[:8],
            "commit_at": commit_at or None,
            "subject": subject[:100] or None,
            "dirty_files": len(dirty.splitlines()),
        })
    return repos


def push_workspace_snapshot() -> None:
    status, resp = api("POST", "/api/v1/tensor/workspace", {
        "executor_id": EXECUTOR_ID,
        "repos": snapshot_workspace(),
    })
    if status not in (200, 201):
        log(f"workspace snapshot: HTTP {status} {resp}")


def recover_stale_claims() -> None:
    for status_filter in ("claimed", "running"):
        status, resp = api("GET",
                           f"/api/v1/tensor/executions?status={status_filter}&limit=50")
        if status != 200:
            continue
        for row in resp.get("items", []):
            if row.get("claimed_by") != EXECUTOR_ID:
                continue
            eid = row["id"]
            key = row["execution_key"]
            log(f"recovering stale claim: {key} ({row['status']})")
            st, rc = api("POST", f"/api/v1/tensor/executions/{eid}/reclaim",
                         {"executor_id": EXECUTOR_ID})
            if st != 200:
                log(f"reclaim {key}: HTTP {st} {rc} (reaper will handle)")
                continue
            token = rc["claim_token"]
            work_dir = RUNS_ROOT / key
            if (work_dir / "s0_report.json").exists():
                registered, metrics = collect_and_register(work_dir)
                api("POST", f"/api/v1/tensor/executions/{eid}/complete", {
                    "claim_token": token, "status": "succeeded",
                    "registered_run_ids": registered,
                    "result_summary": metrics,
                    "events": [{"kind": "note", "payload": {"recovered": True}}],
                })
            else:
                api("POST", f"/api/v1/tensor/executions/{eid}/complete", {
                    "claim_token": token, "status": "failed",
                    "stop_condition": "executor_restarted",
                })


def main() -> None:
    load_env()
    # survive backend restart windows: retry login with backoff forever
    for attempt in range(1, 1000):
        try:
            login()
            break
        except Exception as exc:  # noqa: BLE001 — daemon must survive
            log(f"login attempt {attempt} failed: {type(exc).__name__}: {exc}")
            time.sleep(min(60, attempt * 5))
    log(f"executor {EXECUTOR_ID} online (api={API})")
    recover_stale_claims()
    snapshot_tick = 0
    while True:
        snapshot_tick += 1
        if snapshot_tick % 12 == 1:  # startup + every ~60s
            push_workspace_snapshot()
        status, resp = api("POST", "/api/v1/tensor/executions/claim", {
            "executor_id": EXECUTOR_ID,
            "hostname": socket.gethostname(),
            "version": "1.0.0",
            "capabilities": ["wedge-icebreaking:S0"],
        }, timeout=20)
        if status != 200:
            log(f"claim error: HTTP {status} {resp}")
            time.sleep(POLL_SECONDS)
            continue
        if not resp.get("claimed"):
            time.sleep(POLL_SECONDS)
            continue
        execution = resp["execution"]
        log(f"claimed {execution['execution_key']} (stage {execution['stage']})")
        try:
            run_claimed(execution, execution["claim_token"])
        except Exception as exc:  # noqa: BLE001 — daemon must survive
            log(f"execution crashed: {type(exc).__name__}: {exc}")
            api("POST", f"/api/v1/tensor/executions/{execution['id']}/complete", {
                "claim_token": execution["claim_token"], "status": "failed",
                "stop_condition": "executor_exception",
                "result_summary": {"error": f"{type(exc).__name__}: {exc}"[:500]},
            })


if __name__ == "__main__":
    main()
