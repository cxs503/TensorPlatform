"""E2E-1 修复（门缺陷 B1）· 用例构造器。

B 类负测试构造法沿用 m810 `tests/e2e1b_negative/attack_cases.py` 的思路：
在临时目录里造「run 目录 + RunManifest + 产物文件」的真实结构，只坏一个
维度，其余保持合法，从而证明**是哪一门**拦住了它（而不是别的门顺带挡住）。

每个用例返回：
    {
      "name", "attack", "gate", "expect", "expect_code",
      "run_dir", "artifact", "manifest", "mission_status",
    }
期望值在构造时就写死（判据先于实现），实测由 runner/pytest 填。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# m810 实测攻击载荷（outputs/e2e1b_negative/artifacts/unverified_report.json 原文）：
# 伪造 verdict="PASS"、τ=0.4（ν<0 非物理）、无 RunManifest、无哈希。
M810_UNVERIFIED_REPORT = {
    "case": "suboff_re5e4_unverified",
    "kind": "resistance",
    "verdict": "PASS",
    "physics": {"Re": 50000.0, "tau": 0.4, "u_lb": 0.05, "nu_lb": -0.033},
    "configuration": {"n_steps": 20000},
    "domain": {"grid": [64, 32, 32]},
    "results": {"l2_rel_err": 0.001},
}

VALID_ARTIFACT = {
    "case": "cylinder_re1000_st_gate",
    "kind": "vortex_shedding",
    "verdict": "PASS",
    "physics": {"Re": 1000.0, "tau": 0.516, "u_lb": 0.05, "nu_lb": 0.005333333333333333},
    "configuration": {"n_steps": 20000},
    "results": {"st": 0.1642, "st_ref": 0.164, "err_pct": 0.12},
    "validation_status": "passed",
}

VALID_MISSION = 811


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _valid_manifest(run_dir: Path, artifact_name: str = "report.json", **overrides) -> dict:
    artifact = run_dir / artifact_name
    manifest = {
        "schema_version": 1,
        "run_id": f"run-{run_dir.name}",
        "mission_id": VALID_MISSION,
        "status": "succeeded",
        "validation_status": "passed",
        "producer": {"name": "tensorlbm", "version": "0.1.0", "revision": "58e48da9"},
        "metrics": {"st": 0.1642},
        "outputs": [
            {
                "uri": artifact_name,
                "media_type": "application/json",
                "checksum_sha256": sha256_file(artifact),
                "size_bytes": artifact.stat().st_size,
                "role": "primary",
            }
        ],
    }
    for key, value in overrides.items():
        if key == "outputs" and value is None:
            manifest.pop("outputs", None)
        else:
            manifest[key] = value
    return manifest


def _case(name, attack, gate, expect, expect_code, run_dir, artifact, manifest, mission_status):
    return {
        "name": name,
        "attack": attack,
        "gate": gate,
        "expect": expect,
        "expect_code": expect_code,
        "run_dir": run_dir,
        "artifact": artifact,
        "manifest": manifest,
        "mission_status": mission_status,
    }


def build_all(root: Path) -> list[dict]:
    """构造全部用例（含 1 个正对照）。root 下每例一个 run 目录。"""
    root = Path(root)
    cases: list[dict] = []

    # ---------- C0 正对照：三门全过 ----------
    d = root / "C0_control_valid"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    _write_json(d / "run_manifest.json", _valid_manifest(d))
    cases.append(
        _case(
            "C0_control_valid",
            "合法 run 产物（有效 manifest + mission 811 succeeded + sha256 命中）",
            "三门",
            "ADMITTED",
            None,
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B1 复现 m810 缺陷：裸 JSON 无 RunManifest ----------
    d = root / "B1_repro_m810_unverified_ingest"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "unverified_report.json", M810_UNVERIFIED_REPORT)
    cases.append(
        _case(
            "B1_repro_m810_unverified_ingest",
            "m810 实测载荷原文：伪造 verdict=PASS、τ=0.4(ν<0)、无 RunManifest、无哈希",
            "manifest",
            "REJECTED",
            "E_MANIFEST_MISSING",
            d,
            art,
            None,
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B2 manifest 非对象 ----------
    d = root / "B2_manifest_not_object"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    _write_json(d / "run_manifest.json", ["not", "an", "object"])
    cases.append(
        _case(
            "B2_manifest_not_object",
            "run_manifest.json 是数组而非对象",
            "manifest",
            "REJECTED",
            "E_MANIFEST_NOT_OBJECT",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B3 契约缺 checksum 字段 ----------
    d = root / "B3_contract_missing_checksum"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    bad = _valid_manifest(d)
    bad["outputs"][0].pop("checksum_sha256")
    _write_json(d / "run_manifest.json", bad)
    cases.append(
        _case(
            "B3_contract_missing_checksum",
            "manifest.outputs[0] 缺 checksum_sha256（契约硬约束）",
            "manifest",
            "REJECTED",
            "E_CONTRACT",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B4 哈希不符（产物被篡改） ----------
    d = root / "B4_checksum_mismatch"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    _write_json(d / "run_manifest.json", _valid_manifest(d))
    # 等长篡改：st 由 0.1642 改成 0.1900（字节数不变，size 门放行，只有哈希门能抓）
    text = (d / "report.json").read_text(encoding="utf-8")
    assert "0.1642" in text
    (d / "report.json").write_text(text.replace("0.1642", "0.1900"), encoding="utf-8")
    cases.append(
        _case(
            "B4_checksum_mismatch",
            "manifest 声明哈希后等长篡改产物（St 0.1642→0.1900，字节数不变）",
            "checksum",
            "REJECTED",
            "E_CHECKSUM_MISMATCH",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B5 validation_status != passed ----------
    d = root / "B5_unverified_manifest"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    _write_json(d / "run_manifest.json", _valid_manifest(d, validation_status="not_run"))
    cases.append(
        _case(
            "B5_unverified_manifest",
            "manifest 合法但 validation_status='not_run'（未验证 run）",
            "manifest",
            "REJECTED",
            "E_UNVERIFIED",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B6 来源 mission 终态 failed ----------
    d = root / "B6_mission_failed"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    _write_json(d / "run_manifest.json", _valid_manifest(d))
    cases.append(
        _case(
            "B6_mission_failed",
            "三门前两门合法，但来源 mission 811 终态 failed",
            "mission",
            "REJECTED",
            "E_MISSION_STATE",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: {"status": "failed", "finished_status": "failed"}},
        )
    )

    # ---------- B7 mission 状态不可解析 ----------
    d = root / "B7_mission_unknown"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    _write_json(d / "run_manifest.json", _valid_manifest(d))
    cases.append(
        _case(
            "B7_mission_unknown",
            "来源 mission 不在状态源中（无法确认 succeeded → fail-closed）",
            "mission",
            "REJECTED",
            "E_MISSION_UNRESOLVED",
            d,
            art,
            d / "run_manifest.json",
            {},
        )
    )

    # ---------- B8 路径穿越 ----------
    d = root / "B8_artifact_escape"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    outside = _write_json(root / "outside_secret.json", {"stolen": True})
    bad = _valid_manifest(d)
    bad["outputs"][0]["uri"] = "../outside_secret.json"
    bad["outputs"][0]["checksum_sha256"] = sha256_file(outside)
    bad["outputs"][0]["size_bytes"] = outside.stat().st_size
    _write_json(d / "run_manifest.json", bad)
    cases.append(
        _case(
            "B8_artifact_escape",
            "manifest uri 用 ../ 指向 run 目录外文件（哈希/尺寸都对得上）",
            "checksum",
            "REJECTED",
            "E_ARTIFACT_ESCAPE",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B9 size_bytes 不符 ----------
    d = root / "B9_size_mismatch"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    bad = _valid_manifest(d)
    bad["outputs"][0]["size_bytes"] = bad["outputs"][0]["size_bytes"] + 7
    _write_json(d / "run_manifest.json", bad)
    cases.append(
        _case(
            "B9_size_mismatch",
            "sha256 命中但 size_bytes 声明 +7（被截断/拼接过的产物）",
            "checksum",
            "REJECTED",
            "E_SIZE_MISMATCH",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B10 声明产物缺文件 ----------
    d = root / "B10_artifact_missing_file"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    bad = _valid_manifest(d)
    bad["outputs"].append(
        {
            "uri": "field_dump.npz",
            "media_type": "application/octet-stream",
            "checksum_sha256": "0" * 64,
            "size_bytes": 128,
            "role": "field",
        }
    )
    _write_json(d / "run_manifest.json", bad)
    cases.append(
        _case(
            "B10_artifact_missing_file",
            "manifest 声明了未落盘的场量文件 field_dump.npz",
            "checksum",
            "REJECTED",
            "E_ARTIFACT_MISSING",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    # ---------- B11 outputs 为空 ----------
    d = root / "B11_no_outputs"
    d.mkdir(parents=True, exist_ok=True)
    art = _write_json(d / "report.json", VALID_ARTIFACT)
    _write_json(d / "run_manifest.json", _valid_manifest(d, outputs=[]))
    cases.append(
        _case(
            "B11_no_outputs",
            "manifest.outputs=[]（无可复算的产物 → 哈希门形同虚设）",
            "manifest",
            "REJECTED",
            "E_NO_OUTPUTS",
            d,
            art,
            d / "run_manifest.json",
            {VALID_MISSION: "succeeded"},
        )
    )

    return cases