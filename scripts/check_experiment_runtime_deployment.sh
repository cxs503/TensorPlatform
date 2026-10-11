#!/usr/bin/env bash
# Read-only deployment gate for the Model Factory experiment runtime.
# --static validates files and the merged Compose model without touching services.
# --live additionally inspects already-running containers; it never starts them.

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:---static}
OVERLAY=docker-compose.experiment-runtime.yml
SOURCE_ROOT=${EXPERIMENT_RUNTIME_SOURCE_ROOT:-backend}
HOST_SITE_PACKAGES=${EXPERIMENT_HOST_SITE_PACKAGES:-backend/.venv/lib/python3.11/site-packages}
HOST_PYTHON=${EXPERIMENT_HOST_PYTHON:-backend/.venv/bin/python}
COMPOSE=(docker compose
  -f docker-compose.yml
  -f docker-compose.prod.yml
  -f "$OVERLAY")

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

pass() {
  printf 'OK: %s\n' "$*"
}

[[ "$MODE" == "--static" || "$MODE" == "--compat" || "$MODE" == "--live" ]] || \
  fail "usage: $0 [--static|--compat|--live]"

cd "$ROOT_DIR"

required_files=(
  api/causal_api.py
  api/deps.py
  api/experiment_http.py
  api/gan_training.py
  api/gnn_training.py
  api/metrics_api.py
  api/recsys_training.py
  api/tenant_access.py
  models/core.py
  services/experiment_algorithms_causal_recsys.py
  services/experiment_algorithms_gnn_gan.py
  services/experiment_audit.py
  services/experiment_runtime.py
  tasks/celery_app.py
  tasks/experiment_tasks.py
  utils/experiment_metrics.py
)

for path in "${required_files[@]}"; do
  [[ -r "$SOURCE_ROOT/$path" ]] || fail "required revision file is missing: $SOURCE_ROOT/$path"
done
pass "all experiment revision files are present"

[[ -x "$HOST_PYTHON" ]] || fail "host runtime Python is missing: $HOST_PYTHON"
[[ -d "$HOST_SITE_PACKAGES" ]] || fail "host site-packages is missing: $HOST_SITE_PACKAGES"
host_python_version=$("$HOST_PYTHON" -c 'import platform; print(platform.python_version())')
[[ "$host_python_version" == 3.11.* ]] || \
  fail "host runtime requires CPython 3.11, found $host_python_version"
"$HOST_PYTHON" -c \
  'import torch,fastapi,celery,sqlalchemy,pandas,numpy,sklearn,psycopg2'
pass "host runtime imports are complete on CPython $host_python_version"

command -v docker >/dev/null 2>&1 || fail "docker CLI is unavailable"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
"${COMPOSE[@]}" config --quiet
pass "merged Compose configuration is valid"

config_json=$(mktemp)
trap 'rm -f "$config_json"' EXIT
"${COMPOSE[@]}" config --format json >"$config_json"
python3 - "$config_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)

services = config["services"]
required_services = {"backend", "celery-worker", "experiment-artifact-init"}
missing = required_services.difference(services)
assert not missing, f"missing services: {sorted(missing)}"

for service_name in ("backend", "celery-worker"):
    service = services[service_name]
    assert service["environment"]["EXPERIMENT_ARTIFACT_ROOT"] == "/app/uploads/experiments"
    volumes = service.get("volumes", [])
    artifact_mounts = [item for item in volumes if item.get("target") == "/app/uploads/experiments"]
    assert len(artifact_mounts) == 1, f"{service_name}: artifact mount is absent or ambiguous"
    assert artifact_mounts[0]["type"] == "volume", f"{service_name}: artifacts must use a named volume"

worker = services["celery-worker"]
command = worker.get("command", [])
if isinstance(command, list):
    command = " ".join(command)
assert "--include=tasks.experiment_tasks" in command, "worker does not explicitly include experiment task"

