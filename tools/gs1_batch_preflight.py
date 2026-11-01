"""Read-only live BarTender GS1 batch preflight for both production BTW designs.

No GUI imports. Never invokes Print, PrintOut, image export or Preview.
Does not change the input CSV or any BTW file.
Run from the repository root: python -m tools.gs1_batch_preflight
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from gs1_batch_contract import verify_dm_batch_database


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Logger:
    def log(self, message):
        print("INFO:", message)

    def err(self, message):
        print("ERROR:", message)


def preflight(root: Path) -> int:
    from win32com.client import DispatchEx

    csv_path = root / "tmp_batch.csv"
    templates = (root / "templates" / "16x16.btw",
                 root / "templates" / "30x20.btw")
    for path in (*templates, csv_path):
        if not path.is_file():
            print("MISSING:", path)
            return 2

    checks = []
    app = None
    try:
        app = DispatchEx("BarTender.Application")
        app.Visible = False
        for path in templates:
            before = digest(path)
            fmt = None
            try:
                fmt = app.Formats.Open(str(path), False, "")
                passed = verify_dm_batch_database(
                    fmt, str(csv_path), _Logger()
                )
                print(f"{path.name}: {'PASS' if passed else 'FAIL'}")
                checks.append(passed)
            finally:
                try:
                    if fmt is not None:
                        fmt.Close(1)  # btDoNotSaveChanges
                finally:
                    if digest(path) != before:
                        raise RuntimeError(f"BTW CHANGED: {path.name}")
    finally:
        if app is not None:
            app.Quit(1)  # btDoNotSaveChanges

    print("NO PRINT / NO PRINTOUT / BTW UNCHANGED")
    return 0 if checks and all(checks) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only GS1 COM preflight")
    parser.add_argument(
        "--data-root", type=Path,
        default=Path(os.getenv("LOCALAPPDATA", ".")) / "BarTenderGUI",
    )
    args = parser.parse_args()
    return preflight(args.data_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
