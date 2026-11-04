"""Issue #83: inspect the actual BarTender Databases.Item dispatch contract.

OPT-IN. One documented Databases.Item invocation, NO fallback/probing overloads.
No print submission, preview export, printer setup, user BTW edits, or production
CSV/profile access. Intended for the deployed 2022 R5/pywin32 host where the
native VBScript variant succeeded but the Python exact-head smoke did not.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile

from tools.smoke_83_exact_head import (
    DO_NOT_SAVE,
    _exact_checkout,
    _sha256,
    _synthetic_rows,
    _write_synthetic_csv,
)


def _describe_item_type_info(databases) -> None:
    """Read type-library metadata only; never invoke a BarTender DB lookup."""
    try:
        type_info = databases._oleobj_.GetTypeInfo()
        count = int(type_info.GetTypeAttr().cFuncs)
        found = False
        for index in range(min(count, 160)):
            descriptor = type_info.GetFuncDesc(index)
            names = type_info.GetNames(descriptor.memid)
            if names and str(names[0]).casefold() in ("item", "getdatabase"):
                found = True
                args = getattr(descriptor, "args", descriptor[2])
                print(
                    f"TYPEINFO name={names[0]!s} dispid={descriptor.memid} "
                    f"invkind={descriptor.invkind} params={args!r}"
                )
        if not found:
            print("TYPEINFO: no named Item/GetDatabase entry; inspect type library")
    except Exception as exc:
        print(f"TYPEINFO unavailable: {type(exc).__name__}: {exc}")


def diagnose(source_btw: Path, expected_sha: str) -> int:
    _exact_checkout(expected_sha)
    if os.name != "nt":
        raise RuntimeError("Windows-only BarTender COM diagnostic")
    source_btw = source_btw.resolve(strict=True)
    if source_btw.suffix.casefold() != ".btw":
        raise ValueError("Expected a BTW")
    original_hash = _sha256(source_btw)
    print("DIAGNOSTIC: READ TYPEINFO + ONE ITEM INVOCATION; NO PRINT/PRINTOUT")
    print("EXPECTED HEAD:", expected_sha)

    import pythoncom
    from win32com.client import DispatchEx

    pythoncom.CoInitialize()
    app = fmt = None
    stage = "initialize"
    try:
        with tempfile.TemporaryDirectory(
            prefix="AP83ComStage-", ignore_cleanup_errors=True
        ) as workdir:
            work = Path(workdir)
            os.environ["AUTOPRINT_USER_DATA_DIR"] = str(work / "isolated_profile")
            from bartender_gui import App

            copy = work / "copy.btw"
            csv_path = work / "tmp_batch.csv"
            shutil.copy2(source_btw, copy)
            copy_hash = _sha256(copy)
            _write_synthetic_csv(csv_path, _synthetic_rows())

            try:
                stage = "DispatchEx"
                app = DispatchEx("BarTender.Application")
                app.Visible = False

                stage = "Formats.Open"
                fmt = app.Formats.Open(str(copy), False, "")

                stage = "Databases collection"
                databases = fmt.Databases
                print("PYWIN32 databases wrapper:", type(databases).__module__,
                      type(databases).__name__)
                print("Databases.Count:", int(databases.Count))
                if int(databases.Count) != 1:
                    raise RuntimeError("Need exactly one database")

                _describe_item_type_info(databases)
                stage = "Databases.Item (exact PR helper)"
                db = App._batch_database_one(databases)  # exactly ONE Item
                print("STAGE Databases.Item: PASS")
                stage = "Database.TextFile"
                text_file = db.TextFile
                print("STAGE Database.TextFile: PASS")
                stage = "TextFile.FileName original getter"
                current = str(text_file.FileName)
                print("STAGE FileName getter: PASS (value redacted)")
                stage = "TextFile.FileName setter"
                text_file.FileName = str(csv_path)
                print("STAGE FileName setter: PASS")
                stage = "TextFile.FileName readback"
                rebound = str(text_file.FileName)
                if os.path.normcase(os.path.abspath(rebound)) != os.path.normcase(
                    os.path.abspath(csv_path)
                ):
                    raise RuntimeError("FileName readback mismatch")
                print("STAGE FileName readback: PASS")
                stage = "PrintSetup.RefreshDatabases setter/readback"
                fmt.PrintSetup.RefreshDatabases = True
                if not bool(fmt.PrintSetup.RefreshDatabases):
                    raise RuntimeError("RefreshDatabases did not stay enabled")
                print("STAGE RefreshDatabases: PASS")
                print("NO Print / PrintOut submitted")
                return 0
            except Exception as exc:
                print(f"DIAGNOSTIC FAILED AT: {stage}")
                print(f"ERROR TYPE: {type(exc).__name__}")
                print("ERROR DETAILS:", str(exc)[:500])
                return 2
            finally:
                try:
                    if fmt is not None:
                        fmt.Close(DO_NOT_SAVE)
                finally:
                    fmt = None
                    if app is not None:
                        app.Quit(DO_NOT_SAVE)
                        app = None
                if _sha256(copy) != copy_hash:
                    raise RuntimeError("Disposable BTW modified")
    finally:
        pythoncom.CoUninitialize()
        if _sha256(source_btw) != original_hash:
            raise RuntimeError("Original BTW changed")
        print("Original BTW unchanged: True")


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoPrint #83 COM argument diagnostic")
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    return diagnose(args.btw, args.expected_sha)


if __name__ == "__main__":
    raise SystemExit(main())