for service_name in ("backend", "celery-worker"):
    mounts = {item.get("target"): item for item in services[service_name].get("volumes", [])}
    runtime_mount = mounts.get("/app/services/experiment_runtime.py")
    celery_mount = mounts.get("/app/tasks/celery_app.py")
    task_mount = mounts.get("/app/tasks/experiment_tasks.py")
    metrics_mount = mounts.get("/app/utils/experiment_metrics.py")
    assert runtime_mount and runtime_mount.get("read_only"), f"{service_name}: runtime mount must be read-only"
    assert celery_mount and celery_mount.get("read_only"), f"{service_name}: Celery app mount must be read-only"
    assert task_mount and task_mount.get("read_only"), f"{service_name}: task mount must be read-only"
    assert metrics_mount and metrics_mount.get("read_only"), f"{service_name}: metrics mount must be read-only"
    host_site = mounts.get("/opt/foundation-host-site-packages")
    pth = mounts.get("/opt/venv/lib/python3.11/site-packages/foundation_host_runtime.pth")
    assert host_site and host_site.get("read_only"), f"{service_name}: host packages must be read-only"
    assert pth and pth.get("read_only"), f"{service_name}: runtime .pth must be read-only"

init = services["experiment-artifact-init"]
assert str(init.get("user")) == "0:0", "artifact initializer must have ownership authority"
PY
pass "artifact volume, read-only revision mounts, and task inclusion are wired"

if [[ "$MODE" == "--static" ]]; then
  pass "static experiment-runtime preflight complete (no container was changed)"
  exit 0
fi

if [[ "$MODE" == "--compat" ]]; then
  host_site_absolute=$(realpath "$HOST_SITE_PACKAGES")
  pth_absolute=$(realpath deploy/experiment-runtime/foundation_host_runtime.pth)
  source_absolute=$(realpath "$SOURCE_ROOT")
  docker_runtime=(docker)
  if ! docker info >/dev/null 2>&1; then
    command -v sudo >/dev/null 2>&1 || fail "Docker daemon is not accessible"
    sudo -n docker info >/dev/null 2>&1 || fail "Docker daemon is not accessible"
    docker_runtime=(sudo -n docker)
  fi
  revision_mounts=()
  for path in "${required_files[@]}"; do
    revision_mounts+=( -v "$source_absolute/$path:/app/$path:ro" )
  done
  "${docker_runtime[@]}" run --rm --pull never --network none \
    -v "$host_site_absolute:/opt/foundation-host-site-packages:ro" \
    -v "$pth_absolute:/opt/venv/lib/python3.11/site-packages/foundation_host_runtime.pth:ro" \
    "${revision_mounts[@]}" \
    --entrypoint python open-foundation-backend:2.2.0 -c \
    "import platform; assert platform.python_version() == '$host_python_version'; import torch,fastapi,celery,sqlalchemy,pandas,numpy,sklearn,psycopg2; import api.causal_api,api.recsys_training,api.gnn_training,api.gan_training,api.metrics_api,services.experiment_audit,services.experiment_runtime,tasks.experiment_tasks; from tasks.celery_app import celery_app; assert 'experiment.execute_job' in celery_app.tasks"
  pass "approved image, mounted revision, and host runtime have exact Python ABI, imports, and task registration"
  pass "compatibility preflight complete (transient container used no network)"
  exit 0
fi

docker_runtime=(docker)
if ! docker info >/dev/null 2>&1; then
  command -v sudo >/dev/null 2>&1 || fail "Docker daemon is not accessible"
  sudo -n docker info >/dev/null 2>&1 || fail "Docker daemon is not accessible"
  docker_runtime=(sudo -n docker)
fi
LIVE_COMPOSE=("${docker_runtime[@]}" compose
  -f docker-compose.yml
  -f docker-compose.prod.yml
  -f "$OVERLAY")

project_name=$(python3 - "$config_json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["name"])
PY
)
inspect_json=$(mktemp)
topology_report=$(mktemp)
trap 'rm -f "$config_json" "$inspect_json" "$topology_report"' EXIT
container_ids=$("${docker_runtime[@]}" ps -q \
  --filter "label=com.docker.compose.project=$project_name")
if [[ -n "$container_ids" ]]; then
  # Docker emits one hexadecimal ID per line; no shell metacharacters occur.
  "${docker_runtime[@]}" inspect $container_ids >"$inspect_json"
