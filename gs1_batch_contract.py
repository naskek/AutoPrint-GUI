"""Standalone, read-only GS1 batch preflight; no GUI imports or print calls.

Shared by AutoPrint's pre-submit guard and Windows COM diagnostics.
The schema-only fallback where BarTender does not expose a physical CSV path
requires an isolated source-to-PDF smoke before a PR can merge.
"""
from __future__ import annotations

import csv
import os
import xml.etree.ElementTree as ET

from tools.gs1_dm_audit import BATCH_FIELDS


def verify_dm_batch_database(fmt, expected_path: str, logger, expected_rows=None) -> bool:
    """Check the GS1 batch contract without relying on GetDatabase().

    BarTender 2022 R5 on the production machine exposes Databases.Count,
    Databases.Configuration and UsedNames, but GetDatabase(1) *and*
    GetDatabase("tmp_batch") raise COM error 12345678 even when the
    database-backed print completes. Configuration identifies the recordset;
    if it includes an explicit file path, it must match this batch CSV.

    This validates the schema and available binding evidence, not the
    runtime print output. The exact source-to-symbol check remains a smoke
    gate; do not silently fall back to NamedSubStrings.
    """
    import xml.etree.ElementTree as ET

    expected = os.path.normcase(os.path.abspath(expected_path))
    try:
        with open(expected_path, "r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            header = reader.fieldnames or []
            if not header or len(header) != len(set(header)) or "DM" not in header:
                logger.err("DM batch: текущий CSV не содержит единственного поля DM.")
                return False
            data_rows = list(reader)
        if expected_rows is not None:
            source_dm = [(row.get("DM") or "") for row in expected_rows]
            csv_dm = [(row.get("DM") or "") for row in data_rows]
            if csv_dm != source_dm:
                logger.err(
                    "DM batch: содержимое текущего CSV отличается "
                    "от сформированного пакета; печать остановлена."
                )
                return False
        if not data_rows:
            logger.err("DM batch: текущий CSV не содержит записей.")
            return False
        seen = set()
        for index, row in enumerate(data_rows):
            dm = row.get("DM") or ""
            if dm == "X":
                # Only the original six calibration fixtures can use X.
                if index >= 6 or not all(
                    row.get(key) == (
                        "1" if key == "NUM" else "000" if key == "ShortGTIN" else "X"
                    )
                    for key in BATCH_FIELDS
                ):
                    logger.err(
                        f"DM batch: некорректная калибровочная запись {index + 1}."
                    )
                    return False
            elif not dm or dm in seen:
                logger.err(
                    f"DM batch: пустой или повторный код в строке {index + 1}."
                )
                return False
            else:
                seen.add(dm)
        if not seen:
            logger.err("DM batch: нет ни одного действительного кода маркировки.")
            return False
    except (OSError, UnicodeError, csv.Error) as exc:
        logger.err(f"DM batch: не удалось прочитать текущий CSV: {exc}")
        return False

    try:
        dbs = fmt.Databases
        if int(dbs.Count or 0) != 1:
            logger.err("DM batch: ожидалось одно подключение tmp_batch.")
            return False
        used = {name.casefold() for name in str(fmt.UsedNames(1, "\n") or "").splitlines()}
        if "tmp_batch.tmp_batch.dm" not in used:
            logger.err("DM batch: нет привязки tmp_batch.tmp_batch.DM в UsedNames.")
            return False
        config = dbs.Configuration
        if not isinstance(config, str) or not config.strip():
            logger.err("DM batch: конфигурация базы недоступна.")
            return False
        root = ET.fromstring(config)
        db_names = set()
        file_paths = []
        for node in root.iter():
            key = str(node.tag).rsplit("}", 1)[-1].casefold()
            value = (node.text or "").strip()
            if key == "name" and value:
                db_names.add(value.casefold())
            elif key == "filename" and value:
                file_paths.append(value)
            for attr, attr_value in node.attrib.items():
                attr_key = str(attr).rsplit("}", 1)[-1].casefold()
                if attr_key == "name" and attr_value.strip():
                    db_names.add(attr_value.strip().casefold())
                elif attr_key == "filename" and attr_value.strip():
                    file_paths.append(attr_value.strip())
        if "tmp_batch" not in db_names:
            logger.err("DM batch: recordset tmp_batch отсутствует в конфигурации.")
            return False
        if file_paths and any(
            os.path.normcase(os.path.abspath(path)) != expected
            for path in file_paths
        ):
            logger.err(
                "DM batch: указанный в конфигурации путь БД не совпадает "
                "с текущим tmp_batch.csv; печать остановлена."
            )
            return False
        if not file_paths:
            logger.log(
                "DM batch: схема tmp_batch/DM подтверждена, но путь файла "
                "не предоставлен BarTender COM; требуется контрольный PDF smoke."
            )
        return True
    except Exception as exc:
        logger.err(f"DM batch: ошибка проверки конфигурации: {exc}")
        return False
