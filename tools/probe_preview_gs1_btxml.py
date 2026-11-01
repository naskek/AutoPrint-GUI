"""Isolated NO-PRINT BarTender 2022 GS1 BTXML RecordSet preview proof.

This uses the vendor-documented BTXML <ExportPrintPreviewToImage> command,
with <RecordSet Name="tmp_batch" Type="btTextFile"><FileName> pointing
at an isolated ONE-ROW CSV. The raw GS (ASCII 29) lives in the CSV, never
in XML. Unlike the legacy ActiveX GetDatabase() lookup, BTXML RecordSet
names the database recordset directly.

This is an OPT-IN diagnostic, not part of normal AutoPrint runtime.
It never creates a <Print> command or calls Format.Print/PrintOut.
It attempts to delete temporary BTW/CSV copies after BarTender exits;
if BarTender still holds a file, locked temporary inputs are reported as
pending cleanup rather than obscuring the original export failure.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.gs1_dm_audit import BATCH_FIELDS, parse_dm93

# BtXMLSourceType.btXMLScriptString; BarTender type library enum 0.
BT_XML_SCRIPT_STRING = 0
BT_DO_NOT_SAVE = 1


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def choose_rows(source: Path) -> list[dict[str, str]]:
    results = []
    seen = set()
    with source.open("r", encoding="utf-8-sig", newline="") as inp:
        reader = csv.DictReader(inp, delimiter="\t")
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not set(BATCH_FIELDS).issubset(fields):
            raise ValueError("Source CSV missing/duplicate batch field(s)")
        for row in reader:
            dm = row.get("DM") or ""
            if dm == "X":
                continue
            parse_dm93(dm)
            if dm in seen:
                raise ValueError("Duplicate noncalibration DM")
            seen.add(dm)
            results.append({k: row.get(k) or "" for k in BATCH_FIELDS})
            if len(results) == 2:
                break
    if len(results) != 2:
        raise ValueError("Need two different valid GS1 01/21/93 source rows")
    return results


def write_one_row(path: Path, row: dict[str, str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=BATCH_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerow({field: row[field] for field in BATCH_FIELDS})


def build_preview_xml(btw: Path, csv_path: Path, png_dir: Path) -> str:
    root = ET.Element("XMLScript", Version="2.0", Name="AutoPrint60Probe")
    command = ET.SubElement(root, "Command", Name="PreviewOnly")
    preview = ET.SubElement(
        command, "ExportPrintPreviewToImage", ReturnImageInResponse="false"
    )
    tags = {
        "Format": str(btw),
        "Folder": str(png_dir),
        "FileNameTemplate": "preview_%PageNumber%.png",
        "ImageFormatType": "PNG",
        "Colors": "btColors24Bit",
        "DPI": "300",
        "BackgroundColor": "16777215",
        "Overwrite": "false",
        "IncludeMargins": "false",
        "IncludeBorder": "false",
    }
    for key, value in tags.items():
        ET.SubElement(preview, key).text = value

    recordset = ET.SubElement(
        preview, "RecordSet", Name="tmp_batch", Type="btTextFile",
        AddIfNone="false",
    )
    ET.SubElement(recordset, "FileName").text = str(csv_path)
    ET.SubElement(recordset, "Delimitation").text = "btDelimTab"
    ET.SubElement(recordset, "UseFieldNamesFromFirstRecord").text = "true"

    # BarTender BTXML ExportPrintPreviewToImage honors this documented
    # PrintSetup switch. The previous probe omitted it and did not confirm
    # that the exported image used the supplied one-row record set.
    print_setup = ET.SubElement(preview, "PrintSetup")
    for tag, value in (
        ("UseDatabase", "true"),
        ("RecordRange", "1"),
        ("AutoPrintAgain", "false"),
        ("EnablePrompting", "false"),
        ("IdenticalCopiesOfLabel", "1"),
        ("NumberSerializedLabels", "1"),
    ):
        ET.SubElement(print_setup, tag).text = value

    # Guard against regression introducing submit-capable BTXML commands.
    if root.find(".//Print") is not None:
        raise RuntimeError("Unsafe BTXML command: Print is forbidden")
    return ET.tostring(root, encoding="unicode")


def validate_response(response: object) -> list[str]:
    if not isinstance(response, str):
        raise RuntimeError("BarTender returned a non-XML response")
    root = ET.fromstring(response)
    if root.tag != "Response":
        raise RuntimeError("BarTender did not return a Response element")

    # Do NOT print XML responses or Message.Text: they can contain customer
    # paths, CSV data and individual serialization/marking values.
    summaries = []
    has_errors = root.find(".//Error") is not None
    for message in root.iter("Message"):
        attrs = message.attrib
        severity = attrs.get("Severity", "Unknown")
        category = attrs.get("Category", "Unknown")
        identifier = attrs.get("ID", "Unknown")
        # Only fixed metadata fields, never message body or other attributes.
        summaries.append(
            f"ID={identifier[:32]} severity={severity[:32]} category={category[:48]}"
        )
        if severity.casefold() == "error":
            has_errors = True
    if has_errors:
        raise RuntimeError(
            "BarTender BTXML error: "
            + ("; ".join(summaries[:10]) if summaries else "response contained Error")
        )
    if root.find("./Command/ExportPrintPreviewToImage") is None:
        raise RuntimeError(
            "BTXML export command missing in response; "
            + ("; ".join(summaries[:10]) if summaries else "no messages")
        )
    return summaries



def summarize_com_messages(byref_messages: object) -> list[str]:
    """Read only documented Message.Number/Severity, never Message text."""
    try:
        collection = getattr(byref_messages, "value", None)
        if collection is None:
            return []
        count = min(10, max(0, int(collection.Count or 0)))
    except Exception:
        return []
    result = []
    for index in range(1, count + 1):
        try:
            message = collection.GetMessage(index)
            number = str(message.Number)[:32]
            severity = str(message.Severity)[:16]
        except Exception:
            result.append(f"message {index}: metadata unavailable")
        else:
            result.append(f"Number={number} Severity={severity}")
    return result


def probe(source_btw: Path, source_csv: Path) -> int:
    import pythoncom
    from win32com.client import DispatchEx, VARIANT

    selected = choose_rows(source_csv)
    expected_btw_hash = sha256(source_btw)
    output = Path(tempfile.mkdtemp(prefix="autoprint-preview60-btxml-"))
    print("OUTPUT DIRECTORY:", output)
    print("BTW SHA256:", expected_btw_hash)
    print("Source rows: two distinct valid 01/21/93; codes not logged")

    app = None
    inputs = None
    try:
        # BarTender.Application.Quit() is asynchronous. Windows may still
        # hold the copied BTW or CSV after the COM call returns. Cleanup must
        # never conceal the actual export error.
        with tempfile.TemporaryDirectory(
            prefix="autoprint60-btxml-sources-", ignore_cleanup_errors=True
        ) as directory:
            inputs = Path(directory)
            copied_btw = inputs / "copy_16x16.btw"
            shutil.copy2(source_btw, copied_btw)
            copy_hash = sha256(copied_btw)

            try:
                app = DispatchEx("BarTender.Application")
                app.Visible = False
                for index, row in enumerate(selected, 1):
                    isolated_csv = inputs / f"record_{index}.csv"
                    image_dir = output / f"record_{index}"
                    image_dir.mkdir(parents=True)
                    write_one_row(isolated_csv, row)
                    xml = build_preview_xml(copied_btw, isolated_csv, image_dir)
                    messages = VARIANT(
                        pythoncom.VT_BYREF | pythoncom.VT_DISPATCH, None
                    )
                    response = app.XMLScript(xml, BT_XML_SCRIPT_STRING, messages)
                    summaries = validate_response(response)
                    summaries += summarize_com_messages(messages)
                    if summaries:
                        print("BTXML/COM message metadata:", "; ".join(summaries[:20]))
                    images = list(image_dir.glob("*.png"))
                    if len(images) != 1 or images[0].stat().st_size == 0:
                        raise RuntimeError(
                            f"Record {index}: expected one PNG, found {len(images)}; "
                            "BTXML completed without an image "
                            "(see sanitized messages above)"
                        )
                    print(f"Record {index}: one PNG exported (NOT GS1 validated)")
            finally:
                try:
                    if app is not None:
                        app.Quit(BT_DO_NOT_SAVE)
                finally:
                    app = None
                    if sha256(copied_btw) != copy_hash:
                        raise RuntimeError("Isolated BTW was modified unexpectedly")

        image1 = next((output / "record_1").glob("*.png"))
        image2 = next((output / "record_2").glob("*.png"))
        if sha256(image1) == sha256(image2):
            raise RuntimeError(
                "FAIL: two different GS1 DM inputs produced IDENTICAL PNGs"
            )
        print("IMAGES DIFFER; NOT a GS1 semantic PASS")
        print("VERIFY exact GS1 01/21/93, FNC1, dates and part manually")
        print("NO Print / NO PrintOut / NO source changes")
        return 0
    finally:
        if sha256(source_btw) != expected_btw_hash:
            raise RuntimeError("Original BTW unexpectedly changed")
        if inputs is not None and inputs.exists():
            print(
                "WARNING: Temporary source cleanup deferred: ", inputs,
                "(BarTender may still hold files; close its isolated process "
                "before removing the directory)"
            )
        else:
            print("BTW UNCHANGED; temporary BTW/CSV sources cleaned")


def main() -> int:
    parser = argparse.ArgumentParser(description="No-print BTXML GS1 preview smoke")
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    btw = args.btw.resolve(strict=True)
    csv_path = args.csv.resolve(strict=True)
    if btw.suffix.casefold() != ".btw":
        parser.error("Expected BTW document")
    return probe(btw, csv_path)


if __name__ == "__main__":
    raise SystemExit(main())
