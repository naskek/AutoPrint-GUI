# BarTender testing strategy

This document defines how AutoPrint verifies its BarTender integration. The API contract itself lives in [bartender-automation.md](bartender-automation.md) and the official Seagull Scientific pages linked from it remain authoritative.

## Required workflow for BarTender changes

Before changing COM lifecycle, printing, preview, BTW handling, data sources, printers, copies/serialization, messages/results, events, or cancellation:

1. Read `docs/bartender-automation.md`.
2. Verify the exact COM member, signature, return semantics, and version/edition requirements against the linked official Seagull documentation.
3. Add or update a COM-boundary contract test for the changed behavior.
4. Keep documented API behavior, AutoPrint rules, compatibility heuristics, and locally observed behavior explicitly separate.
5. Never add retry/fallback after an ambiguous print result unless duplicate submission is impossible by design.

## Verification layers

### 1. Unit/regression CI

GitHub Actions job: `unit-regression-tests`.

Runs normal `tests/test_*.py` without requiring an installed BarTender instance.

### 2. BarTender COM-boundary contract CI

GitHub Actions job: `bartender-com-contract`.

Runs `tests/contract_*.py` with fake/mock COM objects. These tests verify AutoPrint's side of the boundary: exact ProgID/member/call shape, no-save lifecycle, result handling, and submission count. They do not prove that a real BarTender installation, Windows spooler, driver, or printer behaves correctly.

Known contract violations may be represented temporarily with `unittest.expectedFailure` only when an open Issue owns the fix. The test must name that Issue, and the decorator must be removed in the fixing PR. An unexpected success is therefore treated as a failing suite until the expectation is updated.

### 3. Live BarTender COM smoke

Entry point: `tools/bartender_live_smoke.py`.

This smoke is intentionally excluded from ordinary CI. It only runs when `AUTOPRINT_LIVE_BARTENDER=1` is explicitly set and only on Windows.

Without arguments it starts a hidden `BarTender.Application`, reports Version/FullVersion/Edition, and quits with `btDoNotSaveChanges`.

With `--btw <path>` it additionally opens a real disposable/test BTW and verifies the installed COM object shape used by AutoPrint:

- documented `Format.Print`, `Format.PrintOut`, `Format.SetNamedSubStringValue`, and `Format.Close`;
- `Format.PrintSetup`;
- documented `PrintSetup.IdenticalCopiesOfLabel` read/write access;
- documented `PrintSetup.Printer`;
- `NamedSubStrings` enumeration;
- `Databases` collection probe;
- read-only `Format.PageSetup.MediaHandling.Action` / `Occurrence` inspection, including documented enum decoding;
- optional `MediaHandling.NumberOfLabels` read;
- document close with `btDoNotSaveChanges`.

The BTW inspection mode deliberately does **not** call `Print` or `PrintOut`, does not touch the Windows spooler, and does not claim physical-print verification. Its output includes `NO PRINT SUBMITTED` when the inspection completes. This lets a work branch validate the installed BarTender COM surface without building/installing a production AutoPrint release.

PowerShell:

```powershell
Set-Location -LiteralPath 'D:\Projects\AutoPrint-GUI'
$env:AUTOPRINT_LIVE_BARTENDER = '1'

# Startup/shutdown only
.\.venv\Scripts\python.exe .\tools\bartender_live_smoke.py

# Real BTW inspection, still no print submission
.\.venv\Scripts\python.exe .\tools\bartender_live_smoke.py --btw 'C:\Path\To\Disposable-Test.btw'

Remove-Item Env:AUTOPRINT_LIVE_BARTENDER
```

This extends LV-01 and provides non-printing capability evidence for LV-06/LV-08/LV-09 plus a read-only media-handling snapshot relevant to historical feed/backfeed diagnostics. The documented values are decoded from Seagull's ActiveX contract: `Occurrence=0` is `btMHAtEndOfPrintJob`, `1` is after every page, `2` is after every record, etc. `Action=0` delegates the actual media handling to printer-driver settings, so this probe must not be treated as proof of physical feed/backfeed behavior.

