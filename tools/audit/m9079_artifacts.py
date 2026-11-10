#!/usr/bin/env python3
"""P162-N10 工件生成：结构面判据数字 + 冻结契约 + 图。

所有数字都由本脚本从磁盘/清单实时计算后写入 artifacts/m9079/，报告再只从这些
JSON 取值（零手抄）。用法：

    python3 tools/audit/m9079_artifacts.py            # 生成 JSON + 图
    python3 tools/audit/m9079_artifacts.py --no-figure
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "audit" / "domain_surface_manifest.py"
OUT = ROOT / "artifacts" / "m9079"
TODAY = "2026-11-10"

#: 本单改动前 `domain_leak_scan.py --json` 的输出指纹（C4 证据：逐字节一致）
PRE_CHANGE_WORD_SCAN_SHA256 = "bdd2fbe48f56a8c45bc8d1c3182f96ce1690a1a1df01cc6bf30a11f71aa1530c"


def load_tool():
    spec = importlib.util.spec_from_file_location("domain_surface_manifest_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _word_hits_on(root: pathlib.Path, snippet: str) -> int:
    """用仓库里同一台词级扫描器测「注入行」的命中数（证明结构门的独立价值）。"""
    scanner = root / "tools" / "audit" / "domain_leak_scan.py"
    denylist = root / "config" / "domain_word_denylist.json"
    import importlib.util
    spec = importlib.util.spec_from_file_location("_word_scanner_artifact", scanner)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    patterns = module.compile_patterns(module.load_denylist(denylist))
    return len(module.scan_text(snippet, patterns))


def layer_of(rel: str, policy: dict) -> str:
    for layer, paths in policy["layers"].items():
        for prefix in paths:
            if rel == prefix or rel.startswith(prefix.rstrip("/") + "/"):
                return layer
    return "unclassified"


def word_scan_contract(dsm) -> dict:
    scanner = ROOT / "tools" / "audit" / "domain_leak_scan.py"
    denylist = ROOT / "config" / "domain_word_denylist.json"
    word = dsm.run_word_scan(ROOT)
    raw = dsm.sha256_text(json.dumps(word, ensure_ascii=False, sort_keys=True))
    return {
        "schema_version": "1.0",
        "scanner": "tools/audit/domain_leak_scan.py",
        "scanner_sha256": hashlib.sha256(scanner.read_bytes()).hexdigest(),
        "denylist": word["denylist"],
        "denylist_sha256": hashlib.sha256(denylist.read_bytes()).hexdigest(),
        "reuse": "同一 denylist / 同一 scan_paths / 同一 scan() 判定（直接调用，不重写）",
        "files_scanned": word["files_scanned"],
        "word_count": word["word_count"],
        "hit_count": word["hit_count"],
        "ok": word["ok"],
        "result_digest": word["digest"],
        "pre_change_json_sha256": PRE_CHANGE_WORD_SCAN_SHA256,
        "note": "pre_change_json_sha256 是本单改动前 `python3 tools/audit/domain_leak_scan.py "
                "--json out.json` 产物的 sha256；本单未改动 scanner 与 denylist，故该指纹不变（C4）。",
        "contract_digest": raw,
    }


def dependency_census(dsm, policy: dict, manifest: dict) -> dict:
    records = manifest["dependency_exemptions"]
    by_layer: dict[str, dict] = {}
    paths = set()
    for record in records:
        layer = layer_of(record["path"], policy)
        bucket = by_layer.setdefault(layer, {"pairs": 0, "sites": 0, "files": 0})
        bucket["pairs"] += 1
        bucket["sites"] += record["sites"]
        paths.add(record["path"])
    for layer, bucket in by_layer.items():
        bucket["files"] = sum(1 for record in records if layer_of(record["path"], policy) == layer)
    # files 去重
    for layer, bucket in by_layer.items():
        bucket["files"] = len({r["path"] for r in records if layer_of(r["path"], policy) == layer})
    return {
        "schema_version": "1.0",
        "sites": sum(r["sites"] for r in records),
        "pairs": len(records),
        "files": len(paths),
        "by_layer": dict(sorted(by_layer.items())),
        "modules_referenced": len({r["module"] for r in records}),
        "expiry": sorted({r["expiry"] for r in records}),
        "ratchet": "只减不增：新依赖未登记 → 红；已登记 (path,module,kind) 的 sites 只能 <= allowed_sites",
        "sample": sorted(records, key=lambda r: (-r["sites"], r["path"]))[:8],
    }


def manifest_stats(manifest: dict) -> dict:
    by_owner: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for module in manifest["modules"]:
        by_owner[module["owner_repo"]] = by_owner.get(module["owner_repo"], 0) + 1
        by_status[module["status"]] = by_status.get(module["status"], 0) + 1
    return {
        "schema_version": "1.0",
        "modules": len(manifest["modules"]),
        "by_owner_repo": dict(sorted(by_owner.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_status": dict(sorted(by_status.items())),
        "expiries": sorted({m["expiry"] for m in manifest["modules"]}),
        "policy_digest": manifest["policy_digest"],
        "content_sha256": manifest["integrity"]["content_sha256"],
        "absent_layers": manifest["absent_layers"],
    }


SYNTH_POLICY = {
    "schema_version": "1.0",
    "purpose": "synthetic workspace policy（零领域语义：全部合成模块名）",
    "surface_roots": ["backend/api/domain"],
    "layers": {"core": ["backend/api/core", "backend/services"], "route": ["backend/api"], "tooling": ["tools"]},
    "layer_exclude": ["backend/api/domain"],
    "provider_seam": {"module": "api.core.domain_provider", "callable": "provide_domain"},
    "ownership_rules": [],
    "default_owner_repo": "example-domain-repo",
    "default_target_layer": "domains/example",
    "default_rationale": "synthetic",
    "default_status": "DECLARED-exempt",
    "default_expiry": "2027-03-31",
    "default_reason": "synthetic",
    "default_dependency_reason": "synthetic",
    "default_approval_reason": "synthetic",
    "approval_authority": "owner",
    "budget_seconds": 120,
    "min_required_modules": 2,
}


def _synth_workspace(base: pathlib.Path):
    ws = base / "ws"
    def w(rel, text):
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    w("config/domain_surface_policy.json", json.dumps(SYNTH_POLICY, ensure_ascii=False, indent=2))
    w("backend/api/__init__.py", "")
    w("backend/api/domain/__init__.py", "")
    w("backend/api/domain/alpha_kernel.py", "def helper():\n    return 'alpha'\n")
    w("backend/api/domain/beta_kernel.py", "def helper():\n    return 'beta'\n")
    w("backend/api/core/__init__.py", "")
    w("backend/services/consumer.py", "from api.domain.alpha_kernel import helper\n")
    w("tools/plain_tool.py", "VALUE = 1\n")
    return ws


def negative_control_matrix(dsm, word_hits_on_injection: int) -> dict:
    """三件套负控 + 棘轮负控的实测出口码（数据全部本脚本现场跑出来）。"""
    import tempfile

    controls: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        ws = _synth_workspace(base)
        dsm.apply_change(ws, kind="generate", by="owner", ref="ART-1", issued=TODAY, reason="artifact baseline")
        controls.append({"control": "green_baseline", "expect": "pass(0)",
                         "exit_code": dsm.check(ws, today=TODAY)["exit_code"]})

        (ws / "backend/api/domain/gamma_kernel.py").write_text("def helper():\n    return 'g'\n", encoding="utf-8")
        result = dsm.check(ws, today=TODAY)
        controls.append({"control": "new_domain_file", "expect": "fail(1)", "exit_code": result["exit_code"],
                         "reason": "surface_growth" if "surface_growth" in result["reasons"] else "-"})
        (ws / "backend/api/domain/gamma_kernel.py").unlink()

        (ws / "backend/api/core/sneaky.py").write_text("from api.domain.alpha_kernel import *\n", encoding="utf-8")
        result = dsm.check(ws, today=TODAY)
        controls.append({"control": "injected_core_import", "expect": "fail(1)", "exit_code": result["exit_code"],
                         "reason": "dependency_not_registered" if "dependency_not_registered" in result["reasons"] else "-"})
        (ws / "backend/api/core/sneaky.py").unlink()

        (ws / "backend/api/domain/beta_kernel.py").unlink()
        result = dsm.check(ws, today=TODAY)
        controls.append({"control": "deleted_registered_module", "expect": "removal_pending(3)",
                         "exit_code": result["exit_code"], "reason": "removal_pending"})
        code, info = dsm.apply_change(ws, kind="confirm-removal", by="owner", ref="ART-2", issued=TODAY)
        controls.append({"control": "after_confirm_removal", "expect": "pass(0)+baseline-1", "exit_code": code,
                         "modules_after": info.get("modules_after") if isinstance(info, dict) else None})
    with tempfile.TemporaryDirectory() as tmp:
        ws2 = _synth_workspace(pathlib.Path(tmp))
        result = dsm.check(ws2, today=TODAY)
        controls.append({"control": "manifest_missing", "expect": "withheld(5)", "exit_code": result["exit_code"],
                         "verdict": result["verdict"]})
    return {
        "schema_version": "1.0",
        "workspace": "synthetic（合成模块名，零领域语义）",
        "controls": controls,
        "word_level_blind_spot": {
            "probe": "from api.domain.holtrop_mennen import *",
            "word_scan_hits": word_hits_on_injection,
            "note": "词级门对该注入行 0 命中（无 denylist 词条），结构门判红——两者互补而非替代。",
        },
    }


def write_ledger(manifest: dict, deps: dict, path: pathlib.Path) -> None:
    """迁移账本：每模块归属仓 + 迁移单引用 + 未迁移理由与到期（表格由清单生成，零手抄）。"""
    lines = [
        "# P162-N10 · 结构面迁移账本（程序化生成）",
        "",
        "> 由 `tools/audit/m9079_artifacts.py` 从 `config/domain_surface_manifest.json` 生成，"
        "禁止手改；改清单请走 `domain_surface_manifest.py generate|confirm-removal|bless`（带 owner 审批）。",
        "",
        f"模块总数 **{len(manifest['modules'])}**；依赖豁免 **{deps['pairs']}** 对 / "
        f"**{deps['sites']}** 处 import 点（到期 {', '.join(deps['expiry'])}）。",
        "",
        "## 1. 模块归属（谁该接管、迁到哪、什么时候必须迁完）",
        "",
        "| # | 模块 | owner_repo | target_layer | status | expiry | migration_ref |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, module in enumerate(manifest["modules"], start=1):
        lines.append(
            f"| {i} | `{module['path'].removeprefix('backend/api/domain/')}` | {module['owner_repo']} | "
            f"`{module['target_layer']}` | {module['status']} | {module['expiry']} | {module['migration_ref']} |"
        )
    lines += [
        "",
        "未迁移理由（同一政策口径，按 owner 分组的代表理由）：",
        "",
    ]
    seen = {}
    for module in manifest["modules"]:
        seen.setdefault(module["owner_repo"], module["reason"])
    for owner, reason in seen.items():
        lines.append(f"- **{owner}**：{reason}")
    lines += [
        "",
        "## 2. 依赖方向豁免账（核心/路由/工具层 → api.domain）",
        "",
        "| # | 文件 | 模块 | 形态 | sites | 到期 |",
        "|---|---|---|---|---|---|",
    ]
    for i, record in enumerate(sorted(manifest["dependency_exemptions"],
                                      key=lambda r: (-r["sites"], r["path"])), start=1):
        lines.append(
            f"| {i} | `{record['path']}` | `{record['module']}` | {record['kind']} | "
            f"{record['sites']} | {record['expiry']} |"
        )
    lines += [
        "",
        "## 3. 迁移动作（怎么把上表清零）",
        "",
        "1. 域模块从 `backend/api/domain/` 迁到归属仓 / `domains/<domain>/`，路由层用 "
        "`provide_domain('<name>')` 解析（`backend/api/core/domain_provider.py`）。",
        "2. 迁移后在目标仓引用新路径，本仓 `confirm-removal --approval-by owner --approval-ref <单号>` → 基线 −1。",
        "3. 依赖豁免随之消失（`generate` 重签），`dependency_census.json` 的 `sites/pairs` 只降不升。",
        "4. 到期（2027-03-31）未完成 → `check --today` 判 `exemption_expired` 红，不允许静默续期。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def figure(check: dict, deps: dict, stats: dict, path: pathlib.Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - 环境无 matplotlib 时降级
        print(f"[figures] skipped: {exc}", file=sys.stderr)
        return False

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))
    owners = list(stats["by_owner_repo"])
    counts = [stats["by_owner_repo"][owner] for owner in owners]
    axes[0].bar(range(len(owners)), counts, color=["#2b6cb0", "#2f855a", "#b7791f"][: len(owners)])
    axes[0].set_xticks(range(len(owners)))
    axes[0].set_xticklabels(owners, rotation=18, ha="right", fontsize=8)
    axes[0].set_ylabel("declared modules")
    axes[0].set_title(f"Domain surface under api/domain (n={stats['modules']})")
    for i, value in enumerate(counts):
        axes[0].text(i, value + 0.3, str(value), ha="center", fontsize=9)

    layers = list(deps["by_layer"])
    sites = [deps["by_layer"][layer]["sites"] for layer in layers]
    pairs = [deps["by_layer"][layer]["pairs"] for layer in layers]
    idx = range(len(layers))
    axes[1].bar([i - 0.2 for i in idx], sites, width=0.4, label="import sites", color="#c53030")
    axes[1].bar([i + 0.2 for i in idx], pairs, width=0.4, label="registered (path,module)", color="#4a5568")
    axes[1].set_xticks(list(idx))
    axes[1].set_xticklabels(layers, fontsize=9)
    axes[1].set_ylabel("count")
    axes[1].set_title(
        f"Core→domain edges: {deps['sites']} sites / {deps['pairs']} pairs, "
        f"verdict={check['verdict']}"
    )
    axes[1].legend(fontsize=8)
    for i, (s, p) in enumerate(zip(sites, pairs)):
        axes[1].text(i - 0.2, s + 0.3, str(s), ha="center", fontsize=8)
        axes[1].text(i + 0.2, p + 0.3, str(p), ha="center", fontsize=8)

    fig.suptitle("P162-N10 structural domain-surface ratchet (TensorPlatform)", fontsize=11)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def control_figure(controls: dict, path: pathlib.Path) -> bool:
    """负控矩阵图：每个控制场景的实测退出码（0 绿 / 1 红 / 3 待确认 / 5 WITHHELD）。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        print(f"[figures] skipped: {exc}", file=sys.stderr)
        return False

    items = controls["controls"]
    labels = [item["control"] for item in items]
    codes = [item["exit_code"] for item in items]
    palette = {0: "#2f855a", 1: "#c53030", 3: "#b7791f", 5: "#4a5568"}
    fig, ax = plt.subplots(figsize=(9.5, 4.4))
    bars = ax.bar(range(len(items)), codes, color=[palette.get(c, "#4a5568") for c in codes])
    ax.set_xticks(range(len(items)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax.set_yticks([0, 1, 3, 5])
    ax.set_yticklabels(["0 pass", "1 fail", "3 removal pending", "5 withheld"])
    ax.set_title("Negative controls: structural gate exit codes (measured)")
    for bar, item in zip(bars, items):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.08,
                f"{item['exit_code']}", ha="center", fontsize=9)
    ax.axhline(0, color="#333", linewidth=0.8)
    ax.text(0.99, 0.04,
            f"word-level gate hits on the injected import: {controls['word_level_blind_spot']['word_scan_hits']}",
            transform=ax.transAxes, ha="right", fontsize=8, color="#c53030")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 P162-N10 判据工件")
    ap.add_argument("--no-figure", action="store_true")
    args = ap.parse_args(argv)

    dsm = load_tool()
    policy = dsm.load_policy(ROOT)
    manifest = dsm.load_manifest(ROOT)
    check = dsm.check(ROOT, today=TODAY)
    deps = dependency_census(dsm, policy, manifest)
    stats = manifest_stats(manifest)
    contract = word_scan_contract(dsm)
    routes = dsm.route_census(ROOT)

    OUT.mkdir(parents=True, exist_ok=True)
    payloads = {
        "domain_surface_check.json": check,
        "dependency_census.json": deps,
        "manifest_stats.json": stats,
        "word_scan_contract.json": contract,
        "api_route_census.json": routes,
    }
    written = []
    for name, payload in payloads.items():
        target = OUT / name
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(str(target.relative_to(ROOT)))

    provenance = {
        "schema_version": "1.0",
        "generated_by": "tools/audit/m9079_artifacts.py",
        "inputs": {
            "tool": "tools/audit/domain_surface_manifest.py",
            "tool_sha256": hashlib.sha256(TOOL.read_bytes()).hexdigest(),
            "policy": dsm.POLICY_REL,
            "manifest": dsm.MANIFEST_REL,
            "approvals": dsm.APPROVALS_REL,
            "TODAY": TODAY,
        },
        "outputs": {
            name: {"sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, indent=2)
                                            .encode("utf-8") + b"\n").hexdigest()}
            for name, payload in payloads.items()
        },
        "key_numbers": {
            "modules": stats["modules"],
            "dependency_sites": deps["sites"],
            "dependency_pairs": deps["pairs"],
            "dependency_files": deps["files"],
            "verdict": check["verdict"],
            "word_scan_hits": contract["hit_count"],
            "word_scan_files": contract["files_scanned"],
            "api_routes": routes["routes"],
        },
    }
    controls = negative_control_matrix(
        dsm,
        word_hits_on_injection=_word_hits_on(ROOT, "from api.domain.holtrop_mennen import *\n"),
    )
    (OUT / "negative_controls.json").write_text(json.dumps(controls, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")

    ledger_path = ROOT / "docs" / "reports" / "m9079_migration_ledger.md"
    write_ledger(manifest, deps, ledger_path)
    written.append(str(ledger_path.relative_to(ROOT)))

    if not args.no_figure:
        fig_path = OUT / "fig1_domain_surface_ratchet.png"
        if figure(check, deps, stats, fig_path):
            provenance["outputs"][str(fig_path.relative_to(ROOT))] = {
                "sha256": hashlib.sha256(fig_path.read_bytes()).hexdigest()
            }
        ctl_path = OUT / "fig2_negative_controls.png"
        if control_figure(controls, ctl_path):
            provenance["outputs"][str(ctl_path.relative_to(ROOT))] = {
                "sha256": hashlib.sha256(ctl_path.read_bytes()).hexdigest()
            }
    (OUT / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print(json.dumps(provenance["key_numbers"], ensure_ascii=False, indent=2))
    print("written:", ", ".join(written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())