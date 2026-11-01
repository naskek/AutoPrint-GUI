"""Opt-in, NO-PRINT Windows/BarTender live preview smoke for Issue #60.

Run from a detached PR-head checkout with two-row Kontur source CSV and
an ISOLATED copy of a 30x20/16x16 BTW file. Never use a production BTW.
This harness does not call Print or PrintOut and closes all formats no-save.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path
import tempfile

import bartender_com
from bartender_com import BT, BT_DO_NOT_SAVE_CHANGES


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Logger:
    def log(self, message):
        print("INFO:", message)

    def err(self, message):
        print("ERROR:", message)


def smoke(template: Path, source_csv: Path) -> int:
    before = sha256(template)
    with source_csv.open(encoding="utf-8-sig", newline="") as inp:
        rows = list(csv.DictReader(inp, delimiter="\t"))
    if len(rows) != 2 or not rows[0].get("DM") or rows[0]["DM"] == rows[1].get("DM"):
        raise ValueError("Контрольный CSV должен содержать два разных кода DM")

    output_root = Path(tempfile.mkdtemp(prefix="autoprint-preview60-")).resolve()
    print("OUTPUT DIRECTORY:", output_root)
    # Use a dedicated BarTender.Application, never attach to an operator
    # Designer/AutoPrint COM instance.
    from win32com.client import DispatchEx
    bartender_com.Dispatch = DispatchEx
    bt = BT(Logger())
    try:
        bt.start()
        for i, (row, prod_date, part) in enumerate(zip(
            rows, ("01.11.2026", "02.11.2026"), ("111", "222")
        ), 1):
            fmt = None
            try:
                fmt = bt.open_format(str(template))
                bt.set_common_print_flags(fmt)
                bt.apply_fields(fmt, {
                    "ShortName": row.get("NAME") or "TEST",
                    "ShortGTIN": str(row.get("GTIN") or "")[-3:],
                    "EXP_DATE": ("01.05.2027" if i == 1 else "02.05.2027"),
                    "PROD_DATE": prod_date,
                    "PART_NUM": part,
                    "DM": row["DM"],
                    "NUM": str(i),
                })
                image = output_root / f"preview_{i}.png"
                if not bt.export_preview(fmt, str(image)) or not image.is_file():
                    raise RuntimeError(f"Preview {i}: export FAILED; no stale image accepted")
            finally:
                if fmt is not None:
                    fmt.Close(BT_DO_NOT_SAVE_CHANGES)
        if sha256(output_root / "preview_1.png") == sha256(output_root / "preview_2.png"):
            raise RuntimeError("Both PNGs are byte-identical despite different row/part/date")
        print("2 DIFFERENT PNGs: PASS (inspect visually for exact field values)")
        return 0
    finally:
        try:
            bt.stop()
        finally:
            if sha256(template) != before:
                raise RuntimeError("TEST BTW CHANGED: invalid smoke")
            print("NO PRINT / NO PRINTOUT / BTW UNCHANGED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    template = args.btw.resolve(strict=True)
    source_csv = args.csv.resolve(strict=True)
    if os.path.commonpath([str(template.parent), str(source_csv)]) == str(template.parent):
        raise SystemExit("Input CSV cannot be inside BTW folder; use isolated test paths")
    return smoke(template, source_csv)


if __name__ == "__main__":
    raise SystemExit(main())
