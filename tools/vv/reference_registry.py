"""跨仓实验参考数据登记册 + 闸门评估器（m1863）。

背景
----
生态各仓（TensorLBM/SPH/DG/FEM/BEM）里散布着数字化实验参考表——例如
``TensorLBM/benchmarks/pending/dam_break_sc/result.json`` 的
``reference_digitisation_lethe``（Martin & Moyce 1952 的 14 点数字化表）。
这些表是各仓唯一能支撑"物理正确"结论的基准，但：

* 格式各写各的（``MM_T/MM_X``、``T/Z``、``reference_digitisation_*``…），
  没有统一 schema，跨仓无法互相引用；
* 出处（文献 DOI / 数字化来源 / 原始文件 sha256 / git commit）要么散落在
  报告文字里，要么根本没有——一旦基准本身被改坏，没人能发现；
* 判定口径各仓自造：同一张表在一个仓算 rel_err、另一个仓算 RMSE，误差正负号与分母
  都不一致，跨仓数字不可比。

本模块把参考数据做成**可校验的一等对象**，并把判定口径固定为平台正典
（``backend/services/benchmark_integrity.py`` 的 PASS(0) < WITHHELD(1) <
WARN(2) < FAIL(3)，行级取最差）：

1. ``validate_dataset`` —— 数据集自检：出处完整性、点序单调、有限非负、
   不确定度声明、容差声明、``y_scale``（零值附近的正则化尺度）。
   缺出处 ⇒ WITHHELD（"有数无据"的参考表**不得**当判据，与正典一致）。
2. ``gate`` —— 仿真曲线 vs 参考表闸门：参考点插值、逐点相对误差、
   覆盖率、最差项，输出 PASS/WITHHELD/WARN/FAIL。
3. ``checkpoint_errors`` —— 在指定自变量刻度上独立重算误差（用于**复核已合并
   结论里写死的误差数字**，分母一律取参考值）。
4. ``harvest`` —— 扫描各仓，把散落的数字化表抽成登记册条目，并钉住
   来源仓库/commit/文件 sha256。

用法
----
    # 扫描生态仓，产出登记册条目 + 台账
    python3 tools/vv/reference_registry.py --harvest \
        --roots /root/tensor-ecosystem --registry references/registry \
        --ledger artifacts/reference_registry/ledger.json

    # 单次闸门评估
    python3 tools/vv/reference_registry.py \
        --gate martin_moyce_1952_dambreak_front \
        --registry references/registry \
        --sim references/sim/tensorlbm_dam_break_sc_a40.csv \
        --x-col T_MM --y-col X_toe

判据先于结论：本模块的任何 PASS 都要求"参考表自带出处 + 覆盖率达标 +
逐点误差 ≤ 容差"，三者缺一即降级为 WITHHELD/WARN，不用"看起来差不多"代替。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_ID = "experimental-reference/v1"

PASS, WITHHELD, WARN, FAIL = "PASS", "WITHHELD", "WARN", "FAIL"
_RANK = {PASS: 0, WITHHELD: 1, WARN: 2, FAIL: 3}

#: 平台精度标准：对标实验/参考值误差 >15% 不可接受（owner 常设口径）。
DEFAULT_GATE_TOL = 0.15
#: 精度判决**不设 WARN 带**：超过容差即 FAIL。WARN 只用于数据集自检的
#: 非阻断瑕疵（点少、无不确定度、负值），不得用来把超差结果降级成"警告"。
#: 参考刻度上至少这个比例的点必须落在仿真曲线覆盖范围内，否则不下 PASS/FAIL。
DEFAULT_MIN_COVERAGE = 0.6
#: 逐点比较的最小点数；少于此数一律 WITHHELD。
MIN_POINTS = 5

REQUIRED_PROVENANCE = ("citation", "digitised_by", "retrieved_from")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def worst(statuses: Iterable[str]) -> str:
    """取最差状态（正典 rank：PASS < WITHHELD < WARN < FAIL）。"""
    out = PASS
    for s in statuses:
        if _RANK[s] > _RANK[out]:
            out = s
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head(repo: Path) -> Optional[str]:
    """仓库 HEAD sha；非 git 目录返回 None（不假装有出处）。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - 环境相关
        return None
    sha = out.stdout.strip()
    return sha or None


def find_repo_root(path: Path) -> Optional[Path]:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _check(name: str, status: str, detail: str, **data: Any) -> Dict[str, Any]:
    return {"check": name, "status": status, "detail": detail, **data}


