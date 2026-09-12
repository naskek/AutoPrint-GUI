from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = [int(x) for x in version.split(".")]
    if len(parts) != 3:
        raise ValueError(f"APP_VERSION must be MAJOR.MINOR.PATCH, got {version!r}")
    return parts[0], parts[1], parts[2], 0


def _write_windows_version_file(root: Path, version: str) -> Path:
    numeric = _version_tuple(version)
    out = root / "build" / "version_info.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={numeric!r},\n"
        f"    prodvers={numeric!r},\n"
        "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('040904B0', [\n"
        "      StringStruct('CompanyName', 'AutoPrint'),\n"
        "      StringStruct('FileDescription', 'AutoPrint'),\n"
        f"      StringStruct('FileVersion', '{version}'),\n"
        "      StringStruct('InternalName', 'AutoPrint'),\n"
        "      StringStruct('OriginalFilename', 'AutoPrint.exe'),\n"
        "      StringStruct('ProductName', 'AutoPrint'),\n"
        f"      StringStruct('ProductVersion', '{version}'),\n"
        "    ])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )
    out.write_text(content, encoding="utf-8")
    return out


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from app_version import APP_VERSION

    target = root / "autoprint_main.py"
    version_file = _write_windows_version_file(root, APP_VERSION)

    args = [
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "AutoPrint",
        "--version-file",
        str(version_file),
        "--collect-all",
        "customtkinter",
        "--hidden-import",
        "darkdetect",
        "--hidden-import",
        "tkcalendar",
    ]

    args.append(str(target))

    print(f"[INFO] Building AutoPrint {APP_VERSION}")
    print("[INFO] Running PyInstaller:")
    print("python", " ".join(args))
    return subprocess.call([sys.executable, *args], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
