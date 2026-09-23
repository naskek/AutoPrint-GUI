# BarTender Automation/COM technical reference

This document is the project contract for changes to AutoPrint's BarTender integration. It records the official API surface, project-specific decisions, an audit of the current source at commit `ad4d027fbd55ac10cc46f1523db25f84e0eee2bb`, and behavior that still has to be tested with a real BarTender installation.

The four evidence categories used below are deliberately distinct:

- **Official contract** — stated in Seagull Scientific documentation.
- **AutoPrint rule** — a project decision; it is not presented as a vendor guarantee.
- **Current-code audit** — what the repository does at the audited commit, not necessarily desired behavior.
- **Needs local verification** — behavior that cannot be established from documentation or source inspection alone.

Official Seagull documentation is authoritative for the API. When different installed BarTender versions behave differently, the verified behavior of every supported local version must also be recorded. Test execution and CI layering are defined in [testing-bartender.md](testing-bartender.md).

## Official BarTender API contracts

### Status of ActiveX and object model

Seagull describes ActiveX Automation as superseded by newer technologies and supported for testing and backward compatibility. This describes the **ActiveX integration as a whole**; it does not make every old-looking member deprecated. Seagull recommends the BarTender REST API whenever possible. Integration Builder is the no-code/low-code alternative, and the Windows-only .NET SDK remains supported but is no longer the strategic API direction. See [Using ActiveX](https://help.seagullscientific.com/2022/en/Subsystems/ActiveX/Content/BT_ActiveX.htm) and [Automating BarTender](https://help.seagullscientific.com/2022/en/Content/Overview_of_Automation.htm).

The core ActiveX hierarchy used by AutoPrint is:

```text
BarTender.Application
`-- Application.Formats
    `-- Format (an open .btw document)
        |-- NamedSubStrings
        |-- Databases
        `-- PrintSetup
```

`BarTender.Application` is the top-level automation object. It represents the BarTender process, exposes the open `Formats` collection, is hidden by default, and can report version and edition information. See [Using the Application Object](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Using_Application_Object.htm), [Formats Collection Object](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Formats_collection_objects.htm), and [Format Object](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Format_Object.htm).

### Lifecycle: start, open, close, quit

**Official contract.** `Formats.Open(PathAndFileName, CloseOutFirstDocument, UsePrinter)` returns an open `Format`. An empty `UsePrinter` selects the printer stored in the document; a printer name overrides it. `CloseOutFirstDocument` concerns the default `Document1.btw`, not every open document. See [Formats.Open](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Open_Method.htm).

`Format.Close(BtSaveOptions)` closes one document, while `Application.Quit(BtSaveOptions)` closes the application. `btPromptSave = 0`, `btDoNotSaveChanges = 1`, and `btSaveChanges = 2`. `Application.Quit` is asynchronous and can return before the process terminates. See [Format.Close](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Close_Method.htm) and [Application.Quit](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Quit_Method.htm).

**AutoPrint rule.** A single thread owns the `Application` object and every COM object obtained from it. Calls and object references must not cross threads. The ordinary path must open formats inside `try/finally`, close each with `btDoNotSaveChanges`, then quit the application with `btDoNotSaveChanges` and release references. Temporary changes to a printer, database path, named data source, copy count, or preview configuration must never be persisted in a user's BTW.

Do not make pywin32's implicit initialization of the Python main thread part of the contract. If a future worker thread becomes the COM-owning thread, that worker must explicitly initialize COM before creating or using BarTender objects and explicitly uninitialize COM after releasing them. Seagull's documentation does not define AutoPrint's Python COM-apartment policy; this paragraph is a project decision.

### Named Data Sources (`NamedSubStrings`)

**Official contract.** `Format.NamedSubStrings` manages named data sources assigned to objects in the template. `Format.SetNamedSubStringValue(name, value)` sets a named data source. See [NamedSubStrings Collection](https://help.seagullscientific.com/2022/en/subsystems/activex/content/NamedSubStrings_collection_.htm) and [SetNamedSubStringValue](https://help.seagullscientific.com/2022/en/subsystems/activex/content/SetNamedSubStringValue_Method.htm).

**AutoPrint rule.** Treat names as the template/program interface: enumerate the names, set only intentional matches, and report required missing names before print. Convert application values to strings deliberately. Do not persist those runtime values. Access through `SetNamedSubStringValue` is the preferred project path; any `SubStrings` fallback is compatibility behavior and must be tested on supported installs.

### CSV and database connections

**Official contract.** `Format.Databases` manages databases defined for the document. A `Database` exposes `FileName` and, for a text-file connection, `TextFile`; `TextFile.FileName` sets the text/CSV source. `PrintSetup.UseDatabase` controls whether a connected database participates in printing, and `PrintSetup.RefreshDatabases` controls reload immediately before printing. See [Databases Collection](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Databases_collection_.htm), [Database Object](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Database_Object.htm), [TextFile Object](https://help.seagullscientific.com/2022/en/subsystems/activex/content/TextFile_Object.htm), [PrintSetup.UseDatabase](https://help.seagullscientific.com/2022/en/subsystems/activex/content/UseDatabase_Property_%28PrintSetup%29.htm), and [PrintSetup.RefreshDatabases](https://help.seagullscientific.com/2022/en/subsystems/activex/content/RefreshDatabases_Property.htm).

Changing a database path mutates the open document. It must therefore be paired with the no-save close policy. Whether the new path is immediately effective, requires refresh, preserves field mappings, and works with the deployed CSV dialect is **Needs local verification**.

The reviewed official ActiveX object model documents `Databases`; it does not establish `Format.DatabaseConnections` as a portable ActiveX contract. Code that probes `DatabaseConnections` is a compatibility heuristic, not a documented guarantee.

### `PrintSetup`, printer selection, copies, and serialization

**Official contract.** `PrintSetup` represents printer and print-job configuration. Its documented printer property is `PrintSetup.Printer`. A printer may also be selected using the `Formats.Open` printer argument. See [PrintSetup Object](https://help.seagullscientific.com/2022/en/subsystems/activex/content/PrintSetup_Object.htm) and [PrintSetup.Printer](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Printer_Property%28PrintSetup%29.htm).

`PrintSetup.IdenticalCopiesOfLabel` is the number of identical copies per item. `PrintSetup.NumberSerializedLabels` is the number of items in a serialization sequence. They are independent concepts. See [PrintSetup.IdenticalCopiesOfLabel](https://help.seagullscientific.com/2022/en/subsystems/activex/content/IdenticalCopiesOfLabel_Property%28PrintSetup%29.htm) and [PrintSetup.NumberSerializedLabels](https://help.seagullscientific.com/2022/en/subsystems/activex/content/NumberSerializedLabels_Property%28PrintSetup%29.htm).

`Format.AutoPrintAgain` and `PrintSetup.AutoPrintAgain` are documented print options controlling whether the document automatically prints again after a completed print. AutoPrint explicitly forces this option off when supported before programmatic printing. See [Format Object](https://help.seagullscientific.com/11.4/en/Subsystems/ActiveX/Content/Format_Object.htm) and [PrintSetup Object](https://help.seagullscientific.com/2021/en/subsystems/activex/content/PrintSetup_Object.htm).

`Format.IdenticalCopiesOfLabel` and `Format.NumberSerializedLabels` are also explicitly documented by Seagull. No reviewed official source calls them deprecated or legacy. AutoPrint prefers the `PrintSetup` properties for one consistent configuration surface; that preference is an **AutoPrint rule**, not a deprecation claim. See [Format.IdenticalCopiesOfLabel](https://help.seagullscientific.com/2022/en/subsystems/activex/content/IdenticalCopiesOfLabel_Property.htm) and [Format.NumberSerializedLabels](https://help.seagullscientific.com/2022/en/subsystems/activex/content/NumberSerializedLabels_Property.htm).

`PrintSetup.PrinterName` was not found in the reviewed official ActiveX documentation. Its presence and effect are **Needs local verification**; new code must use documented `PrintSetup.Printer` unless an explicit compatibility adapter and test justify another member.

### Template image export versus print preview

The two APIs are not interchangeable:

| API | Official meaning | Result |
| --- | --- | --- |
| `Format.ExportToFile(file, type, colors, resolution, saveOptions)` | Creates an image of the document's **template**. | Image file; not a rendered multi-page print run. |
| `Format.ExportPrintPreviewToImage(directory, fileNameTemplate, type, colors, dpi, background, saveOptions, includeMargins, includeBorder, messages)` | Exports an image for **each page of print preview**. `%PageNumber%` is supported. | `BtPrintResult.btSuccess` or `btFailure`, plus messages. |

See [Format.ExportToFile](https://help.seagullscientific.com/2022/en/subsystems/activex/content/ExportToFile_Method.htm) and [Format.ExportPrintPreviewToImage](https://help.seagullscientific.com/2022/en/subsystems/activex/content/ExportPreview_Method.htm).

**AutoPrint rule.** User-facing print preview must use `ExportPrintPreviewToImage`, not `ExportToFile`. It must supply an explicit no-save option, `BtSaveOptions.btDoNotSaveChanges` (or the exact documented equivalent for the supported installed version), collect and inspect messages, write to an AutoPrint-owned temporary directory, and close the format with `btDoNotSaveChanges`. Preview-time printer, database, and named-data-source changes must not be persisted into the user's BTW. File naming, multi-page cleanup, overwrite behavior, and the precise behavior of the no-save option are **Needs local verification** before replacing the current implementation.

### `Format.Print`, `PrintOut`, results, and messages

**Official contract.** The documented signatures are:

```text
Format.Print(PrintJobName, WaitForJobToComplete, TimeoutMs, Messages)
    -> BtPrintResult

Format.PrintOut(ShowStatusWindow, ShowPrintDialog)
    -> void
```

`Format.Print` returns `btSuccess = 0`, `btTimeout = 1`, or `btFailure = 2`, and returns a `Messages` collection through its output parameter. The same official page says both that waiting reaches submission to the spooler and that a timeout can occur before printing finishes. That language does not define a reliable physical-device boundary. See [Format.Print](https://help.seagullscientific.com/2022/en/subsystems/activex/content/PrintJob_Method.htm).

`PrintOut` accepts exactly two Boolean arguments and has no result value. Copies and serialization must be configured through documented properties rather than invented positional arguments. See [Format.PrintOut](https://help.seagullscientific.com/2022/en/subsystems/activex/content/PrintOut_Method.htm).

A `Message` is informational, warning, or error and exposes message text, number, severity, type, and type text. `Message.Severity` values are information `0`, warning `1`, and error `2`. See [Message Object](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Message_Object.htm) and [Message.Severity](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Severity_Property.htm).

**AutoPrint interpretation.** A `btSuccess` return means only `bartender_success/submitted`: BarTender reported success for the requested operation. It does **not** prove that paper was physically printed, that media was accepted, or definitively that spool completion occurred. The exact submitted/spooled/processed/device-completed boundary is **Needs local verification** for every supported BarTender, Windows spooler, driver, and printer combination. A timeout or exception is an ambiguous outcome: the job may already have been submitted.

Always capture the numeric result and all messages, including warnings and informational messages. `PrintOut` can confirm only that the call returned without raising; it supplies no `BtPrintResult` or completion evidence.

**AutoPrint rule.** Prefer the documented four-argument `Format.Print` for unattended printing because it returns a result and messages. For unattended batch submission, AutoPrint uses `WaitForJobToComplete=False`: the application needs the BarTender submission result, not synchronous completion of a potentially long print run. This avoids deliberately blocking the Tk event loop while waiting for job completion and avoids turning long-running jobs into routine 30-second `btTimeout` results. A returned `btTimeout`, malformed result, or COM exception after `Print` begins remains an ambiguous outcome and must never trigger a retry. Use the documented two-Boolean `PrintOut` only when its status-window/print-dialog behavior is intentionally required or as an explicitly tested compatibility path; never treat its return as proof of submission or completion.

### Events, print state, and the System Database

**Official contract.** `Application.OnNewPrintJob` and `Format.OnNewPrintJob` report a newly created job with printer name and job ID; the format-scoped event covers that document's jobs. Creation is not completion or physical-print evidence. See [Application.OnNewPrintJob](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Application_OnNewPrintJob_Event.htm) and [Format.OnNewPrintJob](https://help.seagullscientific.com/2022/en/subsystems/activex/content/Format_OnNewPrintJob_Event.htm). `Application.IsPrinting` reports whether BarTender is currently printing, but is not per-job proof of device output; see [Application.IsPrinting](https://help.seagullscientific.com/2022/en/subsystems/activex/content/IsPrinting_Property.htm).

Seagull documents the BarTender System Database as a source of print jobs, status messages, and application messages. Logging and a configured database connection are prerequisites. It may be evaluated as an optional diagnostic or reconciliation source, but AutoPrint must not make it a runtime dependency without a separate architectural decision. See [Print Job Information](https://help.seagullscientific.com/2022/en/subsystems/reprintconsole/content/BTPrint_Jobs_Overview.htm) and [Configuring Print Job Logging](https://help.seagullscientific.com/2022/en/subsystems/adminconsole/content/Config_Print_Job_Logging_LP.html).

Event delivery through pywin32, message pumping, job-ID correlation, System Database latency, and the meaning of each recorded status are **Needs local verification**.

### Version and edition compatibility

The cited ActiveX pages state minimum versions and editions per member; many core members require Automation or Enterprise, while `ExportPrintPreviewToImage` is documented as BarTender 9.01+ and Enterprise in the 2022 ActiveX reference. Do not infer availability from one development machine or from BarTender's marketing version alone.

**AutoPrint rule.** Record `Application.Version`/`FullVersion` and `Application.Edition` in a local compatibility result, test every supported version/edition, and fail with an actionable capability message when a required member is absent. Do not silently replace a missing documented path with an undocumented overload.

For future redesigns, evaluate the [BarTender REST API](https://help.seagullscientific.com/2022/en/Content/API/API_BarTender.htm), Integration Builder, or the appropriate .NET SDK. This issue does not authorize a migration.

## Mandatory AutoPrint project rules

All later print and preview changes must follow these rules unless an ADR explicitly changes them:

1. One thread owns the entire BarTender COM graph. A future worker that owns it explicitly initializes and uninitializes COM on that same thread.
2. Close every opened `Format` and quit the owned `Application` with `btDoNotSaveChanges`; runtime changes must not alter user templates.
3. Use documented members as the baseline: `Databases`, `TextFile.FileName`, `PrintSetup.Printer`, documented copy/serialization properties, the four-argument `Print`, and the two-Boolean `PrintOut`.
4. Prefer `PrintSetup.IdenticalCopiesOfLabel` and `PrintSetup.NumberSerializedLabels` for consistency. Do not describe the corresponding `Format` properties as deprecated.
5. User-facing preview uses `ExportPrintPreviewToImage` with explicit `btDoNotSaveChanges`, captured messages, isolated temporary output, and deterministic cleanup.
6. Model successful `Format.Print` only as `bartender_success/submitted`. Never claim physical printing from `btSuccess`, a returned `PrintOut`, a GUI counter, or CSV history.
7. Treat timeout, COM exception, cancellation during submission, and a malformed output parameter as ambiguous. Do not submit by a second print API unless there is affirmative evidence that no first job was created or a designed idempotency mechanism prevents duplicates.
8. Preserve the complete `BtPrintResult` and `Message` evidence. Warnings are not success-proof and informational messages can be diagnostically important.
9. Cancellation must state its boundary. A pre-submission flag, a BarTender job operation, a Windows queue deletion, and physical device cancellation are different operations.
10. Gate behavior by verified version/edition capabilities and keep the System Database optional until separately adopted.

## Current AutoPrint implementation audit

This section describes the source at `ad4d027fbd55ac10cc46f1523db25f84e0eee2bb`. It is an audit, not an instruction to preserve defects.

| Area | Current-code audit finding | Consequence |
| --- | --- | --- |
| Lifecycle | `BT.start()` dispatches `BarTender.Application`; `BT.stop()` calls `Application.Quit(1)`. Call sites generally close formats with `fmt.Close(1)`. | The numeric value is `btDoNotSaveChanges`, but cleanup is distributed across callers and `Quit` is asynchronous. |
| COM thread | The current code uses `Dispatch` without an explicit project-level COM initialization abstraction. | The implementation must not become the contract that implicit main-thread initialization is sufficient. |
| Named data | `BT.apply_fields()` enumerates `NamedSubStrings`, calls `SetNamedSubStringValue`, then tries `SubStrings(name).Value` as a fallback. | The primary path is documented; fallback behavior is compatibility code requiring local coverage. |
| Preview | `BT.export_preview()` calls `ExportToFile(path, "PNG", 1, 300, 0)`. | It exports a template image, not the documented print-preview pages. Save option `0` is `btPromptSave`, not the required no-save policy. |
| Printer | `_prepare_btw()`, `_print_rows_direct()`, and `_bt_batch_print()` try both `PrintSetup.PrinterName` and documented `PrintSetup.Printer`. | `PrinterName` is not established by the reviewed official API and exceptions are swallowed. |
| Copies | Batch/direct paths commonly set `PrintSetup.IdenticalCopiesOfLabel`; `_bt_print()` also sets `Format.IdenticalCopiesOfLabel` in its single-job branch. | Both properties are officially documented; project consistency is incomplete, but there is no basis to call the `Format` property deprecated. |
| `PrintOut` | `_bt_print()` first attempts `PrintOut(copies, serialized, showDialog)` and then falls back to documented two-Boolean calls. | The three-argument call is undocumented and can fail after an outcome that the caller cannot reliably classify. |
| `Print` fallback | Historical defect fixed by #13: `_bt_batch_print()` used to follow any `Format.Print` exception/nonzero result with `_bt_print()`. Current code no longer falls back from the batch `Format.Print` path to `PrintOut`; failure to prepare the output parameter also stops before submission. | `btTimeout`, `btFailure`, COM exceptions, malformed results, unknown results, and pre-submit setup failures stop without a second submit. |
| Print state | A zero result is returned as `True`; successful paths increment `sent_total`. `_mark_csv_printed()` then writes `printed: True` plus application history. | The CSV status records AutoPrint application state. It does not prove physical printing or device completion. |
| Message handling | `_collect_bt_messages()` extracts message text, severity, and type text for the batch `Print` path. | Useful evidence exists, but paths using `PrintOut` cannot provide equivalent result/message evidence. |
| Database rebind | `_rebind_template_db()` probes `DatabaseConnections` first and `Databases` second, trying both connection/database `FileName` and `TextFile.FileName`. | Only the `Databases` model is established by the reviewed official ActiveX docs; the other path needs compatibility testing. |
| Database validation | `_check_template_db()` checks `DatabaseConnections`; absent/empty collections and any outer COM inspection error return `True`. | Validation is fail-open and can permit printing without proving the expected CSV binding. It also does not validate the documented `Databases` collection. |
| Cancellation | `_cancel_print()` sets an application flag, then removes jobs from the selected Windows printer queue. Loop checks stop future submissions. | Queue purge can affect unrelated jobs, cannot undo pages already emitted, and is not correlated to BarTender job IDs. Its exact boundary is unknown. |

The original documentation-only change did not fix implementation findings. Subsequent fixes are noted explicitly in the affected rows.

## Local verification matrix

Run these checks on a disposable test workstation and printer/queue. Use a copy of a BTW, never the only production template. Record BarTender full version, edition, Windows version, pywin32/Python architecture, driver name/version, printer model/firmware, and whether the System Database is enabled.

| ID | Test | Evidence to retain | Required interpretation |
| --- | --- | --- | --- |
| LV-01 | Start an owned application; open and close representative BTW files; quit; repeat after forced exceptions. | Process list, AutoPrint log, open-handle observation. | **Needs local verification:** no orphan process/reference; remember `Quit` is asynchronous. |
| LV-02 | Call `Print` with `WaitForJobToComplete` false and true against a held/paused queue, offline printer, driver error, and working printer. | Return code, all messages with severity/type/number, timestamps, Windows queue transitions, physical observation. | **Needs local verification:** exact submitted/spooled/processed/device-complete boundary. Physical output must remain a separate fact. |
| LV-03 | Induce timeout/COM exception near submission without automatic fallback, then inspect queue and job events. | Unique job name, event job ID, queue/System Database records. | **Needs local verification:** whether an ambiguous result can coexist with an already-created job and therefore duplicate on retry. |
| LV-04 | Export a multi-record, multi-page, serialized print preview with conditional-print objects and margins. | Generated file list, page count, pixel comparison to BarTender UI preview, messages/result. | **Needs local verification:** preview fidelity, file naming, overwrite, pagination, copies, database and serialization behavior. |
| LV-05 | Before/after preview, hash the BTW, record mtime, reopen it, and inspect printer, database path, named values, copies, and serialization. Use `btDoNotSaveChanges` and close without saving. | Hash/mtime and reopened settings. | **Needs local verification:** no temporary change persists and the selected save option still produces the intended preview files. |
| LV-06 | Inspect a real CSV-backed template through `Databases` and the optional `DatabaseConnections` probe; rebind a disposable copy. | COM member availability, collection contents, resulting record values. | **Needs local verification:** installed-version object shape, correct path member, refresh requirement, encoding/delimiter/header handling. |
| LV-07 | Set `PrintSetup.Printer`; separately probe `PrintSetup.PrinterName`. Reopen without save. | Selected queue in job/event and COM errors. | **Needs local verification:** documented path works on each install; whether `PrinterName` exists or has any compatibility value. |
| LV-08 | Enumerate/set all required Named Data Sources, including missing, differently cased, database-backed, and embedded sources. | Values in preview/print and exact COM failures. | **Needs local verification:** name lookup/case behavior and pywin32 collection/indexing/fallback semantics. |
| LV-09 | Test identical copies and serialized labels independently and together. | Requested properties, queue job/page counts, physical sequence. | **Needs local verification:** driver/template-specific copy and serialization semantics. |
| LV-10 | Cancel before submission, during BarTender processing, while queued, while printer is offline, and after the device starts. Include unrelated jobs in a safe test queue. | Job IDs, queue transitions, emitted labels, unrelated-job outcome. | **Needs local verification:** each cancellation boundary and collateral effects; never equate flag-setting with cancellation. |
| LV-11 | Subscribe to application and format `OnNewPrintJob` events on the COM-owning thread. | Callback timestamps, printer/job IDs, required message-pump behavior. | **Needs local verification:** pywin32 event delivery and reliable correlation. Creation is not completion. |
| LV-12 | With and without configured System Database logging, correlate unique jobs to stored status/messages. | Database/reprint-console record IDs, timestamps, lag, missing records. | **Needs local verification:** availability, retention, latency, and meaning of statuses. Keep it optional. |
| LV-13 | Exercise each supported version/edition and, in an isolated prototype, a COM-owning worker with explicit initialize/uninitialize. | Capability inventory and clean shutdown logs. | **Needs local verification:** edition gates, member availability, architecture compatibility, and the project thread pattern. |

Minimum smoke coverage before any printing/preview release is LV-01, LV-02, LV-04, LV-05, LV-06, LV-07, LV-08, LV-09, and LV-10. LV-03 is mandatory before adding any retry; LV-11 and LV-12 are mandatory before depending on events or the System Database.

## Known unknowns

The following must remain explicitly unresolved until the matrix produces retained evidence:

- **Needs local verification:** what `Format.Print(..., WaitForJobToComplete=True)` and `btSuccess` mean at the exact submitted/spooled/processed/device-completed boundaries.
- **Needs local verification:** how timeout and COM exceptions relate to jobs that BarTender or Windows may already have created.
- **Needs local verification:** supported-version behavior of preview `SaveOpts=btDoNotSaveChanges`, page naming/cleanup, messages, and temporary setting isolation.
- **Needs local verification:** pywin32 output-parameter shapes for `Messages` in `Print` and preview export.
- **Needs local verification:** whether `DatabaseConnections` and `PrintSetup.PrinterName` exist on any supported installation and whether their semantics are useful.
- **Needs local verification:** CSV rebinding refresh, field mapping, header, encoding, delimiter, and path behavior for production templates.
- **Needs local verification:** event delivery/message pumping and whether job IDs can support safe correlation or cancellation.
- **Needs local verification:** whether System Database logging is licensed, configured, complete, and timely enough for diagnostics on each site.
- **Needs local verification:** Windows spooler and printer-driver status accuracy, queue deletion behavior, and proof available from the physical device.
- **Needs local verification:** the exact ActiveX member set and edition restrictions on every supported BarTender release.
