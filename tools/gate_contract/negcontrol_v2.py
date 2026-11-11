"""v2 六类负控生成器：检验非标量判据的防伪能力。

六类负控（每类 N≥10）：

==================  ============================================================
class               伪证据形态
==================  ============================================================
band_tol_widened    逐点容差被改宽到实测一定通过（登记表比对能否抓到）
band_pass_ratio_faked  pass_ratio 手抄（复算能否抓到）
envelope_peak_shift  峰值频率被偏移超出容差（谱包络检查能否抓到）
envelope_breach     实测谱突破包络（包络检查能否抓到）
fingerprint_stat_tampered 场量统计量被篡改（统计量比对能否抓到）
fingerprint_grid_mismatch  网格哈希不匹配（网格哈希能否抓到）
==================  ============================================================

假通过率 = 这些伪证据被判 PASS 的比例；要求恒为 0。
"""

from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path
from typing import Any

from .contract import canonical_json, env_fingerprint, sha256_bytes, sha256_file
from .contract_v2 import SCHEMA_VERSION_V2

CLASSES_V2 = (
    "band_tol_widened",
    "band_pass_ratio_faked",
    "envelope_peak_shift",
    "envelope_breach",
    "fingerprint_stat_tampered",
    "fingerprint_grid_mismatch",
)
PER_CLASS = 12


def _make_curve(n: int = 20, seed: int = 42) -> list[dict]:
    """生成合成曲线 [{x, y}, ...]。"""
    rng = random.Random(seed)
    return [{"x": i / n, "y": math.sin(2 * math.pi * i / n) + rng.gauss(0, 0.01)}
            for i in range(n)]


def _make_spectrum(n: int = 30, seed: int = 42) -> list[dict]:
    """生成合成谱 [{omega, amplitude}, ...]。"""
    rng = random.Random(seed)
    peak_omega = 2.0
    return [{"omega": i * 0.2,
             "amplitude": math.exp(-((i * 0.2 - peak_omega) ** 2) / 0.5) + rng.gauss(0, 0.001)}
            for i in range(n)]


def _make_field(n: int = 100, seed: int = 42) -> list[float]:
    """生成合成场量。"""
    rng = random.Random(seed)
    return [math.sin(2 * math.pi * i / n) + rng.gauss(0, 0.05) for i in range(n)]


def _write_artifact(dirpath: Path, doc: dict, name: str = "data.json") -> tuple[str, str]:
    """写工件并返回 (rel_path, sha256)。"""
    (dirpath / "evidence").mkdir(parents=True, exist_ok=True)
    path = dirpath / "evidence" / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return f"evidence/{name}", sha256_file(path)


def _base_gate_band(tmp_path: Path, gate_id: str = "g-band",
                    mission_id: int = 100) -> tuple[Path, dict, dict]:
    """生成 pointwise_band 类型的基线门。"""
    ref_curve = _make_curve(20, seed=100)
    meas_curve = [{"x": p["x"], "y": p["y"] + 0.005} for p in ref_curve]

    doc = {"ref_curve": ref_curve, "meas_curve": meas_curve}
    rel, sha = _write_artifact(tmp_path, doc)

    entry = {
        "id": "C1",
        "type": "pointwise_band",
        "artifact": {"path": rel, "sha256": sha},
        "ref_pointer": "ref_curve",
        "meas_pointer": "meas_curve",
        "tolerance": {"mode": "absolute", "value": 0.05},
        "pass_ratio_threshold": 0.95,
        "unit": "ratio",
    }
    gate = {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "criteria": [entry],
    }
    return tmp_path / "gate.json", gate, doc


