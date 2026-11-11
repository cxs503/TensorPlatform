"""Adapters: one real call per external tool class, each with a numeric invariant.

Design rule (inherited from :mod:`tools.toolchain_integration`): an adapter either
produces a *number it can defend* or it refuses.  It never substitutes a value.
Concretely:

* missing backing tool  -> :class:`AdapterUnavailable` -> ``WITHHELD`` (no numbers);
* ran, numbers satisfy the spec invariants -> ``REAL``;
* ran only through the declared fallback -> ``MOCK`` (honest: not the primary path);
* ran, numbers violate an invariant -> ``FAIL`` (honest fail, never upgraded).

Every adapter returns the same shape so the harness never special-cases a tool::

    {"evidence": <level>, "metrics": {...}, "detail": str,
     "call": {"entry": ..., "how": ...}, "artifacts": [...]}

The metrics are the *observables*; the acceptance decision lives in the spec file
(:mod:`tools.external_tools.spec`), so thresholds can be reviewed without reading
this module and a regression in a tool shows up as a failed run, not a silent
number change.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Mapping

AdapterFn = Callable[[Mapping[str, Any]], Dict[str, Any]]


class AdapterUnavailable(RuntimeError):
    """The backing tool is not usable in this environment (-> WITHHELD)."""


def _import(name: str, package_hint: str = ""):
    try:
        return __import__(name)
    except Exception as exc:  # noqa: BLE001 - availability, not logic
        raise AdapterUnavailable(
            f"{name} not importable ({type(exc).__name__}: {exc})"
            + (f"; install {package_hint}" if package_hint else "")) from exc


def _run(argv: List[str], timeout: float = 60.0, cwd: str | None = None):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                             cwd=cwd)
    except FileNotFoundError as exc:
        raise AdapterUnavailable(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdapterUnavailable(f"timeout after {timeout}s: {' '.join(argv)}") from exc
    return out


# ---------------------------------------------------------------------------
# 1. Symbolic computer algebra: differentiate then integrate back, exactly.
# ---------------------------------------------------------------------------
def symbolic_cas(spec: Mapping[str, Any]) -> Dict[str, Any]:
    sympy = _import("sympy", "sympy")
    t0 = time.perf_counter()
    x = sympy.Symbol("x")
    f = x ** 3 * sympy.sin(x)
    df = sympy.diff(f, x)
    back = sympy.simplify(sympy.integrate(df, x) - f)  # fundamental theorem check
    exact_residual = 0.0 if back == 0 else float(abs(sympy.N(back.subs(x, 1.7))))
    # independent numeric cross-check of the derivative (central difference)
    f_num = sympy.lambdify(x, f, "math")
    d_num = sympy.lambdify(x, df, "math")
    x0, h = 1.7, 1e-6
    fd = (f_num(x0 + h) - f_num(x0 - h)) / (2 * h)
    fd_err = abs(fd - float(d_num(x0)))
    return {"evidence": "REAL",
            "metrics": {"antiderivative_identity_residual": exact_residual,
                        "fd_vs_symbolic_abs_err": fd_err},
            "detail": f"d/dx(x^3 sin x) integrated back; exact residual={exact_residual:g}",
            "call": {"entry": "sympy.diff/integrate/lambdify",
                     "how": f"symbolic round-trip + central FD at x0={x0}"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": []}


# ---------------------------------------------------------------------------
# 2. Adaptive quadrature against a closed form.
# ---------------------------------------------------------------------------
def numerical_quadrature(spec: Mapping[str, Any]) -> Dict[str, Any]:
    _import("scipy.integrate")
    from scipy import integrate  # type: ignore

    t0 = time.perf_counter()
    val, err_est = integrate.quad(lambda t: 4.0 / (1.0 + t * t), 0.0, 1.0)
    abs_err = abs(val - math.pi)
    # second, independent rule on the same integrand (Simpson, fixed panel count)
    xs = [i / 1000.0 for i in range(1001)]
    simpson = (1.0 / 1000.0) / 3.0 * (
        (4.0 / (1 + xs[0] ** 2)) + (4.0 / (1 + xs[-1] ** 2))
        + 4 * sum(4.0 / (1 + xs[i] ** 2) for i in range(1, 1000, 2))
        + 2 * sum(4.0 / (1 + xs[i] ** 2) for i in range(2, 1000, 2)))
    return {"evidence": "REAL",
            "metrics": {"quad_vs_pi_abs_err": abs_err,
                        "simpson_vs_pi_abs_err": abs(simpson - math.pi),
                        "quad_value": float(val),
                        "quad_reported_err": float(err_est)},
            "detail": f"∫0^1 4/(1+t²)dt = {val:.15f} vs π = {math.pi:.15f}",
            "call": {"entry": "scipy.integrate.quad", "how": "finite-interval adaptive"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": []}


# ---------------------------------------------------------------------------
# 3. Derivative-free black-box optimisation on a known-quadratic objective.
# ---------------------------------------------------------------------------
def blackbox_optimization(spec: Mapping[str, Any]) -> Dict[str, Any]:
    optuna = _import("optuna", "optuna")
    t0 = time.perf_counter()
    trials = int(spec.get("trials", 200))
    seed = int(spec.get("seed", 7))

    def objective(trial):
        x = trial.suggest_float("x", -3.0, 3.0)
        y = trial.suggest_float("y", -3.0, 3.0)
        return (x - 0.3) ** 2 + (y + 0.7) ** 2

    study = optuna.create_study(direction="minimize",
                                sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=trials)
    best = float(study.best_value)
    return {"evidence": "REAL",
            "metrics": {"best_objective": best,
                        "distance_to_optimum": math.hypot(
                            study.best_params["x"] - 0.3,
                            study.best_params["y"] + 0.7),
                        "n_trials": trials},
            "detail": f"TPE {trials} trials -> best f={best:.3e} at {study.best_params}",
            "call": {"entry": f"optuna {optuna.__version__} study.optimize",
                     "how": f"TPESampler(seed={seed})"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": []}


# ---------------------------------------------------------------------------
# 4. Reverse-mode autodiff vs analytic gradient.
# ---------------------------------------------------------------------------
def automatic_differentiation(spec: Mapping[str, Any]) -> Dict[str, Any]:
    torch = _import("torch", "torch")
    t0 = time.perf_counter()
    x = torch.linspace(-2.0, 2.0, 257, dtype=torch.float64, requires_grad=True)
    y = (x ** 3).sum()
    y.backward()
    analytic = 3.0 * x.detach() ** 2
    max_err = float((x.grad - analytic).abs().max())
    return {"evidence": "REAL",
            "metrics": {"grad_vs_analytic_max_abs_err": max_err,
                        "n_points": int(x.numel())},
            "detail": f"d/dx Σx³ autograd vs 3x²: max|Δ|={max_err:.3e}",
            "call": {"entry": f"torch {torch.__version__} autograd",
                     "how": "float64, 257 points"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": []}


# ---------------------------------------------------------------------------
# 5. Relational store: connect, arithmetic round-trip, version string.
# ---------------------------------------------------------------------------
_PG_ENV_KEYS = ("DATABASE_URL", "TP_PG_DSN", "COUNCIL_LEDGER_DSN", "EXTERNAL_TOOLS_PG_DSN")


def _from_dotenv(path: str) -> str:
    """Read ``DATABASE_URL=`` out of a dotenv file (deployment config, not code)."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def dsn_candidates(spec: Mapping[str, Any]) -> List[tuple]:
    """Ordered (dsn, source) candidates: explicit → env → dotenv files.

    Nothing secret is copied into an artifact: only the *source label* is recorded.
    """
    out: List[tuple] = []

    def add(dsn: str, source: str) -> None:
        if not dsn:
            return
        dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
        if all(dsn != d for d, _ in out):
            out.append((dsn, source))

    add(str(spec.get("dsn") or ""), "spec.dsn")
    for key in _PG_ENV_KEYS:
        add(os.environ.get(key, ""), f"env:{key}")
    if os.environ.get("TP_PG_DB"):
        user = os.environ.get("TP_PG_USER", "foundation")
        pw = os.environ.get("TP_PG_PASSWORD") or os.environ.get("PGPASSWORD") or ""
        host = os.environ.get("TP_PG_HOST", "127.0.0.1")
        port = os.environ.get("TP_PG_PORT", "5432")
        add(f"postgresql://{user}:{pw}@{host}:{port}/{os.environ['TP_PG_DB']}",
            "env:TP_PG_*")
    db = os.environ.get("TP_PG_DB", "")
    if db:
        # local unix socket + peer auth: no secret involved, so it is safe to
        # record as a source label.  Tried *before* the anonymous TCP form.
        add(f"postgresql:///{db}", "env:TP_PG_DB (local socket)")
    dotenvs = [p for p in str(spec.get("dotenv") or "").split(os.pathsep) if p]
    repo = str(spec.get("repo") or "")
    if repo:
        dotenvs.append(os.path.join(repo, ".env"))
    for path in dotenvs:
        add(_from_dotenv(path), f"dotenv:{os.path.basename(path)}")
    return out


