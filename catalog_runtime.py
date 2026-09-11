from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from catalog_compat import build_product_map
from catalog_excel import (
    PreparedExcelImport,
    apply_prepared_import,
    prepare_excel_import,
    select_automatic_migration_source,
)
from catalog_store import CatalogSnapshot, CatalogStore, get_catalog_db_path


@dataclass(frozen=True)
class CatalogRuntimeResult:
    state: str
    store: CatalogStore | None
    snapshot: CatalogSnapshot | None
    product_map: dict
    error: str = ""
    migration_source: Path | None = None
    duplicate_count: int = 0

    @property
    def ready(self) -> bool:
        return self.state == "ready"


def _ready_result(
    store: CatalogStore,
    *,
    migration_source: Path | None = None,
    duplicate_count: int = 0,
) -> CatalogRuntimeResult:
    snapshot = store.read_snapshot()
    return CatalogRuntimeResult(
        state="ready",
        store=store,
        snapshot=snapshot,
        product_map=build_product_map(
            snapshot.products, has_short_col=snapshot.has_short_col
        ),
        migration_source=migration_source,
        duplicate_count=duplicate_count,
    )


def _error_result(message: str) -> CatalogRuntimeResult:
    return CatalogRuntimeResult(
        state="error",
        store=None,
        snapshot=None,
        product_map={},
        error=str(message),
    )


def _duplicate_row_count(prepared: PreparedExcelImport) -> int:
    return sum(max(0, len(item.row_numbers) - 1) for item in prepared.duplicates)


def initialize_catalog_runtime(
    base_dir: str | os.PathLike[str],
    database_path: str | os.PathLike[str] | None = None,
) -> CatalogRuntimeResult:
    """Open the authoritative DB or atomically create it from the initial XLSX."""
    root = Path(base_dir)
    final_path = Path(database_path) if database_path is not None else get_catalog_db_path(root)

    if final_path.exists():
        try:
            store = CatalogStore(final_path)
            store.initialize()
            return _ready_result(store)
        except Exception as exc:
            return _error_result(f"Не удалось открыть каталог {final_path}: {exc}")

    source = select_automatic_migration_source(root, final_path)
    if source is None:
        staging_path = final_path.with_name(
            f".{final_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            staging_store = CatalogStore(staging_path)
            staging_store.initialize()
            staging_store.read_snapshot()
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_path, final_path)
            store = CatalogStore(final_path)
            store.initialize()
            return _ready_result(store)
        except Exception as exc:
            staging_path.unlink(missing_ok=True)
            return _error_result(f"Не удалось создать каталог {final_path}: {exc}")

    staging_path = final_path.with_name(
        f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        prepared = prepare_excel_import(source)
        prepared.require_valid()
        staging_store = CatalogStore(staging_path)
        staging_store.initialize()
        imported = apply_prepared_import(
            staging_store,
            prepared,
            expected_revision=0,
            create_backup=False,
        )
        staging_store.initialize()
        staging_store.read_snapshot()
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, final_path)
        store = CatalogStore(final_path)
        store.initialize()
        return _ready_result(
            store,
            migration_source=source,
            duplicate_count=max(imported.duplicate_count, _duplicate_row_count(prepared)),
        )
    except Exception as exc:
        staging_path.unlink(missing_ok=True)
        return _error_result(f"Не удалось мигрировать каталог из {source}: {exc}")


def install_prepared_catalog(
    prepared: PreparedExcelImport,
    database_path: str | os.PathLike[str],
) -> CatalogRuntimeResult:
    """Create a missing authoritative DB from a validated manual import."""
    final_path = Path(database_path)
    if final_path.exists():
        return _error_result(f"Каталог уже существует: {final_path}")
    staging_path = final_path.with_name(
        f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        prepared.require_valid()
        staging_store = CatalogStore(staging_path)
        staging_store.initialize()
        imported = apply_prepared_import(
            staging_store,
            prepared,
            expected_revision=0,
            create_backup=False,
        )
        staging_store.initialize()
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, final_path)
        store = CatalogStore(final_path)
        store.initialize()
        return _ready_result(
            store,
            migration_source=prepared.source_path,
            duplicate_count=max(imported.duplicate_count, _duplicate_row_count(prepared)),
        )
    except Exception as exc:
        staging_path.unlink(missing_ok=True)
        return _error_result(f"Не удалось создать каталог из {prepared.source_path}: {exc}")
