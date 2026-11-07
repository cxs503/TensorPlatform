"""daemon —— 质询官 24h 主循环。

每周期动作（全部留痕）：
  1. 采集信号（失败不中断：信号缺失本身是质询对象）→ 落 signals_<date>.json
  2. 选 3-5 题（跨族轮转 + 7 周期去重）→ 落 cycle_<date>.json
  3. 写账本（题数/指纹不变量）
  4. （可选）向平台立单 ``POST /tensor/missions``，task_key=interrogator:<fp> 幂等
  5. 回写 mission_id 到账本，append 审计

``create_missions=False`` 时是纯演练（dry-run）：只产出题目与账本，
不消耗舰队算力——首次上线建议先跑一轮 dry-run 校准题面。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .generator import build_cycle, summarize
from .ledger import MAX_PER_CYCLE, MIN_PER_CYCLE, Ledger
from .probes import HttpSignalSource, SignalSource, save_snapshot

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = os.path.join("runs", "IN27-H4")
DEFAULT_INTERVAL_SECONDS = 24 * 3600


class MissionPoster:
    """向平台立单（幂等：同一 task_key 返回既有单号）。"""

    def __init__(self, api_base: str, token: str | None = None, timeout: float = 25.0):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._token = token
        self._source = HttpSignalSource(api_base=self.api_base, token=token, timeout=timeout)

    def _auth(self) -> str | None:
        token = self._token or self._source.login()
        self._token = token
        return token

    def post(self, params: dict[str, Any]) -> dict[str, Any]:
        token = self._auth()
        if not token:
            return {"ok": False, "error": "no_token"}
        req = urllib.request.Request(
            f"{self.api_base}/tensor/missions",
            data=json.dumps(params).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            body = json.loads(urllib.request.urlopen(req, timeout=self.timeout).read())
            return {"ok": True, "mission_id": body.get("id"), "idempotent": body.get("idempotent", False)}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"http_{exc.code}",
                    "body": exc.read()[:400].decode("utf-8", "ignore")}
        except Exception as exc:  # noqa: BLE001 - 记录后继续，绝不因立单失败丢账
            return {"ok": False, "error": f"{type(exc).__name__}:{exc}"}


class InterrogatorDaemon:
    def __init__(
        self,
        ledger_path: str,
        source: SignalSource | None = None,
        api_base: str | None = None,
        out_dir: str = DEFAULT_OUT_DIR,
        interval: int = DEFAULT_INTERVAL_SECONDS,
        repo: str = "TensorPlatform",
    ):
        self.ledger_path = ledger_path
        self.out_dir = out_dir
        self.interval = interval
        self.repo = repo
        if source is not None:
            self.source = source
        elif api_base:
            self.source = HttpSignalSource(api_base=api_base)
        else:
            self.source = HttpSignalSource()
        self.poster = MissionPoster(api_base) if api_base else None

    # -- one cycle ---------------------------------------------------------
    def run_once(
        self,
        as_of: datetime | None = None,
        create_missions: bool = False,
        mode: str = "live",
    ) -> dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        cycle_date = as_of.date().isoformat()
        os.makedirs(self.out_dir, exist_ok=True)

        signals = self.source.collect(window_days=7, as_of=as_of)
        snapshot_path = os.path.join(self.out_dir, f"signals_{cycle_date}.json")
        if mode == "live":
            save_snapshot(signals, snapshot_path)
        statuses = {s.status for s in signals.values()}
        source_status = "ok" if statuses == {"ok"} else (
            "unavailable" if statuses == {"unavailable"} else "partial")

        with Ledger(self.ledger_path) as ledger:
            stats = ledger.stats(as_of=as_of)
            recent = ledger.recent_fingerprints(window_cycles=7)
            bundle = build_cycle(signals, stats, recent, cycle_date, repo=self.repo)
            questions = [item["question"] for item in bundle]
            cycle_id = ledger.record_cycle(
                cycle_date=cycle_date,
                questions=[_as_question(item) for item in bundle],
                mode=mode,
                source_status=source_status,
                artifact_path=os.path.join(self.out_dir, f"cycle_{cycle_date}.json"),
                notes=f"source={type(self.source).__name__}",
            )

            posted: list[dict[str, Any]] = []
            for item in bundle:
                fp = item["question"]["fingerprint"]
                result = {"fingerprint": fp, "created": False}
                if create_missions and self.poster is not None and mode == "live":
                    r = self.poster.post(item["mission"])
                    result.update(r)
                    if r.get("ok"):
                        ledger.attach_mission(fp, r.get("mission_id"), item["mission"]["task_key"])
                posted.append(result)

            artifact = {
                "mechanism": "IN27-H4",
                "cycle_id": cycle_id,
                "cycle_date": cycle_date,
                "mode": mode,
                "as_of": as_of.isoformat(),
                "source": type(self.source).__name__,
                "source_status": source_status,
                "signals": {k: v.to_dict() for k, v in signals.items()},
                "summary": summarize([_as_question(i) for i in bundle]),
                "questions": [
                    {**i["question"], "mission_params": {k: v for k, v in i["mission"].items()
                                                         if k != "goal"}}
                    for i in bundle
                ],
                "posted": posted,
                "created_missions": sum(1 for p in posted if p.get("ok")),
            }
            cycle_path = os.path.join(self.out_dir, f"cycle_{cycle_date}.json")
            with open(cycle_path, "w", encoding="utf-8") as fh:
                json.dump(artifact, fh, ensure_ascii=False, indent=2, default=str)
            artifact["artifact_path"] = cycle_path
            artifact["snapshot_path"] = snapshot_path if mode == "live" else None
            artifact["ledger"] = os.path.abspath(self.ledger_path)
            logger.info("cycle %s: %d questions, %d missions, source_status=%s",
                        cycle_id, len(bundle), artifact["created_missions"], source_status)
            return artifact

    # -- loop --------------------------------------------------------------
    def serve_forever(self, create_missions: bool = False, max_cycles: int | None = None) -> None:
        done = 0
        while True:
            try:
                self.run_once(create_missions=create_missions)
            except Exception:  # noqa: BLE001 - 单周期失败不杀死守护（下一周期再来）
                logger.exception("cycle failed; will retry next interval")
            done += 1
            if max_cycles is not None and done >= max_cycles:
                return
            time.sleep(self.interval)


def _as_question(item: dict[str, Any]):
    """把序列化后的 question dict 还原成 Question（ledger 只吃对象）。"""
    from .question_bank import Question

    q = item["question"]
    return Question(
        key=q["key"], family=q["family"], title=q["title"], text=q["text"],
        evidence=q.get("evidence", []), branches=q.get("branches", []),
        criteria=q.get("criteria", []), convert_rule=q.get("convert_rule", ""),
        severity=int(q.get("severity", 3)), target_repo=q.get("target_repo", "TensorPlatform"),
    )