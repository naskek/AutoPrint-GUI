"""#83 opt-in two-record BTXML GS1 preview: ONE request, NO printing.

Proves whether the same explicit BTXML RecordSet mechanism that worked for
separate one-record previews can render TWO different records in ONE export.

This does not submit a print job and does not by itself verify BTXML <Print>.
The user BTW is never opened directly; only a disposable copy is used.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

from tools.smoke_83_exact_head import (
    _exact_checkout,
    _sha256,
    _synthetic_rows,
    _write_synthetic_csv,
)
from bartender_com import BT, BT_DO_NOT_SAVE_CHANGES


def build_two_row_preview_xml(template: Path, csv_path: Path, output_dir: Path) -> str:
    """Use production preview's documented RecordSet; expand its record range."""
    xml = BT._build_gs1_btxml_preview_request(
        str(template), str(csv_path), str(output_dir)
    )
    root = ET.fromstring(xml)
    if len(root.findall(".//ExportPrintPreviewToImage")) != 1:
        raise RuntimeError("Expected exactly one preview-export command")
    if root.findall(".//Print") or root.findall(".//PrintOut"):
        raise RuntimeError("Print submission command forbidden in diagnostic")
    sets = root.findall(".//RecordSet")
    if len(sets) != 1 or sets[0].get("Name") != "tmp_batch":
        raise RuntimeError("Expected one explicit tmp_batch RecordSet")
    if sets[0].findtext("FileName") != str(csv_path):
        raise RuntimeError("Preview recordset path mismatch")
    setup = root.find(".//ExportPrintPreviewToImage/PrintSetup")
    if setup is None or setup.findtext("UseDatabase") != "true":
        raise RuntimeError("Missing explicit BTXML database enablement")
    selection = setup.find("RecordRange")
    if selection is None:
        raise RuntimeError("Missing explicit BTXML record range")
    selection.text = "1-2"
    return ET.tostring(root, encoding="unicode")


def probe(original_btw: Path, expected_sha: str) -> int:
    _exact_checkout(expected_sha)
    if os.name != "nt":
        raise RuntimeError("Windows-only BarTender 2022 live COM check")
    original_btw = original_btw.resolve(strict=True)
    if original_btw.suffix.casefold() != ".btw":
        raise ValueError("Expected .btw template")
    before = _sha256(original_btw)
    print("SOURCE PR HEAD:", expected_sha)
    print("NO BTXML <Print>, Format.Print or PrintOut: preview export only")
    print("Original BTW opened directly: False")

    import pythoncom
    from win32com.client import DispatchEx, VARIANT
    pythoncom.CoInitialize()
    app = None
    export_dir = Path(tempfile.mkdtemp(prefix="AP83-TWO-ROW-IMAGES-"))
    print("OUTPUT:", export_dir)
    try:
        with tempfile.TemporaryDirectory(
            prefix="AP83-TWO-ROW-SOURCE-", ignore_cleanup_errors=True
        ) as srcdir:
            folder = Path(srcdir)
            os.environ["AUTOPRINT_USER_DATA_DIR"] = str(folder / "isolated_profile")
            copy = folder / "isolated_30x20.btw"
            csv = folder / "tmp_batch.csv"
            shutil.copy2(original_btw, copy)
            copied_sha = _sha256(copy)
            rows = _synthetic_rows()
            _write_synthetic_csv(csv, rows)
            xml = build_two_row_preview_xml(copy, csv, export_dir)
            print("CSV synthetic rows:", len(rows))
            print("Single XMLScript ExportPrintPreviewToImage call: planned")
            try:
                app = DispatchEx("BarTender.Application")
                app.Visible = False
                messages = VARIANT(
                    pythoncom.VT_BYREF | pythoncom.VT_DISPATCH, None
                )
                response = app.XMLScript(xml, 0, messages)
                if not BT._btxml_preview_response_ok(response):
                    raise RuntimeError("BTXML did not confirm preview export")
                images = sorted(export_dir.glob("*.png"))
                print("EXPORTED PNG:", len(images))
                if len(images) != 2 or any(p.stat().st_size == 0 for p in images):
                    raise RuntimeError(
                        "Expected exactly two nonempty preview images from one BTXML call"
                    )
                one, two = (_sha256(path) for path in images)
                if one == two:
                    raise RuntimeError(
                        "Two separate source rows generated identical preview images"
                    )
                print("PNG images differ: True")
                print("TWO-ROW BTXML PREVIEW: PASS")
                print("PNG content still requires GS1 symbol decoding")
                return 0
            finally:
                if app is not None:
                    try:
                        app.Quit(BT_DO_NOT_SAVE_CHANGES)
                    finally:
                        app = None
                if _sha256(copy) != copied_sha:
                    raise RuntimeError("Disposable BTW changed during preview")
    finally:
        pythoncom.CoUninitialize()
        if _sha256(original_btw) != before:
            raise RuntimeError("Original operator BTW changed")
        print("Original BTW unchanged: True")
        print("NO Print / PrintOut submitted")


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoPrint #83 two-record NO PRINT BTXML preview")
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    return probe(args.btw, args.expected_sha)


if __name__ == "__main__":
    raise SystemExit(main())
