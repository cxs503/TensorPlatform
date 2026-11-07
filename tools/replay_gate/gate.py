#!/usr/bin/env python3
"""已交付声明复现门（Replay Gate）——机制 + 判据 + 落盘产物。

门在做什么
----------
「已交付」= 平台对外宣称过的数值（台账/正典/交付实现）。本门不看申报文字，只做：

1. **分层抽样**：从 :mod:`tools.replay_gate.pool` 的申报池里按域（阻力/结构/耐波/
   LBM/FEM/DEM）各抽 4 条，合计 24 条；固定 seed → 清单确定；清单 sha256 落盘，
   换个池/换个 seed 复现率不可跨版本比较。
2. **判据解析**：每条申报带 ``kernel + params + tol_rel``，判据必须能被解析成
   「干净环境里可执行的一段计算」，否则归入 ``CRITERION_NOT_EXECUTABLE``。
3. **干净环境复跑**：``python3 -m venv --without-pip``（零第三方包）+ ``-I``（隔离
   PYTHONPATH/用户 site）子进程复跑；同一份代码在系统环境再跑一遍做对照，
   环境依赖导致的缺口因此**可测量**而不是靠猜。
4. **复现率 + 95% 置信区间**：复现率 = numerator/denominator，Wilson 区间（小样本
   比正态近似稳，n=24 时不会给出越界区间）。
5. **五类根因账本**：``ENV_DEPENDENCY_MISSING`` / ``RANDOM_UNSEEDED`` /
   ``ARTIFACT_MISSING`` / ``CRITERION_NOT_EXECUTABLE`` / ``TRUE_NUMERICAL_DRIFT``，
   每类记定义 + 命中 claims；``Σ 各类计数 == denominator - numerator``（可机器对账）。
6. **篡改注入**：把某条申报值篡改 5% 后重跑，门必须判**不可复现**且归入
   ``TRUE_NUMERICAL_DRIFT``，并给出「篡改前复现、篡改后不复现」的因果证据。

用法
----
    python3 tools/replay_gate/gate.py --run                 # 干净 venv 主跑
    python3 tools/replay_gate/gate.py --run --both          # 干净 venv + 系统环境对照
    python3 tools/replay_gate/gate.py --run --tamper first_within:resistance
    python3 tools/replay_gate/gate.py --check               # 门绿/红（退出码 0/1）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.replay_gate import pool as POOL  # noqa: E402

OUT_DIR = ROOT / "artifacts" / "replay_gate"
KERNELS_SRC = Path(__file__).resolve().parent / "kernels.py"
DEFAULT_SEED = 20261106
SAMPLE_PER_DOMAIN = 4
MIN_RATE = 0.90          # 门判绿门槛（复现率）
Z95 = 1.959963984540054

ROOT_CAUSES = {
    "ENV_DEPENDENCY_MISSING": "判据声明的第三方依赖在目标环境不可导入（干净 venv 无 numpy 等）",
    "RANDOM_UNSEEDED": "判据含未种子的随机过程，两次干净复跑给出不同结果",
    "ARTIFACT_MISSING": "申报值依赖的工件（artifact path）不存在或不可读",
    "CRITERION_NOT_EXECUTABLE": "判据无法执行（内核未登记/异常退出/输出不可解析）",
    "TRUE_NUMERICAL_DRIFT": "判据可执行、工件齐全，但复算值与申报值偏差 > tol（真实数值漂移）",
}

_RUNNER = '''import json, sys
sys.path.insert(0, sys.argv[1])
import kernels
claim = json.loads(sys.argv[2])
value = kernels.compute(claim["kernel"], claim.get("params") or {})
print("RESULT " + json.dumps({"value": value}))
'''

_CTX: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rel(path: Path) -> str:
    """仓内相对路径；不在仓内（演练/临时目录）时返回绝对路径。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve()))
    except Exception:
        return str(path)


def wilson_ci(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """二项比例 Wilson 95% 置信区间（小样本不越界）。"""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = max(0.0, (centre - half) / denom)
    hi = min(1.0, (centre + half) / denom)
    return (round(lo, 6), round(hi, 6))


# ── 干净环境 ────────────────────────────────────────────────────────────
def clean_python() -> str:
    """零第三方包的 venv 解释器（按 Python 版本缓存，创建一次复用）。"""
    key = f"py{sys.version_info.major}{sys.version_info.minor}"
    if key in _CTX:
        return _CTX[key]
    venv = Path(tempfile.gettempdir()) / f"replay_gate_venv_{key}"
    py = venv / "bin" / "python"
    if not py.is_file():
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                       check=True, capture_output=True, timeout=300)
    _CTX[key] = str(py)
    return str(py)


