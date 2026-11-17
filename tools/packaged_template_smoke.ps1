# Issue #5: real Inno installer lifecycle + synthetic BTW preservation on disposable CI.
# Never execute this on a production Windows workstation.
param(
    [Parameter(Mandatory = $true)][string] $Installer,
    [string] $PreviousInstaller = '',
    [string] $PreviousVersion = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or
    -not $env:RUNNER_TEMP -or -not $env:GITHUB_RUN_ID) {
    throw 'Packaged smoke is restricted to an ephemeral GitHub-hosted Windows runner.'
}

$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$previousInstallerPath = ''
if ($PreviousInstaller) {
    $previousInstallerPath = (Resolve-Path -LiteralPath $PreviousInstaller).Path
    if (-not $PreviousVersion) {
        throw 'PreviousVersion is required with PreviousInstaller'
    }
} elseif ($PreviousVersion) {
    throw 'PreviousVersion without PreviousInstaller is not allowed'
}
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$workRoot = Join-Path $env:RUNNER_TEMP ("autoprint-btw-smoke-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT")
if (Test-Path -LiteralPath $workRoot) {
    throw "Refusing reused smoke root: $workRoot"
}

# The real installer AppId is used. A pre-existing install is a hard stop.
$uninstallKeys = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
)
foreach ($key in $uninstallKeys) {
    if (-not (Test-Path $key)) { continue }
    foreach ($entry in (Get-ChildItem $key -ErrorAction Stop)) {
        $info = Get-ItemProperty -LiteralPath $entry.PSPath -ErrorAction SilentlyContinue
        if ($info -and $info.PSObject.Properties.Match('DisplayName').Count -gt 0) {
            $name = $info.DisplayName
            if ($name -eq 'AutoPrint' -or $name -like 'AutoPrint version *') {
                throw "AutoPrint is already registered: $($entry.PSPath)"
            }
        }
    }
}

New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
$installDir = Join-Path $workRoot 'installed-app'
$userData = Join-Path $workRoot 'isolated-user-data'
$env:AUTOPRINT_PACKAGED_SMOKE = '1'
$env:AUTOPRINT_USER_DATA_DIR = $userData
$env:AUTOPRINT_SMOKE_ROOT = $workRoot
$env:AUTOPRINT_SMOKE_INSTALL = $installDir
Set-Location -LiteralPath $repositoryRoot
$actualSha = (& git rev-parse HEAD).Trim()
$expectedSha = $env:AUTOPRINT_SMOKE_EXPECT_SHA
if ($LASTEXITCODE -ne 0 -or -not $expectedSha -or $actualSha -ne $expectedSha) {
    throw "Smoke checkout mismatch: actual=$actualSha expected=$expectedSha"
}
$q = [char]34

function Invoke-Installer([string] $phase, [string] $sourceInstaller = $installerPath) {
    $log = Join-Path $workRoot "$phase.log"
    $argsText = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=$q$installDir$q /LOG=$q$log$q"
    $process = Start-Process -FilePath $sourceInstaller -ArgumentList $argsText -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Inno $phase failed with exit code $($process.ExitCode). See $log"
    }
}

function Assert-Installed([string] $expectedVersion) {
    $appExe = Join-Path $installDir 'AutoPrint.exe'
    if (-not (Test-Path -LiteralPath $appExe)) { throw "Installed EXE missing: $appExe" }
    $actualVersion = (Get-Item -LiteralPath $appExe).VersionInfo.ProductVersion
    if ([string]$actualVersion -ne $expectedVersion) {
        throw "Installed EXE version mismatch: expected=$expectedVersion actual=$actualVersion"
    }
    $bundled = Join-Path $installDir '_internal\bundled_templates'
    foreach ($file in @('16x16.btw', '30x20.btw', '58x60.btw')) {
        if (-not (Test-Path -LiteralPath (Join-Path $bundled $file))) {
            throw "Bundled template missing from installed package: $file"
        }
    }
}

