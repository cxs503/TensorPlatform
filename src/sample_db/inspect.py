"""run 产物三门体检（正例检查 + 上游 gap 定位）。

对给定 run 目录/产物跑**同一套**三门判定，但输出面向人读的 gap 清单：
哪一门过、哪一门缺、缺的具体是什么（manifest? checksum? mission 终态?）。

与 `admission.admit_sample` 共用判定逻辑（单一事实源），差别只在：
  * 体检模式默认 `audit_log=None`（只读体检，不写准入流水；生产入库必写）
  * 额外给出 `missing`（缺什么）与 `remediations`（怎么补齐）

CLI：
    python3 -m src.sample_db.inspect <artifact.json> --run-dir <dir> \
        --mission-id 811 --api --json-out outputs/e2e1_b1/m811_positive_check.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # 允许直接执行
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.sample_db.admission import (  # noqa: E402
    MANIFEST_EMBEDDED_KEYS,
    MANIFEST_FILENAMES,
    AdmissionDecision,
    ApiMissionResolver,
    MissionResolver,
    NullMissionResolver,
    StaticMissionResolver,
    _gate_checksums,
    _gate_mission,
    _locate_manifest,
    _parse_mission_status_specs,
    admit_sample,
    sha256_file,
)


def _missing_items(decision: AdmissionDecision, probe: dict[str, Any] | None = None) -> list[str]:
    """缺什么列什么（体检模式用 probe 的非短路结论，避免把 skipped 误读成缺失）。"""
    probe = probe or {}
    missing: list[str] = []
    manifest_gate = next((g for g in decision.gates if g.gate == "manifest"), None)
    if manifest_gate is not None and manifest_gate.status != "pass":
        if manifest_gate.code == "E_MANIFEST_MISSING":
            missing.append(
                "门1 RunManifest 缺失：run 目录下无 "
                + " / ".join(MANIFEST_FILENAMES)
                + "，产物内也无 "
                + " / ".join(MANIFEST_EMBEDDED_KEYS)
                + " 键"
            )
        else:
            missing.append(f"门1 RunManifest 不合法（{manifest_gate.code}）：{manifest_gate.message}")

    mission_gate = probe.get("mission")
    if mission_gate is not None and mission_gate.get("status") != "pass":
        missing.append(f"门2 来源 mission 终态未确认（{mission_gate.get('code')}）：{mission_gate.get('message')}")

    checksum_gate = probe.get("checksum")
    if checksum_gate is not None:
        if checksum_gate.get("status") == "not_evaluable":
            missing.append("门3 关键文件哈希无法复算：manifest 缺失 → 没有声明哈希可作复算基准")
        elif checksum_gate.get("status") != "pass":
            missing.append(f"门3 关键文件哈希不可复算（{checksum_gate.get('code')}）：{checksum_gate.get('message')}")
    return missing


def _remediations(decision: AdmissionDecision) -> list[str]:
    codes = {g.code for g in decision.gates if g.status == "fail"}
    out: list[str] = []
    if "E_MANIFEST_MISSING" in codes or "E_MANIFEST_NOT_OBJECT" in codes or "E_MANIFEST_UNREADABLE" in codes:
        out.append(
            "补写 RunManifest（contracts/tensorphysics-run-manifest-v1.schema.json）："
            "run 目录下 run_manifest.json 或产物内嵌 manifest 键，至少含 "
            "schema_version/run_id/status/validation_status/outputs[]"
            "（每个 output 需 uri + media_type + checksum_sha256(64hex) + size_bytes）"
        )
    if "E_CONTRACT" in codes:
        out.append("按上述契约补齐/修正字段（sha256 为 64 位小写十六进制，size_bytes 为整数）")
    if "E_UNVERIFIED" in codes:
        out.append("先跑验证器并把 validation_status 置为 'passed'（记录 validator 与 checks）")
    if "E_NO_OUTPUTS" in codes:
        out.append("在 manifest.outputs 中登记至少一个可复算的关键产物（如 summary.json / 场量文件）")
    if codes & {"E_MISSION_STATE", "E_MISSION_UNRESOLVED", "E_MISSION_ID_MISSING"}:
        out.append("确认来源 mission 在平台上终态 succeeded（manifest.mission_id 填对）")
    if codes & {"E_CHECKSUM_MISMATCH", "E_SIZE_MISMATCH", "E_CHECKSUM_FORMAT"}:
        out.append("产物落盘后重算 sha256/size 写回 manifest（落盘前登记会导致 stale 哈希）")
    if "E_ARTIFACT_MISSING" in codes:
        out.append("把 manifest 声明的产物真正落盘到 run 目录，或从 manifest.outputs 中删去")
    if "E_ARTIFACT_ESCAPE" in codes:
        out.append("manifest.outputs[].uri 必须是 run 目录内的相对路径")
    return out


def inspect_run_products(
    artifact_path: str | Path,
    *,
    run_dir: str | Path | None = None,
    mission_id: int | None = None,
    mission_resolver: MissionResolver | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    decision = admit_sample(
        artifact_path,
        mission_resolver=mission_resolver,
        audit_log=None,
        manifest_path=manifest_path,
        run_dir=run_dir,
        mission_id=mission_id,
        actor="inspect_run_products",
    )
    payload = decision.to_dict()

    # 体检模式不做短路：三门各自独立评估一遍，才能回答「缺什么」而不是「先卡在哪」。
    probe: dict[str, Any] = {}
    work_dir = Path(run_dir).resolve() if run_dir else Path(artifact_path).resolve().parent
    manifest, source = _locate_manifest(Path(artifact_path).resolve(), work_dir, Path(manifest_path) if manifest_path else None)
    if decision.mission_id is not None:
        gate2_probe, _state = _gate_mission(decision.mission_id, mission_resolver)
        probe["mission"] = gate2_probe.to_dict()
    if isinstance(manifest, dict):
        gate3_probe, verified = _gate_checksums(manifest, work_dir)
        probe["checksum"] = gate3_probe.to_dict()
        probe["checksum"]["verified_outputs"] = verified
    else:
        probe["checksum"] = {
            "gate": "checksum",
            "status": "not_evaluable",
            "code": None,
            "message": "no manifest ⇒ 没有任何声明哈希可用作复算基准（不是「哈希对得上」）",
            "details": {},
        }
    payload["gate_probe"] = probe
    payload["run_dir"] = str(work_dir)
    payload["run_dir_inventory"] = [
        {
            "name": item.name,
            "size_bytes": item.stat().st_size,
            "sha256": sha256_file(item),
        }
        for item in sorted(work_dir.iterdir())[:12]
        if item.is_file()
    ]
    payload["missing"] = _missing_items(decision, probe)
    payload["remediations"] = _remediations(decision)
    payload["verdict"] = "ADMISSIBLE" if decision.admitted else "NOT_ADMISSIBLE"
    gate_view = {
        "manifest": next((g["status"] for g in payload["gates"] if g["gate"] == "manifest"), None),
        "mission": (probe.get("mission") or {}).get("status"),
        "checksum": (probe.get("checksum") or {}).get("status"),
    }
    payload["gate_view"] = gate_view
    passed = [k for k, v in gate_view.items() if v == "pass"]
    payload["conclusion"] = (
        f"三门中 {len(passed)} 门通过（{', '.join(passed) if passed else '无'}）；"
        f"未通过/不可评估：{', '.join(k for k, v in gate_view.items() if v != 'pass') or '无'} → "
        + ("可准入" if decision.admitted else "不可准入")
    )
    payload["inspection_mode"] = "read_only_no_audit"
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m src.sample_db.inspect")
    parser.add_argument("artifact")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--mission-id", type=int, default=None)
    parser.add_argument("--mission-status", action="append", default=[], metavar="MID=STATUS[:FINISHED]")
    parser.add_argument("--api", action="store_true")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    if args.api:
        resolver: MissionResolver = ApiMissionResolver()
    elif args.mission_status:
        resolver = StaticMissionResolver(_parse_mission_status_specs(args.mission_status))
    else:
        resolver = NullMissionResolver()

    payload = inspect_run_products(
        args.artifact,
        run_dir=args.run_dir,
        mission_id=args.mission_id,
        mission_resolver=resolver,
        manifest_path=args.manifest,
    )
    payload["artifact_arg"] = args.artifact
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if payload["admitted"] else 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())