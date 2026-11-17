# AutoPrint 3.2.6 — release readiness (Issue #100)

Status: **candidate PR; NOT merged, tagged or published**. Treat all statuses as pending until confirmed by GitHub for the exact current PR SHA. Source of truth for mutable state is GitHub; this document is a checklist, not permission to publish.

## Baseline verified 17 Nov 2026

- Latest *private* production release and *public binary-only* updater release: **v3.2.5**, published 3 Nov 2026.
- Both channels expose **AutoPrint_Setup_3.2.5.exe** and **BarTenderGUI_Setup_3.2.5.exe**, byte-identical SHA-256 `3b21d56563ffc32f7c6fef95402cb8f6442f1bdd04d0d07098b7cff7a763ff2f`.
- v3.2.6 tag/Release were absent in *both* repositories at preparation time. Recheck immediately before creating a tag.
- Starting `main`: `8247be3c2d5e100966ae06512681194e6b904758`, `APP_VERSION=3.2.5`; `git compare v3.2.5...main` shows **only one commit**, merged PR [#97](https://github.com/naskek/AutoPrint-GUI/pull/97) (Issue #95).
- #97's exact head passed unit/COM/Windows CI, BarTender 2022 R5 no-print BTXML and isolated AutoPrint GUI preview with two different 01/21/93 codes. It changes **only preview eligibility**, not production batch submission.
- Known-good v3.2.1 updater requires legacy `BarTenderGUI_Setup_<version>.exe` alias. The release workflow still publishes both aliases to the public [naskek/AutoPrint-GUI-Releases](https://github.com/naskek/AutoPrint-GUI-Releases) and private source repository.
- Operator-owned BTW are **not** added to source or bundled resources. #63 bundled default IMO remains deferred; #94 barcode-semantic preflight remains separate; docs-only PR #96 remains separate and unmerged.

## Candidate scope

- `app_version.APP_VERSION`: 3.2.5 → **3.2.6**, with explicit regression assertion and updater alias tests.
- Keep `UPDATE_REPOSITORY = "naskek/AutoPrint-GUI-Releases"`. Do **not** change install AppId, licensing, print paths, templates or versioned release workflow.
- Expand ephemeral `build-windows` packaged smoke on release PRs: pin/authenticate real public v3.2.5 installer SHA-256, install its real EXE with same AppId in disposable runner, inject **synthetic only** user-data templates, SQLite catalog and CSV history/status, config, invalid license and old install-dir mutable fixtures; install candidate in-place; verify candidate Windows EXE version and byte-for-byte persistence across upgrade, uninstall and reinstall.
- The runner smoke cannot validate live BarTender, licensed frozen GUI startup or actual printing. It must never run on a real operator PC. Existing previously verified #97 exact-head no-print GUI smoke relates to source-run preview, not the new installer binary.
- The release workflow re-runs unit/regression + fake COM tests, builds the actual Windows installer from a tag whose commit is contained in main, creates **two byte-identical installer names**, publishes privately and publicly, and verifies public asset SHA-256.

## Required gates — DO NOT SKIP

1. PR #100 created from fresh main with Issue, diff self-reviewed, no unrelated UI/COM/packaging/print edits.
2. Required PR-head CI: `unit-regression-tests`, `bartender-com-contract`, `build-windows` all PASS. The Windows step must show **real v3.2.5 → v3.2.6** packaged upgrade PASS with source SHA and SHA-256 of all synthetic fixtures unchanged, and reinstall PASS.
3. Inspect old v3.2.5 and candidate installer Inno AppId/directory, build metadata, bundled BTW package hashes, and persistence conditions. Fail closed on missing previous installer, unexpected installer path/version, partial migration or insufficient proof.
4. Verify candidate HEAD SHA, no unresolved review threads and CI; obtain separate explicit merge permission for **this** PR, merge, then verify new `main` and Issue closure.
5. Recheck v3.2.6 absent in tags and Releases **both** repos; confirm tested merge commit is in main; the user explicitly requested a release, but if any mismatch/blocker appears stop and report before publication. **Never release from a work branch.**
6. Create annotated tag `v3.2.6` **only on the approved main commit**, then inspect `release.yml` run (no manual artifact replacement). Do not retry/overwrite version if publication partially fails between private and public repositories.
7. Verify private+public Releases, both exact installer asset names, byte-identical SHA-256, public latest release and updater selection from v3.2.5/v3.2.1. Verify compiled binary contains no source code in the public channel.
8. Production workstation update: **only after release fully verified**. Capture backups of existing user BTW, catalog, csv-history/status, config, licenses, sticker template and any install-dir legacy mutable data; verify downloaded installer SHA-256; use in-app updater or controlled installer; confirm installed `3.2.6` and no-print preview for two records. No physical submit is required for this preview-only release; never accidentally reprint unique marking codes.

## Regression risks

Installer same AppId upgrade, legacy BarTenderGUI filenames and paths, loss of source/installed mutable data, embedded unverified BTW assets, updater selecting wrong asset, accidental source publication to binary channel, frozen licensing startup, and untested live physical printing. The user requested rapid delivery, but a green CI alone cannot establish a safe production update.

Current release candidate is NOT a production release and must not be installed on the real workstation without its packaged smoke and published SHA-256.
