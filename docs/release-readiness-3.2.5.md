# Release readiness — Issue #68 (candidate 3.2.5)

Status: **NOT READY TO TAG OR PUBLISH**. This is a planning/preflight PR,
not the version-bump/release authorization.

## Source of truth and current production

- Last published production: **v3.2.4** in both private AutoPrint-GUI
  and public binary-only AutoPrint-GUI-Releases. Public v3.2.4 has
  `AutoPrint_Setup_3.2.4.exe` and `BarTenderGUI_Setup_3.2.4.exe`.
- `APP_VERSION` in main is still **3.2.4**. Candidate **3.2.5**
  must be checked for uniqueness again immediately before version bump/tag.
- Production/frozen installer upgrade must not replace or lose user BTW,
  SQLite catalog, history/status, config, licensing, or other user data.

## Dependencies (each is an independent scope/PR)

- #66 GS1 DataMatrix and #60 preview: closed in main; their integration
  still needs a no-duplicate-print GS1/source-to-PDF release smoke.
- #5 / PR #73 user BTW preservation — draft; packaged migration/real BTW
  inspection not fully proved.
- #6 / PR #74 SQLite catalog — draft; exact-head packaged migration smoke
  not done on real-data-derived isolated fixture.
- #7 / PR #75 CSV history/status — draft; packed upgrade/legacy history
  migration smoke not done.
- #30 / PR #76 CSV browser — draft; Windows live GUI visual smoke not done.
- #63 bundled production BTW audit is open. **Do not commit personal/live
  production BTW** merely to satisfy a stale manifest. Decide separately
  whether to ship verified non-personal defaults or rely on user-owned
  templates; never silently replace operator files.
- Inspect #69 source-run licensing behavior if it blocks isolated GUI smoke.
  No license key or production license file belongs in this PR.

## Required release sequence

1. Complete independent PR reviews, required CI (unit-regression-tests,
   bartender-com-contract, build-windows) and risk-class Windows smoke on
   exact heads; owner approves merges separately. Do not merge Draft PRs
   solely to get a release.
2. Verify merged main contains intended fixes; reconcile conflicts and
   re-run CI/smoke if head changed. Recheck version/tag/release uniqueness
   and choose 3.2.5 only if still unused.
3. Prepare a **separate release/version-bump PR** once dependencies are in
   main: APP_VERSION, release notes, artifact manifest, updater compatibility.
   Never reuse 3.2.2 or silently ship a new binary identified as 3.2.4.
4. Validate built candidate installer in disposable Windows environment,
   including **v3.2.4 -> candidate** upgrade, clean install, uninstall,
   reinstall, BarTenderGUI -> AutoPrint legacy data, BTW and SQLite history,
   config and license persistence; compare SHA-256 before/after.
5. Verify 16x16 and 30x20 BTW with real BarTender 2022 R5 without submitting
   print jobs where possible; separate explicit opt-in for safe live/PDF or
   physical printing. No repeat submit after ambiguous COM response.
6. Confirm release workflow re-runs tests and builds only from a main-contained
   commit, public token access works, and **both installer aliases** are
   byte-identical and SHA-256 verified.
7. Only after **separate explicit authorization**, create tag/Release
   from approved main commit; verify private release, public binary-only
   channel, both asset names, digest and updater detection. Do not push
   tags or create Releases automatically from the PR.

## Publication guard added by this PR

`release.yml` checks public credentials and unique/newer version in
**both** release repositories *before* building or publishing. A rerun that
finds an existing tag Release fails closed. Published assets are never
replaced using `--clobber` or `gh release upload`.

Publication is not an atomic transaction across two GitHub repositories.
If GitHub fails after the private Release is created, STOP, inspect and
reconcile the partial state manually under explicit approval. Never
automatically overwrite or reuse that version.
