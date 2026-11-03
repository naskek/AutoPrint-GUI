# Issue #68: synthetic persistent-data migration across an actual candidate Inno install.
# CI-only / disposable Windows machine. No frozen app startup or print submission.
param(
    [Parameter(Mandatory = $true)][string] $Installer,
    [string] $ProductionInstaller = '',
    [string] $ProductionSha256 = '',
    [string] $SourceDir = '',
    [string] $ExpectedCandidateVersion = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or
    -not $env:RUNNER_TEMP -or -not $env:GITHUB_RUN_ID) {
    throw 'Restricted to a disposable GitHub-hosted Windows runner'
}
# Test harness can validate a separate exact source SHA without changing it.
$repo = if ($SourceDir) {
    (Resolve-Path -LiteralPath $SourceDir).Path
} else {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}
$actualSha = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or
    -not $env:AUTOPRINT_SMOKE_EXPECT_SHA -or
    $actualSha -ne $env:AUTOPRINT_SMOKE_EXPECT_SHA) {
    throw "Unexpected candidate SHA: $actualSha"
}
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$productionPath = ''
if ($ProductionInstaller) {
    $productionPath = (Resolve-Path -LiteralPath $ProductionInstaller).Path
    if (-not $ProductionSha256 -or $ProductionSha256 -notmatch '^[a-fA-F0-9]{64}$') {
        throw 'Production installer must have a pinned SHA-256'
    }
    $realHash = (Get-FileHash -LiteralPath $productionPath -Algorithm SHA256).Hash
    if ($realHash -ne $ProductionSha256) {
        throw "Published installer checksum mismatch. Expected=$ProductionSha256 actual=$realHash"
    }
}
$root = Join-Path $env:RUNNER_TEMP ("autoprint-68-data-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT")
if (Test-Path -LiteralPath $root) { throw "Refusing reused smoke root $root" }

# Never operate where a user/runner AutoPrint is already registered.
foreach ($regRoot in @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
)) {
    if (-not (Test-Path $regRoot)) { continue }
    foreach ($key in (Get-ChildItem $regRoot -ErrorAction Stop)) {
        $info = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if ($info -and $info.PSObject.Properties.Match('DisplayName').Count -gt 0) {
            if ($info.DisplayName -eq 'AutoPrint' -or $info.DisplayName -like 'AutoPrint version *') {
                throw "Existing AutoPrint installation detected: $($key.PSPath)"
            }
        }
    }
}

$install = Join-Path $root 'legacy-install'
$userData = Join-Path $root 'operator-data'
$local = Join-Path $root 'local'
New-Item -ItemType Directory -Path $install,$local -Force | Out-Null
$env:LOCALAPPDATA = $local
$env:AUTOPRINT_USER_DATA_DIR = $userData
$env:AUTOPRINT_PACKAGED_SMOKE = '1'
$env:AUTOPRINT_SMOKE_INSTALL = $install
$env:AUTOPRINT_SMOKE_ROOT = $root
Set-Location -LiteralPath $repo

