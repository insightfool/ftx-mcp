<#
.SYNOPSIS
    Issue a new bearer token for ftx-mcp. DPAPI-encrypted at rest.

.DESCRIPTION
    Generates a fresh bearer secret via the service's `auth.generate_token`
    helper, appends the SHA-256 hash to %LOCALAPPDATA%\ftx-mcp\secrets\
    tokens.json.dpapi (DPAPI / CurrentUser), and prints the bearer secret
    to stdout EXACTLY ONCE. The secret is never persisted.

    Heavy lifting (JSON manipulation, scope enforcement, hash) lives in
    `service._token_admin`; this script only handles DPAPI wrap/unwrap on
    disk so the security-sensitive code is under pytest.

    The service hot-reloads tokens.json.dpapi on mtime change, so issued
    tokens are usable without a service restart.

.PARAMETER Label
    Human-readable label that shows up in `revoke-token.ps1 -List`.
    Required.

.PARAMETER Scope
    One of: health | read | author | deploy. Per design SS1.2:
        health  - health checks only
        read    - read endpoints (project list, git log, deploy tail)
        author  - live authoring: mutate the Studio project, preview in the
                  emulator, drive the canvas (no runtime push). The routine
                  token for day-to-day HMI editing.
        deploy  - everything, including pushing to / controlling the runtime.
    Required. (Coupled to service.auth._SCOPE_ORDER; keep in sync -- this
    ValidateSet cannot import the Python scope model.)

.PARAMETER ExpiresInDays
    Optional integer; if set, the token expires N days from now.
    Omit for a token that never expires.

.PARAMETER RepoRoot
    Path to the ftx-mcp checkout (default: parent of this script).

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\bootstrap\issue-token.ps1 `
        -Label "claude-code-laptop" -Scope deploy

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\bootstrap\issue-token.ps1 `
        -Label "monitor-bot" -Scope read -ExpiresInDays 30
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Label,
    [Parameter(Mandatory=$true)][ValidateSet("health","read","author","deploy")][string]$Scope,
    [int]$ExpiresInDays,
    [string]$RepoRoot,
    # Machine-readable output: suppress the human banner and emit a single JSON
    # object {id,label,scope,bearer} on stdout so automation (setup-mcp-client.ps1)
    # can capture the bearer without scraping the banner. The secret is still only
    # emitted once - pipe it straight into the consumer, don't log it.
    [switch]$Json
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "_common.ps1")

# Refuse to run inside an MSIX-packaged shell before doing any work (see
# _common.ps1's Assert-NotPackagedShell for the full rationale: packaged-shell
# writes are virtualized and the DPAPI token blob would be invisible to the
# real ftx-mcp service).
Assert-NotPackagedShell

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
if (-not (Test-Path $secretsDir)) {
    New-Item -ItemType Directory -Path $secretsDir -Force | Out-Null
}

# Step 1: read + decrypt existing tokens (if any). Empty input = first install.
$plaintext = Read-FtxTokensBlob $tokensBlob

# Step 2: hand off to _token_admin add. Pipe decrypted JSON in, get the new
# payload + bearer secret out. ExpiresInDays is converted to an absolute
# ISO8601-Z timestamp here so the helper does not need a notion of "now".
$adminArgs = @("-m", "service._token_admin", "add", "--label", $Label, "--scope", $Scope)
if ($PSBoundParameters.ContainsKey("ExpiresInDays")) {
    $expires = (Get-Date).ToUniversalTime().AddDays($ExpiresInDays).ToString("yyyy-MM-ddTHH:mm:ssZ")
    $adminArgs += @("--expires-at", $expires)
}

Push-Location $RepoRoot
try {
    $stdout = $plaintext | & $venvPython @adminArgs
} finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: service._token_admin add returned $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

# Step 3: parse helper output, re-encrypt the new payload, write back atomically.
$result      = $stdout | ConvertFrom-Json
$newPayload  = ($result.payload | ConvertTo-Json -Depth 10 -Compress)
$bearer      = $result.bearer
$tokenId     = $result.id

Write-FtxTokensBlob $tokensBlob $newPayload

# Step 4a: machine-readable mode - emit the token as one JSON object on stdout
# and stop. For automation (setup-mcp-client.ps1) that wires the bearer straight
# into a client config without a human copy step.
if ($Json) {
    [pscustomobject]@{ id = $tokenId; label = $Label; scope = $Scope; bearer = $bearer } |
        ConvertTo-Json -Compress
    exit 0
}

# Step 4: surface the bearer secret EXACTLY ONCE. Conspicuous separator so
# the operator does not accidentally truncate or miss it in scrollback.
Write-Host ""
Write-Host "================ BEARER TOKEN - copy now ================" -ForegroundColor Yellow
Write-Host "  id:     $tokenId" -ForegroundColor Yellow
Write-Host "  label:  $Label" -ForegroundColor Yellow
Write-Host "  scope:  $Scope" -ForegroundColor Yellow
Write-Host "  bearer: $bearer" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Yellow
Write-Host "This is the ONLY time the bearer secret is shown." -ForegroundColor Yellow
Write-Host "Store it in your MCP client config or a password manager." -ForegroundColor Yellow
Write-Host "To revoke later: revoke-token.ps1 -Id $tokenId" -ForegroundColor Yellow
