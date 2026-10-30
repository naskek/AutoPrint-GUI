# -*- coding: utf-8 -*-

import csv
import os

try:
    from win32com.client import Dispatch
except ImportError:
    Dispatch = None


BT_DO_NOT_SAVE_CHANGES = 1
BT_COLORS_24BIT = 4
BT_PRINT_SUCCESS = 0
PREVIEW_BACKGROUND_WHITE = 0xFFFFFF
BT_TEXT_FILE_DATABASE = 0
PRODUCTION_BATCH_SOURCE_BASENAME = "tmp_batch.csv"
PREVIEW_DATA_FIELDS = (
    "ShortName",
    "ShortGTIN",
    "EXP_DATE",
    "PROD_DATE",
    "PART_NUM",
    "DM",
    "NUM",
)


class BT:
    def __init__(self, logger):
        self.logger = logger
        self.app = None
        self._preview_fields = None

    def start(self):
        if not Dispatch:
            raise RuntimeError("pywin32 не установлен. Установите: pip install pywin32")
        self.logger.log("Запуск BarTender COM...")
        self.app = Dispatch("BarTender.Application")
        self.app.Visible = False
        self.logger.log("BarTender COM запущен.")

    def stop(self):
        if self.app:
            try:
                self.logger.log("Завершение BarTender COM...")
                self.app.Quit(BT_DO_NOT_SAVE_CHANGES)
            except Exception as e:
                self.logger.err(f"Не удалось корректно закрыть BarTender: {e}")
            finally:
                self.app = None

    def open_format(self, path):
        self._preview_fields = None
        self.logger.log(f"Открытие шаблона: {path}")
        fmt = self.app.Formats.Open(path, False, "")
        try:
            self.logger.log(f"NamedSubStrings: {[s.Name for s in fmt.NamedSubStrings]}")
        except Exception as e:
            self.logger.log(f"Не удалось прочитать NamedSubStrings: {e}")
        return fmt

    def set_common_print_flags(self, fmt):
        """Устанавливает флаги для печати без использования встроенной БД шаблона."""
        for attr, value in (("UseDatabase", False), ("SelectRecordsAtPrint", False), ("RecordRange", "1")):
            try:
                setattr(fmt, attr, value)
            except Exception as e:
                self.logger.log(f"Не удалось установить флаг '{attr}': {e}")

    def apply_fields(self, fmt, data: dict):
        """Подставляет значения в NamedSubStrings шаблона."""
        self._preview_fields = {
            key: value
            for key, value in data.items()
            if not str(key).startswith("_")
        }

        names = set()
        try:
            names = {s.Name for s in fmt.NamedSubStrings}
        except Exception as e:
            self.logger.log(f"Не удалось получить список полей (NamedSubStrings): {e}")

        payload = {k: v for k, v in data.items() if not k.startswith("_") and ((not names) or (k in names))}
        skipped = sorted(set(data.keys()) - set(payload.keys()) - {k for k in data if k.startswith("_")})
        # Пропущенные поля не считаем ошибкой: просто тихо игнорируем, чтобы не засорять лог.

        count = 0
        for k, v in payload.items():
            try:
                fmt.SetNamedSubStringValue(k, str(v))
                count += 1
            except Exception as e1:
                # Fallback for older versions
                try:
                    subs = getattr(fmt, "SubStrings", None)
                    if subs:
                        subs(k).Value = str(v)
                        count += 1
                except Exception as e2:
                    self.logger.err(f"Не удалось подставить поле '{k}': основная ошибка: {e1}; fallback ошибка: {e2}")
        self.logger.log(f"Подстановка полей: всего={len(payload)}, успешно={count}")
        return count > 0

    def _log_preview_messages(self, messages):
        try:
            coll = getattr(messages, "value", None) or messages
            count = int(getattr(coll, "Count", 0) or 0)
        except Exception:
            return
        for idx in range(1, count + 1):
            try:
                msg = coll.Item(idx)
            except Exception:
                try:
                    msg = coll.GetMessage(idx)
                except Exception:
                    continue
            try:
                text = str(getattr(msg, "Message", "") or "").strip()
            except Exception:
                text = ""
            if not text:
                continue
            try:
                severity = str(getattr(msg, "Severity", "") or "").strip()
            except Exception:
                severity = ""
            try:
                type_text = str(getattr(msg, "TypeText", "") or "").strip()
            except Exception:
                type_text = ""
            prefix = " / ".join(part for part in (type_text, severity) if part)
            self.logger.log(
                f"BT Preview Message: {prefix}: {text}" if prefix else f"BT Preview Message: {text}"
            )

    def _write_preview_data_file(self, directory):
        fields = self._preview_fields or {}
        path = os.path.join(directory, "preview_data.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=PREVIEW_DATA_FIELDS,
                delimiter="\t",
            )
            writer.writeheader()
            writer.writerow(
                {
                    key: (fields.get(key, "") or "")
                    for key in PREVIEW_DATA_FIELDS
                }
            )
        return path

    @staticmethod
    def _source_basename(path):
        return str(path or "").replace("\\", "/").rsplit("/", 1)[-1].lower()

    def _bind_preview_text_databases(self, fmt, preview_data_path):
        try:
            databases = fmt.Databases
            count = int(databases.Count or 0)
        except Exception as e:
            self.logger.log(f"Preview DB: Databases недоступен: {e}")
            return 0

        bound = 0
        for index in range(1, count + 1):
            stage = "Databases.GetDatabase"
            try:
                database = databases.GetDatabase(index)
                stage = "Database.Type"
                if int(database.Type) != BT_TEXT_FILE_DATABASE:
                    continue
                stage = "TextFile.FileName (чтение)"
                text_file = database.TextFile
                current_path = text_file.FileName
                if self._source_basename(current_path) != PRODUCTION_BATCH_SOURCE_BASENAME:
                    continue
                stage = "TextFile.FileName (запись)"
                text_file.FileName = preview_data_path
                bound += 1
            except Exception as e:
                raise RuntimeError(
                    f"Preview DB: ошибка БД #{index}, этап {stage}: {e}"
                ) from e

        if bound:
            try:
                fmt.PrintSetup.UseDatabase = True
            except Exception as e:
                raise RuntimeError(
                    f"Preview DB: не удалось включить PrintSetup.UseDatabase: {e}"
                ) from e
            self.logger.log(
                f"Preview DB: временный источник подключён ({bound}): {preview_data_path}"
            )
        elif count:
            self.logger.log(
                f"Preview DB: источник {PRODUCTION_BATCH_SOURCE_BASENAME} "
                f"не обнаружен среди {count} подключений; перепривязка не выполнена."
            )
        return bound

    def export_preview(self, fmt, path):
        """Экспортирует изображение шаблона как в production v3.2.4."""
        output_path = str(path).replace("%PageNumber%", "1")
        directory = os.path.dirname(os.path.abspath(output_path))
        try:
            os.makedirs(directory, exist_ok=True)
            if self._preview_fields:
                preview_data_path = self._write_preview_data_file(directory)
                self._bind_preview_text_databases(fmt, preview_data_path)
            fmt.ExportToFile(output_path, "PNG", 1, 300, 0)
            return True
        except Exception as e:
            self.logger.err(f"Не удалось экспортировать превью в '{output_path}': {e}")
            return False
        finally:
            self._preview_fields = None
