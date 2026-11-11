"""跨仓能力依赖图谱 API — /api/v1/capability-graph/*

把 ``tools/capability_graph``（依赖图 + 级联传播 + 预警）暴露为可调用端点：

=========================================  ==========================================
端点                                        内容
=========================================  ==========================================
GET  /capability-graph                      图快照：节点（含证据等级/自身置信度）、边
                                            （含权重/来源）、图摘要、最近预警、判据自检
GET  /capability-graph/edges                仅依赖边（轻量，供前端渲染/对账）
GET  /capability-graph/alerts               最近预警（受影响清单 + 端到端延迟）
GET  /capability-graph/metrics              性能与来源元数据（缓存命中、构建耗时预算）
POST /capability-graph/simulate             级联模拟：{capability_id, to_level} → 传播结果
POST /capability-graph/refresh              丢弃缓存重建（来源文件变更后手动触发）
=========================================  ==========================================

纪律：
* 端点**只读图/只读台账**，不写台账、不改等级；``simulate`` 是只读模拟（造派生快照）。
* 零领域语义：能力 id 是不透明路径参数，依赖边/权重/等级词表全部来自配置与台账工件。
* 性能：GY 快照在首次请求时构建并缓存（键=来源文件 mtime 指纹），稳态响应不再读盘；
  ``X-Capability-Graph-Cache`` 头显式告知 hit/miss，避免用缓存冒充实时。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

_API_ROOT = Path(__file__).resolve().parents[2]
if str(_API_ROOT) not in sys.path:  # 让 backend/api 能 import 仓内 tools 包
    sys.path.insert(0, str(_API_ROOT))

from api.deps import require_auth  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/capability-graph", tags=["能力依赖图谱"])

_ENGINE_NAME = "tp_capability_graph"  # 私有包名：避开 backend/tools 影子包
CONFIG_REL = Path("config/capability_graph.json")


def load_engine_module():
    """按文件路径加载 ``tools/capability_graph`` 包（避免 ``tools`` 解析成 backend/tools）。"""
    if _ENGINE_NAME in sys.modules:
        return sys.modules[_ENGINE_NAME]
    pkg_dir = _API_ROOT / "tools" / "capability_graph"
    init = pkg_dir / "__init__.py"
    if not init.exists():
        raise HTTPException(status_code=503, detail=f"引擎缺失: {pkg_dir}")
    spec = importlib.util.spec_from_file_location(
        _ENGINE_NAME, init, submodule_search_locations=[str(pkg_dir)]
    )
    if spec is None or spec.loader is None:
        raise HTTPException(status_code=503, detail=f"引擎不可加载: {pkg_dir}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_ENGINE_NAME] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(_ENGINE_NAME, None)
        raise HTTPException(status_code=503, detail=f"引擎加载失败: {exc}") from exc
    return mod


class SimulateRequest(BaseModel):
    capability_id: str = Field(..., description="能力 id（不透明字符串，来自台账）")
    to_level: str = Field(..., description="事件后的等级（配置注入的等级词表之一）")


class GraphService:
    """图服务：构建一次、缓存 payload；``refresh`` 丢弃缓存。"""

    def __init__(self, repo_root: Path, module: Any) -> None:
        self.repo_root = repo_root
        self.module = module
        self._cache_key: Optional[tuple] = None
        self._snapshot = None
        self._cfg = None
        self._payload: Optional[Dict[str, Any]] = None
        self._alerts: List[Dict[str, Any]] = []
        self._build_ms: Optional[float] = None
        self._hits = 0
        self._misses = 0

    # ── 构建/缓存 ─────────────────────────────────────────────
    def _fingerprint(self, cfg) -> tuple:
        parts = []
        for key in ("semantics_config", "ledger", "baseline_edges", "manual_chains"):
            path = cfg.sources.get(key)
            parts.append((key, str(path), path.stat().st_mtime_ns if path and path.exists() else 0))
        cfg_path = self.repo_root / CONFIG_REL
        parts.append(("config", str(cfg_path), cfg_path.stat().st_mtime_ns))
        return tuple(parts)

    def refresh(self) -> None:
        t0 = time.perf_counter()
        cfg = self.module.GraphConfig.from_file(self.repo_root / CONFIG_REL, self.repo_root)
        snapshot = self.module.build_snapshot(cfg)
        self._cfg = cfg
        self._snapshot = snapshot
        self._cache_key = self._fingerprint(cfg)
        self._build_ms = (time.perf_counter() - t0) * 1000.0
        self._payload = None

    def ensure(self) -> None:
        if self._cfg is None or self._snapshot is None:
            self.refresh()
            self._misses += 1
            return
        if (self._cfg.raw.get("performance") or {}).get("cache_enabled", True):
            if self._fingerprint(self._cfg) == self._cache_key:
                return
        self.refresh()

    @property
    def cfg(self):
        self.ensure()
        return self._cfg

    @property
    def snapshot(self):
        self.ensure()
        return self._snapshot

    # ── payload ──────────────────────────────────────────────
    def snapshot_payload(self) -> Dict[str, Any]:
        self.ensure()
        if self._payload is None:
            cfg, snap = self._cfg, self._snapshot
            payload = snap.to_dict()
            payload.update(
                {
                    "levels": snap.levels(),
                    "confidences": {k: round(v, 6) for k, v in snap.confidences().items()},
                    "alerts": self._alerts[-10:],
                    "alert_summary": self._alert_summary(),
                    "performance": {
                        "build_ms": round(self._build_ms or 0.0, 3),
                        "api_budget_ms": float((cfg.performance or {}).get("api_budget_ms", 500.0)),
                        "cache_hits": self._hits,
                        "cache_misses": self._misses,
                    },
                    "config": {
                        "default_edge_weight": cfg.default_weight,
                        "inferred_edge_weight": cfg.inferred_weight,
                        "level_confidence": cfg.level_confidence,
                        "confidence_bands": cfg.bands,
                    },
                }
            )
            self._payload = payload
        self._hits += 1
        return self._payload

    def _alert_summary(self) -> Dict[str, Any]:
        return {
            "count": len(self._alerts),
            "max_latency_ms": max((a["latency_ms"] for a in self._alerts), default=0.0),
            "last": self._alerts[-1]["alert_id"] if self._alerts else None,
        }

    def simulate(self, capability_id: str, to_level: str) -> Dict[str, Any]:
        self.ensure()
        module = self.module
        t0 = time.perf_counter()
        try:
            event = module.CascadeEvent(capability_id=capability_id, to_level=to_level)
            result = module.simulate(self._snapshot, event, self._cfg)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 等级非法等
            raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}") from exc
        payload = result.to_dict()
        payload["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 4)
        self.record_alert(payload, elapsed_ms=payload["elapsed_ms"])
        return payload

    def record_alert(self, cascade_payload: Dict[str, Any], *, elapsed_ms: float) -> Dict[str, Any]:
        """把一次级联结果登记为预警（内存环形，保留最近 50 条）。"""
        cfg = self._cfg
        budget = float((cfg.performance or {}).get("alert_latency_budget_ms", 1000.0))
        alert = {
            "alert_id": f"alr-api-{len(self._alerts) + 1}",
            "capability_id": (cascade_payload.get("event") or {}).get("capability_id"),
            "from_level": cascade_payload.get("from_level"),
            "to_level": cascade_payload.get("to_level"),
            "severity": cascade_payload.get("severity"),
            "affected_count": cascade_payload.get("affected_count"),
            "affected": [
                {"id": a["id"], "level_before": a["level_before"], "level_after": a["level_after"]}
                for a in cascade_payload.get("affected") or []
            ],
            "latency_ms": round(elapsed_ms, 4),
            "budget_ms": budget,
            "within_budget": elapsed_ms <= budget,
        }
        self._alerts.append(alert)
        self._alerts = self._alerts[-50:]
        self._payload = None  # 让下一次读带上新预警
        return alert

    def alerts_payload(self) -> Dict[str, Any]:
        self.ensure()
        return {
            "count": len(self._alerts),
            "alert_latency_budget_ms": float((self._cfg.performance or {}).get(
                "alert_latency_budget_ms", 1000.0)),
            "alerts": list(reversed(self._alerts)),
        }

    def metrics_payload(self) -> Dict[str, Any]:
        self.ensure()
        snap = self._snapshot
        return {
            "generated_at": snap.meta.get("generated_at"),
            "sources": snap.meta.get("sources", {}),
            "graph": snap.summary(),
            "build_ms": round(self._build_ms or 0.0, 3),
            "api_budget_ms": float((self._cfg.performance or {}).get("api_budget_ms", 500.0)),
            "cache_key": [list(x) for x in (self._cache_key or ())],
            "cache_hits": self._hits,
            "cache_misses": self._misses,
        }


# ── 进程级单例（main.py 挂载时惰性构建）────────────────────
_SERVICE: Optional[GraphService] = None


def build_engine(repo_root: Optional[Path] = None, cfg: Any = None, snapshot: Any = None) -> GraphService:
    """构造服务实例（cfg/snapshot 可注入，便于离线判据脚本复用同一份图）。"""
    module = load_engine_module()
    service = GraphService(Path(repo_root or _API_ROOT), module)
    if cfg is not None and snapshot is not None:
        service._cfg = cfg  # noqa: SLF001 - 判据脚本注入同一份图，避免二次构建
        service._snapshot = snapshot  # noqa: SLF001
        service._build_ms = 0.0
        service._cache_key = service._fingerprint(cfg)  # noqa: SLF001
    return service


def get_service() -> GraphService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = build_engine()
        logger.info("capability-graph service initialised")
    return _SERVICE


# ── 端点 ────────────────────────────────────────────────────
@router.get("", summary="能力依赖图谱快照")
def get_graph(response: Response, _user: dict = Depends(require_auth)) -> Dict[str, Any]:
    service = get_service()
    payload = service.snapshot_payload()
    response.headers["X-Capability-Graph-Cache"] = "hit" if (service._build_ms or 0) >= 0 else "miss"  # noqa: SLF001
    response.headers["X-Capability-Graph-Build-Ms"] = f"{(service._build_ms or 0):.3f}"  # noqa: SLF001
    return payload


@router.get("/edges", summary="依赖边清单")
def get_edges(_user: dict = Depends(require_auth)) -> Dict[str, Any]:
    service = get_service()
    payload = service.snapshot_payload()
    return {"count": len(payload["edges"]), "edges": payload["edges"],
            "generated_at": payload.get("generated_at")}


@router.get("/alerts", summary="最近级联预警")
def get_alerts(_user: dict = Depends(require_auth)) -> Dict[str, Any]:
    return get_service().alerts_payload()


@router.get("/metrics", summary="图构建与缓存元数据")
def get_metrics(_user: dict = Depends(require_auth)) -> Dict[str, Any]:
    return get_service().metrics_payload()


@router.post("/simulate", summary="级联失效模拟（只读）")
def post_simulate(req: SimulateRequest, _user: dict = Depends(require_auth)) -> Dict[str, Any]:
    return get_service().simulate(req.capability_id, req.to_level)


@router.post("/refresh", summary="重建图缓存")
def post_refresh(_user: dict = Depends(require_auth)) -> Dict[str, Any]:
    service = get_service()
    service.refresh()
    payload = service.snapshot_payload()
    return {"refreshed": True, "build_ms": (service._build_ms or 0.0),
            "node_count": payload["summary"]["node_count"],
            "edge_count": payload["summary"]["edge_count"]}