"""Opt-in NO-PRINT GS1 sample-data proof for Issue #60 on BarTender 2022 R5.

Uses the DOCUMENTED indexed Format.UsedNamesSampleData property to set six
DATABASE FIELD sample values in a copied BTW; then uses ExportToFile.
This is NOT production preview and NOT evidence of GS1 validity until
the operator compares decoded PNG symbols to raw 01/21/93 source bytes.

No Format.Print/PrintOut/DB GetDatabase calls, no retries/fallbacks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import shutil
import tempfile

from tools.gs1_dm_audit import BATCH_FIELDS, parse_dm93

DB_PREFIX = "tmp_batch.tmp_batch."
PREVIEW_DB_FIELDS = (
    "ShortName", "ShortGTIN", "PROD_DATE", "PART_NUM", "DM", "NUM",
)
EXPECTED_NAMED_GS1 = frozenset({"GS1_GTIN", "GS1_SERIAL", "GS1_CRYPTO"})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_sample_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not set(BATCH_FIELDS).issubset(header):
            raise ValueError("Current batch CSV header is missing/duplicating fields")
        results = []
        for row in reader:
            dm = row.get("DM") or ""
            if dm == "X":
                continue
            if not dm:
                raise ValueError("Blank production DM (not calibration)")
            parse_dm93(dm)
            if any((row.get(key) is None) for key in PREVIEW_DB_FIELDS):
                raise ValueError("Missing runtime CSV field")
            if any(existing["DM"] == dm for existing in results):
                raise ValueError("Duplicate DM")
            results.append(row)
            if len(results) == 2:
                break
    if len(results) != 2:
        raise ValueError("Expected two distinct valid 01/21/93 marking codes")
    return results


def set_documented_sample_values(fmt, values, pythoncom_module):
    """One official COM PROPERTYPUT invocation per field; no fallbacks.

    Vendor signature: UsedNamesSampleData(0, DatabaseField) = Value.
    PyIDispatch.Invoke passes the arguments in normal order and reverses
    them internally for COM property-put (DISPID_PROPERTYPUT).
    """
    used = {str(name).strip().casefold() for name in
            str(fmt.UsedNames(1, "\n") or "").splitlines()}
    expected = {f"{DB_PREFIX}{field}".casefold() for field in PREVIEW_DB_FIELDS}
    if not expected.issubset(used):
        raise RuntimeError("BTW lacks required tmp_batch database-field contract")
    named = {str(name).strip() for name in
             str(fmt.UsedNames(0, "\n") or "").splitlines()}
    if not EXPECTED_NAMED_GS1.issubset(named):
        raise RuntimeError("BTW lacks expected GS1 component data sources")
    # Name lookup reads metadata only; GetTypeInfo is not required.
    dispid = fmt._oleobj_.GetIDsOfNames("UsedNamesSampleData")
    if not isinstance(dispid, int):
        raise RuntimeError("No unambiguous COM DISPID for UsedNamesSampleData")
    fmt.UseDatabase = False
    for field in PREVIEW_DB_FIELDS:
        fmt._oleobj_.Invoke(
            dispid, 0, pythoncom_module.DISPATCH_PROPERTYPUT, False,
            0, f"{DB_PREFIX}{field}", str(values[field]),
        )


def probe(source_btw: Path, csv_path: Path) -> int:
    import pythoncom
    from win32com.client import DispatchEx

    rows = validate_sample_rows(csv_path)
    original = sha256(source_btw)
    output_root = Path(tempfile.mkdtemp(prefix="autoprint-preview60-db-sample-"))
    copied_btw = output_root / "isolated_16x16.btw"
    shutil.copy2(source_btw, copied_btw)
    copied_hash = sha256(copied_btw)
    print("OUTPUT DIRECTORY:", output_root)
    print("SOURCE SHA256:", original)
    print("Sample setter: BarTender documented property only; NO database rebind")
    app = fmt = None
    try:
        app = DispatchEx("BarTender.Application")
        app.Visible = False
        for index, row in enumerate(rows, 1):
            fmt = app.Formats.Open(str(copied_btw), False, "")
            try:
                payload = {
                    field: row[field] for field in PREVIEW_DB_FIELDS
                }
                payload["PROD_DATE"] = f"0{index}.11.2026"
                payload["PART_NUM"] = "111" if index == 1 else "222"
                set_documented_sample_values(fmt, payload, pythoncom)
                image_path = output_root / f"preview_{index}.png"
                fmt.ExportToFile(str(image_path), "PNG", 1, 300, 0)
                if not image_path.is_file() or image_path.stat().st_size == 0:
                    raise RuntimeError(f"Preview {index}: PNG not created")
                print(f"Preview {index}: PNG created (NOT GS1 validated)")
            finally:
                try:
                    fmt.Close(1)
                finally:
                    fmt = None

        if sha256(output_root / "preview_1.png") == sha256(output_root / "preview_2.png"):
            raise RuntimeError("Byte-identical PNGs; no visible sample change")

        print("TWO PNGs DIFFER: diagnostic only, NOT Issue #60 PASS")
        print("MANUAL: check name, date, batch, GS1 FNC1 + 01/21/93 values")
        print("Source marking code values are intentionally not logged.")
        return 0
    finally:
        try:
            if app is not None:
                app.Quit(1)
        finally:
            if sha256(source_btw) != original or sha256(copied_btw) != copied_hash:
                raise RuntimeError("BTW changed: invalid probe")
            print("NO PRINT / NO PRINTOUT / BTW UNCHANGED")


def main() -> int:
    parser = argparse.ArgumentParser(description="NO-PRINT DB sample rendering probe")
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    btw = args.btw.resolve(strict=True)
    csv_path = args.csv.resolve(strict=True)
    if btw.suffix.casefold() != ".btw":
        parser.error("Expected a .btw file")
    return probe(btw, csv_path)


if __name__ == "__main__":
    raise SystemExit(main())
