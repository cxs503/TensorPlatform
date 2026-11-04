"""Route-order regression: lifecycle literal GETs must not be shadowed.

Deployed bug (found live 2026-11-05): /tensor/missions/pr-merge-queue,
/suspended, /orphan-queue and /dashboard-snapshot all returned 422 because
tensor_missions_router (GET /{mission_id}) was included BEFORE the lifecycle
router. FastAPI matches in include order — the param route swallowed every
bare literal GET. This test locks the include order.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "api" / "domain" / "tensor.py").read_text(encoding="utf-8")


def test_lifecycle_router_included_before_missions():
    i_life = SRC.find("include_router(tensor_mission_lifecycle_router)")
    i_miss = SRC.find("include_router(tensor_missions_router)")
    assert i_life != -1 and i_miss != -1, "both includes must exist"
    assert i_life < i_miss, (
        "lifecycle router must be included first: its literal GET routes "
        "(/suspended /pr-merge-queue /orphan-queue /dashboard-snapshot) are "
        "shadowed by missions' GET /{mission_id} otherwise"
    )


def test_lifecycle_exposes_the_four_literal_gets():
    life = (Path(__file__).resolve().parents[1] / "api" / "tensor_mission_lifecycle.py").read_text(encoding="utf-8")
    for path in ('"/suspended"', '"/pr-merge-queue"', '"/orphan-queue"', '"/dashboard-snapshot"'):
        assert f"@router.get({path}" in life, f"lifecycle GET {path} missing"
