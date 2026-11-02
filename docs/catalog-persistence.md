# Catalog data preservation — Issue #6

The operator's live SQLite catalog is stored in
`%LOCALAPPDATA%\AutoPrintData\data\catalog.sqlite3`
(or `AUTOPRINT_USER_DATA_DIR\data\catalog.sqlite3` when using an isolated profile).
It is never installer-owned. The install directory may still hold shipped Excel
sources and historical databases, but those are not the authoritative live DB.

First startup with an existing legacy SQLite and no persistent DB:
1. Look in old `{app}\data\catalog.sqlite3`, `{app}\catalog.sqlite3`,
   `%LOCALAPPDATA%\AutoPrint\{data\,}` and
   `%LOCALAPPDATA%\BarTenderGUI\{data\,}` locations.
2. Validate every candidate's schema/user_version, quick_check, foreign keys.
3. Reject differing candidates instead of guessing the correct catalog.
4. Use SQLite backup API to copy a consistent snapshot (including committed
   WAL records) into a temporary file; validate it again.
5. Preserve a separate recoverable backup under `data\backups` and
   atomically publish the DB with no overwrite. Old source DBs are untouched.
6. Existing valid persistent DB always takes precedence; invalid existing
   persistent DB blocks startup, never replaced with an empty/default one.

A clean profile with no legacy DB follows the existing validated Excel
initialization flow. Schema upgrades are **not** inferred or silently applied:
unsupported user_version is an explicit error pending a versioned migration.

No data is deleted as part of migration; if two legacy files differ, inspect
and back them up before choosing a source. An operator migrating on a production
workstation must verify product counts/GTINs, backing files and read-only
BarTender inspection before merging/releasing this change.

This PR does not relocate unrelated config/history/license data (#7 and separate
licensing work), change any Print/PrintOut calls, or publish a release.
