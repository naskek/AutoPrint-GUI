"""Read-only, NO-PRINT inspection of a copy of a GS1 BarTender BTW for #60.

No database GetDatabase attempts, no setting sample values, no export,
no Print/PrintOut, no original BTW writes, no raw DM or VBScript output.
Use this to check *capabilities*, not rendering or GS1 correctness.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET


GS1_COMPONENTS = frozenset({"GS1_GTIN", "GS1_SERIAL", "GS1_CRYPTO"})
TARGET_MEMBERS = frozenset({
    "UsedNamesSampleData",
    "ExportPrintPreviewRangeToImage",
    "ExportToFile",
})
# IDispatch INVOKEKIND: 1=METHOD, 2=PROPERTYGET, 4=PROPERTYPUT, 8=PROPERTYPUTREF.
INVOKE_KINDS = {1: "method", 2: "get", 4: "put", 8: "putref"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_used_names(raw: object) -> set[str]:
    return {name.strip() for name in str(raw or "").split("\n") if name.strip()}


def inspect_contract(db_names: set[str], named_names: set[str]) -> dict:
    folded = {name.casefold() for name in db_names}
    return {
        "db_dm_used": "tmp_batch.tmp_batch.dm" in folded,
        "gs1_named_components": sorted(GS1_COMPONENTS.intersection(named_names)),
        "raw_dm_named": "DM" in named_names,
    }


def connection_hints(xml: object) -> list[tuple[str, str]]:
    """Return only connection/recordset names (never URLs, paths, passwords)."""
    if not isinstance(xml, str) or not xml.strip():
        return []
    root = ET.fromstring(xml)
    result: list[tuple[str, str]] = []
    for item in root.iter():
        tag = str(item.tag).split("}")[-1].casefold()
        if tag not in {"recordset", "databaseconnection"}:
            continue
        name = next((
            str(value).strip()
            for key, value in item.attrib.items()
            if str(key).split("}")[-1].casefold() == "name"
        ), "")
        if name:
            result.append((tag, name))
    return result[:15]


def member_signatures(type_info) -> dict[str, list[tuple[str, int]]]:
    """Read the COM type library; do not invoke any member.

    pywin32 TYPEATTR[6] is cFuncs, FUNCDESC[0] memid,
    FUNCDESC[4] invkind, FUNCDESC[6] cParams.
    """
    info = {}
    count = int(type_info.GetTypeAttr()[6])
    for index in range(count):
        desc = type_info.GetFuncDesc(index)
        names = type_info.GetNames(desc[0])
        if not names or names[0] not in TARGET_MEMBERS:
            continue
        info.setdefault(names[0], []).append((
            INVOKE_KINDS.get(int(desc[4]), f"other:{desc[4]}"),
            int(desc[6]),
        ))
    return info


def inspect(path: Path) -> int:
    from win32com.client import DispatchEx

    original_hash = digest(path)
    app = fmt = None
    with tempfile.TemporaryDirectory(prefix="ap60-readonly-btw-") as dirname:
        isolated_btw = Path(dirname) / "inspect.btw"
        shutil.copy2(path, isolated_btw)
        copy_hash = digest(isolated_btw)
        try:
            app = DispatchEx("BarTender.Application")
            app.Visible = False
            fmt = app.Formats.Open(str(isolated_btw), False, "")
            named_names = split_used_names(fmt.UsedNames(0, "\n"))
            db_names = split_used_names(fmt.UsedNames(1, "\n"))
            report = inspect_contract(db_names, named_names)

            print("SOURCE BTW SHA256:", original_hash)
            print("COPY SHA256:", copy_hash)
            print("Used DB fields:", ", ".join(sorted(db_names)) or "(none)")
            print("Named fields:", ", ".join(sorted(named_names)) or "(none)")
            print("DM database source:", "PRESENT" if report["db_dm_used"] else "MISSING")
            print("GS1 component sources:", ", ".join(report["gs1_named_components"]) or "(none)")
            print("Named raw DM:", report["raw_dm_named"])

            # Neither database handles nor values are read or changed.
            try:
                db = fmt.Databases
                print("Databases.Count:", int(db.Count or 0))
                for kind, name in connection_hints(db.Configuration):
                    print(f"Database config {kind}.name:", name)
            except Exception as exc:
                print("Database metadata unavailable:", type(exc).__name__)

            try:
                members = member_signatures(fmt._oleobj_.GetTypeInfo())
                for name in sorted(TARGET_MEMBERS):
                    signatures = members.get(name, [])
                    print(f"COM {name}:", signatures if signatures else "not in typeinfo")
                print("Sample-data setter signature documented: (kind=0, dbfield, value)")
                print("Typeinfo presence DOES NOT prove working setter or GS1 VBScript rendering.")
            except Exception as exc:
                print("COM typeinfo unavailable:", type(exc).__name__)

            print("No source values, DM payloads, script text or credentials logged.")
            print("NO PRINT / NO PRINTOUT / NO EXPORT / NO DATA MODIFICATION")
            return 0 if report["db_dm_used"] else 2
        finally:
            if fmt is not None:
                try:
                    fmt.Close(1)
                finally:
                    fmt = None
            if app is not None:
                app.Quit(1)
                app = None
            if digest(path) != original_hash or digest(isolated_btw) != copy_hash:
                raise RuntimeError("BTW copy/original changed; inspection invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--btw", required=True, type=Path,
                        help="Original updated 16x16/30x20 BTW; used only as input to temporary copy")
    args = parser.parse_args()
    source = args.btw.resolve(strict=True)
    if source.suffix.lower() != ".btw":
        parser.error("Expected .btw document")
    return inspect(source)


if __name__ == "__main__":
    raise SystemExit(main())
