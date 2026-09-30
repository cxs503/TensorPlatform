#!/usr/bin/env bash
# Read-only release preflight. It never builds, starts, restarts or migrates.
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:---static}
STATUS_FILE=${MIGRATION_STATUS_FILE:-}
IMAGE=${ODF_RELEASE_IMAGE:-}
REVISION=${ODF_RELEASE_REVISION:-}
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 2; }
[[ "$MODE" == "--static" || "$MODE" == "--live" ]] || fail "usage: $0 [--static|--live]"
[[ -n "$IMAGE" && -n "$REVISION" ]] || fail "ODF_RELEASE_IMAGE and ODF_RELEASE_REVISION are required"
[[ -n "$STATUS_FILE" && -r "$STATUS_FILE" ]] || fail "MIGRATION_STATUS_FILE must contain read-only migration evidence"
command -v docker >/dev/null 2>&1 || fail "docker CLI is unavailable"
cd "$ROOT_DIR"
compose_json=$(mktemp); inspect_json=$(mktemp)
trap 'rm -f "$compose_json" "$inspect_json"' EXIT
compose_files=(-f docker-compose.yml -f docker-compose.prod.yml)
[[ -f docker-compose.runtime-names.yml ]] && compose_files+=(-f docker-compose.runtime-names.yml)
compose_files+=(-f docker-compose.release-consistency.yml)
docker compose "${compose_files[@]}" config --format json >"$compose_json"
gate=(python3 scripts/release_consistency_gate.py --compose-config "$compose_json" --migration-status "$STATUS_FILE")
if [[ "$MODE" == "--static" ]]; then
  "${gate[@]}"
  printf 'OK: static release preflight complete; no service or database was changed\n'
  exit 0
fi
project=$(python3 - "$compose_json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8')).get('name', ''))
PY
)
ids=$(docker ps -q --filter "label=com.docker.compose.project=$project")
[[ -n "$ids" ]] || fail "no running containers belong to the resolved Compose project"
docker inspect $ids >"$inspect_json"
"${gate[@]}" --inspect "$inspect_json"
worker_id=$(docker compose "${compose_files[@]}" ps -q celery-worker)
[[ -n "$worker_id" ]] || fail "celery-worker is not running"
docker exec "$worker_id" celery -A tasks.celery_app inspect registered -d celery@celery-worker | grep -Fq 'pipeline.execute_run' || fail "running worker has not registered pipeline.execute_run"
if [[ -n "${RELEASE_HEALTH_URL:-}" ]]; then
  curl --fail --silent --show-error --max-time 5 "$RELEASE_HEALTH_URL" >/dev/null || fail "read-only health endpoint check failed"
fi
printf 'OK: live release preflight complete; no job, migration, restart, or deployment was performed\n'
