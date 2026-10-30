"""No-print COM inspection of a BarTender template's database/DM bindings.

Never calls Format.Print, PrintOut, or any preview/export method.
Does not change source paths, template properties, or the input BTW.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_btw(path: Path) -> int:
    from win32com.client import Dispatch

    original = sha256(path)
    app = None
    fmt = None
    try:
        app = Dispatch("BarTender.Application")
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
        for index in range(1, count + 1):
            try:
                db = databases.GetDatabase(index)
                print(f"DB[{index}].Name={db.Name!s} Type={db.Type!s}")
                for label, accessor in (
                    ("Database.FileName", lambda: db.FileName),
                    ("TextFile.FileName", lambda: db.TextFile.FileName),
                ):
                    try:
                        print(f"DB[{index}].{label}={accessor()!s}")
                    except Exception as exc:
                        print(f"DB[{index}].{label}: ERROR {exc}")
            except Exception as exc:
                print(f"DB[{index}].GetDatabase: ERROR {exc}")
                return 2
        return 0
    finally:
        if fmt is not None:
            fmt.Close(1)  # btDoNotSaveChanges
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