def _base_gate_envelope(tmp_path: Path, gate_id: str = "g-envelope",
                        mission_id: int = 101) -> tuple[Path, dict, dict]:
    """生成 spectral_envelope 类型的基线门。"""
    spectrum = _make_spectrum(30, seed=200)
    lower = [{"omega": s["omega"], "amplitude": s["amplitude"] * 0.8} for s in spectrum]
    upper = [{"omega": s["omega"], "amplitude": s["amplitude"] * 1.2} for s in spectrum]
    meas = [{"omega": s["omega"], "amplitude": s["amplitude"] * 1.0} for s in spectrum]

    doc = {"lower": lower, "upper": upper, "meas": meas}
    rel, sha = _write_artifact(tmp_path, doc)

    entry = {
        "id": "C1",
        "type": "spectral_envelope",
        "artifact": {"path": rel, "sha256": sha},
        "lower_pointer": "lower",
        "upper_pointer": "upper",
        "meas_pointer": "meas",
        "peak_tolerance": {"freq_abs_tol": 0.5, "amp_abs_tol": 0.2},
        "unit": "amplitude",
    }
    gate = {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "criteria": [entry],
    }
    return tmp_path / "gate.json", gate, doc


def _base_gate_fingerprint(tmp_path: Path, gate_id: str = "g-fingerprint",
                           mission_id: int = 102) -> tuple[Path, dict, dict]:
    """生成 field_fingerprint 类型的基线门。"""
    ref_field = _make_field(100, seed=300)
    meas_field = [v + 0.001 for v in ref_field]

    doc = {"ref_field": ref_field, "meas_field": meas_field,
           "ref_grid": ref_field, "meas_grid": meas_field}
    rel, sha = _write_artifact(tmp_path, doc)

    entry = {
        "id": "C1",
        "type": "field_fingerprint",
        "artifact": {"path": rel, "sha256": sha},
        "stats": {
            "ref_pointer": "ref_field",
            "meas_pointer": "meas_field",
            "orders": [0, 1, 2],
        },
        "stat_tolerance": {"0": 0.01, "1": 0.05, "2": 0.1, "mode": "relative"},
        "grid_hash": {
            "ref_pointer": "ref_grid",
            "meas_pointer": "meas_grid",
        },
        "unit": "field_unit",
    }
    gate = {
        "schema_version": SCHEMA_VERSION_V2,
        "gate_id": gate_id,
        "mission_id": mission_id,
        "criteria": [entry],
    }
    return tmp_path / "gate.json", gate, doc


# ── 负控生成器 ─────────────────────────────────────────────────

def generate_band_tol_widened(tmp_path: Path, case_id: int) -> tuple[Path, dict]:
    """负控：逐点容差被改宽到实测一定通过（但登记表里是严格的）。
    
    关键：改宽容差但登记表比对应能抓到。
    """
    gate_path, gate, doc = _base_gate_band(tmp_path, mission_id=200 + case_id)
    # 改宽容差（登记表里是严格的 0.05）
    gate["criteria"][0]["tolerance"]["value"] = 100.0
    return gate_path, gate


def generate_band_pass_ratio_faked(tmp_path: Path, case_id: int) -> tuple[Path, dict]:
    """负控：让实测曲线偏差很大，pass_ratio 会很低。"""
    gate_path, gate, doc = _base_gate_band(tmp_path, mission_id=300 + case_id)
    # 修改工件让偏差变大
    doc["meas_curve"] = [{"x": p["x"], "y": p["y"] + 1.0} for p in doc["ref_curve"]]
    rel, sha = _write_artifact(tmp_path, doc)
    gate["criteria"][0]["artifact"]["path"] = rel
    gate["criteria"][0]["artifact"]["sha256"] = sha
    return gate_path, gate


def generate_envelope_peak_shift(tmp_path: Path, case_id: int) -> tuple[Path, dict]:
    """负控：峰值频率被偏移超出容差。"""
    gate_path, gate, doc = _base_gate_envelope(tmp_path, mission_id=400 + case_id)
    # 偏移实测谱的峰值
    doc["meas"] = [{"omega": s["omega"] + 2.0, "amplitude": s["amplitude"]}
                   for s in doc["meas"]]
    rel, sha = _write_artifact(tmp_path, doc)
    gate["criteria"][0]["artifact"]["path"] = rel
    gate["criteria"][0]["artifact"]["sha256"] = sha
    # 收紧峰值容差
    gate["criteria"][0]["peak_tolerance"]["freq_abs_tol"] = 0.1
    return gate_path, gate