def _runner_dir() -> str:
    if "runner" in _CTX:
        return _CTX["runner"]
    d = Path(tempfile.mkdtemp(prefix="replay_gate_runner_"))
    shutil.copy2(KERNELS_SRC, d / "kernels.py")
    (d / "_runner.py").write_text(_RUNNER, encoding="utf-8")
    _CTX["runner"] = str(d)
    return str(d)


def _module_available(module: str, python: str, isolated: bool) -> bool:
    code = ("import importlib, sys\n"
            "try:\n    importlib.import_module(sys.argv[1])\n"
            "except Exception:\n    sys.exit(3)\n")
    argv = [python] + (["-I"] if isolated else []) + ["-c", code, module]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


def _run_kernel(claim: dict, python: str, isolated: bool) -> dict:
    d = _runner_dir()
    argv = [python] + (["-I"] if isolated else []) + [str(Path(d) / "_runner.py"), d,
                                                      json.dumps(claim, ensure_ascii=False)]
    t0 = time.perf_counter()
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"status": "error", "detail": "TIMEOUT", "stderr": "", "ms": None}
    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    if r.returncode != 0:
        return {"status": "error", "detail": f"exit={r.returncode}", "stderr": r.stderr[-2000:], "ms": ms}
    for line in r.stdout.splitlines():
        if line.startswith("RESULT "):
            try:
                return {"status": "ok", "value": float(json.loads(line[7:])["value"]),
                        "stderr": r.stderr[-500:], "ms": ms}
            except Exception as exc:
                return {"status": "error", "detail": f"BAD_OUTPUT:{exc}", "stderr": r.stderr[-800:], "ms": ms}
    return {"status": "error", "detail": "NO_RESULT_LINE", "stderr": r.stderr[-800:], "ms": ms}


# ── 单条申报的复现判定 ──────────────────────────────────────────────────
def replay_claim(claim: dict, use_clean_venv: bool = True) -> dict:
    """跑一条申报：判 reproduced / root_cause。分类顺序=先环境、后数值。"""
    python = clean_python() if use_clean_venv else sys.executable
    isolated = bool(use_clean_venv)
    row = {
        "id": claim.get("id"),
        "domain": claim.get("domain"),
        "title": claim.get("title", ""),
        "kernel": claim.get("kernel"),
        "declared": claim.get("declared"),
        "computed": None,
        "rel_err": None,
        "tol_rel": claim.get("tol_rel"),
        "reproduced": False,
        "root_cause": None,
        "detail": "",
        "ms": None,
        "provenance": (claim.get("provenance") or {}).get("kind"),
        "env": "clean-venv" if use_clean_venv else "system",
    }

    # 1) 工件缺失
    art = claim.get("artifact") or None
    if isinstance(art, str):            # 允许简写：artifact 直接给相对路径
        art = {"path": art}
    if art and art.get("path") and not (ROOT / art["path"]).is_file():
        row["root_cause"] = "ARTIFACT_MISSING"
        row["detail"] = f"工件不存在: {art['path']}"
        return row

    # 2) 环境依赖缺失
    missing = [m for m in (claim.get("requires") or [])
               if not _module_available(m, python, isolated)]
    if missing:
        row["root_cause"] = "ENV_DEPENDENCY_MISSING"
        row["detail"] = "缺少依赖: " + ", ".join(missing)
        return row

    # 3) 判据可执行性
    out = _run_kernel(claim, python, isolated)
    row["ms"] = out.get("ms")
    if out["status"] != "ok":
        low = (out.get("stderr") or "")
        if "ModuleNotFoundError" in low or "ImportError" in low:
            row["root_cause"] = "ENV_DEPENDENCY_MISSING"
        else:
            row["root_cause"] = "CRITERION_NOT_EXECUTABLE"
        row["detail"] = f"{out.get('detail')}: {low.strip().splitlines()[-1] if low.strip() else ''}"
        return row
    computed = out["value"]
    row["computed"] = computed

    # 4) 未种子随机：两次独立复跑不一致
    if claim.get("stochastic") and claim.get("seed") is None:
        out2 = _run_kernel(claim, python, isolated)
        if out2["status"] != "ok" or abs(out2["value"] - computed) > 0:
            row["root_cause"] = "RANDOM_UNSEEDED"
            row["detail"] = f"两次复跑 {computed} vs {out2.get('value')}"
            return row

    # 5) 真实数值漂移
    declared = claim.get("declared")
    tol = float(claim.get("tol_rel") or 0.0)
    denom = abs(declared) if declared not in (None, 0) else 1.0
    rel = abs(computed - float(declared)) / denom
    row["rel_err"] = rel
    if rel > tol:
        row["root_cause"] = "TRUE_NUMERICAL_DRIFT"
        row["detail"] = f"|Δ|/|declared|={rel:.4%} > tol={tol:.2%}"
        return row
    row["reproduced"] = True
    return row


