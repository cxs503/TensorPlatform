#!/usr/bin/env python3
"""run_case —— 战役案例一键复现与**事后独立复核**（域无关机制层）。

为什么需要它
------------
`collect_evidence → campaign_distill → make_figures` 此前只能手工串（README 里是三段
命令），于是有两类真问题：

1. **没有单一失败语义**：某一步失败（归档校验不过、缺 matplotlib、产物缺失）只能靠
   人看日志；下游节点无法机器判定"这个案例跑通了没有"。
2. **归档事后不可复核**：`campaign_distill.verify_manifest` 与归档在同一进程里跑，
   且只遍历 manifest 自己列出的条目——归档目录里**多出来的文件**、`SHA256SUMS` 与
   `manifest.json` 互相不一致、manifest 条目被改过（而文件没动），全都检不出来。
   报告里写"`SHA256SUMS` 可 `sha256sum -c` 复核"，但平台里没有一个工具真能对已落盘
   的归档做独立复核（且 `sha256sum -c` 不检测多余文件、不检测 sums↔manifest 对账）。

本工具把"跑一个案例"变成一个入口、一份记录：

* `distill`        → 调 ``campaign_distill.main``：账本 + 报告 + 归档
* `figures`        → 调 ``make_figures.main``：三张核验图
* `verify_archive` → **独立复核**：只信 `SHA256SUMS` + 磁盘，逐条复算 sha256，并与
  `manifest.json` 双向对账（缺文件 / 多余文件 / 篡改 / sums↔manifest 不一致 /
  manifest 条目摘要被改）；**不读** manifest 里的 `verify_status` 结论
* `determinism`    → 同一 bundle 再跑一次 distill，逐字节比对账本与报告

产出 ``case_run.json``：逐阶段 ok / 每个输出文件的 sha256，可被 CI 或下游节点直接引用。

确定性（有边界，边界写清楚）
--------------------------
记录里**不写墙钟、不写绝对路径**（输出路径一律相对 workdir），`snapshot_utc` 取自 bundle。

* 与 workdir 无关、跨环境**逐字节相同**：`coverage_matrix.json` / `gaps.json` /
  `knowledge_graph.json` / `report.md` 的正文数字 / `SHA256SUMS` / 三张核验图。
* 现状内嵌绝对路径、**跨 workdir 会变**的三件：`distill_summary.json`（acceptance 里
  的工件路径）、`report.md`（第 5 节打印归档路径）、`manifest.json`（source_path /
  archive_dir）。记录里 `path_embedding_outputs` 明确列出这三件——CI 做跨机比对时跳过，
  而不是让"看起来全都不同"把真漂移盖住（`determinism` 阶段比对时对 md 做路径规范化）。

用法
----
    python3 tools/distill/run_case.py --case cases/<case>/case.json
    python3 tools/distill/run_case.py --case cases/<case>/case.json --workdir runs/<case>
    python3 tools/distill/run_case.py --verify-only verified/<case>      # 只复核已有归档

退出码：``0`` 全部阶段通过 ｜ ``1`` 有阶段失败（问题清单落 `case_run.json`）｜
``2`` 案例契约不满足（case/bundle 缺失、字段缺项）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import campaign_distill as cd  # noqa: E402  （同目录机制层，仅用其哈希/规范化 JSON 原语）

STAGE_ORDER = ("distill", "figures", "verify_archive", "determinism")

# 归档目录里由归档器自身生成、因而不进 SHA256SUMS 的两个文件
ARCHIVE_META = ("SHA256SUMS", "manifest.json")

# 第一遍 distill 与复跑 distill 之间应逐字节相同的产物（distill_summary.json 除外：
# 它内嵌 --out/--report 路径，两次运行路径本就不同，比它等于比路径）
DETERMINISM_TARGETS = (
    "out/coverage_matrix.json",
    "out/gaps.json",
    "out/knowledge_graph.json",
    "report.md",
)


class CaseContractError(ValueError):
    """案例契约不满足（不是运行失败，是输入不合格）。"""


# --------------------------------------------------------------------------- #
# 原语
# --------------------------------------------------------------------------- #
def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path | str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )


def parse_sums(sums_path: Path) -> tuple[dict, list[dict]]:
    """解析 `sha256  rel_path` 清单；返回 ({rel_path: sha256}, problems)。"""
    sums: dict[str, str] = {}
    problems: list[dict] = []
    for lineno, raw in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            problems.append({"rule": "sums_malformed", "target": raw, "line": lineno})
            continue
        digest, rel = parts[0], parts[1].strip()
        if rel in sums and sums[rel] != digest:
            problems.append({"rule": "sums_duplicate_conflict", "target": rel})
            continue
        sums[rel] = digest
    return sums, problems


# --------------------------------------------------------------------------- #
# 独立归档复核（本工具的核心增量：不信任归档期结论）
# --------------------------------------------------------------------------- #
def verify_archive(archive_dir: Path | str) -> dict:
    """对已落盘归档做独立复核。

    只依赖 ``SHA256SUMS`` + ``manifest.json`` + 磁盘内容，四类问题全部报出：

    * ``file_missing``   清单里有、磁盘没有
    * ``sha256_mismatch`` 内容与清单不符（篡改/截断）
    * ``unlisted_file``  磁盘有、清单没有（**`sha256sum -c` 检不出这类**）
    * ``manifest_*``     `manifest.json` 与 `SHA256SUMS` 不一致 / 条目摘要被改
    """
    d = Path(archive_dir)
    problems: list[dict] = []
    if not d.is_dir():
        return {"ok": False, "checked": 0, "problems": [{"rule": "archive_missing", "target": str(d)}]}

    sums_path, man_path = d / "SHA256SUMS", d / "manifest.json"
    if not sums_path.exists():
        problems.append({"rule": "sums_missing", "target": "SHA256SUMS"})
    if not man_path.exists():
        problems.append({"rule": "manifest_missing", "target": "manifest.json"})

    sums, sums_problems = parse_sums(sums_path) if sums_path.exists() else ({}, [])
    problems.extend(sums_problems)

    checked = 0
    for rel in sorted(sums):
        f = d / rel
        if not f.is_file():
            problems.append({"rule": "file_missing", "target": rel})
            continue
        checked += 1
        actual = sha256_file(f)
        if actual != sums[rel]:
            problems.append(
                {"rule": "sha256_mismatch", "target": rel, "expected": sums[rel], "actual": actual}
            )

    on_disk = sorted(
        p.relative_to(d).as_posix()
        for p in d.rglob("*")
        if p.is_file() and p.relative_to(d).as_posix() not in ARCHIVE_META
    )
    for rel in on_disk:
        if rel not in sums:
            problems.append({"rule": "unlisted_file", "target": rel})

    if man_path.exists():
        manifest = load_json(man_path)
        entries = manifest.get("entries") or []
        listed = {str(e.get("rel_path")): str(e.get("sha256")) for e in entries}
        for rel in sorted(set(listed) - set(sums)):
            problems.append({"rule": "manifest_entry_missing_from_sums", "target": rel})
        for rel in sorted(set(sums) - set(listed)):
            problems.append({"rule": "sums_entry_missing_from_manifest", "target": rel})
        for rel in sorted(set(sums) & set(listed)):
            if sums[rel] != listed[rel]:
                problems.append(
                    {"rule": "sums_manifest_disagree", "target": rel, "sums": sums[rel], "manifest": listed[rel]}
                )
        if len(entries) != len(sums):
            problems.append(
                {"rule": "entry_count_mismatch", "target": "manifest.json",
                 "manifest": len(entries), "sums": len(sums)}
            )
        recomputed = hashlib.sha256(cd.canonical_json(entries).encode()).hexdigest()
        declared = manifest.get("entries_sha256")
        if declared and declared != recomputed:
            problems.append(
                {"rule": "manifest_digest_mismatch", "target": "manifest.json",
                 "declared": declared, "actual": recomputed}
            )

    return {
        "ok": not problems,
        "checked": checked,
        "entries": len(sums),
        "files_on_disk": len(on_disk),
        "problems": problems,
    }


# --------------------------------------------------------------------------- #
# 案例契约
# --------------------------------------------------------------------------- #
def resolve_ref(base: Path, value):
    """case 里的引用既可以是路径字符串，也可以是内联对象（内联即数据，落盘后审计）。"""
    if value is None:
        return None
    if isinstance(value, (str, Path)):
        p = Path(value)
        return p if p.is_absolute() else (base / p)
    return value


def load_case(case_path: Path | str, workdir_override: str | None = None) -> dict:
    path = Path(case_path)
    if not path.is_file():
        raise CaseContractError(f"case 文件不存在: {path}")
    base = path.resolve().parent
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise CaseContractError("case 必须是 JSON 对象")
    if not raw.get("bundle"):
        raise CaseContractError("case 缺少必填字段 bundle")

    bundle = resolve_ref(base, raw["bundle"])
    if not Path(bundle).is_file():
        raise CaseContractError(f"bundle 不存在: {bundle}")

    workdir = Path(workdir_override) if workdir_override else Path(raw.get("workdir") or "out")
    if not workdir.is_absolute():
        workdir = (base / workdir) if not workdir_override else Path.cwd() / workdir

    stages = raw.get("stages") or list(STAGE_ORDER)
    unknown = [s for s in stages if s not in STAGE_ORDER]
    if unknown:
        raise CaseContractError(f"未知阶段: {unknown}")

    return {
        "id": str(raw.get("id") or path.stem),
        "bundle": Path(bundle),
        "spec": resolve_ref(base, raw.get("spec")),
        "narrative": resolve_ref(base, raw.get("narrative")),
        "extra": [str((base / e) if not Path(e).is_absolute() else e) for e in raw.get("extra") or []],
        "workdir": workdir,
        "archive_subdir": str(raw.get("archive_subdir") or "verified"),
        "report_name": str(raw.get("report") or "report.md"),
        "figures_subdir": str(raw.get("figures") or "figures"),
        "stages": list(stages),
        "clean": bool(raw.get("clean", False)),
        "force": bool(raw.get("force", False)),
    }


def materialize_inputs(case: dict) -> dict:
    """把内联 spec/narrative 落成 workdir/inputs/*.json（可审计），路径引用原样返回。"""
    wd = case["workdir"]
    out = {}
    for key in ("spec", "narrative"):
        ref = case[key]
        if ref is None:
            out[key] = None
        elif isinstance(ref, dict):
            target = wd / "inputs" / f"{key}.json"
            dump_json(target, ref)
            out[key] = target
        else:
            if not Path(ref).is_file():
                raise CaseContractError(f"{key} 文件不存在: {ref}")
            out[key] = Path(ref)
    return out


def stale_outputs(case: dict) -> list[str]:
    """workdir 里上一次运行留下的、会被本次复用的产物目录。"""
    wd = case["workdir"]
    cand = [wd / "out", wd / case["archive_subdir"], wd / "_repeat",
            wd / case["figures_subdir"], wd / case["report_name"], wd / "case_run.json"]
    return [str(p) for p in cand if p.exists()]


def clean_outputs(case: dict) -> list[str]:
    """只清 workdir 内**已知**产物路径（不碰 workdir 之外、不做通配删除）。"""
    removed = []
    wd = case["workdir"]
    for p in (wd / "out", wd / case["archive_subdir"], wd / "_repeat", wd / case["figures_subdir"]):
        if p.is_dir():
            shutil.rmtree(p)
            removed.append(str(p))
        elif p.exists():
            p.unlink()
            removed.append(str(p))
    for p in (wd / case["report_name"], wd / "case_run.json"):
        if p.is_file():
            p.unlink()
            removed.append(str(p))
    return removed


def normalize_bundle(case: dict) -> tuple[Path, list[dict]]:
    """把 bundle 里的证据文件路径解析成绝对路径，落成 workdir/inputs/bundle.json。

    为什么必须做：``campaign_distill`` 归档时对不存在的证据文件是
    ``if p.exists()`` **静默跳过**——于是"证据文件路径写错/丢失"的案例照样能归档成功、
    复核通过，只是归档里少了几件工件（假绿）。这里把相对路径按 bundle 所在目录解析，
    解析不到的**逐条报出**（``evidence_unresolvable``），并且让 distill 吃规范化后的
    bundle，保证归档的就是 bundle 声明的那些件。
    """
    wd = case["workdir"]
    raw = load_json(case["bundle"])
    base = Path(case["bundle"]).resolve().parent
    problems: list[dict] = []
    for node in raw.get("nodes") or []:
        key = node.get("node_key")
        for f in (node.get("evidence") or {}).get("files") or []:
            p = Path(str(f.get("path") or ""))
            if not p.is_absolute():
                p = base / p
            if not p.is_file():
                problems.append({"rule": "evidence_unresolvable", "node": key, "target": str(f.get("path"))})
                continue
            f["path"] = str(p)
    target = wd / "inputs" / "bundle.json"
    dump_json(target, raw)
    return target, problems


# --------------------------------------------------------------------------- #
# 阶段
# --------------------------------------------------------------------------- #
def _rel(workdir: Path, p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(workdir.resolve()).as_posix()
    except ValueError:
        return str(p)


def _collect_outputs(workdir: Path, paths) -> list[dict]:
    out = []
    for p in paths:
        p = Path(p)
        if not p.is_file():
            continue
        out.append({"path": _rel(workdir, p), "sha256": sha256_file(p), "bytes": p.stat().st_size})
    return out


def _stage(name: str) -> dict:
    return {"name": name, "ok": False, "problems": [], "outputs": [], "detail": {}}


def stage_distill(case: dict, inputs: dict, bundle_path: Path, ev_problems: list[dict]) -> dict:
    rec = _stage("distill")
    rec["problems"].extend(ev_problems)
    wd = case["workdir"]
    out_dir = wd / "out"
    archive = wd / case["archive_subdir"]
    report = wd / case["report_name"]
    argv = ["--bundle", str(bundle_path), "--out", str(out_dir),
            "--archive", str(archive), "--report", str(report)]
    if inputs.get("spec"):
        argv += ["--spec", str(inputs["spec"])]
    if inputs.get("narrative"):
        argv += ["--narrative", str(inputs["narrative"])]
    for extra in case["extra"]:
        argv += ["--extra", extra]
    try:
        code = cd.main(argv)
    except Exception as exc:  # 归档冲突 / 工件缺失 / 数据坏 → 阶段失败，不掩盖
        rec["problems"].append({"rule": "stage_exception", "error": f"{type(exc).__name__}: {exc}"})
        rec["detail"]["exit_code"] = None
        return rec

    rec["detail"]["exit_code"] = code
    if code != 0:
        rec["problems"].append({"rule": "distill_exit", "exit_code": code})
        summary_path = out_dir / "distill_summary.json"
        if summary_path.is_file():
            for vio in load_json(summary_path).get("violations") or []:
                rec["problems"].append({"rule": "distill_violation", **vio})
    rec["outputs"] = _collect_outputs(wd, [
        out_dir / "coverage_matrix.json",
        out_dir / "gaps.json",
        out_dir / "knowledge_graph.json",
        out_dir / "distill_summary.json",
        report,
        archive / "SHA256SUMS",
        archive / "manifest.json",
    ])
    rec["outputs"].extend(_collect_outputs(wd, [bundle_path]))
    rec["ok"] = code == 0 and not rec["problems"]
    return rec


def stage_figures(case: dict, bundle_path: Path) -> dict:
    rec = _stage("figures")
    wd = case["workdir"]
    fig_dir = wd / case["figures_subdir"]
    try:
        import make_figures as mf
    except ImportError as exc:  # matplotlib/networkx 缺失 → 明确报出，不静默跳过
        rec["problems"].append({"rule": "missing_dependency", "error": str(exc)})
        return rec
    try:
        code = mf.main(["--out-dir", str(wd / "out"), "--bundle", str(bundle_path),
                        "--fig-dir", str(fig_dir)])
    except Exception as exc:
        rec["problems"].append({"rule": "stage_exception", "error": f"{type(exc).__name__}: {exc}"})
        return rec
    rec["detail"]["exit_code"] = code
    if code != 0:
        rec["problems"].append({"rule": "figures_exit", "exit_code": code})
    pngs = sorted(fig_dir.glob("*.png")) if fig_dir.is_dir() else []
    if not pngs:
        rec["problems"].append({"rule": "no_figures", "target": str(fig_dir)})
    rec["outputs"] = _collect_outputs(wd, pngs)
    rec["detail"]["figures"] = len(pngs)
    rec["ok"] = code == 0 and not rec["problems"]
    return rec


def stage_verify_archive(case: dict) -> dict:
    rec = _stage("verify_archive")
    wd = case["workdir"]
    archive = wd / case["archive_subdir"]
    result = verify_archive(archive)
    rec["detail"] = {
        "archive": _rel(wd, archive),
        "checked": result["checked"],
        "entries": result.get("entries", 0),
        "files_on_disk": result.get("files_on_disk", 0),
    }
    rec["problems"] = result["problems"]
    rec["outputs"] = _collect_outputs(wd, [archive / "SHA256SUMS", archive / "manifest.json"])
    rec["ok"] = result["ok"]
    return rec


def canonical_text(data: bytes, roots: list[Path]) -> bytes:
    """把两遍运行各自的 workdir 绝对路径统一成占位符（环境路径不算内容差异）。"""
    text = data.decode("utf-8")
    for root in sorted((str(Path(r).resolve()) for r in roots), key=len, reverse=True):
        text = text.replace(root, "<WORK>")
    return text.encode("utf-8")


def compare_runs(
    workdir: Path, repeat_root: Path, targets=DETERMINISM_TARGETS, roots: list[Path] | None = None
) -> list[dict]:
    """比对两遍运行的指定产物；返回问题清单（空 = 确定性成立）。

    markdown 产物先做路径规范化（报告第 5 节会打印归档的绝对路径，那是环境；其余
    数字与文案必须逐字节相同）。JSON 账本直接逐字节比。
    """
    problems = []
    roots = roots or [workdir, repeat_root]
    for rel in targets:
        first, second = Path(workdir) / rel, Path(repeat_root) / rel
        if not first.is_file() or not second.is_file():
            problems.append({"rule": "repeat_missing", "target": rel,
                             "first": first.is_file(), "second": second.is_file()})
            continue
        a, b = first.read_bytes(), second.read_bytes()
        if rel.endswith(".md"):
            a, b = canonical_text(a, roots), canonical_text(b, roots)
        if a != b:
            problems.append({"rule": "nondeterministic", "target": rel,
                             "sha256_first": hashlib.sha256(a).hexdigest(),
                             "sha256_second": hashlib.sha256(b).hexdigest()})
    return problems


def stage_determinism(case: dict, inputs: dict, bundle_path: Path) -> dict:
    rec = _stage("determinism")
    wd = case["workdir"]
    repeat = wd / "_repeat"
    # 复跑要与首跑同构，否则"差异"是路径布局造成的假阳性，不是不确定性：
    # 1) bundle 复制到 _repeat/inputs/ —— 归档按源路径排序，只有相对布局相同，
    #    [E#] 编号与条目顺序才可比；
    # 2) 复跑带自己的归档目录——报告含"归档件数/manifest 校验"，不归档天然不同。
    (repeat / "inputs").mkdir(parents=True, exist_ok=True)
    repeat_bundle = repeat / "inputs" / "bundle.json"
    repeat_bundle.write_bytes(Path(bundle_path).read_bytes())
    argv = ["--bundle", str(repeat_bundle), "--out", str(repeat / "out"),
            "--archive", str(repeat / "verified"),
            "--report", str(repeat / "report.md")]
    if inputs.get("spec"):
        argv += ["--spec", str(inputs["spec"])]
    if inputs.get("narrative"):
        argv += ["--narrative", str(inputs["narrative"])]
    try:
        code = cd.main(argv)
    except Exception as exc:
        rec["problems"].append({"rule": "stage_exception", "error": f"{type(exc).__name__}: {exc}"})
        return rec
    rec["detail"]["exit_code"] = code
    if code != 0:
        rec["problems"].append({"rule": "repeat_exit", "exit_code": code})
    rec["problems"].extend(compare_runs(wd, repeat, roots=[wd, repeat]))
    rec["outputs"] = _collect_outputs(wd, [repeat / rel for rel in DETERMINISM_TARGETS])
    rec["detail"]["compared"] = list(DETERMINISM_TARGETS)
    rec["ok"] = code == 0 and not rec["problems"]
    return rec


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def run_case(case: dict, stages: list[str] | None = None) -> dict:
    stages = stages or case["stages"]
    wd: Path = case["workdir"]
    wd.mkdir(parents=True, exist_ok=True)

    # 脏 workdir 预检：上一版 bundle 的归档/账本留在盘上会被本次复用，于是本次归档会
    # 夹带陈旧工件（本轮实测：换 bundle 后残留旧件 → 独立复核报 unlisted_file）。
    # 不静默清理（删数据是重动作），也不静默复用（假绿），必须显式选一个。
    stale = stale_outputs(case)
    cleaned: list[str] = []
    if stale:
        if case["clean"]:
            cleaned = clean_outputs(case)
        elif not case["force"]:
            raise CaseContractError(
                "workdir 已有上次运行产物，拒绝复用（会导致陈旧工件混入归档）："
                + "; ".join(stale) + " —— 用 --clean 清理后重跑，或 --force 显式复用"
            )
    inputs = materialize_inputs(case)
    bundle = load_json(case["bundle"])
    bundle_path, ev_problems = normalize_bundle(case)

    records = []
    for name in STAGE_ORDER:
        if name not in stages:
            continue
        try:
            if name == "distill":
                rec = stage_distill(case, inputs, bundle_path, ev_problems)
            elif name == "figures":
                rec = stage_figures(case, bundle_path)
            elif name == "verify_archive":
                rec = stage_verify_archive(case)
            else:
                rec = stage_determinism(case, inputs, bundle_path)
        except CaseContractError:
            raise
        except Exception as exc:  # 阶段内未预期异常同样记账，不让整条链路静默中断
            rec = _stage(name)
            rec["problems"].append({"rule": "stage_exception", "error": f"{type(exc).__name__}: {exc}"})
        records.append(rec)

    return {
        "runner_version": "1.0",
        "case": {
            "id": case["id"],
            "bundle": Path(case["bundle"]).name,
            "bundle_sha256": sha256_file(case["bundle"]),
            "bundle_normalized_sha256": sha256_file(bundle_path),
            "evidence_unresolvable": len(ev_problems),
            "snapshot_utc": bundle.get("snapshot_utc"),
            "stages": [r["name"] for r in records],
        },
        "stages": records,
        "path_embedding_outputs": [
            "out/distill_summary.json", case["report_name"], f"{case['archive_subdir']}/manifest.json",
        ],
        # 与 workdir 无关、跨环境可逐字节比对的产物（图与账本不含任何环境路径）
        "content_stable_outputs": [
            "out/coverage_matrix.json", "out/gaps.json", "out/knowledge_graph.json",
            f"{case['archive_subdir']}/SHA256SUMS", "inputs/bundle.json",
        ],
        "ok": all(r["ok"] for r in records) and bool(records),
        "failed_stages": [r["name"] for r in records if not r["ok"]],
        "workdir": {"stale_before_run": len(stale), "cleaned": len(cleaned)},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="案例一键复现 + 归档独立复核（域无关机制层）")
    ap.add_argument("--case", default=None, help="case.json（域内容经工件注入）")
    ap.add_argument("--workdir", default=None, help="覆盖 case 里的 workdir")
    ap.add_argument("--stage", action="append", default=[], choices=list(STAGE_ORDER),
                    help="只跑指定阶段（可重复）")
    ap.add_argument("--verify-only", default=None, help="只复核一个已落盘归档目录")
    ap.add_argument("--clean", action="store_true", help="重跑前清掉 workdir 内已知产物")
    ap.add_argument("--force", action="store_true", help="显式复用已有 workdir（可能夹带陈旧工件）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.verify_only:
        result = verify_archive(args.verify_only)
        payload = {"archive": str(args.verify_only), **result}
        if not args.quiet:
            print(json.dumps(payload, ensure_ascii=False))
        return 0 if result["ok"] else 1

    if not args.case:
        ap.error("需要 --case 或 --verify-only")
    try:
        case = load_case(args.case, args.workdir)
        case["clean"] = case["clean"] or args.clean
        case["force"] = case["force"] or args.force
        record = run_case(case, args.stage or None)
    except CaseContractError as exc:
        print(json.dumps({"ok": False, "contract_error": str(exc)}, ensure_ascii=False))
        return 2

    record_path = case["workdir"] / "case_run.json"
    dump_json(record_path, record)
    if not args.quiet:
        print(json.dumps({
            "ok": record["ok"],
            "failed_stages": record["failed_stages"],
            "record": str(record_path),
            "stages": {r["name"]: r["ok"] for r in record["stages"]},
        }, ensure_ascii=False))
    return 0 if record["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())