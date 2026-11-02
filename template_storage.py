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



TEMPLATE_REGISTRY_FILENAME = "template-registry.json"
TEMPLATE_REGISTRY_VERSION = 1


def get_template_registry_path(user_data_dir=None) -> Path:
    return Path(user_data_dir or get_user_data_dir()) / TEMPLATE_REGISTRY_FILENAME


def _safe_btw_name(file_name: str) -> str:
    from pathlib import PureWindowsPath

    value = str(file_name or "").strip()
    if (not value or Path(value).name != value
            or PureWindowsPath(value).name != value
            or Path(value).suffix.lower() != ".btw"):
        raise ValueError(f"Not a safe BTW filename: {file_name!r}")
    return value


def read_template_registry(path=None) -> dict | None:
    """Malformed persistent metadata is never silently replaced."""
    import json
    target = Path(path) if path is not None else get_template_registry_path()
    if not target.exists():
        return None
    with target.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if (not isinstance(value, dict)
            or value.get("version") != TEMPLATE_REGISTRY_VERSION
            or not isinstance(value.get("label_templates"), dict)
            or not isinstance(value.get("sticker_file"), str)):
        raise ValueError(f"Invalid template registry: {target}")
    if value["sticker_file"]:
        _safe_btw_name(value["sticker_file"])
    return value


def write_template_registry(label_templates: dict, sticker_file="", *, path=None) -> Path:
    """Atomic user-owned metadata, independent from install/config.json."""
    import json
    import tempfile
    if not isinstance(label_templates, dict):
        raise TypeError("label_templates must be a dictionary")
    sticker = _safe_btw_name(sticker_file) if sticker_file else ""
    target = Path(path) if path is not None else get_template_registry_path()
    if target.exists():
        read_template_registry(target)  # Don't destroy corrupt/unknown data.
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": TEMPLATE_REGISTRY_VERSION,
               "label_templates": label_templates, "sticker_file": sticker}
    stage = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".template-registry-",
            suffix=".tmp", dir=target.parent, delete=False
        ) as stream:
            stage = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, target)
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)
    return target


def bootstrap_template_registry(cfg: dict, template_dir, registry_path) -> dict:
    """Preserve legacy per-template settings and restore them after reinstall.

    Only the template-specific keys move to persistent storage: the rest of
    config.json stays under the existing #7 persistence issue.
    """
    existing = read_template_registry(registry_path)
    restored = dict(cfg)
    if existing is not None:
        restored["label_templates"] = existing["label_templates"]
        if existing["sticker_file"]:
            restored["sticker_btw"] = str(
                Path(template_dir) / existing["sticker_file"]
            )
        return restored

    if restored.get("label_templates") or restored.get("sticker_btw"):
        from pathlib import PureWindowsPath
        path = str(restored.get("sticker_btw") or "")
        name = PureWindowsPath(path).name
        write_template_registry(
            restored.get("label_templates") or {},
            name if name.lower().endswith(".btw") else "",
            path=registry_path,
        )
    return restored


def _copy_new_file(source: Path, destination: Path) -> bool:
    """Stage complete data before publishing under a no-clobber target."""
    import tempfile
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".autoprint-template-", suffix=".tmp",
            dir=destination.parent, delete=False
        ) as stream:
            stage = Path(stream.name)
        shutil.copy2(source, stage)
        try:
            os.link(stage, destination)  # Atomic no-overwrite on local NTFS.
        except FileExistsError:
            return False
        except OSError:
            created = False
            try:
                with destination.open("xb") as output:
                    created = True
                    with stage.open("rb") as input_file:
                        shutil.copyfileobj(input_file, output)
                    output.flush()
                    os.fsync(output.fileno())
                shutil.copystat(stage, destination)
            except FileExistsError:
                return False
            except BaseException:
                if created:
                    destination.unlink(missing_ok=True)
                raise
        return True
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)


def import_user_template(source_path, destination_dir, file_name: str) -> Path:
    """Never overwrite; conflict becomes a content-addressed .import variant."""
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    name = _safe_btw_name(file_name)
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / name
    variant = destination / f"{Path(name).stem}.import-{_sha256(source)[:16]}.btw"
    for candidate in (target, variant):
        try:
            if source.samefile(candidate):
                return candidate
        except (FileNotFoundError, OSError):
            pass
        if candidate.exists():
            if _same_file_content(source, candidate):
                return candidate
            continue
        if _copy_new_file(source, candidate):
            return candidate
        if candidate.exists() and _same_file_content(source, candidate):
            return candidate
    raise FileExistsError(f"Conflict variant already used: {name}")


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
                    if _copy_new_file(source, target):
                        event["status"] = "copied"
                        events.append(event)
                        continue
                    # Another process may have created the target meanwhile.

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

                if not _copy_new_file(source, conflict):
                    if not _same_file_content(source, conflict):
                        raise FileExistsError(f"Concurrent conflict variant: {conflict}")
                event["status"] = "conflict-preserved"
                events.append(event)
            except Exception as exc:
                event["status"] = "error"
                event["error"] = str(exc)
                events.append(event)

    return events
