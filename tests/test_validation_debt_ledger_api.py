"""验证欠账台账的 **API 数据源** 机器判据（内联假 fetch，不依赖网络/数据库）。

被推翻的命题（每条断言对应一条）：
- P1 登录必须先用 TENSOR_AGENT_USER/PASS 换取 token，之后每个请求都带 Bearer；
- P2 列表端点不含正文 ⇒ 必须逐条取详情拿 markdown（N+1），只列 id 的实现在此翻红；
- P3 ``limit`` 必须真的截断（否则台账口径与 --limit 不符）；
- P4 缺凭据必须抛 LedgerSourceError（可被 auto 选源捕获退让），不允许静默返回 []；
- P5 auto 选源优先级：--from-json → (有 agent 凭据) api → psql，且某源失败要退让；
- P6 API 源与 JSON 源的报告经 build_ledger 后**口径一致**（同一条欠账抽出同样条目）。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import validation_debt_ledger as L  # noqa: E402


MD_A = """# 报告 A

## ⑤ 注意点（局限与待办）
- 冰区阻力链路仍是 STUB：`backend/services/ice_resistance.py` 仅返回占位值。
- KVLCC2 湿面积缺失，参考阻力量 WITHHELD，未实测。
"""

MD_B = """# 报告 B

## 局限
- COM 横摇惯性矩缺项，横摇周期 WITHHELD。
"""


def _make_fetch(seen, ids=(3, 1, 2)):
    def fetch(url, payload=None, token=None):
        seen.append((url, payload, token))
        if url.endswith("/auth/login"):
            return {"access_token": "tok-123"}
        if "/tensor/reports?" in url or url.endswith("/tensor/reports"):
            return [{"id": i, "title": f"R{i}"} for i in ids]
        rid = int(url.rsplit("/", 1)[-1])
        return {"id": rid, "title": f"R{rid}", "markdown": MD_A if rid == 1 else MD_B,
                "mission_id": 100 + rid, "scenario_key": "x.y"}

    return fetch


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setenv("TENSOR_AGENT_USER", "u")
    monkeypatch.setenv("TENSOR_AGENT_PASS", "p")
    monkeypatch.setenv("TENSOR_API_BASE", "http://api.test/api/v1")


def test_login_then_bearer_and_details(monkeypatch):
    seen: list = []
    reps = L.load_reports_api(limit=None, fetch=_make_fetch(seen))
    # P1：先登录，且凭据来自环境变量
    assert seen[0][0].endswith("/auth/login") and seen[0][1] == {"username": "u", "password": "p"}
    assert seen[0][2] is None
    assert all(tok == "tok-123" for _u, _p, tok in seen[1:])
    # P2：列表之后逐条取详情，markdown 真的进了结果
    detail_urls = [u for u, _p, _t in seen if "/tensor/reports/" in u]
    assert len(detail_urls) == 3
    assert [r["id"] for r in reps] == [1, 2, 3]           # 按 id 升序（确定性）
    assert reps[0]["markdown"] == MD_A and "占位值" not in reps[1]["markdown"]
    assert reps[0]["mission_id"] == 101


def test_limit_truncates_before_details():
    seen: list = []
    reps = L.load_reports_api(limit=2, fetch=_make_fetch(seen))
    # P3：limit 生效；列表请求也带上 limit 参数
    assert any("limit=2" in u for u, _p, _t in seen)
    assert len(reps) == 2 and [r["id"] for r in reps] == [1, 3]   # ids 顺序 (3,1) 截前 2


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("TENSOR_AGENT_USER", raising=False)
    monkeypatch.delenv("TENSOR_AGENT_PASS", raising=False)
    # P4：缺凭据 ⇒ 可捕获异常（auto 选源据此退让），不是静默空表
    with pytest.raises(L.LedgerSourceError):
        L.load_reports_api(limit=5, fetch=_make_fetch([]))


def test_http_error_becomes_ledger_source_error():
    def boom(url, payload=None, token=None):
        raise L.LedgerSourceError(f"{url} → HTTP 401")

    with pytest.raises(L.LedgerSourceError):
        L.load_reports_api(limit=5, fetch=boom)


def _args(**kw):
    base = dict(source="auto", from_json=None, limit=10, api_base=None, repo_root=".",
                search_roots="", out="/tmp/x", top=5, figures=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_auto_source_prefers_json_then_api_then_psql(tmp_path, monkeypatch):
    snap = tmp_path / "snap.json"
    snap.write_text(f'[{{"id": 1, "markdown": {MD_A!r}}}]'.replace("'", '"'), encoding="utf-8")
    # P5a：给了快照就走快照，不碰网络
    assert L._resolve_source(_args(from_json=str(snap))) == "json"
    # P5b：无快照 + 有 agent 凭据 ⇒ api
    assert L._resolve_source(_args()) == "api"
    # P5c：无凭据 ⇒ psql
    monkeypatch.delenv("TENSOR_AGENT_USER")
    monkeypatch.delenv("TENSOR_AGENT_PASS")
    assert L._resolve_source(_args()) == "psql"


def test_auto_falls_back_when_api_unavailable(monkeypatch):
    """P5d：api 源挂掉 ⇒ 退让到 psql 源；全挂 ⇒ SystemExit 且消息带原因。"""
    logs = []
    monkeypatch.setattr(L, "load_reports_api", lambda *a, **kw: (_ for _ in ()).throw(
        L.LedgerSourceError("api down")))
    monkeypatch.setattr(L, "load_reports_psql", lambda limit=None: [{"id": 7, "markdown": MD_A}])
    monkeypatch.setenv("TP_PG_PASSWORD", "dummy")
    reps = L.load_reports(_args(), log=logs.append)
    assert [r["id"] for r in reps] == [7]
    assert any("api" in m and "退让" in m for m in logs)

    monkeypatch.setattr(L, "load_reports_psql", lambda limit=None: (_ for _ in ()).throw(SystemExit("psql down")))
    with pytest.raises(SystemExit):
        L.load_reports(_args(), log=logs.append)


def test_api_source_parity_with_json_source():
    """P6：同一批报告，API 源与 JSON 源在台账口径上完全一致。"""
    seen: list = []
    api_reps = L.load_reports_api(limit=None, fetch=_make_fetch(seen))
    json_reps = [dict(r) for r in api_reps]
    a = L.build_ledger(api_reps, repo_root=".", search_roots=())
    b = L.build_ledger(json_reps, repo_root=".", search_roots=())
    for key in ("reports_scanned", "reports_with_debt_section", "items_total", "clusters_total"):
        assert a["stats"][key] == b["stats"][key]
    assert a["stats"]["items_total"] >= 2
    assert {it["report_id"] for it in a["items"]} == {1, 2, 3}