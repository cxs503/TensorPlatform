#!/usr/bin/env python3
"""结构级域面清单 + 依赖方向门 + 棘轮（P162-N10）。

问题（判例 #608 的续篇）
----------------------
``tools/audit/domain_leak_scan.py`` 只回答「域**词**有没有写进平台代码」。
它拦不住结构级渗漏：平台核心层直接 ``from api.domain.xxx import *``，
词表里一个域词都不出现，词级门全绿，但依赖方向已经反了——核心层被
某个具体领域的模块反向绑住，领域工件一删/一改，平台核心跟着碎。

本工具把「结构面」变成受版本控制的账本，并加**只减不增**棘轮：

1. **结构清单（manifest）**：扫描 ``surface_roots``（缺省 ``backend/api/domain``）
   下全部 ``*.py``，每条记 ``path → sha256 → owner_repo → target_layer →
   status（migrated / DECLARED-exempt）→ expiry → migration_ref → reason``。
2. **依赖方向门**：``layers`` 里声明核心层/路由层/工具层；这些层内的
   ``api.domain.*`` 静态 import 与动态 ``import_module("api.domain.*")``
   都是违规，除非在 manifest 里登记了带到期的豁免（按 path×module 计数封顶）。
   允许的解析路径：``api.core.domain_provider.provide_domain()``——域能力
   经注册表在路由层解析，核心层只依赖这个接缝。
3. **棘轮**：manifest 入版本库。domain 面新增文件/新增依赖 → 红；
   删除已登记文件 → 退出码 3（待确认），``confirm-removal`` 后基线 −1 才转绿；
   已登记文件内容漂移 → 红，需 ``bless``。每次接受变更都要在审批链里留
   ``by=owner`` 的记录（反绕道：手改 manifest 会被 ``integrity`` 校验判红）。
4. **复用词级门**：同一份 ``config/domain_word_denylist.json``、同一批
   ``scan_paths``，直接调 ``domain_leak_scan.scan()``（不重写、不改判定），
   结果作为结构级的补充证据（``word_scan`` 段）。

退出码
------
0 = 绿；1 = 红（结构面增长/依赖越界/豁免缺到期/账本被手改）；
3 = 删除待确认（基线仍登记该文件）；5 = WITHHELD（政策或清单缺失，
降级为仅词级，不崩也不假绿）。

用法
----
    python3 tools/audit/domain_surface_manifest.py check --today 2026-11-10 --json out.json
    python3 tools/audit/domain_surface_manifest.py generate --approval-by owner --approval-ref M-1 --approval-issued 2026-11-10
    python3 tools/audit/domain_surface_manifest.py confirm-removal --approval-by owner --approval-ref M-2 --approval-issued 2026-11-10
    python3 tools/audit/domain_surface_manifest.py bless --approval-by owner --approval-ref M-3 --approval-issued 2026-11-10
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_REL = "config/domain_surface_policy.json"
MANIFEST_REL = "config/domain_surface_manifest.json"
APPROVALS_REL = "config/domain_surface_approvals.json"
SCHEMA_VERSION = "1.0"
DOMAIN_PACKAGE = "api.domain"
DEFAULT_SEAM = "api.core.domain_provider"

EXIT_PASS, EXIT_FAIL, EXIT_REMOVAL_PENDING, EXIT_WITHHELD = 0, 1, 3, 5


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def canonical(obj) -> str:
    """规范化 JSON 串：所有摘要都基于它，保证跨机器/跨次可复现。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# 政策 / 清单
# --------------------------------------------------------------------------- #
def load_policy(root: Path = ROOT, path: Path | str | None = None) -> dict | None:
    return _load_json(Path(path) if path else root / POLICY_REL)


def load_manifest(root: Path = ROOT, path: Path | str | None = None) -> dict | None:
    return _load_json(Path(path) if path else root / MANIFEST_REL)


def load_approvals(root: Path = ROOT, path: Path | str | None = None) -> dict | None:
    return _load_json(Path(path) if path else root / APPROVALS_REL)


