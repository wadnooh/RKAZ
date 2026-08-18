param(
    [ValidateSet("status", "verify", "install", "run", "network", "permissions", "deploy", "domain")]
    [string]$Mode = "status",

    [ValidateSet("list", "add-permission", "grant-role", "revoke-role", "set-user-role", "list-users", "list-user-permissions", "allow-user-permission", "deny-user-permission", "clear-user-permission")]
    [string]$PermissionAction = "list",
    [string]$Permission,
    [string]$Label,
    [string]$Role,
    [string]$User,
    [string]$DbPath,

    [string]$Port = "5070",
    [switch]$OpenBrowser,
    [switch]$SkipInstall,

    [string]$DeployHost,
    [string]$DeployPath = "/opt/rekaz",
    [string]$DeployBranch = "main",
    [string]$DeployUser = "rekazapp",
    [string]$ServiceName = "rekaz",
    [string]$PrimaryDomain = "report.ralenjaz.com",
    [string]$LegacyDomain = "rekaz.wadnooh.com",
    [string]$CertbotEmail = "admin@wadnooh.com",
    [switch]$PushBeforeDeploy,
    [switch]$ConfigureDomain,
    [switch]$RollbackDomain,
    [switch]$NoRestart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # Older hosts can ignore this; the commands still work.
}

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$PermissionsScript = Join-Path $PSScriptRoot "manage_permissions.ps1"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-PythonCommand {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return "python" }
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { return "py" }
    throw "Python is required. Install Python or add it to PATH."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)] [scriptblock]$Command,
        [string]$FailMessage = "Command failed"
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$FailMessage. Exit code: $LASTEXITCODE"
    }
}

function Invoke-Install {
    $requirements = Join-Path $Root "requirements.txt"
    if (-not (Test-Path -LiteralPath $requirements)) {
        throw "requirements.txt was not found under $Root"
    }
    $python = Get-PythonCommand
    Write-Step "Installing Python requirements"
    Invoke-Checked { & $python -m pip install -r $requirements } "pip install failed"
}

