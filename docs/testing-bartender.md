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

This smoke is intentionally excluded from ordinary CI. It only runs when `AUTOPRINT_LIVE_BARTENDER=1` is explicitly set and only on Windows. It starts a hidden `BarTender.Application`, reports Version/FullVersion/Edition, and quits with `btDoNotSaveChanges`. It does not open a BTW, submit a print job, touch the Windows spooler, or claim physical-print verification.

PowerShell:

```powershell
$env:AUTOPRINT_LIVE_BARTENDER = '1'
python .\tools\bartender_live_smoke.py
Remove-Item Env:AUTOPRINT_LIVE_BARTENDER
```

This covers only the startup/shutdown portion of LV-01 in `docs/bartender-automation.md`. More invasive LV checks must use disposable templates/queues and be run deliberately.

### 4. Physical print verification

Physical output is a separate release/manual gate. A green CI run, a successful COM call, a Windows queue entry, or `BtPrintResult.btSuccess` must never be reported as proof that labels physically printed correctly.

## CI and release behavior

Pull requests expose separate checks for unit/regression tests, BarTender COM-boundary contracts, and Windows packaging. `build-windows` depends on both test jobs.

The tag/release workflow reruns both ordinary tests and COM-boundary contract tests before creating installer/release artifacts.

A future licensed self-hosted Windows runner may execute live BarTender tests, but it is not required by Issue #14 and must not silently submit physical print jobs.
