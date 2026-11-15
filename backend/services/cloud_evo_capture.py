"""云图演化动画·捕获守护钩子 (FEAT-SIM-DATAHOUSE)

新 sim-assets 事件入库即自动渲染成演化动画；渲染失败降级，不阻捕获。

设计：捕获守护（capture guard）在事件落盘后调用本模块的 `on_ingest`。
  - 成功：渲染 mp4 + 海报，登记到 gallery 索引。
  - 失败（坏数据/缺依赖）：记日志 + 写 degraded 标记，**不抛异常**，
    保证捕获链路不被渲染阻塞（fail-degrade 不阻捕获）。

用法（捕获守护侧）：
    from services.cloud_evo_capture import on_ingest
    on_ingest(event_dir, case=..., kind=...)   # 返回渲染结果 dict，永不抛
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("cloud_evo_capture")

# 渲染器（延迟导入，避免捕获守护强依赖）
try:
    from tools.cloud_evo import render_event
    _RENDERER = True
except Exception:  # pragma: no cover
    _RENDERER = False
    logger.warning("cloud_evo 渲染器不可导入——捕获守护降级为纯登记")

# gallery 索引（追加式，供 gallery 页读取）
GALLERY_INDEX = Path(os.environ.get(
    "CLOUD_EVO_GALLERY_INDEX", "artifacts/cloud_evo/gallery_index.json"))


def _register(case: str, result: Dict[str, Any]) -> None:
    """追加登记到 gallery 索引（幂等：同 case 覆盖）。"""
    try:
        GALLERY_INDEX.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        if GALLERY_INDEX.is_file():
            try:
                entries = json.loads(GALLERY_INDEX.read_text()).get("items", [])
            except Exception:
                entries = []
        entries = [e for e in entries if e.get("case") != case]
        entries.append({
            "case": case,
            "status": result.get("status", "ok"),
            "degraded": result.get("degraded", []),
            "kind": result.get("kind"),
            "n_frames": result.get("n_frames"),
            "outputs": result.get("outputs", {}),
            "registered_at": __import__("datetime").datetime.now().isoformat(),
        })
        GALLERY_INDEX.write_text(
            json.dumps({"items": entries, "total": len(entries)},
                       ensure_ascii=False, indent=2))
    except Exception as e:  # 登记失败不阻捕获
        logger.warning("gallery 登记失败（不阻捕获）: %s", e)


def on_ingest(event_dir: Path, case: str = "case",
              kind: Optional[str] = None, fps: int = 10) -> Dict[str, Any]:
    """捕获守护钩子：事件入库即自动渲染。永不抛异常（fail-degrade）。"""
    try:
        if not _RENDERER:
            result = {"case": case, "status": "degraded",
                      "degraded": ["renderer_unavailable"]}
            _register(case, result)
            return result
        result = render_event(Path(event_dir), Path(event_dir) / "_rendered",
                              case=case, kind=kind, fps=fps)
        _register(case, result)
        return result
    except Exception as e:  # 兜底：任何异常都不阻捕获
        logger.error("捕获守护渲染异常（降级，不阻捕获）: %s", e)
        result = {"case": case, "status": "degraded",
                  "degraded": ["capture_guard_error"], "error": str(e)}
        _register(case, result)
        return result
