"""gate.json 回填器：把注入式 claims 数据变成可复算的 gate.json + 权威登记表。

输入（数据，不是代码）：``config/gate_contract/*_claims.json``
  {schema_version, plan_id, work_root_env, nodes:[{gate_id, mission_id, node_id,
   worktree, repo, criteria:[{id, artifact(相对 worktree), pointer, select?, field?,
   scale?, op, threshold, unit, statement?}]}]}

行为：
1. 把每个判据引用的**证据工件复制**进门目录（``evidence/``），使 gate.json 自足：
   sha256 在任何机器上都能复算，不依赖原始 worktree 是否还在；
2. 用同一套指针解析复算 ``measured`` 并写进 gate.json（数字由程序注入，零手抄）；
   指针解析失败 → 不写 measured，只留 ``unresolved`` 说明（让 verify 判 WITHHELD，
   绝不补一个假数字）；
3. 输出权威判据登记表 ``criteria_registry.json``（算子/阈值/单位），供 verify 比对
   「阈值是否被改宽」。

零领域语义：本模块不认识任何域词，只搬运数据。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .contract import (CLAIMS_SCHEMA_VERSION, REGISTRY_SCHEMA_VERSION,
                       SCHEMA_VERSION, ContractError, canonical_json,
                       canonical_sha256, env_fingerprint, measure, sha256_file)

DEFAULT_RUN_CMD = ("python3 tools/gate_contract/cli.py verify "
                   "--gate {gate_rel} --registry {registry_rel} --json")


def _copy_artifact(src_root: Path, rel: str, dst_dir: Path) -> tuple[str, str]:
    src = src_root / rel
    if not src.is_file():
        raise ContractError("artifact_missing", str(src))
    digest = sha256_file(src)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{digest[:12]}_{src.name}"
    if not dst.is_file() or sha256_file(dst) != digest:
        shutil.copy2(src, dst)
    return f"evidence/{dst.name}", digest


def backfill(claims_path: str | Path, out_dir: str | Path, *,
             work_root: str | Path | None = None,
             run_cmd_tpl: str = DEFAULT_RUN_CMD,
             issued_utc: str | None = None) -> dict:
    claims = json.loads(Path(claims_path).read_text(encoding="utf-8"))
    if claims.get("schema_version") != CLAIMS_SCHEMA_VERSION:
        raise ContractError("schema_invalid", f"claims:{claims.get('schema_version')}")
    root = Path(work_root) if work_root is not None else Path(claims.get("work_root", "."))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    registry: dict[str, Any] = {"schema_version": REGISTRY_SCHEMA_VERSION,
                                "plan_id": claims.get("plan_id"), "gates": {}}
    written: list[dict] = []
    for node in claims.get("nodes", []):
        gate_id = node["gate_id"]
        gate_dir = out / gate_id
        gate_dir.mkdir(parents=True, exist_ok=True)
        src_root = root / node["worktree"]
        criteria_out: list[dict] = []
        reg_criteria: dict[str, Any] = {}
        unresolved: list[str] = []
        for crit in node.get("criteria", []):
            entry: dict[str, Any] = {
                "id": crit["id"], "op": crit["op"],
                "threshold": crit["threshold"], "unit": crit["unit"],
                "statement": crit.get("statement", ""),
                "pointer": crit["pointer"],
            }
            for key in ("select", "field", "scale"):
                if crit.get(key) is not None:
                    entry[key] = crit[key]
            try:
                rel, digest = _copy_artifact(src_root, crit["artifact"], gate_dir / "evidence")
                entry["artifact"] = {"path": rel, "sha256": digest}
                value, _ = measure({**entry, "artifact": {"path": rel, "sha256": digest}},
                                   gate_dir)
                entry["measured"] = value
            except ContractError as exc:
                entry["artifact"] = entry.get("artifact") or {
                    "path": crit["artifact"], "sha256": None}
                entry["unresolved"] = f"{exc.code}: {exc.detail}"
                unresolved.append(crit["id"])
            criteria_out.append(entry)
            reg_criteria[str(crit["id"])] = {"op": crit["op"],
                                             "threshold": crit["threshold"],
                                             "unit": crit["unit"]}
        gate = {
            "schema_version": SCHEMA_VERSION,
            "gate_id": gate_id,
            "mission_id": int(node["mission_id"]),
            "subject": {"node_id": node.get("node_id"), "repo": node.get("repo"),
                        "plan_id": claims.get("plan_id"), "worktree": node.get("worktree")},
            "issued_utc": issued_utc,
            "run_cmd": run_cmd_tpl.format(
                gate_rel=str((gate_dir / "gate.json")), registry_rel=str(out / "criteria_registry.json")),
            "env_fingerprint": env_fingerprint(),
            "criteria": criteria_out,
        }
        gate_path = gate_dir / "gate.json"
        gate_path.write_text(json.dumps(gate, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")
        registry["gates"][gate_id] = {
            "mission_id": int(node["mission_id"]),
            "node_id": node.get("node_id"),
            "criteria": reg_criteria,
        }
        written.append({"gate_id": gate_id, "mission_id": int(node["mission_id"]),
                        "gate_path": str(gate_path), "criteria": len(criteria_out),
                        "unresolved": unresolved,
                        "resolvable": len(criteria_out) - len(unresolved),
                        "gate_sha256": canonical_sha256(
                            {k: v for k, v in gate.items() if k != "env_fingerprint"})})
    (out / "criteria_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"claims": str(claims_path), "out_dir": str(out), "nodes": written,
            "registry": str(out / "criteria_registry.json"),
            "coverage": (len(written) / len(claims.get("nodes", []))) if claims.get("nodes") else 0.0}
