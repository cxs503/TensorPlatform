#!/usr/bin/env bash
# Non-destructive PostgreSQL/MLflow logical-backup restore verification.
# Uses the PostgreSQL tools already present in kp-postgres; installs nothing.
set -Eeuo pipefail

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-kp-postgres}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/foundation-dr-backups}"
DATABASES=(open_digital_foundation mlflow)
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TEMP_DATABASES=()
CONTAINER_DUMPS=()

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  DOCKER=(sudo docker)
else
  echo "ERROR: Docker is unavailable (directly or through passwordless sudo)." >&2
  exit 2
fi

cleanup() {
  local db dump
  for db in "${TEMP_DATABASES[@]:-}"; do
    drop_database "$db" >/dev/null 2>&1 || true
  done
  for dump in "${CONTAINER_DUMPS[@]:-}"; do
    "${DOCKER[@]}" exec "$POSTGRES_CONTAINER" rm -f "$dump" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

drop_database() {
  local db="$1"
  printf 'DROP DATABASE IF EXISTS "%s" WITH (FORCE);\n' "$db" | \
    "${DOCKER[@]}" exec -i "$POSTGRES_CONTAINER" sh -lc \
      'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres'
}

db_exists() {
  local db="$1"
  printf "SELECT 1 FROM pg_database WHERE datname = '%s';\n" "$db" | \
    "${DOCKER[@]}" exec -i "$POSTGRES_CONTAINER" sh -lc \
      'psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres' | grep -qx 1
}

fingerprint() {
  local db="$1"
  "${DOCKER[@]}" exec -i "$POSTGRES_CONTAINER" sh -lc \
    'psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"' sh "$db" <<'SQL'
CREATE OR REPLACE FUNCTION pg_temp.dr_table_counts()
RETURNS TABLE(table_name text, row_count bigint)
LANGUAGE plpgsql AS $$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT schemaname, tablename
    FROM pg_catalog.pg_tables
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY schemaname, tablename
  LOOP
    RETURN QUERY EXECUTE format(
      'SELECT %L::text, count(*)::bigint FROM %I.%I',
      item.schemaname || '.' || item.tablename,
      item.schemaname,
      item.tablename
    );
  END LOOP;
END;
$$;
WITH schema_signature AS (
  SELECT md5(COALESCE(string_agg(
    table_schema || '.' || table_name || '.' || column_name || ':' ||
    data_type || ':' || is_nullable || ':' || COALESCE(column_default, ''),
    E'\n' ORDER BY table_schema, table_name, ordinal_position
  ), '')) AS digest
  FROM information_schema.columns
  WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
), table_signature AS (
  SELECT md5(COALESCE(string_agg(table_name || ':' || row_count, E'\n' ORDER BY table_name), '')) AS digest,
         count(*) AS tables,
         COALESCE(sum(row_count), 0) AS rows
  FROM pg_temp.dr_table_counts()
)
SELECT schema_signature.digest || '|' || table_signature.digest || '|' ||
       table_signature.tables || '|' || table_signature.rows
FROM schema_signature CROSS JOIN table_signature;
SQL
}

echo "foundation PostgreSQL disaster-recovery verification"
echo "container=$POSTGRES_CONTAINER backup_dir=$BACKUP_DIR timestamp=$TIMESTAMP"

for source_db in "${DATABASES[@]}"; do
  if ! db_exists "$source_db"; then
    echo "FAIL database=$source_db reason=database_not_found" >&2
    exit 1
  fi

  safe_db="${source_db//[^a-zA-Z0-9_]/_}"
  temp_db="dr_${safe_db}_${TIMESTAMP,,}_$$"
  temp_db="${temp_db:0:63}"
  container_dump="/tmp/${safe_db}_${TIMESTAMP}_$$.dump"
  host_dump="$BACKUP_DIR/${safe_db}_${TIMESTAMP}.dump"
  TEMP_DATABASES+=("$temp_db")
  CONTAINER_DUMPS+=("$container_dump")

  before="$(fingerprint "$source_db")"
  "${DOCKER[@]}" exec "$POSTGRES_CONTAINER" sh -lc \
    'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$1" -f "$2"' \
    sh "$source_db" "$container_dump"
  "${DOCKER[@]}" exec "$POSTGRES_CONTAINER" pg_restore --list "$container_dump" >/dev/null
  after="$(fingerprint "$source_db")"

  # Stream the dump so the host file belongs to the operator rather than to
  # container root (docker cp preserves the container-side ownership).
  "${DOCKER[@]}" exec "$POSTGRES_CONTAINER" cat "$container_dump" >"$host_dump"
  chmod 600 "$host_dump"
  if [[ ! -s "$host_dump" ]]; then
    echo "FAIL database=$source_db reason=empty_dump" >&2
    exit 1
  fi

  "${DOCKER[@]}" exec "$POSTGRES_CONTAINER" sh -lc \
    'createdb -U "$POSTGRES_USER" "$1" && pg_restore --exit-on-error --no-owner --no-acl -U "$POSTGRES_USER" -d "$1" "$2"' \
    sh "$temp_db" "$container_dump"
  restored="$(fingerprint "$temp_db")"

  source_state="stable"
  if [[ "$before" != "$after" ]]; then
    source_state="changed_during_backup"
  fi
  if [[ "$restored" != "$before" && "$restored" != "$after" ]]; then
    echo "FAIL database=$source_db reason=fingerprint_mismatch source_state=$source_state" >&2
    echo "  before=$before" >&2
    echo "  after=$after" >&2
    echo "  restored=$restored" >&2
    exit 1
  fi

  size_bytes="$(stat -c %s "$host_dump")"
  checksum="$(sha256sum "$host_dump" | awk '{print $1}')"
  printf '%s  %s\n' "$checksum" "$(basename "$host_dump")" >"${host_dump}.sha256"
  chmod 600 "${host_dump}.sha256"
  echo "PASS database=$source_db source_state=$source_state dump_bytes=$size_bytes sha256=$checksum"
  echo "  fingerprint=$restored backup=$host_dump"

  drop_database "$temp_db" >/dev/null
  "${DOCKER[@]}" exec "$POSTGRES_CONTAINER" rm -f "$container_dump"
done

echo "PASS all databases restored and verified; temporary databases removed"
