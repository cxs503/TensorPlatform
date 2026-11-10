"""readiness —— 派单前就绪检查器（fail-closed，缺契约降级恒等放行）。

输入（一条派单声明，全部为平台侧机器数据，无领域语义）::

    {
      "node_id": "N1",
      "contract": { ... } | None,          # 节点契约；None = 未声明
      "upstream": {                        # 派单时刻的上游注册表快照
         "<mission_id or artifact path>": {
             "status": "succeeded",
             "finished_at": "2026-11-10T00:00:00Z",
             "delivered_before_dispatch": true,
             "payload": { ... } | None     # 可选：工件载荷（字段/单位/版本）
         }
      },
      "dispatched_at": "2026-11-10T01:00:00Z"
    }

输出::

    {
      "schema": "dispatch-contract/readiness/v1",
      "node_id": ...,
      "verdict": "READY" | "BLOCK",
      "degraded": bool,          # True = 因缺契约/畸形输入而恒等放行
      "score": float,            # 未满足需求权重 / 需求总权重 ∈ [0,1]
      "required_weight": float,
      "unmet_weight": float,
      "unmet": [{"code":..., "kind":..., "ref":..., "detail":...}, ...],
      "degrade_reason": str|None
    }

语义
----
* 任一 ``required`` 需求未满足 → ``BLOCK``（fail-closed）。
* 未声明契约（``contract is None``）→ ``READY`` + ``degraded=True`` +
  ``degrade_reason=CONTRACT_MISSING``（恒等放行，不崩、不拦）。
* 契约自身不合法（schema 错误）→ ``BLOCK`` + 未满足项 ``CONTRACT_INVALID``：
  这是**可以修复的配置错误**，宁可派单前拦下。
* 任何检查内异常 → 该项记 ``CHECK_ERROR``，其余照常；整体永不抛异常。
"""

from __future__ import annotations

from typing import Any, Mapping

from .contract_schema import (
    check_payload,
    iter_requirements,
    load_config,
    validate_contract,
)

SCHEMA = "dispatch-contract/readiness/v1"
VERDICT_READY = "READY"
VERDICT_BLOCK = "BLOCK"


def _check_requirement(req: Mapping[str, Any], upstream: Mapping[str, Any],
                       cfg: Mapping[str, Any]) -> list[dict]:
    """检查单条 input 需求；返回未满足项（空=满足）。"""
    kind = req.get("kind")
    ref = req.get("ref")
    entry = upstream.get(str(ref)) if isinstance(upstream, Mapping) else None
    unmet: list[dict] = []

    if kind == "upstream_delivery":
        if not isinstance(entry, Mapping) or entry.get("found") is False:
            return [{"code": "UPSTREAM_NOT_FOUND", "kind": kind, "ref": ref,
                     "detail": "派单时刻上游注册表中不存在该交付"}]
        status = entry.get("status")
        if status not in cfg.get("satisfied_upstream_status", ["succeeded"]):
            unmet.append({"code": "UPSTREAM_STATUS_NOT_CONFORMANT", "kind": kind, "ref": ref,
                          "detail": f"上游终态={status}"})
        if entry.get("delivered_before_dispatch") is False:
            unmet.append({"code": "UPSTREAM_AFTER_DISPATCH", "kind": kind, "ref": ref,
                          "detail": "上游交付时间晚于本次派单时刻"})
        return unmet

    if kind == "artifact":
        if not isinstance(entry, Mapping) or entry.get("found") is False:
            return [{"code": "ARTIFACT_NOT_FOUND", "kind": kind, "ref": ref,
                     "detail": "派单时刻无任何上游产出该工件路径"}]
        if entry.get("delivered_before_dispatch") is False:
            unmet.append({"code": "ARTIFACT_AFTER_DISPATCH", "kind": kind, "ref": ref,
                          "detail": "工件产出时间晚于本次派单时刻"})
        if entry.get("status") not in cfg.get("satisfied_upstream_status", ["succeeded"]) \
                and entry.get("status") is not None:
            unmet.append({"code": "ARTIFACT_PRODUCER_NOT_CONFORMANT", "kind": kind, "ref": ref,
                          "detail": f"产出方终态={entry.get('status')}"})
        unmet.extend(check_payload(entry.get("payload"), req, cfg))
        return unmet

    if kind == "schema":
        if not isinstance(entry, Mapping) or entry.get("found") is False:
            return [{"code": "SCHEMA_TARGET_NOT_FOUND", "kind": kind, "ref": ref,
                     "detail": "派单时刻无该类别工件"}]
        unmet.extend(check_payload(entry.get("payload"), req, cfg))
        return unmet

    return [{"code": f"UNKNOWN_KIND:{kind}", "kind": kind, "ref": ref, "detail": "配置外的需求类别"}]


