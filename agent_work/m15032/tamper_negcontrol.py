#!/usr/bin/env python3
"""m15032 独立负控：篡改 H6 门工件，逐项看 verify 是否报问题（不复用 m14961 测试）。"""
import json, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "runs" / "m15032_audit" / "tamper"
SRC = ROOT / "artifacts" / "maturity_gate_h6"

CASES = {
    "T1_threshold": lambda g: g["thresholds"].__setitem__("coverage_min", 0.5),
    "T2_axis": lambda g: g["axes"]["cross_source"].__setitem__("agreement", 99),
    "T3_maturity": lambda g: g["maturity"]["after"].__setitem__("mean_rank", 1.0),
    "T4_increment": lambda g: g["increments"]["entries"][0].__setitem__("current_level", "REAL"),
    "T7_entry_rank": lambda g: g["increments"]["entries"][3].__setitem__("rank_delta", -3),
    "T8_entry_sha": lambda g: g["increments"]["entries"][2].__setitem__("sha256", "0"*64),
    "T9_chain_mid": lambda g: g["increments"]["entries"][1].__setitem__("prev_sha256", "0"*64),
    "T5_criteria": lambda g: g["criteria"][0].__setitem__("pass", not g["criteria"][0]["pass"]),
    "T6_verdict": lambda g: g.__setitem__("verdict", "FAIL"),
}


def verify(d):
    p = subprocess.run([sys.executable, "-m", "tools.maturity_gate_h6.cli", "verify", "--out", str(d)],
                       cwd=ROOT, capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"verified": None, "problems": ["unparsable: " + (p.stdout + p.stderr)[-300:]]}


def run():
    rows = []
    for name, mutate in list(CASES.items()) + [("T0_clean", None)]:
        d = OUT / name
        if d.exists():
            shutil.rmtree(d)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SRC, d)
        if mutate is not None:
            g = json.loads((d / "gate.json").read_text())
            mutate(g)
            (d / "gate.json").write_text(json.dumps(g, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        rep = verify(d)
        rows.append({"case": name, "detected": rep.get("verified") is False,
                     "problems": len(rep.get("problems") or []),
                     "sample": (rep.get("problems") or [None])[0]})
    return rows


if __name__ == "__main__":
    rows = run()
    injected = [r for r in rows if r["case"] != "T0_clean"]
    summary = {
        "injected": len(injected),
        "detected": sum(1 for r in injected if r["detected"]),
        "recall": round(sum(1 for r in injected if r["detected"]) / len(injected), 6),
        "false_positive": bool([r for r in rows if r["case"] == "T0_clean" and r["detected"]]),
        "cases": rows,
    }
    outp = ROOT / "runs" / "m15032_audit" / "tamper_negative_control.json"
    outp.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))