function Get-UserSnapshot {
    if (-not (Test-Path -LiteralPath $userData)) {
        throw "User data directory missing: $userData"
    }
    $result = @{}
    foreach ($file in (Get-ChildItem -LiteralPath $userData -Recurse -File)) {
        $relative = [IO.Path]::GetRelativePath($userData, $file.FullName)
        $result[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    }
    if ($result.Count -lt 5) { throw "Incomplete synthetic fixture: $($result.Count) files" }
    return $result
}

function Assert-Snapshot([hashtable] $expected, [string] $phase) {
    $actual = Get-UserSnapshot
    if ($actual.Count -ne $expected.Count) {
        throw "User-data file count changed at $phase : $($expected.Count) -> $($actual.Count)"
    }
    foreach ($path in $expected.Keys) {
        if (-not $actual.ContainsKey($path) -or $actual[$path] -ne $expected[$path]) {
            throw "User BTW/metadata changed at $phase : $path"
        }
    }
    Write-Host "PASS $phase : $($expected.Count) persistent files preserved byte-for-byte"
}

$candidateVersion = (& python -c "from app_version import APP_VERSION; print(APP_VERSION)").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Failed to resolve candidate APP_VERSION' }
Write-Host "Packaged smoke: $installerPath"
Write-Host "Tested exact PR HEAD SHA: $actualSha"
if ($previousInstallerPath) {
    Write-Host "Cross-version smoke: v$PreviousVersion -> v$candidateVersion"
    Invoke-Installer 'fresh-install' $previousInstallerPath
    Assert-Installed $PreviousVersion
} else {
    Invoke-Installer 'fresh-install'
    Assert-Installed $candidateVersion
}
if (Test-Path -LiteralPath $userData) {
    throw 'Fresh installer unexpectedly created user-data storage before the app ran'
}

# The installed EXE is not launched: licensing, COM and print are out of scope.
# Exercise source-level storage against the same disposable profile and installed path.
@'
import os
from pathlib import Path
from template_storage import (
    bootstrap_template_registry, get_user_templates_dir, import_user_template,
    migrate_legacy_templates, read_template_registry, write_template_registry,
)

root = Path(os.environ["AUTOPRINT_SMOKE_ROOT"])
installed = Path(os.environ["AUTOPRINT_SMOKE_INSTALL"])
userdata = Path(os.environ["AUTOPRINT_USER_DATA_DIR"])
user = userdata / "templates"
assert Path(get_user_templates_dir()) == user
user.mkdir(parents=True)
(user / "16x16.btw").write_bytes(b"operator original collision")
(user / "sticker60x40.btw").write_bytes(b"operator sticker")
(user / "personal.btw").write_bytes(b"operator unique file")
incoming = root / "incoming"
incoming.mkdir()
(incoming / "16x16.btw").write_bytes(b"incoming different 16x16")
choice = import_user_template(incoming / "16x16.btw", user, "16x16.btw")
assert choice.name.startswith("16x16.import-")
assert choice.read_bytes() == b"incoming different 16x16"
assert (user / "16x16.btw").read_bytes() == b"operator original collision"
assert import_user_template(incoming / "16x16.btw", user, "16x16.btw") == choice

old = root / "legacy" / "BarTenderGUI" / "templates"
in_install = installed / "templates"
old.mkdir(parents=True)
in_install.mkdir(parents=True)
(old / "16x16.btw").write_bytes(b"bar-tender legacy")
(in_install / "16x16.btw").write_bytes(b"installed legacy")
(old / "legacy-only.btw").write_bytes(b"unique legacy")
first = migrate_legacy_templates(destination_dir=user, legacy_dirs=[old, in_install])
second = migrate_legacy_templates(destination_dir=user, legacy_dirs=[old, in_install])
assert all(x["status"] != "error" for x in first + second)
assert len(list(user.glob("16x16.legacy-*.btw"))) == 2
assert (user / "legacy-only.btw").read_bytes() == b"unique legacy"
assert (old / "16x16.btw").read_bytes() == b"bar-tender legacy"
assert (in_install / "16x16.btw").read_bytes() == b"installed legacy"

labels = {"16x16": {
    "id": "16x16", "name": "Operator custom", "file_name": choice.name,
    "part_mode": "manual", "printer": "CI dummy - never print",
}}
registry = userdata / "template-registry.json"
write_template_registry(labels, "sticker60x40.btw", path=registry)
restored = bootstrap_template_registry({}, user, registry)
assert restored["label_templates"] == labels
assert restored["sticker_btw"] == str(user / "sticker60x40.btw")
assert read_template_registry(registry)["label_templates"] == labels

# Production-like persistent fixtures, entirely in the disposable runner.
from catalog_store import CatalogStore
from csv_history_store import CsvHistoryStore
catalog = CatalogStore(userdata / "data" / "catalog.sqlite3")
catalog.initialize()
catalog.create_product({"gtin": "99900000000000", "name": "Synthetic smoke product"})
history = CsvHistoryStore(userdata / "csv-history.sqlite3")
history.initialize()
history.append_event(
    str(root / "synthetic-test.csv"),
    "submitted_to_bartender",
    printer="NO PRINT - SYNTHETIC HISTORY ONLY", sent=2, fmt="16x16",
)
user_cfg = userdata / "config"
user_cfg.mkdir(parents=True)
(user_cfg / "config.json").write_text('{"smoke": "preserve user config"}', encoding="utf-8")
(user_cfg / "license.json").write_text('{"test": "intentionally invalid synthetic license"}', encoding="utf-8")

# Old installs may also have mutable data inside the application directory.
legacy_cfg = installed / "config"
legacy_cfg.mkdir(parents=True, exist_ok=True)
(legacy_cfg / "config.json").write_text('{"smoke": "preserve legacy config"}', encoding="utf-8")
(legacy_cfg / "license.json").write_text('{"test": "invalid legacy license"}', encoding="utf-8")
legacy_data = installed / "data"
legacy_data.mkdir(parents=True, exist_ok=True)
import sqlite3
with sqlite3.connect(legacy_data / "catalog.sqlite3") as db:
    db.execute("CREATE TABLE smoke (value TEXT NOT NULL)")
    db.execute("INSERT INTO smoke VALUES ('legacy user catalog')")
print("PASS synthetic SQLite catalog/history, config/license and legacy install data created")
print("PASS imports, filename conflict, legacy migration, registry restore")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Python storage fixture failed' }

$original = Get-UserSnapshot
$legacyPaths = @('config\config.json', 'config\license.json', 'data\catalog.sqlite3')
$legacyHashes = @{}
foreach ($relative in $legacyPaths) {
    $target = Join-Path $installDir $relative
    if (-not (Test-Path -LiteralPath $target)) { throw "Missing legacy fixture: $relative" }
    $legacyHashes[$relative] = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
}
function Assert-LegacyMutable([string] $phase) {
    foreach ($relative in $legacyPaths) {
        $target = Join-Path $installDir $relative
        if (-not (Test-Path -LiteralPath $target) -or
            (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $legacyHashes[$relative]) {
            throw "Legacy install-dir user data modified at $phase : $relative"
        }
    }
    Write-Host "PASS $phase : install-dir config/license/catalog preserved"
}

Invoke-Installer 'in-place-upgrade'
Assert-Installed $candidateVersion
Assert-Snapshot $original 'in-place-upgrade'
Assert-LegacyMutable 'in-place-upgrade'
if (-not (Test-Path -LiteralPath (Join-Path $installDir 'templates\16x16.btw'))) {
    throw 'Installer removed an operator-owned legacy BTW in the install directory'
}

$uninstaller = Get-ChildItem -LiteralPath $installDir -Filter 'unins*.exe' -File |
    Select-Object -First 1
if (-not $uninstaller) { throw 'Inno uninstaller was not created' }
$uninstallLog = Join-Path $workRoot 'uninstall.log'
$uninstallArgs = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=$q$uninstallLog$q"
$process = Start-Process -FilePath $uninstaller.FullName -ArgumentList $uninstallArgs -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "Uninstall failed with exit code $($process.ExitCode)"
}
for ($attempt = 0; $attempt -lt 30 -and
     (Test-Path -LiteralPath (Join-Path $installDir 'AutoPrint.exe')); $attempt++) {
    Start-Sleep -Seconds 1
}
if (Test-Path -LiteralPath (Join-Path $installDir 'AutoPrint.exe')) {
    throw 'Uninstaller did not remove the installed application'
}
Assert-Snapshot $original 'uninstall'
Assert-LegacyMutable 'uninstall'
Invoke-Installer 'reinstall'
Assert-Installed $candidateVersion
Assert-Snapshot $original 'reinstall'
Assert-LegacyMutable 'reinstall'

@'
import os
from pathlib import Path
from template_storage import bootstrap_template_registry, read_template_registry
userdata = Path(os.environ["AUTOPRINT_USER_DATA_DIR"])
templates = userdata / "templates"
registry = userdata / "template-registry.json"
restored = bootstrap_template_registry({"label_templates": {"wrong": {}}}, templates, registry)
assert list(restored["label_templates"]) == ["16x16"]
assert restored["label_templates"]["16x16"]["part_mode"] == "manual"
assert restored["sticker_btw"] == str(templates / "sticker60x40.btw")
assert read_template_registry(registry)["label_templates"] == restored["label_templates"]
print("PASS reinstall metadata restoration")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Post-reinstall metadata validation failed' }

Write-Host 'PASS: Inno lifecycle and SHA-256 preservation on disposable Windows CI runner.'
if ($previousInstallerPath) {
    Write-Host "PASS: v$PreviousVersion to v$candidateVersion installer upgrade on disposable runner."
} else {
    Write-Host 'Cross-version upgrade not requested in this invocation.'
}
Write-Host 'NOT COVERED: licensed frozen GUI startup, live BarTender COM, physical print.'
