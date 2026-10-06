param([ValidateSet('start','stop','status','logs','rebuild')][string]$Action = 'start')
Set-Location -LiteralPath $PSScriptRoot
switch ($Action) {
    'start' { docker compose -f compose.local.yml up -d }
    'stop' { docker compose -f compose.local.yml stop }
    'status' { docker compose -f compose.local.yml ps }
    'logs' { docker compose -f compose.local.yml logs --tail 100 -f }
    'rebuild' { docker compose -f compose.local.yml up -d --build }
}
exit $LASTEXITCODE
