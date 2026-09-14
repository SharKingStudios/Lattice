#!/usr/bin/env bash
set -euo pipefail

data_dir="${UGA_BUS_DATA_DIR:-/var/lib/uga-bus}"
database="${data_dir}/uga_bus.sqlite3"
backup_dir="${data_dir}/backups"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp_database="${backup_dir}/uga_bus-${timestamp}.sqlite3"
archive="${tmp_database}.gz"

mkdir -p "$backup_dir"
test -f "$database"
sqlite3 "$database" ".backup '$tmp_database'"
sqlite3 "$tmp_database" 'PRAGMA integrity_check;' | grep -qx 'ok'
gzip -9 "$tmp_database"
gzip -t "$archive"
find "$backup_dir" -type f -name 'uga_bus-*.sqlite3.gz' -mtime +14 -delete
echo "verified backup: $archive"
