"""Issue #83 opt-in Windows BarTender COM smoke of *production* binding methods.

No Print, PrintOut, preview export, printer selection or spooler use.
Requires an authenticated source checkout at the exact PR head SHA. Only a
disposable BTW copy and synthetic CSV inside a private temporary folder are
opened. Existing BarTender processes are never stopped.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

FIELDS = ("ShortName", "ShortGTIN", "EXP_DATE", "PROD_DATE", "PART_NUM", "DM", "NUM")
DO_NOT_SAVE = 1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exact_checkout(expected_sha: str) -> str:
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo,
        check=True, capture_output=True, text=True,
    )
    actual = result.stdout.strip()
    if actual.casefold() != expected_sha.casefold():
        raise RuntimeError(f"Checkout SHA mismatch: expected {expected_sha}, got {actual}")
    # No source files may have local modifications: the tested logic must
    # match the commit, not a patched working tree.
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo,
        check=True, capture_output=True, text=True,
    )
    if dirty.stdout.strip():
        raise RuntimeError("Tracked source files changed after checkout")
    return actual


def _synthetic_rows() -> list[dict[str, str]]:
    common = {
        "ShortGTIN": "893",
        "EXP_DATE": "01.01.2030",
        "PROD_DATE": "01.01.2029",
    }
    result = []
    for index in (1, 2):
        result.append({
            **common,
            "ShortName": f"SYNTHETIC-{index}",
            "PART_NUM": f"TEST-{index}",
            "DM": "01" + "04601234567893"
                  + "21" + f"SYNTH{index:08d}" + "\x1d" + "93ABCD",
            "NUM": str(index),
        })
    return result


def _write_synthetic_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


class _SafeLogger:
    def log(self, message):
        # User paths and COM member names are safe; do not log raw codes.
        print("INFO:", message)

    def err(self, message):
        print("ERROR:", message)


def smoke(source_btw: Path, expected_sha: str) -> bool:
    _exact_checkout(expected_sha)
    # Avoid touching AutoPrint's real persistent profile even during import.
    if os.name != "nt":
        raise RuntimeError("Windows-only BarTender COM smoke")
    if source_btw.suffix.casefold() != ".btw":
        raise ValueError("Expected a .btw template")
    source_btw = source_btw.resolve(strict=True)
    original_hash = _sha256(source_btw)
    print(f"PR HEAD: {expected_sha}")
    print("BarTender input: operator BTW is copied, never opened directly")
    print("NO PRINT / PRINTOUT / XMLSCRIPT / PREVIEW in this harness")

    import pythoncom
    from win32com.client import DispatchEx
    from bartender_gui import App, DEFAULT_REQ_COLS

    pythoncom.CoInitialize()
    app = fmt = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="AutoPrint83Exact-", ignore_cleanup_errors=True
        ) as root:
            work = Path(root)
            # The module import above happens under the source checkout; no
            # production config, catalog or license file is read by this test.
            copy = work / "isolated_30x20.btw"
            tmp_csv = work / "tmp_batch.csv"
            shutil.copy2(source_btw, copy)
            copy_hash = _sha256(copy)
            rows = _synthetic_rows()
            _write_synthetic_csv(tmp_csv, rows)

            guard = object.__new__(App)
            guard.logger = _SafeLogger()
            guard.REQ_COLS = list(DEFAULT_REQ_COLS)

            try:
                app = DispatchEx("BarTender.Application")
                app.Visible = False
                fmt = app.Formats.Open(str(copy), False, "")
                report = guard._template_batch_report(fmt, part_mode="manual")
                print(f"TEMPLATE can_batch={report['can_batch']}; "
                      f"db_count={report['db_count']}; "
                      f"GS1_database_DM={guard._requires_database_dm(report)}")
                if not report["can_batch"] or not guard._requires_database_dm(report):
                    raise RuntimeError(
                        "Template is not a verified GS1 database batch template; "
                        + str(report["batch_message"])
                    )

                if not guard._rebind_template_db(fmt, str(tmp_csv)):
                    raise RuntimeError("ACTUAL PR _rebind_template_db returned False")
                print("PR BINDING AND REFRESH: PASS")

                if not guard._verify_dm_batch_database(fmt, str(tmp_csv), rows):
                    raise RuntimeError("ACTUAL PR GS1 schema/data preflight rejected CSV")
                print("PR GS1 PREFLIGHT: PASS")

                # Exact same production pre-submit flag and RecordRange path.
                fmt.UseDatabase = True
                fmt.SelectRecordsAtPrint = False
                fmt.RecordRange = str(f"1-{len(rows)}")
                if not guard._check_template_db(fmt, str(tmp_csv)):
                    raise RuntimeError("ACTUAL PR final _check_template_db returned False")
                print("PR FINAL BINDING READBACK: PASS")
                print("NO SUBMIT WAS ATTEMPTED")
            finally:
                if fmt is not None:
                    try:
                        fmt.Close(DO_NOT_SAVE)
                    finally:
                        fmt = None
                if app is not None:
                    try:
                        app.Quit(DO_NOT_SAVE)
                    finally:
                        app = None

                if _sha256(copy) != copy_hash:
                    raise RuntimeError("Disposable BTW modified")
    finally:
        pythoncom.CoUninitialize()
        if _sha256(source_btw) != original_hash:
            raise RuntimeError("ORIGINAL BTW CHANGED — INVESTIGATE BEFORE ANY PRINT")
        print("Original BTW SHA256 unchanged: True")
        print("NO Print / PrintOut submitted by diagnostic")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoPrint #83 exact-head NO-PRINT smoke")
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    return 0 if smoke(args.btw, args.expected_sha) else 1


if __name__ == "__main__":
    raise SystemExit(main())
