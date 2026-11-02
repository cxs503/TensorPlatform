#!/usr/bin/env python3
"""Figure-data provenance gate (PG9) — sandboxed execution of a figure
generator, recording every array handed to matplotlib and adjudicating it
against the artifact tree.

Why this exists
---------------
``tools/paper_gate/traceability_gate.py`` (PG1–PG8) judges *text*: paths that
must resolve, report ids that must exist, table arithmetic, inline error
percentages. Its own docstring and ``docs/papers/PAPER_TRACEABILITY_GATE.md``
§5.2 declare the remaining hole honestly:

    图内数据合成不可静态判定（见 §2 第 5 条）；下一步应做
    ``generate_figures.py`` 沙箱执行 + 与工件数值 diff。

That hole is exactly the failure that cost two drafts (paper-draft-ice-wave-
mechanics-r2: "H3 figs 2-5 synthesised"). A generator can print a beautiful
curve, cite a real report id in the caption, pass PG1–PG8 — and never read a
single artifact. Text-level gates cannot see it, because the fabrication lives
in the *plot call*, not in the prose.

This gate closes it by execution:

1. run the generator in a child process with the Agg backend, after installing
   hooks on the matplotlib artist APIs (plot/bar/scatter/contour/imshow/...);
2. every numeric array passed to an artist is captured together with its
   call-site line in the generator source and the figure file it ends up in
   (via a ``Figure.savefig`` hook);
3. harvest a corpus of numeric series from the artifact tree
   (json / csv / npy / npz, per-json-pointer and per-csv-column);
4. adjudicate each *primary* array (the y / heights / field argument):
   RESOLVED if it matches an artifact series within (rtol, atol) — the citation
   ``path#pointer`` is emitted so the author can paste it into the manuscript;
   otherwise FAIL as synthesised data, unless the bundle carries an explicit
   declaration (``figprov.json``) that names the analytic source *and* cites it.

Verdicts (codes)
----------------
PG9_synthesised_figure_data        FAIL  primary array matches no artifact and
                                         carries no analytic declaration
PG9_declaration_missing_citation   FAIL  declaration exists but citation/expr empty
PG9_partial_provenance             WARN  primary resolved, but a secondary array
                                         (x / yerr / grid) matches nothing
PG9_figure_without_recorded_data   WARN  a figure file was written with zero
                                         recorded artists (schematic, or plotting
                                         happened in a subprocess)
PG9_generator_crash                WARN  generator raised; unverified is reported
                                         as unverified, never as PASS
PG9_hook_missed                    WARN  image files appeared in the output dir
                                         but no savefig was intercepted

Declared out of scope (honest, not silent)
------------------------------------------
* A plotted array that happens to *equal* an artifact series by construction
  (e.g. ``np.linspace`` tuned to the artifact endpoints) is resolvable-by-value,
  not resolvable-by-origin: this gate judges values, not data flow.
* Arrays of fewer than 3 points are not adjudicated (counted as skipped).
* Images produced without matplotlib (PIL, gnuplot, LaTeX) are not traced.

Usage
-----
    python3 tools/paper_gate/figure_provenance.py \
        --generator gen_figures.py --out-fig-dir figures \
        --artifact-root artifacts --artifact-root runs \
        --bundle figures --json-out /tmp/figprov.json

Exit code 0 = no FAIL findings, 1 = at least one FAIL, 2 = usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

# ─────────────────────────── hook bootstrap (child side) ───────────────────────────

BOOTSTRAP = r'''
import json, os, runpy, sys, traceback

script, record_path = sys.argv[1], sys.argv[2]
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib
matplotlib.use("Agg", force=True)
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure

RECORD = {"artists": [], "saved": [], "error": None, "version": matplotlib.__version__}

#: method -> how to pick the primary (the "data") array out of the positional args
SPEC = {
    "plot": "y2", "semilogx": "y2", "semilogy": "y2", "loglog": "y2",
    "step": "y2", "stem": "y2", "fill_between": "y2", "errorbar": "y2",
    "scatter": "y2", "bar": "y2", "barh": "y2",
    "contour": "last", "contourf": "last", "pcolormesh": "last",
    "imshow": "first", "matshow": "first",
}


def _arrays(args):
    out = []
    for a in args:
        try:
            arr = np.asarray(a, dtype=float)
        except Exception:
            out.append(None)
            continue
        out.append(arr.ravel() if arr.size else None)
    return out


def _primary_index(rule, arrays):
    idx = [i for i, a in enumerate(arrays) if a is not None and a.size >= 3]
    if not idx:
        return None
    if rule == "last" or rule == "first":
        return idx[-1] if rule == "last" else idx[0]
    # "y2": prefer the second big array (y of plot(x, y), heights of bar(x, h))
    return idx[1] if len(idx) >= 2 else idx[0]


def _callsite():
    for fr in reversed(traceback.extract_stack()[:-1]):
        if os.path.abspath(fr.filename) == os.path.abspath(script):
            return {"line": fr.lineno, "source": (fr.line or "").strip()[:200]}
    return {"line": None, "source": ""}


def _wrap(name):
    orig = getattr(Axes, name, None)
    if orig is None:
        return

    def hooked(self, *args, **kw):
        try:
            arrays = _arrays(args)
            pi = _primary_index(SPEC.get(name, "last"), arrays)
            kw_series = []
            for k, v in kw.items():
                if k in ("label", "color", "c", "facecolor", "edgecolor",
                         "markerfacecolor", "markeredgecolor"):
                    continue
                try:
                    ka = np.asarray(v, dtype=float)
                except Exception:
                    continue
                if ka.size >= 3 and np.all(np.isfinite(ka[:1])):
                    kw_series.append([k, [float(x) for x in ka.ravel()[:5000]]])
            if pi is not None:
                RECORD["artists"].append({
                    "figure": None,
                    "figid": id(self.figure),
                    "call": name,
                    "label": str(kw.get("label", ""))[:80],
                    "primary": [float(x) for x in arrays[pi][:5000]],
                    "secondaries": [[float(x) for x in a[:5000]]
                                    for i, a in enumerate(arrays)
                                    if a is not None and i != pi and a.size >= 3],
                    "keyword_series": kw_series,
                    "callsite": _callsite(),
                })
        except Exception:
            RECORD["artists"].append({"figure": None, "figid": id(self.figure),
                                      "call": name, "error": traceback.format_exc()[-500:],
                                      "primary": [], "secondaries": [],
                                      "callsite": _callsite()})
        return orig(self, *args, **kw)

    setattr(Axes, name, hooked)


for _m in SPEC:
    _wrap(_m)

_orig_savefig = Figure.savefig


def _patched_savefig(self, fname, *a, **kw):
    try:
        target = os.fspath(fname)
    except Exception:
        target = None
    if isinstance(target, str):
        target = os.path.abspath(target)
        RECORD["saved"].append(target)
        for r in RECORD["artists"]:
            if r.get("figid") == id(self) and r.get("figure") is None:
                r["figure"] = target
    return _orig_savefig(self, fname, *a, **kw)


Figure.savefig = _patched_savefig

sys.argv = [script]
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit:
    pass
except Exception:
    RECORD["error"] = traceback.format_exc()[-4000:]

with open(record_path, "w", encoding="utf-8") as fh:
    json.dump(RECORD, fh)
'''


# ─────────────────────────── artifact corpus ───────────────────────────


def _num_list(seq) -> np.ndarray | None:
    if not seq:
        return None
    for v in seq:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None
    arr = np.asarray(seq, dtype=float).ravel()
    if arr.size < 3 or not np.all(np.isfinite(arr)):
        return None
    return arr


def _walk_json(obj, path: str, out: list, depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk_json(v, f"{path}.{k}", out, depth + 1)
    elif isinstance(obj, list):
        arr = _num_list(obj)
        if arr is not None:
            out.append((path or "$", arr))
            return
        if obj and all(isinstance(v, dict) for v in obj):
            # the dominant artifact shape: [{"Re": 1e6, "Cf": 0.0048}, ...]
            keys: list[str] = []
            for v in obj:
                for k in v:
                    if k not in keys:
                        keys.append(k)
            for k in keys:
                col = [v[k] for v in obj if isinstance(v.get(k), (int, float))
                       and not isinstance(v.get(k), bool)]
                carr = _num_list(col)
                if carr is not None:
                    out.append((f"{path}[*].{k}", carr))
            for i, v in enumerate(obj):
                _walk_json(v, f"{path}[{i}]", out, depth + 1)
            return
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                _walk_json(v, f"{path}[{i}]", out, depth + 1)


def harvest_series(roots, *, max_series: int = 20000, max_values: int = 4_000_000,
                   max_files: int = 4000) -> tuple[dict, dict]:
    """Harvest numeric series from json/csv/npy/npz under ``roots``.

    Returns ``(corpus, stats)`` where corpus maps ``n`` (series length) to a list
    of ``{"path", "pointer", "values"}`` entries, de-duplicated by value digest.
    """
    corpus: dict[int, list[dict]] = {}
    seen: set[str] = set()
    stats = {"files": 0, "series": 0, "values": 0, "skipped": []}
    nexts = (".json", ".csv", ".npy", ".npz")
    for root in roots:
        root = Path(root)
        if not root.exists():
            stats["skipped"].append(f"missing root: {root}")
            continue
        for f in sorted(root.rglob("*")):
            if stats["files"] >= max_files or stats["series"] >= max_series:
                break
            if not f.is_file() or f.suffix.lower() not in nexts:
                continue
            try:
                if f.stat().st_size > 64 * 1024 * 1024:
                    continue
            except OSError:
                continue
            stats["files"] += 1
            items: list[tuple[str, np.ndarray]] = []
            try:
                if f.suffix.lower() == ".json":
                    items = []
                    _walk_json(json.loads(f.read_text(encoding="utf-8", errors="replace")),
                               "$", items)
                elif f.suffix.lower() == ".csv":
                    items = _csv_series(f)
                else:
                    with np.load(f, allow_pickle=False) as z:
                        keys = z.files if hasattr(z, "files") else [""]
                        for k in keys:
                            arr = np.asarray(z[k], dtype=float).ravel()
                            if arr.size >= 3 and np.all(np.isfinite(arr)):
                                items.append((f"$.{k}" if k else "$", arr))
            except Exception as exc:  # unreadable artifact must not crash the gate
                stats["skipped"].append(f"{f}: {type(exc).__name__}")
                continue
            for pointer, arr in items:
                if arr.size > 200_000:
                    continue
                digest = hashlib.sha1(np.round(arr, 12).tobytes()).hexdigest()
                key = f"{arr.size}:{digest}"
                if key in seen:
                    continue
                seen.add(key)
                corpus.setdefault(int(arr.size), []).append(
                    {"path": str(f), "pointer": pointer, "values": arr})
                stats["series"] += 1
                stats["values"] += int(arr.size)
                if stats["values"] >= max_values or stats["series"] >= max_series:
                    break
    return corpus, stats


def _csv_series(path: Path) -> list[tuple[str, np.ndarray]]:
    import csv as _csv

    out: list[tuple[str, np.ndarray]] = []
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        rd = _csv.reader(fh)
        try:
            header = next(rd)
        except StopIteration:
            return out
        cols: list[list[float]] = [[] for _ in header]
        for row in rd:
            if len(row) != len(header):
                continue
            for i, cell in enumerate(row):
                try:
                    cols[i].append(float(str(cell).strip()))
                except (TypeError, ValueError):
                    cols[i].append(float("nan"))
        for name, col in zip(header, cols):
            arr = np.asarray(col, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size >= 3:
                out.append((f"$.{name.strip() or 'col'}", arr))
    return out


# ─────────────────────────── adjudication ───────────────────────────


def resolve_series(arr: np.ndarray, corpus: dict, rtol: float, atol: float,
                   max_window_probes: int = 200_000) -> dict | None:
    """Resolve ``arr`` against the corpus: exact-length match, then window match."""
    n = int(arr.size)
    for it in corpus.get(n, ()):
        if np.allclose(arr, it["values"], rtol=rtol, atol=atol):
            return {"path": it["path"], "pointer": it["pointer"], "mode": "exact"}
    probes = 0
    for m, items in corpus.items():
        if m <= n or m > 6 * n:
            continue
        for it in items:
            win = np.lib.stride_tricks.sliding_window_view(it["values"], n)
            probes += win.shape[0]
            if probes > max_window_probes:
                return None
            hits = np.nonzero(np.isclose(win, arr, rtol=rtol, atol=atol).all(axis=1))[0]
            if hits.size:
                return {"path": it["path"], "pointer": it["pointer"],
                        "mode": f"window@{int(hits[0])}"}
    return None


def load_declarations(paths) -> dict:
    """Load ``figprov.json`` declarations: analytic sources must carry citation."""
    decl: dict[str, list[dict]] = {}
    for p in paths:
        p = Path(p)
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        figs = data.get("figures", data) if isinstance(data, dict) else {}
        for name, entries in (figs or {}).items():
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                continue
            for e in entries:
                if isinstance(e, dict):
                    e = dict(e)
                    e.setdefault("_declared_in", str(p))
                    decl.setdefault(Path(name).name, []).append(e)
    return decl


def _match_declaration(e: dict, artist: dict, order: int) -> bool:
    if "index" in e:
        try:
            return int(e["index"]) == order
        except (TypeError, ValueError):
            return False
    lbl = str(e.get("label", "")).strip()
    if lbl:
        return lbl == (artist.get("label") or "")
    return True


def adjudicate(record: dict, corpus: dict, decl: dict, *, rtol: float, atol: float,
               out_fig_dir: Path | None = None) -> dict:
    findings: list[dict] = []
    artists = [a for a in record.get("artists", []) if a.get("primary")]
    verdicts = []
    skipped_short = 0
    order_per_fig: dict[str, int] = {}

    for a in artists:
        arr = np.asarray(a["primary"], dtype=float)
        if arr.size < 3:
            skipped_short += 1
            continue
        fig = os.path.basename(a.get("figure") or "")
        hit = resolve_series(arr, corpus, rtol, atol)
        order = order_per_fig.get(fig, 0)
        order_per_fig[fig] = order + 1

        entry = {"figure": fig, "call": a["call"], "label": a.get("label", ""),
                 "n": int(arr.size), "callsite": a.get("callsite", {}), "verdict": None,
                 "citation": None}
        if hit:
            entry["verdict"] = "RESOLVED"
            entry["citation"] = f"{hit['path']}#{hit['pointer']}"
            entry["mode"] = hit["mode"]
            unresolved_sec = 0
            for sec in a.get("secondaries", []):
                s = np.asarray(sec, dtype=float)
                if s.size >= 3 and not resolve_series(s, corpus, rtol, atol):
                    unresolved_sec += 1
            for _name, sec in a.get("keyword_series", []):
                s = np.asarray(sec, dtype=float)
                if s.size >= 3 and not resolve_series(s, corpus, rtol, atol):
                    unresolved_sec += 1
            if unresolved_sec:
                findings.append({
                    "code": "PG9_partial_provenance", "severity": "WARN",
                    "figure": fig, "line": a.get("callsite", {}).get("line"),
                    "detail": (f"{a['call']}() 的主数据序列已溯源到 {entry['citation']}，"
                               f"但另有 {unresolved_sec} 条辅助序列（x/yerr/网格）"
                               "在工件树中无对应值——坐标轴或误差棒可能是合成/推算的"),
                })
            verdicts.append(entry)
            continue

        # unresolved: look for an explicit analytic declaration on this figure
        d = None
        for e in decl.get(fig, []):
            if _match_declaration(e, a, order):
                d = e
                break
        if d is not None:
            cite = str(d.get("citation") or "").strip()
            expr = str(d.get("expr") or d.get("note") or "").strip()
            if not cite or not expr:
                findings.append({
                    "code": "PG9_declaration_missing_citation", "severity": "FAIL",
                    "figure": fig, "line": a.get("callsite", {}).get("line"),
                    "detail": (f"{fig}: {a['call']}() 数据既无工件对应值，声明文件 "
                               f"{d.get('_declared_in')} 也缺 citation/expr —— "
                               "『声明过』不等于『可复核』"),
                })
                entry["verdict"] = "DECLARATION_INCOMPLETE"
            else:
                entry["verdict"] = "ANALYTIC_DECLARED"
                entry["citation"] = f"analytic: {cite}"
                entry["declared_expr"] = expr
            verdicts.append(entry)
            continue

        findings.append({
            "code": "PG9_synthesised_figure_data", "severity": "FAIL",
            "figure": fig, "line": a.get("callsite", {}).get("line"),
            "detail": (f"{fig}: {a['call']}() 的 {arr.size} 点数据序列在工件树中无匹配"
                       f"（rtol={rtol:g}, atol={atol:g}），也无 figprov.json 解析声明；"
                       f"调用点 `{a.get('callsite', {}).get('source', '')[:90]}` —— "
                       "这正是 H3『figs synthesised』失效模式"),
        })
        entry["verdict"] = "SYNTHESISED"
        verdicts.append(entry)

    if record.get("error"):
        findings.append({"code": "PG9_generator_crash", "severity": "WARN",
                         "figure": None, "line": None,
                         "detail": "生成器执行中抛异常，未记录到的图一律按『未核验』处理：\n"
                                   + record["error"].strip().splitlines()[-1][:300]})

    saved = record.get("saved") or []
    per_fig_artists = {os.path.basename(a.get("figure") or "") for a in artists}
    for f in saved:
        b = os.path.basename(f)
        if b not in per_fig_artists:
            findings.append({"code": "PG9_figure_without_recorded_data", "severity": "WARN",
                             "figure": b, "line": None,
                             "detail": (f"{b} 已写出但本次执行未捕获任何绘图数据"
                                        "（示意图 / 子进程绘图 / 非 matplotlib 输出）")})

    if out_fig_dir and out_fig_dir.is_dir():
        produced = {p.name for p in out_fig_dir.iterdir()
                    if p.suffix.lower() in {".png", ".svg", ".pdf", ".jpg", ".eps", ".tif"}}
        missed = produced - per_fig_artists
        if produced and not saved:
            findings.append({"code": "PG9_hook_missed", "severity": "WARN", "figure": None,
                             "line": None,
                             "detail": (f"输出目录出现 {len(produced)} 个图像文件但拦截到 0 次 "
                                        f"savefig，钩子可能未生效（示例: "
                                        f"{', '.join(sorted(missed)[:3])}）")})

    fails = [x for x in findings if x["severity"] == "FAIL"]
    warns = [x for x in findings if x["severity"] == "WARN"]
    graded = [v for v in verdicts if v["verdict"] in ("RESOLVED", "ANALYTIC_DECLARED")]
    by_code: dict[str, int] = {}
    for x in findings:
        by_code[x["code"]] = by_code.get(x["code"], 0) + 1
    return {
        "verdict": "FAIL" if fails else ("WARN" if warns else "PASS"),
        "counts": {"fail": len(fails), "warn": len(warns), "by_code": by_code},
        "coverage": {
            "plotted_series": len(verdicts),
            "provenanced": len(graded),
            "rate": round(len(graded) / len(verdicts), 4) if verdicts else 1.0,
            "skipped_short_series": skipped_short,
        },
        "series": verdicts,
        "findings": findings,
        "citations": sorted({v["citation"] for v in graded if v["citation"]}),
    }


def citation_values(citation: str) -> np.ndarray:
    """Resolve a citation produced by this gate back to its numeric values.

    Supported pointer shapes::

        path/artifact.json#$.runs[*].Cf     column of a list-of-records
        path/artifact.json#$.cf_curve       a list of numbers
        path/table.csv#$.error              a CSV column
        path/f.npz#$[0]                     first array of an npz

    Used by the tests and by authors who want to re-derive a plotted curve from
    the citation the gate printed.
    """
    path, _, pointer = citation.partition("#")
    f = Path(path)
    if not pointer:
        raise ValueError(f"citation has no pointer: {citation}")
    if f.suffix.lower() == ".csv":
        col = pointer.rsplit(".", 1)[-1]
        rows = list(_csv_reader(f))
        return np.asarray([r[col] for r in rows], dtype=float)
    if f.suffix.lower() in (".npy", ".npz"):
        with np.load(f, allow_pickle=False) as z:
            key = pointer.lstrip("$").strip(".")
            arr = z[key] if key and hasattr(z, "files") else z[z.files[0]]
            return np.asarray(arr, dtype=float).ravel()
    node = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    body = pointer.lstrip("$").strip(".")
    if "[*]" in body:                      # column projection over a record list
        head, _, tail = body.partition("[*]")
        rows = node
        for part in [x for x in head.split(".") if x]:
            rows = rows[int(part)] if part.isdigit() else rows[part]
        col = tail.strip(".")
        return np.asarray([row[col] for row in rows], dtype=float)
    for part in [x for x in body.split(".") if x]:
        node = node[int(part)] if part.isdigit() else node[part]
    return np.asarray(node, dtype=float).ravel()


def _csv_reader(path: Path):
    import csv as _csv

    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        yield from _csv.DictReader(fh)


# ─────────────────────────── runner ───────────────────────────


def _repo_root(start: Path) -> Path:
    """Nearest ancestor holding .git (generators cite repo-relative paths)."""
    for cand in [start, *start.parents]:
        if (cand / ".git").exists():
            return cand
    return start


def run_generator(generator: Path, *, timeout: float = 900.0, cwd: Path | None = None,
                  env_extra: dict | None = None) -> dict:
    """Execute ``generator`` under the recording bootstrap; return the record."""
    tmp = Path(tempfile.mkdtemp(prefix="figprov_"))
    try:
        boot = tmp / "_boot.py"
        boot.write_text(BOOTSTRAP, encoding="utf-8")
        rec_path = tmp / "record.json"
        env = dict(os.environ)
        env["MPLBACKEND"] = "Agg"
        env["PYTHONHASHSEED"] = "0"
        env.pop("PYTHONSTARTUP", None)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run([sys.executable, str(boot), str(generator), str(rec_path)],
                              cwd=str(cwd or _repo_root(generator.resolve().parent)), env=env,
                              capture_output=True, text=True, timeout=timeout)
        if rec_path.is_file():
            record = json.loads(rec_path.read_text(encoding="utf-8"))
        else:
            record = {"artists": [], "saved": [], "error":
                      f"bootstrap produced no record (rc={proc.returncode}): "
                      f"{proc.stderr.strip()[-800:]}"}
        record["returncode"] = proc.returncode
        record["stderr_tail"] = (proc.stderr or "").strip()[-1500:]
        return record
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_figure_provenance(generator, *, artifact_roots=(), out_fig_dir=None,
                            declarations=(), rtol: float = 1e-6, atol: float = 1e-12,
                            timeout: float = 900.0, run: bool = True,
                            precomputed_record=None, cwd=None) -> dict:
    gen = Path(generator)
    if not gen.is_file():
        raise FileNotFoundError(gen)
    if precomputed_record is not None:
        record = precomputed_record
    elif run:
        record = run_generator(gen, timeout=timeout, cwd=cwd)
    else:  # pragma: no cover - explicit "not verified" path
        record = {"artists": [], "saved": [], "error": "run=False (not executed)"}

    roots = [Path(r) for r in artifact_roots] or [gen.parent]
    corpus, cstats = harvest_series(roots)
    decl = load_declarations(declarations)
    out = adjudicate(record, corpus, decl, rtol=rtol, atol=atol,
                     out_fig_dir=Path(out_fig_dir) if out_fig_dir else None)
    out["generator"] = str(gen)
    out["corpus"] = {"roots": [str(r) for r in roots], **cstats}
    out["policy"] = {"rtol": rtol, "atol": atol, "timeout": timeout,
                     "declarations": [str(d) for d in declarations],
                     "scoped_out": ["arrays with <3 points", "non-matplotlib images",
                                    "value-equal but origin-fabricated arrays"]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="figure-data provenance gate (PG9)")
    ap.add_argument("--generator", required=True, help="figure generator script")
    ap.add_argument("--artifact-root", action="append", default=[],
                    help="root(s) to harvest numeric series from (repeatable)")
    ap.add_argument("--out-fig-dir", default=None, help="dir the generator writes images to")
    ap.add_argument("--bundle", default=None,
                    help="bundle dir holding figprov.json declarations (default: generator dir)")
    ap.add_argument("--declarations", action="append", default=[],
                    help="explicit figprov.json path(s); default = <bundle|gen.dir>/figprov.json")
    ap.add_argument("--rtol", type=float, default=1e-6)
    ap.add_argument("--atol", type=float, default=1e-12)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)

    gen = Path(a.generator)
    if not gen.is_file():
        print(f"generator not found: {gen}", file=sys.stderr)
        return 2

    decls = list(a.declarations)
    if not decls:
        base = Path(a.bundle) if a.bundle else gen.parent
        cand = base / "figprov.json"
        if cand.is_file():
            decls = [str(cand)]

    try:
        out = check_figure_provenance(gen, artifact_roots=a.artifact_root,
                                      out_fig_dir=a.out_fig_dir, declarations=decls,
                                      rtol=a.rtol, atol=a.atol, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        print(f"generator timed out after {a.timeout:g}s", file=sys.stderr)
        return 2

    if a.json_out:
        Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps({"verdict": out["verdict"], "counts": out["counts"],
                      "coverage": out["coverage"]}, ensure_ascii=False, indent=2))
    for f in out["findings"][:40]:
        print(f"  [{f['severity']}] {f['code']} L{f.get('line')}: {f['detail']}")
    if out["citations"]:
        print("  citations:")
        for c in out["citations"][:20]:
            print(f"    {c}")
    return 0 if out["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())