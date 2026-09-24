from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable


USER_DATA_ENV = "AUTOPRINT_USER_DATA_DIR"
USER_DATA_DIRNAME = "AutoPrintData"
BUNDLED_TEMPLATES_DIRNAME = "bundled_templates"


def _runtime_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_user_data_dir(local_appdata: str | os.PathLike[str] | None = None) -> str:
    override = str(os.environ.get(USER_DATA_ENV) or "").strip()
    if override:
        return str(Path(override).expanduser())

    if local_appdata is not None:
        return str(Path(local_appdata) / USER_DATA_DIRNAME)
    if os.environ.get("LOCALAPPDATA"):
        return str(Path(os.environ["LOCALAPPDATA"]) / USER_DATA_DIRNAME)
    return str(Path.home() / ".autoprint-data")


def get_user_templates_dir(
    local_appdata: str | os.PathLike[str] | None = None,
) -> str:
    return str(Path(get_user_data_dir(local_appdata)) / "templates")


def get_bundled_templates_dir(
    base_dir: str | os.PathLike[str] | None = None,
    *,
    frozen: bool | None = None,
    bundle_root: str | os.PathLike[str] | None = None,
) -> str:
    is_frozen = getattr(sys, "frozen", False) if frozen is None else bool(frozen)
    if is_frozen:
        root = Path(
            bundle_root
            or getattr(sys, "_MEIPASS", "")
            or base_dir
            or _runtime_base_dir()
        )
        return str(root / BUNDLED_TEMPLATES_DIRNAME)
    return str(Path(base_dir or _runtime_base_dir()) / "templates")


def legacy_template_dirs(
    *,
    base_dir: str | os.PathLike[str] | None = None,
    local_appdata: str | os.PathLike[str] | None = None,
) -> list[Path]:
    base = Path(base_dir or _runtime_base_dir())
    candidates = [base / "templates", base]

    if local_appdata is not None:
        local_root = Path(local_appdata)
    elif os.environ.get("LOCALAPPDATA"):
        local_root = Path(os.environ["LOCALAPPDATA"])
    else:
        local_root = None

    if local_root is not None:
        for app_name in ("AutoPrint", "BarTenderGUI"):
            app_root = local_root / app_name
            candidates.extend((app_root / "templates", app_root))

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(str(candidate)))
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file_content(left: Path, right: Path) -> bool:
    try:
        return (
            left.stat().st_size == right.stat().st_size
            and _sha256(left) == _sha256(right)
        )
    except OSError:
        return False


def migrate_legacy_templates(
    *,
    destination_dir: str | os.PathLike[str],
    base_dir: str | os.PathLike[str] | None = None,
    local_appdata: str | os.PathLike[str] | None = None,
    legacy_dirs: Iterable[str | os.PathLike[str]] | None = None,
) -> list[dict[str, str]]:
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)

    sources = (
        [Path(item) for item in legacy_dirs]
        if legacy_dirs is not None
        else legacy_template_dirs(base_dir=base_dir, local_appdata=local_appdata)
    )

    destination_key = os.path.normcase(os.path.abspath(str(destination)))
    events: list[dict[str, str]] = []
    seen_sources: set[str] = set()

    for source_dir in sources:
        source_dir_key = os.path.normcase(os.path.abspath(str(source_dir)))
        if source_dir_key == destination_key or source_dir_key in seen_sources:
            continue
        seen_sources.add(source_dir_key)
        if not source_dir.is_dir():
            continue

        for source in sorted(source_dir.glob("*.btw")):
            target = destination / source.name
            event = {
                "source": str(source),
                "target": str(target),
                "status": "",
            }
            try:
                if not target.exists():
                    shutil.copy2(source, target)
                    event["status"] = "copied"
                    events.append(event)
                    continue

                if _same_file_content(source, target):
                    event["status"] = "already-present"
                    events.append(event)
                    continue

                digest = _sha256(source)[:16]
                conflict = destination / f"{source.stem}.legacy-{digest}{source.suffix}"
                event["target"] = str(conflict)
                if conflict.exists():
                    if _same_file_content(source, conflict):
                        event["status"] = "conflict-preserved"
                        events.append(event)
                        continue
                    raise FileExistsError(
                        f"Conflict backup already exists with different content: {conflict}"
                    )

                shutil.copy2(source, conflict)
                event["status"] = "conflict-preserved"
                events.append(event)
            except Exception as exc:
                event["status"] = "error"
                event["error"] = str(exc)
                events.append(event)

    return events
