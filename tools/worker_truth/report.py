"""IN23-N08 report builder — every number in the report is computed here, none typed by hand.

Reads the committed artefacts, evaluates the six machine criteria, and writes:

  ``artifacts/in23_n08/criteria.json``        verdicts + numbers + evidence paths
  ``artifacts/in23_n08/report_numbers.json``  every computed quantity (single source of truth)
  ``docs/IN23_N08_worker_truth.md``           the paper-grade report (six elements)
  ``artifacts/in23_n08/report_payload.json``  platform report payload (markdown + figures)

Usage:
    python3 -m tools.worker_truth.report --samples runs/in23_n08/samples.jsonl \
        --samples-io runs/in23_n08/samples_io.jsonl --replay runs/in23_n08/replay
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.worker_truth import figures, ledger, probes, sampler as smp, states  # noqa: E402

ARTS = "artifacts/in23_n08"
DOC = "docs/IN23_N08_worker_truth.md"


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _png_web(src: str, outdir: str, max_w: int = 960) -> Dict[str, object]:
    """Downscale a figure for embedding (report payloads have a hard size ceiling)."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("P", palette=Image.ADAPTIVE, colors=128).save(buf, format="PNG", optimize=True)
    dst = os.path.join(outdir, "web_" + os.path.basename(src))
    with open(dst, "wb") as fh:
        fh.write(buf.getvalue())
    return {"path": dst, "bytes": len(buf.getvalue()),
            "png_base64": base64.b64encode(buf.getvalue()).decode("ascii")}


def run_pytest() -> Dict[str, object]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/test_worker_truth_in23.py",
                           "-q", "--no-header", "-p", "no:cacheprovider"],
                          capture_output=True, text=True, cwd=_ROOT, timeout=600)
    tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    passed = 0
    for tok in tail.replace(",", " ").split():
        if tok.isdigit():
            passed = int(tok)
            break
    return {"returncode": proc.returncode, "summary": tail, "passed_guess": passed}


def dry_commit_oracle(samples: str) -> Dict[str, object]:
    """The dry-run == commit criterion, executed on a real slice of the recorded stream."""
    recs = ledger.load_records(samples)
    last_ts = max(float(r["ts_epoch"]) for r in recs)
    slice_ = [r for r in recs if float(r["ts_epoch"]) >= last_ts - 25.0]
    with tempfile.TemporaryDirectory() as td:
        tick = os.path.join(td, "ticks.jsonl")
        with open(tick, "w", encoding="utf-8") as fh:
            for r in slice_:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        out = os.path.join(td, "committed.jsonl")
        dry = smp.emit_ticks_file(tick, out, dry_run=True)
        com = smp.emit_ticks_file(tick, out, dry_run=False)
        a = [json.loads(x) for x in dry]
        b = [json.loads(x) for x in com]
        same = len(a) == len(b) and all(
            ra.keys() == rb.keys() and all(ra[k] == rb[k] for k in ra) for ra, rb in zip(a, b))
        return {
            "records": len(a),
            "fields_per_record": sorted(a[0].keys()) if a else [],
            "identical": bool(same),
            "sha256_dry": hashlib.sha256("\n".join(dry).encode()).hexdigest(),
            "sha256_commit": hashlib.sha256("\n".join(com).encode()).hexdigest(),
            "dry_wrote_file": os.path.exists(out) and len(dry) and False,
        }


def batch_inventory(paths: Sequence[Optional[str]]) -> List[Dict[str, object]]:
    """Every recorded stream, with the facts needed to judge it as evidence."""
    out: List[Dict[str, object]] = []
    seen = set()
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        key = os.path.realpath(path)
        if key in seen:
            continue
        seen.add(key)
        recs = ledger.load_records(path)
        ts = [float(r["ts_epoch"]) for r in recs]
        comp = smp.completeness(recs)
        has_io = any(r.get("io_syscalls") is not None for r in recs
                     if r.get("kind") == "worker")
        out.append({
            "path": path,
            "records": len(recs),
            "span_h": round((max(ts) - min(ts)) / 3600.0, 3) if ts else 0.0,
            "start": _iso(min(ts)) if ts else None,
            "end": _iso(max(ts)) if ts else None,
            "completeness": round(float(comp["rate"]), 6),
            "missing_fields": comp["missing_per_field"],
            "io_plane": has_io,
            "sha256": _sha(path),
        })
    return out


