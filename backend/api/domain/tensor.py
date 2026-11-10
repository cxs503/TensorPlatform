"""
Tensor domain — the research console operation plane.

Aggregates scenario / execution / approval / session / system / campaign routers.
Sub-modules keep their own prefixes under /api/v1/tensor/*.
"""
from fastapi import APIRouter

router = APIRouter(tags=["科研运行台"])

from api.tensor_scenarios import router as tensor_scenarios_router          # prefix="/tensor/scenarios"
from api.tensor_executions import router as tensor_executions_router       # prefix="/tensor/executions"
from api.tensor_approvals import router as tensor_approvals_router         # prefix="/tensor/approvals"
from api.tensor_sessions import router as tensor_sessions_router           # prefix="/tensor/sessions"
from api.tensor_system import router as tensor_system_router               # prefix="/tensor/system"
from api.tensor_activity import router as tensor_activity_router           # prefix="/tensor/activity"
from api.tensor_workspace import router as tensor_workspace_router         # prefix="/tensor/workspace"
from api.tensor_llm import router as tensor_llm_router                     # prefix="/tensor/llm"
from api.tensor_missions import router as tensor_missions_router           # prefix="/tensor/missions"
from api.tensor_mission_lifecycle import router as tensor_mission_lifecycle_router  # prefix="/tensor/missions" (lifecycle)
from api.tensor_reports import router as tensor_reports_router             # prefix="/tensor/reports"
from api.tensor_papers import router as tensor_papers_router             # prefix="/tensor/papers"
from api.knowledge_hub import router as knowledge_hub_router           # prefix="/tensor/knowledge" (知识专栏)
from api.tensor_capability import router as tensor_capability_router      # prefix="/tensor/capability" (PR-10 能力中心聚合面)
from api.campaign_plans import router as campaign_plans_router             # prefix="/tensor/campaigns"
from api.campaign_routing import router as campaign_routing_router         # prefix="/tensor/campaigns" (conditional DAG)
from api.campaign_self_heal import router as campaign_self_heal_router   # prefix="/tensor/campaigns" (DAG self-healing + elastic scheduling)
from api.tensor_centers import router as tensor_centers_router             # prefix="/tensor/centers" (PR-10 四中心最小视图)
from api.tensor_repro import router as tensor_repro_router                 # prefix="/tensor/repro"
from api.execution_lineage import router as execution_lineage_router       # prefix="/lineage"
from api.kpi import router as kpi_router                                   # prefix="/tensor/kpi" (m5121 KPI guide tracker)
from api.failure_postmortems import router as failure_postmortems_router   # prefix="/tensor/failure-postmortems" (IN59-A4)
from api.tensor_reconcile import router as tensor_reconcile_router         # prefix="/tensor/reconcile" (p254w1-n01 报告对账门)
from api.negative_knowledge import router as negative_knowledge_router     # prefix="/tensor/negative-knowledge" (P155W1-N03 否定性知识台账)

router.include_router(tensor_scenarios_router)
router.include_router(tensor_executions_router)
router.include_router(tensor_approvals_router)
router.include_router(tensor_sessions_router)
router.include_router(tensor_system_router)
router.include_router(tensor_activity_router)
router.include_router(tensor_workspace_router)
router.include_router(tensor_llm_router)
# lifecycle FIRST: its literal routes (/suspended, /pr-merge-queue,
# /orphan-queue, /dashboard-snapshot) must not be shadowed by missions'
# GET /{mission_id} path-param route (FastAPI matches in include order).
router.include_router(tensor_mission_lifecycle_router)
router.include_router(tensor_missions_router)
router.include_router(tensor_reports_router)
router.include_router(tensor_papers_router)
router.include_router(knowledge_hub_router)
router.include_router(tensor_capability_router)
router.include_router(campaign_plans_router)
router.include_router(campaign_routing_router)
router.include_router(campaign_self_heal_router)
router.include_router(tensor_centers_router)
router.include_router(kpi_router)
router.include_router(failure_postmortems_router)

# ── m613: Truth Gates — Independence-Convergence Verdict Mechanism ──
from api.truth_gates import router as truth_gates_router              # prefix="/truth-gates"
router.include_router(truth_gates_router)
router.include_router(tensor_repro_router)
router.include_router(execution_lineage_router)

# ── p254w1-n01: Report Reconciliation Gate ──
router.include_router(tensor_reconcile_router)

# ── P155W1-N03: Negative Knowledge Registry (否定性知识台账) ──
router.include_router(negative_knowledge_router)

# ── PR#732: Proposal Gate — 提案→战役播种供给链机器门 ──
from api.proposal_gate import router as proposal_gate_router          # prefix="/proposals"
router.include_router(proposal_gate_router)

# ── m9254: Platform Capability Audit & Autonomous Sustainability ──
from api.capability_audit import router as capability_audit_router      # prefix="/tensor/capabilities"
from api.platform_kpi_api import router as platform_kpi_api_router     # prefix="/tensor/kpi"
router.include_router(capability_audit_router)
router.include_router(platform_kpi_api_router)
