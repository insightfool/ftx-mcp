<#
.SYNOPSIS
    Revoke (delete) a bearer token, or list issued tokens.

.DESCRIPTION
    Decrypts %LOCALAPPDATA%\ftx-mcp\secrets\tokens.json.dpapi, removes
    the entry matching -Id, and re-encrypts. The service hot-reloads on
    mtime change, so the revoked token stops working without a restart.

    Pair with `-List` to see what's issued (id, label, scope, expiry).
    The bearer hash is intentionally NOT printed - it's secret-adjacent
    and there is no operator workflow that needs it.

.PARAMETER Id
    Token id (ULID hex) to revoke. Use `-List` to find it. Required
    unless `-List` is set.

.PARAMETER List
    Print issued tokens and exit; do not modify the file. Convenient
    pre-flight before revoking.

.PARAMETER RepoRoot
    Path to the ftx-mcp checkout (default: parent of this script).

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\bootstrap\revoke-token.ps1 -List

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\bootstrap\revoke-token.ps1 `
        -Id 9f2c3d44e1f04b...
#>
[CmdletBinding(DefaultParameterSetName="Revoke")]
param(
    [Parameter(ParameterSetName="Revoke", Mandatory=$true)][string]$Id,
    [Parameter(ParameterSetName="List",   Mandatory=$true)][switch]$List,
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "_common.ps1")

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$state       = Join-Path $env:LOCALAPPDATA $Script:FtxTaskName
$secretsDir  = Join-Path $state "secrets"
$tokensBlob  = Join-Path $secretsDir "tokens.json.dpapi"
$venvPython  = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "FAIL: venv python not found at $venvPython. Run setup.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $tokensBlob)) {
    Write-Host "No tokens issued yet (file not found: $tokensBlob)." -ForegroundColor Yellow
    exit 0
}

# Decrypt the existing blob.
$plaintext = Read-FtxTokensBlob $tokensBlob

if ($List) {
    Push-Location $RepoRoot
    try {
        $stdout = $plaintext | & $venvPython -m service._token_admin list
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAIL: service._token_admin list returned $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
    $listing = $stdout | ConvertFrom-Json
    if ($listing.tokens.Count -eq 0) {
        Write-Host "(no tokens)" -ForegroundColor Yellow
    } else {
        $listing.tokens | Format-Table -AutoSize id, label, scope, created_at, expires_at, last_seen_at | Out-String | Write-Host
    }
    exit 0
}

# Revoke path.
Push-Location $RepoRoot
try {
    $stdout = $plaintext | & $venvPython -m service._token_admin remove --id $Id
} finally {
    Pop-Location
}
if ($LASTEXITCODE -eq 2) {
    Write-Host "FAIL: no token with id $Id. Run with -List to see issued tokens." -ForegroundColor Red
    exit 1
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: service._token_admin remove returned $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

$result      = $stdout | ConvertFrom-Json
$newPayload  = ($result.payload | ConvertTo-Json -Depth 10 -Compress)
Write-FtxTokensBlob $tokensBlob $newPayload

Write-Host "Revoked token id $Id." -ForegroundColor Green
Write-Host "Service hot-reloads on mtime change; the revoked token stops working without a restart." -ForegroundColor Cyan
