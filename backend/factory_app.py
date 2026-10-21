"""Optional resident community application; numerical and data-platform routers are excluded."""
from __future__ import annotations

import os
import hashlib

if len(os.getenv("JWT_SECRET_KEY", "")) < 32 or len(os.getenv("REFRESH_SECRET_KEY", "")) < 32:
    raise RuntimeError("Configure distinct JWT_SECRET_KEY and REFRESH_SECRET_KEY (at least 32 characters)")
if os.environ["JWT_SECRET_KEY"] == os.environ["REFRESH_SECRET_KEY"]:
    raise RuntimeError("Access and refresh signing keys must differ")

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.auth import router as upstream_auth
from api.deps import require_auth
from api.domain.tensor_common import resolved_org
from api.tensor_approvals import router as approvals
from api.tensor_missions import router as missions
from api.tensor_sessions import router as sessions
from api.factory_models import router as models
from api.factory_residents import router as residents
from api.factory_strategy import router as strategy
from database.config import get_db
from services.factory_profile import get_factory_profile

profile = get_factory_profile()
if profile is None:
    raise RuntimeError("FACTORY_PROFILE is required for the TensorPlatform agent application")

app = FastAPI(title=profile.name, version="0.1.0")
# Reuse the existing login/refresh contract without enabling public registration.
auth = APIRouter()
auth.routes.extend(route for route in upstream_auth.routes if route.path in ("/login", "/refresh"))
app.include_router(auth, prefix="/api/v1/auth")
for router in (missions, sessions, approvals, models, residents, strategy):
    app.include_router(router, prefix="/api/v1")


@app.get("/api/v1/health")
def health():
    return {"status": "ready", "service": "tensorplatform-resident-community",
            "revision": os.getenv("FACTORY_REVISION", "unknown")}


@app.get("/api/v1/tensor/factory")
def configuration(user: dict = Depends(require_auth)):
    return profile.public()


@app.get("/api/v1/tensor/factory/overview")
def overview(db: Session = Depends(get_db), user: dict = Depends(require_auth)):
    org = resolved_org(None, user, db)
    counts = db.execute(text("""
        SELECT status, count(*) AS count FROM tensor_dev_missions
        WHERE organization_id=:org GROUP BY status
    """), {"org": org}).mappings().all()
    approvals_count = db.execute(text("""
        SELECT count(*) FROM tensor_approval_requests
        WHERE organization_id=:org AND status='pending'
    """), {"org": org}).scalar()
    agents = db.execute(text("""
        SELECT u.id, u.username, u.is_active,
               m.id AS mission_id, m.claimed_by, m.heartbeat_at,
               COALESCE(m.heartbeat_at > now() - interval '60 seconds', false) AS online
        FROM users u LEFT JOIN tensor_dev_missions m
          ON m.claimed_user_id=u.id AND m.status IN ('claimed','running')
        WHERE u.organization_id=:org AND upper(u.role)='AGENT'
        ORDER BY u.id, m.id
    """), {"org": org}).mappings().all()
    resident_count = db.execute(text("SELECT count(*) FROM factory_resident_workers WHERE organization_id=:org AND enabled AND heartbeat_at > now()-interval '90 seconds'"), {'org': org}).scalar()
    return {"online_agent_sessions": resident_count, "name": profile.name, "organization_id": org,
            "missions_by_status": {row["status"]: row["count"] for row in counts},
            "pending_approvals": approvals_count,
            "agents": [dict(row) for row in agents]}


@app.get("/api/v1/tensor/factory/missions/{mission_id}/artifacts/{name}")
def download_artifact(mission_id: int, name: str, db: Session = Depends(get_db),
                      user: dict = Depends(require_auth)):
    from api.tensor_missions import _load_mission

    mission = _load_mission(db, mission_id, user)
    refs = (mission["result"] or {}).get("artifacts", [])
    ref = next((item for item in refs if item.get("name") == name), None)
    if not ref or "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(404, "artifact not found")
    root = (profile.state_root / f"m{mission_id}" / "artifacts").resolve()
    path = (root / name).resolve()
    if not root.is_relative_to(profile.state_root.resolve()) or path.parent != root or not path.is_file():
        raise HTTPException(404, "artifact not found")
    if path.stat().st_size > 10 * 1024 * 1024:
        raise HTTPException(413, "report exceeds the local artifact limit")
    payload = path.read_bytes()
    if len(payload) != ref.get("size_bytes") or hashlib.sha256(payload).hexdigest() != ref.get("sha256"):
        raise HTTPException(409, "artifact checksum mismatch")
    return Response(payload, media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment",
                             "X-Artifact-Sha256": ref["sha256"]})
