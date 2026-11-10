"""注入式负控：五类伪证据 × N 例，计量「假通过率」。

负控不是把代码改坏，而是把**证据**改坏，然后看门有没有放它过去：

============  ==============================================================
class          伪证据形态
============  ==============================================================
no_gate        没有 gate.json（只有口头/日志）
threshold_widened  阈值被改宽到实测值一定通过（登记表比对能否抓到）
sha_mismatch   工件内容被改而 sha256 未更新（复算能否抓到）
missing_unit   缺单位（数字无物理可解释性）
log_only       只贴日志文本，没有可复算数字/指针
criterion_dropped  删掉过不了的判据（登记表⊆门 的完整性检查能否抓到）
measured_handcopied 手抄一个更好看的实测数字（数字-工件复算能否抓到）
path_escape    工件路径指到门外（绝对路径/``..`` 穿越）
registry_omitted 把权威登记表摘掉再判（缺权威是否仍恒等降级为 WITHHELD）
============  ==============================================================

假通过率 = 这些伪证据被判 PASS 的比例；要求恒为 0。
同时给**阳性对照**：真门必须 PASS（否则「一律 WITHHELD」也能刷出假通过率 0）。
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

from .contract import canonical_json
from .verify import PASS, verify_gate

CLASSES = ("no_gate", "threshold_widened", "sha_mismatch", "missing_unit", "log_only",
           "criterion_dropped", "measured_handcopied", "path_escape", "registry_omitted")
PER_CLASS = 12


def _write_case(case_dir: Path, gate: dict | None, *, mutate_artifacts: bool = False) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    if gate is not None:
        (case_dir / "gate.json").write_text(
            json.dumps(gate, ensure_ascii=False, indent=1), encoding="utf-8")


def _copy_evidence(src_dir: Path, dst_dir: Path) -> None:
    src_ev = src_dir / "evidence"
    if not src_ev.is_dir():
        return
    dst_ev = dst_dir / "evidence"
    dst_ev.mkdir(parents=True, exist_ok=True)
    for f in sorted(src_ev.iterdir()):
        if f.is_file():
            shutil.copy2(f, dst_ev / f.name)


def _tamper_artifacts(dst_dir: Path) -> None:
    """改工件里的第一个数字（保持文件名），sha256 不更新 → 复算必然对不上。"""
    for f in sorted((dst_dir / "evidence").glob("*.*")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        changed = [False]

        def walk(node: Any) -> Any:
            if changed[0]:
                return node
            if isinstance(node, dict):
                for k, v in list(node.items()):
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        node[k] = v + 1.0
                        changed[0] = True
                        return node
                    walk(v)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        node[i] = v + 1.0
                        changed[0] = True
                        return node
                    walk(v)
            return node

        walk(doc)
        if changed[0]:
            f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            return


def build_cases(gate_dirs: list[Path], out_dir: str | Path, *,
                per_class: int = PER_CLASS) -> list[dict]:
    """由真门目录生成负控用例（确定性：按排序后的门目录循环取用）。"""
    gates = [Path(d) for d in sorted(gate_dirs)]
    if not gates:
        raise ValueError("no gate dirs supplied")
    out = Path(out_dir)
    cases: list[dict] = []
    for cls in CLASSES:
        for i in range(per_class):
            src = gates[i % len(gates)]
            gate = json.loads((src / "gate.json").read_text(encoding="utf-8"))
            case_dir = out / cls / f"case_{i:02d}"
            if case_dir.exists():
                shutil.rmtree(case_dir)
            _copy_evidence(src, case_dir)
            if cls in ("no_gate", "registry_omitted"):
                case_dir.mkdir(parents=True, exist_ok=True)
                extra = {"registry_override": None} if cls == "registry_omitted" else {}
                if cls == "registry_omitted":
                    _write_case(case_dir, gate)
                cases.append({"class": cls, "case": f"case_{i:02d}",
                              "gate_dir": str(case_dir), "expect": "not_pass",
                              "source_gate": src.name, **extra})
                continue
            fake = copy.deepcopy(gate)
            if cls == "threshold_widened":
                for crit in fake.get("criteria", []):
                    thr = crit.get("threshold")
                    if isinstance(thr, (int, float)) and not isinstance(thr, bool):
                        crit["threshold"] = abs(float(thr)) * 10.0 + 100.0
                    elif isinstance(thr, list) and len(thr) == 2:
                        crit["threshold"] = [float(thr[0]) - 100.0, float(thr[1]) + 100.0]
            elif cls == "sha_mismatch":
                _tamper_artifacts(case_dir)
            elif cls == "missing_unit":
                for crit in fake.get("criteria", []):
                    crit.pop("unit", None)
            elif cls == "criterion_dropped":
                crit = fake.get("criteria") or []
                if crit:
                    crit.pop(len(crit) - 1 - (i % min(len(crit), 3)))
            elif cls == "measured_handcopied":
                for crit in fake.get("criteria", []):
                    val = crit.get("measured")
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        crit["measured"] = float(val) * 0.5  # 更好看的手抄数字
            elif cls == "path_escape":
                for crit in fake.get("criteria", []):
                    art = crit.get("artifact") or {}
                    name = str(art.get("path") or "").split("/")[-1]
                    if i % 2 == 0:
                        art["path"] = str((src / "evidence" / name).resolve())  # 绝对路径
                    else:
                        art["path"] = f"../{src.name}/evidence/{name}"  # .. 穿越
                    crit["artifact"] = art
            elif cls == "log_only":
                fake["criteria"] = [
                    {"id": c.get("id"), "log": "all good, see console output",
                     "artifact": c.get("artifact")}
                    for c in fake.get("criteria", [])
                ]
            _write_case(case_dir, fake)
            cases.append({"class": cls, "case": f"case_{i:02d}", "gate_dir": str(case_dir),
                          "expect": "not_pass", "source_gate": src.name})
    return cases


def measure_cases(cases: list[dict], registry: dict | None,
                  positive_dirs: list[Path] | None = None) -> dict:
    """跑负控 + 阳性对照，返回假通过率与逐例判定。"""
    results: list[dict] = []
    false_pass = 0
    for case in cases:
        gate_path = Path(case["gate_dir"]) / "gate.json"
        reg = case["registry_override"] if "registry_override" in case else registry
        res = verify_gate(gate_path if gate_path.is_file() else None,
                          registry=reg,
                          evidence_root=Path(case["gate_dir"]))
        if res["grade"] == PASS:
            false_pass += 1
        results.append({**case, "grade": res["grade"],
                        "codes": sorted({i["code"] for i in res["issues"]}),
                        "verdict_digest": res["verdict_digest"]})
    positives = []
    positive_fail = 0
    for d in sorted(positive_dirs or []):
        gate_path = Path(d) / "gate.json"
        res = verify_gate(gate_path, registry=registry, evidence_root=Path(d))
        if res["grade"] != PASS:
            positive_fail += 1
        positives.append({"gate_dir": str(d), "grade": res["grade"],
                          "verdict_digest": res["verdict_digest"],
                          "codes": sorted({i["code"] for i in res["issues"]})})
    by_class: dict[str, dict[str, Any]] = {}
    for r in results:
        slot = by_class.setdefault(r["class"], {"n": 0, "false_pass": 0, "grades": {}})
        slot["n"] += 1
        slot["grades"][r["grade"]] = slot["grades"].get(r["grade"], 0) + 1
        if r["grade"] == PASS:
            slot["false_pass"] += 1
    return {
        "n_cases": len(results),
        "false_pass": false_pass,
        "false_pass_rate": (false_pass / len(results)) if results else 0.0,
        "by_class": by_class,
        "cases": results,
        "positive_controls": positives,
        "positive_pass": sum(1 for p in positives if p["grade"] == PASS),
        "positive_fail": positive_fail,
        "detection_matrix_sha256": __import__("hashlib").sha256(
            canonical_json([{k: r[k] for k in ("class", "case", "grade", "codes")}
                            for r in results]).encode("utf-8")).hexdigest(),
    }
