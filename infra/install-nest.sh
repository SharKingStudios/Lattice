#!/usr/bin/env bash
# Run once as root on the Nest server after cloning into /opt/uga-bus.
set -euo pipefail

app_dir=/opt/uga-bus
data_dir=/var/lib/uga-bus

apt-get update
apt-get install -y python3 python3-venv python3-pip sqlite3 rsync
id -u uga-bus >/dev/null 2>&1 || useradd --system --home "$data_dir" --shell /usr/sbin/nologin uga-bus
install -d -o uga-bus -g uga-bus -m 0750 "$data_dir" "$data_dir/backups"
install -d -o root -g uga-bus -m 0750 /etc/uga-bus
chmod 0755 "$app_dir/infra/backup.sh"
test -f /etc/uga-bus/uga-bus.env || install -o root -g uga-bus -m 0640 "$app_dir/apps/api/.env.example" /etc/uga-bus/uga-bus.env
python3 -m venv "$app_dir/.venv"
"$app_dir/.venv/bin/pip" install --upgrade pip
"$app_dir/.venv/bin/pip" install -r "$app_dir/apps/api/requirements.txt"
PYTHONPATH="$app_dir/apps/api/src" "$app_dir/.venv/bin/python" -m uga_bus.cli migrate
install -m 0644 "$app_dir/infra/uga-bus.service" /etc/systemd/system/uga-bus.service
install -m 0644 "$app_dir/infra/uga-bus-backup.service" /etc/systemd/system/uga-bus-backup.service
install -m 0644 "$app_dir/infra/uga-bus-backup.timer" /etc/systemd/system/uga-bus-backup.timer
systemctl daemon-reload
systemctl enable --now uga-bus.service uga-bus-backup.timer
