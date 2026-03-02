from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    target = root / "bartender_gui.py"
    templates_dir = root / "templates"
    config_dir = root / "config"

    static_files = [
        (Path("product_catalog.xlsx"), "."),
        (Path("preview.png"), "."),
        (Path("tmp_batch.csv"), "."),
        (Path("config") / "stats.json", "config"),
        (Path("config") / "config.json", "config"),
    ]

    template_files = [(p, "templates") for p in templates_dir.glob("*.btw")] if templates_dir.exists() else []
    data_files = static_files + template_files

    args = [
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "BarTenderGUI",
        "--collect-all",
        "customtkinter",
        "--collect-all",
        "psycopg",
        "--hidden-import",
        "darkdetect",
        "--hidden-import",
        "tkcalendar",
        "--hidden-import",
        "psycopg_binary",
    ]

    for rel_path, dest in data_files:
        src = root / rel_path
        if not src.exists():
            print(f"[WARN] Skip missing data file: {src}", file=sys.stderr)
            continue
        args += ["--add-data", f"{src}{os.pathsep}{dest}"]

    args.append(str(target))

    print("[INFO] Running PyInstaller:")
    print("python", " ".join(args))
    return subprocess.call([sys.executable, *args], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
