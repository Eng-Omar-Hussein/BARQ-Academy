#!/usr/bin/env bash
# Restore a PostgreSQL backup produced by backup.sh into the running
# 'postgres' service and prove the restored data is queryable.
#
# Usage: ./restore.sh path/to/backup.sql
set -euo pipefail

FILE="${1:?Usage: ./restore.sh path/to/backup.sql}"

if [ ! -s "${FILE}" ]; then
  echo "FAIL: backup file not found or empty: ${FILE}" >&2
  exit 1
fi

if ! docker inspect postgres >/dev/null 2>&1; then
  echo "FAIL: 'postgres' container is not running." >&2
  exit 1
fi

echo "== Restoring PostgreSQL from ${FILE} =="

# Drop and recreate the schema first so this is a true restore test,
# not an accidental no-op against data that was never removed.
docker exec postgres psql -U barq_app -d barq_tasks -c "DROP TABLE IF EXISTS records;"

docker exec -i postgres psql -U barq_app -d barq_tasks < "${FILE}"

ROWS=$(docker exec postgres psql -U barq_app -d barq_tasks -tAc "SELECT count(*) FROM records;")
if [ "${ROWS}" -lt 1 ]; then
  echo "FAIL: restore completed but 'records' has ${ROWS} rows (expected >= 1)." >&2
  exit 1
fi

echo "PASS: restore verified. 'records' table now has ${ROWS} row(s)."
docker exec postgres psql -U barq_app -d barq_tasks -c "SELECT id, title FROM records ORDER BY id;"