Official references:

- https://help.seagullscientific.com/2022/en/subsystems/activex/content/PageSetup_Object.htm
- https://help.seagullscientific.com/2022/en/subsystems/activex/content/MediaHandling_Object.htm
- https://help.seagullscientific.com/11.8/en/Subsystems/ActiveX/Content/Action_Property.htm
- https://help.seagullscientific.com/11.8/en/Subsystems/ActiveX/Content/Occurrence_Property.htm

Any test that actually invokes `Format.Print` or `PrintOut` remains a separate deliberate live-print/queue check and must never be hidden inside this smoke harness.

### 4. Paused dummy-queue spooler smoke

Entry point: `tools/bartender_spooler_smoke.py`.

This is a deliberate live-submit test. Unlike the non-printing BTW inspection, it calls the documented four-argument `Format.Print(..., WaitForJobToComplete=False, ...)` once. It must target only a dedicated Windows printer queue that:

- contains `TEST` in its queue name;
- uses a port whose name contains `AUTOPRINT_TEST`;
- is already paused before the script starts;
- contains no pre-existing jobs.

The recommended queue uses the installed Printronix driver but a dedicated local sink port named `AUTOPRINT_TEST_SINK`. No TCP/IP host is configured at all. The queue remains paused for the entire smoke. This lets Windows/BarTender build a real spool job while preventing physical output and avoids any dependency on network-port creation.

The smoke rejects unsafe queues before submission, then requires:

```text
one BarTender Format.Print call
    -> exactly one Windows spool job
```

Any result other than exactly one job is a regression signal. Test jobs are deleted after the observation while the queue remains paused. Cleanup is enabled only after the queue has passed all safety checks; an accidentally supplied production queue is never cleaned or modified by this script.

This test does **not** prove physical feed/backfeed behavior or high-speed printer throughput. It proves the BarTender/driver/Windows-spooler job boundary for the selected batch.

Queue setup helper:

```powershell
.\tools\setup_bartender_spooler_test_queue.ps1
```

The helper creates/validates the dedicated local `AUTOPRINT_TEST` port and `AutoPrint_TEST_Printronix` queue, selects exactly one installed Printronix driver, pauses the queue through documented `Win32_Printer.Pause()`, and refuses to proceed when the queue already contains jobs.

After the smoke completes, remove the dedicated test queue and local port:

```powershell
.\tools\remove_bartender_spooler_test_queue.ps1
```

The cleanup helper refuses to touch a queue without `TEST` in its name, refuses to remove an unexpected port, and only removes the local test port after confirming no printer queue still uses it.

Windows Print Spooler can keep a local port handle busy after the queue is deleted. The default cleanup never restarts the spooler. If the TEST port remains busy and the workstation is idle, rerun explicitly with:

```powershell
.\tools\remove_bartender_spooler_test_queue.ps1 -AllowSpoolerRestart
```

Before restarting the Spooler service, the helper enumerates every installed printer queue and refuses the restart if any print job exists or any queue cannot be queried. It never clears the spool directory and never modifies production queue configuration.

Required opt-in flag:

```text
AUTOPRINT_LIVE_BARTENDER_SPOOLER=1
```

### 5. Physical print verification



Physical output is a separate release/manual gate. A green CI run, a successful COM call, a Windows queue entry, or `BtPrintResult.btSuccess` must never be reported as proof that labels physically printed correctly.

## CI and release behavior

Pull requests expose separate checks for unit/regression tests, BarTender COM-boundary contracts, and Windows packaging. `build-windows` depends on both test jobs.

The tag/release workflow reruns both ordinary tests and COM-boundary contract tests before creating installer/release artifacts.

A future licensed self-hosted Windows runner may execute live BarTender tests, but it is not required by Issue #14 and must not silently submit physical print jobs.
