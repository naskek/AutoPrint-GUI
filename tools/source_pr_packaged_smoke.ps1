# Smoke an INSTALLER BUILT FROM THE EXACT SOURCE PR HEAD; test tools live only in PR #78.
# CI-only, independent disposable runner per source PR, no valid license or printing.
param(
    [Parameter(Mandatory=$true)][ValidateSet('Catalog','History')][string]$Kind,
    [Parameter(Mandatory=$true)][string]$SourceDir,
    [Parameter(Mandatory=$true)][string]$ExpectedSourceSha
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or
    -not $env:RUNNER_TEMP -or -not $env:GITHUB_RUN_ID) {
    throw 'Disposable GitHub-hosted Windows runner is mandatory'
}
$source = (Resolve-Path -LiteralPath $SourceDir).Path
$actual = (& git -C $source rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actual -ne $ExpectedSourceSha) {
    throw "Source PR SHA mismatch: $actual expected $ExpectedSourceSha"
}
$root = Join-Path $env:RUNNER_TEMP "autoprint-source-$Kind-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT"
if (Test-Path -LiteralPath $root) { throw "Nonempty reused smoke directory: $root" }

# Only one isolated AutoPrint Inno AppId can be registered on a runner.
foreach ($regRoot in @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
)) {
    if (-not (Test-Path -LiteralPath $regRoot)) { continue }
    foreach ($key in (Get-ChildItem -LiteralPath $regRoot -ErrorAction Stop)) {
        $v = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
        if ($v -and $v.PSObject.Properties.Match('DisplayName').Count -gt 0 -and
            ($v.DisplayName -eq 'AutoPrint' -or $v.DisplayName -like 'AutoPrint version *')) {
            throw "Already registered installation: $($key.PSPath)"
        }
    }
}
$install = Join-Path $root 'isolated-install'
$userData = Join-Path $root 'isolated-user-data'
$env:LOCALAPPDATA = Join-Path $root 'isolated-local'
$env:AUTOPRINT_USER_DATA_DIR = $userData
$env:AUTOPRINT_PACKAGED_SMOKE = '1'
$env:AUTOPRINT_68_SOURCE_INSTALL = $install
$env:AUTOPRINT_68_SOURCE_ROOT = $root
New-Item -ItemType Directory -Path $root,$install,$env:LOCALAPPDATA -Force | Out-Null
$published = Join-Path $root 'AutoPrint_Setup_public-v3.2.4.exe'
$url = 'https://github.com/naskek/AutoPrint-GUI-Releases/releases/download/v3.2.4/AutoPrint_Setup_3.2.4.exe'
$publicSha = 'bf69876759b3a8d356d8fe26494b7a13bb2bd2f9e92dd6e56dfa3ba16087c168'
Invoke-WebRequest -Uri $url -OutFile $published -MaximumRedirection 10 -ErrorAction Stop
if ((Get-FileHash -LiteralPath $published -Algorithm SHA256).Hash -ne $publicSha) {
    throw 'Actual production v3.2.4 installer failed SHA-256'
}
$version = (& python -c "from app_version import APP_VERSION; print(APP_VERSION)" 2>$null)
# The working directory for Python validation is explicitly switched below.
$candidate = Join-Path $source 'dist\installer\AutoPrint_Setup_3.2.4.exe'
if (-not (Test-Path -LiteralPath $candidate)) { throw "Missing exact-source candidate: $candidate" }
$q = [char]34
function Stop-OnlyIsolatedGui {
    # Old v3.2.4 and source-head candidate both autolaunch on silent install.
    # They have no valid license on this disposable test VM. Kill ONLY our EXE.
    $expectedExe = Join-Path $install 'AutoPrint.exe'
    for ($n=0; $n -lt 5; $n++) {
        Start-Sleep -Milliseconds 350
        $matches = @(Get-CimInstance Win32_Process -Filter "Name='AutoPrint.exe'" |
            Where-Object { $_.ExecutablePath -eq $expectedExe })
        foreach ($p in $matches) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
        }
    }
    $still = @(Get-CimInstance Win32_Process -Filter "Name='AutoPrint.exe'" |
        Where-Object { $_.ExecutablePath -eq $expectedExe })
    if ($still.Count -gt 0) { throw "Isolated GUI still running: $expectedExe" }
}
function Invoke-IsolatedInstaller([string]$installer,[string]$phase) {
    $log = Join-Path $root "$phase.log"
    $args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=$q$install$q /LOG=$q$log$q"
    $p = Start-Process -FilePath $installer -ArgumentList $args -PassThru
    if (-not $p.WaitForExit(120000)) {
        Stop-Process -Id $p.Id -Force
        throw "$phase timed out; log: $log"
    }
    $p.Refresh()
    if ($p.ExitCode -ne 0) { throw "$phase failed: $($p.ExitCode) log=$log" }
    Stop-OnlyIsolatedGui
    if (-not (Test-Path -LiteralPath (Join-Path $install 'AutoPrint.exe'))) {
        throw "EXE missing after $phase"
    }
}
function Get-ShaMap([string[]]$items) {
    $result=@{}
    foreach ($path in $items) {
        if (-not (Test-Path -LiteralPath $path)) { throw "File missing: $path" }
        $result[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    }
    return $result
}
function Confirm-ShaMap([hashtable]$map,[string]$phase) {
    foreach ($item in $map.Keys) {
        if (-not (Test-Path -LiteralPath $item) -or
            (Get-FileHash -LiteralPath $item -Algorithm SHA256).Hash -ne $map[$item]) {
            throw "User data lost or overwritten in $phase : $item"
        }
    }
    Write-Host "PASS $Kind $phase : $($map.Count) files preserved SHA-256"
}
$oldDir = Get-Location
try {
    Set-Location -LiteralPath $source
    Invoke-IsolatedInstaller $published 'old-public-v3.2.4-install'
    $beforeExe = (Get-FileHash -LiteralPath (Join-Path $install 'AutoPrint.exe') -Algorithm SHA256).Hash
    if ($Kind -eq 'Catalog') {
        @'
import os
from pathlib import Path
from catalog_store import CatalogStore
root=Path(os.environ["AUTOPRINT_68_SOURCE_INSTALL"])
store=CatalogStore(root/"data"/"catalog.sqlite3")
store.initialize()
store.create_product({"gtin":"04601234567890","name":"Operator Product Old Version"})
assert len(store.list_products())==1
print("PASS seeded a real SQLite catalog under old install")
'@ | python -
        if ($LASTEXITCODE -ne 0) { throw 'Catalog fixture failed' }
        $legacy = @(Join-Path $install 'data\catalog.sqlite3')
    } else {
        @'
import json,os
from pathlib import Path
root=Path(os.environ["AUTOPRINT_68_SOURCE_INSTALL"])
fixture=Path(os.environ["AUTOPRINT_68_SOURCE_ROOT"])
d=root/"config";d.mkdir(parents=True,exist_ok=True)
items={}
for n in range(1,3):
    p=fixture/f"history_{n:02d}.csv"
    p.write_text("GTIN\tDM\n04601234567890\tNEVER-PRINT\n",encoding="utf-8")
    items[str(p)]={"printed":bool(n==1),"print_runs":1 if n==1 else 0,
        "last_print":"2026-11-01 10:00:00" if n==1 else "",
        "history":[{"ts":"2026-11-01 10:00:00","sent":3}] if n==1 else []}
(d/"config.json").write_text(json.dumps({"csv_print_stats":items}),encoding="utf-8")
print("PASS seeded history for 2 synthetic CSVs under old install")
'@ | python -
        if ($LASTEXITCODE -ne 0) { throw 'History fixture failed' }
        $legacy = @(Join-Path $install 'config\config.json')
    }
    $legacyHashes = Get-ShaMap $legacy
    Invoke-IsolatedInstaller $candidate 'candidate-install-over-real-public-version'
    $afterExe = (Get-FileHash -LiteralPath (Join-Path $install 'AutoPrint.exe') -Algorithm SHA256).Hash
    if ($beforeExe -eq $afterExe) { throw 'Candidate binary was not installed over real production 3.2.4' }
    Confirm-ShaMap $legacyHashes 'public-to-candidate-upgrade'

    if ($Kind -eq 'Catalog') {
        @'
import os
from pathlib import Path
from catalog_runtime import initialize_catalog_runtime
from catalog_store import get_catalog_db_path,CatalogStore
root=Path(os.environ["AUTOPRINT_68_SOURCE_INSTALL"])
target=Path(get_catalog_db_path())
res=initialize_catalog_runtime(root,database_path=target)
assert res.ready,res.error
assert len(res.snapshot.products)==1
assert res.snapshot.products[0].name=="Operator Product Old Version"
assert target!=root/"data"/"catalog.sqlite3"
assert next((target.parent/"backups").glob("*.sqlite3"))
again=initialize_catalog_runtime(root,database_path=target)
assert again.ready and again.migration_source is None
print("PASS exact PR #74 source: catalog migrated/backed up/idempotent")
'@ | python -
        if ($LASTEXITCODE -ne 0) { throw 'Catalog migration failed' }
    } else {
        @'
import os
from pathlib import Path
from csv_history_store import CsvHistoryStore,legacy_config_paths,normalize_csv_path
root=Path(os.environ["AUTOPRINT_68_SOURCE_INSTALL"])
fixture=Path(os.environ["AUTOPRINT_68_SOURCE_ROOT"])
store=CsvHistoryStore()
store.initialize(legacy_config_paths(root))
data=store.read_all()
assert len(data)==2
first=data[normalize_csv_path(fixture/"history_01.csv")]
second=data[normalize_csv_path(fixture/"history_02.csv")]
assert first["state"]=="submitted_to_bartender"
assert first["print_runs"]==1 and len(first["history"])==1
assert second["print_runs"]==0 and not second["printed"]
store.initialize(legacy_config_paths(root))
assert len(store.read_all())==2
print("PASS exact PR #75 source: history migrated/transactional/idempotent")
'@ | python -
        if ($LASTEXITCODE -ne 0) { throw 'History migration failed' }
    }
    $persistent = @(Get-ChildItem -LiteralPath $userData -Recurse -File |
        ForEach-Object { $_.FullName })
    if ($persistent.Count -lt 1) { throw "No persistent files created in $Kind" }
    $persistentHashes = Get-ShaMap $persistent
    Invoke-IsolatedInstaller $candidate 'candidate-in-place-reinstall'
    Confirm-ShaMap $legacyHashes 'in-place-reinstall-legacy'
    Confirm-ShaMap $persistentHashes 'in-place-reinstall-persistent'
    $uninstaller = Get-ChildItem -LiteralPath $install -Filter 'unins*.exe' -File |
        Select-Object -First 1
    if (-not $uninstaller) { throw 'No Inno uninstaller found' }
    $uninstallLog = Join-Path $root 'uninstall.log'
    $args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=$q$uninstallLog$q"
    $p=Start-Process -FilePath $uninstaller.FullName -ArgumentList $args -PassThru
    if (-not $p.WaitForExit(90000)) { Stop-Process -Id $p.Id -Force;throw 'Uninstall timeout' }
    $p.Refresh()
    if ($p.ExitCode -ne 0) { throw "Uninstall failed: $($p.ExitCode)" }
    Confirm-ShaMap $legacyHashes 'uninstall-legacy'
    Confirm-ShaMap $persistentHashes 'uninstall-persistent'
    Invoke-IsolatedInstaller $candidate 'candidate-reinstall'
    Confirm-ShaMap $legacyHashes 'reinstall-legacy'
    Confirm-ShaMap $persistentHashes 'reinstall-persistent'
    Write-Host "PASS exact source HEAD $ExpectedSourceSha ($Kind): real v3.2.4 -> candidate installer, migration and lifecycle."
    Write-Host "NO PRINT/PRINTOUT, no real license, no actual customer data."
} finally {
    Set-Location -LiteralPath $oldDir
}