def assess(dispatch: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict:
    """就绪判定主入口。永不抛异常。"""
    cfg = config or load_config()
    gate_cfg = cfg.get("gate") or {}
    weights = gate_cfg.get("weights") or {}
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "node_id": None,
        "verdict": VERDICT_READY,
        "degraded": False,
        "degrade_reason": None,
        "score": 0.0,
        "required_weight": 0.0,
        "unmet_weight": 0.0,
        "n_required": 0,
        "unmet": [],
    }
    try:
        if not isinstance(dispatch, Mapping):
            out.update(degraded=True, degrade_reason="DISPATCH_NOT_OBJECT")
            return out
        out["node_id"] = dispatch.get("node_id")
        contract = dispatch.get("contract")
        if contract is None:
            out.update(degraded=True, degrade_reason="CONTRACT_MISSING")
            return out

        ok, errs = validate_contract(contract, cfg)
        if not ok:
            out["verdict"] = VERDICT_BLOCK
            out["unmet"] = [{"code": e, "kind": "contract", "ref": out["node_id"],
                             "detail": "契约自身不合法（可修复的配置错误）"} for e in errs]
            out["score"] = 1.0
            out["unmet_weight"] = out["required_weight"] = float(len(errs))
            return out

        upstream = dispatch.get("upstream") or {}
        total_w = 0.0
        unmet_w = 0.0
        n_req = 0
        for req in iter_requirements(contract, "input_contract"):
            w = float(weights.get(str(req.get("kind")), 1.0))
            required = req.get("required", True)
            if required:
                total_w += w
                n_req += 1
            try:
                unmet = _check_requirement(req, upstream, cfg)
            except Exception as exc:  # noqa: BLE001 —— 单项异常不拖死判定
                unmet = [{"code": "CHECK_ERROR", "kind": str(req.get("kind")),
                          "ref": req.get("ref"), "detail": f"{type(exc).__name__}: {exc}"[:200]}]
            if unmet and required:
                unmet_w += w
            out["unmet"].extend(unmet)

        out["required_weight"] = round(total_w, 6)
        out["n_required"] = n_req
        out["unmet_weight"] = round(unmet_w, 6)
        out["score"] = round(unmet_w / total_w, 6) if total_w > 0 else 0.0
        if total_w == 0:
            # 声明了契约但没有任何 required 需求：无判定力，按降级放行（不冒充证据）
            out.update(degraded=True, degrade_reason="NO_REQUIRED_INPUT")
            return out
        thr = float(gate_cfg.get("score_threshold", 0.0))
        must_block = bool(gate_cfg.get("block_when_any_required_unmet", True))
        if (must_block and unmet_w > 0) or out["score"] > thr:
            out["verdict"] = VERDICT_BLOCK
        return out
    except Exception as exc:  # noqa: BLE001 —— 兜底：恒等放行，绝不抛
        out.update(verdict=VERDICT_READY, degraded=True,
                   degrade_reason=f"ASSESS_ERROR:{type(exc).__name__}")
        return out