"""Physics seam guards (R4): lock in the R2 boundary inversion.

R2 (PR #577) moved physics implementations into the domain payload zone and
gave core audit code a single consumption point: services.validation_providers.
These guards fail if anyone reintroduces a direct core->physics import or
breaks the seam's neutrality.  They complement the surface-level G1-G3
boundary gates (tools/audit/boundary_gates.py) with seam-specific invariants.
"""
from __future__ import annotations

import ast
import copy
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVICES = REPO / "backend" / "services"
MAIN = REPO / "backend" / "main.py"

# Physics implementations that live in the domain payload zone since R2.
DOMAIN_PHYSICS_MODULES = (
    "api.domain.resistance_model",
    "api.domain.resistance_core_service",
    "services.resistance_model",      # pre-R2 location; must stay gone
    "services.resistance_core",       # pre-R2 location; must stay gone
)


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                for alias in node.names:
                    names.add(f"{node.module}.{alias.name}")
    return names


def _scan_services_for_physics(services_dir: Path) -> list[str]:
    violations = []
    for py in sorted(services_dir.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        hit = _imported_names(tree) & set(DOMAIN_PHYSICS_MODULES)
        if hit:
            violations.append(f"{py.name}: imports {sorted(hit)}")
    return violations


class TestSeamNeutrality:
    def test_seam_module_imports_nothing_from_domain(self):
        """The seam itself must stay domain-neutral forever."""
        src = (SERVICES / "validation_providers.py").read_text(encoding="utf-8")
        names = _imported_names(ast.parse(src))
        domain_imports = {n for n in names if n.startswith("api.") or "domain" in n}
        assert not domain_imports, f"seam must not import domain code: {sorted(domain_imports)}"

    def test_seam_module_has_no_physics_constants(self):
        """No rho/nu/g literals hidden in the seam (constants belong to providers)."""
        src = (SERVICES / "validation_providers.py").read_text(encoding="utf-8")
        for token in ("1025", "998.", "9.81", "1.188"):
            assert token not in src, f"physics constant {token} leaked into the seam"


class TestCoreNeverImportsPhysics:
    def test_services_dir_is_physics_free(self):
        violations = _scan_services_for_physics(SERVICES)
        assert not violations, "core->physics import reintroduced: " + "; ".join(violations)

    def test_guard_actually_fails_on_planted_violation(self, tmp_path):
        """Red-team: the guard must be able to fail (quality bar R1)."""
        planted = tmp_path / "services"
        planted.mkdir()
        (planted / "evil.py").write_text(
            "from api.domain.resistance_model import ittc57_cf  # planted violation\n",
            encoding="utf-8")
        violations = _scan_services_for_physics(planted)
        assert violations and "evil.py" in violations[0]


class TestWiringIsIntact:
    def test_main_registers_the_domain_provider(self):
        src = MAIN.read_text(encoding="utf-8")
        assert "from api.domain.physics_providers import register_ship_resistance_providers" in src
        assert "register_ship_resistance_providers()" in src

    def test_domain_adapter_registers_nothing_else(self):
        """The adapter publishes constants/friction lines; it must not smuggle
        model callables into the seam that core audit code doesn't declare."""
        adapter = (REPO / "backend" / "api" / "domain" / "physics_providers.py")
        src = adapter.read_text(encoding="utf-8")
        tree = ast.parse(src)
        registered = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name == "register_provider":
                registered.append(ast.literal_eval(node.args[1]))
        assert set(registered) == {
            "constant:g_m_s2",
            "constant:rho_sea_water_kg_m3",
            "constant:nu_sea_water_15c_m2_s",
            "friction:ittc1957",
        }

    def test_registry_api_surface_stable(self):
        """Consumers may rely on these names; renames are breaking changes."""
        src = (SERVICES / "validation_providers.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        exported = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        assert {"register_provider", "require_provider", "physical_constant",
                "friction_line", "reset_providers", "ProviderNotRegistered"} <= exported