#: 1000 terms of i^2 -> closed form 1000*1001*2001/6 = 333833500 (integer exact).
SERIES_N = 1000


def _series_reference() -> int:
    n = SERIES_N
    return n * (n + 1) * (2 * n + 1) // 6


def _probe_sql() -> str:
    return ("SELECT 6*7 AS answer, length(version()) AS vlen, "
            f"(SELECT sum(i::bigint*i) FROM generate_series(1,{SERIES_N}) i) AS ssum")


def relational_store(spec: Mapping[str, Any]) -> Dict[str, Any]:
    """Primary path = a real DB driver; declared fallback = psql CLI (MOCK)."""

    t0 = time.perf_counter()
    cands = dsn_candidates(spec)
    if not cands:
        raise AdapterUnavailable("no DSN candidate in env/spec/dotenv")
    sql = _probe_sql()
    errors: List[str] = []

    for dsn, source in cands:
        for driver in ("psycopg", "psycopg2"):
            try:
                mod = __import__(driver)
            except Exception as exc:  # noqa: BLE001 - stub or absent driver
                errors.append(f"{driver}@{source}: {type(exc).__name__}")
                continue
            try:
                if driver == "psycopg":
                    conn = mod.connect(dsn, connect_timeout=10)
                else:
                    conn = mod.connect(dsn, connect_timeout=10)
                try:
                    cur = conn.cursor()
                    cur.execute(sql)
                    answer, vlen, ssum = cur.fetchone()
                    cur.execute("SELECT version()")
                    version = cur.fetchone()[0]
                    # pg_class (not information_schema) because the latter filters by
                    # privilege: a restricted role would read 0 tables on a full DB.
                    cur.execute("SELECT count(*) FROM pg_catalog.pg_class c "
                                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                                "WHERE n.nspname='public' AND c.relkind IN ('r','p')")
                    tables = cur.fetchone()[0]
                finally:
                    conn.close()
                return {"evidence": "REAL",
                        "metrics": {"arithmetic_answer": float(answer),
                                    "series_sum": float(ssum),
                                    "server_version_len": float(vlen),
                                    "public_table_count": float(tables)},
                        "detail": f"{driver} round-trip via {source}; "
                                  f"server={str(version)[:40]}",
                        "call": {"entry": f"{driver}.connect + cursor.execute",
                                 "how": sql},
                        "duration_s": time.perf_counter() - t0,
                        "artifacts": []}
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                errors.append(f"{driver}@{source}: {type(exc).__name__}: "
                              f"{str(exc)[:80]}")

    cli = shutil.which("psql")
    if cli:
        for dsn, source in cands:
            out = _run([cli, "-d", dsn, "-t", "-A", "-c", sql], timeout=30)
            if out.returncode == 0 and "|" in out.stdout:
                parts = out.stdout.strip().splitlines()[0].split("|")
                answer_s, vlen_s, ssum_s = parts[0], parts[1], parts[2]
                return {"evidence": "MOCK",  # honest: driver path unavailable here
                        "metrics": {"arithmetic_answer": float(answer_s),
                                    "series_sum": float(ssum_s),
                                    "server_version_len": float(vlen_s)},
                        "detail": f"psql CLI fallback via {source} "
                                  f"(drivers unusable: {errors[:2]})",
                        "call": {"entry": "psql -t -A -c", "how": sql},
                        "duration_s": time.perf_counter() - t0,
                        "artifacts": []}
            errors.append(f"psql@{source}: rc={out.returncode} "
                          f"{out.stderr.strip()[:80]}")
    raise AdapterUnavailable("all DB paths failed: " + "; ".join(errors[:4]))


