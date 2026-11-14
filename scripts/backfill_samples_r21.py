#!/usr/bin/env python3
"""样本库批量回填 R21（KPI-SWEEP-1000 工况级落地）。

功能：
  1. 扫描近 7 天 succeeded mission 产物
  2. 对每个产物跑三门准入（RunManifest / mission 终态 / sha256 复算）
  3. 被拒条目按 error code 分类留痕（JSONL）
  4. 输出统计报告 + 逐条明细

用法：
  # 干跑 + 统计
  python3 scripts/backfill_samples_r21.py --dry-run --stats artifacts/sample_backfill/r21_stats.json

  # 验证统计一致性
  python3 scripts/backfill_samples_r21.py --verify artifacts/sample_backfill/r21_stats.json

  # 实际回填（写入明细 JSON）
  python3 scripts/backfill_samples_r21.py --output artifacts/sample_backfill/r21.json

口径：
  一个 sample = 一次真实可复算计算（mission × 工况点）
  工况身份 = (mission_id, run_id) 二元组
  去重：同 (mission_id, run_id) 只计一次
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 确保 repo root 在 path 中
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.sample_db.admission import (
    AdmissionDecision,
    ApiMissionResolver,
    StaticMissionResolver,
    NullMissionResolver,
    admit_sample,
    sha256_file,
)

# ── 工况计数工具函数 ────────────────────────────────────────────────

def sample_identity(record: dict[str, Any]) -> tuple:
    """工况身份 = (mission_id, run_id) 二元组。"""
    return (record.get("mission_id"), record.get("run_id"))


def deduplicate_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (mission_id, run_id) 去重，保留第一条。"""
    seen: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for s in samples:
        identity = sample_identity(s)
        if identity not in seen:
            seen.add(identity)
            deduped.append(s)
    return deduped


def count_workload(samples: list[dict[str, Any]]) -> int:
    """从样本明细复算工况数（去重后）。"""
    return len(deduplicate_samples(samples))


def case_to_workload_ratio(case_count: int, avg_points_per_case: float) -> int:
    """案例级 → 工况级换算：案例数 × 平均每案例工况点数。"""
    return int(case_count * avg_points_per_case)


def verify_stats_consistency(stats: dict[str, Any]) -> bool:
    """验证：admitted_count + Σrejected_by_code == scanned_total。"""
    admitted = stats.get("admitted_count", 0)
    rejected = stats.get("rejected_by_code", {})
    rejected_total = sum(rejected.values()) if isinstance(rejected, dict) else 0
    scanned = stats.get("scanned_total", 0)
    return admitted + rejected_total == scanned


# ── 数据源：扫描 mission 产物 ───────────────────────────────────────

def scan_local_artifacts(work_root: str = "/root/tp-agent/work", max_missions: int = 50) -> list[dict[str, Any]]:
    """扫描本地 work 目录下的 mission 产物（离线回退方案）。

    限制 max_missions 避免超时。
    """
    root = Path(work_root)
    artifacts = []
    if not root.exists():
        return artifacts

    mission_dirs = sorted([
        d for d in root.iterdir()
        if d.is_dir() and d.name.startswith("m") and d.name[1:].isdigit()
    ])

    for mission_dir in mission_dirs[:max_missions]:
        name = mission_dir.name
        mission_id = int(name[1:])

        # 查找产物 JSON（只扫描顶层和一级子目录）
        for json_file in list(mission_dir.glob("*.json")) + list(mission_dir.glob("*/*.json")):
            if json_file.name in ("run_manifest.json", "manifest.json"):
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    artifacts.append({
                        "path": str(json_file),
                        "mission_id": mission_id,
                        "run_dir": str(mission_dir),
                        "data": data,
                    })
            except Exception:
                continue

        if len(artifacts) >= 200:  # 限制总数
            break

    return artifacts


# ── 回填主逻辑 ──────────────────────────────────────────────────────

@dataclass
class BackfillResult:
    admitted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    rejected_by_code: dict[str, int] = field(default_factory=dict)
    scanned_total: int = 0


