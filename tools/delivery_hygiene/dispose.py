"""处置动作：A/D → 归档 ``refs/attic/`` + 关分支；B → 标注超集来源；C → 开交付补单。

动作表
------
============  =================================  ==========================
类             动作                                副作用
============  =================================  ==========================
``D`` 孤儿     ``archive_ref``                    建 ``refs/attic/<date>/<b>`` 后删分支
``A`` 已覆盖   ``archive_ref``                    同上（内容已在 base）
``B`` 超集     ``annotate``（+ ``archive_ref`` 若内容留存已证明）
               账本标注 ``superset_source``，可选归档
``C`` 未交付   ``open_ticket``                    写 ``tickets/<repo>-<mission>.json``
============  =================================  ==========================

铁律
----
1. **dry-run 是默认**：只有 ``apply=True`` 且调用方显式 ``--i-mean-it`` 才动 ref。
2. **先归档后删**：归档 ref 建立并回读校验通过才删分支；任一步失败保留归档（内容不丢）。
3. **C 类绝不关**：哪怕账本里写错了，也只在 ticket 里出现，不会进 archive 动作列表。
4. 每步写进 ``manifest``（含 sha），回滚一行命令：``git update-ref refs/heads/<b> <sha>``。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from . import CLASS_A, CLASS_B, CLASS_C, CLASS_D
from . import gitio, ledger as ledger_mod

ACTION_ARCHIVE = "archive_ref"
ACTION_ANNOTATE = "annotate"
ACTION_TICKET = "open_ticket"
ACTION_NONE = "none"


def plan_disposition(row: Dict[str, Any], date_tag: str,
                     archive_prefix: str = "refs/attic",
                     archive_b: bool = True) -> Dict[str, Any]:
    """单分支处置计划（纯函数，无 git 调用 ⇒ 可在测试里对任意账本行断言）。"""
    cls = row["class"]
    base = {
        "repo": row["repo"],
        "branch": row["branch"],
        "class": cls,
        "safe_to_close": row["safe_to_close"],
        "actions": [],
        "archive_ref": None,
        "keep_ticket": None,
        "reason": row["decided_by"],
    }
    if cls == CLASS_D:
        base["actions"] = [ACTION_ARCHIVE]
        base["archive_ref"] = gitio.attic_ref_name(row["branch"], date_tag, archive_prefix)
        base["reason_note"] = "tip 已在 base 历史内：分支 ref 只剩指针价值"
    elif cls == CLASS_A:
        base["actions"] = [ACTION_ARCHIVE]
        base["archive_ref"] = gitio.attic_ref_name(row["branch"], date_tag, archive_prefix)
        base["reason_note"] = "内容已等价进 base：删分支不丢内容"
    elif cls == CLASS_B:
        base["actions"] = [ACTION_ANNOTATE]
        if archive_b and row["safe_to_close"]:
            base["actions"].append(ACTION_ARCHIVE)
            base["archive_ref"] = gitio.attic_ref_name(row["branch"], date_tag, archive_prefix)
        base["superset_source"] = row.get("superset_source")
        base["reason_note"] = f"独有补丁被 `{row.get('superset_source')}` 覆盖，内容留在该分支"
    elif cls == CLASS_C:
        base["actions"] = [ACTION_TICKET]
        base["reason_note"] = "独有补丁既不在 base 也不在别分支：必须保留并开补单"
    else:  # pragma: no cover - 防御
        base["actions"] = [ACTION_NONE]
        base["reason_note"] = "未知类别：不做任何动作"
    return base


def plan_dispositions(ledger: Dict[str, Any], date_tag: Optional[str] = None,
                      archive_prefix: str = "refs/attic",
                      archive_b: bool = True) -> List[Dict[str, Any]]:
    date_tag = date_tag or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [plan_disposition(r, date_tag, archive_prefix, archive_b) for r in ledger["rows"]]


def summarize_plan(plan: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    out = {ACTION_ARCHIVE: 0, ACTION_ANNOTATE: 0, ACTION_TICKET: 0, ACTION_NONE: 0}
    for p in plan:
        for a in p["actions"]:
            out[a] = out.get(a, 0) + 1
    return out


def write_tickets(ledger: Dict[str, Any], out_dir: str,
                  plan: Optional[Sequence[Dict[str, Any]]] = None) -> List[str]:
    """把账本里的 C 类补单写成独立 JSON（一行一单，任务机可直接收养）。"""
    paths: List[str] = []
    os.makedirs(out_dir, exist_ok=True)
    for t in ledger.get("tickets", []):
        slug = t["branch"].replace("/", "_")
        path = os.path.join(out_dir, f"{ledger['repo']}--{slug}.json")
        ledger_mod.write_json(path, t)
        paths.append(path)
    return paths


def apply_plan(repo: str, plan: Sequence[Dict[str, Any]], date_tag: str,
               apply: bool = False, archive_prefix: str = "refs/attic",
               manifest_path: Optional[str] = None) -> Dict[str, Any]:
    """执行/演练处置计划。返回 manifest（含每分支结果与回滚命令）。"""
    results: List[Dict[str, Any]] = []
    for p in plan:
        if ACTION_ARCHIVE not in p["actions"]:
            continue
        sha = gitio.rev_parse(repo, p["branch"])
        if sha is None:
            results.append({"branch": p["branch"], "class": p["class"], "ok": True,
                            "skipped": "branch-already-gone", "applied": False})
            continue
        res = gitio.archive_and_close(repo, p["branch"], sha, date_tag,
                                      prefix=archive_prefix, apply=apply)
        res["class"] = p["class"]
        res["rollback"] = f"git update-ref refs/heads/{p['branch']} {sha}"
        results.append(res)
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": repo,
        "date_tag": date_tag,
        "apply": bool(apply),
        "archive_prefix": archive_prefix,
        "counts": {
            "planned": sum(1 for p in plan if ACTION_ARCHIVE in p["actions"]),
            "applied": sum(1 for r in results if r.get("applied")),
            "skipped": sum(1 for r in results if r.get("skipped")),
            "failed": sum(1 for r in results if not r.get("ok")),
        },
        "results": results,
        "tickets": sum(1 for p in plan if ACTION_TICKET in p["actions"]),
        "annotations": sum(1 for p in plan if ACTION_ANNOTATE in p["actions"]),
    }
    if manifest_path:
        ledger_mod.write_json(manifest_path, manifest)
    return manifest