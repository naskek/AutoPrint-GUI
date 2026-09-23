import os
import sys


LIVE_FLAG = "AUTOPRINT_LIVE_BARTENDER"


def main():
    if os.environ.get(LIVE_FLAG) != "1":
        print(
            f"Refusing live BarTender COM smoke: set {LIVE_FLAG}=1 explicitly.",
            file=sys.stderr,
        )
        return 2
    if os.name != "nt":
        print("Live BarTender COM smoke requires Windows.", file=sys.stderr)
        return 2

    import pythoncom
    from win32com.client import Dispatch

    app = None
    pythoncom.CoInitialize()
    try:
        app = Dispatch("BarTender.Application")
        app.Visible = False
        print(f"BarTender Version: {app.Version}")
        print(f"BarTender FullVersion: {app.FullVersion}")
        print(f"BarTender Edition: {app.Edition}")
        print("COM startup smoke: OK")
        return 0
    except Exception as exc:
        print(f"COM startup smoke: FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if app is not None:
            try:
                app.Quit(1)  # btDoNotSaveChanges
            except Exception as exc:
                print(f"BarTender Quit warning: {exc}", file=sys.stderr)
        app = None
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
