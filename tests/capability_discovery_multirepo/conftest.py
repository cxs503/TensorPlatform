"""Test fixtures for capability_discovery_multirepo tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make src/ importable
_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with synthetic solver repos."""
    # Create TensorLBM repo
    lbm = tmp_path / "TensorLBM"
    lbm.mkdir()
    (lbm / "tests").mkdir()
    (lbm / "src").mkdir()

    # Test file with capability markers
    (lbm / "tests" / "test_multiphase.py").write_text(
        'import pytest\n\n'
        '@pytest.mark.capability("multiphase", evidence_level="REAL", '
        'density_ratio=100, anchors=["Laplace"]) \n'
        'def test_multiphase_laplace():\n'
        '    assert True\n\n'
        '@pytest.mark.capability("multiphase", evidence_level="REAL", '
        'density_ratio=1000)\n'
        'def test_multiphase_high_ratio():\n'
        '    assert True\n'
    )

    # Source file with patterns
    (lbm / "src" / "lbm_solver.py").write_text(
        'class LBMSolver:\n'
        '    """Lattice Boltzmann solver."""\n'
        '    def solve(self):\n'
        '        pass\n'
    )

    # Create TensorFEM repo
    fem = tmp_path / "TensorFEM"
    fem.mkdir()
    (fem / "tests").mkdir()
    (fem / "src").mkdir()

    (fem / "tests" / "test_plate.py").write_text(
        'import pytest\n\n'
        '@pytest.mark.capability("plate_bending", evidence_level="REAL", '
        'anchors=["Timoshenko"])\n'
        'def test_mindlin_plate():\n'
        '    assert True\n'
    )

    (fem / "src" / "fem_solver.py").write_text(
        'class FEMSolver:\n'
        '    """Finite element solver."""\n'
        '    pass\n'
    )

    # Create TensorDEM repo
    dem = tmp_path / "TensorDEM"
    dem.mkdir()
    (dem / "tests").mkdir()

    (dem / "tests" / "test_contact.py").write_text(
        'import pytest\n\n'
        '@pytest.mark.capability("contact_mechanics", evidence_level="MOCK")\n'
        'def test_hertz_contact():\n'
        '    # mock implementation\n'
        '    pass\n'
    )

    # Create TensorFVM repo
    fvm = tmp_path / "TensorFVM"
    fvm.mkdir()
    (fvm / "tests").mkdir()

    (fvm / "tests" / "test_navier_stokes.py").write_text(
        'import pytest\n\n'
        '@pytest.mark.capability("cfd_incompressible", evidence_level="WITHHELD")\n'
        'def test_channel_flow():\n'
        '    pass\n'
    )

    return tmp_path


@pytest.fixture
def config_with_repos(workspace):
    """Create a DiscoveryConfig pointing at synthetic repos."""
    from capability_discovery_multirepo.config import DiscoveryConfig, RepoDescriptor

    return DiscoveryConfig(
        repos=[
            RepoDescriptor(name="TensorLBM", root=str(workspace / "TensorLBM")),
            RepoDescriptor(name="TensorFEM", root=str(workspace / "TensorFEM")),
            RepoDescriptor(name="TensorDEM", root=str(workspace / "TensorDEM")),
            RepoDescriptor(name="TensorFVM", root=str(workspace / "TensorFVM")),
        ],
        output_dir=str(workspace / "output"),
    )


@pytest.fixture
def central_registry(workspace):
    """Create a central capability registry."""
    registry = {
        "schema_version": "1.0",
        "capabilities": [
            {
                "capability_id": "multiphase",
                "label": "Multiphase Flow",
                "code_patterns": ["multiphase", "two_phase", "LBM"],
                "test_patterns": ["multiphase", "two_phase"],
            },
            {
                "capability_id": "plate_bending",
                "label": "Plate Bending",
                "code_patterns": ["plate", "bending", "Mindlin"],
                "test_patterns": ["plate", "bending"],
            },
            {
                "capability_id": "contact_mechanics",
                "label": "Contact Mechanics",
                "code_patterns": ["contact", "Hertz"],
                "test_patterns": ["contact", "Hertz"],
            },
            {
                "capability_id": "cfd_incompressible",
                "label": "Incompressible CFD",
                "code_patterns": ["navier", "stokes", "incompressible"],
                "test_patterns": ["navier", "stokes", "channel"],
            },
        ],
    }
    reg_path = workspace / "capabilities.json"
    reg_path.write_text(json.dumps(registry, indent=2))
    return reg_path


@pytest.fixture
def ledger_path(workspace):
    """Create a ledger path."""
    return workspace / "ledger.json"