def content_digest(doc: dict) -> str:
    """清单内容摘要：政策指纹 + 模块账 + 依赖豁免账（integrity 段自身除外）。"""
    return sha256_text(
        canonical(
            {
                "policy_digest": doc.get("policy_digest"),
                "surface_roots": doc.get("surface_roots"),
                "modules": doc.get("modules"),
                "dependency_exemptions": doc.get("dependency_exemptions"),
            }
        )
    )


# --------------------------------------------------------------------------- #
# 结构面枚举
# --------------------------------------------------------------------------- #
def surface_files(root: Path, policy: dict) -> list[str]:
    out: list[str] = []
    for rel_root in policy["surface_roots"]:
        d = root / rel_root
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.py")):
            out.append(_rel(p, root))
    return sorted(out)


def owner_for(rel_path: str, policy: dict) -> tuple[str, str, str]:
    """路径 → (owner_repo, target_layer, rationale)；规则顺序敏感，先匹配者胜。"""
    name = Path(rel_path).stem
    for rule in policy.get("ownership_rules", []):
        if name in rule.get("names", []) or any(name.startswith(p) for p in rule.get("prefix", [])):
            return rule["owner_repo"], rule["target_layer"], rule.get("rationale", "")
    return (
        policy["default_owner_repo"],
        policy["default_target_layer"],
        policy.get("default_rationale", ""),
    )


def iter_layer_files(root: Path, policy: dict) -> list[tuple[str, str]]:
    """(相对路径, 层名)。结构面自身与排除目录不算平台层。"""
    surface = {str(r).rstrip("/") for r in policy["surface_roots"]}
    excluded = [str(e).rstrip("/") for e in policy.get("layer_exclude", [])]
    seen: dict[str, str] = {}
    for layer, paths in policy.get("layers", {}).items():
        for rel_root in paths:
            d = root / rel_root
            if not d.is_dir():
                continue
            candidates = [d] if d.is_file() else sorted(d.rglob("*.py"))
            for p in candidates:
                if not p.is_file() or p.suffix != ".py":
                    continue
                rel = _rel(p, root)
                if any(rel == s or rel.startswith(s + "/") for s in surface):
                    continue
                if any(rel == e or rel.startswith(e + "/") for e in excluded):
                    continue
                seen.setdefault(rel, layer)
    return sorted(seen.items())


def _is_domain_module(name: str | None) -> bool:
    return bool(name) and (name == DOMAIN_PACKAGE or name.startswith(DOMAIN_PACKAGE + "."))


def scan_layers(root: Path, policy: dict) -> dict:
    """一次遍历平台各层，同时产出依赖边与接缝使用面。

    性能：先做文本预筛（文件里连 ``api.domain`` / 接缝模块名都不出现就不解析），
    1627 个层内文件的结构扫描因此从 ~19 s 降到亚秒级；预筛不降低召回——被检
    形态本身就是含该字面量的 import 或字符串常量。
    """
    seam = (policy.get("provider_seam") or {}).get("module", DEFAULT_SEAM)
    grouped: dict[tuple[str, str, str], list[int]] = {}
    seam_files: list[str] = []
    for rel, _layer in iter_layer_files(root, policy):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if DOMAIN_PACKAGE not in text and seam not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_domain_module(alias.name):
                        grouped.setdefault((rel, alias.name, "import"), []).append(node.lineno)
                    elif alias.name == seam or alias.name.startswith(seam + "."):
                        seam_files.append(rel)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_domain_module(module):
                    grouped.setdefault((rel, module, "from"), []).append(node.lineno)
                elif module == seam or module.startswith(seam + "."):
                    seam_files.append(rel)
            elif isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        target = arg.value.strip().split(":")[0]
                        if _is_domain_module(target):
                            grouped.setdefault((rel, arg.value, "import_module"), []).append(node.lineno)
    edges = [
        {"path": rel, "module": module, "kind": kind, "sites": len(lines), "lines": sorted(lines)}
        for (rel, module, kind), lines in sorted(grouped.items())
    ]
    return {"edges": edges, "provider_seam_files": sorted(set(seam_files))}


