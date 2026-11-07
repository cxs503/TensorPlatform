"""provider 层：把「机构动作」抽象成协议，实现与判据/引擎解耦。

设计约束（任务书「假设明天没有人类来」）：
  * 引擎只依赖协议，provider 可替换（pytest 用 fake，线上用 HTTP）；
  * 所有外部调用都带超时 + 失败返回结构体，不抛裸异常穿透到引擎；
  * 任何凭据只从环境/凭据文件读，**不进审计、不进报告**。

协议族：
  Queue          —— 本轮要清算的 PR 清单（来源=GitHub open PR 或平台队列）
  MissionLedger  —— 任务系统验收记录（读）+ PR 合并回写（写）
  CheckRuns      —— 分支检查结果 + main 基线失败集（相对红口径）
  DiffSource     —— PR 净变更（喂边界扫描门）
  Merger         —— 真正执行合并的动作面
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, Sequence

from .model import MissionRecord, PullRequest


class Queue(Protocol):
    def list_awaiting(self) -> List[PullRequest]: ...


class MissionLedger(Protocol):
    def find(self, repo: str, pr: int) -> Optional[MissionRecord]: ...
    def writeback(self, mission_id: int, state: str, repo: str, pr: int,
                  sha: Optional[str], note: str) -> Dict[str, Any]: ...


class CheckRuns(Protocol):
    def for_sha(self, repo: str, sha: str) -> List[Dict[str, Any]]: ...
    def main_failing(self, repo: str) -> List[str]: ...


class DiffSource(Protocol):
    def diff(self, repo: str, pr: int) -> Optional[str]: ...


class Merger(Protocol):
    def merge(self, repo: str, pr: int, sha: str,
              method: str = "merge") -> Dict[str, Any]: ...


# --------------------------------------------------------------------------- #
# HTTP 底层
# --------------------------------------------------------------------------- #
class Http:
    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def request(self, url: str, *, method: str = "GET",
                headers: Optional[Dict[str, str]] = None,
                body: Any = None, accept: str = "application/json",
                raw: bool = False):
        hdrs = {"Accept": accept, "User-Agent": "tp-merge-master/1.0"}
        hdrs.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=hdrs,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = r.read()
                if raw:
                    return {"status": r.status, "text": payload.decode(
                        "utf-8", "replace")}
                return {"status": r.status,
                        "json": json.loads(payload or b"{}")}
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf-8", "replace")
            return {"status": exc.code, "error": detail}
        except Exception as exc:  # 网络层故障也算「动作失败」，不抛穿
            return {"status": 0, "error": repr(exc)[:200]}


def _github_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if tok:
        return tok
    for path in ("/root/.git-credentials", os.path.expanduser("~/.git-credentials")):
        try:
            m = re.search(r"//[^:@\s]+:([^@\s]+)@github\.com",
                          open(path, "r", encoding="utf-8").read())
            if m:
                return m.group(1)
        except OSError:
            continue
    return ""


class GitHub:
    API = "https://api.github.com"
    OWNER = os.environ.get("TP_GH_OWNER", "cxs503")

    def __init__(self, http: Optional[Http] = None, token: Optional[str] = None):
        self.http = http or Http()
        self.token = token if token is not None else _github_token()

    @property
    def headers(self) -> Dict[str, str]:
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        return h

    # -- 读 ---------------------------------------------------------------
    def get_pr(self, repo: str, pr: int,
               retries: int = 2) -> Dict[str, Any]:
        """取 PR 详情；``mergeable`` 由 GitHub 异步计算，未定时重取一次。"""
        url = "%s/repos/%s/%s/pulls/%d" % (self.API, self.OWNER, repo, pr)
        r = self.http.request(url, headers=self.headers)
        for _ in range(max(0, retries)):
            body = r.get("json") or {}
            if body.get("mergeable") is not None or r.get("status") != 200:
                break
            time.sleep(1.2)
            r = self.http.request(url, headers=self.headers)
        return r

    def open_prs(self, repo: str, limit: int = 100) -> List[Dict[str, Any]]:
        r = self.http.request(
            "%s/repos/%s/%s/pulls?state=open&per_page=%d"
            % (self.API, self.OWNER, repo, limit), headers=self.headers)
        return r.get("json") or [] if r.get("status") == 200 else []

    def check_runs(self, repo: str, sha: str) -> List[Dict[str, Any]]:
        r = self.http.request(
            "%s/repos/%s/%s/commits/%s/check-runs?per_page=100"
            % (self.API, self.OWNER, repo, sha), headers=self.headers)
        if r.get("status") != 200:
            return []
        return list((r.get("json") or {}).get("check_runs") or [])

    def main_sha(self, repo: str) -> Optional[str]:
        r = self.http.request(
            "%s/repos/%s/%s/branches/main" % (self.API, self.OWNER, repo),
            headers=self.headers)
        if r.get("status") != 200:
            return None
        return ((r.get("json") or {}).get("commit") or {}).get("sha")

    def pr_diff(self, repo: str, pr: int) -> Optional[str]:
        r = self.http.request(
            "%s/repos/%s/%s/pulls/%d" % (self.API, self.OWNER, repo, pr),
            headers=dict(self.headers, Accept="application/vnd.github.v3.diff"),
            raw=True)
        return r.get("text") if r.get("status") == 200 else None

    def rate_left(self) -> int:
        r = self.http.request(self.API + "/rate_limit", headers=self.headers)
        core = ((r.get("json") or {}).get("resources") or {}).get("core") or {}
        return int(core.get("remaining", 0))

    # -- 写 ---------------------------------------------------------------
    def merge(self, repo: str, pr: int, sha: str,
              method: str = "merge") -> Dict[str, Any]:
        r = self.http.request(
            "%s/repos/%s/%s/pulls/%d/merge" % (self.API, self.OWNER, repo, pr),
            method="PUT", headers=self.headers,
            body={"sha": sha, "merge_method": method})
        return r


class GitHubQueue:
    """队列来源：GitHub open PR（ground truth）+ 平台 awaiting 任务补集。"""

    def __init__(self, gh: GitHub, repos: Sequence[str],
                 extra_prs: Optional[Sequence[Sequence[Any]]] = None):
        self.gh = gh
        self.repos = list(repos)
        self.extra = [(str(r), int(n)) for r, n in (extra_prs or [])]

    def _mk(self, repo: str, d: Dict[str, Any]) -> PullRequest:
        return PullRequest(
            repo=repo, number=int(d["number"]),
            head_sha=((d.get("head") or {}).get("sha") or ""),
            branch=((d.get("head") or {}).get("ref") or ""),
            state=str(d.get("state") or "open"),
            merged=bool(d.get("merged") or d.get("merged_at")),
            mergeable=d.get("mergeable"),
            mergeable_state=str(d.get("mergeable_state") or ""),
            base_ref=((d.get("base") or {}).get("ref") or "main"),
            title=str(d.get("title") or "")[:120])

    def list_awaiting(self) -> List[PullRequest]:
        out: List[PullRequest] = []
        for repo in self.repos:
            for d in self.gh.open_prs(repo):
                # 列表接口不含合并性字段 → 逐张取详情（合并性 oracle 是判据四）
                full = self.gh.get_pr(repo, int(d["number"]))
                out.append(self._mk(repo, full.get("json") or d))
        for repo, num in self.extra:
            r = self.gh.get_pr(repo, num)
            if r.get("status") == 200:
                out.append(self._mk(repo, r["json"]))
        seen, uniq = set(), []
        for pr in out:
            if pr.key in seen:
                continue
            seen.add(pr.key)
            uniq.append(pr)
        return uniq


class GitHubChecks:
    def __init__(self, gh: GitHub):
        self.gh = gh
        self._base: Dict[str, List[str]] = {}

    def for_sha(self, repo: str, sha: str) -> List[Dict[str, Any]]:
        return self.gh.check_runs(repo, sha)

    def main_failing(self, repo: str) -> List[str]:
        if repo in self._base:
            return self._base[repo]
        from .gates import failing_names
        sha = self.gh.main_sha(repo)
        names = failing_names(self.gh.check_runs(repo, sha)) if sha else []
        self._base[repo] = names
        return names


class GitHubDiffs:
    def __init__(self, gh: GitHub):
        self.gh = gh
        self._cache: Dict[str, Optional[str]] = {}

    def diff(self, repo: str, pr: int) -> Optional[str]:
        key = "%s#%d" % (repo, pr)
        if key not in self._cache:
            self._cache[key] = self.gh.pr_diff(repo, pr)
        return self._cache[key]


class GitHubMerger:
    def __init__(self, gh: GitHub, method: str = "merge"):
        self.gh = gh
        self.method = method

    def merge(self, repo: str, pr: int, sha: str,
              method: str = "") -> Dict[str, Any]:
        r = self.gh.merge(repo, pr, sha, method or self.method)
        ok = r.get("status") == 200 and bool((r.get("json") or {}).get("merged"))
        return {"ok": ok, "status": r.get("status"),
                "merge_commit": ((r.get("json") or {}).get("sha")),
                "message": ((r.get("json") or {}).get("message")
                            or r.get("error") or "")[:300]}


# --------------------------------------------------------------------------- #
# 平台任务系统（验收记录 + 回写）
# --------------------------------------------------------------------------- #
class PlatformLedger:
    """任务系统账本：读验收记录，写 pr-gate 状态（合并回写）。

    验收记录字段映射（平台 result JSON）：accepted / decided_by / checks /
    evidence / pr_gate。缺失即「验收记录不在案」。
    """

    def __init__(self, base: str, user: str, password: str,
                 http: Optional[Http] = None, page: int = 500,
                 max_pages: int = 8):
        self.base = base.rstrip("/")
        self.user, self.password = user, password
        self.http = http or Http()
        self.page = page
        self.max_pages = max_pages
        self._token = ""
        self._index: Optional[Dict[str, Any]] = None

    # -- auth -------------------------------------------------------------
    def token(self) -> str:
        if self._token:
            return self._token
        r = self.http.request(self.base + "/auth/login", method="POST",
                              body={"username": self.user,
                                    "password": self.password})
        tok = (r.get("json") or {}).get("access_token") or ""
        if not tok:
            raise RuntimeError("task-system login failed (status=%s)"
                               % r.get("status"))
        self._token = tok
        return tok

    def _h(self) -> Dict[str, str]:
        return {"Authorization": "Bearer " + self.token()}

    def get(self, path: str) -> Dict[str, Any]:
        r = self.http.request(self.base + path, headers=self._h())
        return r.get("json") or {}

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.http.request(self.base + path, method="POST",
                                 headers=self._h(), body=body)

    # -- 索引 -------------------------------------------------------------
    @staticmethod
    def _record(row: Dict[str, Any]) -> MissionRecord:
        res = row.get("result") or {}
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except ValueError:
                res = {}
        gate = res.get("pr_gate") or {}
        ev = res.get("evidence") or {}
        if isinstance(ev, str):
            ev = {}
        report_id = None
        for src in (res, ev):
            for key in ("report_id", "report", "paper_report_id"):
                if isinstance(src, dict) and src.get(key):
                    report_id = src.get(key)
                    break
            if report_id:
                break
        return MissionRecord(
            mission_id=int(row["id"]) if row.get("id") is not None else None,
            status=str(row.get("status") or ""),
            accepted=bool(res.get("accepted") is True
                          or str(res.get("accepted")).lower() == "true"),
            decided_by=str(res.get("decided_by") or ""),
            checks_text=str(res.get("checks") or ""),
            report_id=report_id if isinstance(report_id, int) else None,
            pr_number=(int(gate["pr"]) if gate.get("pr") else None),
            repo=str(row.get("repo") or gate.get("repo") or ""),
            notes=str(res.get("decision_note") or "")[:200])

    def _build_index(self) -> Dict[str, Any]:
        idx: Dict[str, Any] = {}
        latest_running: Dict[tuple, int] = {}
        for p in range(self.max_pages):
            d = self.get("/tensor/missions?limit=%d&offset=%d"
                         % (self.page, p * self.page))
            items = d.get("items") or []
            if not items:
                break
            for row in items:
                res = row.get("result") or {}
                if isinstance(res, str):
                    try:
                        res = json.loads(res)
                    except ValueError:
                        res = {}
                gate = res.get("pr_gate") or {}
                pr, repo = gate.get("pr"), gate.get("repo") or row.get("repo")
                if not pr or not repo:
                    continue
                key = "%s#%s" % (repo, pr)
                rec = self._record(row)
                # 同一 PR 可能挂过多轮任务：优先「未了结」的那条
                if key not in idx or rec.status in ("succeeded", "failed"):
                    idx.setdefault(key, rec)
                if str(row.get("status")) in ("succeeded", "awaiting_pr_merge"):
                    idx[key] = rec
                elif key not in idx:
                    idx[key] = rec
                if str(row.get("status")) == "running":
                    latest_running[(repo, int(pr))] = int(row["id"])
            if len(items) < self.page:
                break
        idx["__running__"] = latest_running
        return idx

    def index(self) -> Dict[str, Any]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def find(self, repo: str, pr: int) -> Optional[MissionRecord]:
        return self.index().get("%s#%d" % (repo, int(pr)))

    # -- 回写 -------------------------------------------------------------
    def writeback(self, mission_id: int, state: str, repo: str, pr: int,
                  sha: Optional[str], note: str) -> Dict[str, Any]:
        r = self.post("/tensor/missions/%d/pr-gate" % int(mission_id),
                      {"state": state, "repo": repo, "pr": int(pr),
                       "sha": (sha or None), "note": note[:480]})
        ok = r.get("status") == 200
        return {"ok": ok, "status": r.get("status"),
                "mission_status": (r.get("json") or {}).get("status"),
                "error": (r.get("error") or "")[:300]}


def platform_from_env(**kw: Any) -> PlatformLedger:
    """从 agent.env 读平台凭据（值绝不写日志/审计）。"""
    env = dict(os.environ)
    for path in ("/root/tp-agent/agent.env",
                 os.path.join(os.path.dirname(__file__), "..", "..", ".env")):
        try:
            for line in open(path, "r", encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
        except OSError:
            continue
    base = env.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")
    user = env.get("TENSOR_AGENT_USER") or env.get("ECO_ADMIN_USER") or ""
    pwd = env.get("TENSOR_AGENT_PASS") or env.get("ECO_ADMIN_PASS") or ""
    for path in ("/root/tp-agent/eco_admin.env",):
        if user and pwd:
            break
        try:
            for line in open(path, "r", encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
        except OSError:
            continue
    user = user or env.get("ECO_ADMIN_USER", "")
    pwd = pwd or env.get("ECO_ADMIN_PASS", "")
    return PlatformLedger(base, user, pwd, **kw)