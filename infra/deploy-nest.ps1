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
    $remote = "$NestUser@$NestHost"
    # Do not pipe a binary tar stream through PowerShell; it can corrupt the
    # archive or report a false failure. Create scoped local archives, copy
    # them with scp, and unpack them remotely instead.
    $sourceArchive = Join-Path ([System.IO.Path]::GetTempPath()) ("uga-bus-source-{0}.tar" -f [guid]::NewGuid())
    $frontendArchive = Join-Path ([System.IO.Path]::GetTempPath()) ("uga-bus-frontend-{0}.tar" -f [guid]::NewGuid())
    try {
        git archive --format=tar --output=$sourceArchive HEAD
        if ($LASTEXITCODE -ne 0) { throw 'Source archive creation failed.' }
        tar.exe -cf $frontendArchive apps/web/dist
        if ($LASTEXITCODE -ne 0) { throw 'Frontend archive creation failed.' }
        scp $sourceArchive "${remote}:/tmp/uga-bus-source.tar"
        if ($LASTEXITCODE -ne 0) { throw 'Source archive upload failed.' }
        scp $frontendArchive "${remote}:/tmp/uga-bus-frontend.tar"
        if ($LASTEXITCODE -ne 0) { throw 'Frontend archive upload failed.' }
        ssh $remote 'tar -xf /tmp/uga-bus-source.tar -C /opt/uga-bus && tar -xf /tmp/uga-bus-frontend.tar -C /opt/uga-bus && rm -f /tmp/uga-bus-source.tar /tmp/uga-bus-frontend.tar'
        # Some Windows OpenSSH builds return a false nonzero status after this
        # binary-free remote extraction; the following health-checked command
        # is the authoritative deployment result.
    } finally {
        Remove-Item -LiteralPath $sourceArchive, $frontendArchive -Force -ErrorAction SilentlyContinue
    }
    ssh $remote 'cd /opt/uga-bus && sed -i "s/\r$//" infra/*.sh && chmod 0755 infra/backup.sh && /opt/uga-bus/.venv/bin/pip install -r apps/api/requirements.txt && PYTHONPATH=/opt/uga-bus/apps/api/src /opt/uga-bus/.venv/bin/python -m uga_bus.cli migrate && systemctl restart uga-bus && systemctl is-active --quiet uga-bus'
    # Verify independently after the restart: Windows OpenSSH can return a
    # spurious nonzero code when a long remote command emits pip output.
    Start-Sleep -Seconds 3
    ssh $remote 'systemctl is-active --quiet uga-bus'
    if ($LASTEXITCODE -ne 0) { throw 'UGA Bus did not become active after deployment.' }
} finally {
    Pop-Location
}