# For the upgrade scenario install the actual public production binary FIRST.
# Public 3.2.4 auto-starts AutoPrint even during /VERYSILENT. It is unlicensed
# on the disposable runner; close ONLY that exact isolated executable. No
# production license and no real print driver/BarTender are provisioned here.
$oldExeHash = $null
if ($productionPath) {
    $q = [char]34
    $log = Join-Path $root 'production-3.2.4-install.log'
    $args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=$q$install$q /LOG=$q$log$q"
    # Do NOT use Start-Process -Wait: Inno 3.2.4 starts a child GUI
    # even in silent mode and -Wait would wait for that child as well.
    $process = Start-Process -FilePath $productionPath -ArgumentList $args -PassThru
    if (-not $process.WaitForExit(120000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction Stop
        throw 'Published 3.2.4 installer timeout'
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) {
        throw "Published 3.2.4 installer failed (exit=$($process.ExitCode)): $log"
    }
    $oldExe = Join-Path $install 'AutoPrint.exe'
    if (-not (Test-Path -LiteralPath $oldExe)) {
        throw 'Published 3.2.4 did not install AutoPrint.exe'
    }
    $oldExeHash = (Get-FileHash -LiteralPath $oldExe -Algorithm SHA256).Hash
    $oldAppVersion = (Get-Item -LiteralPath $oldExe).VersionInfo.FileVersion
    if ($oldAppVersion -notlike '3.2.4*') {
        throw "Wrong published executable version: $oldAppVersion"
    }

    $foundOldProcess = $false
    for ($n = 0; $n -lt 8; $n++) {
        Start-Sleep -Milliseconds 400
        $matches = @(Get-CimInstance Win32_Process -Filter "Name='AutoPrint.exe'" |
            Where-Object { $_.ExecutablePath -eq $oldExe })
        foreach ($running in $matches) {
            Stop-Process -Id $running.ProcessId -Force -ErrorAction Stop
            $foundOldProcess = $true
        }
    }
    $stillRunning = @(Get-CimInstance Win32_Process -Filter "Name='AutoPrint.exe'" |
        Where-Object { $_.ExecutablePath -eq $oldExe })
    if ($stillRunning.Count) { throw 'Published AutoPrint.exe is still running' }
    Write-Host "PASS actual public 3.2.4 installed (signed SHA-256 verified); autolaunched GUI isolated=$foundOldProcess"
}

@'
import json
import os
from pathlib import Path
from catalog_store import CatalogStore

root = Path(os.environ["AUTOPRINT_SMOKE_ROOT"])
install = Path(os.environ["AUTOPRINT_SMOKE_INSTALL"])
fixture_dir = root / "csv-fixtures"
fixture_dir.mkdir()
for n in (1, 2):
    (fixture_dir / f"test_{n:02d}.csv").write_text(
        "GTIN\tDM\n04601234567890\tNEVER_PRINT_THIS\n", encoding="utf-8"
    )

legacy = install / "data" / "catalog.sqlite3"
store = CatalogStore(legacy)
store.initialize()
store.create_product({"gtin": "04601234567890", "name": "Legacy operator product"})
assert store.list_products()[0].name == "Legacy operator product"

cfg = install / "config"
cfg.mkdir(parents=True)
config = {
    "csv_dir": str(fixture_dir),
    "label_templates": {
        "16x16": {"id": "16x16", "file_name": "16x16.btw",
                 "part_mode": "manual", "name": "Legacy custom"}
    },
    "csv_print_stats": {
        str(fixture_dir / "test_01.csv"): {
            "printed": True, "print_runs": 1,
            "last_print": "2026-10-31 12:00:00",
            "history": [{"ts": "2026-10-31 12:00:00", "sent": 7}]
        },
        str(fixture_dir / "test_02.csv"): {
            "printed": False, "print_runs": 0, "history": []
        },
    },
}
(cfg / "config.json").write_text(json.dumps(config), encoding="utf-8")
# Explicitly synthetic and INVALID; never use/copy a real license.
(cfg / "license.json").write_bytes(b"SYNTHETIC-INVALID-LICENSE-NOT-A-SECRET")
templates = install / "templates"
templates.mkdir()
(templates / "16x16.btw").write_bytes(b"SYNTHETIC-USER-16x16-NOT-A-REAL-BTW")
(templates / "operator.btw").write_bytes(b"SYNTHETIC-USER-CUSTOM")
print("PASS seeded legacy catalog, config, 2 CSV histories, fake license, user BTW")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Could not seed isolated legacy fixture' }

$legacyFiles = @(
    (Join-Path $install 'data\catalog.sqlite3'),
    (Join-Path $install 'config\config.json'),
    (Join-Path $install 'config\license.json'),
    (Join-Path $install 'templates\16x16.btw'),
    (Join-Path $install 'templates\operator.btw')
)
function Get-FileHashMap([string[]]$files) {
    $hashes = @{}
    foreach ($path in $files) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing operator data: $path" }
        $hashes[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    }
    return $hashes
}
function Assert-FileHashMap([hashtable]$expected,[string]$phase) {
    foreach ($path in $expected.Keys) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing $phase : $path" }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        if ($actual -ne $expected[$path]) {
            throw "Operator file modified at $phase : $path"
        }
    }
    Write-Host "PASS $phase : $($expected.Count) source files preserved SHA-256"
}
$q = [char]34
function Install-Candidate([string]$phase) {
    $log = Join-Path $root "$phase.log"
    $argsText = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=$q$install$q /LOG=$q$log$q"
    $process = Start-Process -FilePath $installerPath -ArgumentList $argsText -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Installer $phase exit=$($process.ExitCode): $log"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $install 'AutoPrint.exe'))) {
        throw "Installed EXE missing at $phase"
    }
}
$baseline = Get-FileHashMap $legacyFiles
Write-Host "Cross-source data smoke, candidate HEAD: $actualSha"
Install-Candidate 'install-over-legacy-layout'
Assert-FileHashMap $baseline 'initial candidate install'
if ($productionPath) {
    $newExe = Join-Path $install 'AutoPrint.exe'
    $newExeHash = (Get-FileHash -LiteralPath $newExe -Algorithm SHA256).Hash
    if ($ExpectedCandidateVersion) {
        $actualVersion = (Get-Item -LiteralPath $newExe).VersionInfo.FileVersion
        if ($actualVersion -ne $ExpectedCandidateVersion -and $actualVersion -ne "$ExpectedCandidateVersion.0") {
            throw "Candidate EXE FileVersion mismatch: expected $ExpectedCandidateVersion, found $actualVersion"
        }
        Write-Host "PASS true version upgrade: old 3.2.4 -> new $actualVersion (installed EXE)"
    }
    if ($newExeHash -eq $oldExeHash) {
        throw 'Candidate did not replace the actual published 3.2.4 executable'
    }
    Write-Host 'PASS binary upgrade: public v3.2.4 installer -> integration candidate EXE (different SHA-256)'
}

