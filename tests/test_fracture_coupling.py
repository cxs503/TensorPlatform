"""
Comprehensive tests for FEM cohesive → DEM fracture fragment handoff.

Validates:
    1. mass_transfer_error < 1e-10 [kg]
    2. momentum_transfer_error < 1e-08 [kg·m/s]
    3. no_orphan_particles == True

Test scenarios:
    - Single fragment transfer (1:1)
    - Multiple fragments with varying properties
    - Subdivided transfer (1:N)
    - Edge cases: zero velocity, zero mass edge
    - Ice-specific parameters (density ~917 kg/m³)
    - Large-scale transfer (1000+ fragments)
    - Conservation under floating-point stress
"""

import numpy as np
import pytest
import sys
import os
import json

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tensorphysics.fracture_coupling import (
    CohesiveFragment,
    DEMParticle,
    FractureCouplingTransfer,
    TransferResult,
    transfer_fragments_to_dem,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

def make_fragment(fid=0, mass=1.0, pos=None, vel=None, ang_vel=None,
                  density=917.0, failure_time=0.0):
    """Helper to create a CohesiveFragment with defaults."""
    if pos is None:
        pos = np.array([0.0, 0.0, 0.0])
    if vel is None:
        vel = np.array([1.0, 0.0, 0.0])
    if ang_vel is None:
        ang_vel = np.array([0.0, 0.0, 0.0])
    volume = mass / density
    # Inertia of equivalent sphere
    r = (3.0 * volume / (4.0 * np.pi)) ** (1.0 / 3.0)
    I = 0.4 * mass * r**2 * np.eye(3)
    return CohesiveFragment(
        fragment_id=fid,
        mass=mass,
        position=np.asarray(pos, dtype=float),
        velocity=np.asarray(vel, dtype=float),
        angular_velocity=np.asarray(ang_vel, dtype=float),
        inertia_tensor=I,
        volume=volume,
        density=density,
        failure_time=failure_time,
    )


def make_ice_fragments(n=10, seed=42):
    """Generate n ice-like fragments with random properties."""
    rng = np.random.RandomState(seed)
    fragments = []
    for i in range(n):
        mass = rng.uniform(0.01, 5.0)  # kg
        pos = rng.uniform(-1.0, 1.0, 3)  # m
        vel = rng.uniform(-5.0, 5.0, 3)  # m/s
        ang_vel = rng.uniform(-10.0, 10.0, 3)  # rad/s
        fragments.append(make_fragment(
            fid=i, mass=mass, pos=pos, vel=vel, ang_vel=ang_vel,
            density=917.0, failure_time=rng.uniform(0, 1.0)
        ))
    return fragments


# ─── Test: Single Fragment 1:1 Transfer ──────────────────────────────────────

class TestSingleFragmentTransfer:
    """Test basic 1:1 transfer with a single fragment."""

    def test_mass_conservation_single(self):
        """Mass must be exactly preserved for single fragment."""
        frag = make_fragment(fid=0, mass=2.5, vel=[3.0, -1.0, 0.5])
        particles, result = transfer_fragments_to_dem([frag])

        assert result.mass_transfer_error < 1e-10, \
            f"Mass error {result.mass_transfer_error} >= 1e-10"
        assert result.mass_pass

    def test_momentum_conservation_single(self):
        """Momentum must be exactly preserved for single fragment."""
        frag = make_fragment(fid=0, mass=2.5, vel=[3.0, -1.0, 0.5])
        particles, result = transfer_fragments_to_dem([frag])

        assert result.momentum_transfer_error < 1e-8, \
            f"Momentum error {result.momentum_transfer_error} >= 1e-8"
        assert result.momentum_pass

    def test_no_orphan_single(self):
        """Single fragment must produce exactly one particle."""
        frag = make_fragment(fid=0, mass=1.0)
        particles, result = transfer_fragments_to_dem([frag])

        assert result.no_orphan_particles is True
        assert result.n_particles_out == 1
        assert result.orphan_pass

    def test_exact_values_single(self):
        """Verify exact property transfer for single fragment."""
        frag = make_fragment(fid=42, mass=3.14, pos=[1.0, 2.0, 3.0],
                             vel=[0.5, -0.5, 1.0])
        particles, result = transfer_fragments_to_dem([frag])

        p = particles[0]
        assert p.mass == frag.mass
        np.testing.assert_array_equal(p.position, frag.position)
        np.testing.assert_array_equal(p.velocity, frag.velocity)
        assert p.source_fragment_id == 42


# ─── Test: Multiple Fragments ────────────────────────────────────────────────

class TestMultipleFragments:
    """Test 1:1 transfer with multiple fragments."""

    def test_mass_conservation_10_fragments(self):
        """Mass conservation with 10 ice fragments."""
        fragments = make_ice_fragments(n=10)
        particles, result = transfer_fragments_to_dem(fragments)

        assert result.mass_transfer_error < 1e-10, \
            f"Mass error {result.mass_transfer_error:.2e} >= 1e-10"
        assert result.mass_pass

    def test_momentum_conservation_10_fragments(self):
        """Momentum conservation with 10 ice fragments."""
        fragments = make_ice_fragments(n=10)
        particles, result = transfer_fragments_to_dem(fragments)

        assert result.momentum_transfer_error < 1e-8, \
            f"Momentum error {result.momentum_transfer_error:.2e} >= 1e-8"
        assert result.momentum_pass

    def test_no_orphan_10_fragments(self):
        """No orphans with 10 fragments."""
        fragments = make_ice_fragments(n=10)
        particles, result = transfer_fragments_to_dem(fragments)

        assert result.no_orphan_particles is True
        assert result.n_fragments_in == 10
        assert result.n_particles_out == 10
        assert result.orphan_pass

    def test_all_pass_10_fragments(self):
        """All criteria pass with 10 fragments."""
        fragments = make_ice_fragments(n=10)
        particles, result = transfer_fragments_to_dem(fragments)
        assert result.all_pass is True


# ─── Test: Large Scale ───────────────────────────────────────────────────────

class TestLargeScale:
    """Test conservation with large number of fragments."""

    @pytest.mark.parametrize("n", [100, 500, 1000])
    def test_mass_conservation_large(self, n):
        """Mass conservation scales to 1000+ fragments."""
        fragments = make_ice_fragments(n=n)
        particles, result = transfer_fragments_to_dem(fragments)

        assert result.mass_transfer_error < 1e-10, \
            f"n={n}: Mass error {result.mass_transfer_error:.2e}"

    @pytest.mark.parametrize("n", [100, 500, 1000])
    def test_momentum_conservation_large(self, n):
        """Momentum conservation scales to 1000+ fragments."""
        fragments = make_ice_fragments(n=n)
        particles, result = transfer_fragments_to_dem(fragments)

        assert result.momentum_transfer_error < 1e-8, \
            f"n={n}: Momentum error {result.momentum_transfer_error:.2e}"

    @pytest.mark.parametrize("n", [100, 500, 1000])
    def test_no_orphan_large(self, n):
        """No orphans scales to 1000+ fragments."""
        fragments = make_ice_fragments(n=n)
        particles, result = transfer_fragments_to_dem(fragments)

        assert result.no_orphan_particles is True
        assert result.n_particles_out == n


# ─── Test: Subdivided Transfer (1:N) ────────────────────────────────────────

class TestSubdividedTransfer:
    """Test 1:N transfer with subdivision."""

    def test_mass_conservation_subdivided(self):
        """Mass conservation with 1:4 subdivision."""
        fragments = make_ice_fragments(n=5)
        particles, result = transfer_fragments_to_dem(
            fragments, n_subdivisions=4)

        assert result.mass_transfer_error < 1e-10, \
            f"Mass error {result.mass_transfer_error:.2e}"
        assert result.mass_pass

    def test_momentum_conservation_subdivided(self):
        """Momentum conservation with 1:4 subdivision."""
        fragments = make_ice_fragments(n=5)
        particles, result = transfer_fragments_to_dem(
            fragments, n_subdivisions=4)

        assert result.momentum_transfer_error < 1e-8, \
            f"Momentum error {result.momentum_transfer_error:.2e}"
        assert result.momentum_pass

    def test_no_orphan_subdivided(self):
        """No orphans with 1:4 subdivision."""
        fragments = make_ice_fragments(n=5)
        particles, result = transfer_fragments_to_dem(
            fragments, n_subdivisions=4)

        assert result.no_orphan_particles is True
        assert result.n_particles_out == 20  # 5 * 4
        assert result.orphan_pass

    def test_particle_count_subdivided(self):
        """Correct number of particles after subdivision."""
        fragments = make_ice_fragments(n=3)
        particles, result = transfer_fragments_to_dem(
            fragments, n_subdivisions=8)

        assert result.n_particles_out == 24  # 3 * 8


# ─── Test: Edge Cases ────────────────────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_velocity(self):
        """Fragments with zero velocity."""
        frag = make_fragment(fid=0, mass=1.0, vel=[0.0, 0.0, 0.0])
        particles, result = transfer_fragments_to_dem([frag])

        assert result.all_pass
        assert result.momentum_transfer_error == 0.0

    def test_very_small_mass(self):
        """Very small mass fragment (micro-scale)."""
        frag = make_fragment(fid=0, mass=1e-15, vel=[1.0, 0.0, 0.0])
        particles, result = transfer_fragments_to_dem([frag])

        assert result.mass_transfer_error < 1e-10
        assert result.momentum_transfer_error < 1e-8

    def test_very_large_mass(self):
        """Very large mass fragment (macro-scale)."""
        frag = make_fragment(fid=0, mass=1e6, vel=[0.01, 0.0, 0.0])
        particles, result = transfer_fragments_to_dem([frag])

        assert result.all_pass

    def test_high_velocity(self):
        """High velocity fragment (impact scenario)."""
        frag = make_fragment(fid=0, mass=0.5, vel=[100.0, -50.0, 25.0])
        particles, result = transfer_fragments_to_dem([frag])

        assert result.all_pass

    def test_mixed_mass_range(self):
        """Fragments with vastly different masses."""
        fragments = [
            make_fragment(fid=0, mass=1e-6, vel=[1.0, 0.0, 0.0]),
            make_fragment(fid=1, mass=1e3, vel=[0.001, 0.0, 0.0]),
            make_fragment(fid=2, mass=1.0, vel=[1.0, 1.0, 1.0]),
        ]
        particles, result = transfer_fragments_to_dem(fragments)

        assert result.all_pass

    def test_empty_fragment_list(self):
        """Empty input should produce empty output."""
        particles, result = transfer_fragments_to_dem([])

        assert result.n_fragments_in == 0
        assert result.n_particles_out == 0
        assert result.no_orphan_particles is True
        assert result.mass_transfer_error == 0.0
        assert result.momentum_transfer_error == 0.0


# ─── Test: Ice-Specific Scenario ─────────────────────────────────────────────

class TestIceScenario:
    """
    Ice-zone navigation scenario: ice plate fracture under hull impact.

    Physical setup:
    - Ice plate (density 917 kg/m³, thickness 0.5m)
    - Fractured into multiple pieces by hull impact
    - Each piece transferred to DEM for drift simulation
    """

    def test_ice_plate_fracture(self):
        """Simulate ice plate breaking into 20 fragments."""
        # Ice properties
        rho_ice = 917.0  # kg/m³
        plate_area = 4.0  # m²
        plate_thickness = 0.5  # m
        plate_volume = plate_area * plate_thickness
        plate_mass = rho_ice * plate_volume  # ~1834 kg

        # Simulate fracture into 20 pieces
        n_frags = 20
        frag_mass = plate_mass / n_frags
        rng = np.random.RandomState(123)

        fragments = []
        total_momentum = np.zeros(3)
        for i in range(n_frags):
            # Random velocity perturbation around mean drift
            v_mean = np.array([0.5, 0.0, -0.1])  # m/s
            v_perturb = rng.uniform(-0.2, 0.2, 3)
            vel = v_mean + v_perturb

            pos = rng.uniform(-1.0, 1.0, 3)
            frag = make_fragment(
                fid=i, mass=frag_mass, pos=pos, vel=vel,
                density=rho_ice, failure_time=0.5
            )
            fragments.append(frag)
            total_momentum += frag.linear_momentum

        particles, result = transfer_fragments_to_dem(
            fragments, transfer_time=0.5, particle_density=rho_ice)

        # Verify conservation
        assert result.mass_transfer_error < 1e-10, \
            f"Ice scenario mass error: {result.mass_transfer_error:.2e}"
        assert result.momentum_transfer_error < 1e-8, \
            f"Ice scenario momentum error: {result.momentum_transfer_error:.2e}"
        assert result.no_orphan_particles is True
        assert result.all_pass

        # Verify total mass matches plate
        assert abs(result.total_mass_in - plate_mass) < 1e-10


# ─── Test: Conservation Under Stress ─────────────────────────────────────────

class TestConservationStress:
    """Stress test conservation with adversarial inputs."""

    def test_floating_point_stress(self):
        """Many small additions that could accumulate FP error."""
        # 10000 fragments each with mass = 1/3 (not exactly representable)
        n = 10000
        mass_each = 1.0 / 3.0
        fragments = [
            make_fragment(fid=i, mass=mass_each, vel=[1.0, 0.0, 0.0])
            for i in range(n)
        ]
        particles, result = transfer_fragments_to_dem(fragments)

        # Mass error should still be < 1e-10
        assert result.mass_transfer_error < 1e-10, \
            f"FP stress mass error: {result.mass_transfer_error:.2e}"
        # Momentum error should still be < 1e-8
        assert result.momentum_transfer_error < 1e-8, \
            f"FP stress momentum error: {result.momentum_transfer_error:.2e}"

    def test_cancellation_stress(self):
        """Fragments with opposing momenta (cancellation test)."""
        fragments = [
            make_fragment(fid=0, mass=1.0, vel=[1e5, 0.0, 0.0]),
            make_fragment(fid=1, mass=1.0, vel=[-1e5, 0.0, 0.0]),
        ]
        particles, result = transfer_fragments_to_dem(fragments)

        assert result.all_pass
        # Net momentum should be ~0
        assert np.linalg.norm(result.total_momentum_out) < 1e-6


# ─── Test: API Contract ──────────────────────────────────────────────────────

class TestAPIContract:
    """Verify the API contract and return types."""

    def test_return_types(self):
        """Verify return types are correct."""
        frag = make_fragment()
        particles, result = transfer_fragments_to_dem([frag])

        assert isinstance(particles, list)
        assert isinstance(particles[0], DEMParticle)
        assert isinstance(result, TransferResult)

    def test_result_serialization(self):
        """TransferResult can be serialized to dict/JSON."""
        frag = make_fragment()
        _, result = transfer_fragments_to_dem([frag])

        d = result.to_dict()
        assert isinstance(d, dict)
        # Should be JSON-serializable
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_engine_reuse(self):
        """Engine can be reused for multiple transfers."""
        engine = FractureCouplingTransfer()

        frags1 = [make_fragment(fid=0)]
        frags2 = [make_fragment(fid=1)]

        p1, r1 = engine.transfer(frags1)
        p2, r2 = engine.transfer(frags2)

        # Particle IDs should be unique across transfers
        assert p1[0].particle_id != p2[0].particle_id


# ─── Acceptance Criteria Test ────────────────────────────────────────────────

class TestAcceptanceCriteria:
    """
    Direct tests of the machine acceptance criteria.
    These are the definitive pass/fail tests.
    """

    def test_mass_transfer_error_threshold(self):
        """ACCEPTANCE: mass_transfer_error < 1e-10 [kg]"""
        fragments = make_ice_fragments(n=50, seed=999)
        _, result = transfer_fragments_to_dem(fragments)

        assert result.mass_transfer_error < 1e-10, \
            f"FAIL: mass_transfer_error = {result.mass_transfer_error:.2e} >= 1e-10"

    def test_momentum_transfer_error_threshold(self):
        """ACCEPTANCE: momentum_transfer_error < 1e-08 [kg·m/s]"""
        fragments = make_ice_fragments(n=50, seed=999)
        _, result = transfer_fragments_to_dem(fragments)

        assert result.momentum_transfer_error < 1e-8, \
            f"FAIL: momentum_transfer_error = {result.momentum_transfer_error:.2e} >= 1e-8"

    def test_no_orphan_particles(self):
        """ACCEPTANCE: no_orphan_particles == True"""
        fragments = make_ice_fragments(n=50, seed=999)
        _, result = transfer_fragments_to_dem(fragments)

        assert result.no_orphan_particles is True, \
            "FAIL: orphan particles detected"

    def test_all_criteria_combined(self):
        """All three acceptance criteria simultaneously."""
        fragments = make_ice_fragments(n=100, seed=2024)
        _, result = transfer_fragments_to_dem(fragments)

        assert result.mass_transfer_error < 1e-10
        assert result.momentum_transfer_error < 1e-8
        assert result.no_orphan_particles is True
        assert result.all_pass is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
