"""Issue #6: safe SQLite migration out of installer-owned directories."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from catalog_store import CatalogStore


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def legacy_database_candidates(root: Path, final_path: Path, paths=None) -> list[Path]:
    """Find potential old SQLite stores without modifying installation paths."""
    if paths is not None:
        candidates = [Path(path) for path in paths]
    else:
        candidates = [root / "data" / "catalog.sqlite3", root / "catalog.sqlite3"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            for app in ("AutoPrint", "BarTenderGUI"):
                app_dir = Path(local) / app
                candidates.extend((
                    app_dir / "data" / "catalog.sqlite3",
                    app_dir / "catalog.sqlite3",
                ))
    unique = []
    seen = {os.path.normcase(os.path.abspath(final_path))}
    for source in candidates:
        key = os.path.normcase(os.path.abspath(source))
        if key not in seen:
            seen.add(key)
            unique.append(source)
    return [source for source in unique if source.is_file()]


def migrate_legacy_catalog(root: Path, target: Path, legacy_paths=None) -> Path | None:
    """Copy a consistent SQLite snapshot with recoverable backup; never overwrite.

    If several legacy stores differ or a source is corrupt, fail closed. Original
    data stays in place. No migration mutates the production print/COM path.
    """
    sources = legacy_database_candidates(root, target, legacy_paths)
    if not sources:
        return None
    for source in sources:
        CatalogStore(source).initialize()  # version, quick_check, FK, schema
    if len({_sha256_file(source) for source in sources}) > 1:
        raise RuntimeError(
            "Conflicting legacy SQLite catalogs: "
            + "; ".join(map(str, sources))
            + "; select the authoritative source manually"
        )
    source = sources[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(f".{target.name}.migration-{uuid.uuid4().hex}.tmp")
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(stage) as dst:
                src.backup(dst)  # includes committed WAL transactions
        CatalogStore(stage).initialize()
        digest = _sha256_file(stage)
        backups = target.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        backup = backups / f"catalog.migration-{digest[:16]}.sqlite3"
        if backup.exists():
            if _sha256_file(backup) != digest:
                raise RuntimeError(f"Existing backup differs: {backup}")
        else:
            created = False
            try:
                with backup.open("xb") as output, stage.open("rb") as input_file:
                    created = True
                    shutil.copyfileobj(input_file, output)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                if created:
                    backup.unlink(missing_ok=True)
                raise
        CatalogStore(backup).initialize()
        os.link(stage, target)  # NTFS atomic no-clobber; fail rather than replace
        return source
    finally:
        stage.unlink(missing_ok=True)
