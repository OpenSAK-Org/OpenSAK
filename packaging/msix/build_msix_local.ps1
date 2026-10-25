<#
.SYNOPSIS
    Prototype MSIX packaging for OpenSAK (issue #786, step 1).
    Builds the PyInstaller dist, wraps it in an MSIX package using
    makeappx.exe (Windows SDK), signs it with a local self-signed test
    certificate, and optionally installs it for a Gatekeeper-style
    local sideload check.

.DESCRIPTION
    This is LOCAL PROTOTYPE tooling only - nothing here touches CI or
    opensak.spec. It exists to answer the open risk in #786 before any
    further MSIX work: does a package containing QtWebEngine even
    install and run correctly, before spending time on Partner Center
    submission or CI integration.

    Run from repo root on Windows, with the "MSIX Packaging Tool" or the
    Windows 10/11 SDK installed (both ship makeappx.exe and
    signtool.exe - see README.md for install links).

.NOTES
    Does NOT create or touch a Partner Center submission. That's a
    manual step in the browser - see packaging/msix/README.md step 2.
#>

[CmdletBinding()]
param(
    [string]$IdentityName = "OpenSAKDevTest.OpenSAK",
    [string]$PublisherCN = "CN=OpenSAK Dev Test",
    [string]$PublisherDisplayName = "OpenSAK Dev Test",
    [string]$DisplayName = "OpenSAK",
    [string]$Version = "1.17.2.0",
    # Command name of the console program's App Execution Alias (#937).
    # Default: "opensak-beta.exe" when -DisplayName ends in "Beta", else
    # "opensak.exe" - so a beta and a stable install don't claim the same
    # name (README.md "Command line (App Execution Alias)").
    [string]$CliAlias = "",
    [switch]$SkipBuild,
    [switch]$InstallAfterBuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $RepoRoot

$MsixDir = "$RepoRoot\packaging\msix"
$StagingDir = "$RepoRoot\build\msix-staging"
$OutputMsix = "$RepoRoot\dist\OpenSAK-$Version-prototype.msix"
# opensak.spec builds both: the windowed app and its console twin (#937).
$RequiredExes = @("opensak.exe", "opensak-cli.exe")
if (-not $CliAlias) {
    $CliAlias = if ($DisplayName -match "Beta$") { "opensak-beta.exe" } else { "opensak.exe" }
}

# ------------------------------------------------------------------
# 1. Build the PyInstaller dist (same as build.yml's Windows job)
# ------------------------------------------------------------------
if (-not $SkipBuild) {
    Write-Host "==> Building with PyInstaller..." -ForegroundColor Cyan
    python -m pip install --upgrade pip
    pip install -e . pyinstaller
    python scripts\fetch_boundary_baseline.py
    pyinstaller opensak.spec --clean --noconfirm
    foreach ($exe in $RequiredExes) {
        if (-not (Test-Path "$RepoRoot\dist\OpenSAK\$exe")) {
            throw "PyInstaller build did not produce dist\OpenSAK\$exe"
        }
    }
} else {
    Write-Host "==> Skipping PyInstaller build (-SkipBuild)" -ForegroundColor Yellow
    foreach ($exe in $RequiredExes) {
        if (-not (Test-Path "$RepoRoot\dist\OpenSAK\$exe")) {
            throw "dist\OpenSAK\$exe not found - remove -SkipBuild to build it first."
        }
    }
}

# ------------------------------------------------------------------
# 2. Generate image assets from the existing app icon
# ------------------------------------------------------------------
Write-Host "==> Generating MSIX image assets..." -ForegroundColor Cyan
python packaging\msix\generate_assets.py

# ------------------------------------------------------------------
# 3. Assemble the MSIX staging layout
# ------------------------------------------------------------------
Write-Host "==> Assembling staging directory..." -ForegroundColor Cyan
if (Test-Path $StagingDir) { Remove-Item $StagingDir -Recurse -Force }
New-Item -ItemType Directory -Path $StagingDir | Out-Null
New-Item -ItemType Directory -Path "$StagingDir\OpenSAK" | Out-Null
New-Item -ItemType Directory -Path "$StagingDir\assets" | Out-Null

Copy-Item "$RepoRoot\dist\OpenSAK\*" "$StagingDir\OpenSAK\" -Recurse
Copy-Item "$MsixDir\assets\*.png" "$StagingDir\assets\"

$manifest = Get-Content "$MsixDir\AppxManifest.xml.template" -Raw
$manifest = $manifest.Replace("__IDENTITY_NAME__", $IdentityName)
$manifest = $manifest.Replace("__PUBLISHER_CN__", $PublisherCN)
$manifest = $manifest.Replace("__PUBLISHER_DISPLAY_NAME__", $PublisherDisplayName)
$manifest = $manifest.Replace("__DISPLAY_NAME__", $DisplayName)
$manifest = $manifest.Replace("__VERSION__", $Version)
$manifest = $manifest.Replace("__EXE_RELATIVE_PATH__", "OpenSAK\opensak.exe")
$manifest = $manifest.Replace("__CLI_EXE_RELATIVE_PATH__", "OpenSAK\opensak-cli.exe")
$manifest = $manifest.Replace("__CLI_ALIAS__", $CliAlias)
Write-Host "    Command-line alias: $CliAlias" -ForegroundColor Cyan
Set-Content -Path "$StagingDir\AppxManifest.xml" -Value $manifest -Encoding UTF8

# ------------------------------------------------------------------
# 4. Package with makeappx.exe
# ------------------------------------------------------------------
Write-Host "==> Packaging with makeappx.exe..." -ForegroundColor Cyan
$makeappx = Get-Command makeappx.exe -ErrorAction SilentlyContinue
if (-not $makeappx) {
    throw "makeappx.exe not found on PATH. Install the Windows SDK (or MSIX " +
          "Packaging Tool) and re-run - see packaging/msix/README.md."
}
if (Test-Path $OutputMsix) { Remove-Item $OutputMsix -Force }
New-Item -ItemType Directory -Path "$RepoRoot\dist" -Force | Out-Null
& makeappx.exe pack /d $StagingDir /p $OutputMsix
if ($LASTEXITCODE -ne 0) { throw "makeappx.exe failed with exit code $LASTEXITCODE" }

# ------------------------------------------------------------------
# 5. Sign with a local self-signed test certificate
# ------------------------------------------------------------------
Write-Host "==> Signing package for local sideload testing..." -ForegroundColor Cyan
$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
    throw "signtool.exe not found on PATH. Install the Windows SDK and re-run."
}

$certSubject = $PublisherCN
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq $certSubject } | Select-Object -First 1
if (-not $cert) {
    Write-Host "    No matching test certificate found - creating one (self-signed, CurrentUser\My)." -ForegroundColor Yellow
    $cert = New-SelfSignedCertificate -Type Custom -Subject $certSubject `
        -KeyUsage DigitalSignature -FriendlyName "OpenSAK MSIX Dev Test" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
    Write-Host "    NOTE: for Add-AppxPackage to succeed, this certificate also needs" -ForegroundColor Yellow
    Write-Host "    to be trusted - see README.md step 1c (import into Trusted People)." -ForegroundColor Yellow
}

& signtool.exe sign /fd SHA256 /a /s My /sha1 $cert.Thumbprint $OutputMsix
if ($LASTEXITCODE -ne 0) { throw "signtool.exe failed with exit code $LASTEXITCODE" }

Write-Host "==> Built and signed: $OutputMsix" -ForegroundColor Green

# ------------------------------------------------------------------
# 6. Optional: install locally to sanity-check it launches
# ------------------------------------------------------------------
if ($InstallAfterBuild) {
    Write-Host "==> Installing locally (Add-AppxPackage)..." -ForegroundColor Cyan
    Add-AppxPackage -Path $OutputMsix
    Write-Host "==> Installed. Launch OpenSAK from the Start Menu to verify it runs," -ForegroundColor Green
    Write-Host "    and specifically check the map tab (QtWebEngine) loads correctly." -ForegroundColor Green
} else {
    Write-Host "==> Skipping install. Re-run with -InstallAfterBuild to sideload-test," -ForegroundColor Yellow
    Write-Host "    or manually: Add-AppxPackage -Path `"$OutputMsix`"" -ForegroundColor Yellow
}
