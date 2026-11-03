# Local opt-in smoke on exact source heads #73 (real migrated BTW) and #76 (GUI).
# Does not install/uninstall any application; never invokes Print/PrintOut.
param([string] $TemplatePath = '')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'Windows 10/11 required' }
# Existing COM instances may legitimately run in the background for BarTender
# services. Never kill them, attach to them, or stop their sessions.
$existingBt = @(Get-Process -Name 'bartend' -ErrorAction SilentlyContinue)
$skipLiveCom = $existingBt.Count -gt 0
if ($skipLiveCom) {
    Write-Warning "Found $($existingBt.Count) existing bartend processes. Skipping LIVE COM only; running offline BTW-migration and isolated GUI smoke. No processes will be touched."
}
if (Get-Process -Name 'AutoPrint' -ErrorAction SilentlyContinue) {
    Write-Warning 'An AutoPrint instance exists. It will be left untouched; this smoke uses isolated source/profile and disabled printing.'
}
$head73 = '93a6fbdc230249e087acaf832ddbfcd7deef6609'
$head76 = '0068cd65884874be47ae41df9d4777e5f9395c4a'
$homeBefore = Get-Location
$vars = @('LOCALAPPDATA','APPDATA','TEMP','TMP','AUTOPRINT_USER_DATA_DIR')
$prev = @{}
foreach ($v in $vars) { $prev[$v] = [Environment]::GetEnvironmentVariable($v,'Process') }
$work = Join-Path $env:TEMP "AutoPrint68_FinalSmoke_$(Get-Date -Format yyyyMMdd_HHmmss)"
$guiSource = Join-Path $work 'pr76'
$btwSource = Join-Path $work 'pr73'
$legacy = Join-Path $work 'legacy\templates'
$userData = Join-Path $work 'operator-data'
$fixture = Join-Path $work 'csv-fixtures'
$python = Join-Path $work '.venv\Scripts\python.exe'
try {
    if (-not $TemplatePath) {
        $prior = Join-Path $env:TEMP 'AutoPrint68_Preview_20261103_101138\test_template.btw'
        if (Test-Path -LiteralPath $prior) { $TemplatePath = $prior }
    }
    if (-not $TemplatePath) {
        Add-Type -AssemblyName System.Windows.Forms
        $choose = [System.Windows.Forms.OpenFileDialog]::new()
        $choose.Title = 'Выбери BTW для безопасной проверки копии'
        $choose.Filter = 'BarTender label (*.btw)|*.btw'
        if ($choose.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            throw 'BTW not selected'
        }
        $TemplatePath = $choose.FileName
    }
    $TemplatePath = (Resolve-Path -LiteralPath $TemplatePath).Path
    $hash = (Get-FileHash -LiteralPath $TemplatePath -Algorithm SHA256).Hash
    New-Item -ItemType Directory -Path $work,$legacy,$fixture -Force | Out-Null
    $copied = Join-Path $legacy 'operator-test-template.btw'
    Copy-Item -LiteralPath $TemplatePath -Destination $copied -ErrorAction Stop

    git clone --depth 1 --single-branch --branch fix/5-preserve-user-btw-templates https://github.com/naskek/AutoPrint-GUI.git $btwSource
    if ($LASTEXITCODE -ne 0) { throw 'Cannot clone PR #73 source' }
    git clone --depth 1 --single-branch --branch fix/30-csv-browser-height-folder-visuals https://github.com/naskek/AutoPrint-GUI.git $guiSource
    if ($LASTEXITCODE -ne 0) { throw 'Cannot clone PR #76 source' }
    if ((git -C $btwSource rev-parse HEAD).Trim() -ne $head73) {
        throw 'PR #73 SHA has changed; do not smoke a mismatched head'
    }
    if ((git -C $guiSource rev-parse HEAD).Trim() -ne $head76) {
        throw 'PR #76 SHA has changed; do not smoke a mismatched head'
    }

    py -3.12 -m venv (Join-Path $work '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 venv unavailable' }
    & $python -m pip install -q -r (Join-Path $btwSource 'build_tools\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Missing runtime dependencies' }

    $env:LOCALAPPDATA = Join-Path $work 'local'
    $env:APPDATA = Join-Path $work 'roaming'
    $env:TEMP = Join-Path $work 'tmp'
    $env:TMP = $env:TEMP
    $env:AUTOPRINT_USER_DATA_DIR = $userData
    foreach ($dir in @($env:LOCALAPPDATA,$env:APPDATA,$env:TEMP,$userData)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $env:AUTOPRINT68_LEGACY_TEMPLATE = $copied
    $env:AUTOPRINT68_BTW_SMOKE_OUTPUT = Join-Path $work 'live-preview.png'
    $env:AUTOPRINT68_SKIP_LIVE_BT = if ($skipLiveCom) { '1' } else { '0' }
    # Real BarTender COM uses the operator's normal Seagull profile.
    # AutoPrint user data stays isolated via AUTOPRINT_USER_DATA_DIR.
    $env:LOCALAPPDATA = $prev['LOCALAPPDATA']
    $env:APPDATA = $prev['APPDATA']
    Set-Location -LiteralPath $btwSource
    @'
import hashlib, os
from pathlib import Path
from template_storage import migrate_legacy_templates, get_user_templates_dir, bootstrap_template_registry, get_template_registry_path

source = Path(os.environ["AUTOPRINT68_LEGACY_TEMPLATE"])
target = Path(get_user_templates_dir())
result = migrate_legacy_templates(destination_dir=target, legacy_dirs=[source.parent])
assert result and all(x["status"] != "error" for x in result), result
copy = target / source.name
assert copy.is_file(), "Migrated BTW missing"
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(copy) == sha(source), "Migration changed real BTW bytes"
config = {"label_templates": {"16x16": {
    "id": "16x16", "file_name": copy.name,
    "name": "Read-only operator smoke", "part_mode": "manual"
}}}
cfg = bootstrap_template_registry(config, target, get_template_registry_path())
assert cfg["label_templates"]["16x16"]["part_mode"] == "manual"
if os.environ.get("AUTOPRINT68_SKIP_LIVE_BT") == "1":
    print("PASS #73 EXACT SHA: real BTW migrated byte-for-byte, registry restored")
    print("SKIP: real BarTender COM preview (existing bartend.exe processes untouched)")
else:
    import pythoncom
    from PIL import Image
    from bartender_com import BT
    class Log:
        def log(self, msg): print(msg)
        def err(self, msg): print("ERROR:", msg)

    pythoncom.CoInitialize()
    engine = BT(Log())
    fmt = None
    try:
        engine.start()
        fmt = engine.open_format(str(copy))
        out = os.environ["AUTOPRINT68_BTW_SMOKE_OUTPUT"]
        if not engine.export_preview(fmt, out):
            raise RuntimeError("BarTender failed to export migrated BTW")
        with Image.open(out) as png:
            png.verify()
        print("PASS #73 EXACT SHA: migration + real BarTender opened migrated copy and PNG exported; no print")
    finally:
        if fmt is not None:
            try: fmt.Close(1)  # btDoNotSaveChanges
            except Exception as ex: print("Close warning:", ex)
        engine.stop()
        pythoncom.CoUninitialize()
'@ | & $python -
    if ($LASTEXITCODE -ne 0) { throw 'PR #73 migrated BTW smoke FAILED' }
    if ((Get-FileHash -LiteralPath $TemplatePath -Algorithm SHA256).Hash -ne $hash) {
        throw 'Original BTW changed! Check filesystem'
    }
    Write-Host 'PASS: original operator BTW SHA-256 unchanged.'
    $env:LOCALAPPDATA = Join-Path $work 'local'
    $env:APPDATA = Join-Path $work 'roaming'

    # The second exact-head check is source-run-only and has no BarTender COM.
    Set-Location -LiteralPath $guiSource
    $env:AUTOPRINT68_CSV_FIXTURES = $fixture
    @'
import json, os
from pathlib import Path
from unittest.mock import patch
from catalog_store import CatalogStore

src = Path.cwd()
fixtures = Path(os.environ["AUTOPRINT68_CSV_FIXTURES"])
for i in range(1, 17):
    (fixtures / f"test_{i:02d}.csv").write_text(
        "GTIN\tDM\n04601234567890\tNEVER-PRINT\n",encoding="utf-8")
for d in ("Архив","Заказы","Ноябрь"):
    (fixtures / d).mkdir(exist_ok=True)
db = CatalogStore(src / "data" / "catalog.sqlite3")
db.initialize()
if not db.list_products():
    db.create_product({"gtin":"04601234567890","name":"UI isolated smoke"})
cfgdir = src / "config"
cfgdir.mkdir(exist_ok=True)
(cfgdir/"config.json").write_text(
    json.dumps({"csv_dir":str(fixtures)}),encoding="utf-8")

import bartender_gui
from autoprint_main import AutoPrintApp
# Keep the no-COM and no-submit guards for the ENTIRE GUI mainloop,
# not just construction, so timers and keyboard callbacks cannot submit.
with patch.object(bartender_gui.App,"_start_bt",return_value=None), \
     patch.object(bartender_gui.App,"_print_range_one_job_via_csv",return_value=None), \
     patch.object(bartender_gui.App,"_print_rows_direct",return_value=None), \
     patch.object(bartender_gui.App,"_print_sticker_placeholder",return_value=None), \
     patch.object(bartender_gui,"enum_printers",return_value=[]):
    app = AutoPrintApp()
    app.print_btn.configure(state="disabled")
    app.cancel_btn.configure(state="disabled")
    app.sticker_reprint_btn.configure(state="disabled")
    app.title("PR #76 EXACT HEAD UI SMOKE — PRINT DISABLED")
    app.csv_dir = str(fixtures)
    app._render_csv_list()
    app.geometry("1040x720")
    print("PASS #76 source GUI started; yellow folders and 16 CSV fixtures visible")
    print("NO COM, NO PRINT/PRINTOUT — close the GUI after inspecting layout")
    app.mainloop()
'@ | & $python -
    if ($LASTEXITCODE -ne 0) { throw 'PR #76 GUI smoke FAILED' }
    if ((Get-FileHash -LiteralPath $TemplatePath -Algorithm SHA256).Hash -ne $hash) {
        throw 'Original BTW changed after GUI test'
    }
    Write-Host 'PASS: source PR #73 and #76 exact SHAs, original BTW unchanged.'
    Write-Host "Isolated test directory: $work"
}
finally {
    Set-Location -LiteralPath $homeBefore
    foreach ($v in $vars) {
        [Environment]::SetEnvironmentVariable($v,$prev[$v],'Process')
    }
    Remove-Item Env:AUTOPRINT68_LEGACY_TEMPLATE,Env:AUTOPRINT68_BTW_SMOKE_OUTPUT,Env:AUTOPRINT68_SKIP_LIVE_BT,Env:AUTOPRINT68_CSV_FIXTURES -ErrorAction SilentlyContinue
}