# --------------------------------------------------------------------------- #
# 数据集校验
# --------------------------------------------------------------------------- #
def _points(ds: Dict[str, Any]) -> List[Tuple[float, float, Optional[float]]]:
    pts = []
    for row in ds.get("points") or []:
        x, y = float(row[0]), float(row[1])
        s = None if len(row) < 3 or row[2] is None else float(row[2])
        pts.append((x, y, s))
    return pts


def validate_dataset(ds: Dict[str, Any]) -> Dict[str, Any]:
    """数据集自检。返回 {dataset_id, checks[], verdict, usable_as_gate}。

    ``usable_as_gate`` 为 False 表示这张表**不得**用于 PASS/FAIL 判决
    （缺出处或缺正则化尺度），只能作为 WITHHELD 记录留档。
    """
    checks: List[Dict[str, Any]] = []
    ds_id = str(ds.get("dataset_id") or "<missing>")

    checks.append(
        _check("schema", PASS if ds.get("schema") == SCHEMA_ID else FAIL,
               f"schema={ds.get('schema')!r}，期望 {SCHEMA_ID!r}")
    )

    prov = ds.get("provenance") or {}
    missing = [k for k in REQUIRED_PROVENANCE if not str(prov.get(k) or "").strip()]
    has_locator = bool(str(prov.get("doi") or "").strip() or str(prov.get("url") or "").strip())
    if missing or not has_locator:
        checks.append(_check(
            "provenance", WITHHELD,
            "缺出处字段："
            + ", ".join(missing + ([] if has_locator else ["doi|url"]))
            + "；无出处的参考表不得充当判据",
            missing=missing, has_locator=has_locator))
    else:
        checks.append(_check("provenance", PASS,
                             f"{prov.get('citation')}；来源 {prov.get('retrieved_from')}",
                             doi=prov.get("doi"), commit=prov.get("retrieved_repo_commit"),
                             sha256=prov.get("retrieved_sha256")))

    pts = _points(ds)
    if len(pts) < MIN_POINTS:
        checks.append(_check("point_count", WARN,
                             f"仅 {len(pts)} 点（< {MIN_POINTS}），拟合噪声会主导结论"))
    else:
        checks.append(_check("point_count", PASS, f"{len(pts)} 点"))

    finite = all(math.isfinite(x) and math.isfinite(y) for x, y, _ in pts)
    checks.append(_check("finite", PASS if finite else FAIL,
                         "全部有限" if finite else "存在 NaN/Inf"))

    mono = all(pts[i][0] < pts[i + 1][0] for i in range(len(pts) - 1)) if len(pts) > 1 else False
    checks.append(_check("x_strictly_increasing", PASS if mono else FAIL,
                         "自变量严格递增" if mono else "自变量非严格递增（重复或逆序）"))

    nonneg = all(y >= 0.0 for _, y, _ in pts)
    checks.append(_check("y_nonnegative", PASS if nonneg else WARN,
                         "因变量全非负" if nonneg else "存在负值：确认符号约定后再用"))

    unc = [s for _, _, s in pts]
    declared = all(s is not None and s >= 0.0 for s in unc)
    if not declared:
        checks.append(_check("uncertainty", WARN,
                             "有参考点未声明数字化不确定度：误差带无法与不确定度区分"))
    else:
        checks.append(_check("uncertainty", PASS,
                             f"逐点不确定度已声明，max={max(unc):.4g}"))

    tol = (ds.get("tolerance") or {}).get("gate")
    tol_ok = isinstance(tol, (int, float)) and 0.0 < float(tol) <= 0.5
    checks.append(_check("tolerance", PASS if tol_ok else WARN,
                         f"gate 容差={tol}" + ("" if tol_ok else "（缺失或超 (0,0.5]，按默认 15% 处理）")))

    ys = [y for _, y, _ in pts]
    y_max = max(ys) if ys else 0.0
    y_min_abs = min(abs(y) for y in ys) if ys else 0.0
    y_scale = (ds.get("y_scale") or {}).get("value") if isinstance(ds.get("y_scale"), dict) else ds.get("y_scale")
    near_zero = y_max > 0 and y_min_abs <= 0.05 * y_max
    if near_zero and not y_scale:
        checks.append(_check("y_scale", WITHHELD,
                             f"参考值含近零点（min|y|={y_min_abs:.4g}），未声明 y_scale，"
                             "相对误差在零点附近无意义"))
    else:
        checks.append(_check("y_scale", PASS,
                             f"y_scale={y_scale}" if y_scale else "参考值远离零点，无需 y_scale"))

    verdict = worst(c["status"] for c in checks)
    unusable = [c["check"] for c in checks
                if c["status"] in (WITHHELD, FAIL) and c["check"] in ("provenance", "y_scale", "schema", "x_strictly_increasing", "finite")]
    return {
        "dataset_id": ds_id,
        "title": ds.get("title"),
        "verdict": verdict,
        "usable_as_gate": not unusable,
        "blocking": unusable,
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# 插值 / 闸门
# --------------------------------------------------------------------------- #
def interp(xs: Sequence[float], ys: Sequence[float], x: float) -> Optional[float]:
    """线性插值；超出定义域返回 None（不外推——外推出来的"误差"不是误差）。"""
    if len(xs) < 2 or x < xs[0] or x > xs[-1]:
        return None
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    if span == 0:
        return ys[lo]
    w = (x - xs[lo]) / span
    return ys[lo] + w * (ys[hi] - ys[lo])


def _denominator(ref: float, y_floor: float) -> float:
    """相对误差的分母：|ref| 或（近零点）正则化尺度。"""
    return abs(ref) if abs(ref) >= y_floor else y_floor


def gate(
    dataset: Dict[str, Any],
    sim: Sequence[Tuple[float, float]],
    tol: Optional[float] = None,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
) -> Dict[str, Any]:
    """仿真曲线 vs 参考表。误差定义：|sim - ref| / max(|ref|, y_floor)。"""
    validation = validate_dataset(dataset)
    ds_tol = (dataset.get("tolerance") or {}).get("gate")
    gate_tol = float(tol if tol is not None else (ds_tol or DEFAULT_GATE_TOL))

    pts = _points(dataset)
    ref_x = [x for x, _, _ in pts]
    ref_y = [y for _, y, _ in pts]
    ys = [abs(y) for y in ref_y]
    y_max = max(ys) if ys else 0.0
    y_scale_raw = dataset.get("y_scale")
    y_scale = (y_scale_raw.get("value") if isinstance(y_scale_raw, dict) else y_scale_raw) or y_max
    y_floor = 0.05 * float(y_scale) if y_scale else 0.0

    sim_sorted = sorted((float(a), float(b)) for a, b in sim)
    sim_x = [a for a, _ in sim_sorted]
    sim_y = [b for _, b in sim_sorted]

    rows: List[Dict[str, Any]] = []
    for x, y, _s in pts:
        v = interp(sim_x, sim_y, x) if sim_x else None
        if v is None:
            rows.append({"x": x, "ref": y, "sim": None, "rel_err": None, "note": "仿真未覆盖该刻度"})
            continue
        err = abs(v - y) / _denominator(y, y_floor)
        rows.append({"x": x, "ref": y, "sim": v, "rel_err": err})

    compared = [r for r in rows if r["rel_err"] is not None]
    coverage = len(compared) / len(rows) if rows else 0.0
    worst_row = max(compared, key=lambda r: r["rel_err"]) if compared else None

    if not validation["usable_as_gate"]:
        verdict = WITHHELD
        reason = f"参考表不可作判据：{validation['blocking']}"
    elif coverage < min_coverage:
        verdict = WITHHELD
        reason = f"覆盖率 {coverage:.0%} < {min_coverage:.0%}：仿真曲线未覆盖参考刻度"
    elif len(compared) < MIN_POINTS:
        verdict = WITHHELD
        reason = f"可比点数 {len(compared)} < {MIN_POINTS}"
    elif worst_row["rel_err"] <= gate_tol:
        verdict = PASS
        reason = f"最差 {worst_row['rel_err']:.2%} ≤ 容差 {gate_tol:.0%}"
    else:
        verdict = FAIL
        reason = f"最差 {worst_row['rel_err']:.2%} > 容差 {gate_tol:.0%}（平台口径：>15% 不可接受）"

    errs = [r["rel_err"] for r in compared]
    return {
        "dataset_id": dataset.get("dataset_id"),
        "dataset_verdict": validation["verdict"],
        "tolerance": gate_tol,
        "min_coverage": min_coverage,
        "coverage": coverage,
        "n_compared": len(compared),
        "worst": worst_row,
        "max_rel_err": max(errs) if errs else None,
        "rmse": (math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else None),
        "mae": (sum(errs) / len(errs) if errs else None),
        "verdict": verdict,
        "reason": reason,
        "points": rows,
    }


def checkpoint_errors(
    dataset: Dict[str, Any],
    sim: Sequence[Tuple[float, float]],
    checkpoints: Sequence[float],
) -> Dict[str, Dict[str, Any]]:
    """在指定刻度上独立重算误差（复核报告里写死的误差数字用）。

    参考值与仿真值**都**插值到该刻度，分母取插值后的参考值。
    返回 {刻度字符串: {ref, sim, err, err_pct, verdict}}。
    """
    tol = float((dataset.get("tolerance") or {}).get("gate") or DEFAULT_GATE_TOL)
    pts = _points(dataset)
    ref_x = [x for x, _, _ in pts]
    ref_y = [y for _, y, _ in pts]
    ys = [abs(y) for y in ref_y]
    y_scale_raw = dataset.get("y_scale")
    y_scale = (y_scale_raw.get("value") if isinstance(y_scale_raw, dict) else y_scale_raw) or (max(ys) if ys else 0.0)
    y_floor = 0.05 * float(y_scale) if y_scale else 0.0

    sim_sorted = sorted((float(a), float(b)) for a, b in sim)
    sim_x = [a for a, _ in sim_sorted]
    sim_y = [b for _, b in sim_sorted]

    out: Dict[str, Dict[str, Any]] = {}
    for cp in checkpoints:
        x = float(cp)
        ref = interp(ref_x, ref_y, x)
        simv = interp(sim_x, sim_y, x)
        key = f"T={x:g}"
        if ref is None or simv is None:
            out[key] = {"ref": ref, "sim": simv, "err": None, "err_pct": None,
                        "verdict": WITHHELD,
                        "reason": "参考值或仿真值在该刻度无定义（不外推）"}
            continue
        err = abs(simv - ref) / _denominator(ref, y_floor)
        out[key] = {
            "ref": ref, "sim": simv, "err": err, "err_pct": err * 100.0,
            # 有符号误差保留，便于与参考文献的 +/− 号对齐
            "signed_err_pct": (simv - ref) / _denominator(ref, y_floor) * 100.0,
            "verdict": PASS if err <= tol else FAIL,
        }
    return out


# --------------------------------------------------------------------------- #
# 登记册读写 / 采集
# --------------------------------------------------------------------------- #
def load_registry(registry_dir: Path) -> Dict[str, Dict[str, Any]]:
    """读取登记册目录下所有 ``*.json``，返回 {dataset_id: dataset}。"""
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(Path(registry_dir).glob("*.json")):
        ds = json.loads(path.read_text(encoding="utf-8"))
        out[ds.get("dataset_id") or path.stem] = ds
    return out


def load_sim_csv(path: Path, x_col: str, y_col: str) -> List[Tuple[float, float]]:
    rows: List[Tuple[float, float]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                rows.append((float(row[x_col]), float(row[y_col])))
            except (KeyError, TypeError, ValueError):
                continue
    return rows


_DIGIT_KEYS = (
    ("T", "Z"), ("t", "z"), ("T", "X"), ("t", "x"),
    ("MM_T", "MM_X"), ("MM_T", "MM_Z"), ("tau", "X"),
)


def _extract_table(obj: Any) -> Optional[Tuple[List[float], List[float], str]]:
    """在任意 JSON 子树里找"等长双列数字化表"，返回 (x, y, 命中的键名)。"""
    if isinstance(obj, dict):
        for kx, ky in _DIGIT_KEYS:
            xs, ys = obj.get(kx), obj.get(ky)
            if isinstance(xs, list) and isinstance(ys, list) and len(xs) == len(ys) >= MIN_POINTS:
                try:
                    return ([float(v) for v in xs], [float(v) for v in ys], f"{kx}/{ky}")
                except (TypeError, ValueError):
                    continue
        for v in obj.values():
            hit = _extract_table(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _extract_table(v)
            if hit:
                return hit
    return None


def harvest(roots: Sequence[Path], citation_hint: str = "") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """扫描各仓 JSON，把散落的数字化参考表抽成登记册条目。

    返回 (datasets, rejects)。rejects 里是"有表无出处"的候选——按正典不得当判据，
    但留档以便人工补出处，不静默丢弃。
    """
    datasets: List[Dict[str, Any]] = []
    rejects: List[Dict[str, Any]] = []
    seen: set = set()
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            if any(part in {".git", "node_modules", "__pycache__"} for part in path.parts):
                continue
            try:
                if path.stat().st_size > 8 << 20:
                    continue
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            hit = _extract_table(doc)
            if not hit:
                continue
            xs, ys, keys = hit
            key = (tuple(xs), tuple(ys))
            if key in seen:
                continue
            seen.add(key)
            repo = find_repo_root(path)
            rel = str(path.relative_to(repo)) if repo else path.name
            cite = ""
            prov: Dict[str, Any] = {}
            if isinstance(doc, dict):
                cite = str(doc.get("reference") or doc.get("citation") or "")
                nd = doc.get("reference_digitisation_lethe") or doc.get("digitisation") or {}
                if isinstance(nd, dict):
                    cite = cite or str(nd.get("source") or "")
                prov = doc.get("provenance") if isinstance(doc.get("provenance"), dict) else {}
            doi = ""
            for token in cite.replace("(", " ").replace(")", " ").split():
                if token.startswith("doi:") or token.startswith("10."):
                    doi = token[4:] if token.startswith("doi:") else token
                    break
            entry = {
                "schema": SCHEMA_ID,
                "dataset_id": f"{repo.name.lower() if repo else 'unknown'}_{rel.replace('/', '_').replace('.json', '')}_{keys.replace('/', '_')}",
                "title": (citation_hint or cite or f"digitised reference table ({keys})"),
                "quantity": f"digitised table, columns {keys}",
                "points": [[x, y, None] for x, y in zip(xs, ys)],
                "y_scale": max(abs(y) for y in ys),
                "tolerance": {"gate": DEFAULT_GATE_TOL},
                "provenance": {
                    "citation": cite,
                    "doi": doi,
                    "digitised_by": prov.get("digitised_by", ""),
                    "retrieved_from": f"{repo.name if repo else '?'}/{rel}#{keys}",
                    "retrieved_repo_commit": git_head(repo) if repo else None,
                    "retrieved_sha256": sha256_file(path),
                    "license_note": "数字化表按原文献引用；再分发前核对期刊条款",
                },
            }
            v = validate_dataset(entry)
            if v["usable_as_gate"]:
                datasets.append(entry)
            else:
                rejects.append({**entry, "validation": v})
    return datasets, rejects


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="跨仓实验参考数据登记册 + 闸门评估器")
    ap.add_argument("--harvest", action="store_true", help="扫描 roots 采集数字化参考表")
    ap.add_argument("--roots", default="/root/tensor-ecosystem")
    ap.add_argument("--registry", default="references/registry")
    ap.add_argument("--ledger", default="artifacts/reference_registry/ledger.json")
    ap.add_argument("--validate", action="store_true", help="只校验登记册")
    ap.add_argument("--gate", metavar="DATASET_ID")
    ap.add_argument("--sim", help="仿真曲线 CSV")
    ap.add_argument("--x-col", default="T_MM")
    ap.add_argument("--y-col", default="X_toe")
    ap.add_argument("--tol", type=float, default=None)
    ap.add_argument("--checkpoints", default="", help="逗号分隔，独立重算这些刻度上的误差")
    args = ap.parse_args(argv)

    reg_dir = Path(args.registry)
    ledger: Dict[str, Any] = {"schema": SCHEMA_ID, "registry_dir": str(reg_dir)}

    if args.harvest:
        datasets, rejects = harvest([Path(p) for p in args.roots.split(",")])
        reg_dir.mkdir(parents=True, exist_ok=True)
        for ds in datasets:
            (reg_dir / f"{ds['dataset_id']}.json").write_text(
                json.dumps(ds, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        ledger["harvested"] = [{"dataset_id": d["dataset_id"],
                                "provenance": d["provenance"]} for d in datasets]
        ledger["rejected"] = [{"dataset_id": d["dataset_id"], "blocking": d["validation"]["blocking"]}
                              for d in rejects]
        print(f"[harvest] 可用 {len(datasets)} 张，无出处拒收 {len(rejects)} 张")

    registry = load_registry(reg_dir) if reg_dir.exists() else {}
    if args.validate or not args.gate:
        ledger["datasets"] = {k: validate_dataset(v) for k, v in registry.items()}

    if args.gate:
        if args.gate not in registry:
            print(f"[error] 登记册里没有 {args.gate}；现有：{sorted(registry)}", file=sys.stderr)
            return 2
        ds = registry[args.gate]
        if not args.sim:
            print("[error] --gate 需要 --sim", file=sys.stderr)
            return 2
        sim = load_sim_csv(Path(args.sim), args.x_col, args.y_col)
        result = gate(ds, sim, tol=args.tol)
        result["sim"] = {"path": args.sim, "x_col": args.x_col, "y_col": args.y_col,
                         "n": len(sim)}
        if args.checkpoints:
            cps = [float(s) for s in args.checkpoints.split(",") if s.strip()]
            result["checkpoints"] = checkpoint_errors(ds, sim, cps)
        ledger["gate"] = result
        print(f"[gate] {args.gate}: {result['verdict']} — {result['reason']}")

    if args.ledger:
        out = Path(args.ledger)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[ledger] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())