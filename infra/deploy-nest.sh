#!/usr/bin/env bash
# Run from a checkout. Requires NEST_HOST (the real host/IP from the Nest dashboard), not an email address.
set -euo pipefail
: "${NEST_HOST:?Set NEST_HOST to the actual Nest SSH host/IP}"
: "${NEST_USER:=root}"

remote="${NEST_USER}@${NEST_HOST}"
# Build on the workstation; the Nest host only needs Python at runtime.
npm ci
npm run web:build
rsync -az --delete --exclude .git --exclude node_modules --exclude data --exclude .env --exclude '*.sqlite3*' ./ "$remote:/opt/uga-bus/"
ssh "$remote" 'cd /opt/uga-bus && chmod 0755 infra/backup.sh && install -m 0644 infra/nest-proxy.Caddyfile /etc/uga-bus/nest-proxy.Caddyfile && install -m 0644 infra/uga-bus-proxy.service /etc/systemd/system/uga-bus-proxy.service && systemctl daemon-reload && /opt/uga-bus/.venv/bin/pip install -r apps/api/requirements.txt && PYTHONPATH=/opt/uga-bus/apps/api/src /opt/uga-bus/.venv/bin/python -m uga_bus.cli migrate && systemctl restart uga-bus uga-bus-proxy && systemctl is-active --quiet uga-bus uga-bus-proxy'
