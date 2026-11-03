# AutoPrint 3.2.5 — release candidate notes

**Status:** candidate, not published. These notes are not authorization to tag or release.

## Changes since 3.2.4

- **User BarTender templates (#5 / PR #73):** independent persistent template storage and template registry, non-destructive legacy BTW migration and conflict preservation.
- **Product catalog (#6 / PR #74):** operator-owned SQLite catalog migrated away from application binaries, backup/idempotency protection, and atomic no-overwrite initialization to avoid two instances clobbering a newly created catalog.
- **CSV print history (#7 / PR #75):** history/status stored persistently outside the install directory, with legacy configuration migration. Historical status is retained without re-submitting print jobs.
- **CSV file browser (#30 / PR #76):** improved usable list layout, compact rows, always-yellow folder icons.
- **Release safety (#68 / PR #77):** verify public channel access and unique newer version before publishing; reject already published release assets instead of overwriting.

The existing GS1 marking and live preview improvements already in `main` are retained. They do not imply that physical printing, scanner readability, or all printers have been verified on the 3.2.5 packaged candidate.

## Compatibility and user data

- Production source: published `v3.2.4` binary. Updater channel stays **`naskek/AutoPrint-GUI-Releases`** (binary assets only).
- Publish both **`AutoPrint_Setup_3.2.5.exe`** and **`BarTenderGUI_Setup_3.2.5.exe`** as byte-identical installer assets for older clients.
- Existing user BTW, SQLite catalog, CSV history/status, configuration and license files must remain intact when installing, updating, reinstalling or uninstalling. The installed application must not overwrite operator-owned BTW files.
- **Bundled BTW are deliberately unchanged in 3.2.5.** Issue #63 is deferred at the owner's request; the historical defaults may differ from current operator/GS1 templates. Users must select their verified local templates rather than assume bundled defaults are current production layouts.

## Release gating

- Source PRs #73–77 are merged into `main`; mandatory integration CI has passed.
- CI of this exact 3.2.5 candidate and Windows smoke of the **actual built installer** are mandatory.
- Upgrade test: public 3.2.4 installer → **versioned candidate 3.2.5**, verifying installed EXE version, user-file SHA-256, catalog/history/BTW migrations and persistence across reinstall/uninstall.
- A frozen EXE test with an intentionally invalid test license may confirm the license gate, **not** full licensed GUI behavior. Real BarTender startup, template inspection and print/GS1 semantics require appropriate opt-in verification and must not be claimed based only on mock/CI.
- Source `APP_VERSION`, tag and release uniqueness, `main` ancestry, both installer names and matching SHA-256 must be verified again immediately before publication.
- **No tag, private/public Release or change to the production update channel** without separate explicit owner authorization.

## Published artifact manifest (fill only after release approval)

| Item | Expected name | SHA-256 |
| --- | --- | --- |
| Main installer | `AutoPrint_Setup_3.2.5.exe` | Pending built artifact verification |
| Legacy-compatible alias | `BarTenderGUI_Setup_3.2.5.exe` | Must equal main installer digest |
| Public release tag | `v3.2.5` | Not created |
