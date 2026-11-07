"""probes —— 质询官的事实底座。

纪律（与平台 TRUTH 门一致）：
  * 每个信号都必须带 ``source`` 与 ``status``；探针失败记 ``unavailable``，
    **绝不补零**——补零会把「没测」伪装成「测到 0」，质询会因此问错。
  * 只用标准库（urllib），不引入后端依赖，便于 cron/daemon 独立运行。
  * 探针是只读的：GET 只读端点，不做任何写操作（立单在 generator/daemon 里）。

信号清单见 ``SIGNAL_NAMES``。历史回放（backfill）时用 ``as_of`` 截断，
保证「7 天连续」的每一天都只用当日可见数据。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

DEFAULT_API_BASE = os.environ.get("TENSOR_API_BASE", "http://127.0.0.1:8010/api/v1")

SIGNAL_NAMES = (
    "missions_by_status",       # dict[status -> n]
    "missions_terminal_window",  # 窗口内终态单数
    "missions_succeeded_window",
    "missions_honest_fail_window",
    "missions_failed_window",
    "missions_pending",          # 队列深度（pending + queued）
    "mission_grade_counts",      # dict[grade -> n]（result.evidence_grade）
    "reports_window",            # 窗口内报告数
    "reports_approved_window",
    "plans_budget",              # dict: total / zero_actual / examples
    "capability_grade_counts",   # 能力清单 grade 分布（平台暂无该端点→unavailable）
    "capability_coverage",       # 能力增量覆盖率（succeeded 单里有多少真入账）
    "playbook_entries",          # PLAYBOOK 条目数（经验积累轨）
    "zero_flow_organs",          # 零流器官名单（可得时）
)

#: 时间戳字段：终态单优先 finished_at，其次 created_at。
_TS_FIELDS = ("finished_at", "created_at", "updated_at")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        txt = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class Signal:
    """一条事实。``status`` ∈ {ok, partial, unavailable}。"""

    name: str
    value: Any
    source: str
    status: str = "ok"
    detail: str = ""
    captured_at: str = field(default_factory=lambda: _now().isoformat())

    @property
    def usable(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["usable"] = self.usable
        return d


class SignalSource:
    """信号源接口：``collect(window_days, as_of) -> dict[name -> Signal]``。"""

    label = "abstract"

    def collect(self, window_days: int = 7, as_of: datetime | None = None) -> dict[str, Signal]:
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _missing(name: str, source: str, detail: str) -> Signal:
        return Signal(name=name, value=None, source=source, status="unavailable", detail=detail)


class MappingSignalSource(SignalSource):
    """离线/测试用信号源：直接给一份 name -> value 或 name -> Signal 映射。"""

    label = "mapping"

    def __init__(self, mapping: dict[str, Any], label: str = "mapping"):
        self._mapping = dict(mapping)
        self.label = label

    def collect(self, window_days: int = 7, as_of: datetime | None = None) -> dict[str, Signal]:
        out: dict[str, Signal] = {}
        for name, raw in self._mapping.items():
            if isinstance(raw, Signal):
                out[name] = raw
            else:
                out[name] = Signal(name=name, value=raw, source=self.label)
        return out


class HttpSignalSource(SignalSource):
    """只读 HTTP 信号源（平台 :8010/api/v1）。

    token 可通过构造参数或 ``TENSOR_AGENT_USER``/``TENSOR_AGENT_PASS`` 现场登录获取。
    任何端点失败都不抛出，只把相关信号标成 unavailable / partial。
    """

    label = "http"

    def __init__(
        self,
        api_base: str = DEFAULT_API_BASE,
        token: str | None = None,
        timeout: float = 20.0,
        playbook_paths: Iterable[str] = (),
        mass_limit: int = 200,
    ):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._token = token
        self._mass_limit = mass_limit
        self._playbook_paths = tuple(playbook_paths) or (
            os.environ.get("TP_BOOTSTRAP", "/root/tp-agent/AGENT_BOOTSTRAP.md"),
            os.path.join(os.path.dirname(__file__), "..", "..", "AGENT_BOOTSTRAP.md"),
        )

    # -- transport ---------------------------------------------------------
    def login(self) -> str | None:
        user = os.environ.get("TENSOR_AGENT_USER")
        password = os.environ.get("TENSOR_AGENT_PASS")
        if not user or not password:
            return None
        payload = json.dumps({"username": user, "password": password}).encode()
        req = urllib.request.Request(
            f"{self.api_base}/auth/login",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            body = json.loads(urllib.request.urlopen(req, timeout=self.timeout).read())
        except Exception:
            return None
        return body.get("access_token") or body.get("token")

    def _get(self, path: str) -> tuple[Any, str]:
        token = self._token or self.login()
        if not token:
            return None, "no_token"
        self._token = token
        url = f"{self.api_base}{path}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=self.timeout).read()), ""
        except urllib.error.HTTPError as exc:  # 4xx/5xx 也是事实
            return None, f"http_{exc.code}"
        except Exception as exc:  # 网络/解析
            return None, f"{type(exc).__name__}:{exc}"

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data", "missions", "reports", "plans", "capabilities"):
                got = payload.get(key)
                if isinstance(got, list):
                    return [x for x in got if isinstance(x, dict)]
            if "id" in payload:
                return [payload]
        return []

    # -- collect -----------------------------------------------------------
    def collect(self, window_days: int = 7, as_of: datetime | None = None) -> dict[str, Signal]:
        as_of = as_of or _now()
        since = as_of - timedelta(days=window_days)
        out: dict[str, Signal] = {}

        missions, err = self._get(f"/tensor/missions?limit={self._mass_limit}")
        if missions is None:
            for name in ("missions_by_status", "missions_terminal_window",
                         "missions_succeeded_window", "missions_honest_fail_window",
                         "missions_failed_window", "missions_pending",
                         "mission_grade_counts"):
                out[name] = self._missing(name, "http:/tensor/missions", err)
        else:
            out.update(self._derive_missions(self._items(missions), since, as_of))

        reports, err = self._get(f"/tensor/reports?limit={self._mass_limit}")
        if reports is None:
            for name in ("reports_window", "reports_approved_window"):
                out[name] = self._missing(name, "http:/tensor/reports", err)
        else:
            out.update(self._derive_reports(self._items(reports), since))

        plans, err = self._get("/tensor/campaigns/plans")
        if plans is None:
            out["plans_budget"] = self._missing("plans_budget", "http:/tensor/campaigns/plans", err)
        else:
            out["plans_budget"] = Signal(
                "plans_budget",
                self._derive_plans_budget(self._items(plans)),
                "http:/tensor/campaigns/plans",
            )

        # 平台能力中心没有「能力清单+grade」端点（只有 /deltas 与 /summary，
        # 2026-11-07 实测），因此 grade 分布只能从 mission.result 派生；
        # 覆盖率端点则真实存在，是「成果是否入账」的硬信号。
        out["capability_grade_counts"] = self._missing(
            "capability_grade_counts", "http:/tensor/capability", "平台无能力 grade 端点")
        summary, err = self._get("/tensor/capability/summary")
        if summary is None:
            out["capability_coverage"] = self._missing(
                "capability_coverage", "http:/tensor/capability/summary", err)
        else:
            covered = summary.get("covered_succeeded") or 0
            succeeded = summary.get("succeeded_missions") or summary.get("succeeded") or 0
            out["capability_coverage"] = Signal(
                "capability_coverage",
                {"covered": covered, "succeeded": succeeded,
                 "ratio": summary.get("coverage_ratio")
                 if summary.get("coverage_ratio") is not None else
                 (round(covered / succeeded, 4) if succeeded else None),
                 "total_deltas": summary.get("total_deltas"),
                 "substantive_deltas": summary.get("substantive_deltas"),
                 "no_substantive_deltas": summary.get("no_substantive_deltas"),
                 "by_category": summary.get("by_category") or {}},
                "http:/tensor/capability/summary",
            )

        out["playbook_entries"] = self._probe_playbook()
        # 零流器官：平台侧尚无稳定的「器官吞吐」端点，如实记 unavailable，
        # 这份不可得本身就是质询族 ZERO_FLOW 的一等事实。
        out["zero_flow_organs"] = self._missing(
            "zero_flow_organs", "http:(none)", "平台无器官吞吐只读端点")
        return out

    # -- derivation --------------------------------------------------------
    @staticmethod
    def _within(row: dict[str, Any], since: datetime) -> bool:
        for fld in _TS_FIELDS:
            ts = _parse_ts(row.get(fld))
            if ts is not None:
                return ts >= since
        return False

    def _derive_missions(self, rows: list[dict[str, Any]], since: datetime,
                        as_of: datetime) -> dict[str, Signal]:
        by_status: dict[str, int] = {}
        grades: dict[str, int] = {}
        terminal = succeeded = honest_fail = failed = 0
        for row in rows:
            status = str(row.get("status") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            result = row.get("result") or {}
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:
                    result = {}
            grade = result.get("evidence_grade") or result.get("grade")
            if grade:
                grades[str(grade)] = grades.get(str(grade), 0) + 1
            if status in ("succeeded", "failed", "stopped", "cancelled") and self._within(row, since):
                terminal += 1
                if status == "succeeded":
                    succeeded += 1
                if status == "failed":
                    failed += 1
                verdict = str(result.get("verdict") or result.get("status") or "").upper()
                if "FAIL" in verdict:
                    honest_fail += 1
        src = "http:/tensor/missions"
        return {
            "missions_by_status": Signal("missions_by_status", by_status, src,
                                         detail=f"rows={len(rows)}"),
            "missions_terminal_window": Signal("missions_terminal_window", terminal, src),
            "missions_succeeded_window": Signal("missions_succeeded_window", succeeded, src),
            "missions_honest_fail_window": Signal("missions_honest_fail_window", honest_fail, src),
            "missions_failed_window": Signal("missions_failed_window", failed, src),
            "missions_pending": Signal("missions_pending",
                                       by_status.get("pending", 0) + by_status.get("queued", 0),
                                       src, detail="pending+queued"),
            "mission_grade_counts": Signal("mission_grade_counts", grades, src,
                                           status="ok" if grades else "partial",
                                           detail="" if grades else "result 无 grade 字段"),
        }

    def _derive_reports(self, rows: list[dict[str, Any]], since: datetime) -> dict[str, Signal]:
        window = [r for r in rows if self._within(r, since)]
        approved = 0
        for rep in window:
            verdict = str(rep.get("verdict") or rep.get("status") or "").upper()
            if "APPROV" in verdict or "PASS" in verdict:
                approved += 1
        return {
            "reports_window": Signal("reports_window", len(window), "http:/tensor/reports"),
            "reports_approved_window": Signal("reports_approved_window", approved,
                                              "http:/tensor/reports"),
        }

    @staticmethod
    def _derive_plans_budget(rows: list[dict[str, Any]]) -> dict[str, Any]:
        zero_actual, examples = 0, []
        for plan in rows:
            actual = plan.get("budget_actual") or {}
            if isinstance(actual, str):
                try:
                    actual = json.loads(actual)
                except Exception:
                    actual = {}
            planned = plan.get("budget_planned") or {}
            if not isinstance(planned, dict) or not planned:
                continue
            if all(not (actual.get(k) or 0) for k in actual) and actual:
                zero_actual += 1
                if len(examples) < 3:
                    examples.append({"plan_id": plan.get("id"),
                                     "name": plan.get("name"),
                                     "planned": planned, "actual": actual})
        return {"total": len(rows), "zero_actual": zero_actual, "examples": examples}

    def _probe_playbook(self) -> Signal:
        for path in self._playbook_paths:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            entries = sum(1 for line in text.splitlines() if line.lstrip().startswith("- 20"))
            return Signal("playbook_entries", entries, f"file:{os.path.abspath(path)}",
                          detail=f"chars={len(text)}")
        return self._missing("playbook_entries", "file:(none)", "BOOTSTRAP/PLAYBOOK 不可读")


def _count_grades(rows: list[dict[str, Any]]) -> dict[str, int]:
    grades: dict[str, int] = {}
    for row in rows:
        grade = row.get("grade") or row.get("evidence_grade") or row.get("level")
        if grade:
            key = str(grade)
            grades[key] = grades.get(key, 0) + 1
    return grades


def save_snapshot(signals: dict[str, Signal], path: str) -> None:
    """把一次采集的原始信号落盘（审计链：质询引用的每个数字都能回溯）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({k: v.to_dict() for k, v in signals.items()}, fh,
                  ensure_ascii=False, indent=2, default=str)


def load_snapshot(path: str) -> dict[str, Signal]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out: dict[str, Signal] = {}
    for name, item in raw.items():
        out[name] = Signal(
            name=name, value=item.get("value"), source=item.get("source", f"file:{path}"),
            status=item.get("status", "ok"), detail=item.get("detail", ""),
            captured_at=item.get("captured_at") or _now().isoformat(),
        )
    return out