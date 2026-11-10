"""证据工件生成：JSON + PNG + sha256 + provenance。

每个算例落一个 JSON（inputs / outputs / metrics / checks / provenance），每个
suite 落 ``result.json`` + ``figures/<suite>_verdict.png``，最后落 ``manifest.json``
把**所有**工件的 sha256 与配置摘要绑在一起——报告里的每个数字都能顺着
manifest → 工件 → config_sha256 → git sha 回溯（零手抄的前提）。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import ENGINE_VERSION

# 环境里可能存在的包：存在就记版本，缺了就记 None（不吞异常、不假装）
_TRACKED_PACKAGES = ("numpy", "matplotlib", "yaml")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: str | pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _git(*args: str, cwd: Optional[pathlib.Path] = None) -> Optional[str]:
    git = shutil.which("git")
    if not git:
        return None
    try:
        out = subprocess.run([git, *args], cwd=str(cwd) if cwd else None,
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _pkg_version(name: str) -> Optional[str]:
    try:
        mod = __import__(name)
    except Exception:
        return None
    return getattr(mod, "__version__", None)


def _porcelain_paths(porcelain: Optional[str]) -> List[str]:
    """把 ``git status --porcelain`` 解析成路径列表（去掉状态位与引号）。"""
    out: List[str] = []
    for line in (porcelain or "").splitlines():
        raw = line[3:].strip()
        if " -> " in raw:                    # 重命名：取新路径
            raw = raw.split(" -> ", 1)[1]
        out.append(raw.strip('"'))
    return out


def collect_provenance(repo_root: Optional[pathlib.Path] = None,
                       config_digest: Optional[str] = None,
                       extra: Optional[Mapping[str, Any]] = None,
                       ignore_paths: Sequence[str] = ()) -> Dict[str, Any]:
    """收集可复核的出处信息（不含任何密钥/凭据）。

    ``ignore_paths``：这些前缀下的改动不计入 ``git_dirty``——工件目录是本次运行
    **自己写出来的**，把"我刚写的输出"算成源码脏会污染出处语义。
    """
    root = pathlib.Path(repo_root) if repo_root else pathlib.Path(__file__).resolve().parents[2]
    git_sha = _git("rev-parse", "HEAD", cwd=root)
    porcelain = _git("status", "--porcelain", cwd=root)
    dirty_lines = [pth for pth in _porcelain_paths(porcelain)
                   if not any(pth == ig or pth.startswith(str(ig).rstrip("/") + "/")
                              for ig in ignore_paths)]
    dirty = None if porcelain is None else dirty_lines
    prov: Dict[str, Any] = {
        "engine": "tools.benchmark_runner",
        "engine_version": ENGINE_VERSION,
        "generated_at": utc_now(),
        "git_sha": git_sha,
        "git_dirty": None if dirty is None else bool(dirty),
        "git_dirty_paths": (dirty or [])[:20],
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD", cwd=root),
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "hostname": platform.node(),
        "cpu_count": os.cpu_count(),
        "packages": {n: _pkg_version(n) for n in _TRACKED_PACKAGES},
        "argv": list(sys.argv[1:]),
        "cwd": os.getcwd(),
        "config_sha256": config_digest,
    }
    if extra:
        prov.update(dict(extra))
    return prov


def write_json(path: str | pathlib.Path, obj: Any) -> pathlib.Path:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False, default=str) + "\n",
                 encoding="utf-8")
    return p


def read_json(path: str | pathlib.Path) -> Any:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


# ── 图 ─────────────────────────────────────────────────────────────────────
# 分辨率类参数名（无语义）：用于"误差随分辨率收敛"面板
_RESOLUTION_KEYS = ("intervals", "terms", "iterations", "n", "steps")


def _mpl():
    import matplotlib
    matplotlib.use("Agg")               # 无显示器/无人值守
    import matplotlib.pyplot as plt
    return plt


def render_suite_figure(out_png: str | pathlib.Path,
                        suite: Mapping[str, Any],
                        cases: Sequence[Mapping[str, Any]]) -> pathlib.Path:
    """两联图：(a) 逐算例主判据余量；(b) 误差随分辨率收敛（有则画）。

    全部数据来自 suite verdict，**不重算**任何数值（图上数字=工件数字）。
    """
    plt = _mpl()
    p = pathlib.Path(out_png)
    p.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.6), gridspec_kw={"width_ratios": [1.55, 1.0]})
    ax = axes[0]
    ids = [c["case_id"] for c in cases]
    errs: List[Optional[float]] = []
    for c in cases:
        e = c.get("metrics", {}).get("rel_error")
        errs.append(float(e) if isinstance(e, (int, float)) and e == e else None)
    x = list(range(len(ids)))
    if ids:
        vals = [e if e is not None else 0.0 for e in errs]
        floor = max([v for v in vals if v > 0] + [1e-18]) * 1e-3
        vals_plot = [v if v > 0 else floor for v in vals]
        colors = ["#2e7d32" if c["passed"] else "#c62828" for c in cases]
        ax.bar(x, vals_plot, color=colors, edgecolor="black", linewidth=0.4)
        # 预声明阈值线：只画实际出现在配置里的数字（不臆造）
        for thr in sorted({chk["threshold"] for c in cases for chk in c.get("checks", [])
                           if chk["metric"] == "rel_error" and isinstance(chk.get("threshold"), (int, float))}):
            if thr > 0:
                ax.axhline(thr, linestyle="--", linewidth=1.0, color="#1565c0",
                           label=f"declared threshold {thr:g}")
        ax.set_yscale("log")
        ax.set_xticks(x)
        ax.set_xticklabels(ids, rotation=45, ha="right", fontsize=7)
        ax.set_ylabel("relative error (log)")
        ax.set_title(f"(a) {suite.get('suite')} — per-case rel. error vs predeclared threshold", fontsize=9)
        ax.grid(True, axis="y", alpha=0.3)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc="upper right")
    else:
        ax.text(0.5, 0.5, "no cases", ha="center", va="center")
        ax.set_axis_off()

    ax2 = axes[1]
    # 收敛面板：同一 suite 内 ≥2 个算例共享同一"分辨率"输入键且带 rel_error
    plotted = False
    for key in _RESOLUTION_KEYS:
        pts = []
        for c in cases:
            res = c.get("inputs", {}).get(key)
            e = c.get("metrics", {}).get("rel_error")
            if isinstance(res, (int, float)) and isinstance(e, (int, float)) and e > 0:
                pts.append((float(res), float(e)))
        if len(pts) >= 2:
            pts.sort()
            ax2.plot([q[0] for q in pts], [q[1] for q in pts], "o-", color="#2e7d32", label=key)
            plotted = True
    if plotted:
        ax2.set_xscale("log")
        ax2.set_yscale("log")
        ax2.set_xlabel("resolution parameter (log)")
        ax2.set_ylabel("relative error (log)")
        ax2.set_title("(b) convergence: error vs resolution", fontsize=9)
        ax2.grid(True, which="both", alpha=0.3)
        ax2.legend(fontsize=7)
    else:
        ax2.text(0.5, 0.5, "no resolution sweep in this suite", ha="center", va="center", fontsize=8)
        ax2.set_axis_off()
    fig.suptitle(f"benchmark runner · suite={suite.get('suite')} · run_id={suite.get('run_id')} · "
                 f"passed={suite.get('passed')}", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(p, dpi=130)
    plt.close(fig)
    return p


def write_manifest(out_dir: str | pathlib.Path,
                   suite: Mapping[str, Any],
                   provenance: Mapping[str, Any],
                   extra_files: Iterable[str | pathlib.Path] = ()) -> pathlib.Path:
    """把所有工件按 sha256 绑定；manifest 自身不自我哈希（避免循环）。"""
    d = pathlib.Path(out_dir)
    files = sorted([q for q in d.rglob("*") if q.is_file() and q.name != "manifest.json"])
    entries = []
    for q in files:
        entries.append({
            "path": str(q.relative_to(d)),
            "sha256": sha256_file(q),
            "bytes": q.stat().st_size,
        })
    manifest = {
        "suite": suite.get("suite"),
        "run_id": suite.get("run_id"),
        "passed": suite.get("passed"),
        "engine_version": provenance.get("engine_version"),
        "config_sha256": provenance.get("config_sha256"),
        "git_sha": provenance.get("git_sha"),
        "generated_at": provenance.get("generated_at"),
        "file_count": len(entries),
        "files": entries,
        "manifest_digest": sha256_obj(entries),
    }
    return write_json(d / "manifest.json", manifest)


# ── 证据链回验（tamper detection） ────────────────────────────────────────
# "有产物" ≠ "产物可信"。manifest 记录了 sha256，但**只有有人真的重算一遍**，
# 这个哈希才有约束力——否则它只是一行看着像证据的字符串。本函数就是那次重算：
# 逐文件重算 sha256 → 与 manifest 比；检查清单自身 digest；检查是否有未登记的
# 多余文件；再校验每个算例 JSON 自带的 ``artifact_sha256``（自洽性）。
def verify_manifest(out_dir: str | pathlib.Path) -> Dict[str, Any]:
    """回验 ``manifest.json`` 记录的哈希链，返回裁决 dict（``ok`` 严格为 bool）。

    检查项：
    (a) 每个登记文件存在且 sha256 与字节数一致；
    (b) manifest 的 ``manifest_digest``（对 entries 列表的摘要）可重算一致；
    (c) 目录内不存在未登记的额外文件（防"偷偷多塞一个工件"）；
    (d) ``cases/*.json`` 的 ``artifact_sha256`` 与自身内容一致（防局部改写）。

    任一项不满足 → ``ok=False``（fail-closed：证据不可信就是不可信）。
    """
    d = pathlib.Path(out_dir)
    mpath = d / "manifest.json"
    result: Dict[str, Any] = {
        "ok": False, "root": str(d), "checked": 0, "mismatch": [], "missing": [],
        "extra": [], "case_self_hash_errors": [], "manifest_digest_ok": None,
        "manifest_sha256": None,
    }
    if not mpath.is_file():
        result["reason"] = "manifest.json missing: no evidence chain to verify"
        return result
    result["manifest_sha256"] = sha256_file(mpath)
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result["reason"] = f"manifest.json unreadable: {exc}"
        return result

    entries = manifest.get("files") or []
    listed: set[str] = set()
    for e in entries:
        rel = e.get("path")
        if not isinstance(rel, str) or not rel:
            result["mismatch"].append({"path": None, "reason": "entry without path"})
            continue
        listed.add(rel)
        f = d / rel
        if not f.is_file():
            result["missing"].append(rel)
            continue
        got = sha256_file(f)
        if got != e.get("sha256"):
            result["mismatch"].append({"path": rel, "reason": "sha256 mismatch",
                                       "expected": e.get("sha256"), "actual": got})
        elif f.stat().st_size != e.get("bytes"):
            result["mismatch"].append({"path": rel, "reason": "size mismatch",
                                       "expected": e.get("bytes"),
                                       "actual": f.stat().st_size})
    result["checked"] = len(listed)

    result["manifest_digest_ok"] = (manifest.get("manifest_digest") == sha256_obj(entries))
    for q in sorted(d.rglob("*")):
        if q.is_file() and q.name != "manifest.json":
            rel = str(q.relative_to(d))
            if rel not in listed:
                result["extra"].append(rel)

    cases_dir = d / "cases"
    if cases_dir.is_dir():
        for q in sorted(cases_dir.glob("*.json")):
            try:
                payload = json.loads(q.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                result["case_self_hash_errors"].append(str(q.relative_to(d)))
                continue
            claimed = payload.pop("artifact_sha256", None)
            if not claimed or sha256_obj(payload) != claimed:
                result["case_self_hash_errors"].append(str(q.relative_to(d)))

    result["ok"] = (not result["missing"] and not result["mismatch"] and not result["extra"]
                    and not result["case_self_hash_errors"]
                    and result["manifest_digest_ok"] is True)
    if not result["ok"]:
        result["reason"] = "evidence chain broken (see missing/mismatch/extra/self-hash fields)"
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI：``python3 -m tools.benchmark_runner.artifact --verify <工件目录>``。"""
    import argparse
    ap = argparse.ArgumentParser(description="benchmark 工件证据链回验（零领域语义）")
    ap.add_argument("--verify", required=True, metavar="DIR", help="含 manifest.json 的工件目录")
    args = ap.parse_args(argv)
    report = verify_manifest(args.verify)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