# ---------------------------------------------------------------------------
# 6. Version control: HEAD identity is a fact the platform can re-derive.
# ---------------------------------------------------------------------------
def version_control(spec: Mapping[str, Any]) -> Dict[str, Any]:
    git = shutil.which("git")
    if not git:
        raise AdapterUnavailable("git not on PATH")
    repo = str(spec.get("repo") or os.getcwd())
    t0 = time.perf_counter()
    head = _run([git, "-C", repo, "rev-parse", "HEAD"], timeout=30)
    if head.returncode != 0:
        raise AdapterUnavailable(f"not a git work tree: {repo}: {head.stderr.strip()[:120]}")
    sha = head.stdout.strip()
    count = _run([git, "-C", repo, "rev-list", "--count", "HEAD"], timeout=60)
    tracked = _run([git, "-C", repo, "ls-files"], timeout=120)
    files = [f for f in tracked.stdout.splitlines() if f.strip()]
    # Non-trivial anchor: re-derive a git blob id from worktree bytes and compare
    # with the id git itself reports for HEAD:<path>.  A git that "runs" but
    # cannot reproduce an object id is not usable as a version-control backend.
    blob_match = 0.0
    catfile_match = 0.0
    blob_path = ""
    for rel in files[:400]:
        full = os.path.join(repo, rel)
        if not os.path.isfile(full):
            continue
        status = _run([git, "-C", repo, "status", "--porcelain", "--", rel], timeout=30)
        if status.stdout.strip():
            continue                      # dirty file: HEAD bytes differ, skip
        with open(full, "rb") as fh:
            data = fh.read()
        oid = _run([git, "-C", repo, "rev-parse", f"HEAD:{rel}"], timeout=30)
        if oid.returncode != 0:
            continue
        expected = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        blob_match = 1.0 if oid.stdout.strip() == expected else 0.0
        try:                              # bytes, so binary blobs cannot break decoding
            cat = subprocess.run([git, "-C", repo, "cat-file", "-p", oid.stdout.strip()],
                                 capture_output=True, timeout=30)
            catfile_match = 1.0 if cat.returncode == 0 and cat.stdout == data else 0.0
        except subprocess.TimeoutExpired:
            catfile_match = 0.0
        blob_path = rel
        break
    return {"evidence": "REAL",
            "metrics": {"head_sha_len": float(len(sha)),
                        "commit_count": float(count.stdout.strip() or 0),
                        "tracked_file_count": float(len(files)),
                        "blob_id_reproduced": blob_match,
                        "blob_catfile_roundtrip": catfile_match},
            "detail": f"HEAD={sha[:12]} commits={count.stdout.strip()} "
                      f"tracked={len(files)} blob_reproduced={blob_match:g} "
                      f"({blob_path})",
            "call": {"entry": "git rev-parse / rev-list / ls-files / cat-file",
                     "how": f"repo={repo}; sha1('blob <n>\\0'+bytes) vs HEAD:<path>"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": []}


# ---------------------------------------------------------------------------
# 7. Native build toolchain: compile C, run it, compare against Python float.
# ---------------------------------------------------------------------------
_C_SRC = r"""
#include <stdio.h>
int main(void){
    double h = 0.0;
    for (int i = 1; i <= 10; ++i) h += 1.0 / (double)i;
    printf("%.17g\n", h);
    return 0;
}
"""


def native_build_toolchain(spec: Mapping[str, Any]) -> Dict[str, Any]:
    cc = shutil.which("gcc") or shutil.which("cc")
    if not cc:
        raise AdapterUnavailable("no C compiler on PATH")
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "h10.c")
        exe = os.path.join(tmp, "h10")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(_C_SRC)
        build = _run([cc, "-O2", "-std=c11", "-o", exe, src], timeout=120)
        if build.returncode != 0:
            raise AdapterUnavailable(f"compile failed: {build.stderr.strip()[:200]}")
        run = _run([exe], timeout=30)
        got = float(run.stdout.strip())
    ref = sum(1.0 / i for i in range(1, 11))
    return {"evidence": "REAL",
            "metrics": {"h10_abs_err_vs_python": abs(got - ref),
                        "h10_value": got},
            "detail": f"gcc -O2 compiled+executed; H10={got:.17g} vs python {ref:.17g}",
            "call": {"entry": f"{os.path.basename(cc)} -O2 + execute",
                     "how": "single-file C, stdout parsed"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": []}


# ---------------------------------------------------------------------------
# 8. HDF5 store: write/read round-trip with a digest of the container.
# ---------------------------------------------------------------------------
def hdf5_store(spec: Mapping[str, Any]) -> Dict[str, Any]:
    h5py = _import("h5py", "h5py")
    import hashlib

    import numpy as np
    t0 = time.perf_counter()
    arts: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "roundtrip.h5")
        data = np.linspace(-1.0, 1.0, 1024) ** 2
        with h5py.File(path, "w") as fh:
            dset = fh.create_dataset("values", data=data, compression="gzip")
            dset.attrs["units"] = "nondimensional"
            fh.attrs["producer"] = "external_tools_smoke"
        with h5py.File(path, "r") as fh:
            back = fh["values"][...]
            units = fh["values"].attrs["units"]
            compression = fh["values"].compression
        max_diff = float(np.max(np.abs(back - data)))
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return {"evidence": "REAL",
            "metrics": {"roundtrip_max_abs_diff": max_diff,
                        "n_values": float(data.size),
                        "gzip_attr_preserved": 1.0 if units == "nondimensional" else 0.0,
                        "gzip_filter_active": 1.0 if compression == "gzip" else 0.0},
            "detail": f"h5py {h5py.__version__} gzip round-trip sha256={digest[:16]}",
            "call": {"entry": "h5py.File create_dataset/attrs",
                     "how": "write→read 1024 float64 with gzip"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": arts}


# ---------------------------------------------------------------------------
# 9. Plotting engine: a figure either has ink or it does not.
# ---------------------------------------------------------------------------
def plotting_engine(spec: Mapping[str, Any]) -> Dict[str, Any]:
    import hashlib

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    t0 = time.perf_counter()
    arts: List[str] = []
    out_dir = str(spec.get("out_dir") or os.environ.get("EXTERNAL_TOOLS_OUT", ""))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(out_dir or tmp, "plotting_engine_smoke.png")
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        fig = plt.figure(figsize=(3.0, 3.0), dpi=100)
        ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])       # full-bleed: pixel<->data is exact
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
        ax.axis("off")
        ax.axhline(0.25, color="black", linewidth=1.0)   # known data ordinate
        fig.savefig(path)
        plt.close(fig)
        blob = open(path, "rb").read()
        digest = hashlib.sha256(blob).hexdigest()
        if out_dir:
            arts.append(path)
        img = plt.imread(path)                            # HxWx4 float
        gray = img[..., :3].mean(axis=2)
        ink = gray < 0.5
        height = ink.shape[0]
        row = int(np.argmax(ink.sum(axis=1)))
        data_y = 1.0 - (row + 0.5) / float(height)        # row 0 = top = y=1
        coord_err = abs(data_y - 0.25)
        ink_fraction = float(ink.mean())
    return {"evidence": "REAL",
            "metrics": {"png_bytes": float(len(blob)),
                        "png_magic_ok": 1.0 if blob[:8] == b"\x89PNG\r\n\x1a\n" else 0.0,
                        "line_ordinate_abs_err": float(coord_err),
                        "ink_pixel_fraction": ink_fraction},
            "detail": f"matplotlib {matplotlib.__version__} Agg PNG {len(blob)}B "
                      f"sha256={digest[:16]} y=0.25 rendered at data_y={data_y:.4f}",
            "call": {"entry": "matplotlib.figure.savefig (Agg) + imread",
                     "how": "full-bleed axhline(0.25); pixel row -> data ordinate"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": arts}


# ---------------------------------------------------------------------------
# 10. Schema contract engine: a validator that accepts everything is not usable.
# ---------------------------------------------------------------------------
def json_contract_validation(spec: Mapping[str, Any]) -> Dict[str, Any]:
    pydantic = _import("pydantic", "pydantic")
    t0 = time.perf_counter()

    class ProbeModel(pydantic.BaseModel):  # local, generic: no platform schema
        name: str
        n: int
        weights: List[float] = []

    good = ProbeModel(name="x", n=3, weights=[0.25, 0.5])
    rejected = 0
    for bad in ({"name": "x", "n": "not-an-int-and-not-coercible"},
                {"name": "x"},
                {"name": 5, "n": 1}):
        try:
            ProbeModel(**bad)
        except pydantic.ValidationError:
            rejected += 1
    dumped = good.model_dump_json()
    roundtrip_ok = json.loads(dumped)["n"] == 3
    return {"evidence": "REAL",
            "metrics": {"invalid_inputs_rejected": float(rejected),
                        "valid_roundtrip_ok": 1.0 if roundtrip_ok else 0.0},
            "detail": f"pydantic {pydantic.VERSION}: rejected {rejected}/3 invalid "
                      f"payloads, roundtrip={roundtrip_ok}",
            "call": {"entry": "pydantic.BaseModel validate + model_dump_json",
                     "how": "3 invalid payloads must raise, 1 valid must round-trip"},
            "duration_s": time.perf_counter() - t0,
            "artifacts": []}


ADAPTERS: Dict[str, AdapterFn] = {
    "symbolic_cas": symbolic_cas,
    "numerical_quadrature": numerical_quadrature,
    "blackbox_optimization": blackbox_optimization,
    "automatic_differentiation": automatic_differentiation,
    "relational_store": relational_store,
    "version_control": version_control,
    "native_build_toolchain": native_build_toolchain,
    "hdf5_store": hdf5_store,
    "plotting_engine": plotting_engine,
    "json_contract_validation": json_contract_validation,
}


def known() -> List[str]:
    return sorted(ADAPTERS)


def get(name: str) -> AdapterFn:
    if name not in ADAPTERS:
        raise KeyError(f"unknown adapter {name!r}; known: {known()}")
    return ADAPTERS[name]