@'
import json
import os
from pathlib import Path
from catalog_runtime import initialize_catalog_runtime
from catalog_store import CatalogStore, get_catalog_db_path
from csv_history_store import CsvHistoryStore, legacy_config_paths, normalize_csv_path
from template_storage import (
    bootstrap_template_registry, get_user_templates_dir,
    get_template_registry_path, migrate_legacy_templates,
)

root = Path(os.environ["AUTOPRINT_SMOKE_ROOT"])
install = Path(os.environ["AUTOPRINT_SMOKE_INSTALL"])
user = Path(os.environ["AUTOPRINT_USER_DATA_DIR"])
legacy = install / "data" / "catalog.sqlite3"
target = Path(get_catalog_db_path())
res = initialize_catalog_runtime(install, database_path=target)
assert res.ready, res.error
assert target != legacy and target.is_file()
assert len(res.snapshot.products) == 1, "Migrated catalog count mismatch"
assert res.snapshot.products[0].name == "Legacy operator product"
assert legacy.is_file() and next((target.parent / "backups").glob("*.sqlite3"))
assert CatalogStore(target).list_products()[0].gtin == "04601234567890"

history = CsvHistoryStore()
history.initialize(legacy_config_paths(install))
entries = history.read_all()
fixture = root / "csv-fixtures"
first = entries[normalize_csv_path(fixture / "test_01.csv")]
second = entries[normalize_csv_path(fixture / "test_02.csv")]
assert len(entries) == 2
assert first["print_runs"] == 1 and len(first["history"]) == 1
assert first["state"] == "submitted_to_bartender"
assert second["print_runs"] == 0 and second["printed"] is False
assert (install / "config" / "config.json").is_file()

