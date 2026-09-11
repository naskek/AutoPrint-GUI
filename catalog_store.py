from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from utils import get_base_dir


SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 2000
CATALOG_FILENAME = "catalog.sqlite3"

MASTER_FIELDS = (
    "gtin",
    "name",
    "brand",
    "weight",
    "shelf_life",
    "permit_document",
    "permit_from",
    "permit_to",
    "packaging",
    "material",
    "marking_method",
    "tnved",
    "short_name",
)
PRINT_SETTING_FIELDS = (
    "print_template",
    "client",
    "display_packaging",
)
CATALOG_FIELDS = MASTER_FIELDS + PRINT_SETTING_FIELDS
_TEXT_FIELDS = tuple(field for field in CATALOG_FIELDS if field != "gtin")


class CatalogError(RuntimeError):
    pass


class CatalogSchemaError(CatalogError):
    pass


class CatalogValidationError(CatalogError):
    pass


class CatalogConflictError(CatalogError):
    pass


@dataclass(frozen=True)
class CatalogProduct:
    id: str
    gtin: str
    name: str = ""
    brand: str = ""
    weight: str = ""
    shelf_life: str = ""
    permit_document: str = ""
    permit_from: str = ""
    permit_to: str = ""
    packaging: str = ""
    material: str = ""
    marking_method: str = ""
    tnved: str = ""
    short_name: str = ""
    print_template: str = ""
    # Deprecated schema-compatibility value. Application code must ignore it.
    batch_template: str = ""
    client: str = ""
    display_packaging: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class CatalogSnapshot:
    products: tuple[CatalogProduct, ...]
    revision: int
    has_short_col: bool


@dataclass(frozen=True)
class ImportResult:
    added: int
    updated: int
    unchanged: int
    duplicate_count: int
    revision: int


