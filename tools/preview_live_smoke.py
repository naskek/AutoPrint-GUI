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
import sys

# Resolve the repository modules when this script is invoked by path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bartender_com
from bartender_com import BT, BT_DO_NOT_SAVE_CHANGES


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Logger:
    def log(self, message):
        print("INFO:", message)

    def err(self, message):
        print("ERROR:", message)


# These symbols use per-record GS1 processing in the production BTW.
# A raw DM SetNamedSubStringValue + template ExportToFile can produce a
# different image while silently bypassing the database/VBScript pipeline.
GS1_TRANSFORMED_NAMES = frozenset({
    "GS1_GTIN", "GS1_SERIAL", "GS1_CRYPTO"
})


def assert_named_only_is_safe(names):
    names = set(names)
    gs1 = GS1_TRANSFORMED_NAMES & names
    if gs1:
        raise RuntimeError(
            "Named-only diagnostic is INVALID for GS1/VBScript-backed templates "
            f"({', '.join(sorted(gs1))}). Use database-backed preview verification; "
            "raw DM PNG cannot validate GS1 encoding."
        )
    required = {"ShortName", "ShortGTIN", "PROD_DATE", "PART_NUM", "DM", "NUM"}
    missing = required - names
    if missing:
        raise RuntimeError(
            f"Named-only diagnostic unsupported: missing {sorted(missing)}"
        )


def smoke(template: Path, source_csv: Path, named_diagnostic: bool = False) -> int:
    before = sha256(template)
    with source_csv.open(encoding="utf-8-sig", newline="") as inp:
        rows = list(csv.DictReader(inp, delimiter="\t"))
    if len(rows) != 2 or not rows[0].get("DM") or rows[0]["DM"] == rows[1].get("DM"):
        raise ValueError("Контрольный CSV должен содержать два разных кода DM")

    output_root = Path(tempfile.mkdtemp(prefix="autoprint-preview60-")).resolve()
    print("OUTPUT DIRECTORY:", output_root)
    if named_diagnostic:
        print("DIAGNOSTIC ONLY: checking named data source rendering; NOT #60 acceptance")
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
                if named_diagnostic:
                    names = {item.Name for item in fmt.NamedSubStrings}
                    assert_named_only_is_safe(names)
                bt.apply_fields(fmt, {
                    "ShortName": row.get("ShortName") or row.get("NAME") or "TEST",
                    "ShortGTIN": row.get("ShortGTIN") or str(row.get("GTIN") or "")[-3:],
                    "EXP_DATE": ("01.05.2027" if i == 1 else "02.05.2027"),
                    "PROD_DATE": prod_date,
                    "PART_NUM": part,
                    "DM": row["DM"],
                    "NUM": str(i),
                })
                image = output_root / f"preview_{i}.png"
                if named_diagnostic:
                    # Intentionally separate operator-selected, NO-PRINT diagnostic.
                    # This does not change the production preview path or try a
                    # fallback after a failed database lookup.
                    fmt.ExportToFile(str(image), "PNG", 1, 300, 0)
                elif not bt.export_preview(fmt, str(image)):
                    raise RuntimeError(f"Preview {i}: export FAILED; no stale image accepted")
                if not image.is_file():
                    raise RuntimeError(f"Preview {i}: PNG missing; no stale image accepted")
            finally:
                if fmt is not None:
                    fmt.Close(BT_DO_NOT_SAVE_CHANGES)
        if sha256(output_root / "preview_1.png") == sha256(output_root / "preview_2.png"):
            raise RuntimeError("Both PNGs are byte-identical despite different row/part/date")
        if named_diagnostic:
            print("NAMED DIAGNOSTIC: PNGs differ; NOT #60 PASS")
            print("MANUAL CHECK: verify dates, batch number, product name and decoded GS1 DM")
        else:
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
    parser.add_argument(
        "--diagnose-named-only", action="store_true",
        help="Opt-in NO-PRINT rendering diagnostic; never treated as #60 acceptance",
    )
    args = parser.parse_args()
    template = args.btw.resolve(strict=True)
    source_csv = args.csv.resolve(strict=True)
    if os.path.commonpath([str(template.parent), str(source_csv)]) == str(template.parent):
        raise SystemExit("Input CSV cannot be inside BTW folder; use isolated test paths")
    return smoke(template, source_csv, named_diagnostic=args.diagnose_named_only)


if __name__ == "__main__":
    raise SystemExit(main())
