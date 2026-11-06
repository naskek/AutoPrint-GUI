"""#87: exact-head Windows BarTender NO-PRINT primary-before-sticker preflight.

Uses the deployed BarTender's real Format/Databases/UsedNames inspection on
a disposable copy of the operator's BTW and two synthetic GS1 CSV rows.
Never invokes Print, PrintOut, XMLScript, a printer queue or GUI Print action.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from tools.smoke_83_exact_head import _exact_checkout, _synthetic_rows


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Logger:
    def log(self, message):
        print("INFO:", message)

    def err(self, message):
        print("ERROR:", message)


def smoke(btw_path: Path, expected_sha: str) -> bool:
    _exact_checkout(expected_sha)
    if os.name != "nt":
        raise RuntimeError("This BarTender test requires Windows")

    source = btw_path.resolve(strict=True)
    if source.suffix.casefold() != ".btw":
        raise ValueError("Expected a .btw document")
    source_hash = _sha(source)
    print("HEAD SHA:", expected_sha)
    print("NO PRINT / PRINTOUT / XMLSCRIPT / PRINT-QUEUE ACCESS")
    print("Operator's original BTW is not opened")

    import pythoncom
    from win32com.client import DispatchEx

    pythoncom.CoInitialize()
    application = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="AutoPrint87Exact-", ignore_cleanup_errors=True
        ) as folder:
            root = Path(folder)
            os.environ["AUTOPRINT_USER_DATA_DIR"] = str(root / "user-profile")
            from bartender_gui import App, DEFAULT_REQ_COLS
            from bartender_com import BT

            copied = root / "isolated_30x20.btw"
            shutil.copy2(source, copied)
            copied_hash = _sha(copied)
            rows = _synthetic_rows()
            logger = Logger()

            try:
                application = DispatchEx("BarTender.Application")
                application.Visible = False
                bt = BT(logger)
                bt.app = application
                obj = object.__new__(App)
                obj.logger = logger
                obj.bt = bt
                obj.REQ_COLS = list(DEFAULT_REQ_COLS)
                obj.calib_var = False
                obj._bool_var = lambda value: bool(value)
                obj._pause_wait = lambda: None
                obj._should_stop = lambda: False
                obj._can_fast_enrich = lambda records: bool(records)
                obj._enrich = lambda row, number: dict(
                    row, NUM=str(number), _FORMAT="30x20"
                )
                obj._set_progress = lambda *args, **kwargs: None
                obj.update_idletasks = lambda: None
                obj.update = lambda: None
                obj._apply_format_ui = lambda name: None
                obj._get_printer = lambda: "Microsoft Print to PDF"
                obj._ensure_printer_available_for_print = (
                    lambda name, label: True
                )
                obj._get_btw_for_format = lambda name: str(copied)
                obj._template_part_mode = lambda name: "manual"
                obj._dialog_flag = lambda: False

                plans = obj._preflight_primary_batches_before_stickers(
                    [(0, 1), (1, 2)], rows, 0, 1
                )
                if not plans or len(plans) != 2:
                    raise RuntimeError("PRIMARY PREFLIGHT FAILED: no stickers sent")
                for index, (start, end) in enumerate(
                    ((0, 1), (1, 2)), start=1
                ):
                    prepared_rows, calib = plans[(start, end)]
                    if calib != 0 or len(prepared_rows) != 1:
                        raise RuntimeError(
                            f"Batch {index} has an unexpected row count"
                        )
                    if prepared_rows[0]["DM"] != rows[index - 1]["DM"]:
                        raise RuntimeError(
                            f"Batch {index} synthetic DM differs from input"
                        )
                print("ALL PRIMARY BATCHES PREFLIGHT: PASS")
                print("ALL STICKER SUBMISSION FUNCTIONS: NOT CALLED")
                print("ALL PRIMARY SUBMISSION FUNCTIONS: NOT CALLED")
                return True
            finally:
                if application is not None:
                    try:
                        application.Quit(1)
                    finally:
                        application = None
                if _sha(copied) != copied_hash:
                    raise RuntimeError("Disposable BTW was modified")
    finally:
        pythoncom.CoUninitialize()
        if _sha(source) != source_hash:
            raise RuntimeError("Original user BTW was modified")
        print("Original BTW SHA256 unchanged: True")
        print("NO PRINT / PRINTOUT / XMLSCRIPT SUBMITTED")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AutoPrint #87 exact-head NO-PRINT BarTender preflight"
    )
    parser.add_argument("--btw", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    return 0 if smoke(args.btw, args.expected_sha) else 1


if __name__ == "__main__":
    raise SystemExit(main())
