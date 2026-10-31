"""No-print COM inspection of a BarTender template's database/DM bindings.

Never calls Format.Print, PrintOut, or any preview/export method.
Does not change source paths, template properties, or the input BTW.
"""
from __future__ import annotations

import argparse
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_configuration_hints(xml_text: str):
    """Extract only connection names and file names; never print raw XML.

    The configuration may contain credentials, so all other fields are omitted.
    """
    root = ET.fromstring(xml_text)
    hints = []
    approved = {"name", "filename", "recordsetname", "databasename"}
    for element in root.iter():
        name = str(element.tag).split("}")[-1].lower()
        if name in approved and element.text and element.text.strip():
            hints.append((name, element.text.strip()[:180]))
        for attr, value in element.attrib.items():
            key = str(attr).split("}")[-1].lower()
            if key in approved and value.strip():
                hints.append((key, value.strip()[:180]))
    # Preserve order and avoid potentially huge output.
    return list(dict.fromkeys(hints))[:20]


def inspect_btw(path: Path) -> int:
    from win32com.client import DispatchEx

    original = sha256(path)
    app = None
    fmt = None
    try:
        app = DispatchEx("BarTender.Application")
        app.Visible = False
        fmt = app.Formats.Open(str(path), False, "")
        for kind, label in ((0, "NamedSubStrings"), (1, "Database Fields"), (2, "Template Fields")):
            try:
                names = fmt.UsedNames(kind, ";")
                print(f"{label}: {names!s}")
            except Exception as exc:
                print(f"{label}: ERROR {exc}")
        try:
            databases = fmt.Databases
            count = int(databases.Count or 0)
        except Exception as exc:
            print(f"Databases: ERROR {exc}")
            return 2
        print(f"Databases.Count={count}")
        # Seagull documents this read-only XML property as the way to
        # discover recordset/database names without dereferencing a Database.
        try:
            raw_config = databases.Configuration
            if isinstance(raw_config, str) and raw_config.strip():
                for key, value in database_configuration_hints(raw_config):
                    print(f"Configuration.{key}: {value}")
                if not database_configuration_hints(raw_config):
                    print("Configuration: XML available but no name/file hints")
            else:
                print("Configuration: empty/non-text response")
        except Exception as exc:
            print(f"Configuration: ERROR {exc}")
        # The production document's configured database name is "tmp_batch".
        # Seagull recommends name lookup: integer indices are not stable.
        database_name = "tmp_batch"
        try:
            db = databases.GetDatabase(database_name)
        except Exception as exc:
            print(f"GetDatabase({database_name!r}): UNAVAILABLE {exc}")
            # An unavailable COM Database object does not disprove the live
            # schema: on production BarTender 2022 R5 both access modes fail,
            # while the same document prints variable records correctly.
            try:
                hints = database_configuration_hints(databases.Configuration)
                recordset_ok = any(
                    key == "name" and name.casefold() == "tmp_batch"
                    for key, name in hints
                )
                fields = str(fmt.UsedNames(1, ";") or "").casefold().split(";")
                field_ok = "tmp_batch.tmp_batch.dm" in fields
            except Exception as inspect_exc:
                print(f"Schema inspection: ERROR {inspect_exc}")
                return 2
            if recordset_ok and field_ok:
                print("PASS: GS1 DM database schema; actual CSV path not proven by COM")
                print("PRINT / PDF / PHYSICAL output still requires separate smoke")
                return 0
            print("FAIL: tmp_batch/DM database field not confirmed")
            return 2
        for label, accessor in (
            ("Name", lambda: db.Name),
            ("Type", lambda: db.Type),
            ("FileName", lambda: db.FileName),
            ("TextFile.FileName", lambda: db.TextFile.FileName),
        ):
            try:
                print(f"DB[{database_name}].{label}={accessor()!s}")
            except Exception as exc:
                print(f"DB[{database_name}].{label}: ERROR {exc}")
        return 0
    finally:
        try:
            if fmt is not None:
                fmt.Close(1)  # btDoNotSaveChanges
        finally:
            if app is not None:
                app.Quit(1)  # btDoNotSaveChanges
        if sha256(path) != original:
            raise RuntimeError("BTW CHANGED unexpectedly: abort verification")
        print("BTW SHA256 unchanged; NO Print/PrintOut called")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only BarTender 2022 GS1 template inspection")
    parser.add_argument("btw", type=Path)
    args = parser.parse_args()
    if not args.btw.is_file():
        parser.error("BTW file does not exist")
    return inspect_btw(args.btw.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
