"""集群侧重跑探针：在干净检出内重放历史节点的工件脚本 / 测试，并与锚定参考值逐项比对。

调用方式（由 ``tools/repro_audit/run_audit.py`` 打包进 `_repro/` 一起 stage 到节点）::

    python3 _repro/probe_runner.py --spec _repro/spec.json --out _repro/out/result.json

流程（每节点一次调用，内部重复 ``reps`` 次 = 漂移可复现性判据 (c) 的「≥2 次验证」）：
1. 从 ``_repro/pristine/`` 恢复所有被重放脚本会覆写的工件（保证两轮起点一致）；
2. 用 ``launch.py``（装入导入面替身）重放工件脚本，读回其 JSON 数值；
3. 用 ``pytest`` 重放该节点新增的测试文件，解析 passed/failed 计数；
4. 与 pristine 参考值逐项比对（``canonical.compare_number_maps``），
   计算两轮之间 drift 路径集合是否一致。

只读：不写库、不联网、不改检出内的源码（唯一写入是被重放脚本自己的工件与 ``_repro/out/``）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import canonical  # noqa: E402

PYTEST_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|warning)")
GEN_TIMEOUT_DEFAULT = 420
PYTEST_TIMEOUT_DEFAULT = 300


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_numbers(path: str) -> Dict[str, float]:
    with open(path, "r", encoding="utf-8") as f:
        return canonical.flatten_numbers(json.load(f))


FAILED_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)", re.M)


def _failed_ids(text: str, limit: int = 60) -> List[str]:
    out: List[str] = []
    for kind, tid in FAILED_RE.findall(text):
        tag = "%s:%s" % (kind, tid)
        if tag not in out:
            out.append(tag)
        if len(out) >= limit:
            break
    return out


def _parse_pytest(text: str) -> Dict[str, Any]:
    out = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
           "xfailed": 0, "xpassed": 0}
    tail = text[-4000:]
    for n, kind in PYTEST_COUNT_RE.findall(tail):
        key = {"error": "errors"}.get(kind, kind)
        if key in out:
            out[key] = int(n)
    m = re.search(r"collected (\d+) item", text)
    if m:
        out["collected"] = int(m.group(1))
    elif "no tests ran" in text:
        out["collected"] = 0
    if out["collected"] == 0 and (out["passed"] or out["failed"]):
        out["collected"] = out["passed"] + out["failed"] + out["skipped"] + out["xfailed"]
    return out


def _env() -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "python": platform.python_version(),
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
        "threads_env": {k: os.environ.get(k) for k in
                        ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
    }
    for mod in ("numpy", "scipy"):
        try:
            m = __import__(mod)
            env[mod] = getattr(m, "__version__", "?")
        except Exception:
            env[mod] = None
    return env


def _stat_sig(path: str):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


SENTINEL_MTIME = 1.0     # 复原后的哨兵时间戳：mtime 变过 = 本轮确实被脚本写出


def _auto_pristine(pristine_dir: str) -> Dict[str, str]:
    """{相对路径: pristine 文件 sha256}——覆盖提交内全部 artifacts JSON。"""
    out: Dict[str, str] = {}
    for root, _dirs, files in os.walk(pristine_dir):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, pristine_dir)
            out[rel] = _sha256_file(full)
    return out


def _auto_compare(pristine_dir: str, signature: Dict[str, str]) -> Dict[str, Any]:
    """跑完后：hash 变了的工件 = 本次重放写出的 → 与 pristine 数值逐项比对。"""
    counts: Dict[str, int] = {"compared": 0, "pass": 0, "drift": 0, "structural": 0,
                              "observed": 0, "diagnostic": 0}
    drift_paths: List[str] = []
    items_head: List[Dict[str, Any]] = []
    written: List[str] = []
    for rel, sha in signature.items():
        cur_path = rel
        if not os.path.exists(cur_path):
            continue
        try:
            if abs(os.stat(cur_path).st_mtime - SENTINEL_MTIME) < 1e-6:
                continue        # 仍带哨兵时间戳 → 本轮没被写出（不冤枉）
        except OSError:
            continue
        written.append(rel)
        try:
            ref = _read_numbers(os.path.join(pristine_dir, rel))
            cur = _read_numbers(cur_path)
        except Exception:
            continue
        cmp = canonical.compare_number_maps(ref, cur)
        for k in counts:
            counts[k] += int(cmp["counts"].get(k, 0))
        counts["max_ulp"] = max(counts.get("max_ulp", 0), int(cmp["counts"].get("max_ulp", 0)))
        counts["max_rel"] = max(counts.get("max_rel", 0.0),
                               float(cmp["counts"].get("max_rel", 0.0) or 0.0))
        for it in cmp["items"]:
            if it["verdict"] == "drift":
                drift_paths.append("%s::%s" % (rel, it["path"]))
                if len(items_head) < 12:
                    items_head.append({
                        "path": "%s::%s" % (rel, it["path"]), "cls": it["cls"],
                        "ref": float("%.12g" % it["ref"]), "cur": float("%.12g" % it["cur"]),
                        "rel": float("%.6g" % it["rel_err"]),
                        "abs": float("%.6g" % it["abs_err"]), "ulp": it["ulp"], "tol": it["tol"]})
        for st in cmp["structural"]:
            drift_paths.append("%s::%s::%s" % (rel, st["path"], st["kind"]))
    return {"counts": counts, "drift_paths": drift_paths[:200],
            "drift_total": len(drift_paths), "items_head": items_head,
            "written": written[:20], "n_written": len(written)}


def _restore(pristine_dir: str, rel: str) -> None:
    src = os.path.join(pristine_dir, rel)
    if os.path.exists(src):
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        shutil.copyfile(src, rel)


def _run(cmd: List[str], timeout: int, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    t0 = time.time()
    e = dict(os.environ)
    e.update(env or {})
    e.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)
        return {"rc": cp.returncode, "secs": round(time.time() - t0, 3),
                "stdout": cp.stdout, "stderr": cp.stderr}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "secs": round(time.time() - t0, 3), "stdout": "",
                "stderr": "TIMEOUT after %ss" % timeout}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="_repro/spec.json")
    ap.add_argument("--out", default="_repro/out/result.json")
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(a.spec))
    with open(a.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)
    reps = int(spec.get("reps", 2))
    gen_timeout = int(spec.get("gen_timeout", GEN_TIMEOUT_DEFAULT))
    py_timeout = int(spec.get("pytest_timeout", PYTEST_TIMEOUT_DEFAULT))
    node_timeout = int(spec.get("node_timeout", 540))
    t_node = time.time()

    def remaining() -> float:
        return node_timeout - (time.time() - t_node)

    pristine = os.path.join(here, "pristine")
    launch = os.path.join(here, "launch.py")

    ref_files: List[Dict[str, Any]] = []
    ref_numbers: Dict[str, Dict[str, float]] = {}
    for probe in spec.get("numeric", []):
        for rel in probe.get("outputs", []):
            p = os.path.join(pristine, rel)
            if not os.path.exists(p):
                continue
            nums = _read_numbers(p)
            ref_numbers[rel] = nums
            ref_files.append({
                "path": rel,
                "sha256": _sha256_file(p),
                "n_numbers": len(nums),
                "numbers_sha256": _sha256_bytes(
                    json.dumps([[k, canonical.quantize(v)] for k, v in sorted(nums.items())]
                               ).encode("utf-8")),
                "generator": probe.get("script"),
            })

    result: Dict[str, Any] = {
        "mission_id": spec.get("mission_id"),
        "commit": spec.get("commit"),
        "env": _env(),
        "import_stubs": spec.get("import_stubs", []),
        "reps_requested": reps,
        "ref": {"files": ref_files},
        "runs": [],
        "blocked_reason": None,
    }

    if not ref_numbers and not spec.get("pytest"):
        result["blocked_reason"] = "no_executable_artifact"
        _write(a.out, result)
        return 0

    drift_sets: List[set] = []
    for rep in range(1, reps + 1):
        run: Dict[str, Any] = {"rep": rep, "gen": [], "pytest": None, "duration_sec": 0.0}
        t0 = time.time()
        auto_sig = _auto_pristine(pristine) if spec.get("auto_outputs") else {}
        for rel in (auto_sig if auto_sig else ref_numbers):
            _restore(pristine, rel)
            if auto_sig:
                try:
                    os.utime(rel, (SENTINEL_MTIME, SENTINEL_MTIME))
                except OSError:
                    pass
        for probe in spec.get("numeric", []):
            script = probe["script"]
            env_over = probe.get("env") or {}
            outs = list(probe.get("outputs", []))
            sig_before = {rel: _stat_sig(rel) for rel in outs}
            left = remaining()
            if left <= 20:
                entry_skip = {"script": script, "rc": 125, "secs": 0.0,
                              "error": "node_time_budget_exhausted", "counts": {},
                              "drift_paths": [], "drift_total": 0}
                run["gen"].append(entry_skip)
                continue
            info = _run([sys.executable, launch, script], int(min(gen_timeout, left)), env_over)
            entry: Dict[str, Any] = {"script": script, "rc": info["rc"], "secs": info["secs"]}
            if info["rc"] != 0:
                entry["error"] = (info["stderr"] or info["stdout"])[-600:]
                entry["counts"] = {"compared": 0, "pass": 0, "drift": 0, "structural": 0}
                entry["drift_paths"] = []
                entry["drift_total"] = 0
                run["gen"].append(entry)
                continue
            counts: Dict[str, int] = {"compared": 0, "pass": 0, "drift": 0, "structural": 0,
                                      "observed": 0, "diagnostic": 0}
            drift_paths: List[str] = []
            items_head: List[Dict[str, Any]] = []
            missing_outputs: List[str] = []
            not_written: List[str] = []
            if auto_sig:
                auto = _auto_compare(pristine, auto_sig)
                for k in counts:
                    counts[k] += int(auto["counts"].get(k, 0))
                counts["max_ulp"] = max(int(counts.get("max_ulp", 0)),
                                        int(auto["counts"].get("max_ulp", 0)))
                counts["max_rel"] = max(float(counts.get("max_rel", 0.0)),
                                        float(auto["counts"].get("max_rel", 0.0) or 0.0))
                drift_paths.extend(auto["drift_paths"])
                items_head.extend(auto["items_head"][:max(0, 12 - len(items_head))])
                entry.update({"counts": counts, "drift_paths": drift_paths[:200],
                              "drift_total": len(drift_paths), "items_head": items_head,
                              "written": auto["written"], "n_written": auto["n_written"],
                              "outputs_not_written": []})
                run["gen"].append(entry)
                continue
            for rel in outs:
                cur_path = rel
                if not os.path.exists(cur_path):
                    missing_outputs.append(rel)
                    continue
                if _stat_sig(cur_path) == sig_before.get(rel):
                    # 脚本没碰这个工件 → 不能算「复现成功」（防止误挂的生成器造成假 pass）
                    not_written.append(rel)
                    continue
                cur = _read_numbers(cur_path)
                cmp = canonical.compare_number_maps(ref_numbers.get(rel, {}), cur)
                for k in counts:
                    counts[k] += int(cmp["counts"].get(k, 0))
                for it in cmp["items"]:
                    if it["verdict"] == "drift":
                        drift_paths.append("%s::%s" % (rel, it["path"]))
                        if len(items_head) < 12:
                            items_head.append({
                                "path": "%s::%s" % (rel, it["path"]), "cls": it["cls"],
                                "ref": float("%.12g" % it["ref"]),
                                "cur": float("%.12g" % it["cur"]),
                                "rel": float("%.6g" % it["rel_err"]),
                                "abs": float("%.6g" % it["abs_err"]),
                                "ulp": it["ulp"], "tol": it["tol"],
                            })
                for s in cmp["structural"]:
                    drift_paths.append("%s::%s::%s" % (rel, s["path"], s["kind"]))
                    if len(items_head) < 12:
                        items_head.append({"path": "%s::%s" % (rel, s["path"]),
                                           "cls": "structural", "kind": s["kind"]})
            entry.update({"counts": counts, "drift_paths": drift_paths[:200],
                          "drift_total": len(drift_paths), "items_head": items_head,
                          "missing_outputs": missing_outputs, "outputs_not_written": not_written})
            run["gen"].append(entry)
        if spec.get("pytest"):
            pinfo = _run([sys.executable, "-m", "pytest", *spec["pytest"], "-q", "--tb=line",
                          "-rfE", "-p", "no:cacheprovider"],
                         int(max(30, min(py_timeout, remaining()))))
            counts = _parse_pytest(pinfo["stdout"] + pinfo["stderr"])
            collected = max(counts["collected"], counts["passed"] + counts["failed"]
                            + counts["errors"] + counts["xfailed"])
            regressions = counts["failed"] + counts["errors"]
            run["pytest"] = {"targets": spec["pytest"], "rc": pinfo["rc"], "secs": pinfo["secs"],
                             **counts, "collected": collected, "regressions": regressions,
                             "failed_ids": _failed_ids(pinfo["stdout"] + pinfo["stderr"]),
                             "tail": (pinfo["stdout"] + pinfo["stderr"])[-1500:].strip()}
        run["duration_sec"] = round(time.time() - t0, 2)
        drift_sets.append({p for g in run["gen"] for p in g.get("drift_paths", [])})
        result["runs"].append(run)

    if len(drift_sets) >= 2:
        result["drift_consistent"] = sorted(set.intersection(*drift_sets))
        result["drift_inconsistent"] = sorted(set.union(*drift_sets) - set.intersection(*drift_sets))
    else:
        result["drift_consistent"] = sorted(drift_sets[0]) if drift_sets else []
        result["drift_inconsistent"] = []

    ran = any(g.get("counts", {}).get("compared", 0) > 0
              for run in result["runs"] for g in run["gen"])
    ran = ran or any((run.get("pytest") or {}).get("collected", 0) > 0 for run in result["runs"])
    if not ran:
        touched = any(g.get("counts", {}).get("compared", 0) > 0 or g.get("drift_total", 0) > 0
                      for run in result["runs"] for g in run["gen"])
        if not touched and any(g.get("outputs_not_written") for run in result["runs"]
                               for g in run["gen"]):
            result["blocked_reason"] = ("generator_did_not_write_outputs: "
                                        + ", ".join(g["outputs_not_written"][0]
                                                    for run in result["runs"] for g in run["gen"]
                                                    if g.get("outputs_not_written"))[:200])
            _write(a.out, result)
            return 0
        errs = [g.get("error", "")[:200] for run in result["runs"] for g in run["gen"]]
        result["blocked_reason"] = ("probe_produced_no_numbers: " + (errs[0] if errs else "unknown"))
    _write(a.out, result)
    return 0


def _write(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    # stdout 走**紧凑**序列化：集群通道只回传尾部 ~8 KB，缩进版会被截断
    compact = dict(obj)
    for run in compact.get("runs", []):
        py = run.get("pytest")
        if py:
            py["tail"] = (py.get("tail") or "")[-500:]
        for g in run.get("gen", []):
            if g.get("error"):
                g["error"] = g["error"][-300:]
            g["items_head"] = g.get("items_head", [])[:12]
            g["drift_paths"] = g.get("drift_paths", [])[:60]
    print("###REPRO_JSON###")
    print(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
    print("###REPRO_END###")


if __name__ == "__main__":
    raise SystemExit(main())