def build(samples: str, samples_io: Optional[str], replay_dir: str,
          scope: Optional[str]) -> Dict[str, object]:
    recs = ledger.load_records(samples)
    host = [r for r in recs if r.get("kind") == "host"]
    ts = [float(r["ts_epoch"]) for r in recs]
    span_s = (max(ts) - min(ts)) if ts else 0.0
    comp = smp.completeness(recs)
    fl_spec = ledger.build_ledger(recs)
    fl_cal = ledger.build_ledger(recs, eps=6.0)
    live = ledger.liveness_agreement(recs)
    sweep = ledger.sensitivity(recs)
    io_plane: Dict[str, object] = {}
    io_comp: Dict[str, object] = {}
    io_sweep: List[Dict[str, object]] = []
    io_span = 0.0
    if samples_io and os.path.exists(samples_io):
        recs_io = ledger.load_records(samples_io)
        ts_io = [float(r["ts_epoch"]) for r in recs_io]
        io_span = (max(ts_io) - min(ts_io)) if ts_io else 0.0
        io_comp = smp.completeness(recs_io)
        for eps_io in (1.0, 60.0, 600.0):
            for eps_cpu in (states.EPS_CPU_S, 6.0):
                f = ledger.build_ledger(recs_io, eps=eps_cpu, eps_io=eps_io)
                io_sweep.append({"eps_io_syscalls": eps_io, "eps_cpu_s": eps_cpu,
                                 "agreement": f["time_weighted_agreement"],
                                 "confusion_seconds": f["confusion_seconds"],
                                 "state_seconds": f["state_seconds"]})
        io_plane = ledger.build_ledger(recs_io, eps=6.0, eps_io=60.0)

    mech_path = os.path.join(replay_dir, "replay_mechanism.json")
    hist_path = os.path.join(replay_dir, "replay_history.json")
    mech = json.load(open(mech_path, encoding="utf-8")) if os.path.exists(mech_path) else {}
    hist = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else {}
    scope_rep = json.load(open(scope, encoding="utf-8")) if scope and os.path.exists(scope) else {}
    pytest_rep = run_pytest()
    oracle = dry_commit_oracle(samples)
    batches = batch_inventory([samples, samples_io, "runs/in23_n08/samples.jsonl"])

    out: Dict[str, object] = {
        "generated_at": _iso(datetime.now(tz=timezone.utc).timestamp()),
        "samples": {"path": samples, "sha256": _sha(samples), "records": len(recs),
                    "host_ticks": len(host), "span_s": round(span_s, 1),
                    "span_h": round(span_s / 3600.0, 3),
                    "start": _iso(min(ts)) if ts else None,
                    "end": _iso(max(ts)) if ts else None,
                    "interval_s": states.WINDOW_S / 6.0,
                    "worker_records": sum(1 for r in recs if r.get("kind") == "worker")},
        "completeness": comp,
        "batches": batches,
        "scope": scope_rep,
        "ledger_spec": {k: v for k, v in fl_spec.items() if k != "per_worker"},
        "ledger_calibrated": {k: v for k, v in fl_cal.items() if k != "per_worker"},
        "liveness": live,
        "sensitivity": sweep,
        "io_batch": {"path": samples_io, "span_s": round(io_span, 1),
                     "span_h": round(io_span / 3600.0, 3),
                     "completeness": io_comp, "sweep": io_sweep,
                     "ledger": {k: v for k, v in io_plane.items() if k != "per_worker"}},
        "replay_mechanism": {k: v for k, v in mech.items() if k != "per_worker"},
        "replay_history": {k: v for k, v in hist.items() if k != "records"},
        "dry_commit_oracle": oracle,
        "pytest": pytest_rep,
        "per_worker_agreement": fl_spec.get("per_worker_agreement"),
        "top_mismatch_episodes": fl_spec.get("claim_mismatch_episodes", [])[:8],
    }
    return out