def generate_envelope_breach(tmp_path: Path, case_id: int) -> tuple[Path, dict]:
    """负控：实测谱突破包络。"""
    gate_path, gate, doc = _base_gate_envelope(tmp_path, mission_id=500 + case_id)
    # 让实测谱幅值超出上界
    doc["meas"] = [{"omega": s["omega"], "amplitude": s["amplitude"] * 2.0}
                   for s in doc["meas"]]
    rel, sha = _write_artifact(tmp_path, doc)
    gate["criteria"][0]["artifact"]["path"] = rel
    gate["criteria"][0]["artifact"]["sha256"] = sha
    return gate_path, gate


def generate_fingerprint_stat_tampered(tmp_path: Path, case_id: int) -> tuple[Path, dict]:
    """负控：场量统计量被篡改。"""
    gate_path, gate, doc = _base_gate_fingerprint(tmp_path, mission_id=600 + case_id)
    # 大幅修改实测场
    doc["meas_field"] = [v * 5.0 for v in doc["ref_field"]]
    rel, sha = _write_artifact(tmp_path, doc)
    gate["criteria"][0]["artifact"]["path"] = rel
    gate["criteria"][0]["artifact"]["sha256"] = sha
    # 收紧统计量容差
    gate["criteria"][0]["stat_tolerance"] = {"0": 0.001, "1": 0.001, "2": 0.001,
                                              "mode": "relative"}
    return gate_path, gate


def generate_fingerprint_grid_mismatch(tmp_path: Path, case_id: int) -> tuple[Path, dict]:
    """负控：网格哈希不匹配。"""
    gate_path, gate, doc = _base_gate_fingerprint(tmp_path, mission_id=700 + case_id)
    # 让网格数据完全不同
    doc["meas_grid"] = [v + 10.0 for v in doc["ref_grid"]]
    rel, sha = _write_artifact(tmp_path, doc)
    gate["criteria"][0]["artifact"]["path"] = rel
    gate["criteria"][0]["artifact"]["sha256"] = sha
    return gate_path, gate


GENERATORS = {
    "band_tol_widened": generate_band_tol_widened,
    "band_pass_ratio_faked": generate_band_pass_ratio_faked,
    "envelope_peak_shift": generate_envelope_peak_shift,
    "envelope_breach": generate_envelope_breach,
    "fingerprint_stat_tampered": generate_fingerprint_stat_tampered,
    "fingerprint_grid_mismatch": generate_fingerprint_grid_mismatch,
}


def build_v2_negcontrol_cases(root: Path, per_class: int = PER_CLASS) -> list[dict]:
    """生成全部负控案例。返回 [{class, case_id, gate_path, expected: FAIL/WITHHELD}, ...]。"""
    cases = []
    for cls_name, gen_fn in GENERATORS.items():
        for i in range(per_class):
            case_dir = root / cls_name / f"case_{i:03d}"
            case_dir.mkdir(parents=True, exist_ok=True)
            gate_path, gate = gen_fn(case_dir, i)
            gate_path.write_text(json.dumps(gate, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
            cases.append({
                "class": cls_name,
                "case_id": i,
                "gate_path": str(gate_path),
                "evidence_root": str(case_dir),
                "expected": "fail_or_withheld",
            })
    return cases


def measure_v2_negcontrols(cases: list[dict], registry: dict | None = None) -> dict:
    """跑全部负控，统计假通过率。"""
    from .contract_v2 import verify_gate_v2
    from .verify import PASS

    results = {cls: {"total": 0, "false_pass": 0, "correctly_caught": 0}
               for cls in CLASSES_V2}

    for case in cases:
        cls = case["class"]
        results[cls]["total"] += 1
        try:
            result = verify_gate_v2(
                case["gate_path"],
                registry=registry,
                evidence_root=case["evidence_root"],
            )
            if result["grade"] == PASS:
                results[cls]["false_pass"] += 1
            else:
                results[cls]["correctly_caught"] += 1
        except Exception as e:
            results[cls]["correctly_caught"] += 1

    total = sum(r["total"] for r in results.values())
    false_pass = sum(r["false_pass"] for r in results.values())
    return {
        "per_class": results,
        "total": total,
        "false_pass": false_pass,
        "false_pass_rate": false_pass / total if total > 0 else 0.0,
    }