def dependency_edges(root: Path, policy: dict) -> list[dict]:
    """核心/路由/工具层里所有指向 api.domain 的依赖，按 (path, module, kind) 归并。"""
    return scan_layers(root, policy)["edges"]


def provider_seam_usage(root: Path, policy: dict) -> list[str]:
    """核心/路由层里经 provide_domain 接缝解析域能力的文件（合规解析路径的证据面）。"""
    return scan_layers(root, policy)["provider_seam_files"]


def route_census(root: Path = ROOT, rel_dir: str = "backend/api") -> dict:
    """静态路由普查（向后兼容判据 C7 的不变量）：只数已声明的端点，不执行应用。"""
    entries: list[tuple[str, str, str]] = []
    files = 0
    for p in sorted((root / rel_dir).glob("*.py")):
        files += 1
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                obj = dec.func.value
                if not isinstance(obj, ast.Name) or obj.id not in ("router", "app"):
                    continue
                path = ""
                if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    path = dec.args[0].value
                entries.append((_rel(p, root), dec.func.attr, path))
    entries.sort()
    return {
        "dir": rel_dir,
        "files": files,
        "routes": len(entries),
        "sha256": sha256_text(canonical(entries)),
    }


def absent_layers(root: Path, policy: dict) -> list[str]:
    declared = [str(layer_root) for paths in policy.get("layers", {}).values() for layer_root in paths]
    return sorted({r for r in declared if not (root / r).exists()})


# --------------------------------------------------------------------------- #
# 生成 / 写状态
# --------------------------------------------------------------------------- #
def _approval_record(kind: str, by: str, ref: str, issued: str, reason: str, digest: str,
                     modules_after: int, detail: dict) -> dict:
    return {
        "id": f"{kind}-{digest[:12]}",
        "kind": kind,
        "by": by,
        "issued": issued,
        "ref": ref,
        "reason": reason,
        "modules_after": modules_after,
        "content_sha256_after": digest,
        "detail": detail,
    }


def build_manifest(root: Path, policy: dict, *, modules: list[dict], deps: list[dict]) -> dict:
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "tools/audit/domain_surface_manifest.py",
        "policy_digest": sha256_text(canonical(policy)),
        "surface_roots": list(policy["surface_roots"]),
        "layers": {k: list(v) for k, v in policy.get("layers", {}).items()},
        "absent_layers": absent_layers(root, policy),
        "modules": sorted(modules, key=lambda m: m["path"]),
        "dependency_exemptions": sorted(deps, key=lambda d: (d["path"], d["module"], d["kind"])),
    }
    doc["integrity"] = {"content_sha256": content_digest(doc), "algorithm": "sha256/canonical-json"}
    return doc


def build_modules_from_fs(root: Path, policy: dict, previous: dict[str, dict], *,
                          approval_ref: str) -> list[dict]:
    """以文件系统为准重建模块账；保留既有条目的 owner/status/expiry（政策改了则以政策为准）。"""
    modules: list[dict] = []
    for rel in surface_files(root, policy):
        owner_repo, target_layer, rationale = owner_for(rel, policy)
        old = previous.get(rel) or {}
        modules.append(
            {
                "path": rel,
                "sha256": sha256_file(root / rel),
                "owner_repo": owner_repo,
                "target_layer": target_layer,
                "status": old.get("status", policy["default_status"]),
                "expiry": old.get("expiry", policy["default_expiry"]),
                "migration_ref": old.get("migration_ref", approval_ref),
                "reason": old.get("reason") or rationale or policy.get("default_reason", ""),
            }
        )
    return modules


def _dependency_records(root: Path, policy: dict,
                        previous: dict[tuple[str, str, str], dict]) -> list[dict]:
    out = []
    for edge in dependency_edges(root, policy):
        key = (edge["path"], edge["module"], edge["kind"])
        old = previous.get(key) or {}
        out.append(
            {
                **edge,
                "expiry": old.get("expiry", policy["default_expiry"]),
                "reason": old.get("reason") or policy.get("default_dependency_reason", ""),
                "allowed_sites": max(int(old.get("allowed_sites", 0)), edge["sites"]),
            }
        )
    return out


