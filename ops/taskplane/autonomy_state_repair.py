"""把 ``campaign_plans.metadata.autonomy`` 的旧形状状态补默认键（IN27-H1 数据修复）。

根因见 ``campaign_dispatcher._norm_autonomy``：autonomy 状态是外部可写的持久 dict，
旧形状缺 ``arbiter_attempts``（实测 plan 94 只有 wave/arbiter/proposers），
``aut["arbiter_attempts"] += 1`` 直接下标 → 每轮 KeyError → 守护告警流 daemon_warns
的 error_total 越界（55→64 行/小时）且坏状态写不回去，永远不会自愈。

用法（默认只读 dry-run，加 --apply 才写库）::

    runuser -u postgres -- python3 ops/taskplane/autonomy_state_repair.py            # 看哪些计划要修
    runuser -u postgres -- python3 ops/taskplane/autonomy_state_repair.py --apply    # 写入补默认后的状态

幂等：只对被 ``_norm_autonomy`` 判为需修复的行动手；重复执行为空操作。
写回内容与 dispatcher 运行时自愈写回的内容**同一实现**（复用 ``_norm_autonomy``，无第二套判据）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
PSQL = ["runuser", "-u", "postgres", "--", "psql", "-d", "tensorplatform", "-t", "-A",
        "-F", "\x02", "-c"]


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location("asr_dispatcher", _HERE / "campaign_dispatcher.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["asr_dispatcher"] = mod
    spec.loader.exec_module(mod)
    return mod


disp = _load_dispatcher()


def q(sql: str) -> list[list[str]]:
    r = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("SQL: " + r.stderr.strip()[:400])
    return [ln.split("\x02") for ln in r.stdout.strip().split("\n") if ln]


def stage(states: dict[int, dict], norm=disp._norm_autonomy) -> list[dict]:
    """纯函数：给定 {plan_id: 持久化 autonomy 状态}，返回待修复清单（不动库，便于 pytest）。"""
    todo = []
    for pid, aut in sorted(states.items()):
        fixed, repaired = norm(aut)
        if repaired:
            todo.append({"plan_id": int(pid), "before": aut, "after": fixed,
                         "missing": [k for k in fixed if k not in (aut or {})]})
    return todo


def apply_one(pid: int, fixed: dict) -> None:
    payload = "$jd$" + json.dumps(fixed, ensure_ascii=False) + "$jd$::jsonb"
    q("UPDATE campaign_plans SET metadata = jsonb_set(coalesce(metadata,'{}'::jsonb), "
      "'{autonomy}', %s) WHERE id=%d" % (payload, int(pid)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    args = ap.parse_args(argv)
    rows = q("SELECT id, (metadata->'autonomy')::text FROM campaign_plans "
             "WHERE metadata ? 'autonomy' ORDER BY id")
    states = {int(r[0]): (json.loads(r[1]) if len(r) > 1 and r[1] else None) for r in rows}
    todo = stage(states)
    if not todo:
        print("OK: 所有 autonomy 状态已符合当前 schema，无需修复")
        return 0
    for item in todo:
        print(f"plan {item['plan_id']}: 缺/坏键 {item['missing']}")
        print(f"  before={json.dumps(item['before'], ensure_ascii=False)}")
        print(f"  after ={json.dumps(item['after'], ensure_ascii=False)}")
        if args.apply:
            apply_one(item["plan_id"], item["after"])
    print(("APPLIED %d 行" if args.apply else "DRY-RUN %d 行（加 --apply 写库）") % len(todo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())