# User BTW preservation contract (Issue #5)

## Storage boundary

- **Persistent operator-owned files**: `%LOCALAPPDATA%\AutoPrintData\templates\*.btw` and `%LOCALAPPDATA%\AutoPrintData\template-registry.json`. If `AUTOPRINT_USER_DATA_DIR` is explicitly set for an isolated test, the same structure is under that directory.
- **Installer-owned assets**: `bundled_templates\*.btw` inside the PyInstaller bundle. These are historical defaults only; **do not commit the operator's actual working/production BTW files**. New/updated bundled assets are a separate decision, not part of Issue #5.
- **Legacy locations**: an existing `BarTenderGUI\templates`, `AutoPrint\templates`, and historical install roots. On frozen startup, BTW files are copied into user data. The originals remain untouched. Name conflicts preserve both versions: the active target and a conflict copy in `AutoPrintData\templates\archive\*.legacy-<hash>.btw`. Older `*.legacy-*.btw` backups already in the active `templates` directory are recognized and left **in place**; re-running migration will not duplicate those archives. Inspect the log to choose the correct version when sources disagree. Migrations may need manual operator selection; **do not infer the right GS1 production BTW by filename alone**.
- **Legacy backup archive** (`templates\archive`): stores **new** name-conflict backups. The resolver only checks known filenames at the top level, never recursively in this archive. This is a separation of storage, **not an endorsement or automatic replacement of any BTW**.

The runtime resolution order remains `user template -> bundled default -> legacy fallback`. Because user-data and bundle storage are distinct, replacing/removing the installer must not overwrite operator BTW files.

## Existing root-level backups (Issue #98)

Upgrading an existing profile does **not** move, delete, or rename any `*.legacy-*.btw` already located directly under `AutoPrintData\templates`, even if an identical file is still in `BarTenderGUI` or `AutoPrint`. Root-level backups can have explicit registry or legacy-config references; a hash match alone does not prove they are unused. The new archive location only applies when creating **new** migration-conflict backups.

To clean up a workstation, first collect a read-only inventory of active template IDs and filenames from `template-registry.json`, the sticker setting and any legacy `formats` / `btw_16` / `btw_30` overrides; include BTW SHA-256 and the current installed build. Confirm no selected path targets a backup. Only after explicit operator approval should a separate, verified migration move the existing backups; test exact content integrity and rollback. Do **not** archive `.import-<hash>.btw` merely because of its suffix: this filename can be actively selected by the editor.

## Imports and edits

The template editor always imports into persistent user-data storage. If a BTW with the same name already exists:
- identical file: reuse it without rewriting;
- different file: copy the new BTW to `<stem>.import-<SHA256-first-16>.btw` and return that filename to the editor;
- the previous file stays intact; the editor saves the chosen filename under its logical template ID.
- sticker imports use the same no-clobber behavior and save the selected filename in the persistent registry.

Any copy is staged in the destination directory and only published after a successful full copy. Errors should leave previous BTW and metadata intact. Application and installer never delete the persistent template directory.

## Persistent template metadata

The template editor saves its `label_templates` mapping (logical IDs, name, filename, part mode and printer) and the **sticker basename** in `template-registry.json`, independent of `config/config.json`.

On first updated startup, if no registry exists, it snapshots available old editor settings from install config. On subsequent starts, a valid registry is authoritative. An invalid/corrupted existing registry is **not silently reset or overwritten**; investigate and restore the original file.

This task does **not** migrate unrelated config fields, history, product database, or licenses. Those are tracked separately. If a user uninstalled an older version **before ever running the registry-aware version**, historical editor metadata cannot be reconstructed automatically from a now-missing config; existing BTW files can still be imported manually.

## Verification matrix

### Automated

1. Import new unique BTW; repeated import is idempotent.
2. Import different bytes under same name; both survive; editor points to the new content-addressed filename.
3. Legacy `BarTenderGUI -> AutoPrint` conflict; originals and multiple conflict variants survive two migrations under `templates/archive`; existing root-level backups are recognized without being recopied.
4. Simulate a failed read/write copy; no partial user BTW and original data intact.
5. Snapshot old `label_templates` to registry; simulate install-directory deletion and fresh config; restore template IDs, filenames and part modes without touching BTW.
6. Corrupt registry is not overwritten.
7. Windows CI `unit-regression-tests`, `bartender-com-contract`, `build-windows`.

### Mandatory packaged Windows smoke before merge

Use **exact PR HEAD SHA** built by PyInstaller and Inno Setup on a separate Windows test install path, with `AUTOPRINT_USER_DATA_DIR` pointing to a disposable isolated profile. Never point the test at production user-data, old installation, license, catalog, or real print queue. Use synthetic disposable BTW bytes for installer/uninstall preservation checks. For COM inspection, opt in to a real BarTender session and open a **copy** of a known-good BTW without calling `Print` or `PrintOut`.

- Fresh install: ensure imported BTW goes to persistent profile, not the install dir; verify bundled fallback and filename resolution.
- Upgrade to a newly built installer: existing user BTW SHA-256 and registry metadata remain identical, independent of bundled defaults.
- Uninstall -> reinstall: both user BTW and editor metadata survive.
- Import conflicting same name; verify original remains, replacement selected and referenced, repeated import unchanged.
- Simulate `BarTenderGUI\templates` with unique and conflicting files: confirm backups under `templates/archive` and idempotence; no legacy source removed.
- Read-only/locked file: clear failure and no partial destination; no mutation of user data.
- Check real BarTender opens migrated **copy** of BTW in source-run and built app (non-print inspection only).
- Verify user-data directory is not registered as installer-owned payload and is not deleted by uninstall.

**Smoke PASS is valid only for the exact current PR SHA. New commits require repeating packaged smoke.** CI is not proof of real Windows installer migration, live BarTender or physical print. Merge requires packaged smoke PASS and separate explicit user approval.


### Disposable GitHub Actions packaged smoke

The Windows `build-windows` job runs `tools/packaged_template_smoke.ps1` after building the actual Inno Setup EXE. The script is restricted to an ephemeral GitHub Actions Windows runner, checks for existing AutoPrint registration, and uses a disposable install directory plus `AUTOPRINT_USER_DATA_DIR`. During this test only, `AUTOPRINT_PACKAGED_SMOKE=1` and `GITHUB_ACTIONS=true` suppress the installer's normal silent-mode GUI auto-launch; production installer/updater behavior is unchanged.

It verifies: first installation and bundled default assets; content-addressed conflict import; two archived legacy-conflict variants and idempotent source-level migration; metadata restoration; same-version in-place installer update; uninstall; reinstall; and exact SHA-256 preservation of all synthetic user BTW files and registry metadata. Inno install/uninstall logs are uploaded on CI failure. No real print job or BarTender COM session is started.

**Coverage limits:** This does not test an upgrade from a different production installer version, the installed frozen GUI startup, automatic migration inside that frozen GUI, or a non-print BarTender open of a real BTW. Real BarTender verification remains a separate opt-in Windows smoke step. CI PASS alone must not be described as end-to-end live BarTender/package migration verification.
