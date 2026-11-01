"""上游节点兼容性探测 (upstream compatibility probe).

W8-N08 判据 (a)(e) 要求"基线=main, 上游未合并分支的集成兼容性检查"。上游 7 个
节点在**不同仓库/不同 worktree** 中并行开发, 因此本模块只做**只读探测**并如实
记录: 分支/是否已并入本基线/未提交改动数/是否已出现与本契约相关的模块或测试。

探测结论按以下状态码落地 (不做粉饰):
    MERGED              该节点的实现已在本基线 (HEAD) 中
    UNMERGED_ARTIFACT   未合并, 但其 worktree 中已有相关模块/测试文件
    UNMERGED_EMPTY      未合并, 且未探测到相关产物
    ABSENT              worktree 不存在
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List

#: 任务号 / worktree / 关键词 (用于在 worktree 内定位该节点的物理模块)
NODE_TARGETS: Dict[str, dict] = {
    "N01": {"mission": 1212, "worktree": "/srv/tensor/agent/work/m1212",
            "keywords": ("clausius", "stefan", "melt", "phase_change", "phasechange", "freez")},
    "N02": {"mission": 1216, "worktree": "/srv/tensor/agent/work/m1216",
            "keywords": ("slosh", "liquid_tank", "tank", "free_surface")},
    "N03": {"mission": 1217, "worktree": "/srv/tensor/agent/work/m1217",
            "keywords": ("roll", "stability", "gz", "metacentric")},
    "N04": {"mission": 1213, "worktree": "/srv/tensor/agent/work/m1213",
            "keywords": ("sea", "coupling_loss", "subsystem", "vibroacoustic")},
    "N05": {"mission": 1214, "worktree": "/srv/tensor/agent/work/m1214",
            "keywords": ("beverloo", "ergun", "jamming", "blockage", "discharge")},
    "N06": {"mission": 1215, "worktree": "/srv/tensor/agent/work/m1215",
            "keywords": ("frazil", "grease", "krieger", "viscosity", "rheolog")},
    "N07": {"mission": 1218, "worktree": "/srv/tensor/agent/work/m1218",
            "keywords": ("ekf", "kalman", "digital_twin", "sensormix", "sensor")},
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".pytest_cache"}
MAX_FILES = 4000
MAX_BYTES = 400_000


def _git(worktree: str, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", worktree, *args],
            capture_output=True, text=True, timeout=20, check=False,
        )
        return out.stdout.strip()
    except Exception:  # pragma: no cover - 环境异常时如实退化
        return ""


def _merged_into_base(worktree: str, base: str = "HEAD") -> bool:
    """工作树 HEAD 是否为当前仓库某分支的祖先 (用本仓库的 git 对象库判断)."""
    head = _git(worktree, "rev-parse", "HEAD")
    if not head:
        return False
    here = Path(__file__).resolve().parents[3]
    return _git(str(here), "merge-base", "--is-ancestor", head, base) != "" or \
        subprocess.run(["git", "-C", str(here), "merge-base", "--is-ancestor", head, base],
                       capture_output=True, check=False).returncode == 0


def _scan_keywords(root: str, keywords) -> List[str]:
    hits: List[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            scanned += 1
            if scanned > MAX_FILES or len(hits) >= 8:
                return hits
            lower = fn.lower()
            path = os.path.join(dirpath, fn)
            if any(k in lower for k in keywords):
                hits.append(path)
                continue
            if not lower.endswith((".py", ".md", ".json", ".yaml", ".yml")) or "_test" in lower:
                continue
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
                text = Path(path).read_text(errors="ignore").lower()
            except OSError:
                continue
            if any(k in text for k in keywords):
                hits.append(path)
    return hits


def probe_node(node: str, base_ref: str = "HEAD") -> dict:
    target = NODE_TARGETS[node]
    wt = target["worktree"]
    info = {
        "node": node,
        "mission": target["mission"],
        "worktree": wt,
        "worktree_exists": os.path.isdir(wt),
        "branch": "",
        "head": "",
        "dirty_files": 0,
        "merged_into_base": False,
        "artifact_hits": [],
        "test_files": [],
        "status": "ABSENT",
    }
    if not info["worktree_exists"]:
        return info
    info["branch"] = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    info["head"] = _git(wt, "rev-parse", "--short", "HEAD")
    dirty = _git(wt, "status", "--porcelain")
    info["dirty_files"] = len([ln for ln in dirty.splitlines() if ln.strip()])
    info["merged_into_base"] = _merged_into_base(wt, "HEAD")
    hits = _scan_keywords(wt, target["keywords"])
    info["artifact_hits"] = [h.replace(wt + "/", "") for h in hits][:8]
    info["test_files"] = [h.replace(wt + "/", "") for h in hits if "test" in os.path.basename(h).lower()]
    if info["merged_into_base"]:
        info["status"] = "MERGED"
    elif hits:
        info["status"] = "UNMERGED_ARTIFACT"
    else:
        info["status"] = "UNMERGED_EMPTY"
    return info


def probe_all(base_ref: str = "HEAD") -> dict:
    rows = {node: probe_node(node, base_ref) for node in NODE_TARGETS}
    summary = {
        "merged": [n for n, r in rows.items() if r["status"] == "MERGED"],
        "unmerged_artifact": [n for n, r in rows.items() if r["status"] == "UNMERGED_ARTIFACT"],
        "unmerged_empty": [n for n, r in rows.items() if r["status"] == "UNMERGED_EMPTY"],
        "absent": [n for n, r in rows.items() if r["status"] == "ABSENT"],
    }
    return {"base_ref": base_ref, "nodes": rows, "summary": summary}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(probe_all(), indent=2, ensure_ascii=False))