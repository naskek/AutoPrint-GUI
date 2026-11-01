"""NO-PRINT GS1 database RECORD preview experiment for AutoPrint Issue #60.

BarTender's ExportToFile only exports the *template*, and even changing
UsedNamesSampleData does not reproduce 01/21/93/VBScript on the tested BTW.
This experiment invokes the officially documented print-preview RANGE export
instead. It opens an isolated BTW copy and keeps the database connection
as configured in the BTW; it never changes the source CSV or sends a print.

The supplied --csv is ONLY a read-only expected-record list. The ActiveX
database's effective CSV path cannot be verified on BarTender 2022 R5,
so this test must NOT be interpreted as a GS1 PASS until the symbols are
decoded and compared to the exact raw records in that CSV.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.gs1_dm_audit import BATCH_FIELDS, parse_dm93

BT_DO_NOT_SAVE_CHANGES = 1
BT_COLORS_24BIT = 4
PREVIEW_WHITE_BACKGROUND = 0xFFFFFF


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_two_real_records(csv_path: Path) -> list[int]:
    """1-based record indices; include calibration rows in the count."""
    selected = []
    seen = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not set(BATCH_FIELDS).issubset(header):
            raise ValueError("CSV lacks unique production batch fields")
        for i, row in enumerate(reader, 1):
            dm = row.get("DM") or ""
            if dm == "X":
                continue
            parse_dm93(dm)
            if dm in seen:
                raise ValueError("Duplicate marking code in selected preview rows")
            seen.add(dm)
            selected.append(i)
            if len(selected) == 2:
                break
    if len(selected) != 2:
        raise ValueError("Need two distinct valid marking records")
    return selected


def export_one_record(fmt, row_index: int, output: Path, messages):
    """Exact documented BarTender ActiveX print-preview range invocation."""
    # Crucial: unlike set_common_print_flags, keep the database ENABLED.
    fmt.UseDatabase = True
    fmt.PrintSetup.UseDatabase = True
    fmt.SelectRecordsAtPrint = False
    fmt.RecordRange = str(row_index)
    result = fmt.ExportPrintPreviewRangeToImage(
        "1", str(output), "preview_%PageNumber%.png", "PNG",
        BT_COLORS_24BIT, 300, PREVIEW_WHITE_BACKGROUND,
        BT_DO_NOT_SAVE_CHANGES, False, False, messages,
    )
    if int(result) != 0:
        raise RuntimeError(f"BarTender preview returned failure: {result!r}")


def probe(source_btw: Path, source_csv: Path) -> int:
    import pythoncom
    from win32com.client import DispatchEx, VARIANT

    selected = select_two_real_records(source_csv)
    btw_hash = sha256(source_btw)
    output_root = Path(tempfile.mkdtemp(prefix="autoprint-preview60-record-"))
    copied_btw = output_root / "isolated_template.btw"
    shutil.copy2(source_btw, copied_btw)
    copied_hash = sha256(copied_btw)
    print("OUTPUT DIRECTORY:", output_root)
    print("SOURCE BTW SHA256:", btw_hash)
    print("SELECTED ROWS:", selected)
    print("Important: ActiveX does NOT prove which CSV the BTW reads")
    app = fmt = None
    try:
        app = DispatchEx("BarTender.Application")
        app.Visible = False
        for i, record_number in enumerate(selected, 1):
            image_dir = output_root / f"record_{i}"
            image_dir.mkdir()
            fmt = app.Formats.Open(str(copied_btw), False, "")
            try:
                db_names = {
                    name.strip().casefold()
                    for name in str(fmt.UsedNames(1, "\n") or "").splitlines()
                }
                if "tmp_batch.tmp_batch.dm" not in db_names:
                    raise RuntimeError("Missing expected DB-bound DM field")
                if int(fmt.Databases.Count or 0) != 1:
                    raise RuntimeError("Expected exactly one template database")

                messages = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_DISPATCH, None)
                export_one_record(fmt, record_number, image_dir, messages)

                images = sorted(image_dir.glob("*.png"))
                if len(images) != 1 or images[0].stat().st_size == 0:
                    raise RuntimeError(f"Record {record_number}: expected exactly one PNG")
                print(f"Record {record_number}: preview exported; GS1 NOT validated")
            finally:
                try:
                    fmt.Close(1)
                finally:
                    fmt = None

        # Identical output is a definitive failure for two distinct source DMs.
        # Distinct output is NOT proof of correct GS1 bytes or database binding.
        if sha256(output_root / "record_1" / "preview_1.png") == sha256(
            output_root / "record_2" / "preview_1.png"
        ):
            raise RuntimeError(
                "FAIL: two distinct source DM rows produced IDENTICAL PNGs; "
                "database/record binding not confirmed"
            )
        print("DIAGNOSTIC COMPLETED; PNGs differ but NOT Issue #60 PASS")
        print("MANUAL CHECK: compare decoded GS1 01/21/93 and FNC1 with exact source")
        print("NO Print / NO PrintOut / NO DATABASE PATH CHANGE")
        return 0
    finally:
        try:
            if app is not None:
                app.Quit(1)
        finally:
            if sha256(source_btw) != btw_hash or sha256(copied_btw) != copied_hash:
                raise RuntimeError("Original or copied BTW changed; inspection invalid")
            print("BTW SHA256 unchanged; no print submission")


def main() -> int:
    parser = argparse.ArgumentParser(description="BarTender GS1 record-preview proof, NO PRINT")
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    source_btw = args.btw.resolve(strict=True)
    source_csv = args.csv.resolve(strict=True)
    if source_btw.suffix.lower() != ".btw":
        parser.error("Expected a .btw file")
    return probe(source_btw, source_csv)


if __name__ == "__main__":
    raise SystemExit(main())