function Invoke-Verify {
    $python = Get-PythonCommand
    if (-not $env:SECRET_KEY) {
        $env:SECRET_KEY = "rekaz-local-verification"
    }
    $code = @'
import ast
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
errors = []

for rel in ("webapp/permissions.py", "webapp/app.py"):
    path = root / rel
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{rel}: {exc}")

try:
    from jinja2 import Environment
    env = Environment()
    for path in sorted((root / "webapp" / "templates").rglob("*.html")):
        env.parse(path.read_text(encoding="utf-8"))
except Exception as exc:
    errors.append(f"templates: {exc}")

if errors:
    print("Verification failed:")
    for error in errors:
        print(" - " + error)
    raise SystemExit(1)

print("Verification OK")
'@
    $tmp = Join-Path $env:TEMP ("rekaz-verify-{0}.py" -f ([guid]::NewGuid().ToString("N")))
    Set-Content -LiteralPath $tmp -Value $code -Encoding UTF8
    try {
        Write-Step "Verifying Python and Jinja templates"
        Invoke-Checked { & $python -B $tmp $Root } "verification failed"
    }
    finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Permissions {
    if (-not (Test-Path -LiteralPath $PermissionsScript)) {
        throw "Missing permissions script: $PermissionsScript"
    }
    $psArgs = @(
        "-ExecutionPolicy", "Bypass",
        "-File", $PermissionsScript,
        "-Action", $PermissionAction,
        "-Root", $Root
    )
    if ($Permission) { $psArgs += @("-Permission", $Permission) }
    if ($Label) { $psArgs += @("-Label", $Label) }
    if ($Role) { $psArgs += @("-Role", $Role) }
    if ($User) { $psArgs += @("-User", $User) }
    if ($DbPath) { $psArgs += @("-DbPath", $DbPath) }
    Write-Step "Running permissions action: $PermissionAction"
    Invoke-Checked { & powershell @psArgs } "permissions action failed"
}

function Invoke-RunApp {
    param([bool]$Network)
    if (-not $SkipInstall) {
        Invoke-Install
    }
    if (-not $env:SECRET_KEY) {
        $env:SECRET_KEY = "rekaz-local-dev-key"
    }
    $env:PORT = $Port
    if ($Network) {
        $env:HOST = "0.0.0.0"
        $env:USE_WAITRESS = "1"
    } else {
        $env:HOST = "127.0.0.1"
        Remove-Item Env:\USE_WAITRESS -ErrorAction SilentlyContinue
    }

    $urlHost = if ($Network) { "127.0.0.1" } else { $env:HOST }
    $url = "http://$urlHost`:$Port"
    if ($OpenBrowser) {
        Start-Process $url
    }
    $python = Get-PythonCommand
    Write-Step "Starting Rekaz at $url"
    Push-Location $Root
    try {
        Invoke-Checked { & $python -m webapp.app } "application stopped with an error"
    }
    finally {
        Pop-Location
    }
}

function Assert-RemoteValue {
    param(
        [Parameter(Mandatory = $true)] [string]$Name,
        [Parameter(Mandatory = $true)] [string]$Value
    )
    if ($Value -notmatch '^[A-Za-z0-9._@:/-]+$') {
        throw "$Name contains unsupported characters for remote deployment: $Value"
    }
}

function Get-DomainCommand {
    if ($RollbackDomain) {
        Assert-RemoteValue -Name "PrimaryDomain" -Value $LegacyDomain
        Assert-RemoteValue -Name "LegacyDomain" -Value $PrimaryDomain
        Assert-RemoteValue -Name "CertbotEmail" -Value $CertbotEmail
        Assert-RemoteValue -Name "DeployPath" -Value $DeployPath
        return "sudo PRIMARY_DOMAIN=$LegacyDomain SECONDARY_DOMAIN=$PrimaryDomain CERTBOT_EMAIL=$CertbotEmail bash $DeployPath/tools/rollback_to_rekaz_wadnooh.sh"
    }

    Assert-RemoteValue -Name "PrimaryDomain" -Value $PrimaryDomain
    Assert-RemoteValue -Name "LegacyDomain" -Value $LegacyDomain
    Assert-RemoteValue -Name "CertbotEmail" -Value $CertbotEmail
    Assert-RemoteValue -Name "DeployPath" -Value $DeployPath
    return "sudo PRIMARY_DOMAIN=$PrimaryDomain LEGACY_DOMAIN=$LegacyDomain CERTBOT_EMAIL=$CertbotEmail bash $DeployPath/tools/finish_report_ralenjaz_ssl.sh"
}

function Invoke-Domain {
    if (-not $DeployHost) {
        Write-Host ""
        Write-Host "DeployHost is required for domain configuration."
        Write-Host "Examples:"
        Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode domain -DeployHost root@191.101.2.59"
        Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode domain -DeployHost root@191.101.2.59 -PrimaryDomain report.ralenjaz.com -LegacyDomain rekaz.wadnooh.com"
        Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode domain -DeployHost root@191.101.2.59 -RollbackDomain"
        return
    }
    $remoteCommand = Get-DomainCommand
    Write-Step "Configuring domain on $DeployHost"
    Invoke-Checked { & ssh $DeployHost $remoteCommand } "domain configuration failed"
}

function Invoke-Deploy {
    Invoke-Verify
    if ($PushBeforeDeploy) {
        Write-Step "Pushing current branch to origin/$DeployBranch"
        Push-Location $Root
        try {
            Invoke-Checked { & git push origin $DeployBranch } "git push failed"
        }
        finally {
            Pop-Location
        }
    }
    if (-not $DeployHost) {
        Write-Host ""
        Write-Host "DeployHost is required for live deployment."
        Write-Host "Example:"
        Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode deploy -DeployHost root@191.101.2.59 -PushBeforeDeploy -ConfigureDomain"
        return
    }

    $remote = @(
        "cd $DeployPath",
        "chown -R $DeployUser`:$DeployUser $DeployPath/.git",
        "sudo -u $DeployUser git pull origin $DeployBranch",
        "sudo -u $DeployUser $DeployPath/.venv/bin/pip install -r requirements.txt"
    )
    if (-not $NoRestart) {
        $remote += "sudo systemctl restart $ServiceName"
        $remote += "systemctl status $ServiceName --no-pager"
    }
    if ($ConfigureDomain -or $RollbackDomain) {
        $remote += (Get-DomainCommand)
    }
    $remoteCommand = $remote -join " && "

    Write-Step "Deploying to $DeployHost"
    Invoke-Checked { & ssh $DeployHost $remoteCommand } "remote deployment failed"
}

function Show-Status {
    Write-Host "Rekaz unified PowerShell tool"
    Write-Host "Project: $Root"
    Write-Host ""
    Write-Host "Common commands:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode verify"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode permissions -PermissionAction list"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode run -OpenBrowser"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode network -OpenBrowser"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode deploy -DeployHost root@191.101.2.59 -PushBeforeDeploy -ConfigureDomain"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode domain -DeployHost root@191.101.2.59"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\rekaz.ps1`" -Mode domain -DeployHost root@191.101.2.59 -RollbackDomain"
}

switch ($Mode) {
    "status" { Show-Status }
    "verify" { Invoke-Verify }
    "install" { Invoke-Install }
    "run" { Invoke-RunApp -Network $false }
    "network" { Invoke-RunApp -Network $true }
    "permissions" { Invoke-Permissions }
    "deploy" { Invoke-Deploy }
    "domain" { Invoke-Domain }
}
