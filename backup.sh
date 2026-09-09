#!/usr/bin/env bash
# Take a logical PostgreSQL backup from the running 'postgres' service
# and prove it is non-empty and structurally valid.
#
# Usage: ./backup.sh [output_dir]   (default: ./backups)
set -euo pipefail

OUT_DIR="${1:-backups}"
PROJECT="${COMPOSE_PROJECT_NAME:-barq-assessment}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="${OUT_DIR}/barq_tasks_${STAMP}.sql"

mkdir -p "${OUT_DIR}"

echo "== Backing up PostgreSQL (project=${PROJECT}) =="
if ! docker inspect postgres >/dev/null 2>&1; then
  echo "FAIL: 'postgres' container is not running." >&2
  exit 1
fi

docker exec postgres pg_dump -U barq_app -d barq_tasks --no-owner --no-privileges > "${FILE}"

if [ ! -s "${FILE}" ]; then
  echo "FAIL: backup file is empty: ${FILE}" >&2
  exit 1
fi

if ! grep -q "CREATE TABLE" "${FILE}"; then
  echo "FAIL: backup does not contain expected schema (CREATE TABLE records)." >&2
  exit 1
fi

ROWS=$(docker exec postgres psql -U barq_app -d barq_tasks -tAc "SELECT count(*) FROM records;")
echo "PASS: backup written to ${FILE} ($(wc -l < "${FILE}") lines, records table had ${ROWS} row(s))."
echo "${FILE}"
