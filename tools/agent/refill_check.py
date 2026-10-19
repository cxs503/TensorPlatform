#!/usr/bin/env python3
"""refill 资格机械判定——剧本案例 2（m49 误续）的结构化替代。

问题：mission 行的 stop_condition 可能滞后于审计裁决（m49 行内停留在
agent_lost，账本已有 worktree_bug_crash + operator_rejected，operator 按行内
值合法续了被驳回的腿）。此前修法=prose 纪律（refill 前 grep 账本全史），本
工具把它变成机械判定：行内值 + 账本全史合并定性，人/agent 都不再靠记性。

用法（operator 值班例行；凭据同 operator.env）：
  set -a; source /srv/tensor/shared/tensor_eco/agent_work/operator.env; set +a
  /srv/tensor/shared/venvs/tensor_eco/bin/python refill_check.py <mission_id>

退出码：0=可续班（infra 终局且无终局驳回标记）；3=拒绝（附原因与回写行）；
1=错误（API/账本不可得 fail closed，不派生任何动作）。
"""
from __future__ import annotations

import glob
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1")
ECO = "/srv/tensor/shared/tensor_eco"
LEDGER_GLOB = ECO + "/agent_memory/*/memory.jsonl"

# infra 类终局=可续（环境/预算/网关背锅）；其余（honest FAIL/owner 锁）=不续
INFRA = {"agent_lost", "budget_exceeded", "agent_crash", "gateway_unavailable"}
# 账本里出现即终局锁死的定性（驳回过就永远驳回，除非 owner 明确解锁）
TERMINAL_LEDGER = {"operator_rejected", "owner_stopped", "rejected"}
# 行内出现即拒绝的 stop_condition
TERMINAL_ROW = {"owner_stopped", "finished_without_acceptance"}

EXIT_OK, EXIT_REFUSE, EXIT_ERR = 0, 3, 1


def api(method: str, path: str, body: dict | None = None, tok: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except Exception:  # noqa: BLE001
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001  传输层
        raise RuntimeError(f"{method} {path} 传输错误: {exc!r}") from exc
    return status, json.loads(raw or b"null")


def read_ledger(mission_id: int) -> list[dict]:
    """grep 全部账本中该 mission 的条目（ts 字典序即可比）。fail closed。"""
    entries: list[dict] = []
    for path in sorted(glob.glob(LEDGER_GLOB)):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("mission") == mission_id:
                        entries.append(e)
        except OSError as exc:
            raise RuntimeError(f"账本不可读 {path}: {exc}") from exc
    return sorted(entries, key=lambda e: str(e.get("ts") or ""))


def verdict(row_stop: str | None, row_status: str | None,
            ledger: list[dict]) -> tuple[int, str]:
    """纯函数：行内值 + 账本全史 → (退出码, 结论)。测试直接覆盖此处。"""
    terminal_hits = [e for e in ledger if e.get("status") in TERMINAL_LEDGER]
    if terminal_hits:
        e = terminal_hits[-1]
        return EXIT_REFUSE, (
            f"拒绝：账本存在终局驳回标记 {e.get('status')}（{e.get('ts')}），"
            "驳回过即锁死，续班前必须 owner 解锁。若 mission 行 stop_condition "
            f"仍为可续类（当前={row_stop!r}），先按剧本案例 2 的回写命令把行内 "
            "值锁成 owner_stopped，再结束本条处置。")
    if row_stop in TERMINAL_ROW:
        return EXIT_REFUSE, (
            f"拒绝：行内 stop_condition={row_stop!r}（owner 锁/honest FAIL），"
            "不属于 infra 续班类。honest FAIL 重派走 [上报]，不动手。")
    if row_stop in INFRA:
        last = ledger[-1] if ledger else None
        extra = f"（账本最晚：{last.get('ts')} {last.get('status')}）" if last else "（无账本条目）"
        return EXIT_OK, (
            f"可续班：行内 stop_condition={row_stop!r} 属 infra 类，且账本全史"
            f"无驳回标记{extra}。按剧本 refill 并写账本。")
    if row_stop is None and row_status in ("queued", "claimed", "running"):
        return EXIT_REFUSE, f"拒绝：mission 仍在飞（{row_status}），无续班可言。"
    return EXIT_REFUSE, (
        f"拒绝：行内 stop_condition={row_stop!r} 非法/非 infra 类，不续。")


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("用法: refill_check.py <mission_id>", file=sys.stderr)
        return EXIT_ERR
    mid = int(sys.argv[1])
    user, pw = os.environ.get("OPERATOR_USER"), os.environ.get("OPERATOR_PASS")
    if not (user and pw):
        print("ERROR: OPERATOR_USER/OPERATOR_PASS 未设置（source operator.env）",
              file=sys.stderr)
        return EXIT_ERR
    try:
        code, body = api("POST", "/auth/login", {"username": user, "password": pw})
        if code != 200:
            print(f"ERROR: 登录失败 HTTP {code}", file=sys.stderr)
            return EXIT_ERR
        tok = body["access_token"]
        code, m = api("GET", f"/tensor/missions/{mid}", tok=tok)
        if code != 200 or not isinstance(m, dict):
            print(f"ERROR: mission 读取失败 HTTP {code}", file=sys.stderr)
            return EXIT_ERR
        ledger = read_ledger(mid)
    except RuntimeError as exc:
        print(f"ERROR: fail closed——{exc}", file=sys.stderr)
        return EXIT_ERR
    r = m.get("result") or {}
    rc, msg = verdict(r.get("stop_condition"), m.get("status"), ledger)
    print(f"[refill_check] m{mid} status={m.get('status')} "
          f"stop={r.get('stop_condition')!r} ledger_entries={len(ledger)}")
    print(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