templates = Path(get_user_templates_dir())
result = migrate_legacy_templates(destination_dir=templates, base_dir=install)
assert all(e["status"] != "error" for e in result), result
assert (templates / "16x16.btw").read_bytes() == b"SYNTHETIC-USER-16x16-NOT-A-REAL-BTW"
assert (templates / "operator.btw").read_bytes() == b"SYNTHETIC-USER-CUSTOM"
cfg = json.loads((install / "config" / "config.json").read_text())
registry_path = get_template_registry_path()
restored = bootstrap_template_registry(cfg, templates, registry_path)
assert restored["label_templates"]["16x16"]["part_mode"] == "manual"
assert registry_path.is_file()
print("PASS migration: catalog(1), CSV history(2), BTW(2), registry; legacy sources intact")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }

$persisted = @(Get-ChildItem -LiteralPath $userData -Recurse -File |
    ForEach-Object { $_.FullName })
if ($persisted.Count -lt 5) { throw "Too few persisted files: $($persisted.Count)" }
$persistedHashes = Get-FileHashMap $persisted

Install-Candidate 'in-place-reinstall'
Assert-FileHashMap $baseline 'in-place reinstall legacy'
Assert-FileHashMap $persistedHashes 'in-place reinstall persistent'

$uninstaller = Get-ChildItem -LiteralPath $install -Filter 'unins*.exe' -File |
    Select-Object -First 1
if (-not $uninstaller) { throw 'Missing Inno uninstaller' }
$uninstallLog = Join-Path $root 'uninstall.log'
$argsText = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=$q$uninstallLog$q"
$p = Start-Process -FilePath $uninstaller.FullName -ArgumentList $argsText -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Uninstall failed: $($p.ExitCode)" }
Assert-FileHashMap $baseline 'uninstall legacy'
Assert-FileHashMap $persistedHashes 'uninstall persistent'

Install-Candidate 'reinstall-after-uninstall'
Assert-FileHashMap $baseline 'reinstall legacy'
Assert-FileHashMap $persistedHashes 'reinstall persistent'

@'
import os
from pathlib import Path
from catalog_runtime import initialize_catalog_runtime
from catalog_store import get_catalog_db_path
from csv_history_store import CsvHistoryStore, legacy_config_paths
from template_storage import bootstrap_template_registry, get_user_templates_dir, get_template_registry_path
import json

install = Path(os.environ["AUTOPRINT_SMOKE_INSTALL"])
state = initialize_catalog_runtime(install, database_path=get_catalog_db_path())
assert state.ready and len(state.snapshot.products) == 1
assert state.migration_source is None  # persistent catalog wins on restart
store = CsvHistoryStore()
store.initialize(legacy_config_paths(install))
assert len(store.read_all()) == 2
config = json.loads((install / "config" / "config.json").read_text())
result = bootstrap_template_registry(config, get_user_templates_dir(), get_template_registry_path())
assert result["label_templates"]["16x16"]["part_mode"] == "manual"
print("PASS verified restored catalog/history/templates/registry after reinstall")
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Post-reinstall restore failed' }

Write-Host 'PASS: synthetic catalog/history/template/config/license preserved after installed Inno lifecycle.'
if ($productionPath) {
    & python (Join-Path $PSScriptRoot 'frozen_license_gate_probe.py') $install
    if ($LASTEXITCODE -ne 0) { throw 'Frozen license-gate probe failed' }
}

if ($productionPath) {
    Write-Host 'PASS: genuine public v3.2.4 installer -> integration candidate EXE replacement.'
} else {
    Write-Host 'NOT COVERED: published v3.2.4 installer upgrade.'
}
if (-not $ExpectedCandidateVersion) {
    Write-Host 'NOT COVERED: version-number change 3.2.4 -> 3.2.5.'
}
Write-Host 'NOT COVERED: live licensed frozen GUI, real BTW/BarTender or physical print.'
