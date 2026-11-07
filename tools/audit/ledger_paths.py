#!/usr/bin/env python3
"""能力台账单一路径解析器（capability ledger single resolver）—— 消费者契约门。

为什么需要它（根因）
--------------------
``audit_capabilities`` 工具曾**硬编码单一绝对路径**指向船舶 harness 的
``capabilities.json``；该文件在 incoming 树里只剩 ``.pyc``，于是工具对全部 agent
恒返回「读取能力清单失败」(FileNotFoundError)，证据分级仪整月不可用（m2031）。
补丁把「候选路径表」抄进了每个消费者各一份，于是出现第二种病：**四个消费者可能
解析到不同文件**——agent 工具读 ``capability_ledger_resolved.json``、CLI 读
``capability_ledger.json``、``/health`` 还没有字段、报告 gate 读第三份。同一时刻
平台会对外宣称两个不同的证据等级，且没人能机器裁决哪个是真值。

本模块把「哪个文件是台账真值」变成**一份可机器裁决的契约**：

* 唯一解析入口 :func:`resolve_ledger` —— **永不抛异常**，任何缺陷降级为
  ``status="DEGRADED"`` + 具名 ``reasons``（重定位演练必须不炸）；
* 唯一内容指纹 ``sha256`` —— 4 个消费者必须解析到同一 sha256；
* 唯一 schema 门 :data:`SCHEMA_VERSIONS` —— 不符即 ``SCHEMA_VERSION_MISMATCH``，
  CI（``tools/audit/ledger_contract_gate.py --check``）返回非 0；
* 漂移告警 —— 与 pin 文件 ``artifacts/capability_ledger/ledger_contract.json``
  记录的 sha256 不一致 → ``LEDGER_DRIFT``（台账被改但没重新 pin）。

四个消费者（只允许经由本模块取路径/指纹）
----------------------------------------
1. ``audit_capabilities`` 工具 → ``tools/audit/ledger_consumer_audit_tool.py``
2. 能力审计 CLI            → ``tools/audit/capability_audit_cli.py::ledger_ref``
3. ``/health``              → ``backend/api/capability_health.py::ledger_ref``
4. 报告 gate                → ``tools/audit/report_gate_ledger.py::ledger_ref``

用法
----
    python3 tools/audit/ledger_paths.py --report        # 打印契约报告（JSON）
    python3 tools/audit/ledger_paths.py --check         # 契约绿/红，退出码 0/1
    python3 tools/audit/ledger_paths.py --write-pin     # 刷新 pin（台账合法变更后）
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CONTRACT_VERSION = "capability-ledger-consumer-contract/1.0"
#: 允许的台账 schema_version；不符 → CI 红（新增版本必须显式登记，禁止静默兼容）
SCHEMA_VERSIONS = ("2.0",)

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

CONSUMERS = ("audit_capabilities", "cli", "health", "report_gate")

#: 消费者访问器：模块名（或 "<file>路径"）+ 属性名
CONSUMER_SPECS = {
    "audit_capabilities": ("tools.audit.ledger_consumer_audit_tool", "ledger_ref"),
    "cli": ("tools.audit.capability_audit_cli", "ledger_ref"),
    "health": ("<file>backend/api/capability_health.py", "ledger_ref"),
    "report_gate": ("tools.audit.report_gate_ledger", "ledger_ref"),
}

LEDGER_REL = Path("artifacts/capability_ledger/capability_ledger.json")
RESOLVED_REL = Path("artifacts/capability_ledger/capability_ledger_resolved.json")
PIN_REL = Path("artifacts/capability_ledger/ledger_contract.json")
HARNESS_CAPS_REL = Path("backend/platform_harness/capabilities.json")
FALSIFICATION_REL = Path("artifacts/capability_falsification/capabilities.json")
#: 同一台账的等价副本（只有它们之间有图章可比性；resolved/falsification 是派生物）
DUPLICATE_CANDIDATES = ("harness", "env")


# ── 基础工具 ────────────────────────────────────────────────────────────
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidate_paths(root: Path | None = None, ledger_hint: str | os.PathLike | None = None) -> list[dict]:
    """台账候选文件（按优先级）。每个候选项带 ``label``，供漂移审计区分等价副本。"""
    root = Path(root or repo_root())
    cands: list[dict] = []
    env_path = os.environ.get("TP_CAP_LEDGER")
    if ledger_hint:
        cands.append({"label": "hint", "path": Path(ledger_hint)})
    if env_path:
        cands.append({"label": "env", "path": Path(env_path)})
    cands.append({"label": "canonical", "path": root / LEDGER_REL})
    cands.append({"label": "resolved", "path": root / RESOLVED_REL})
    cands.append({"label": "harness", "path": root / HARNESS_CAPS_REL})
    cands.append({"label": "falsification", "path": root / FALSIFICATION_REL})
    return cands


def render_candidates(root: Path | None = None) -> list[dict]:
    """**渲染**候选序（工具正文优先用已对账的 resolved，再退到原始台账）。

    与 :func:`resolve_ledger` 的**身份**候选区分开：身份只认 canonical 台账
    （有 schema_version、有四消费者 sha256），渲染则允许用派生富化件。
    顺序沿用历史工具行为（env > resolved > harness > ledger > falsification），
    以免 agent 侧正文格式与既有回归判据脱钩。
    """
    root = Path(root or repo_root())
    cands: list[dict] = []
    env_path = os.environ.get("TP_CAP_LEDGER")
    if env_path:
        cands.append({"label": "env", "path": Path(env_path)})
    cands.append({"label": "resolved", "path": root / RESOLVED_REL})
    cands.append({"label": "harness", "path": root / HARNESS_CAPS_REL})
    cands.append({"label": "canonical", "path": root / LEDGER_REL})
    cands.append({"label": "falsification", "path": root / FALSIFICATION_REL})
    return cands


def _summarize(payload) -> dict:
    """把台账/派生物/快照三种 schema 归一成 (capabilities, unverified, overclaim)。"""
    caps = payload.get("capabilities") if isinstance(payload, dict) else None
    rows: list[dict] = []
    if isinstance(caps, list):
        rows = [c for c in caps if isinstance(c, dict)]
    elif isinstance(caps, dict):
        rows = [{"id": k, "claimed_level": v} for k, v in caps.items()]
    overclaim = 0
    unverified = 0
    for c in rows:
        div = str(c.get("divergence") or "").upper()
        claimed = str(c.get("claimed_level") or c.get("level") or "").upper()
        rec = str(c.get("reconciled_level") or "").upper()
        if div == "OVERCLAIM":
            overclaim += 1
        if div in ("OVERCLAIM", "REVERIFY", "UNVERIFIED") or not rec:
            unverified += 1
        elif div == "" and claimed and rec and claimed != rec:
            overclaim += 1
    return {"capabilities": len(rows), "unverified": unverified, "overclaim": overclaim}


def _load_json(path: Path):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _read_pin(root: Path) -> dict | None:
    p = root / PIN_REL
    if not p.is_file():
        return None
    try:
        return _load_json(p)
    except Exception:
        return None


def _path_repr(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


# ── 唯一解析入口 ────────────────────────────────────────────────────────
def resolve_ledger(root: str | os.PathLike | None = None,
                   ledger_hint: str | os.PathLike | None = None) -> dict:
    """解析能力台账。**永不抛异常**：所有失败都体现为 status/reasons。

    返回字段（消费者只允许读这些）：
    ``path`` 台账绝对路径（找不到时 None）/ ``path_rel`` 仓内相对路径 /
    ``sha256`` 内容指纹 / ``schema_version`` / ``capabilities`` 条数 /
    ``unverified`` 未获证实条数 / ``overclaim`` 自述强于证据的条数 /
    ``status`` OK|DEGRADED / ``reasons`` 具名缺陷 / ``candidates`` 候选审计 /
    ``drift`` 漂移告警 / ``derived`` resolved 派生物（path+sha256）/ ``pinned``。
    """
    root = Path(root or repo_root())
    reasons: list[str] = []
    cands = candidate_paths(root, ledger_hint)

    audit: list[dict] = []
    chosen: dict | None = None
    for c in cands:
        p: Path = c["path"]
        rec = {"label": c["label"], "path": str(p),
               "path_rel": _path_repr(p, root), "exists": p.is_file(),
               "sha256": None, "schema_version": None, "parse_ok": False}
        if rec["exists"]:
            try:
                rec["sha256"] = sha256_file(p)
                payload = _load_json(p)
                rec["parse_ok"] = isinstance(payload, dict)
                rec["schema_version"] = payload.get("schema_version") if isinstance(payload, dict) else None
            except Exception as exc:  # 坏文件：如实登记，继续试下一个候选
                rec["error"] = f"{type(exc).__name__}: {exc}"
        audit.append(rec)
        if chosen is None and rec["parse_ok"] and c["label"] in ("hint", "env", "canonical"):
            chosen = {"ref": rec, "payload": payload}

    if not any(c["exists"] for c in audit):
        reasons.append("NO_LEDGER")
    if chosen is None:
        reasons.append("NO_PARSEABLE_LEDGER")
        # 退化：允许读任一可解析的等价副本，但状态仍是 DEGRADED（不得假装健康）
        for rec in audit:
            if rec["parse_ok"] and rec["label"] in DUPLICATE_CANDIDATES:
                reasons.append("FALLBACK_NON_CANONICAL")
                break

    ref: dict = {
        "contract_version": CONTRACT_VERSION,
        "root": str(root),
        "path": None,
        "path_rel": None,
        "sha256": None,
        "schema_version": None,
        "schema_ok": False,
        "capabilities": 0,
        "unverified": 0,
        "overclaim": 0,
        "status": STATUS_DEGRADED,
        "reasons": reasons,
        "candidates": audit,
        "drift": [],
        "derived": None,
        "pinned": None,
        "relocated": False,
        "resolved_at": _now(),
    }

    if chosen is not None:
        rec, payload = chosen["ref"], chosen["payload"]
        ref.update({
            "path": rec["path"], "path_rel": rec["path_rel"],
            "sha256": rec["sha256"], "schema_version": rec["schema_version"],
        })
        ref["schema_ok"] = ref["schema_version"] in SCHEMA_VERSIONS
        if not ref["schema_ok"]:
            reasons.append("SCHEMA_VERSION_MISMATCH")
        ref["relocated"] = rec["label"] in ("hint", "env")
        ref.update(_summarize(payload))

    # 派生物（resolved）：只做富化，不改变真值身份
    derived = next((c for c in audit if c["label"] == "resolved" and c["parse_ok"]), None)
    if derived is not None:
        ref["derived"] = {"path": derived["path"], "path_rel": derived["path_rel"],
                          "sha256": derived["sha256"]}
        try:
            dsum = _summarize(_load_json(Path(derived["path"])))
            if ref["capabilities"] == 0:
                ref.update(dsum)
            else:
                ref["unverified"] = dsum["unverified"]
                ref["overclaim"] = dsum["overclaim"]
        except Exception:
            reasons.append("DERIVED_UNREADABLE")
    else:
        reasons.append("DERIVED_MISSING")

    # 等价副本漂移：同源副本 sha256 不一致 = 有人改了一份没改另一份
    primary_sha = ref["sha256"]
    for c in audit:
        if c["label"] in DUPLICATE_CANDIDATES and c["exists"] and primary_sha and c["sha256"] != primary_sha:
            ref["drift"].append({"against": c["label"], "path_rel": c["path_rel"],
                                 "sha256": c["sha256"], "expected": primary_sha})

    # 图章（pin）漂移告警
    pin = _read_pin(root)
    if pin is None:
        reasons.append("PIN_MISSING")
    else:
        ref["pinned"] = {"sha256": pin.get("sha256"), "path_rel": pin.get("path_rel"),
                         "schema_version": pin.get("schema_version"),
                         "pinned_at": pin.get("pinned_at")}
        if primary_sha and pin.get("sha256") and pin["sha256"] != primary_sha:
            ref["drift"].append({"against": "pin", "path_rel": pin.get("path_rel"),
                                 "sha256": primary_sha, "expected": pin["sha256"]})
        if pin.get("schema_version") and pin["schema_version"] != ref["schema_version"]:
            ref["drift"].append({"against": "pin-schema", "path_rel": pin.get("path_rel"),
                                 "sha256": ref["schema_version"], "expected": pin["schema_version"]})

    if ref["drift"]:
        reasons.append("LEDGER_DRIFT")

    blocking = {"NO_LEDGER", "NO_PARSEABLE_LEDGER", "SCHEMA_VERSION_MISMATCH",
                "LEDGER_DRIFT", "PIN_MISSING", "DERIVED_MISSING"}
    if not set(reasons) & blocking:
        ref["status"] = STATUS_OK
    return ref


def ledger_view(ref: dict | None = None) -> dict:
    """消费者共用的对外视图（``/health`` 字段子集，schema 冻结）。"""
    ref = ref or resolve_ledger()
    return {
        "path": ref.get("path_rel") or ref.get("path"),
        "sha256": ref.get("sha256"),
        "schema_version": ref.get("schema_version"),
        "capabilities": ref.get("capabilities", 0),
        "unverified": ref.get("unverified", 0),
        "overclaim": ref.get("overclaim", 0),
        "status": ref.get("status"),
        "reasons": list(ref.get("reasons") or []),
        "contract_version": CONTRACT_VERSION,
    }


# ── 四消费者契约 ────────────────────────────────────────────────────────
def _load_consumer(module: str, attr: str):
    """按 spec 载入某消费者的访问器（``<file>`` 前缀=按文件加载，避开包初始化副作用）。"""
    root = repo_root()
    if module.startswith("<file>"):
        path = root / module[len("<file>"):]
        if not path.is_file():
            raise FileNotFoundError(f"消费者模块缺失: {path}")
        spec = importlib.util.spec_from_file_location(f"_consumer_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return getattr(mod, attr)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return getattr(importlib.import_module(module), attr)


def consumer_refs() -> dict:
    """四个消费者各自解析到的台账身份（导入失败也如实成行，不静默）。"""
    out: dict = {}
    for name in CONSUMERS:
        module, attr = CONSUMER_SPECS[name]
        entry = {"spec": f"{module}:{attr}", "path": None, "sha256": None,
                 "status": STATUS_DEGRADED, "error": None}
        try:
            ref = _load_consumer(module, attr)()
            entry.update({"path": ref.get("path_rel") or ref.get("path"),
                          "sha256": ref.get("sha256"),
                          "status": ref.get("status")})
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out[name] = entry
    return out


def contract_report(root: str | os.PathLike | None = None) -> dict:
    """契约报告：4 消费者 sha256 一致性 + 台账状态 + 漂移 + schema 门。"""
    ref = resolve_ledger(root)
    cons = consumer_refs()
    canonical = ref.get("sha256")
    shas = {name: c["sha256"] for name, c in cons.items()}
    same_sha = bool(canonical) and all(s == canonical for s in shas.values())
    same_path = len({c["path"] for c in cons.values()}) == 1
    degraded = [n for n, c in cons.items() if c["status"] != STATUS_OK or c["error"]]
    contract_ok = bool(same_sha and ref["status"] == STATUS_OK and not degraded)
    violations: list[str] = []
    if not same_sha:
        violations.append("CONSUMER_SHA256_DIVERGENCE")
    if not same_path:
        violations.append("CONSUMER_PATH_DIVERGENCE")
    for n in degraded:
        violations.append(f"CONSUMER_DEGRADED:{n}")
    violations.extend(f"LEDGER:{r}" for r in (ref.get("reasons") or []))
    return {
        "contract_version": CONTRACT_VERSION,
        "checked_at": _now(),
        "contract_ok": contract_ok,
        "violations": violations,
        "canonical_sha256": canonical,
        "canonical_path": ref.get("path_rel") or ref.get("path"),
        "schema_version": ref.get("schema_version"),
        "status": ref.get("status"),
        "reasons": ref.get("reasons"),
        "drift": ref.get("drift"),
        "relocated": ref.get("relocated"),
        "consumers": cons,
        "ledger": ledger_view(ref),
    }


class LedgerContractViolation(RuntimeError):
    """契约红灯（pytest / CI 用）。"""


def assert_contract(root: str | os.PathLike | None = None) -> dict:
    rep = contract_report(root)
    if not rep["contract_ok"]:
        raise LedgerContractViolation("台账消费者契约红灯: " + ", ".join(rep["violations"]))
    return rep


def write_pin(root: str | os.PathLike | None = None) -> dict:
    """刷新图章（台账合法变更后的人工动作；CI 只读）。"""
    root = Path(root or repo_root())
    ref = resolve_ledger(root)
    if not ref.get("sha256"):
        raise LedgerContractViolation("无法写 pin：台账不可解析 " + str(ref.get("reasons")))
    pin = {
        "contract_version": CONTRACT_VERSION,
        "path_rel": ref["path_rel"],
        "sha256": ref["sha256"],
        "schema_version": ref["schema_version"],
        "capabilities": ref["capabilities"],
        "pinned_at": _now(),
        "consumers": {n: CONSUMER_SPECS[n][0] for n in CONSUMERS},
        "note": "台账内容合法变更后必须重跑 `python3 tools/audit/ledger_paths.py --write-pin`；"
                "否则 ledger_contract_gate --check 判 LEDGER_DRIFT 红灯。",
    }
    p = root / PIN_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pin, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return pin


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="能力台账消费者契约门")
    ap.add_argument("--root", default=None, help="覆盖仓库根（演练用）")
    ap.add_argument("--report", action="store_true", help="打印契约报告 JSON")
    ap.add_argument("--check", action="store_true", help="契约红灯 → 退出码 1")
    ap.add_argument("--write-pin", action="store_true", help="刷新图章")
    args = ap.parse_args(argv)

    if args.write_pin:
        pin = write_pin(args.root)
        print(json.dumps(pin, ensure_ascii=False, indent=1))
        return 0
    rep = contract_report(args.root)
    print(json.dumps(rep if args.report else {
        "contract_ok": rep["contract_ok"], "violations": rep["violations"],
        "canonical_sha256": rep["canonical_sha256"], "ledger": rep["ledger"]},
        ensure_ascii=False, indent=1))
    if args.check:
        return 0 if rep["contract_ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())