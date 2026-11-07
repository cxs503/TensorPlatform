"""daemon — 常设补给器主循环。

容器 daemon：队列深度低于阈值（pending 任务节点=0 且 idle worker≥6）时，
从已文档化缺口账本自动生成 mission。

触发条件:
  - pending_missions == 0（无排队任务）
  - idle_workers >= 6（至少 6 个空闲 worker）

循环间隔: 60 秒（可配置）
存活证明: 每次循环记录 liveness 审计条目
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from .audit import AuditLogger
from .gap_ledger import GapLedgerSource
from .mission_generator import MissionGenerator

logger = logging.getLogger(__name__)

# 默认阈值
DEFAULT_PENDING_THRESHOLD = 0       # pending 任务数阈值
DEFAULT_IDLE_WORKER_THRESHOLD = 6   # idle worker 数阈值
DEFAULT_LOOP_INTERVAL = 60          # 循环间隔（秒）
DEFAULT_MAX_REFILL_PER_CYCLE = 3    # 每轮最多生成 mission 数


class BacklogRefillerDaemon:
    """常设补给器 daemon。

    参数:
        db_session_factory: SQLAlchemy session 工厂函数
        api_base: 平台 API 基础 URL
        pending_threshold: pending 任务数阈值
        idle_worker_threshold: idle worker 数阈值
        loop_interval: 循环间隔（秒）
        max_refill_per_cycle: 每轮最多生成 mission 数
        daemon_id: daemon 标识
    """

    def __init__(
        self,
        db_session_factory,
        api_base: str = "http://localhost:8001",
        pending_threshold: int = DEFAULT_PENDING_THRESHOLD,
        idle_worker_threshold: int = DEFAULT_IDLE_WORKER_THRESHOLD,
        loop_interval: int = DEFAULT_LOOP_INTERVAL,
        max_refill_per_cycle: int = DEFAULT_MAX_REFILL_PER_CYCLE,
        daemon_id: str = "backlog_refiller_01",
    ):
        self.db_factory = db_session_factory
        self.api_base = api_base
        self.pending_threshold = pending_threshold
        self.idle_worker_threshold = idle_worker_threshold
        self.loop_interval = loop_interval
        self.max_refill_per_cycle = max_refill_per_cycle
        self.daemon_id = daemon_id
        self.start_time = datetime.now(timezone.utc)
        self.running = False
        self.cycle_count = 0
        self.refill_count = 0

    def _get_session(self):
        """获取数据库会话。"""
        return self.db_factory()

    def check_queue_depth(self) -> dict[str, Any]:
        """检查队列深度。

        返回:
            {
                "pending_missions": int,
                "idle_workers": int,
                "should_trigger": bool,
            }
        """
        from sqlalchemy import text
        session = self._get_session()
        try:
            # 查询 pending 任务数
            pending = session.execute(text("""
                SELECT count(*) FROM tensor_dev_missions
                WHERE status = 'queued'
            """)).scalar()

            # 查询 idle worker 数（heartbeat 在 90 秒内且无当前任务）
            idle_workers = session.execute(text("""
                SELECT count(*) FROM factory_resident_workers
                WHERE enabled = true
                  AND heartbeat_at > now() - interval '90 seconds'
                  AND worker_id NOT IN (
                      SELECT DISTINCT claimed_by FROM tensor_dev_missions
                      WHERE status IN ('claimed', 'running')
                        AND claimed_by IS NOT NULL
                  )
            """)).scalar()

            should_trigger = (
                pending <= self.pending_threshold
                and idle_workers >= self.idle_worker_threshold
            )

            return {
                "pending_missions": pending,
                "idle_workers": idle_workers,
                "should_trigger": should_trigger,
            }
        finally:
            session.close()

    def check_queue_depth_static(self, pending: int, idle_workers: int) -> bool:
        """静态检查队列深度（用于测试）。

        参数:
            pending: pending 任务数
            idle_workers: idle worker 数

        返回:
            是否应该触发补给
        """
        return (
            pending <= self.pending_threshold
            and idle_workers >= self.idle_worker_threshold
        )

    def refill_cycle(self) -> list[dict[str, Any]]:
        """执行一轮补给。

        返回:
            生成的 mission 列表
        """
        session = self._get_session()
        try:
            audit = AuditLogger(session)
            gap_source = GapLedgerSource(session, self.api_base)
            generator = MissionGenerator(session)

            # 收集缺口
            gaps = gap_source.collect_gaps()
            gaps = gap_source.filter_by_whitelist(gaps)
            logger.info("[refiller] 收集到 %d 个缺口", len(gaps))

            generated = []
            for gap in gaps[:self.max_refill_per_cycle]:
                params = generator.generate_mission_params(gap)
                if params is None:
                    continue

                # 创建 mission
                from sqlalchemy import text
                org_id = session.execute(text(
                    "SELECT organization_id FROM tensor_dev_missions LIMIT 1"
                )).scalar()
                if org_id is None:
                    org_id = 1  # 默认组织

                budget = params.pop("budget")
                ext_id = params.pop("ext_id")
                ext_id_hash = params.pop("ext_id_hash")
                gap_source_name = params.pop("gap_source")
                gap_priority = params.pop("gap_priority")

                import json
                budget_json = json.dumps(budget, ensure_ascii=False)
                result_json = json.dumps({
                    "ext_id": ext_id,
                    "ext_id_hash": ext_id_hash,
                    "gap_source": gap_source_name,
                    "gap_priority": gap_priority,
                    "generated_by": self.daemon_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False)

                mission_id = session.execute(text("""
                    INSERT INTO tensor_dev_missions
                        (organization_id, goal, repo, budget, runner, origin, result, status)
                    VALUES (:org, :goal, :repo, CAST(:budget AS jsonb), :runner, :origin,
                            CAST(:result AS jsonb), 'queued')
                    RETURNING id
                """), {
                    "org": org_id,
                    "goal": params["goal"],
                    "repo": params["repo"],
                    "budget": budget_json,
                    "runner": params["runner"],
                    "origin": params["origin"],
                    "result": result_json,
                }).scalar()
                session.commit()

                # 记录审计
                rollback_sql = generator.build_rollback_sql(mission_id)
                audit.log(
                    action="refill",
                    mission_id=mission_id,
                    ext_id=ext_id,
                    details={
                        "gap_source": gap_source_name,
                        "repo": params["repo"],
                        "daemon_id": self.daemon_id,
                    },
                    rollback_sql=rollback_sql,
                )

                generated.append({
                    "mission_id": mission_id,
                    "ext_id": ext_id,
                    "repo": params["repo"],
                })
                self.refill_count += 1
                logger.info("[refiller] 生成 mission m%d (ext_id=%s)", mission_id, ext_id)

            return generated
        except Exception as exc:
            session.rollback()
            logger.error("[refiller] 补给失败: %s", exc)
            raise
        finally:
            session.close()

    def run_once(self) -> dict[str, Any]:
        """执行单次检查与可能的补给。

        返回:
            {
                "triggered": bool,
                "queue_depth": dict,
                "generated_missions": list,
                "cycle_count": int,
            }
        """
        self.cycle_count += 1
        queue_depth = self.check_queue_depth()

        result = {
            "triggered": False,
            "queue_depth": queue_depth,
            "generated_missions": [],
            "cycle_count": self.cycle_count,
        }

        if queue_depth["should_trigger"]:
            logger.info("[refiller] 触发补给 (pending=%d, idle=%d)",
                        queue_depth["pending_missions"], queue_depth["idle_workers"])
            generated = self.refill_cycle()
            result["triggered"] = True
            result["generated_missions"] = generated
        else:
            logger.debug("[refiller] 未触发补给 (pending=%d, idle=%d)",
                         queue_depth["pending_missions"], queue_depth["idle_workers"])

        # 记录存活心跳
        session = self._get_session()
        try:
            audit = AuditLogger(session)
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()
            audit.log_liveness(self.daemon_id, uptime)
        finally:
            session.close()

        return result

    def run_loop(self, max_cycles: int | None = None):
        """运行主循环。

        参数:
            max_cycles: 最大循环次数（None=无限）
        """
        self.running = True
        logger.info("[refiller] daemon 启动 id=%s", self.daemon_id)

        cycle = 0
        while self.running:
            if max_cycles is not None and cycle >= max_cycles:
                break

            try:
                result = self.run_once()
                logger.info("[refiller] cycle=%d triggered=%s generated=%d",
                            cycle, result["triggered"], len(result["generated_missions"]))
            except Exception as exc:
                logger.error("[refiller] cycle=%d 异常: %s", cycle, exc)

            cycle += 1
            time.sleep(self.loop_interval)

        self.running = False
        logger.info("[refiller] daemon 停止 id=%s cycles=%d", self.daemon_id, cycle)

    def stop(self):
        """停止 daemon。"""
        self.running = False
        logger.info("[refiller] 收到停止信号")

    @property
    def uptime_seconds(self) -> float:
        """获取运行时间（秒）。"""
        return (datetime.now(timezone.utc) - self.start_time).total_seconds()
