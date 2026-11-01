"""No-print Windows verification of AutoPrint's *production adapter* for #60.

Unlike tools/probe_preview_gs1_btxml.py, this smoke calls the actual
bartender_com.BT.supports_gs1_btxml_preview/export_gs1_btxml_preview methods
used by the normal-label GUI. The original BTW/CSV are read only. Every
successful PNG must still be decoded to establish correct GS1 semantics.

Use an isolated DispatchEx BarTender process; NEVER Print/PrintOut.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bartender_com
from tools.gs1_dm_audit import BATCH_FIELDS, parse_dm93


class Logger:
    def log(self, message):
        print("BT:", message)

    def err(self, message):
        print("BT ERROR:", message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not set(BATCH_FIELDS).issubset(header):
            raise ValueError("Expected standard tmp_batch CSV fields")
        selected = []
        seen = set()
        for row in reader:
            dm = row.get("DM") or ""
            if dm == "X":
                continue
            parse_dm93(dm)
            if dm in seen:
                raise ValueError("Duplicate source DM")
            seen.add(dm)
            selected.append(row)
            if len(selected) == 2:
                break
    if len(selected) != 2:
        raise ValueError("Expected two valid distinct marking rows")
    return selected


def inspect(template: Path, source_csv: Path) -> int:
    from win32com.client import DispatchEx

    operator_hash = sha256(template)
    rows = test_rows(source_csv)
    output = Path(tempfile.mkdtemp(prefix="autoprint-live60-adapter-"))
    print("OUTPUT DIRECTORY:", output)
    print("EXPECTED HEAD: run only from checked-out PR #62 SHA")
    print("Source template SHA256:", operator_hash)
    print("Input DM values are never printed")

    bartender_com.Dispatch = DispatchEx
    bt = bartender_com.BT(Logger())
    fmt = None
    try:
        bt.start()
        fmt = bt.open_format(str(template))
        if not bt.supports_gs1_btxml_preview(fmt):
            raise RuntimeError("Actual BTW does not match required GS1 DB template")

        for index, source_row in enumerate(rows, 1):
            payload = {
                key: source_row.get(key) or ""
                for key in bartender_com.PREVIEW_DATA_FIELDS
            }
            # Two distinct UI-style runtime values, not values in original CSV.
            payload["PROD_DATE"] = f"0{index}.11.2026"
            payload["PART_NUM"] = "111" if index == 1 else "222"
            payload["NUM"] = str(index)
            dest = output / f"case_{index}"
            dest.mkdir()
            pattern = dest / "preview_%PageNumber%.png"
            if not bt.export_gs1_btxml_preview(str(template), payload, str(pattern)):
                raise RuntimeError(f"Adapter case {index}: export FAILED")
            png = dest / "preview_1.png"
            if not png.is_file() or not png.stat().st_size:
                raise RuntimeError(f"Adapter case {index}: PNG missing")
            print(
                f"Adapter case {index}: PNG exported; expected date "
                f"{payload['PROD_DATE']} part {payload['PART_NUM']}; "
                "GS1 NOT YET VALIDATED"
            )
        first = output / "case_1" / "preview_1.png"
        second = output / "case_2" / "preview_1.png"
        if sha256(first) == sha256(second):
            raise RuntimeError("Two source rows produced IDENTICAL PNGs")
        print("ADAPTER RENDERING: PASS (not a GS1 or GUI smoke PASS)")
        print("CHECK: decode GS1/FNC1 + AI01/21/93 against original rows")
        print("NO Print / PrintOut / production source modifications")
        return 0
    finally:
        try:
            if fmt is not None:
                fmt.Close(bartender_com.BT_DO_NOT_SAVE_CHANGES)
        finally:
            bt.stop()
        if sha256(template) != operator_hash:
            raise RuntimeError("SOURCE BTW CHANGED: smoke INVALID")
        print("SOURCE BTW UNCHANGED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    return inspect(args.btw.resolve(strict=True), args.csv.resolve(strict=True))


if __name__ == "__main__":
    raise SystemExit(main())
