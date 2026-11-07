#!/usr/bin/env python3
"""报告载荷路径约定 lint：禁止派单脚本里出现「裸 /tmp/report.json」。

问题（物理机制）
----------------
历史派单脚本统一把报告体写到 ``/tmp/report.json``：**一个全局固定文件名**。
平台是并发跑多单的，于是两单同时在 /tmp 写同一路径 → 后写覆盖先写，提交上去的
报告体是别人的（或半截的）。事故形态不是报错，而是**静默串号**：
HTTP 200 + 内容张冠李戴。服务端归属门（REPORT_MISSION_NOT_OWNED）只拦得住
「单号与提交者不符」，拦不住「单号对但内容是别人的」——那一层必须由
**路径独占**（文件名含 mission_id 令牌）在客户端堵住。

约定（本 lint 的可裁判据）
--------------------------
* 报告体落盘路径的 basename 必须含 mission_id 令牌：``report_m5617.json`` /
  ``report_5617.json``（见 ``tools/report_payload.py`` 的
  ``require_exclusive_name``，它写盘时强制同一规则）；
* 只扫 **/tmp 下的 report.json**（其余 ``report.json`` 是各工具自己的产物名，
  与派单载荷无关——故意不扫，避免宽正则假阳性，判例：关键词过滤假阳性）；
* 存量欠账登记在 ``config/report_path_baseline.json``（file → 出现次数）：
  **超基线即红，新增文件即红**；欠账只能减不能增。

用法
----
    python3 tools/audit/report_path_convention_lint.py                # 人读报告
    python3 tools/audit/report_path_convention_lint.py --json out.json
    python3 tools/audit/report_path_convention_lint.py --write-baseline
    python3 tools/audit/report_path_convention_lint.py --paths a.py b/  # 只扫指定路径

退出码：0 = 无新增违规；1 = 有新增违规（超出基线）；2 = 输入/配置错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "config" / "report_path_baseline.json"
DEFAULT_ROOTS = ("ops", "scripts", "tools", "harness_gateway", "backend",
                 "agent_work", "tests", "docs")
SUFFIXES = {".py", ".sh", ".bash"}

#: /tmp 下的裸 report.json（含 /tmp/xxx/report.json 变体）。
BARE_TMP_REPORT = re.compile(r"/tmp/[A-Za-z0-9_./@+-]*report\.json")
#: 允许的独占形态：basename 里带 mission 令牌（m5617 / 5617）。
MISSION_TOKEN = re.compile(r"(?:^|[_.-])m?\d{2,}(?=[_.-]|$)")


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def iter_files(paths: Iterable[str | Path]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT / p
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix in SUFFIXES and "__pycache__" not in f.parts:
                    out.append(f)
        elif p.is_file() and p.suffix in SUFFIXES:
            out.append(p)
    return out


def scan_file(path: Path) -> list[dict]:
    """返回该文件里的违规行（裸 /tmp/report.json，basename 无 mission 令牌）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in BARE_TMP_REPORT.finditer(line):
            literal = m.group(0)
            base = literal.rsplit("/", 1)[-1]
            if MISSION_TOKEN.search(base):
                continue
            hits.append({"file": _rel(path), "line": lineno, "snippet": literal,
                         "line_text": line.strip()[:200]})
    return hits


def scan(paths: Iterable[str | Path] = DEFAULT_ROOTS) -> dict:
    files = iter_files(paths)
    violations: list[dict] = []
    for f in files:
        violations.extend(scan_file(f))
    counts: dict[str, int] = {}
    for v in violations:
        counts[v["file"]] = counts.get(v["file"], 0) + 1
    return {"scanned_files": len(files), "violations": violations, "counts": counts}


def load_baseline(path: Path = BASELINE) -> dict:
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # 配置坏了必须报错，不能静默放行
        raise SystemExit(f"baseline 解析失败 {path}: {exc}")
    return dict(doc.get("allow", {}))


def compare(counts: dict, baseline: dict) -> dict:
    """基线对账：新增文件/超基线即违规（欠账只减不增）。"""
    regressions: list[dict] = []
    for file, n in sorted(counts.items()):
        allowed = int(baseline.get(file, 0))
        if n > allowed:
            regressions.append({"file": file, "found": n, "allowed": allowed,
                                "delta": n - allowed})
    improved = sorted(f for f, n in counts.items() if n < int(baseline.get(f, 0)))
    retired = sorted(f for f in baseline if f not in counts)
    return {
        "ok": not regressions,
        "regressions": regressions,
        "improved": improved,
        "retired": retired,
        "current_total": sum(counts.values()),
        "baseline_total": sum(int(v) for v in baseline.values()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="裸 /tmp/report.json 约定 lint")
    ap.add_argument("--paths", nargs="*", default=list(DEFAULT_ROOTS))
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--write-baseline", action="store_true",
                    help="把当前违规计数登记为基线（只用于一次性登记存量欠账）")
    args = ap.parse_args(argv)

    result = scan(args.paths)
    baseline_path = Path(args.baseline)
    if args.write_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(
            {"note": "存量欠账登记：裸 /tmp/report.json 出现次数上限，只减不增。",
             "allow": dict(sorted(result["counts"].items()))},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written: {baseline_path} ({len(result['counts'])} files)")
        return 0

    comparison = compare(result["counts"], load_baseline(baseline_path))
    report = {**result, "comparison": comparison}
    if args.json_out:
        out = Path(args.json_out)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    print(f"扫描文件: {result['scanned_files']}  违规行: {comparison['current_total']}"
          f"（基线 {comparison['baseline_total']}）")
    for reg in comparison["regressions"]:
        print(f"  ✗ {reg['file']}: {reg['found']} 处（基线 {reg['allowed']}，新增 {reg['delta']}）")
    if comparison["retired"]:
        print(f"  ✓ 已清零可移出基线: {', '.join(comparison['retired'][:5])}"
              f"{' …' if len(comparison['retired']) > 5 else ''}")
    failed = not comparison["ok"]
    print("结论:", "FAIL（新增裸路径）" if failed else "PASS（无新增，欠账只减不增）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())