# ── 五类根因的可裁判自检（每次门跑都做，0 失败的账本也要能被证明是可判的） ──
SELF_CHECK_CLAIMS = {
    # 用**必然不存在**的依赖名，使该类在干净 venv 与系统环境下都可判（不依赖 numpy 是否装）
    "ENV_DEPENDENCY_MISSING": {"id": "sc-env", "domain": "lbm", "kernel": "numpy_only",
                               "params": {}, "declared": 0.0, "tol_rel": 0.03,
                               "requires": ["__absent_dependency_probe__"]},
    "RANDOM_UNSEEDED": {"id": "sc-rnd", "domain": "dem", "kernel": "stochastic_probe",
                        "params": {}, "declared": 0.0, "tol_rel": 0.03,
                        "stochastic": True, "seed": None},
    "ARTIFACT_MISSING": {"id": "sc-art", "domain": "structure", "kernel": "plate_buckling_sigma_cr",
                         "params": {"t": 0.012, "b": 0.6}, "declared": 1.0, "tol_rel": 0.03,
                         "artifact": {"path": "artifacts/replay_gate/__absent_artifact__.json",
                                      "sha256": "0" * 64}},
    "CRITERION_NOT_EXECUTABLE": {"id": "sc-crit", "domain": "fem", "kernel": "no_such_kernel",
                                 "params": {}, "declared": 1.0, "tol_rel": 0.03},
    "TRUE_NUMERICAL_DRIFT": {"id": "sc-drift", "domain": "resistance", "kernel": "froude",
                             "params": {"V": 3.0, "L": 4.356}, "declared": 0.90, "tol_rel": 0.03},
}


def self_check(use_clean_venv: bool = True) -> dict:
    """对五类根因各造一条**已知答案**的负控申报，验证门能把它们分别归类。"""
    out: dict = {}
    for cls, claim in SELF_CHECK_CLAIMS.items():
        row = replay_claim(claim, use_clean_venv=use_clean_venv)
        out[cls] = {"claim": claim["id"], "classified_as": row["root_cause"],
                    "ok": row["root_cause"] == cls, "detail": row["detail"]}
    return out


def env_dependency_probe() -> dict:
    """环境依赖缺口的实测：同一判据在「干净 venv」与「系统环境」各跑一次。"""
    claim = {"id": "probe-numpy", "domain": "lbm", "kernel": "numpy_only", "params": {},
             "declared": 0.0, "tol_rel": 0.03}
    clean = replay_claim(claim, use_clean_venv=True)
    system = replay_claim(claim, use_clean_venv=False)
    return {"claim": claim["id"],
            "clean_venv": {"root_cause": clean["root_cause"], "reproduced": clean["reproduced"]},
            "system": {"root_cause": system["root_cause"], "reproduced": system["reproduced"]},
            "gap_demonstrated": (not clean["reproduced"]) and system["reproduced"]}


# ── 抽样 ────────────────────────────────────────────────────────────────
def build_sample(seed: int = DEFAULT_SEED, pool: dict | None = None) -> dict:
    """分层抽样：每域抽 ``SAMPLE_PER_DOMAIN`` 条（域内按 id 排序后 Random 抽样）。"""
    import random

    pool = pool or POOL.build_pool()
    picked: list[dict] = []
    for domain in POOL.DOMAINS:
        rows = sorted((c for c in pool["claims"] if c["domain"] == domain), key=lambda c: c["id"])
        if len(rows) < SAMPLE_PER_DOMAIN:
            raise ValueError(f"域 {domain} 申报不足 {SAMPLE_PER_DOMAIN} 条")
        rng = random.Random(f"{seed}:{domain}")
        picked.extend(rng.sample(rows, SAMPLE_PER_DOMAIN))
    picked.sort(key=lambda c: c["id"])
    fingerprint = json.dumps(
        [{"id": c["id"], "declared": c["declared"], "tol_rel": c["tol_rel"],
          "kernel": c["kernel"], "params": c["params"]} for c in picked],
        ensure_ascii=False, sort_keys=True).encode()
    return {
        "sample_version": "replay-sample/1.0",
        "seed": seed,
        "n": len(picked),
        "per_domain": SAMPLE_PER_DOMAIN,
        "domains": list(POOL.DOMAINS),
        "pool_sha256": pool["sha256"],
        "pool_n": pool["n"],
        "sha256": hashlib.sha256(fingerprint).hexdigest(),
        "generated_at": _now(),
        "claims": picked,
    }


