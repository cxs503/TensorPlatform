"""Provenance pinning of the kernel conservation probe -- regression suite.

Defect this locks down (found 2026-11-10 while maturing node p83w1-n07)
---------------------------------------------------------------------
``services.xsolver.probe`` imported the backend with a plain
``importlib.import_module("tensorlbm")`` after appending the repo to ``sys.path``,
and then recorded ``git_commit`` of ``/root/tensor-ecosystem/TensorLBM``.  On this
host ``tensorlbm`` is also installed editable against other agents' worktrees, and
an editable finder lives in ``sys.meta_path`` -- which is consulted *before*
``sys.path`` -- so the probe measured ``/root/tp-agent/work/m6150/src`` and wrote a
residual next to the wrong commit:

    foreign worktree (m6150)  mass defect @100 steps = 9.313e-07
    declared repo (421f043e)  mass defect @100 steps = 7.105e-15      (measured here)

The 9.3e-7 was then compared against the 1e-6 tolerance and reported as a
barely-passing kernel: a *foreign* checkout's defect certified under this repo's
commit.  Both directions are wrong -- it could certify a broken kernel as fine and
it could fail a healthy one.

The fix: import the package by file location under the declared repo, verify the
resolved origin, store ``module_file`` + ``module_sha256`` + ``shadow_copies`` with
the residual, and raise :class:`ProvenanceError` when the origin cannot be proven.
"""
from __future__ import annotations

import hashlib
import importlib
import sys

import pytest


# ---------------------------------------------------------------------------
# the hazard, reproduced
# ---------------------------------------------------------------------------
def test_plain_import_resolves_to_the_shadow_copy(xsolver, fake_package, tmp_path,
                                                  clean_sys_modules):
    """The old implementation's behaviour: a shadow copy wins the import."""
    declared = tmp_path / "declared_repo"
    shadow = tmp_path / "shadow_repo"
    fake_package(declared, "xsv_fakebackend")
    fake_package(shadow, "xsv_fakebackend", marker="shadow")
    clean_sys_modules.append("xsv_fakebackend")

    sys.path.insert(0, str(shadow))
    try:
        module = importlib.import_module("xsv_fakebackend")
    finally:
        sys.path.remove(str(shadow))

    assert "shadow_repo" in module.__file__, "shim did not reproduce the hazard"
    assert "declared_repo" not in module.__file__


def test_pinned_loader_ignores_the_shadow_copy(xsolver, fake_package, tmp_path,
                                               clean_sys_modules):
    """The fix: the declared repo wins even when a shadow copy is first on sys.path."""
    declared = tmp_path / "declared_repo"
    shadow = tmp_path / "shadow_repo"
    fake_package(declared, "xsv_fakebackend")
    fake_package(shadow, "xsv_fakebackend", marker="shadow")
    clean_sys_modules.append("xsv_fakebackend")

    spec = {"module": "xsv_fakebackend", "repo_default": str(declared),
            "repo_relpath": "", "api": ["equilibrium", "collide_bgk", "stream",
                                        "macroscopic"],
            "version_attr": "__version__"}
    sys.path.insert(0, str(shadow))
    try:
        module, provenance = xsolver.probe._load_backend(spec)
    finally:
        sys.path.remove(str(shadow))

    assert module.MARKER == "original"
    assert provenance["loaded_from"] == "repo-dir-pinned"
    assert provenance["verified"] is True
    assert str(declared) in provenance["module_file"]
    assert any("shadow_repo" in copy for copy in provenance["shadow_copies"]), \
        "the shadow copy must be listed, otherwise the audit cannot explain a future drift"


def test_package_found_under_src_layout(xsolver, fake_package, tmp_path,
                                        clean_sys_modules):
    """A ``src/`` layout repo is resolved like the real TensorLBM checkout."""
    repo = tmp_path / "repo_with_src"
    (repo / "src").mkdir(parents=True)
    fake_package(repo / "src", "xsv_fakebackend")
    clean_sys_modules.append("xsv_fakebackend")

    spec = {"module": "xsv_fakebackend", "repo_default": str(repo),
            "repo_relpath": "", "api": ["equilibrium"]}
    _, provenance = xsolver.probe._load_backend(spec)
    assert provenance["verified"] is True
    assert "/src/xsv_fakebackend/__init__.py" in provenance["module_file"].replace("\\", "/")


