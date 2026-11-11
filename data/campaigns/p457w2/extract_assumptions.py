#!/usr/bin/env python3
"""从兄弟 worktree 的实现里**机器抽取**跨节点共享假设 → ``assumptions.json``。

纪律：闸要核的每个数字都从被测源码里读出来（不手抄），并记录源文件 sha256；
抽取失败（文件缺失/字段改名）一律 **fail-closed**：写空的 declarations 并让
闸报 UNDETERMINED，而不是悄悄用默认值兜底。

兄弟 worktree 根目录可用 ``TP_SIBLING_ROOT`` 覆盖（默认 /root/tp-agent/work）。

用法：``python3 data/campaigns/p457w2/extract_assumptions.py [--out assumptions.json]``
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIBLING_ROOT = Path(os.environ.get("TP_SIBLING_ROOT", "/root/tp-agent/work"))
N01 = SIBLING_ROOT / "m13988" / "backend" / "physics" / "spectral_fatigue.py"
N02 = SIBLING_ROOT / "m13986" / "backend" / "modules" / "damage_stability_flooding"
N05 = SIBLING_ROOT / "m13989" / "backend" / "physics" / "pipe_laying" / "fatigue.py"

# 双段幂律 S-N 的连续性条件（几何事实，不是标准数值）：
# log N = log a - m log(ds) 两段必须交于拐点 ds_knee ⇒
#   log_a2 = log_a1 + (m2 - m1) * log10(ds_knee)
CONTINUITY_EXPR = "log_a1 + (m2 - m1) * log10(knee)"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path, name: str):
    if not path.is_file():
        return None
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return mod


def decl(node: str, path: Path, params: dict[str, float], condition: str) -> dict:
    return {"node": node,
            "condition": condition,
            "params": {k: float(v) for k, v in params.items()},
            "source": {"ref": str(path), "sha256": sha256(path)}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "assumptions.json"))
    args = ap.parse_args(argv)

    notes: list[str] = []
    law_decls: list[dict] = []
    cond_decls: list[dict] = []
    gravity_decls: list[dict] = []
    solas_decls: list[dict] = []
    implied: list[dict] = []

    m01 = load(N01, "n01_spectral_fatigue")
    if m01 is not None:
        d = (getattr(m01, "DNV_SN_CURVES", {}) or {}).get("D")
        if isinstance(d, dict):
            params = {"m1": d["m1"], "m2": d["m2"], "log_a1": d["log_a1"],
                      "log_a2": d["log_a2"], "knee": d["delta_sigma_c"]}
            law_decls.append(decl("p457w2-n01-structural-fatigue", N01, params,
                                  "D curve, transverse stiffener detail"))
            cond_decls.append({"node": "p457w2-n01-structural-fatigue",
                               "value": "in air",
                               "source": {"ref": str(N01), "sha256": sha256(N01)}})
            implied.append({"node": "p457w2-n01-structural-fatigue",
                            "expr": CONTINUITY_EXPR, "target": "log_a2", "tol": 0.01})
            if hasattr(m01, "G"):
                gravity_decls.append({"node": "p457w2-n01-structural-fatigue",
                                      "value": float(m01.G),
                                      "source": {"ref": str(N01), "sha256": sha256(N01)}})
        else:
            notes.append(f"n01: DNV_SN_CURVES['D'] absent in {N01}")
    else:
        notes.append(f"n01: module not loadable at {N01}")

    m05 = load(N05, "n05_pipe_laying_fatigue")
    if m05 is not None:
        d = getattr(m05, "DNV_D_CURVE")
        if isinstance(d, dict):
            params = {"m1": d["m1"], "m2": d["m2"], "log_a1": d["log_A1"],
                      "log_a2": d["log_A2"], "knee": d["Sc"]}
            law_decls.append(decl("p457w2-n05-pipe-seabed", N05, params,
                                  "D curve in air (mean stress corrected), Table 2-1"))
            cond_decls.append({"node": "p457w2-n05-pipe-seabed", "value": "in air",
                               "source": {"ref": str(N05), "sha256": sha256(N05)}})
            implied.append({"node": "p457w2-n05-pipe-seabed",
                            "expr": CONTINUITY_EXPR, "target": "log_a2", "tol": 0.01})
        else:
            notes.append(f"n05: DNV_D_CURVE absent in {N05}")
    else:
        notes.append(f"n05: module not loadable at {N05}")

    geom = N02 / "geometry.py"
    if geom.is_file():
        m02 = load(geom, "n02_geometry")
        if m02 is not None and hasattr(m02, "G"):
            gravity_decls.append({"node": "p457w2-n02-damage-stability",
                                  "value": float(m02.G),
                                  "source": {"ref": str(geom), "sha256": sha256(geom)}})
        solas = N02 / "solas.py"
        if solas.is_file():
            txt = solas.read_text(encoding="utf-8")
            for token in ("MSC.216(82)", "SOLAS 2009", "SOLAS2009"):
                if token in txt:
                    solas_decls.append({"node": "p457w2-n02-damage-stability",
                                        "value": "SOLAS 2009 (MSC.216(82))",
                                        "source": {"ref": str(solas),
                                                   "sha256": sha256(solas)}})
                    break
    else:
        notes.append(f"n02: geometry module absent at {geom}")

    for row in implied:
        node = row["node"]
        d = next(x for x in law_decls if x["node"] == node)
        env = dict(d["params"])
        expected = (env["log_a1"] + (env["m2"] - env["m1"]) * math.log10(env["knee"]))
        row["expected_log_a2"] = expected
        row["declared_log_a2"] = env["log_a2"]
        row["continuity_gap_decades"] = expected - env["log_a2"]

    doc = {
        "schema": "tp.integration.assumptions/v1",
        "campaign": "p457w2",
        "extracted_utc": None,
        "notes": notes,
        "keys": [
            {"key": "fatigue.sn_curve.D",
             "kind": "law",
             "standard": "DNV-RP-C203 Table 2-1 (D curve)",
             "description": "同一 D 曲线的双段幂律参数与拐点；连续性由拐点几何约束",
             "tol": 1e-6,
             "declarations": law_decls,
             "implied": implied,
             "mitigation": {"kind": "pair_downgrade",
                            "pair": "ADJ-2-sn-curve-n01-vs-n05",
                            "note": "两侧寿命律不一致 ⇒ 引用对已判 DISAGREE，疲劳寿命类能力按对降级"}},
            {"key": "fatigue.sn_curve.D.condition",
             "kind": "edition",
             "standard": "DNV-RP-C203 Table 2-1",
             "description": "同一曲线类别/腐蚀条件的同名前提（不同条件之间不可比）",
             "declarations": cond_decls},
            {"key": "constant.gravity",
             "kind": "value",
             "standard": "SI convention",
             "description": "重力加速度常数：细则差异 ≤0.1% 视为同一约定",
             "unit": "m/s^2",
             "rel_tol": 1e-3,
             "declarations": gravity_decls},
            {"key": "solas.edition",
             "kind": "edition",
             "standard": "SOLAS Ch. II-1 Part B-1",
             "description": "破舱稳性分舱指数所用 SOLAS 版本（单节点声明 ⇒ 无法跨节点互证）",
             "declarations": solas_decls},
        ],
    }
    out = Path(args.out)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"written": str(out), "keys": len(doc["keys"]),
                      "law_declarations": len(law_decls), "notes": notes},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())