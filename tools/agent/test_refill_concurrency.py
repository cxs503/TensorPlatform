#!/usr/bin/env python3
"""补测②（PR #22 初审第 2 点）：并发 refill 不重复加预算（活体 API 级）。

流程：合成一条终态 mission（stop_condition=agent_lost，model=pr22-noclaim
保证没有真 daemon 来认领）→ 双线程 barrier 同时 POST refill → 判据：
  T1 恰一个 200 + 一个 409
  T2 max_tool_calls 只加一次（10+25=35，不是 60）
  T3 胜者落账后状态 queued、审计恰一条 refill 记录
  T4 对 queued 态再 refill → 409（非终态拒绝）
跑完删合成行（finally 兜底）。

运行（5090）：
  set -a; source /srv/tensor/shared/tensor_eco/review.env; set +a
  python3 test_refill_concurrency.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request

BASE = os.environ.get("TENSOR_API_BASE", "http://localhost:3000/api/v1").rstrip("/")
DOCKER = ["docker", "--host", "unix:///run/user/1020/docker.sock", "exec",
          "open-foundation-postgres", "psql", "-U", "foundation",
          "-d", "open_digital_foundation", "-v", "ON_ERROR_STOP=1",
          "-q", "-A", "-t", "-c"]
MID = 998001
PASS: list[str] = []


def psql(sql: str) -> str:
    r = subprocess.run(DOCKER + [sql], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"psql 失败: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def api(method: str, path: str, body=None, tok: str | None = None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except Exception:  # noqa: BLE001
            return exc.code, {}


def main() -> int:
    tok = api("POST", "/auth/login", {
        "username": os.environ["REVIEW_USER"],
        "password": os.environ["REVIEW_PASS"]})[1]["access_token"]
    try:
        psql(f"DELETE FROM tensor_dev_missions WHERE id={MID}")   # 幂等预备
        out = psql(
            f"""INSERT INTO tensor_dev_missions
                (id, organization_id, goal, repo, status, runner, origin, model,
                 budget, result, created_at, heartbeat_at, finished_at)
                SELECT {MID}, organization_id,
                 'PR22 并发 refill 补测合成行（跑完即删）', 'TensorPhysics',
                 'failed', 'native', 'owner', 'pr22-noclaim',
                 '{{"max_tool_calls":10,"max_wall_seconds":600,"max_tokens":100000}}'::jsonb,
                 '{{"stop_condition":"agent_lost",
                     "summary":{{"headline":"synthetic"}}}}'::jsonb,
                 now(), now(), now()
                FROM tensor_dev_missions LIMIT 1 RETURNING id""")
        assert out == str(MID), f"合成行插入失败: {out!r}"

        barrier = threading.Barrier(2, timeout=30)
        results: list[tuple[int, dict]] = []
        lock = threading.Lock()

        def refill() -> None:
            barrier.wait()
            r = api("POST", f"/tensor/missions/{MID}/refill",
                    {"add_tool_calls": 25, "note": "pr22-concurrency-test"}, tok=tok)
            with lock:
                results.append(r)

        ts = [threading.Thread(target=refill) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(120)
        codes = sorted(r[0] for r in results)
        assert codes == [200, 409], f"T1 FAIL: 双 refill 返回 {codes}（应一 200 一 409）"
        PASS.append("T1 并发双 refill → 恰一个 200 + 一个 409"
                    f"（409 detail={next(b for c, b in results if c == 409).get('detail', '')[:40]}）")

        row = psql(f"SELECT status, budget->>'max_tool_calls',"
                   f" result->'refill' IS NOT NULL FROM tensor_dev_missions"
                   f" WHERE id={MID}")
        status, tc, has_audit = row.split("|")
        assert status == "queued", f"T2 pre-FAIL: status={status}"
        assert tc == "35", f"T2 FAIL: max_tool_calls={tc}（应为 10+25=35，双加会是 60）"
        assert has_audit == "t", "T3 FAIL: refill 审计未落账"
        PASS.append("T2 预算恰加一次（10+25=35）")
        PASS.append("T3 胜者落账：status=queued、审计恰一条")

        code, _ = api("POST", f"/tensor/missions/{MID}/refill",
                      {"add_tool_calls": 5}, tok=tok)
        assert code == 409, f"T4 FAIL: 非终态 refill 返回 {code}（应 409）"
        PASS.append("T4 对 queued 态再 refill → 409（非终态拒绝）")

        # T5 infra 类终局可续班（m50 实证后资格扩展）：gateway_unavailable/
        # agent_crash 与 agent_lost 同权；honest FAIL 类仍拒
        psql(f"""UPDATE tensor_dev_missions SET status='failed', finished_at=now(),
                 result = result || '{{"stop_condition":"gateway_unavailable"}}'::jsonb
                 WHERE id={MID}""")
        code, _ = api("POST", f"/tensor/missions/{MID}/refill",
                      {"add_tool_calls": 5, "note": "T5 infra 终局资格"}, tok=tok)
        assert code == 200, f"T5a FAIL: gateway_unavailable 终局 refill 返回 {code}（应 200）"
        psql(f"""UPDATE tensor_dev_missions SET status='failed', finished_at=now(),
                 result = result || '{{"stop_condition":"finished_without_acceptance"}}'::jsonb
                 WHERE id={MID}""")
        code, _ = api("POST", f"/tensor/missions/{MID}/refill",
                      {"add_tool_calls": 5}, tok=tok)
        assert code == 409, f"T5b FAIL: honest FAIL 类 refill 返回 {code}（应 409）"
        PASS.append("T5 infra 类终局（gateway_unavailable）可 refill；honest FAIL 类（finished_without_acceptance）仍拒")

        print("\n".join(f"PASS {x}" for x in PASS))
        print(f"OK {len(PASS)} 判据全过")
        return 0
    finally:
        # 合成行 session_id=NULL（refill 只重开既有会话，不新建），无会话残留；
        # 清理失败不掩盖测试结论
        try:
            psql(f"DELETE FROM tensor_dev_missions WHERE id={MID}")
        except Exception as exc:  # noqa: BLE001
            print(f"CLEANUP-WARN: {exc}")


if __name__ == "__main__":
    sys.exit(main())
