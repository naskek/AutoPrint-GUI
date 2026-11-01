# -*- coding: utf-8 -*-

import csv
import hashlib
import os
import shutil
import xml.etree.ElementTree as ET

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

    @staticmethod
    def _preview_db_connection_names(configuration):
        """Read connection names, not recordset names, from Seagull's XML.

        A document can have DatabaseConnection Name="@1" and RecordSet
        Name="tmp_batch". GetDatabase expects the connection name (@1).
        The XML is inspection-only; no Configuration mutation is attempted.
        """
        if not isinstance(configuration, str) or not configuration.strip():
            return ()
        root = ET.fromstring(configuration)
        connection_names = set()
        matches = set()
        for item in root.iter():
            tag = str(item.tag).rsplit("}", 1)[-1].casefold()
            attrs = {str(k).rsplit("}", 1)[-1].casefold(): str(v)
                     for k, v in item.attrib.items()}
            if tag == "databaseconnection" and attrs.get("name"):
                connection_names.add(attrs["name"])
            if tag == "recordset" and attrs.get("name", "").casefold() == "tmp_batch":
                if attrs.get("connection"):
                    matches.add(attrs["connection"])
        return tuple(sorted(connection_names & matches))

    def _bind_preview_text_databases(self, fmt, preview_data_path):
        try:
            databases = fmt.Databases
            count = int(databases.Count or 0)
        except Exception as e:
            self.logger.log(f"Preview DB: Databases недоступен: {e}")
            return 0

        # Prefer the documented database *connection* name, not a
        # recordset name. BarTender's Configuration XML exposes both.
        try:
            config = databases.Configuration
        except (AttributeError, TypeError):
            config = None
        try:
            connection_names = self._preview_db_connection_names(config)
        except ET.ParseError as e:
            raise RuntimeError("Preview DB: недоступна структура XML подключения") from e
        if config and not connection_names:
            raise RuntimeError(
                "Preview DB: не удалось однозначно определить подключение tmp_batch"
            )
        indices = connection_names if config else range(1, count + 1)
        bound = 0
        for index in indices:
            try:
                database = databases.GetDatabase(index)
            except Exception as e:
                # A real BarTender 2022 R5 installation reports COM error
                # 12345678 here despite valid Databases.Count/Configuration.
                # Exporting afterwards would show stale template data and
                # incorrectly report that the preview was refreshed.
                raise RuntimeError(
                    f"Preview DB: не удалось получить базу #{index}; "
                    "текущие данные не подтверждены, экспорт отменён"
                ) from e
            try:
                if int(database.Type) != BT_TEXT_FILE_DATABASE:
                    continue
                current_path = database.TextFile.FileName
                if self._source_basename(current_path) != PRODUCTION_BATCH_SOURCE_BASENAME:
                    continue
                database.TextFile.FileName = preview_data_path
                bound += 1
            except Exception as e:
                raise RuntimeError(
                    f"Preview DB: ошибка привязки текстовой базы #{index}; "
                    "экспорт отменён"
                ) from e

        if bound:
            try:
                # Format.UseDatabase is the documented active-record flag;
                # PrintSetup.UseDatabase is also used by the print setup.
                # Both changes live only in the opened no-save preview format.
                fmt.UseDatabase = True
                fmt.PrintSetup.UseDatabase = True
            except Exception as e:
                raise RuntimeError(
                    "Preview DB: не удалось включить UseDatabase; экспорт отменён"
                ) from e
            self.logger.log(
                f"Preview DB: временный источник подключён ({bound}): {preview_data_path}"
            )
        return bound

    @staticmethod
    def supports_gs1_btxml_preview(fmt):
        """Only the verified tmp_batch/GS1 contract can use BTXML preview."""
        try:
            db_fields = {
                name.strip().casefold()
                for name in str(fmt.UsedNames(1, "\n") or "").splitlines()
            }
            required = {
                ("tmp_batch.tmp_batch." + name).casefold()
                for name in ("ShortName", "ShortGTIN", "PROD_DATE",
                             "PART_NUM", "DM", "NUM")
            }
            named = {
                name.strip().casefold()
                for name in str(fmt.UsedNames(0, "\n") or "").splitlines()
            }
            gs1_names = {"gs1_gtin", "gs1_serial", "gs1_crypto"}
            return (
                required.issubset(db_fields)
                and gs1_names.issubset(named)
                and int(fmt.Databases.Count or 0) == 1
            )
        except Exception:
            return False

    @staticmethod
    def _build_gs1_btxml_preview_request(template_path, csv_path, directory):
        """Build only the documented BTXML export command, never Print."""
        root = ET.Element("XMLScript", Version="2.0",
                          Name="AutoPrintLivePreview")
        cmd = ET.SubElement(root, "Command", Name="PreviewOnly")
        preview = ET.SubElement(
            cmd, "ExportPrintPreviewToImage",
            ReturnImageInResponse="false",
        )
        for tag, value in (
            ("Format", str(template_path)),
            ("Folder", str(directory)),
            ("FileNameTemplate", "preview_%PageNumber%.png"),
            ("ImageFormatType", "PNG"),
            ("Colors", "btColors24Bit"),
            ("DPI", "300"),
            ("BackgroundColor", "16777215"),
            ("Overwrite", "false"),
            ("IncludeMargins", "false"),
            ("IncludeBorder", "false"),
        ):
            ET.SubElement(preview, tag).text = value
        recordset = ET.SubElement(
            preview, "RecordSet", Name="tmp_batch", Type="btTextFile",
            AddIfNone="false",
        )
        ET.SubElement(recordset, "FileName").text = str(csv_path)
        ET.SubElement(recordset, "Delimitation").text = "btDelimTab"
        ET.SubElement(recordset, "UseFieldNamesFromFirstRecord").text = "true"
        setup = ET.SubElement(preview, "PrintSetup")
        for tag, value in (
            ("UseDatabase", "true"), ("RecordRange", "1"),
            ("AutoPrintAgain", "false"), ("EnablePrompting", "false"),
            ("IdenticalCopiesOfLabel", "1"),
            ("NumberSerializedLabels", "1"),
        ):
            ET.SubElement(setup, tag).text = value
        return ET.tostring(root, encoding="unicode")

    @staticmethod
    def _btxml_preview_response_ok(response):
        """Return a result without exposing raw XML/marking values in logs."""
        if not isinstance(response, str):
            return False
        try:
            root = ET.fromstring(response)
            if root.tag != "Response":
                return False
            if root.find(".//Error") is not None:
                return False
            if root.find("./Command/ExportPrintPreviewToImage") is None:
                return False
            for msg in root.iter("Message"):
                if msg.attrib.get("Severity", "").casefold() == "error":
                    return False
            return True
        except ET.ParseError:
            return False

    def export_gs1_btxml_preview(self, btw_path, row, output_path):
        """Preview one real GS1 DB row without calling any print submit API.

        The current preview-owned directory holds the copied BTW and CSV.
        Close temporary COM format with btDoNotSaveChanges when accessible.
        This is restricted to templates matching supports_gs1_btxml_preview.
        """
        dirname = os.path.dirname(os.path.abspath(output_path))
        preview_template = os.path.join(dirname, "btxml_preview.btw")
        preview_csv = os.path.join(dirname, "preview_data.csv")
        final_image = os.path.join(dirname, "preview_1.png")
        bt_doc = None
        try:
            if self.app is None:
                raise RuntimeError("BarTender COM не запущен.")
            if not isinstance(row, dict) or not row.get("DM"):
                raise RuntimeError("Нет данных DM для предпросмотра.")
            if not all(k in row for k in PREVIEW_DATA_FIELDS):
                raise RuntimeError("Неполная запись для GS1-предпросмотра.")
            from pythoncom import VT_BYREF, VT_DISPATCH
            from win32com.client import VARIANT

            os.makedirs(dirname, exist_ok=True)
            with open(btw_path, "rb") as src:
                source_sha = hashlib.sha256(src.read()).hexdigest()
            shutil.copy2(btw_path, preview_template)
            with open(preview_template, "rb") as copied:
                copy_sha = hashlib.sha256(copied.read()).hexdigest()

            with open(preview_csv, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=PREVIEW_DATA_FIELDS, delimiter="\t",
                )
                writer.writeheader()
                writer.writerow({key: row.get(key) or "" for key in PREVIEW_DATA_FIELDS})

            xml = self._build_gs1_btxml_preview_request(
                preview_template, preview_csv, dirname
            )
            messages = VARIANT(VT_BYREF | VT_DISPATCH, None)
            response = self.app.XMLScript(xml, 0, messages)
            if not self._btxml_preview_response_ok(response):
                raise RuntimeError("BarTender BTXML не подтвердил экспорт.")
            if not os.path.isfile(final_image) or os.path.getsize(final_image) == 0:
                raise RuntimeError("BarTender BTXML не создал изображение.")

            with open(btw_path, "rb") as src:
                if hashlib.sha256(src.read()).hexdigest() != source_sha:
                    raise RuntimeError("Исходный BTW изменился во время превью.")
            with open(preview_template, "rb") as copied:
                if hashlib.sha256(copied.read()).hexdigest() != copy_sha:
                    raise RuntimeError("Временная копия BTW изменилась.")
            self.logger.log("BTXML GS1: экспорт записи завершён (без печати).")
            return True
        except Exception as exc:
            # Do not log raw XML, source CSV records or marking values.
            self.logger.err(
                "GS1 preview: не удалось сформировать корректный кадр "
                f"({type(exc).__name__}); печать не выполнялась."
            )
            return False
        finally:
            if self.app is not None and os.path.isfile(preview_template):
                try:
                    bt_doc = self.app.Formats.Item(preview_template)
                    bt_doc.Close(BT_DO_NOT_SAVE_CHANGES)
                except Exception:
                    # BarTender can retain file handles after export. The
                    # preview-owned temporary directory remains in place,
                    # until cleanup/restart; never touch production data.
                    self.logger.log(
                        "GS1 preview: временный BTW может удерживаться "
                        "BarTender; очистка отложена."
                    )
                finally:
                    bt_doc = None

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