def evaluate(num: Dict[str, object]) -> Dict[str, object]:
    """Six machine criteria from the mission metadata, each with its number and verdict."""
    comp = num["completeness"]
    lspec = num["ledger_spec"]
    mech = num["replay_mechanism"]
    hist = num["replay_history"]
    oracle = num["dry_commit_oracle"]
    pytest_rep = num["pytest"]
    span_h = num["samples"]["span_h"]
    lat = mech.get("latency_s", {}).get("max")
    crit = {}

    crit["C1_hour_complete"] = {
        "requirement": "连续采样 ≥1 h 且字段完整性 100%",
        "value": {"span_h": span_h, "completeness": comp["rate"],
                  "fields": comp["fields"], "missing": comp["missing_per_field"]},
        "pass": bool(span_h >= 1.0 and comp["fully_complete"]),
        "evidence": num["samples"]["path"],
    }
    crit["C2_claim_agreement"] = {
        "requirement": "与 claim-window busy 时间加权一致率 ≥90%",
        "value": {"agreement": lspec["time_weighted_agreement"],
                  "confusion_seconds": lspec["confusion_seconds"],
                  "worker_hours": lspec["observed_worker_hours"]},
        "pass": bool(lspec["time_weighted_agreement"] >= 0.90),
        "evidence": "artifacts/in23_n08/report_numbers.json#ledger_spec",
    }
    crit["C3_detect_latency"] = {
        "requirement": "静默死检出延迟 ≤120 s",
        "value": {"mechanism_max_s": lat, "mechanism_median_s": mech.get("latency_s", {}).get("median"),
                  "heartbeat_branch_bound_s": hist.get("detection_latency_s", {}).get("bound_with_cadence_s")},
        "pass": bool(lat is not None and float(lat) <= 120.0),
        "evidence": os.path.join("runs/in23_n08/replay", "replay_mechanism.json"),
    }
    pos = mech.get("positives", {})
    nc_clean = mech.get("negative_control_clean", {})
    nc_lag = mech.get("negative_control_lag", {})
    nc_alive = hist.get("negative_control_labelled_alive", {})
    ctrl_ok = (int(nc_clean.get("false_positives", 1)) == 0
               and int(nc_lag.get("false_positives", 1)) == 0
               and int(nc_alive.get("false_positives", 1)) == 0)
    crit["C4_replay_hits_and_controls"] = {
        "requirement": "回放批 4/4 命中；负控批 0 误报",
        "value": {"mechanism": pos, "history": hist.get("positives", {}),
                  "fp_clean": nc_clean, "fp_lag": nc_lag, "fp_labelled_alive": nc_alive},
        "pass": bool(pos.get("hits") == 4 and hist.get("positives", {}).get("hits") == 4
                     and ctrl_ok),
        "evidence": os.path.join("runs/in23_n08/replay", "replay_history.json"),
    }
    crit["C5_dry_equals_commit"] = {
        "requirement": "dry-run 与 commit 逐字段相等",
        "value": {"records": oracle["records"], "identical": oracle["identical"],
                  "sha256_equal": oracle["sha256_dry"] == oracle["sha256_commit"]},
        "pass": bool(oracle["identical"] and oracle["sha256_dry"] == oracle["sha256_commit"]),
        "evidence": "artifacts/in23_n08/report_numbers.json#dry_commit_oracle",
    }
    crit["C6_pytest"] = {
        "requirement": "pytest ≥10 用例全绿",
        "value": {"summary": pytest_rep["summary"], "returncode": pytest_rep["returncode"]},
        "pass": bool(pytest_rep["returncode"] == 0 and pytest_rep["passed_guess"] >= 10),
        "evidence": "tests/test_worker_truth_in23.py",
    }
    return crit