# ---------------------------------------------------------------------------
# refusals: an unprovable residual is withheld, not published
# ---------------------------------------------------------------------------
def test_missing_repo_is_refused(xsolver, tmp_path):
    spec = {"module": "xsv_fakebackend", "repo_default": str(tmp_path / "absent"),
            "repo_relpath": "", "api": ["equilibrium"]}
    with pytest.raises(xsolver.probe.ProvenanceError):
        xsolver.probe._load_backend(spec)


def test_repo_without_the_package_is_refused(xsolver, tmp_path):
    (tmp_path / "empty_repo").mkdir()
    spec = {"module": "xsv_fakebackend", "repo_default": str(tmp_path / "empty_repo"),
            "repo_relpath": "", "api": ["equilibrium"]}
    with pytest.raises(xsolver.probe.ProvenanceError):
        xsolver.probe._load_backend(spec)


def test_api_drift_is_refused_not_absorbed(xsolver, fake_package, tmp_path,
                                          clean_sys_modules):
    """A checkout whose kernel API changed must not silently measure something else."""
    repo = tmp_path / "drifted_repo"
    fake_package(repo, "xsv_fakebackend", api=("equilibrium", "macroscopic"),
                 version="2.0.0")
    clean_sys_modules.append("xsv_fakebackend")
    spec = {"module": "xsv_fakebackend", "repo_default": str(repo), "repo_relpath": "",
            "api": ["equilibrium", "collide_bgk", "stream", "macroscopic"]}
    with pytest.raises(xsolver.probe.ApiDriftError) as excinfo:
        xsolver.probe._load_backend(spec)
    assert "collide_bgk" in str(excinfo.value) and "stream" in str(excinfo.value)
    # both refusals are ProbeUnavailable, so the acceptance driver reports WITHHELD
    assert isinstance(excinfo.value, xsolver.probe.ProbeUnavailable)


def test_sha256_of_the_measured_module_is_recorded(xsolver, fake_package, tmp_path,
                                                   clean_sys_modules):
    repo = tmp_path / "hashed_repo"
    pkg = fake_package(repo, "xsv_fakebackend")
    clean_sys_modules.append("xsv_fakebackend")
    _, provenance = xsolver.probe._load_backend(
        {"module": "xsv_fakebackend", "repo_default": str(repo), "repo_relpath": "",
         "api": ["equilibrium"]})
    expected = hashlib.sha256((pkg / "__init__.py").read_bytes()).hexdigest()
    assert provenance["module_sha256"] == expected


# ---------------------------------------------------------------------------
# the real kernel (skips rather than fakes when the checkout is unavailable)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real_backend_available():
    import os
    from pathlib import Path

    try:
        import torch  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("torch unavailable")
    root = Path(os.environ.get("XSOLVER_ECOSYSTEM_ROOT") or "/root/tensor-ecosystem")
    pkg = root / "TensorLBM" / "src" / "tensorlbm" / "__init__.py"
    if not pkg.is_file():
        pytest.skip(f"TensorLBM checkout not available at {pkg}")
    return root


def test_real_kernel_conserves_and_is_pinned(xsolver, real_backend_available):
    """C2 on the real kernel: mass < 1e-6, momentum < 1e-4, origin inside the repo.

    The assertion is much tighter than the acceptance tolerance on purpose: a
    float64 BGK step on a periodic box can only drift by round-off, so anything
    above ~1e-12 means the kernel (or the import) is not what it claims to be --
    exactly the 9.3e-7 the unpinned probe produced from a foreign worktree.
    """
    import os

    os.environ["XSOLVER_ECOSYSTEM_ROOT"] = str(real_backend_available)
    measurement = xsolver.probe.measure({"grid": {"nx": 16, "ny": 16},
                                         "horizons": [10, 100],
                                         "certification_horizon": 100})
    provenance = measurement["provenance"]
    assert provenance["verified"] is True
    assert str(real_backend_available / "TensorLBM") in provenance["module_file"]

    certified = measurement["certification"]
    assert certified["mass"] < 1e-6, certified
    assert certified["momentum"] < 1e-4, certified
    assert certified["mass"] < 1e-12, (
        "float64 periodic-box mass drift above round-off: "
        f"{certified['mass']:.3e} via {provenance['module_file']}")
    assert measurement["energy"] is None, "an isothermal model must not claim energy conservation"