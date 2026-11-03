# CSV print-history persistence — Issue #7

The live source of truth is `%LOCALAPPDATA%\AutoPrintData\csv-history.sqlite3`
(or `AUTOPRINT_USER_DATA_DIR\csv-history.sqlite3` in an isolated profile).

At first startup only, existing legacy `csv_print_stats` data are read from the
installed `config/config.json` and historical `BarTenderGUI` / `AutoPrint`
config locations. History records are deduplicated by exact event JSON and
merged across files. The old configs are left untouched. Existing SQLite state
is always authoritative and is never overwritten/reimported. Corrupt persistent
SQLite or unreadable legacy config stops initialization instead of replacing
history with empty state.

The database has schema version 1, a per-CSV snapshot table for the existing
UI and an append-only event table. Status and event are written in a single
SQLite transaction. CSV identity is its normalized absolute path, not its
basename: move/rename creates a distinct entry unless explicitly migrated.

Statuses: `prepared`, `submitted_to_bartender`, `spooled`, `completed`,
`failed`, `cancelled`, `unknown`. A successful AutoPrint batch submission
records **submitted_to_bartender**, not spooler/device/physical completion.
An ambiguous COM outcome is recorded as `unknown` without a retry. Historical
`printed=True` means only that a submission was previously reported; it is
not retroactively promoted to `completed`.

Limits:
- Current GUI has no authoritative spooler completion correlation. It never
  claims completed/physical status on the basis of BarTender's return alone.
- A sudden process crash before the durable event write cannot prove a submit.
  Such external print-system outcomes need operator reconciliation.
- A preexisting DB is authoritative: writes made by older app versions to old
  `config.json` after migration are not automatically merged.
- Existing unrelated config fields, license paths, BarTender batch/PrintOut
  semantics and installer setup are unchanged by this issue.

Before merge/release: inspect actual CSV status counts and history in an isolated
Windows profile and packaged update, including legacy installation layout,
corrupt-store failure and real BarTender status interpretation. No physical print
verification is claimed.
