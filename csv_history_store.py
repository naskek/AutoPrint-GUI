"""Issue #7: durable per-CSV submission history; never equate submit with print."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from template_storage import get_user_data_dir

SCHEMA_VERSION = 1
STATES = frozenset({
    "prepared", "submitted_to_bartender", "spooled", "completed",
    "failed", "cancelled", "unknown",
})


class CsvHistoryError(RuntimeError):
    pass


def get_csv_history_path() -> Path:
    return Path(get_user_data_dir()) / "csv-history.sqlite3"


def normalize_csv_path(path) -> str:
    return os.path.normcase(os.path.abspath(str(path or ""))) if path else ""


def legacy_config_paths(base_dir) -> list[Path]:
    """Oldest to newest; includes both historical installation layouts."""
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.extend((Path(local) / "BarTenderGUI", Path(local) / "AutoPrint"))
    roots.append(Path(base_dir))
    paths = []
    seen = set()
    for root in roots:
        for candidate in (root / "config" / "config.json", root / "config.json"):
            key = os.path.normcase(os.path.abspath(candidate))
            if key not in seen:
                seen.add(key)
                paths.append(candidate)
    return paths


def _load_legacy_stats(config_paths) -> dict:
    merged = {}
    seen_events = {}
    for path in config_paths:
        source = Path(path)
        if not source.is_file():
            continue
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CsvHistoryError(f"Cannot read legacy print-history config {source}: {exc}") from exc
        if not isinstance(value, dict):
            raise CsvHistoryError(f"Invalid legacy config: {source}")
        stats = value.get("csv_print_stats", {})
        if not isinstance(stats, dict):
            raise CsvHistoryError(f"Invalid csv_print_stats: {source}")
        for raw_path, raw in stats.items():
            key = normalize_csv_path(raw_path)
            if not key or not isinstance(raw, dict):
                continue
            current = merged.get(key, {})
            old_date = str(current.get("last_print") or "")
            new_date = str(raw.get("last_print") or "")
            # Keep most recent aggregate metadata, but union all historical
            # entries across BarTenderGUI and AutoPrint without duplicates.
            if not current or new_date >= old_date:
                selected = {**current, **raw}
            else:
                selected = {**raw, **current}
            history = []
            fingerprints = seen_events.setdefault(key, set())
            for event in list(current.get("history") or []) + list(raw.get("history") or []):
                if not isinstance(event, dict):
                    continue
                fingerprint = json.dumps(event, sort_keys=True, ensure_ascii=False)
                if fingerprint not in fingerprints:
                    fingerprints.add(fingerprint)
                    history.append(event)
            # Previous unique events already lived in current.
            selected["history"] = history
            selected["printed"] = bool(current.get("printed") or raw.get("printed"))
            selected["print_runs"] = max(
                int(current.get("print_runs") or 0),
                int(raw.get("print_runs") or 0),
                len(history),
            )
            selected["state"] = (
                "submitted_to_bartender" if selected["printed"] else "unknown"
            )
            merged[key] = selected
    return merged


class CsvHistoryStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else get_csv_history_path()

    @staticmethod
    def _schema(db):
        db.executescript("""
            CREATE TABLE csv_stats (
                csv_path TEXT PRIMARY KEY NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE print_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                csv_path TEXT NOT NULL,
                state TEXT NOT NULL,
                event_json TEXT NOT NULL
            );
            CREATE INDEX events_by_csv ON print_events(csv_path, id);
            PRAGMA user_version = 1;
        """)

    def _validate(self):
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise CsvHistoryError(f"Unsupported CSV history version: {version}")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise CsvHistoryError("CSV history quick_check failed")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise CsvHistoryError("CSV history foreign keys invalid")
            for table in ("csv_stats", "print_events"):
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone() is None:
                    raise CsvHistoryError(f"CSV history table missing: {table}")
            for path, payload in db.execute("SELECT csv_path, payload_json FROM csv_stats"):
                try:
                    parsed = json.loads(payload)
                    if not isinstance(parsed, dict):
                        raise ValueError("expected an object")
                except (ValueError, TypeError) as exc:
                    raise CsvHistoryError(f"Invalid history record {path}: {exc}") from exc

    def initialize(self, config_paths=()):
        if self.path.exists():
            try:
                self._validate()
            except sqlite3.DatabaseError as exc:
                raise CsvHistoryError(f"Invalid existing CSV history {self.path}: {exc}") from exc
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        legacy = _load_legacy_stats(config_paths)
        stage = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with closing(sqlite3.connect(stage)) as db:
                self._schema(db)
                with db:
                    for path, info in legacy.items():
                        db.execute(
                            "INSERT INTO csv_stats VALUES (?, ?)",
                            (path, json.dumps(info, ensure_ascii=False)),
                        )
                        for event in info.get("history") or []:
                            db.execute(
                                "INSERT INTO print_events (csv_path, state, event_json) VALUES (?, ?, ?)",
                                (path, "submitted_to_bartender" if info["printed"] else "unknown",
                                 json.dumps(event, ensure_ascii=False)),
                            )
            with closing(sqlite3.connect(stage.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise CsvHistoryError("Staged CSV history failed integrity check")
            os.link(stage, self.path)  # no overwrite of concurrently created DB
        finally:
            stage.unlink(missing_ok=True)
        self._validate()

    def read_all(self) -> dict:
        self._validate()
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            return {
                path: json.loads(payload)
                for path, payload in db.execute("SELECT csv_path, payload_json FROM csv_stats")
            }

    def append_event(self, path, state, *, printer=None, fmt=None,
                     sent=0, calib=0, user="-", timestamp=None):
        if state not in STATES:
            raise ValueError(f"Invalid print state: {state}")
        key = normalize_csv_path(path)
        if not key:
            raise ValueError("CSV path required")
        stamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        event = {
            "ts": stamp, "state": state, "printer": printer, "fmt": fmt,
            "sent": int(sent or 0), "calib": int(calib or 0), "user": user,
        }
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA busy_timeout=2000")
            with db:  # status and event committed together or not at all
                row = db.execute(
                    "SELECT payload_json FROM csv_stats WHERE csv_path=?", (key,)
                ).fetchone()
                info = json.loads(row[0]) if row else {}
                history = list(info.get("history") or [])
                history.append(event)
                info["history"] = history
                info["state"] = state
                info["last_event"] = stamp
                if state == "submitted_to_bartender":
                    info.update({
                        "printed": True,
                        "last_print": stamp,  # historical key: means submitted, not completed
                        "print_runs": int(info.get("print_runs") or 0) + 1,
                        "last_sent": int(sent or 0), "last_printer": printer,
                        "last_fmt": fmt, "last_calib": int(calib or 0), "last_user": user,
                    })
                db.execute(
                    """INSERT INTO csv_stats(csv_path, payload_json) VALUES (?, ?)
                    ON CONFLICT(csv_path) DO UPDATE SET payload_json=excluded.payload_json""",
                    (key, json.dumps(info, ensure_ascii=False)),
                )
                db.execute(
                    "INSERT INTO print_events(csv_path, state, event_json) VALUES (?, ?, ?)",
                    (key, state, json.dumps(event, ensure_ascii=False)),
                )
        return info
