<#
.SYNOPSIS
    Shared helpers for bootstrap/*.ps1 -- dot-source, do not execute directly.

.DESCRIPTION
    Every top-level bootstrap script dot-sources this file immediately after
    setting $ErrorActionPreference / Set-StrictMode:

        . (Join-Path $PSScriptRoot "_common.ps1")

    It defines the Ok/Warn/Section/Fail console helpers, the ftx-mcp
    task-name/port constants, and the handful of functions that were
    previously duplicated byte-for-byte across setup.ps1,
    install-chrome-cdp.ps1, issue-token.ps1, revoke-token.ps1, services.ps1,
    and uninstall.ps1.

    bootstrap/optional/updatesvc/*.ps1 (a separate, not-yet-wired deploy
    path per setup.ps1's own docstring) is intentionally NOT converted to
    dot-source this file in this pass -- see the PR notes for the scope
    boundary.
#>

# --- Task-name / port constants --------------------------------------------
# Defaults only. setup.ps1's OPTIX_HTTP_PORT / OPTIX_MCP_PORT env-override
# logic (used by install-smoke runs that redirect ports) reads the env var
# first and falls back to these constants -- it keeps taking precedence,
# never the other way around.
$Script:FtxTaskName    = "ftx-mcp"
$Script:FtxCdpTaskName = "ftx-mcp-chrome-cdp"
$Script:FtxHttpPort    = 8765
$Script:FtxMcpPort     = 8766
$Script:FtxCdpPort     = 9222

# --- Console helpers ---------------------------------------------------------
function Section($name) {
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
}

function Ok($msg) {
    Write-Host "ok: $msg" -ForegroundColor Green
}

function Warn($msg) {
    Write-Host "WARN: $msg" -ForegroundColor Yellow
}

function Fail($msg) {
    Write-Host "FAIL: $msg" -ForegroundColor Red
    exit 1
}

# --- MSIX-packaged-shell guard -----------------------------------------------
function Assert-NotPackagedShell {
    # Refuses to continue when running inside an MSIX-packaged shell (e.g.
    # the Microsoft Store build of Claude Desktop). Writes to %LOCALAPPDATA%
    # from a packaged process are virtualized into the app's private
    # LocalCache overlay: in-shell checks see the merged view and pass, but
    # the ftx-mcp scheduled tasks run OUTSIDE the package against the real
    # filesystem, where none of those writes exist. The service self-creates
    # its state dirs at startup, but token/secret writes have no such
    # recovery, so the only safe behavior is to refuse and point at a
    # regular shell.
    #
    # Existence-guarded: Add-Type -Name/-Namespace defines a new in-memory
    # type and throws if called twice in the same process, which would
    # happen if _common.ps1 is dot-sourced more than once in one PowerShell
    # session.
    if (-not ([System.Management.Automation.PSTypeName]'FtxCommon.PkgIdentity').Type) {
        $pkgSig = @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
public static extern int GetCurrentPackageFullName(ref uint length, System.Text.StringBuilder fullName);
'@
        Add-Type -MemberDefinition $pkgSig -Name PkgIdentity -Namespace FtxCommon
    }
    $pkgLen = [uint32]0
    # 15700 = APPMODEL_ERROR_NO_PACKAGE -> unpackaged process, safe to proceed
    if ([FtxCommon.PkgIdentity]::GetCurrentPackageFullName([ref]$pkgLen, $null) -ne 15700) {
        Fail ("This shell is running inside an MSIX-packaged app (e.g. the Microsoft " +
              "Store build of Claude Desktop). Its writes to %LOCALAPPDATA% are " +
              "virtualized into the app's private LocalCache and invisible to the " +
              "ftx-mcp scheduled tasks. Re-run from a regular PowerShell window.")
    }
}

# --- DPAPI tokens.json.dpapi read/write --------------------------------------
Add-Type -AssemblyName System.Security

function Read-FtxTokensBlob($tokensBlob) {
    # Returns "" (not an error) when the blob doesn't exist yet -- a first
    # install has no tokens to decrypt. Callers that need "file not present
    # yet" to be its own friendlier case (revoke-token.ps1 -List on a fresh
    # box) still do their own Test-Path check before calling this.
    $plaintext = ""
    if (Test-Path $tokensBlob) {
        try {
            $cipher = [System.IO.File]::ReadAllBytes($tokensBlob)
            $bytes  = [System.Security.Cryptography.ProtectedData]::Unprotect(
                $cipher, $null, 'CurrentUser')
            $plaintext = [System.Text.Encoding]::UTF8.GetString($bytes)
        } catch {
            Write-Host "FAIL: could not decrypt $tokensBlob - wrong Windows user, different machine, or corrupt file?" -ForegroundColor Red
            Write-Host $_.Exception.Message -ForegroundColor Red
            exit 1
        }
    }
    return $plaintext
}

function Write-FtxTokensBlob($tokensBlob, $newPayload) {
    # Assert-NotPackagedShell lives HERE (not just at each caller's top) so
    # every current and future writer of tokens.json.dpapi is covered
    # automatically -- this closes the gap revoke-token.ps1 had: issue-token.ps1
    # guarded itself inline, but revoke-token.ps1 did the identical
    # decrypt-mutate-write-back with no guard at all.
    Assert-NotPackagedShell
    $newBytes  = [System.Text.Encoding]::UTF8.GetBytes($newPayload)
    $newCipher = [System.Security.Cryptography.ProtectedData]::Protect(
        $newBytes, $null, 'CurrentUser')
    $tempBlob  = "$tokensBlob.tmp"
    [System.IO.File]::WriteAllBytes($tempBlob, $newCipher)
    Move-Item -Path $tempBlob -Destination $tokensBlob -Force
}

# --- Chrome discovery ---------------------------------------------------------
function Find-FtxChrome {
    # Returns $null on miss; callers decide Fail vs Warn (setup.ps1 needs
    # -NoCdp-aware branching, install-chrome-cdp.ps1 just Warns and returns).
    $chromePaths = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    )
    return $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}

# --- Scheduled-task settings --------------------------------------------------
function New-FtxTaskSettings {
    # Restart-on-failure + unlimited ExecutionTimeLimit: the Task Scheduler
    # default (72h) silently kills a long-lived service/chrome mid-week
    # (field-validated fix). Identical block shared by setup.ps1 (main
    # service task) and install-chrome-cdp.ps1 (CDP chrome task).
    New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
}
