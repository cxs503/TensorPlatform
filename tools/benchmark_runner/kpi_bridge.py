"""KPI 桥：把无人值守 benchmark 运行账本汇总成 ``unattended_cases`` 口径计数。

口径（与 ``tools/guide_kpi_ledger.py`` 的 KPI ``unattended_cases`` 对齐）：
**通过判据门、且工件带 sha256 + provenance 的算例** 才计数；去重口径为
``case_id``（同一算例多次运行只算一次，取最近一次通过的 run）。

本工具只读工件、不写库、不碰领域语义——它回答的问题是：
"引擎这一轮**实证**了多少个无需人工判读的算例？"
"
"""
from __future__ import annotations

import argparse
import json
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from . import artifact as art


def _iter_results(root: pathlib.Path) -> Iterable[pathlib.Path]:
    for p in sorted(root.glob("*/*/result.json")):
        yield p


def collect(root: str | pathlib.Path,
            require_manifest: bool = True) -> Dict[str, Any]:
    """扫描工件树，返回无人值守算例账本。"""
    root = pathlib.Path(root)
    cases: Dict[str, Dict[str, Any]] = {}
    suites: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for res in _iter_results(root):
        try:
            data = art.read_json(res)
        except Exception as exc:                        # noqa: BLE001
            skipped.append({"path": str(res), "reason": f"unreadable: {exc}"})
            continue
        run_dir = res.parent
        manifest = run_dir / "manifest.json"
        has_manifest = manifest.is_file()
        if require_manifest and not has_manifest:
            skipped.append({"path": str(res), "reason": "manifest.json missing (no sha256 chain)"})
            continue
        manifest_digest = art.sha256_file(manifest) if has_manifest else None
        prov = data.get("provenance") or {}
        n_pass = 0
        for c in data.get("cases", []):
            if not c.get("passed"):
                continue
            n_pass += 1
            cid = c["case_id"]
            # 逐算例工件自带 artifact_sha256（result.json 的 cases 只是摘要）→ 优先读它
            case_file = run_dir / "cases" / f"{cid}.json"
            case_sha = None
            if case_file.is_file():
                try:
                    case_sha = art.read_json(case_file).get("artifact_sha256")
                except Exception:                       # noqa: BLE001 - 坏文件不该让整本账失败
                    case_sha = None
            if not case_sha:
                case_sha = art.sha256_obj(c)
            entry = {
                "case_id": cid,
                "suite": data.get("suite"),
                "run_id": data.get("run_id"),
                "status": c.get("status"),
                "rel_error": (c.get("metrics") or {}).get("rel_error"),
                "case_artifact_sha256": case_sha,
                "case_file": str(case_file) if case_file.is_file() else None,
                "manifest_sha256": manifest_digest,
                "config_sha256": prov.get("config_sha256"),
                "git_sha": prov.get("git_sha"),
                "generated_at": prov.get("generated_at"),
            }
            prev = cases.get(cid)
            # 同一算例多次运行：保留最近一次（run_id 含 UTC 时间戳，字典序即时序）
            if prev is None or str(entry["run_id"]) >= str(prev["run_id"]):
                cases[cid] = entry
        suites.append({"suite": data.get("suite"), "run_id": data.get("run_id"),
                       "passed": data.get("passed"), "n_passed": n_pass,
                       "n_cases": len(data.get("cases", [])),
                       "manifest_sha256": manifest_digest,
                       "git_sha": prov.get("git_sha")})
    ordered = [cases[k] for k in sorted(cases)]
    return {
        "metric": "unattended_cases",
        "definition": ("distinct case_id with gate verdict pass AND hashed artifact chain "
                       "(case json + manifest sha256 + provenance)"),
        "kpi_alignment": {
            "kpi_key": "unattended_cases",
            "etl": "tools/guide_kpi_ledger.py",
            "status": "not_wired",
            "note": ("该 KPI 在 ETL 里的现有口径是 DB mission-goal 去重计数（代理口径）；"
                     "本账本是引擎侧可复核来源，并入 KPI 口径需改 ETL/由上游按算例铺单——"
                     "本工具不擅自改口径，只提供可追溯的计数与哈希链。"),
        },
        "count": len(ordered),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "suites": suites,
        "cases": ordered,
        "skipped": skipped,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="unattended_cases KPI 桥（只读工件）")
    ap.add_argument("--root", default="artifacts/benchmark_runner")
    ap.add_argument("--out", default="artifacts/benchmark_runner/unattended_cases.json")
    ap.add_argument("--allow-unhashed", action="store_true",
                    help="允许没有 manifest 的 run 计入（默认禁止：无哈希链不计）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ledger = collect(args.root, require_manifest=not args.allow_unhashed)
    art.write_json(args.out, ledger)
    if args.json:
        print(json.dumps(ledger, indent=2, ensure_ascii=False))
    else:
        print(f"unattended_cases={ledger['count']} (root={ledger['root']})")
        for s in ledger["suites"]:
            print(f"  {s['suite']:22s} {s['n_passed']}/{s['n_cases']} passed  run_id={s['run_id']}")
        for sk in ledger["skipped"]:
            print(f"  SKIP {sk['path']}: {sk['reason']}")
        print(f"  ledger -> {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())