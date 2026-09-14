# Hack Club Nest deployment

Nest is a Linux VPS; the account email `sharkingstudios@hackclub.app` is an account identifier, **not** a hostname. Use the actual SSH host or public IP shown in the Nest dashboard/account setup. Current community guidance confirms systemd is appropriate for long-running Nest processes; this project uses a local FastAPI bind plus Caddy reverse proxy.

## First install

1. Determine the actual server connection value in the Nest dashboard. From this checkout, test it without guessing an email hostname:

   ```bash
   ssh root@ACTUAL_NEST_HOST 'hostname; hostname -I'
   ```

2. Clone/push this repository to the server at `/opt/uga-bus`, then install once:

   ```bash
   ssh root@ACTUAL_NEST_HOST
   git clone REPOSITORY_URL /opt/uga-bus
   cd /opt/uga-bus
   bash infra/install-nest.sh
   ```

3. Edit the out-of-git environment file:

   ```bash
   sudoedit /etc/uga-bus/uga-bus.env
   ```

   Keep the persistent database below `/var/lib/uga-bus`, set poll/retention values if needed, and set a random `UGA_BUS_DIAGNOSTICS_TOKEN` before externally exposing diagnostics.

4. Verify collector and database startup:

   ```bash
   systemctl status uga-bus
   journalctl -u uga-bus -f
   curl -fsS http://127.0.0.1:8000/api/v1/health
   ```

The `uga-bus` system user owns only the persistent data. The service binds `127.0.0.1:8000`; Caddy is the public TLS terminator. systemd restarts it after a failure or reboot.

## DNS and TLS

No Nest IP or DNS authority is available in this checkout, so no DNS record has been invented. After running `hostname -I` above, add this exact kind of record at the DNS provider that controls `loganpeterson.org`:

| Type | Name | Value | TTL |
| --- | --- | --- | --- |
| A | `bus` | the first public IPv4 returned by `hostname -I` / Nest dashboard | 300 |

Do not use `sharkingstudios@hackclub.app` as the record target. Once `bus.loganpeterson.org` resolves to the server, Caddy automatically obtains and renews HTTPS. Check:

```bash
curl -I https://bus.loganpeterson.org/api/v1/health
```

If Nest supplies a canonical hostname instead of an IP, use a CNAME only when that hostname is explicitly documented as stable for custom domains; otherwise use the public IPv4 A record above.

## Update / rollback

For an update from a workstation with SSH access:

```bash
NEST_HOST=ACTUAL_NEST_HOST NEST_USER=root bash infra/deploy-nest.sh
```

The script excludes database/history, env files and node modules, builds the static client, runs the idempotent schema initialization, then restarts the API. It never deletes `/var/lib/uga-bus`.

For a rollback, check out the previous Git commit in `/opt/uga-bus`, run the same build/migration steps, and `systemctl restart uga-bus`. Static GTFS data stays intact because it is outside the checkout.

## Logs, backup and restore

```bash
journalctl -u uga-bus --since '1 hour ago'
systemctl list-timers uga-bus-backup.timer
sudo -u uga-bus /opt/uga-bus/infra/backup.sh
```

The backup tool uses SQLite’s online `.backup`, verifies `PRAGMA integrity_check`, compresses, tests the gzip archive, and rotates backups older than 14 days. Copy archives off-server periodically; rotation never makes a second location by itself.

To restore during an outage, stop the API, preserve the failed DB, decompress a verified archive to the persistent location, verify, then restart:

```bash
sudo systemctl stop uga-bus
sudo cp /var/lib/uga-bus/uga_bus.sqlite3 /var/lib/uga-bus/uga_bus.sqlite3.before-restore
sudo -u uga-bus sh -c 'gzip -cd /var/lib/uga-bus/backups/uga_bus-TIMESTAMP.sqlite3.gz > /var/lib/uga-bus/uga_bus.sqlite3'
sudo -u uga-bus sqlite3 /var/lib/uga-bus/uga_bus.sqlite3 'PRAGMA integrity_check;'
sudo systemctl start uga-bus
```

## Capacity

SQLite plus a few polls per minute is intentionally modest for the first deployment. Start with standard Nest storage/RAM. Request additional storage only when `python -m uga_bus.cli db-stats` and backup growth show that the configured 180-day detailed observation retention will exceed available disk. Memory use is bounded by the latest live feed plus static GTFS cache/query objects; no Redis, Kafka, ML service, or cluster is required.