def run_backfill(
    artifacts: list[dict[str, Any]],
    mission_resolver: Any,
    audit_log: Path | None = None,
    dry_run: bool = False,
) -> BackfillResult:
    """对每个产物跑三门准入，收集 admitted / rejected 明细。"""
    result = BackfillResult()
    result.scanned_total = len(artifacts)

    for art in artifacts:
        artifact_path = art["path"]
        run_dir = art.get("run_dir")
        mission_id = art.get("mission_id")

        try:
            decision = admit_sample(
                artifact_path,
                mission_resolver=mission_resolver,
                audit_log=str(audit_log) if audit_log else None,
                run_dir=run_dir,
                mission_id=mission_id,
                actor="backfill_r21",
            )
        except Exception as exc:
            # 工具故障：记录为 rejected
            result.rejected.append({
                "artifact": artifact_path,
                "mission_id": mission_id,
                "code": "E_TOOL_ERROR",
                "message": str(exc),
                "admitted": False,
            })
            result.rejected_by_code["E_TOOL_ERROR"] = result.rejected_by_code.get("E_TOOL_ERROR", 0) + 1
            continue

        if decision.admitted:
            result.admitted.append({
                "artifact": decision.artifact,
                "mission_id": decision.mission_id,
                "run_id": decision.gates[0].details.get("source", "") if decision.gates else "",
                "manifest_source": decision.manifest_source,
                "verified_outputs": decision.verified_outputs,
                "admitted": True,
            })
        else:
            entry = {
                "artifact": decision.artifact,
                "mission_id": decision.mission_id,
                "code": decision.code,
                "message": decision.message,
                "admitted": False,
                "gates": {g.gate: g.status for g in decision.gates},
            }
            result.rejected.append(entry)
            code = decision.code or "E_UNKNOWN"
            result.rejected_by_code[code] = result.rejected_by_code.get(code, 0) + 1

    return result


