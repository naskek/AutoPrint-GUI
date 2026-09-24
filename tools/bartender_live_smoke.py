import argparse
import os
import sys


LIVE_FLAG = "AUTOPRINT_LIVE_BARTENDER"
BT_DO_NOT_SAVE_CHANGES = 1


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Opt-in live BarTender COM smoke. By default it only starts and stops "
            "BarTender. With --btw it opens and inspects a real document without printing."
        )
    )
    parser.add_argument(
        "--btw",
        help="Optional disposable/test BTW to open and inspect. No print call is made.",
    )
    return parser.parse_args(argv)


def _require_member(obj, name: str, owner: str):
    try:
        value = getattr(obj, name)
    except Exception as exc:
        raise RuntimeError(f"{owner}.{name} is unavailable: {exc}") from exc
    if value is None:
        raise RuntimeError(f"{owner}.{name} returned None")
    print(f"COM member OK: {owner}.{name}")
    return value


def _inspect_format(app, btw_path: str):
    path = os.path.abspath(btw_path)
    if not os.path.isfile(path):
        raise RuntimeError(f"BTW does not exist: {path}")

    fmt = None
    try:
        fmt = app.Formats.Open(path, False, "")
        print(f"BTW opened: {path}")

        print_setup = _require_member(fmt, "PrintSetup", "Format")
        _require_member(fmt, "Print", "Format")
        _require_member(fmt, "PrintOut", "Format")
        _require_member(fmt, "SetNamedSubStringValue", "Format")
        _require_member(fmt, "Close", "Format")
        _require_member(print_setup, "IdenticalCopiesOfLabel", "PrintSetup")
        _require_member(print_setup, "Printer", "PrintSetup")

        try:
            copies = int(print_setup.IdenticalCopiesOfLabel)
            print_setup.IdenticalCopiesOfLabel = copies
            print(
                "PrintSetup.IdenticalCopiesOfLabel round-trip: "
                f"OK (value={copies})"
            )
        except Exception as exc:
            raise RuntimeError(
                "PrintSetup.IdenticalCopiesOfLabel read/write probe failed: "
                f"{exc}"
            ) from exc

        try:
            names = [str(item.Name) for item in fmt.NamedSubStrings]
        except Exception as exc:
            raise RuntimeError(f"NamedSubStrings enumeration failed: {exc}") from exc
        print(f"Named data sources: {len(names)}")
        if names:
            print("  " + ", ".join(names))

        try:
            databases = fmt.Databases
            count = int(databases.Count)
            print(f"Databases: {count}")
        except Exception as exc:
            print(f"Databases probe: unavailable/failed: {exc}")

        print("BTW inspection smoke: OK")
        print("NO PRINT SUBMITTED")
    finally:
        if fmt is not None:
            try:
                fmt.Close(BT_DO_NOT_SAVE_CHANGES)
                print("BTW close (btDoNotSaveChanges): OK")
            except Exception as exc:
                print(f"BTW close warning: {exc}", file=sys.stderr)


def main(argv=None):
    args = _parse_args(argv)

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

        if args.btw:
            _inspect_format(app, args.btw)
        else:
            print("No BTW supplied; startup/shutdown smoke only.")

        return 0
    except Exception as exc:
        print(f"Live BarTender COM smoke: FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if app is not None:
            try:
                app.Quit(BT_DO_NOT_SAVE_CHANGES)
            except Exception as exc:
                print(f"BarTender Quit warning: {exc}", file=sys.stderr)
        app = None
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
