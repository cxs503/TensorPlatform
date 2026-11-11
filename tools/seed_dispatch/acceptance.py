"""验收编排：真实日志 → 记录 → 排序评估 → off-policy → 泄漏审计 → 种子排序 → 判据 (a)~(g)。

产物（``--out`` 目录）
----------------------
``eval_report.json``  判据数字 + 产物路径 + 溯源链（机器可算验收）
``eval_report.md``    论文级报告（数字全部程序化注入）
``seeds_ranked.json`` 种子 EVOI 排名 + 每条溯源链
``fig_*.png``         四张图
``MANIFEST.json``     上述每个文件的 sha256

纪律
----
* 判据 (f)/(g) 只认真跑：pytest / coverage / 域词扫描都以子进程真实退出码为准，缺文件/未跑=不通过。
* 判据 (b) 需要真值标注或人工抽检，两者都缺 = 不通过（n/a 不许静默通过）。
* 任一判据不通过 → ``all_pass=False``，报告如实写（honest FAIL 不可覆盖）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .integration import (
    assemble_gates,
    audit_leakage,
    corpus_summary,
    load_distill_bundle,
    rank_seeds,
    seed_candidates,
)
from .log import build_records, load_snapshot, snapshot_sha256, summarise
from .params import SeedDispatchParams, load_params
from .policy import build_slates, offpolicy_summary, walk_forward

__all__ = ["run", "check_pytest", "check_coverage", "check_domain_scan", "check_cluster_truth"]

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = "tools/seed_dispatch"
_TESTS = ("tests/test_seed_dispatch_core.py tests/test_seed_dispatch_integration.py "
          "tests/test_seed_dispatch_surface.py")


def _tail(text: str, n: int = 4) -> list[str]:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-n:]


def check_pytest(tests: str = _TESTS, timeout: int = 900) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", "-q", *tests.split()]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT), timeout=timeout)
    return {
        "pass": proc.returncode == 0,
        "returncode": int(proc.returncode),
        "command": " ".join(cmd),
        "tail": _tail(proc.stdout + proc.stderr),
    }


def check_coverage(source: str = _MODULE, tests: str = _TESTS,
                   minimum: float = 0.85, timeout: int = 1800) -> dict[str, Any]:
    report_path = _ROOT / "runs" / "p318w2" / "n10" / "coverage.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "coverage", "run", f"--source={source}",
        f"--data-file={report_path.with_suffix('.data')}", "-m", "pytest", "-q", *tests.split(),
    ]
    run = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT), timeout=timeout)
    if run.returncode != 0:
        return {"value": 0.0, "ok": False, "reason": "coverage run failed",
                "tail": _tail(run.stdout + run.stderr)}
    rep = subprocess.run(
        [sys.executable, "-m", "coverage", "json", f"--data-file={report_path.with_suffix('.data')}",
         "-o", str(report_path), "--quiet"],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=timeout,
    )
    if not report_path.is_file():
        return {"value": 0.0, "ok": False, "reason": "coverage json missing",
                "tail": _tail(rep.stdout + rep.stderr)}
    data = json.loads(report_path.read_text(encoding="utf-8"))
    totals = data.get("totals") or {}
    pct = float(totals.get("percent_covered") or 0.0) / 100.0
    return {
        "value": round(pct, 6),
        "threshold": minimum,
        "ok": bool(pct >= minimum),
        "statements": totals.get("num_statements"),
        "missing": totals.get("missing_lines"),
        "report": str(report_path),
    }


def check_domain_scan(paths: Sequence[str] | None = None) -> dict[str, Any]:
    """域词扫描：给定路径 → 落 JSON 摘要 → 读回命中/实扫文件数。

    判据要求「命中 0 且路径全部被实扫」：路径写错或文件被删时 ``files_missing`` 非空 → 判负
    （反空转：扫描器对不存在的路径是静默跳过，不读摘要就会假绿）。
    """
    paths = list(paths or [f"{_MODULE}", *_TESTS.split()])
    out = _ROOT / "runs" / "p318w2" / "n10" / "domain_scan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "tools/audit/domain_leak_scan.py", "--paths", *paths,
           "--json", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT), timeout=600)
    payload: dict[str, Any] = {}
    if out.is_file():
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    hits = payload.get("hits") or payload.get("matches") or []
    scanned = payload.get("files_scanned") or []
    missing = payload.get("files_missing") or []
    # 目录会展开成多个文件，故不比较 len(scanned)==len(paths)，只要求「零命中 + 零缺失 + 有实扫」
    return {
        "ok": bool(proc.returncode == 0 and not hits and not missing and len(scanned) > 0),
        "returncode": int(proc.returncode),
        "hits": hits[:10],
        "hit_count": len(hits),
        "files_scanned": len(scanned),
        "paths": len(paths),
        "files_missing": missing,
        "tail": _tail(proc.stdout + proc.stderr),
    }


def check_cluster_truth(fixture_dir: str | None = None, out_dir: str | None = None) -> dict[str, Any]:
    """判据 (b)：用带真值的合成夹具跑蒸馏，读回簇一致率。"""
    base = _ROOT / "tests" / "fixtures" / "knowledge_distill"
    reports = Path(fixture_dir or base / "reports")
    truth = base / "truth_items.json"
    if not reports.exists() or not truth.exists():
        return {"value": 0.0, "judged": 0, "ok": False,
                "reason": "truth-labelled fixture missing"}
    out = Path(out_dir or (_ROOT / "runs" / "p318w2" / "n10" / "distill_fixture"))
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "tools.knowledge_distill.cli",
        "--reports", str(reports),
        "--references", str(base / "reference"),
        "--truth-items", str(truth),
        "--out", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ROOT), timeout=900)
    run_path = out / "run.json"
    if not run_path.is_file():
        return {"value": 0.0, "judged": 0, "ok": False, "reason": "fixture run produced no run.json",
                "tail": _tail(proc.stdout + proc.stderr)}
    data = json.loads(run_path.read_text(encoding="utf-8"))
    acc = (data.get("acceptance") or {}).get("b_cluster_consistency") or {}
    return {
        "value": round(float(acc.get("value") or 0.0), 6),
        "judged": int(acc.get("clusters_judged") or 0),
        "judged_by": acc.get("judged_by") or "truth_labels",
        "threshold": 0.8,
        "ok": bool(acc.get("ok")),
        "source": str(run_path),
        "tail": _tail(proc.stdout + proc.stderr),
    }


def run(
    *,
    snapshot: str,
    distill_dir: str,
    out: str = "runs/p318w2/n10",
    params: SeedDispatchParams | None = None,
    with_checks: bool = True,
    with_figures: bool = True,
) -> dict[str, Any]:
    started = time.time()
    p = params or SeedDispatchParams()
    raw = load_snapshot(snapshot)
    build_kwargs = {
        "novelty_pool": p.novelty_pool,
        "novelty_alpha": p.novelty_alpha,
        "token_min_len": p.token_min_len,
        "cjk_ngram": p.cjk_ngram,
        "hash_bits": p.hash_bits,
    }
    records = build_records(raw, **build_kwargs)
    print(f"[1/6] records={len(records)} labelled={sum(1 for r in records if r.label is not None)}")
    walk = walk_forward(
        records,
        windows=p.windows,
        top_frac=p.top_frac,
        min_train=p.min_train,
        ridge=p.ridge,
        epochs=p.epochs,
        lr=p.lr,
        random_repeats=p.random_repeats,
        gain_floor=p.gain_floor,
    )
    print(f"[2/6] precision@20% evoi={walk['precision_weighted']['evoi']:.4f} "
          f"fifo={walk['precision_weighted']['fifo']:.4f} gain={walk['gain_vs_fifo_pp']:+.2f}pp")
    labelled = [r for r in records if r.label is not None]
    scores = _forward_scores(records, labelled, p)
    slates = build_slates(
        labelled, scores, hours=p.slate_hours, min_slate=p.min_slate,
        fifo_beta=p.fifo_beta, explore_eps=p.explore_eps,
    )
    offpolicy = offpolicy_summary(slates)
    print(f"[3/6] off-policy rounds={offpolicy.rounds} support={offpolicy.support:.3f} "
          f"ips={offpolicy.ips_value:.4f} snips={offpolicy.snips_value:.4f} ess={offpolicy.ess:.1f}")
    leakage = audit_leakage(raw, records, params=asdict(p), build_kwargs=build_kwargs)
    print(f"[4/6] leakage features_identical={leakage['features_identical']} "
          f"tol_ok={leakage['tolerance_ok']} sens_ok={leakage['sensitivity_ok']}")
    bundle = load_distill_bundle(distill_dir)
    candidates = seed_candidates(bundle, records)
    ranking = rank_seeds(candidates, records, ridge=p.ridge, epochs=p.epochs, lr=p.lr,
                         gain_floor=p.gain_floor)
    print(f"[5/6] seeds ranked={len(ranking['ranked'])}")
    checks = {
        "pytest": check_pytest() if with_checks else {"pass": False, "reason": "checks disabled"},
        "coverage": check_coverage(minimum=p.min_coverage) if with_checks
        else {"value": 0.0, "ok": False, "reason": "checks disabled"},
        "domain": check_domain_scan() if with_checks
        else {"ok": False, "reason": "checks disabled"},
        "cluster_truth": check_cluster_truth() if with_checks
        else {"value": 0.0, "ok": False, "reason": "checks disabled"},
    }
    print(f"[6/6] pytest={checks['pytest']['pass']} "
          f"coverage={checks['coverage'].get('value')} domain_ok={checks['domain']['ok']}")
    abl = ablation(records, p)
    print(f"[5b/6] ablation variants={len(abl)} best_gain="
          f"{max((a['gain_pp'] for a in abl), default=0.0):+.2f}pp")
    gates = assemble_gates(
        distill_run=bundle["run"],
        walk=walk,
        leakage=leakage,
        pytest_check=checks["pytest"],
        coverage=checks["coverage"],
        domain_scan=checks["domain"],
        cluster_consistency=checks["cluster_truth"],
        params=asdict(p),
    )
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "mission_id": 11930,
        "node": "p318w2-n10",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "params": asdict(p),
        "corpus": {**corpus_summary(records), "snapshot": snapshot,
                   "snapshot_sha256": snapshot_sha256(raw)},
        "walk_forward": walk,
        "offpolicy": offpolicy.to_dict(),
        "leakage": leakage,
        "seed_ranking": {
            "model": ranking["model"],
            "cost_model": ranking["cost_model"],
            "train_n": ranking.get("train_n"),
            "score_sha256": ranking.get("score_sha256"),
            "count": len(ranking["ranked"]),
            "top": ranking["ranked"][:10],
        },
        "ablation": abl,
        "gates": gates,
        "distill": {
            "dir": distill_dir,
            "seeds_sha256": bundle.get("seeds_sha256"),
            "run_sha256": bundle.get("run_sha256"),
            "coverage": bundle["run"].get("coverage"),
            "gate": bundle["run"].get("gate"),
        },
        "runtime_sec": round(time.time() - started, 1),
    }
    (out_dir / "seeds_ranked.json").write_text(
        json.dumps(ranking["ranked"], ensure_ascii=False, indent=1), encoding="utf-8")
    from .report import write_report  # 局部导入：图依赖 matplotlib，避免测试环境强依赖

    paths = write_report(payload, out_dir, figures=with_figures)
    payload["artifacts"] = paths
    (out_dir / "eval_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    _write_manifest(out_dir)
    return payload


def ablation(
    records: Sequence[Mapping[str, Any]], p: SeedDispatchParams
) -> list[dict[str, Any]]:
    """敏感性分析：改变 gain 形态 / 成本指数 α，看 precision@20% 相对 FIFO 的增益。

    主口径 **不** 由该表挑出（那是拿评估集选口径的乐观偏差）；本表只回答
    「结果对口径有多敏感」，主结论一律用 ``SeedDispatchParams`` 预注册的 (gain=novelty, α=1)。
    """
    import numpy as np

    from .policy import FEATURES, evoi, fit_cost_model, fit_logistic, matrix, predict

    labelled = [r for r in records if r.get("label") is not None]
    X_all = matrix(labelled)
    y_all = np.asarray([int(r["label"]) for r in labelled], dtype=float)
    ts = np.asarray([float(r["ts"]) for r in labelled])
    nov = X_all[:, FEATURES.index("f_novelty")]
    n = len(labelled)
    bounds = [int(round(k * n / p.windows)) for k in range(p.windows + 1)]
    variants = [
        ("gain=novelty, alpha=1.0 (primary)", "raw", 1.0),
        ("gain=novelty, alpha=2.0", "raw", 2.0),
        ("gain=1+novelty, alpha=1.0", "plus1", 1.0),
        ("gain=const 1, alpha=1.0 (pure P/cost)", "flat", 1.0),
    ]
    ib = FEATURES.index("f_log_budget")
    il = FEATURES.index("f_log_len")
    from .policy import precision_at_frac

    out: list[dict[str, Any]] = []
    for label, mode, alpha in variants:
        per_window: list[tuple[float, float, int]] = []   # (evoi prec, fifo prec, n)
        for k in range(p.windows):
            lo, hi = bounds[k], bounds[k + 1]
            if lo < p.min_train or hi - lo < 2:
                continue
            model = fit_logistic(X_all[:lo], y_all[:lo], ridge=p.ridge, epochs=p.epochs, lr=p.lr)
            cost_model = fit_cost_model(labelled[:lo])
            prob = predict(model, X_all[lo:hi])
            cost = cost_model.predict(X_all[lo:hi][:, [ib, il]])
            gain = {"raw": np.maximum(nov[lo:hi], p.gain_floor),
                    "plus1": 1.0 + nov[lo:hi],
                    "flat": np.ones(hi - lo)}[mode]
            score = prob * gain / np.clip(cost, 1.0, None) ** alpha
            yt = y_all[lo:hi].astype(int).tolist()
            fifo = [float(x) for x in (-ts[lo:hi])]
            per_window.append((
                precision_at_frac([float(x) for x in score], yt, p.top_frac),
                precision_at_frac(fifo, yt, p.top_frac),
                hi - lo,
            ))
        if not per_window:
            continue
        weights = np.asarray([n for _, _, n in per_window], dtype=float)
        prec = float(np.average([pv for pv, _, _ in per_window], weights=weights))
        base = float(np.average([fv for _, fv, _ in per_window], weights=weights))
        out.append({
            "variant": label,
            "precision_evoi": round(prec, 6),
            "precision_fifo": round(base, 6),
            "gain_pp": round((prec - base) * 100.0, 3),
            "meets_gate": bool((prec - base) * 100.0 >= p.min_precision_gain_pp),
            "records": int(weights.sum()),
        })
    return out


def _forward_scores(records, labelled, p) -> list[float]:
    """给 labelled 记录算 walk-forward 的 EVOI 分（第 k 窗用 <k 窗模型，严格向前）。"""
    import numpy as np

    from .policy import FEATURES, evoi, fit_cost_model, fit_logistic, matrix, predict

    n = len(labelled)
    bounds = [int(round(k * n / p.windows)) for k in range(p.windows + 1)]
    scores = [0.0] * n
    ib, il, ino = (FEATURES.index("f_log_budget"), FEATURES.index("f_log_len"),
                   FEATURES.index("f_novelty"))
    for k in range(p.windows):
        lo, hi = bounds[k], bounds[k + 1]
        train = labelled[:lo]
        if hi - lo < 1:
            continue
        if lo < p.min_train:
            # 冷启动窗：用全部前序数据（若够）否则退回 FIFO 序（负时间戳）
            for i in range(lo, hi):
                scores[i] = -float(labelled[i]["ts"])
            continue
        cost_model = fit_cost_model(train)
        model = fit_logistic(matrix(train),
                             np.asarray([int(r["label"]) for r in train], float),
                             ridge=p.ridge, epochs=p.epochs, lr=p.lr)
        X = matrix(labelled[lo:hi])
        pr = predict(model, X)
        cost = cost_model.predict(X[:, [ib, il]])
        gain = np.maximum(X[:, ino], p.gain_floor)
        for j, val in enumerate(evoi(pr, gain, cost)):
            scores[lo + j] = float(val)
    return scores


def _write_manifest(out_dir: Path) -> None:
    files = {}
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "MANIFEST.json":
            files[path.name] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
    (out_dir / "MANIFEST.json").write_text(
        json.dumps({"files": files}, ensure_ascii=False, indent=1), encoding="utf-8")