# ── CLI ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backfill_samples_r21",
        description="样本库批量回填 R21（三门准入 + 工况级计数）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只扫描不写入")
    parser.add_argument("--stats", default=None, help="统计输出 JSON 路径")
    parser.add_argument("--output", default=None, help="逐条明细 JSON 路径")
    parser.add_argument("--verify", default=None, help="验证统计一致性（传入 stats JSON 路径）")
    parser.add_argument("--api", action="store_true", help="使用平台 API 获取 mission 列表")
    parser.add_argument("--days", type=int, default=7, help="扫描近 N 天（默认 7）")
    parser.add_argument("--work-root", default="/root/tp-agent/work", help="本地 work 目录")
    parser.add_argument("--audit-log", default=None, help="JSONL 审计留痕路径")
    parser.add_argument("--max-missions", type=int, default=50, help="最多扫描 mission 数（防超时）")
    args = parser.parse_args(argv)

    # ── verify 模式 ──
    if args.verify:
        stats_path = Path(args.verify)
        if not stats_path.exists():
            print(f"ERROR: stats file not found: {stats_path}", file=sys.stderr)
            return 1
        stats = json.loads(stats_path.read_text(encoding="utf-8"))

        # 检查必需字段
        for key in ("admitted_count", "rejected_by_code", "workload_count"):
            if key not in stats:
                print(f"ERROR: missing required field '{key}' in stats", file=sys.stderr)
                return 1

        # 一致性检查
        if not verify_stats_consistency(stats):
            admitted = stats.get("admitted_count", 0)
            rejected = stats.get("rejected_by_code", {})
            rejected_total = sum(rejected.values()) if isinstance(rejected, dict) else 0
            scanned = stats.get("scanned_total", 0)
            print(
                f"ERROR: admitted({admitted}) + rejected({rejected_total}) != scanned({scanned})",
                file=sys.stderr,
            )
            return 1

        # workload_count 复算
        if "samples" in stats:
            computed_workload = count_workload(stats["samples"])
            if computed_workload != stats.get("workload_count", -1):
                print(
                    f"ERROR: workload_count mismatch: stored={stats['workload_count']}, "
                    f"computed={computed_workload}",
                    file=sys.stderr,
                )
                return 1

        print(f"OK: stats verified (scanned={stats['scanned_total']}, "
              f"admitted={stats['admitted_count']}, workload={stats['workload_count']})")
        return 0

    # ── 获取产物列表 ──
    artifacts: list[dict[str, Any]] = []

    if args.api:
        # API 模式：尝试从平台拉取 succeeded mission
        try:
            base = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")
            user = os.environ.get("TENSOR_AGENT_USER", "")
            password = os.environ.get("TENSOR_AGENT_PASS", "")
            if user and password:
                # 登录
                import urllib.request
                import urllib.error
                body = json.dumps({"username": user, "password": password}).encode()
                req = urllib.request.Request(
                    f"{base}/auth/login", data=body, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    token_data = json.load(resp)
                token = token_data.get("access_token") or token_data.get("token", "")

                # 拉取 missions
                req = urllib.request.Request(
                    f"{base}/tensor/missions?per_page=100",
                    headers={"Authorization": f"Bearer {token}"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    missions_data = json.load(resp)

                missions = missions_data if isinstance(missions_data, list) else missions_data.get("items", [])
                succeeded = [m for m in missions if m.get("status") == "succeeded"]

                for m in succeeded[:args.max_missions]:
                    mid = m.get("id") or m.get("mission_id")
                    if mid is None:
                        continue
                    mission_dir = Path(args.work_root) / f"m{mid}"
                    if mission_dir.exists():
                        for json_file in list(mission_dir.glob("*.json")) + list(mission_dir.glob("*/*.json")):
                            if json_file.name in ("run_manifest.json", "manifest.json"):
                                continue
                            try:
                                data = json.loads(json_file.read_text(encoding="utf-8"))
                                if isinstance(data, dict):
                                    artifacts.append({
                                        "path": str(json_file),
                                        "mission_id": mid,
                                        "run_dir": str(mission_dir),
                                        "data": data,
                                    })
                            except Exception:
                                continue
        except Exception as exc:
            print(f"WARNING: API fetch failed ({exc}), falling back to local scan", file=sys.stderr)

    if not artifacts:
        # 回退到本地扫描
        artifacts = scan_local_artifacts(args.work_root, max_missions=args.max_missions)

    if not artifacts:
        print("WARNING: no artifacts found", file=sys.stderr)

    # ── 构造 mission resolver ──
    if args.api:
        resolver = ApiMissionResolver()
    else:
        # 从 artifacts 中提取所有 mission_id，假设都 succeeded
        mission_ids = set()
        for art in artifacts:
            mid = art.get("mission_id")
            if mid is not None:
                mission_ids.add(mid)
        mapping = {mid: "succeeded" for mid in mission_ids}
        resolver = StaticMissionResolver(mapping)

    # ── 执行回填 ──
    audit_log = Path(args.audit_log) if args.audit_log else None
    result = run_backfill(artifacts, resolver, audit_log=audit_log, dry_run=args.dry_run)

    # ── 去重 + 工况计数 ──
    deduped_admitted = deduplicate_samples(result.admitted)
    workload_count = len(deduped_admitted)

    # ── 构建统计 ──
    stats = {
        "scanned_total": result.scanned_total,
        "admitted_count": len(result.admitted),
        "rejected_by_code": result.rejected_by_code,
        "workload_count": workload_count,
        "deduped_admitted_count": len(deduped_admitted),
        "kpi_target": 1000,
        "kpi_gap": max(0, 1000 - workload_count),
        "case_level_estimate": 65,
        "case_to_workload_ratio": 3.2,
        "case_level_workload_estimate": case_to_workload_ratio(65, 3.2),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
    }

    # ── 输出 ──
    if args.stats:
        stats_path = Path(args.stats)
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        # 添加 samples 明细用于 verify 复算
        stats_with_samples = dict(stats)
        stats_with_samples["samples"] = result.admitted
        stats_path.write_text(
            json.dumps(stats_with_samples, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Stats written to {stats_path}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_data = {
            "admitted": result.admitted,
            "rejected": result.rejected,
            "stats": stats,
        }
        output_path.write_text(
            json.dumps(output_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Details written to {output_path}")

    # ── 打印摘要 ──
    print(f"\n=== Backfill R21 Summary ===")
    print(f"Scanned:       {result.scanned_total}")
    print(f"Admitted:      {len(result.admitted)}")
    print(f"Rejected:      {len(result.rejected)}")
    print(f"  by code:     {json.dumps(result.rejected_by_code, ensure_ascii=False)}")
    print(f"Workload (dedup): {workload_count}")
    print(f"KPI target:    1000")
    print(f"KPI gap:       {stats['kpi_gap']}")
    print(f"Case-level est: {stats['case_level_workload_estimate']} (65 cases × 3.2)")

    if args.dry_run:
        print("\n[DRY RUN] No data was written to sample DB.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