else
  printf '[]\n' >"$inspect_json"
fi
set +e
python3 scripts/audit_compose_topology.py \
  --compose-config "$config_json" \
  --inspect "$inspect_json" \
  --output "$topology_report"
topology_status=$?
set -e
cat "$topology_report"
if [[ -n "${TOPOLOGY_AUDIT_REPORT:-}" ]]; then
  install -m 0600 "$topology_report" "$TOPOLOGY_AUDIT_REPORT"
  pass "machine-readable topology report written to $TOPOLOGY_AUDIT_REPORT"
fi

for service in backend celery-worker; do
  container_id=$("${LIVE_COMPOSE[@]}" ps -q "$service")
  if [[ -z "$container_id" ]]; then
    labelled_ids=$("${docker_runtime[@]}" ps -q \
      --filter "label=com.docker.compose.project=$project_name" \
      --filter "label=com.docker.compose.service=$service")
    if [[ -n "$labelled_ids" ]]; then
      fail "$service has container(s) with project/service labels but merged Compose cannot discover them; topology ownership/config drift detected (fail-closed)"
    fi
    fail "$service has no container for project=$project_name service=$service"
  fi
  running=$("${docker_runtime[@]}" inspect -f '{{.State.Running}}' "$container_id")
  [[ "$running" == "true" ]] || fail "$service is not running"
done
pass "backend and worker are already running"

[[ "$topology_status" == 0 ]] || \
  fail "running backend/celery-worker/frontend topology differs from merged Compose intent"
pass "running topology matches service, name, image, network, mount, and environment-key intent"

for service in backend celery-worker; do
  container_python_version=$("${LIVE_COMPOSE[@]}" exec -T "$service" python -c \
    'import platform; print(platform.python_version())')
  [[ "$container_python_version" == "$host_python_version" ]] || \
    fail "$service Python $container_python_version != host runtime $host_python_version"
done
pass "backend, worker, and host runtime use CPython $host_python_version"

"${LIVE_COMPOSE[@]}" exec -T backend python -c \
  'import api.causal_api, api.recsys_training, api.gnn_training, api.gan_training; import services.experiment_runtime'
"${LIVE_COMPOSE[@]}" exec -T celery-worker python -c \
  'import tasks.experiment_tasks; from tasks.celery_app import celery_app; assert "experiment.execute_job" in celery_app.tasks'
pass "mounted API/runtime modules import in both containers"

registered=$("${LIVE_COMPOSE[@]}" exec -T celery-worker \
  celery -A tasks.celery_app inspect registered -d celery@celery-worker)
grep -q 'experiment.execute_job' <<<"$registered" || \
  fail "running worker has not registered experiment.execute_job"
pass "running worker registered experiment.execute_job"

marker="/app/uploads/experiments/.preflight-$$"
cleanup_marker() {
  "${LIVE_COMPOSE[@]}" exec -T backend rm -f "$marker" >/dev/null 2>&1 || true
}
trap 'cleanup_marker; rm -f "$config_json" "$inspect_json" "$topology_report"' EXIT
"${LIVE_COMPOSE[@]}" exec -T backend touch "$marker"
"${LIVE_COMPOSE[@]}" exec -T celery-worker test -f "$marker"
cleanup_marker
pass "artifact volume is writable by backend and shared with worker"

"${LIVE_COMPOSE[@]}" exec -T backend curl -fsS \
  http://localhost:8001/api/v1/health >/dev/null
pass "backend health endpoint is healthy"

"${LIVE_COMPOSE[@]}" exec -T backend python -c \
  'import json,urllib.request; paths=json.load(urllib.request.urlopen("http://localhost:8001/openapi.json", timeout=5))["paths"]; required={"/api/v1/causal/analyze","/api/v1/recsys/train","/api/v1/gnn/train","/api/v1/gan/train"}; assert required <= paths.keys(), sorted(required-paths.keys())'
pass "running API exposes all four experiment entry points"
pass "live experiment-runtime preflight complete (no job was submitted)"