def get_catalog_db_path(base_dir: str | os.PathLike[str] | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else Path(get_base_dir())
    return root / "data" / CATALOG_FILENAME


def normalize_gtin(value) -> str:
    if value is None:
        raise CatalogValidationError("GTIN is required")
    if isinstance(value, bool):
        raise CatalogValidationError("GTIN must contain digits")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not value.is_integer():
            raise CatalogValidationError("GTIN must not be fractional")
        if abs(value) > 2**53:
            raise CatalogValidationError("GTIN numeric value is not precise")
        text = str(int(value))
    else:
        text = str(value).strip()
    if not text:
        raise CatalogValidationError("GTIN is required")
    if any(character in text.lower() for character in ("e+", "e-")):
        raise CatalogValidationError("GTIN in scientific notation is not supported")
    allowed_separators = {" ", "\t", "\r", "\n", "-", "(", ")"}
    if any(
        not ("0" <= character <= "9" or character in allowed_separators)
        for character in text
    ):
        raise CatalogValidationError("GTIN contains unsupported characters")
    normalized = "".join(character for character in text if "0" <= character <= "9")
    if not normalized:
        raise CatalogValidationError("GTIN must contain digits")
    return normalized


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


_PRODUCT_SELECT = """
SELECT
    p.id, p.gtin, p.name, p.brand, p.weight, p.shelf_life,
    p.permit_document, p.permit_from, p.permit_to, p.packaging,
    p.material, p.marking_method, p.tnved, p.short_name,
    s.print_template, s.batch_template, s.client, s.display_packaging,
    p.created_at, p.updated_at
FROM products AS p
JOIN product_print_settings AS s ON s.product_id = p.id
"""


class CatalogStore:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path is not None else get_catalog_db_path()

    def initialize(self) -> None:
        existed = self.path.exists()
        if existed and self.path.stat().st_size == 0:
            raise CatalogSchemaError(f"Catalog database is empty: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not existed:
            self._create_schema()
        self._validate_existing_database()

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(
                uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
            )
        else:
            connection = sqlite3.connect(
                self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
            )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        if not readonly:
            connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _write_transaction(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _create_schema(self) -> None:
        with self._write_transaction() as connection:
            connection.execute(
                """
                CREATE TABLE products (
                    id              TEXT PRIMARY KEY NOT NULL,
                    gtin            TEXT NOT NULL UNIQUE
                                    CHECK (gtin <> '' AND gtin NOT GLOB '*[^0-9]*'),
                    name            TEXT NOT NULL DEFAULT '',
                    brand           TEXT NOT NULL DEFAULT '',
                    weight          TEXT NOT NULL DEFAULT '',
                    shelf_life      TEXT NOT NULL DEFAULT '',
                    permit_document TEXT NOT NULL DEFAULT '',
                    permit_from     TEXT NOT NULL DEFAULT '',
                    permit_to       TEXT NOT NULL DEFAULT '',
                    packaging       TEXT NOT NULL DEFAULT '',
                    material        TEXT NOT NULL DEFAULT '',
                    marking_method  TEXT NOT NULL DEFAULT '',
                    tnved           TEXT NOT NULL DEFAULT '',
                    short_name      TEXT NOT NULL DEFAULT '',
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE product_print_settings (
                    product_id        TEXT PRIMARY KEY NOT NULL
                                      REFERENCES products(id) ON DELETE CASCADE,
                    print_template    TEXT NOT NULL DEFAULT '',
                    batch_template    TEXT NOT NULL DEFAULT '',
                    client            TEXT NOT NULL DEFAULT '',
                    display_packaging TEXT NOT NULL DEFAULT '',
                    created_at        TEXT NOT NULL,
                    updated_at        TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE catalog_meta (
                    key   TEXT PRIMARY KEY NOT NULL,
                    value TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                "INSERT INTO catalog_meta (key, value) VALUES (?, ?)",
                (
                    ("revision", "0"),
                    ("has_short_col", "0"),
                    ("initialization_state", "ready"),
                ),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _validate_existing_database(self) -> None:
        try:
            connection = self._connect(readonly=True)
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version != SCHEMA_VERSION:
                    relation = "newer" if version > SCHEMA_VERSION else "unsupported"
                    raise CatalogSchemaError(
                        f"Catalog schema version {version} is {relation}; expected {SCHEMA_VERSION}"
                    )
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                if integrity != "ok":
                    raise CatalogSchemaError(f"Catalog integrity check failed: {integrity}")
                required = {"products", "product_print_settings", "catalog_meta"}
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                missing = required - tables
                if missing:
                    raise CatalogSchemaError(
                        "Catalog schema is incomplete: " + ", ".join(sorted(missing))
                    )
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise CatalogSchemaError("Catalog foreign key check failed")
                product_count = connection.execute(
                    "SELECT COUNT(*) FROM products"
                ).fetchone()[0]
                settings_count = connection.execute(
                    "SELECT COUNT(*) FROM product_print_settings"
                ).fetchone()[0]
                if product_count != settings_count:
                    raise CatalogSchemaError("Catalog contains products without print settings")
                connection.execute(_PRODUCT_SELECT + " LIMIT 0")
                for key in ("revision", "has_short_col", "initialization_state"):
                    if (
                        connection.execute(
                            "SELECT 1 FROM catalog_meta WHERE key = ?", (key,)
                        ).fetchone()
                        is None
                    ):
                        raise CatalogSchemaError(f"Catalog metadata is missing: {key}")
                self._revision(connection)
            finally:
                connection.close()
        except CatalogSchemaError:
            raise
        except sqlite3.DatabaseError as exc:
            raise CatalogSchemaError(f"Cannot open catalog database: {exc}") from exc

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> CatalogProduct:
        return CatalogProduct(**dict(row))

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM catalog_meta WHERE key = 'revision'"
        ).fetchone()
        if row is None:
            raise CatalogSchemaError("Catalog revision metadata is missing")
        return int(row[0])

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value) -> None:
        connection.execute(
            """
            INSERT INTO catalog_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )

    @staticmethod
    def _check_revision(actual: int, expected: int | None) -> None:
        if expected is not None and actual != int(expected):
            raise CatalogConflictError(
                f"Catalog revision changed: expected {expected}, actual {actual}"
            )

    def get_metadata(self) -> dict[str, str]:
        connection = self._connect(readonly=True)
        try:
            return {
                str(row[0]): str(row[1])
                for row in connection.execute("SELECT key, value FROM catalog_meta")
            }
        finally:
            connection.close()

    def read_snapshot(self) -> CatalogSnapshot:
        connection = self._connect(readonly=True)
        try:
            connection.execute("BEGIN")
            rows = connection.execute(_PRODUCT_SELECT + " ORDER BY p.rowid").fetchall()
            revision = self._revision(connection)
            meta = connection.execute(
                "SELECT value FROM catalog_meta WHERE key = 'has_short_col'"
            ).fetchone()
            connection.commit()
            return CatalogSnapshot(
                products=tuple(self._row_to_product(row) for row in rows),
                revision=revision,
                has_short_col=bool(int(meta[0])) if meta is not None else False,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_products(self) -> list[CatalogProduct]:
        return list(self.read_snapshot().products)

    def get_product(self, product_id: str) -> CatalogProduct | None:
        connection = self._connect(readonly=True)
        try:
            row = connection.execute(
                _PRODUCT_SELECT + " WHERE p.id = ?", (str(product_id),)
            ).fetchone()
            return None if row is None else self._row_to_product(row)
        finally:
            connection.close()

    def _validated_values(self, values: Mapping) -> dict[str, str]:
        unknown = set(values) - set(CATALOG_FIELDS)
        if unknown:
            raise CatalogValidationError(
                "Unknown catalog fields: " + ", ".join(sorted(map(str, unknown)))
            )
        if "gtin" not in values:
            raise CatalogValidationError("GTIN is required")
        result = {field: "" for field in CATALOG_FIELDS}
        result["gtin"] = normalize_gtin(values.get("gtin"))
        for field in _TEXT_FIELDS:
            result[field] = _text(values.get(field, ""))
        return result

    def _daily_backup_marker(self) -> str | None:
        today = datetime.now(timezone.utc).date().isoformat()
        if self.get_metadata().get("last_daily_backup_date") == today:
            return None
        self.backup()
        return today

    def create_product(
        self, values: Mapping, *, expected_revision: int | None = None
    ) -> CatalogProduct:
        prepared = self._validated_values(values)
        snapshot = self.read_snapshot()
        self._check_revision(snapshot.revision, expected_revision)
        if any(product.gtin == prepared["gtin"] for product in snapshot.products):
            raise CatalogValidationError(f"GTIN already exists: {prepared['gtin']}")
        backup_marker = self._daily_backup_marker()
        product_id = str(uuid.uuid4())
        timestamp = _utc_now()
        with self._write_transaction() as connection:
            self._check_revision(self._revision(connection), snapshot.revision)
            connection.execute(
                """
                INSERT INTO products (
                    id, gtin, name, brand, weight, shelf_life, permit_document,
                    permit_from, permit_to, packaging, material, marking_method,
                    tnved, short_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    *(prepared[field] for field in MASTER_FIELDS),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO product_print_settings (
                    product_id, print_template, batch_template, client,
                    display_packaging, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    prepared["print_template"],
                    "",
                    prepared["client"],
                    prepared["display_packaging"],
                    timestamp,
                    timestamp,
                ),
            )
            self._set_meta(connection, "revision", snapshot.revision + 1)
            self._set_meta(connection, "has_short_col", "1")
            if backup_marker:
                self._set_meta(connection, "last_daily_backup_date", backup_marker)
        return self.get_product(product_id)

    def update_product(
        self,
        product_id: str,
        changes: Mapping,
        *,
        expected_revision: int | None = None,
    ) -> CatalogProduct:
        unknown = set(changes) - set(CATALOG_FIELDS)
        if unknown:
            raise CatalogValidationError(
                "Unknown catalog fields: " + ", ".join(sorted(map(str, unknown)))
            )
        snapshot = self.read_snapshot()
        self._check_revision(snapshot.revision, expected_revision)
        current = next(
            (product for product in snapshot.products if product.id == str(product_id)),
            None,
        )
        if current is None:
            raise CatalogValidationError(f"Catalog product not found: {product_id}")
        candidate = {field: getattr(current, field) for field in CATALOG_FIELDS}
        for field, value in changes.items():
            candidate[field] = normalize_gtin(value) if field == "gtin" else _text(value)
        if all(candidate[field] == getattr(current, field) for field in CATALOG_FIELDS):
            return current
        if any(
            product.id != current.id and product.gtin == candidate["gtin"]
            for product in snapshot.products
        ):
            raise CatalogValidationError(f"GTIN already exists: {candidate['gtin']}")
        backup_marker = self._daily_backup_marker()
        timestamp = _utc_now()
        with self._write_transaction() as connection:
            self._check_revision(self._revision(connection), snapshot.revision)
            connection.execute(
                """
                UPDATE products SET
                    gtin = ?, name = ?, brand = ?, weight = ?, shelf_life = ?,
                    permit_document = ?, permit_from = ?, permit_to = ?,
                    packaging = ?, material = ?, marking_method = ?, tnved = ?,
                    short_name = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    *(candidate[field] for field in MASTER_FIELDS),
                    timestamp,
                    current.id,
                ),
            )
            connection.execute(
                """
                UPDATE product_print_settings SET
                    print_template = ?, client = ?, display_packaging = ?,
                    updated_at = ?
                WHERE product_id = ?
                """,
                (
                    *(candidate[field] for field in PRINT_SETTING_FIELDS),
                    timestamp,
                    current.id,
                ),
            )
            self._set_meta(connection, "revision", snapshot.revision + 1)
            self._set_meta(connection, "has_short_col", "1")
            if backup_marker:
                self._set_meta(connection, "last_daily_backup_date", backup_marker)
        return self.get_product(current.id)

    def delete_product(
        self, product_id: str, *, expected_revision: int | None = None
    ) -> None:
        snapshot = self.read_snapshot()
        self._check_revision(snapshot.revision, expected_revision)
        if not any(product.id == str(product_id) for product in snapshot.products):
            raise CatalogValidationError(f"Catalog product not found: {product_id}")
        backup_marker = self._daily_backup_marker()
        with self._write_transaction() as connection:
            self._check_revision(self._revision(connection), snapshot.revision)
            connection.execute("DELETE FROM products WHERE id = ?", (str(product_id),))
            self._set_meta(connection, "revision", snapshot.revision + 1)
            if backup_marker:
                self._set_meta(connection, "last_daily_backup_date", backup_marker)

    def _plan_import(
        self,
        records: Iterable[Mapping],
        *,
        present_fields: Iterable[str] | None = None,
        has_short_col: bool | None = None,
        expected_revision: int | None = None,
    ):
        raw_records = list(records)
        if present_fields is None:
            present = set().union(*(set(record) for record in raw_records))
        else:
            present = set(present_fields)
        unknown = present - set(CATALOG_FIELDS)
        if unknown:
            raise CatalogValidationError(
                "Unknown catalog fields: " + ", ".join(sorted(map(str, unknown)))
            )
        present.add("gtin")
        deduplicated: dict[str, dict[str, str]] = {}
        for record in raw_records:
            unknown_record_fields = set(record) - set(CATALOG_FIELDS)
            if unknown_record_fields:
                raise CatalogValidationError(
                    "Unknown catalog fields: "
                    + ", ".join(sorted(map(str, unknown_record_fields)))
                )
            gtin = normalize_gtin(record.get("gtin"))
            values = {"gtin": gtin}
            for field in _TEXT_FIELDS:
                if field in record:
                    values[field] = _text(record[field])
            deduplicated[gtin] = values
        duplicate_count = len(raw_records) - len(deduplicated)

        snapshot = self.read_snapshot()
        self._check_revision(snapshot.revision, expected_revision)
        existing = {product.gtin: product for product in snapshot.products}
        planned: list[tuple[CatalogProduct | None, dict[str, str]]] = []
        added = updated = unchanged = 0
        for gtin, incoming in deduplicated.items():
            current = existing.get(gtin)
            if current is None:
                values = {field: "" for field in CATALOG_FIELDS}
                values.update(incoming)
                added += 1
            else:
                values = {field: getattr(current, field) for field in CATALOG_FIELDS}
                for field in present:
                    if field in incoming:
                        values[field] = incoming[field]
                if any(values[field] != getattr(current, field) for field in CATALOG_FIELDS):
                    updated += 1
                else:
                    unchanged += 1
            planned.append((current, values))

        target_short = snapshot.has_short_col if has_short_col is None else bool(has_short_col)
        return (
            snapshot,
            planned,
            target_short,
            ImportResult(
                added=added,
                updated=updated,
                unchanged=unchanged,
                duplicate_count=duplicate_count,
                revision=snapshot.revision,
            ),
        )

    def preview_import(
        self,
        records: Iterable[Mapping],
        *,
        present_fields: Iterable[str] | None = None,
        has_short_col: bool | None = None,
        expected_revision: int | None = None,
    ) -> ImportResult:
        _, _, _, result = self._plan_import(
            records,
            present_fields=present_fields,
            has_short_col=has_short_col,
            expected_revision=expected_revision,
        )
        return result

    def apply_import(
        self,
        records: Iterable[Mapping],
        *,
        present_fields: Iterable[str] | None = None,
        has_short_col: bool | None = None,
        expected_revision: int | None = None,
        create_backup: bool = True,
    ) -> ImportResult:
        snapshot, planned, target_short, result = self._plan_import(
            records,
            present_fields=present_fields,
            has_short_col=has_short_col,
            expected_revision=expected_revision,
        )
        data_changed = bool(
            result.added
            or result.updated
            or target_short != snapshot.has_short_col
        )
        if not data_changed:
            return result

        if create_backup:
            self.backup()
        timestamp = _utc_now()
        with self._write_transaction() as connection:
            self._check_revision(self._revision(connection), snapshot.revision)
            for current, values in planned:
                if current is None:
                    product_id = str(uuid.uuid4())
                    connection.execute(
                        """
                        INSERT INTO products (
                            id, gtin, name, brand, weight, shelf_life,
                            permit_document, permit_from, permit_to, packaging,
                            material, marking_method, tnved, short_name,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            product_id,
                            *(values[field] for field in MASTER_FIELDS),
                            timestamp,
                            timestamp,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO product_print_settings (
                            product_id, print_template, batch_template, client,
                            display_packaging, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            product_id,
                            values["print_template"],
                            "",
                            values["client"],
                            values["display_packaging"],
                            timestamp,
                            timestamp,
                        ),
                    )
                elif any(
                    values[field] != getattr(current, field) for field in CATALOG_FIELDS
                ):
                    connection.execute(
                        """
                        UPDATE products SET
                            name = ?, brand = ?, weight = ?, shelf_life = ?,
                            permit_document = ?, permit_from = ?, permit_to = ?,
                            packaging = ?, material = ?, marking_method = ?,
                            tnved = ?, short_name = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            *(values[field] for field in MASTER_FIELDS if field != "gtin"),
                            timestamp,
                            current.id,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE product_print_settings SET
                            print_template = ?, client = ?, display_packaging = ?,
                            updated_at = ?
                        WHERE product_id = ?
                        """,
                        (
                            *(values[field] for field in PRINT_SETTING_FIELDS),
                            timestamp,
                            current.id,
                        ),
                    )
            next_revision = snapshot.revision + 1
            self._set_meta(connection, "revision", next_revision)
            self._set_meta(connection, "has_short_col", "1" if target_short else "0")
            self._set_meta(
                connection,
                "last_import_at",
                timestamp,
            )
        return ImportResult(
            added=result.added,
            updated=result.updated,
            unchanged=result.unchanged,
            duplicate_count=result.duplicate_count,
            revision=snapshot.revision + 1,
        )

    def count_template_usage(self, template_id: str) -> int:
        connection = self._connect(readonly=True)
        try:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM product_print_settings
                    WHERE print_template = ?
                    """,
                    (_text(template_id),),
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def backup(
        self, destination: str | os.PathLike[str] | None = None
    ) -> Path:
        if destination is None:
            backup_dir = self.path.parent / "backups"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            destination_path = backup_dir / f"catalog-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
            automatic = True
        else:
            destination_path = Path(destination)
            automatic = False
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists():
            raise CatalogError(f"Backup destination already exists: {destination_path}")
        if destination_path.resolve() == self.path.resolve():
            raise CatalogError("Backup destination must differ from catalog database")
        temp_path = destination_path.with_name(
            f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
        )
        source = self._connect(readonly=True)
        target = sqlite3.connect(temp_path)
        try:
            source.backup(target)
            result = target.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise CatalogError(f"Backup integrity check failed: {result}")
        except Exception:
            target.close()
            source.close()
            temp_path.unlink(missing_ok=True)
            raise
        else:
            target.close()
            source.close()
        os.replace(temp_path, destination_path)
        if automatic:
            backups = sorted(destination_path.parent.glob("catalog-*.sqlite3"))
            for old_backup in backups[:-10]:
                old_backup.unlink(missing_ok=True)
        return destination_path
