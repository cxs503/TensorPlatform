#!/usr/bin/env bash
# Compatibility entry point. The old implementation embedded a database
# password and produced an unverified SQL stream; use the restore-verified
# custom-format backup workflow instead.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BACKUP_DIR="${BACKUP_DIR:-/var/backups/open-foundation}"

echo "backup.sh now runs the restore-verified PostgreSQL/MLflow backup workflow"
exec "$SCRIPT_DIR/verify_postgres_restore.sh"