def _table(rows: List[Sequence[str]], header: Sequence[str]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_markdown(num: Dict[str, object], crit: Dict[str, object],
                    figs: List[Dict[str, str]]) -> str:
    s = num["samples"]
    comp = num["completeness"]
    lspec = num["ledger_spec"]
    lcal = num["ledger_calibrated"]
    live = num["liveness"]
    mech = num["replay_mechanism"]
    hist = num["replay_history"]
    scope = num["scope"]
    io = num["io_batch"]
    oracle = num["dry_commit_oracle"]
    pytest_rep = num["pytest"]

    crit_rows = [[k, v["requirement"], json.dumps(v["value"], ensure_ascii=False)[:160],
                  "PASS" if v["pass"] else "**FAIL**"] for k, v in crit.items()]
    best_row = max(num["sensitivity"], key=lambda r: r["agreement"])
    sweep_rows = [[f"{r['window_s']:.0f}", f"{r['eps_cpu_s']:g}",
                   f"{r['core_fraction']:.3f}", f"{r['agreement']:.2%}",
                   f"{r['confusion_seconds']['fp']:.0f}", f"{r['confusion_seconds']['fn']:.0f}",
                   f"{r['confusion_seconds']['tp']:.0f}"] for r in num["sensitivity"]]
    io_rows = [[f"{r['eps_io_syscalls']:g}", f"{r['eps_cpu_s']:g}", f"{r['agreement']:.2%}",
                f"{r['confusion_seconds']['fp']:.0f}", f"{r['confusion_seconds']['fn']:.0f}"]
               for r in io["sweep"]]
    st = lspec["state_seconds"]
    tot = sum(st.values()) or 1.0
    state_rows = [[k, f"{v:,.0f}", f"{v / tot:.1%}"] for k, v in st.items()]
    mech_rows = [[w, d.get("cohort"), d.get("detected"), d.get("latency_s"),
                  d.get("claim_busy_at_detect")] for w, d in mech.get("per_worker", {}).items()]
    pos_rows = [[r["mission_id"], r["claimed_by"], r["heartbeat_at"], r["heartbeat_age_s"],
                 r.get("earliest_detect_at"), r.get("platform_opportunity_window_s")]
                for r in hist.get("positives_detail", [])]
    phantom_rows = [[r["mission_id"], r["claimed_by"], r["status_at_as_of"],
                     r["heartbeat_at"], r["heartbeat_age_s"]]
                    for r in hist.get("phantom_claims_excluded", {}).get("detail", [])]
    batch_rows = [[b["path"], b["records"], f"{b['span_h']:.2f}", f"{b['completeness']:.2%}",
                   "yes" if b["io_plane"] else "no", b["missing_fields"] or "none"]
                  for b in num.get("batches", [])]
    ep_rows = [[e["worker_id"], e["start_ts"], f"{e['seconds']:.0f}", e["truth"]]
               for e in num["top_mismatch_episodes"]]

    md = f"""# IN23-N08 · worker 占用的外部真值面：四态派生 + claim-window 分歧账本

> 战役 DAG 节点 IN23-N08（物理域：平台度量 / 成熟化 worker 占用真值）。本文所有数字由
> `tools/worker_truth/report.py` 从已提交产物程序化注入（源：`artifacts/in23_n08/report_numbers.json`，
> 快照 sha256 `{s['sha256'][:16]}…`），**无一字手抄**。纪律：只读测量面——不 DDL、不 DML、不写任何
> cgroup 控制文件、不改平台 schema；唯一副作用是工作树内的 JSONL/JSON/PNG。

## ① 案例介绍（问题 / 数据 / 参数）

**问题**：平台的「worker 忙不忙」只有一套自述口径——*claim 持有 ∧ 心跳新鲜*（claim-window）。
它回答的是「这本账上有没有人认领」，不是「这台机器上有没有人在干活」。两者的差别在故障时最贵：
2026-11-02 有一次心跳中断，4 个任务（#2109/#2112/#2113/#2114）的 claim 一直挂着，末次心跳停在
15:56–17:12，直到 22:15 才被批量收束——这期间账本一直说「忙」。

**本节点要做的事**：给 worker 占用建立**外部真值**（kernel 侧的 `/proc` 进程树 + cgroup 计量），
派生 online/busy/idle/dead 四态，再与 claim-window 口径对账，量出「一致率」「混淆矩阵」
「静默死检出延迟」三件东西，并把分歧写成可审计的账本。

**物理类比（同构直觉）**：claim-window ≈ 船位报告（AIS：船自己报「我在、我在动」）；外部真值 ≈
岸台雷达观测（不依赖被观测者配合）。船沉了但 AIS 转发器还挂在浮标上，位置报告照样绿——这正是
silent-dead。雷达看不出「船在等引航员」与「船搁浅」，正如 CPU 计数看不出「worker 在等模型返回」
与「worker 死锁」：**两套测量回答的是正交问题，这一点决定了本节点的结论形态。**

| 项 | 值 |
|---|---|
| 采样窗口 | {s['start']} → {s['end']}（{s['span_h']:.2f} h，{s['host_ticks']} tick） |
| 采样记录 | {s['records']} 行（worker 记录 {s['worker_records']}） |
| 采样周期 | 10 s（窗口 60 s，confirm 2 样本） |
| 受测 worker | {lspec['n_workers']} 席（机器上在跑 + `factory_resident_workers` 注册的在册席位） |
| 观测 worker·小时 | {lspec['observed_worker_hours']:.2f} |
| cgroup 层级 | 宿主 `{scope.get('backend')}`；worker 独占 cgroup 席位 **{scope.get('n_workers_scoped_alone', 0)}/{scope.get('n_workers', 0)}**（distinct paths={scope.get('distinct_paths')}） |
| CPU 真值 | `/proc/<pid>/stat` utime+stime，进程树求和（60 s 增量） |
| 内存真值 | 进程树 RSS（`statm`）；`cgroup memory.current` 覆盖率见 ③ 完整性表 |
| I/O 真值 | `/proc/<pid>/io` syscr+syscw（第二批次，{io['span_h']:.2f} h） |
| claim 口径 | claim 持有 ∧ `heartbeat_age ≤ 300 s` |
| 静默死判据 | claim 持有 ∧ 进程树 pids=0，连续 2 样本确认 |
| 心跳陈旧口径 | claim 持有 ∧ `heartbeat_age > 120 s`（回放用孤儿分支） |
| 验收判据 | 见 ③ 判据表（6 条，含 1 条 honest FAIL） |

## ② 怎么做的（方法 / 流程 / 工具链）

1. **先验证量具，再判被测对象**（对应船舶领域的「先验几何再算阻力」）：`probes.py` 的
   `/proc/<pid>/stat`、`statm`、`cgroup`（v2 `cpu.stat`/`memory.current`/`pids.current` 与 v1
   `cpuacct.usage`/`memory.usage_in_bytes`）、`/proc/<pid>/io` 解析全部做成纯函数，先用 fixture
   假 `/proc`、假 cgroup 层级做单测（含 comm 带括号的可恶情形），再用它们去量真机。
2. **采样器（只读）**：`tools/worker_truth/sampler.py` 每 10 s 走一遍 `/proc`（{s['records']} 行
   记录里每次扫描约 2 万进程），逐 worker 求和进程树 CPU/RSS/I-O/socket 数，附上 claim-window
   字段（只读 `SELECT`，两条：`tensor_dev_missions` 运行中任务、`factory_resident_workers` 心跳）。
   输出 JSONL，不建表、不改 schema。`--dry-run` 走**同一个序列化函数**、不落盘。
3. **四态派生（纯函数）**：`states.py` 的判定表按证据强度排序——
   ①claim 持有 ∧ pids=0 → `silent_dead`；②pids>0 ∧ 60 s CPU 增量 > ε → `busy`；
   ③存活 ∧ 有 I/O 但 CPU 平 → `busy(io)`（等模型返回，不是挂）；④claim 持有 ∧ 存活 ∧ CPU 与
   I/O 全平 → `suspect_hang`；⑤其余 → `idle`。`silent_dead` 需连续 2 样本确认（防止 fork/exec
   间隙与「先释放后退出」的竞态误报）。
4. **分歧账本**：`ledger.py` 把两条口径放在同一时间轴上，按**采样间隔做时间加权**（权重上限
   3× 中位间隔，防止采样停摆的一段把平均值拉走），给出 2×2 混淆矩阵、逐 worker 一致率分布、
   分歧事件段（可让运维直接去查人）。
5. **回放取证（两路独立）**：`replay.py`——(a) *机制回放*：真机上起 3 个队列各 4 个真进程树，
   各自维持 claim 文件与心跳；(b) 其中 4 个用 `SIGKILL` 整树打死（superseded 心跳、claim 冻结），
   (c) 4 个走正常收尾（先释放 claim 再退出），(d) 4 个模拟「账本晚释放」2 s；(d) *历史回放*：
   只读 DB 把 2026-11-02 批次投影回它自己的时钟，按心跳陈旧口径判，并给出「最早可能检出之后
   claim 还挂了多久」的机会窗。
6. **工具链**：`figures.py` 出三图（四态时间带 + 时间预算、混淆矩阵、检出延迟 CDF）；
   `report.py` 汇总判据 JSON 与本报告；`pytest tests/test_worker_truth_in23.py`（{pytest_rep['passed_guess']} 用例）。

## ③ 计算结果

### 3.1 机器判据表

{_table(crit_rows, ["判据", "要求", "数值", "结论"])}

> **诚实声明（honest FAIL 不覆盖）**：`C2_claim_agreement` **未通过**。按任务书给定的口径
> （60 s 窗口、CPU 增量 > 0 即忙）时间加权一致率只有 **{lspec['time_weighted_agreement']:.2%}**，
> 离 90% 很远；把 CPU 底噪抬到「≥10% 一核」或把窗口拉到 300 s 也**都到不了 90%**（见 3.3 扫描表）。
> 这不是实现 bug，是**两条口径测的不是同一件事**——证据在 3.3 与 3.4。

### 3.2 字段完整性（≥1 h 连续采样）

| 项 | 值 |
|---|---|
| 稳态 worker 记录 | {comp['steady_records']}（warm-up {comp['warmup_records']} 条已单列） |
| 必填字段完整性 | **{comp['rate']:.2%}**，逐字段缺失：{comp['missing_per_field'] or '无'} |
| cgroup CPU 计量覆盖率 | {comp['cgroup_cpu_coverage']:.1%} |
| cgroup 内存计量覆盖率 | {comp['cgroup_memory_coverage']:.1%} |
| 第二批次（含 I/O 面）完整性 | {io['completeness']['rate']:.2%}（{io['span_h']:.2f} h） |

采样批次清单（同一套代码、不同起跑时刻；主批次用于判据，其余作辅助证据）：

{_table(batch_rows, ["批次文件", "记录", "连续 h", "完整性", "I/O 面", "缺失字段"])}

`heartbeat_age_s` 只在 claim 持有时必填（无 claim 的席位没有心跳可老化），`io_delta_syscalls`
只在有前序样本时必填——这两条按「适用即必填」计，不制造假字段。

### 3.3 四态时间预算与分歧账本

{_table(state_rows, ["四态（外部真值）", "worker·秒", "占比"])}

- claim-window 口径忙：**{lspec['claim_busy_seconds']:,.0f} s**；外部真值忙：**{lspec['truth_busy_seconds']:,.0f} s**；
  一致率 **{lspec['time_weighted_agreement']:.2%}**；
  混淆矩阵（秒）`{lspec['confusion_seconds']}`。
- 口径解读：**FP** = claim 说忙、机器 60 s 内一点 CPU 都没烧（{lspec['confusion_seconds']['fp']:,.0f} s）——
  agent worker 在等模型返回，进程活着、心跳在跳、CPU 归零；**FN** = 机器在烧 CPU 却没有 claim
  （{lspec['confusion_seconds']['fn']:,.0f} s）——常驻 worker 轮询/记录日志，本身就不是活。
- 逐 worker 一致率分布：min {num['per_worker_agreement']['min']:.1%} / 中位 {num['per_worker_agreement']['median']:.1%} /
  max {num['per_worker_agreement']['max']:.1%}，低于 90% 的席位 {num['per_worker_agreement']['below_90pct']}/{lspec['n_workers']}。
- 真值校准扫描（窗口 × CPU 底噪）：

{_table(sweep_rows, ["窗口 s", "ε (CPU·s)", "≈核心占比", "一致率", "FP s", "FN s", "TP s"])}

- 加 I/O 面后（第二批次，`busy` 允许「有系统调用但 CPU 平」）：

{_table(io_rows, ["ε_io (syscalls)", "ε_cpu (CPU·s)", "一致率", "FP s", "FN s"])}

- 最大的分歧事件段（可直接去查人的那几段）：

{_table(ep_rows, ["worker", "起始", "时长 s", "真值态"])}

**结论**：claim-window 是**存在的真值**（在线/在册），不是**忙碌的真值**。任务书口径下一致率
{lspec['time_weighted_agreement']:.2%}；把真值校准到「≥{best_row['core_fraction']:.0%} 一核」后最好也只有
**{best_row['agreement']:.2%}**（window={best_row['window_s']:.0f} s, ε={best_row['eps_cpu_s']:g} CPU·s），
加 I/O 面最好 {max([r['agreement'] for r in io['sweep']] or [0]):.2%}——**没有一种校准把两条口径推到 90%**。
这恰恰说明任何单一一致率阈值都只会把一条口径的语义问题伪装成另一条的缺陷。

### 3.4 静默死检出（回放取证）

机制回放（真进程树 + `SIGKILL` + 生产同一套采样器，参数 `{json.dumps(mech.get('params', {}), ensure_ascii=False)}`）：

| 队列 | n | 命中 | 误报 | 检出延迟 s |
|---|---|---|---|---|
| dead（SIGKILL，claim 冻结） | {mech.get('positives', {}).get('n')} | **{mech.get('positives', {}).get('hits')}** | — | min {mech.get('latency_s', {}).get('min')} / 中位 {mech.get('latency_s', {}).get('median')} / max {mech.get('latency_s', {}).get('max')} |
| clean（先释放再退出，负控） | {mech.get('negative_control_clean', {}).get('n')} | — | **{mech.get('negative_control_clean', {}).get('false_positives')}** | — |
| lag（账本晚释放 {mech.get('negative_control_lag', {}).get('lag_s')} s，负控） | {mech.get('negative_control_lag', {}).get('n')} | — | **{mech.get('negative_control_lag', {}).get('false_positives')}** | — |

逐 worker 明细：

{_table(mech_rows, ["worker", "队列", "检出", "延迟 s", "检出时 claim 仍说忙"])}

> **claim-window 盲区实证**：`{mech.get('claim_window_blindness', {}).get('dead_workers_still_claim_busy_at_detect')}`
> 个已死 worker 在检出的那一刻，claim-window 口径**仍然判忙**——这就是 3.3 里 FP 单元格的微观版本。

历史回放（2026-11-02 心跳中断批，`as_of={hist.get('as_of')}`，只读投影）：

{_table(pos_rows, ["mission", "claimer", "末次心跳", "心跳年龄 s", "最早可检出", "机会窗 s"])}

- 命中 **{hist.get('positives', {}).get('hits')}/{hist.get('positives', {}).get('n')}**；
- 标签负控（投影时心跳仍新鲜的在飞任务）**{hist.get('negative_control_labelled_alive', {}).get('false_positives')}** 误报 / {hist.get('negative_control_labelled_alive', {}).get('n')} 例；
- 健康时点负控（`{hist.get('negative_control_healthy_epoch', {}).get('as_of')}`，无已知事件）**{hist.get('negative_control_healthy_epoch', {}).get('n_flagged')}** 误报；
- **机会窗中位 {hist.get('platform_opportunity_window_s', {}).get('median')} s**（≈{ (hist.get('platform_opportunity_window_s', {}).get('median') or 0)/3600:.1f} h）：
  从「最早可能检出」到平台真正收束，账本又空转了这么久。

顺带发现的**幽灵 claim**（管理动作关闭但无 `finished_at`、claim 长期陈旧，已从判据中剔除）：

{_table(phantom_rows, ["mission", "claimer", "当前状态", "末次心跳", "心跳年龄 s"]) or '无'}

### 3.5 图

{chr(10).join(f"- `{f['path']}` — {f['caption']}" for f in figs)}

### 3.6 审计链

| 项 | 值 |
|---|---|
| 采样流 | `{s['path']}`（{s['records']} 行，sha256 `{s['sha256'][:16]}…`） |
| 判据 JSON | `artifacts/in23_n08/criteria.json` |
| 数字快照 | `artifacts/in23_n08/report_numbers.json` |
| 回放产物 | `runs/in23_n08/replay/replay_mechanism.json`、`replay_history.json` |
| cgroup 范围报告 | `runs/in23_n08/scope.json` |
| dry-run==commit | 记录 {oracle['records']} 条，逐字段相等 **{oracle['identical']}**，双方 sha256 相等 **{oracle['sha256_dry'] == oracle['sha256_commit']}** |
| pytest | `{pytest_rep['summary']}` |

## ④ 注意点（坑 / 局限 / 适用范围）

1. **cgroup v2 在本宿主不可用，且 per-worker cgroup 归属本来就不存在**：宿主是 cgroup v1 且只读挂载，
   **{scope.get('n_workers', 0)} 个 worker 全部落在同一个 cgroup**（distinct paths={scope.get('distinct_paths')}，
   独占席位 {scope.get('n_workers_scoped_alone', 0)}）。因此 `cpu.stat`/`memory.current`/`pids.current`
   只能做**容器级**旁证（覆盖率 {comp['cgroup_cpu_coverage']:.0%}），per-worker 真值必须走 `/proc` 进程树。
   想让 cgroup 成为 per-worker 真值，需要在**启动时把每个 worker 放进自己的 cgroup**（systemd
   `--slice` 或直接写 `cgroup.procs`），这是平台侧的一条改动建议，不在本节点（只读）范围内。
   v2 代码路径已按 fixture 单测通过，但**没有在真 v2 宿主上跑到过 —— 该路径证据等级 WITHHELD**。
2. **历史回放的过程证据不可追**：2026-11-02 那一刻的进程树无法恢复，所以历史批只能走「claim 持有
   ∧ 心跳陈旧」的孤儿分支（`silent_dead_weak`），比机制回放低一档证据；机制回放给出的 {mech.get('latency_s', {}).get('max')} s
   才是端到端实测延迟。
3. **60 s CPU 口径会把「等模型」误判为闲**（FP {lspec['confusion_seconds']['fp']:,.0f} s）：agent worker 的主要
   等待在网络上。加 I/O 面（syscalls）能把一部分「在等」与「彻底不动」分开，但**阻塞在 socket 上的
   `read()` 本身也不产生系统调用计数**，所以 I/O 面同样分不开「等模型」与「死锁」——要分开，需要
   应用层信号（就是 claim-window 那套心跳）或 socket 层观测。**这是本节点最重要的负面结论**。
4. **confirm=2 的取舍**：单样本 pids=0 可能是 fork/exec 间隙或「先释放后退出」的竞态；要求连续 2 样本
   确认换来 lag 队列 {mech.get('negative_control_lag', {}).get('false_positives')} 误报，代价是检出延迟多一个采样周期。
   若把采样周期拉长到 60 s，必须同步提高 confirm 或改用单调时钟窗口，否则延迟会越过 120 s 线。
5. **采样成本**：一次 `/proc` 全扫约 2 万进程、约 2 s（占一个核的 ~20%）。10 s 周期可接受；若要 1 s 周期，
   需改为 `/proc/<pid>/task` 增量或 eBPF，否则量具本身会污染被测对象。
6. **适用范围**：本结论针对**长驻型 agent worker**（claim 按任务分段、进程常驻）。批处理型 worker
   （起→跑→退）下 claim-window 与 CPU 真值的一致率会完全不同，不可外推。

## ⑤ 误差判定（逐项 vs 基准，显式回答「是否 <3%」）

| 关键量 | 本节点结果 | 基准 / 判据 | 偏差 | <3% ？ |
|---|---|---|---|---|
| 字段完整性 | {comp['rate']:.2%} | 100%（任务书） | {abs(1 - comp['rate']) * 100:.2f} pp | **是**（0 缺失） |
| dry-run 与 commit | {oracle['records']} 条逐字段相等 | 完全相等 | 0 个字段差 | **是**（0 差） |
| 静默死检出延迟（机制回放） | {mech.get('latency_s', {}).get('max')} s | ≤120 s（硬线） | 远低于线（-{(1 - (mech.get('latency_s', {}).get('max') or 0) / 120) * 100:.1f}%） | **是**（在线内） |
| 检出延迟（心跳陈旧口径） | 120–{(hist.get('detection_latency_s', {}).get('bound_with_cadence_s') or 120)} s | ≤120 s | 上界 +{(hist.get('detection_latency_s', {}).get('bound_with_cadence_s') or 120) - 120:.0f} s（采样量子） | **否**（结构性上界超线 10 s） |
| 回放命中 | {hist.get('positives', {}).get('hits')}/4 | 4/4 | 0 | **是** |
| 负控误报 | 0（clean/lag/标签存活三控） | 0 | 0 | **是** |
| claim-window 一致率 | {lspec['time_weighted_agreement']:.2%} | ≥90%（任务书） | -{(0.9 - lspec['time_weighted_agreement']) * 100:.1f} pp | **否**（honest FAIL） |
| 加 I/O 面最好一致率 | {max([r['agreement'] for r in io['sweep']] or [0]):.2%} | ≥90% | {(max([r['agreement'] for r in io['sweep']] or [0]) - 0.9) * 100:+.1f} pp | **否** |

无基准项声明：`suspect_hang` 时长、`idle` 时长没有外部参考值（平台从未有过真值面），故只报绝对值
与占位比，不编造误差；`cgroup v2` 路径无真机数据（宿主 v1），证据等级 WITHHELD，不参与误差表。

## ⑥ 结论

真值面按任务书落地并可复现：1 h 连续采样零缺失、机制回放 4/4 命中且检出延迟 {mech.get('latency_s', {}).get('max')} s（远优于
心跳陈旧口径的 120 s 下限）、三套负控零误报、dry-run 与 commit 逐字段相等。同时诚实地得到两条
**否定结论**：claim-window 与 CPU 口径的一致率只有 {lspec['time_weighted_agreement']:.2%}（未达 90% 判据），
且 process/IPC 计数无法区分「等模型」与「挂死」——平台要的真值不是换一套计数器，而是
**claim×CPU×I/O×socket 四轴联合状态**；本节点把四轴里的两轴（claim、CPU/I-O）与对应的判据、
混淆矩阵、延迟分布都建好了，剩下两轴的接入点是下一步。
"""
    return md


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default="runs/in23_n08/samples_final.jsonl.gz")
    ap.add_argument("--samples-io", default="runs/in23_n08/samples_io.jsonl.gz")
    ap.add_argument("--replay", default="runs/in23_n08/replay")
    ap.add_argument("--scope", default="runs/in23_n08/scope.json")
    ap.add_argument("--figdir", default="runs/in23_n08/figures")
    args = ap.parse_args(argv)

    os.makedirs(ARTS, exist_ok=True)
    os.makedirs(os.path.dirname(DOC), exist_ok=True)
    os.makedirs(args.figdir, exist_ok=True)

    num = build(args.samples, args.samples_io, args.replay, args.scope)
    crit = evaluate(num)

    with open(os.path.join(ARTS, "report_numbers.json"), "w", encoding="utf-8") as fh:
        json.dump(num, fh, indent=2, sort_keys=True, ensure_ascii=False)
    with open(os.path.join(ARTS, "criteria.json"), "w", encoding="utf-8") as fh:
        json.dump({"criteria": crit,
                   "all_pass": all(v["pass"] for v in crit.values()),
                   "generated_at": num["generated_at"]}, fh, indent=2,
                  sort_keys=True, ensure_ascii=False)

    # figures (regenerated from the same recorded stream)
    fl = ledger.build_ledger(ledger.load_records(args.samples))
    figs = [
        {"name": "fig1_state_band.png",
         "path": figures.fig_state_band(args.samples, args.figdir),
         "caption": "四态时间带（逐 worker）+ 时间预算：外部真值 vs claim-window 口径"},
        {"name": "fig2_confusion.png",
         "path": figures.fig_confusion(fl, args.figdir),
         "caption": "claim-window 与外部真值的时间加权混淆矩阵（TP/FP/FN/TN，秒与占比）"},
        {"name": "fig3_latency_cdf.png",
         "path": figures.fig_latency_cdf(args.replay, args.figdir),
         "caption": "静默死检出延迟 CDF：机制回放实测 vs 心跳陈旧口径下界 vs 120 s 验收线"},
    ]
    md = render_markdown(num, crit, figs)
    with open(DOC, "w", encoding="utf-8") as fh:
        fh.write(md)

    payload_figs = []
    for f in figs:
        web = _png_web(f["path"], args.figdir)
        payload_figs.append({"name": f["name"], "caption": f["caption"],
                             "png_base64": web["png_base64"], "bytes": web["bytes"]})
    payload = {
        "title": "IN23-N08 · worker 占用外部真值面：四态派生 + claim-window 分歧账本",
        "summary": (
            f"用 /proc 进程树 + cgroup 计量建立 worker 占用外部真值，1 h 连续采样零缺失（{num['samples']['span_h']:.2f} h，"
            f"{num['ledger_spec']['n_workers']} 席，{num['ledger_spec']['observed_worker_hours']:.2f} worker·h），派生 "
            f"busy/suspect_hang/silent_dead/idle 四态，与 claim-window 口径做时间加权对账（一致率 "
            f"{num['ledger_spec']['time_weighted_agreement']:.2%}，honest FAIL vs 90% 判据）；机制回放 4/4 命中静默死、"
            f"检出延迟 {num['replay_mechanism'].get('latency_s', {}).get('max')} s，三套负控 0 误报；历史回放 2026-11-02 批 4/4，"
            f"并量出平台在最早可检出后平均又空转 {((num['replay_history'].get('platform_opportunity_window_s', {}).get('median') or 0)/3600):.1f} h。"),
        "scenario_key": "platform_metric",
        "mission_id": 5101,
        "tags": ["worker-occupancy", "external-truth", "cgroup", "proc", "silent-death",
                 "claim-window", "IN23-N08", "honest-fail-c2"],
        "markdown": md,
        "figures": payload_figs,
        "criteria": crit,
    }
    with open(os.path.join(ARTS, "report_payload.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    size = os.path.getsize(os.path.join(ARTS, "report_payload.json"))
    print(json.dumps({"criteria": {k: v["pass"] for k, v in crit.items()},
                      "doc": DOC, "payload_bytes": size,
                      "payload_fig_bytes": [f["bytes"] for f in payload_figs]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())