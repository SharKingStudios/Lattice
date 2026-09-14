param(
    [Parameter(Mandatory = $true)] [string] $NestHost,
    [string] $NestUser = 'root'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repo
try {
    npm ci
    npm run web:build
    git archive --format=tar HEAD | ssh "$NestUser@$NestHost" 'mkdir -p /opt/uga-bus && tar -xf - -C /opt/uga-bus'
    tar -cf - apps/web/dist | ssh "$NestUser@$NestHost" 'tar -xf - -C /opt/uga-bus'
    ssh "$NestUser@$NestHost" 'cd /opt/uga-bus && /opt/uga-bus/.venv/bin/pip install -r apps/api/requirements.txt && PYTHONPATH=/opt/uga-bus/apps/api/src /opt/uga-bus/.venv/bin/python -m uga_bus.cli migrate && systemctl restart uga-bus && systemctl is-active --quiet uga-bus'
} finally {
    Pop-Location
}