def write_state(root: Path, policy: dict, *, kind: str, by: str, ref: str, issued: str,
                reason: str, detail: dict) -> dict:
    """重新落盘 manifest + 审批链（追加一条 by=owner 的记录），返回新 manifest。"""
    old_manifest = load_manifest(root) or {}
    prev_modules = {m["path"]: m for m in old_manifest.get("modules", []) if isinstance(m, dict)}
    prev_deps = {
        (d.get("path"), d.get("module"), d.get("kind")): d
        for d in old_manifest.get("dependency_exemptions", [])
        if isinstance(d, dict)
    }
    modules = build_modules_from_fs(root, policy, prev_modules, approval_ref=ref)
    deps = _dependency_records(root, policy, prev_deps)
    manifest = build_manifest(root, policy, modules=modules, deps=deps)
    digest = manifest["integrity"]["content_sha256"]

    approvals = load_approvals(root) or {"schema_version": SCHEMA_VERSION, "records": []}
    records = list(approvals.get("records", []))
    records.append(
        _approval_record(
            kind, by, ref, issued, reason, digest, len(modules),
            {**detail, "module_paths_after": sorted(m["path"] for m in modules)},
        )
    )
    approvals = {
        "schema_version": SCHEMA_VERSION,
        "policy": "append-only：每条记录 = 一次被 owner 接受的基线变更（新增/删除/内容漂移）",
        "records": records,
    }

    target_dir = root / MANIFEST_REL
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_REL).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / APPROVALS_REL).write_text(
        json.dumps(approvals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


# --------------------------------------------------------------------------- #
# 词级门复用（不得改变 #608 既有判定）
# --------------------------------------------------------------------------- #
def run_word_scan(root: Path = ROOT) -> dict:
    """直接调用 domain_leak_scan.scan()：同一 denylist、同一 scan_paths、同一判定。"""
    scanner = root / "tools" / "audit" / "domain_leak_scan.py"
    denylist = root / "config" / "domain_word_denylist.json"
    if not scanner.is_file() or not denylist.is_file():
        return {
            "status": "unavailable",
            "reason": f"missing {'scanner' if not scanner.is_file() else 'denylist'}",
        }
    spec = importlib.util.spec_from_file_location("_domain_leak_scan_for_manifest", scanner)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    try:
        result = module.scan(denylist_path=denylist, root=root)
    except (OSError, ValueError) as exc:  # 输入缺失 → 降级，不影响结构判据
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "ok",
        "scanner": _rel(scanner, root),
        "denylist": _rel(denylist, root),
        "files_scanned": len(result["files_scanned"]),
        "word_count": result["word_count"],
        "hit_count": result["hit_count"],
        "ok": result["ok"],
        "hits": result["hits"],
        "digest": sha256_text(canonical(result)),
    }


# --------------------------------------------------------------------------- #
# 检查（门 + 棘轮）
# --------------------------------------------------------------------------- #
def _structural_reasons(manifest: dict) -> list[str]:
    reasons = []
    modules = manifest.get("modules")
    if not isinstance(modules, list) or not modules:
        reasons.append("manifest_empty")
    for m in modules or []:
        for field in ("path", "sha256", "owner_repo", "status", "expiry", "migration_ref", "reason"):
            if not m.get(field):
                reasons.append(f"manifest_field_missing:{m.get('path', '?')}:{field}")
        if m.get("status") not in ("migrated", "DECLARED-exempt"):
            reasons.append(f"manifest_status_invalid:{m.get('path', '?')}:{m.get('status')}")
    return reasons


def _expired(expiry: str, today: str) -> bool:
    return bool(expiry) and expiry < today


def check(root: Path = ROOT, *, today: str | None = None, word_scan: dict | None = None) -> dict:
    """结构门 + 棘轮总判定。返回可落盘的确定性结果（无时间戳/无耗时）。"""
    policy = load_policy(root)
    manifest = load_manifest(root)
    word = word_scan if word_scan is not None else run_word_scan(root)
    result: dict = {
        "schema_version": SCHEMA_VERSION,
        "tool": "tools/audit/domain_surface_manifest.py",
        "root": str(root),
        "today": today or "",
        "budget_limit_seconds": int((policy or {}).get("budget_seconds", 120)),
        "policy": {"path": POLICY_REL, "digest": sha256_text(canonical(policy)) if policy else None},
        "manifest": {"path": MANIFEST_REL, "digest": None, "module_count": 0, "dependency_count": 0},
        "surface": {"on_disk": [], "baseline": [], "added": [], "missing": [], "content_drift": []},
        "dependencies": {"observed": 0, "registered": 0, "unregistered": [], "expanded": [],
                         "expired": [], "provider_seam_files": []},
        "exemptions": {"missing_expiry": [], "expired": []},
        "approvals": {"records": 0, "head_matches_manifest": False},
        "word_scan": word,
        "reasons": [],
        "warnings": [],
        "verdict": "pass",
        "exit_code": EXIT_PASS,
    }
    today = today or ""

    if policy is None or manifest is None:
        result["reasons"].append("policy_missing" if policy is None else "manifest_missing")
        result["verdict"] = "withheld"
        result["exit_code"] = EXIT_WITHHELD
        result["warnings"].append(
            "结构清单/政策缺失 → 降级为仅词级（WITHHELD，不崩不假绿）；先运行 generate 重建基线"
        )
        result["digest"] = sha256_text(canonical(result))
        return result

    reasons: list[str] = result["reasons"]

    # 1) 清单自洽性（空清单 fail-closed；字段非空；状态合法）
    reasons.extend(_structural_reasons(manifest))

    # 2) 政策漂移：清单指纹必须等于当前政策指纹
    policy_digest = result["policy"]["digest"]
    if manifest.get("policy_digest") != policy_digest:
        reasons.append("policy_drift")

    # 3) 完整性（反手改）：内容摘要必须等于 integrity 段
    integrity = manifest.get("integrity", {}) or {}
    recomputed = content_digest(manifest)
    result["manifest"]["digest"] = recomputed
    if integrity.get("content_sha256") != recomputed:
        reasons.append("manifest_hand_edited")

    baseline_modules = {m["path"]: m for m in manifest.get("modules", []) if isinstance(m, dict)}
    on_disk = surface_files(root, policy)
    result["manifest"]["module_count"] = len(baseline_modules)
    result["surface"]["on_disk"] = on_disk
    result["surface"]["baseline"] = sorted(baseline_modules)
    result["surface"]["added"] = sorted(set(on_disk) - set(baseline_modules))
    result["surface"]["missing"] = sorted(set(baseline_modules) - set(on_disk))
    result["surface"]["content_drift"] = sorted(
        rel for rel in on_disk
        if rel in baseline_modules and sha256_file(root / rel) != baseline_modules[rel].get("sha256")
    )
    if result["surface"]["added"]:
        reasons.append("surface_growth")
    if result["surface"]["missing"]:
        reasons.append("removal_pending")
    if result["surface"]["content_drift"]:
        reasons.append("content_drift")

    # 4) 依赖方向门
    registered = {
        (d.get("path"), d.get("module"), d.get("kind")): d
        for d in manifest.get("dependency_exemptions", [])
        if isinstance(d, dict)
    }
    result["dependencies"]["registered"] = len(registered)
    result["manifest"]["dependency_count"] = len(registered)
    layer_scan = scan_layers(root, policy)
    result["dependencies"]["provider_seam_files"] = layer_scan["provider_seam_files"]
    observed = layer_scan["edges"]
    result["dependencies"]["observed"] = sum(e["sites"] for e in observed)
    for edge in observed:
        key = (edge["path"], edge["module"], edge["kind"])
        reg = registered.get(key)
        if reg is None:
            if "dependency_not_registered" not in reasons:
                reasons.append("dependency_not_registered")
            result["dependencies"]["unregistered"].append(
                {"path": edge["path"], "line": edge["lines"][0], "module": edge["module"], "kind": edge["kind"]}
            )
            continue
        allowed = int(reg.get("allowed_sites", 0))
        if edge["sites"] > allowed:
            if "dependency_expanded" not in reasons:
                reasons.append("dependency_expanded")
            result["dependencies"]["expanded"].append(
                {"path": edge["path"], "line": edge["lines"][-1], "module": edge["module"],
                 "observed": edge["sites"], "allowed": allowed}
            )
        if not reg.get("expiry"):
            result["exemptions"]["missing_expiry"].append({"path": edge["path"], "module": edge["module"]})
        elif today and _expired(str(reg.get("expiry")), today):
            reason = f"exemption_expired:{edge['path']}:{edge['module']}"
            reasons.append(reason)
            result["dependencies"]["expired"].append(
                {"path": edge["path"], "module": edge["module"], "expiry": reg.get("expiry")}
            )

    for path, mod in sorted(baseline_modules.items()):
        if mod.get("status") == "DECLARED-exempt" and not mod.get("expiry"):
            result["exemptions"]["missing_expiry"].append({"path": path, "module": None})
        elif today and _expired(str(mod.get("expiry") or ""), today):
            reasons.append(f"exemption_expired:{path}")
            result["exemptions"]["expired"].append({"path": path, "expiry": mod.get("expiry")})
    if result["exemptions"]["missing_expiry"]:
        reasons.append("exemption_missing_expiry")
    if result["exemptions"]["expired"] and "exemption_expired" not in reasons:
        reasons.append("exemption_expired")

    # 5) 审批链（反绕道）：最后一次被 owner 接受的基线必须就是当前这份清单
    approvals = load_approvals(root) or {}
    records = [r for r in approvals.get("records", []) if isinstance(r, dict)]
    result["approvals"]["records"] = len(records)
    authority = policy.get("approval_authority", "owner")
    if not records:
        reasons.append("approval_missing")
    else:
        head = records[-1]
        result["approvals"]["head_matches_manifest"] = (
            head.get("content_sha256_after") == integrity.get("content_sha256")
        )
        if not result["approvals"]["head_matches_manifest"]:
            reasons.append("approval_stale")
        if head.get("by") != authority:
            reasons.append(f"approval_authority_invalid:{head.get('by')}")
        if int(head.get("modules_after", -1)) != len(baseline_modules):
            reasons.append("approval_module_count_mismatch")

    # 判定
    blocking = [r for r in reasons if r != "removal_pending"]
    if blocking:
        result["verdict"] = "fail"
        result["exit_code"] = EXIT_FAIL
    elif "removal_pending" in reasons:
        result["verdict"] = "removal_pending"
        result["exit_code"] = EXIT_REMOVAL_PENDING
        result["warnings"].append(
            "已登记模块从磁盘消失：确认是迁移而非删功能换绿后，运行 "
            "confirm-removal --approval-by owner --approval-ref <单号>（基线 −1）"
        )
    else:
        result["verdict"] = "pass"
        result["exit_code"] = EXIT_PASS

    if word.get("status") == "ok" and not word.get("ok"):
        result["warnings"].append(
            "词级门（#608）有命中：作为补充证据记录，判定仍以词级门自身退出码为准"
        )
    result["digest"] = sha256_text(canonical(result))
    return result


def apply_change(root: Path, *, kind: str, by: str, ref: str, issued: str, reason: str = "",
                 today: str | None = None) -> tuple[int, dict]:
    """接受一次基线变更：generate（首次/新增）/ confirm-removal（删除）/ bless（内容漂移）。"""
    policy = load_policy(root)
    if policy is None:
        return EXIT_WITHHELD, {"error": "policy_missing", "path": POLICY_REL}
    authority = policy.get("approval_authority", "owner")
    if by != authority:
        return EXIT_FAIL, {"error": "approval_authority_invalid", "by": by, "required": authority}

    manifest = load_manifest(root) or {"modules": []}
    baseline = {m["path"]: m for m in manifest.get("modules", []) if isinstance(m, dict)}
    on_disk = surface_files(root, policy)
    added = sorted(set(on_disk) - set(baseline))
    missing = sorted(set(baseline) - set(on_disk))
    drift = sorted(
        rel for rel in on_disk if rel in baseline and sha256_file(root / rel) != baseline[rel].get("sha256")
    )
    detail = {"added": added, "removed": missing, "content_drift": drift}

    if kind == "confirm-removal":
        if added or drift:
            return EXIT_FAIL, {"error": "removal_only", "detail": detail}
        if not missing:
            return EXIT_FAIL, {"error": "nothing_to_confirm", "detail": detail}
    elif kind == "bless":
        if added or missing:
            return EXIT_FAIL, {"error": "bless_only", "detail": detail}
        if not drift:
            return EXIT_FAIL, {"error": "nothing_to_bless", "detail": detail}
    elif kind != "generate":
        return EXIT_FAIL, {"error": f"unknown_kind:{kind}"}

    new_manifest = write_state(root, policy, kind=kind, by=by, ref=ref, issued=issued,
                               reason=reason or policy.get("default_approval_reason", ""), detail=detail)
    return EXIT_PASS, {
        "kind": kind,
        "modules_after": len(new_manifest["modules"]),
        "content_sha256": new_manifest["integrity"]["content_sha256"],
        "detail": detail,
        "manifest": MANIFEST_REL,
        "approvals": APPROVALS_REL,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_summary(result: dict) -> None:
    print(f"[domain-surface] verdict={result['verdict']} exit={result['exit_code']} "
          f"modules={result['manifest']['module_count']}/on_disk={len(result['surface']['on_disk'])} "
          f"deps={result['dependencies']['observed']}/{result['dependencies']['registered']}")
    for rel in result["surface"]["added"]:
        print(f"  + surface_growth  {rel}")
    for rel in result["surface"]["missing"]:
        print(f"  - removal_pending {rel}")
    for rel in result["surface"]["content_drift"]:
        print(f"  ~ content_drift   {rel}")
    for item in result["dependencies"]["unregistered"]:
        print(f"  ! dependency      {item['path']}:{item['line']}  {item['module']} ({item['kind']})")
    for item in result["dependencies"]["expanded"]:
        print(f"  ! dependency+     {item['path']}:{item['line']}  {item['module']} "
              f"{item['observed']}>{item['allowed']}")
    for reason in result["reasons"]:
        if reason.startswith(("exemption", "approval", "manifest", "policy", "dependency_")):
            print(f"  ! {reason}")
    word = result.get("word_scan") or {}
    if word.get("status") == "ok":
        print(f"  word-scan(hits)={word['hit_count']} ok={word['ok']} digest={word['digest'][:12]}")
    else:
        print(f"  word-scan={word.get('status')} {word.get('reason', '')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="结构级域面清单 + 依赖方向门 + 棘轮（P162-N10）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="跑结构与依赖门（CI 用）")
    p_check.add_argument("--root", default=str(ROOT))
    p_check.add_argument("--json", dest="json_out", default=None)
    p_check.add_argument("--today", default=None, help="ISO 日期；给了才做豁免到期判定")
    p_check.add_argument("--quiet", action="store_true")

    for name, helptext in (
        ("generate", "重建基线（首次/接受新增）"),
        ("confirm-removal", "确认删除（基线 −1）"),
        ("bless", "接受内容漂移（重签 sha256）"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--root", default=str(ROOT))
        p.add_argument("--approval-by", required=True)
        p.add_argument("--approval-ref", required=True)
        p.add_argument("--approval-issued", required=True, help="ISO 日期")
        p.add_argument("--reason", default="")
        p.add_argument("--today", default=None)

    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.cmd == "check":
        result = check(root, today=args.today)
        if args.json_out:
            out = Path(args.json_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.quiet:
            _print_summary(result)
        return int(result["exit_code"])

    code, info = apply_change(
        root, kind=args.cmd, by=args.approval_by, ref=args.approval_ref,
        issued=args.approval_issued, reason=args.reason, today=args.today,
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())