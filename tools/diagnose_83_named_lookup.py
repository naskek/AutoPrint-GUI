"""#83: opt-in single named Databases.Item lookup on real BarTender 2022 COM.

Seagull documents and recommends the string database name over collection
index because ordering is not guaranteed. This diagnostic invokes only the
documented Item("tmp_batch") once, never an indexed fallback.

NO Print, PrintOut, XMLScript, preview, or printer calls. Uses an isolated user
profile, one copied operator BTW, and two synthetic TSV rows under TEMP.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

from tools.smoke_83_exact_head import (
    DO_NOT_SAVE,
    _exact_checkout,
    _sha256,
    _synthetic_rows,
    _write_synthetic_csv,
)

RECORDSET = "tmp_batch"


def _names_in_configuration(config: str) -> set[str]:
    root = ET.fromstring(config)
    names: set[str] = set()
    for node in root.iter():
        if str(node.tag).rsplit("}", 1)[-1].casefold() == "name":
            if (node.text or "").strip():
                names.add(node.text.strip().casefold())
        for key, value in node.attrib.items():
            if str(key).rsplit("}", 1)[-1].casefold() == "name":
                if str(value).strip():
                    names.add(str(value).strip().casefold())
    return names


def probe(source_btw: Path, expected_sha: str) -> int:
    _exact_checkout(expected_sha)
    if os.name != "nt":
        raise RuntimeError("Windows-only BarTender COM diagnostic")
    source_btw = source_btw.resolve(strict=True)
    if source_btw.suffix.casefold() != ".btw":
        raise ValueError("Expected .btw document")
    before = _sha256(source_btw)
    print("PR HEAD:", expected_sha)
    print("NO PRINT, PRINTOUT OR PREVIEW; ONE NAMED ITEM CALL ONLY")

    import pythoncom
    from win32com.client import DispatchEx

    pythoncom.CoInitialize()
    application = document = None
    stage = "starting COM"
    try:
        with tempfile.TemporaryDirectory(
            prefix="AP83Named-", ignore_cleanup_errors=True
        ) as root:
            work = Path(root)
            os.environ["AUTOPRINT_USER_DATA_DIR"] = str(work / "isolated_profile")
            copied_btw = work / "isolated_30x20.btw"
            csv_path = work / "tmp_batch.csv"
            shutil.copy2(source_btw, copied_btw)
            copied_hash = _sha256(copied_btw)
            _write_synthetic_csv(csv_path, _synthetic_rows())

            try:
                stage = "DispatchEx"
                application = DispatchEx("BarTender.Application")
                application.Visible = False
                stage = "Formats.Open"
                document = application.Formats.Open(str(copied_btw), False, "")
                stage = "Databases.Count/Configuration"
                databases = document.Databases
                count = int(databases.Count)
                print("Database count:", count)
                if count != 1:
                    raise RuntimeError("Expected exactly one database")
                names = _names_in_configuration(str(databases.Configuration))
                if RECORDSET.casefold() not in names:
                    raise RuntimeError("Configured recordset tmp_batch not present")
                print("Configuration recordset tmp_batch: PRESENT")

                stage = "Databases.Item('tmp_batch')"
                database = databases.Item(RECORDSET)
                print("NAMED Item lookup: PASS")

                stage = "Database.TextFile.FileName getter"
                text_file = database.TextFile
                old_file = str(text_file.FileName or "")
                if not old_file:
                    raise RuntimeError("Original text file source is empty")
                print("TextFile.FileName getter: PASS")

                stage = "TextFile.FileName synthetic setter"
                text_file.FileName = str(csv_path)
                print("Synthetic setter: PASS")

                stage = "TextFile.FileName readback"
                rebound = str(text_file.FileName or "")
                if os.path.normcase(os.path.abspath(rebound)) != (
                    os.path.normcase(os.path.abspath(csv_path))
                ):
                    raise RuntimeError("Synthetic path readback mismatch")
                print("Synthetic readback: PASS")

                stage = "PrintSetup.RefreshDatabases"
                document.PrintSetup.RefreshDatabases = True
                if not bool(document.PrintSetup.RefreshDatabases):
                    raise RuntimeError("Database refresh not enabled")
                print("RefreshDatabases: PASS")
                print("NAMED COM CONTRACT: PASS; NO PRINT SUBMITTED")
                return 0
            except Exception as exc:
                print("FAILED STAGE:", stage)
                print("ERROR:", type(exc).__name__, str(exc)[:450])
                return 2
            finally:
                try:
                    if document is not None:
                        document.Close(DO_NOT_SAVE)
                finally:
                    document = None
                    if application is not None:
                        application.Quit(DO_NOT_SAVE)
                        application = None
                if _sha256(copied_btw) != copied_hash:
                    raise RuntimeError("Disposable BTW unexpectedly modified")
    finally:
        pythoncom.CoUninitialize()
        if _sha256(source_btw) != before:
            raise RuntimeError("Original operator BTW unexpectedly modified")
        print("Original BTW unchanged: True")
        print("NO Print / PrintOut / preview called")


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoPrint #83 named COM smoke")
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    return probe(args.btw, args.expected_sha)


if __name__ == "__main__":
    raise SystemExit(main())
