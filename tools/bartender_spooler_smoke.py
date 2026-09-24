import argparse
import os
import sys
import time


LIVE_FLAG = "AUTOPRINT_LIVE_BARTENDER_SPOOLER"
BT_DO_NOT_SAVE_CHANGES = 1
TEST_NAME_MARKER = "TEST"
TEST_PORT_MARKER = "AUTOPRINT_TEST"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Opt-in BarTender -> Windows spooler smoke for a deliberately paused "
            "dummy TEST queue. This submits one BarTender print job but must never "
            "target a physical printer."
        )
    )
    parser.add_argument("--btw", required=True, help="Disposable/test BTW copy.")
    parser.add_argument("--printer", required=True, help="Paused dummy TEST queue name.")
    parser.add_argument(
        "--record-range",
        default="1-100",
        help="Database RecordRange to submit, default: 1-100.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=3.0,
        help="How long to wait for the paused queue to expose the job.",
    )
    return parser.parse_args(argv)


def _require_live_gate():
    if os.environ.get(LIVE_FLAG) != "1":
        raise RuntimeError(
            f"Refusing spooler smoke: set {LIVE_FLAG}=1 explicitly."
        )
    if os.name != "nt":
        raise RuntimeError("BarTender spooler smoke requires Windows.")


def _printer_snapshot(printer_name):
    import win32print

    handle = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(handle, 2)
        jobs = win32print.EnumJobs(handle, 0, 999, 1)
    finally:
        win32print.ClosePrinter(handle)

    return {
        "name": str(info.get("pPrinterName") or printer_name),
        "port": str(info.get("pPortName") or ""),
        "driver": str(info.get("pDriverName") or ""),
        "status": int(info.get("Status") or 0),
        "jobs": list(jobs or []),
    }


def _assert_safe_test_queue(snapshot):
    import win32print

    name = snapshot["name"]
    port = snapshot["port"]
    status = snapshot["status"]

    if TEST_NAME_MARKER not in name.upper():
        raise RuntimeError(
            f"Unsafe queue name {name!r}: it must contain {TEST_NAME_MARKER!r}."
        )
    if TEST_PORT_MARKER not in port.upper():
        raise RuntimeError(
            f"Unsafe queue port {port!r}: it must contain {TEST_PORT_MARKER!r}."
        )
    if not (status & win32print.PRINTER_STATUS_PAUSED):
        raise RuntimeError(
            f"Unsafe queue state: {name!r} is not paused (Status={status})."
        )
    if snapshot["jobs"]:
        raise RuntimeError(
            f"Test queue is not empty: {len(snapshot['jobs'])} job(s) already present."
        )


def _delete_test_jobs(printer_name, jobs):
    import win32print

    handle = win32print.OpenPrinter(printer_name)
    try:
        for job in jobs:
            job_id = int(job.get("JobId") or 0)
            if job_id:
                win32print.SetJob(
                    handle,
                    job_id,
                    0,
                    None,
                    win32print.JOB_CONTROL_DELETE,
                )
    finally:
        win32print.ClosePrinter(handle)


def _submit_one_batch(btw_path, printer_name, record_range):
    import pythoncom
    from win32com.client import Dispatch, VARIANT

    app = None
    fmt = None
    pythoncom.CoInitialize()
    try:
        app = Dispatch("BarTender.Application")
        app.Visible = False
        print(f"BarTender FullVersion: {app.FullVersion}")
        print(f"BarTender Edition: {app.Edition}")

        path = os.path.abspath(btw_path)
        if not os.path.isfile(path):
            raise RuntimeError(f"BTW does not exist: {path}")

        fmt = app.Formats.Open(path, False, "")
        fmt.PrintSetup.Printer = printer_name
        fmt.PrintSetup.IdenticalCopiesOfLabel = 1
        try:
            fmt.AutoPrintAgain = False
        except Exception:
            pass
        try:
            fmt.PrintSetup.AutoPrintAgain = False
        except Exception:
            pass

        # Preserve the production batch shape used by AutoPrint 3.2.4.
        fmt.UseDatabase = True
        fmt.SelectRecordsAtPrint = False
        fmt.RecordRange = record_range

        messages = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_DISPATCH, None)
        job_name = f"AutoPrint TEST spooler smoke {record_range}"
        print(
            "Submitting exactly one BarTender batch to the paused dummy queue: "
            f"job={job_name!r}, RecordRange={record_range!r}"
        )

        result = fmt.Print(job_name, False, 30000, messages)

        try:
            result_code = int(result)
        except Exception:
            result_code = None
        print(f"BarTender Print result: {result!r}")

        if result_code not in (0, 1, 2):
            raise RuntimeError(f"Unexpected BtPrintResult: {result!r}")
        if result_code == 2:
            raise RuntimeError("BarTender returned btFailure (2).")

        return result_code
    finally:
        if fmt is not None:
            try:
                fmt.Close(BT_DO_NOT_SAVE_CHANGES)
            except Exception as exc:
                print(f"BTW close warning: {exc}", file=sys.stderr)
        if app is not None:
            try:
                app.Quit(BT_DO_NOT_SAVE_CHANGES)
            except Exception as exc:
                print(f"BarTender Quit warning: {exc}", file=sys.stderr)
        pythoncom.CoUninitialize()


def main(argv=None):
    args = _parse_args(argv)
    cleanup_allowed = False

    try:
        _require_live_gate()
        before = _printer_snapshot(args.printer)
        _assert_safe_test_queue(before)
        cleanup_allowed = True

        print(
            "Safe queue verified: "
            f"name={before['name']!r}, port={before['port']!r}, "
            f"driver={before['driver']!r}, status={before['status']}"
        )
        print("Queue is paused and empty. Physical output is blocked.")

        result_code = _submit_one_batch(
            args.btw,
            args.printer,
            args.record_range,
        )

        time.sleep(max(0.0, args.settle_seconds))
        after = _printer_snapshot(args.printer)
        jobs = after["jobs"]

        print(f"Paused queue job count after submit: {len(jobs)}")
        for job in jobs:
            print(
                "  "
                f"JobId={job.get('JobId')} "
                f"Document={job.get('pDocument')!r} "
                f"Status={job.get('Status')!r} "
                f"TotalPages={job.get('TotalPages')!r}"
            )

        if len(jobs) != 1:
            raise RuntimeError(
                "Spooler invariant failed: expected exactly one Windows print job "
                f"for one BarTender batch, observed {len(jobs)}."
            )

        print("SPOOLER CONTRACT: PASS — one batch -> one Windows print job")
        print("PHYSICAL PRINT: BLOCKED — queue remained paused on dummy TEST port")
        print(f"BtPrintResult code: {result_code}")
        return 0
    except Exception as exc:
        print(f"BarTender spooler smoke: FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if cleanup_allowed:
            try:
                snapshot = _printer_snapshot(args.printer)
                if snapshot["jobs"]:
                    _delete_test_jobs(args.printer, snapshot["jobs"])
                    print(f"Deleted {len(snapshot['jobs'])} test spool job(s).")
            except Exception as exc:
                print(f"Test spool cleanup warning: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
