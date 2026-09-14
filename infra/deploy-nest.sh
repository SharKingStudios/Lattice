#!/usr/bin/env bash
# Run from a checkout. Requires NEST_HOST (the real host/IP from the Nest dashboard), not an email address.
set -euo pipefail
: "${NEST_HOST:?Set NEST_HOST to the actual Nest SSH host/IP}"
: "${NEST_USER:=root}"

remote="${NEST_USER}@${NEST_HOST}"
rsync -az --delete --exclude .git --exclude node_modules --exclude data --exclude .env --exclude '*.sqlite3*' ./ "$remote:/opt/uga-bus/"
ssh "$remote" 'cd /opt/uga-bus && npm ci && npm run web:build && /opt/uga-bus/.venv/bin/pip install -r apps/api/requirements.txt && PYTHONPATH=/opt/uga-bus/apps/api/src /opt/uga-bus/.venv/bin/python -m uga_bus.cli migrate && systemctl restart uga-bus && systemctl is-active --quiet uga-bus'
