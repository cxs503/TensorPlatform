"""tb-eval-loop: tensorbench_results / tensorbench_evaluations + bench_verified category

Revision ID: 112_tensorbench_eval_loop
Revises: 111_campaign_selfheal
"""
from alembic import op

revision = "112_tensorbench_eval_loop"
down_revision = "111_campaign_selfheal"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE IF NOT EXISTS tensorbench_results (
    result_id TEXT PRIMARY KEY,
    mission_id BIGINT NOT NULL,
    organization_id BIGINT,
    benchmark_id TEXT NOT NULL,
    benchmark_version TEXT NOT NULL,
    workload_id TEXT NOT NULL,
    workload_version TEXT NOT NULL,
    metric TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '-',
    ref_value DOUBLE PRECISION NOT NULL,
    got_value DOUBLE PRECISION NOT NULL,
    abs_error DOUBLE PRECISION NOT NULL,
    rel_error DOUBLE PRECISION,
    tolerance_rel DOUBLE PRECISION NOT NULL,
    passed BOOLEAN NOT NULL,
    run_id TEXT NOT NULL,
    implementation_project TEXT,
    implementation_revision TEXT,
    execution_status TEXT NOT NULL,
    cluster_job_id BIGINT NOT NULL,
    source TEXT NOT NULL,
    spec_sha256 TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    stdout_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tb_results_mission ON tensorbench_results (mission_id);
CREATE INDEX IF NOT EXISTS idx_tb_results_bench ON tensorbench_results (benchmark_id, recorded_at);
CREATE TABLE IF NOT EXISTS tensorbench_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    mission_id BIGINT NOT NULL,
    benchmark_id TEXT,
    spec_sha256 TEXT,
    cluster_job_id BIGINT,
    state TEXT NOT NULL,
    reason TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tb_eval_mission ON tensorbench_evaluations (mission_id);
CREATE INDEX IF NOT EXISTS idx_tb_eval_state ON tensorbench_evaluations (state, created_at);
"""


def upgrade() -> None:
    for stmt in [s.strip() for s in DDL.split(";") if s.strip()]:
        op.execute(stmt)
    # L4 真增量口径需要在类别枚举里放开 bench_verified
    op.execute("ALTER TABLE tensor_capability_deltas "
               "DROP CONSTRAINT IF EXISTS ck_capability_delta_category")
    op.execute("ALTER TABLE tensor_capability_deltas ADD CONSTRAINT ck_capability_delta_category "
               "CHECK (category IN ('new_capability','capability_extension','defect_fix',"
               "'reusable_knowledge','falsified_hypothesis','no_substantive_delta','bench_verified'))")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tensorbench_evaluations")
    op.execute("DROP TABLE IF EXISTS tensorbench_results")
