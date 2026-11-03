# Release readiness — Issue #68 (candidate 3.2.5)

Status: **3.2.5 candidate is being prepared; NOT AUTHORIZED TO TAG OR PUBLISH.**
The earlier release-safety PR #77 was merged. This file tracks the separate
version-bump PR, its CI and its remaining release gates.

## Source of truth and current production

- Last published production: **v3.2.4** in both private AutoPrint-GUI
  and public binary-only AutoPrint-GUI-Releases. Public v3.2.4 has
  `AutoPrint_Setup_3.2.4.exe` and `BarTenderGUI_Setup_3.2.4.exe`.
- Last verified `main` before this release-candidate branch:
  `820ed03a1a7649dcfd27f46e8b2eb5e8fcdfe68c`, with `APP_VERSION=3.2.4`.
  The release-candidate branch now sets **`APP_VERSION=3.2.5`**.
- GitHub confirmed no `v3.2.5` Git tag in either private or public repository
  at preparation time; recheck tags, releases and main immediately before tagging.
- Production/frozen installer upgrade must not replace or lose user BTW,
  SQLite catalog, history/status, config, licensing, or other user data.

## Merged dependencies and explicit bundled-template decision

- **#5 / PR #73**: user BTW preservation merged, exact-head packaged and
  real BTW copy migration checks passed; exact-head real COM was skipped
  because BarTender processes were already running.
- **#6 / PR #74**: SQLite catalog persistence merged, including no-clobber
  first-run publication; packaged upgrade passed on exact source SHA.
- **#7 / PR #75**: persistent CSV history/status merged, exact-source
  packaged migration passed.
- **#30 / PR #76**: CSV browser layout/icons merged and accepted by operator
  on exact source SHA using a no-print GUI smoke.
- **#68 / PR #77**: release preflight hardening merged.
- **#60 and #66**: GS1/preview work already in main and separately tested
  with real BarTender, without physical printing on the 3.2.5 candidate.
- **#63**: **explicitly deferred for 3.2.5 by owner**. Existing bundled
  historical BTW assets and their default metadata remain EXACTLY unchanged;
  do not replace with private working templates and do not silently assert
  bundled defaults are validated production GS1 layouts. Operator BTW
  overrides must retain priority.
- **Test-only PR #78**: synthetic integration and production-installer
  replacement smoke passed, **NEVER MERGE**.

## Evidence obtained before merges (2026-11-03)

These checks ran on an **integration-only composite branch**, not on
independently merged commits in main. Never merge test PR #78.

- Combined candidate `0ce8a5d0dffb59596520c0c5971ae6af3eb4475a`:
  [Windows CI](https://github.com/naskek/AutoPrint-GUI/actions/runs/41596576460)
  **4/4 PASS** (unit-regression, COM-contract, build installer, isolated
  packaged-data migration).
- Authentic public v3.2.4 installer downloaded, checked against published
  SHA-256 `bf69876759b3a8d356d8fe26494b7a13bb2bd2f9e92dd6e56dfa3ba16087c168`,
  installed on disposable Windows CI, then replaced by the composite
  candidate. SQLite catalog (1 synthetic product), 2 synthetic CSV histories,
  BTW, config and an **invalid synthetic** license survived install,
  uninstall and reinstall, verified by SHA-256.
- Real candidate PyInstaller EXE launched on Windows and correctly displayed
  the missing/invalid license dialog, then exited on closing it. This was
  **not** a licensed frozen-GUI startup test.
- Local BarTender 2022 R5 Enterprise COM start/stop PASS. Static export
  of a copy of real BTW yielded PNG 189x189 and original BTW SHA-256 unchanged.
  Two **synthetic** BTXML inputs produced visually different rendered
  DataMatrix and substituted TEST-A/B, BATCH-A/B, NUM 10001/20002. No print
  was submitted. Scanner validation of GS1 01/21/93 not implied.
- User accepted CSV-browser layout (dark theme) from integration candidate.
  This is an operator visual acceptance of the combined UI, not proof of
  independent PR #76 exact-head smoke.

**Remaining release gates:** exact release-candidate 3.2.5 CI, actual
versioned 3.2.4→3.2.5 installer upgrade and installed EXE version check,
operator-data checksums, final source main ancestry and unique tag checks.
A fully licensed frozen GUI run, read-only real BarTender inspection after
packaged migration and physical print remain separately scoped live tests
and are not established by synthetic CI. The #63 bundled-default decision
is to leave those files unchanged in this release. **No tag, Release or
physical print is authorized by this PR.**

## Required release sequence

1. **DONE:** PRs #73–77 merged in order after exact-head review/smoke
   and green required CI on each resulting main commit. Issue #63 decision:
   bundled BTW assets unchanged.
2. **DONE at candidate creation:** verified merged main and available
   version `3.2.5`. Recheck tag and release uniqueness before publication.
3. **IN PROGRESS:** separate candidate PR bumps `APP_VERSION` to 3.2.5
   with matching updater/version tests, release notes and artifact manifest.
   The source PR must pass required CI; no tag/release on the PR branch.
4. Validate built candidate installer in disposable Windows environment,
   including **v3.2.4 -> candidate** upgrade, clean install, uninstall,
   reinstall, BarTenderGUI -> AutoPrint legacy data, BTW and SQLite history,
   config and license persistence; compare SHA-256 before/after.
5. Historical bundled 16x16 and 30x20 BTW are unchanged by explicit owner
   decision. Existing real BarTender 2022 R5 dynamic-preview test is evidence
   only for the earlier integrated source candidate, not the final installed
   3.2.5 binary. No automatic or duplicate printing; physical print is
   separate opt-in.
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