def write_manifest(sample: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sample, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


# ── 篡改注入 ────────────────────────────────────────────────────────────
def _tamper(sample: dict, spec: str | None) -> tuple[dict, list[dict]]:
    """``spec='first_within:<domain>'``：把该域抽样里 id 最小的申报值篡改 ×1.05。"""
    if not spec:
        return sample, []
    if ":" not in spec:
        raise ValueError("tamper 格式应为 first_within:<domain>")
    mode, domain = spec.split(":", 1)
    if mode != "first_within":
        raise ValueError(f"未知篡改模式 {mode}")
    cands = [c for c in sample["claims"] if c["domain"] == domain]
    if not cands:
        raise ValueError(f"域 {domain} 没有抽样申报")
    # 优先选非 legacy 申报：篡改前必须复现，篡改后必不复现，因果才成立
    target = sorted(cands, key=lambda c: (str((c.get("provenance") or {}).get("kind")) ==
                                          "repo_artifact_legacy", c["id"]))[0]
    tampered = json.loads(json.dumps(sample))
    for c in tampered["claims"]:
        if c["id"] == target["id"]:
            c["declared"] = float(c["declared"]) * 1.05
            c["_tampered_from"] = target["declared"]
    return tampered, [target]


# ── 主跑 ────────────────────────────────────────────────────────────────
def run_gate(seed: int = DEFAULT_SEED, out_dir: str | Path | None = None,
             use_clean_venv: bool = True, tamper: str | None = None,
             pool: dict | None = None, write: bool = True) -> dict:
    out_dir = Path(out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    pool = pool or POOL.build_pool()
    sample = build_sample(seed, pool)
    tampered_sample, tamper_targets = _tamper(sample, tamper)
    if write:
        write_manifest(sample, out_dir / "sample_manifest.json")

    results = [replay_claim(c, use_clean_venv=use_clean_venv) for c in tampered_sample["claims"]]
    n = len(results)
    k = sum(1 for r in results if r["reproduced"])
    failures = [r for r in results if not r["reproduced"]]
    counts = Counter(r["root_cause"] for r in failures)
    ledger = {
        cls: {"count": counts.get(cls, 0),
              "definition": definition,
              "claims": [r["id"] for r in failures if r["root_cause"] == cls]}
        for cls, definition in ROOT_CAUSES.items()
    }

    per_domain = {}
    for d in POOL.DOMAINS:
        rows = [r for r in results if r["domain"] == d]
        kk = sum(1 for r in rows if r["reproduced"])
        per_domain[d] = {"n": len(rows), "reproduced": kk,
                         "rate": round(kk / len(rows), 4) if rows else None}

    # 篡改因果证据：同一申报的未篡改版本必须复现，篡改后必须不复现
    tamper_evidence = {"injected": bool(tamper_targets), "spec": tamper,
                       "claims": [t["id"] for t in tamper_targets],
                       "detected": None, "evidence": []}
    for t in tamper_targets:
        base = replay_claim(t, use_clean_venv=use_clean_venv)
        tam = next(r for r in results if r["id"] == t["id"])
        tamper_evidence["evidence"].append({
            "id": t["id"],
            "declared_original": t["declared"],
            "declared_tampered": tam["declared"],
            "baseline_reproduced": base["reproduced"],
            "tampered_reproduced": tam["reproduced"],
            "tampered_root_cause": tam["root_cause"],
        })
    if tamper_targets:
        tamper_evidence["detected"] = all(
            (not e["tampered_reproduced"]) and e["tampered_root_cause"] == "TRUE_NUMERICAL_DRIFT"
            and e["baseline_reproduced"] for e in tamper_evidence["evidence"])

    sc = self_check(use_clean_venv=use_clean_venv)
    probe = env_dependency_probe()
    lo, hi = wilson_ci(k, n)
    failed_ms = sum(r["ms"] or 0.0 for r in results)
    rate = k / n
    criteria = {
        "rate_ge_min": rate >= MIN_RATE,
        "no_true_numerical_drift": ledger["TRUE_NUMERICAL_DRIFT"]["count"] == 0,
        "tamper_detected": (tamper_evidence["detected"] if tamper_targets else True),
        "five_root_causes_adjudicable": all(v["ok"] for v in sc.values()),
    }
    verdict_reasons = [f"{name} 不满足" for name, ok in criteria.items() if not ok]
    verdict = "PASS" if all(criteria.values()) else "FAIL"

    report = {
        "gate": "delivered-claim-replay-gate",
        "version": "replay-gate/1.0",
        "verdict": verdict,
        "verdict_criteria": criteria,
        "verdict_reasons": verdict_reasons,
        "min_rate": MIN_RATE,
        "environment": {
            "clean_venv": use_clean_venv,
            "python": (clean_python() if use_clean_venv else sys.executable),
            "python_version": sys.version.split()[0],
            "isolation": "-I" if use_clean_venv else "none",
            "note": "clean venv = python3 -m venv --without-pip，零第三方包",
        },
        "seed": seed,
        "pool": {"n": pool["n"], "per_domain": pool["per_domain"], "sha256": pool["sha256"]},
        "sample": {"n": sample["n"], "sha256": sample["sha256"],
                   "manifest": _rel(out_dir / "sample_manifest.json") if write else None},
        "denominator": n,
        "numerator": k,
        "reproduction_rate": round(k / n, 6),
        "ci95": [lo, hi],
        "ci95_method": "Wilson score interval, z=1.96",
        "root_cause_ledger": ledger,
        "self_check": sc,
        "env_dependency_probe": probe,
        "root_cause_total": sum(v["count"] for v in ledger.values()),
        "per_domain": per_domain,
        "tamper": tamper_evidence,
        "runtime_ms_total_claims": round(failed_ms, 1),
        "results": results,
        "generated_at": _now(),
    }
    if write:
        name = "replay_report.json"
        if not use_clean_venv:
            name = "replay_report_systemenv.json"
        if tamper:
            name = name.replace(".json", "_tampered.json")
        (out_dir / name).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
        # 抽样清单与判据池同步落盘，供审计链复算
        POOL.write_pool(pool, out_dir / "claims_pool.json")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="已交付声明复现门")
    ap.add_argument("--run", action="store_true", help="跑复现门")
    ap.add_argument("--both", action="store_true", help="干净 venv + 系统环境各跑一遍")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--tamper", default=None, help="注入篡改（first_within:<domain>）")
    ap.add_argument("--check", action="store_true", help="门判绿/红（退出码 0/1）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not (args.run or args.check or args.json):
        ap.print_help()
        return 0

    if args.both:
        clean = run_gate(args.seed, args.out, use_clean_venv=True, tamper=args.tamper)
        sysenv = run_gate(args.seed, args.out, use_clean_venv=False, tamper=args.tamper)
        for tag, rep in (("clean-venv", clean), ("system", sysenv)):
            print(f"[{tag}] rate={rep['reproduction_rate']:.3%} "
                  f"ci95={rep['ci95']} verdict={rep['verdict']} "
                  f"failures={rep['denominator'] - rep['numerator']}")
        gap = sysenv["reproduction_rate"] - clean["reproduction_rate"]
        print(f"环境依赖缺口(系统-干净) = {gap:+.3%}")
        return 0 if clean["verdict"] == "PASS" else 1

    rep = run_gate(args.seed, args.out, use_clean_venv=True, tamper=args.tamper)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(f"verdict={rep['verdict']} rate={rep['reproduction_rate']:.3%} "
              f"numerator={rep['numerator']}/{rep['denominator']} ci95={rep['ci95']}")
        for cls, v in rep["root_cause_ledger"].items():
            if v["count"]:
                print(f"  {cls}: {v['count']} -> {', '.join(v['claims'])}")
        for d, v in rep["per_domain"].items():
            print(f"  [{POOL.DOMAIN_ZH[d]}] {v['reproduced']}/{v['n']} = {v['rate']:.0%}")
        if rep["tamper"]["injected"]:
            print(f"  篡改注入 {rep['tamper']['claims']} detected={rep['tamper']['detected']}")
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())