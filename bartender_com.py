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
        # BarTender 2022 BTXML Format can close the export-only document
        # at command completion without saving changes. Avoid Forms.Item()
        # post-export lookup: not verified on the deployed ActiveX collection.
        ET.SubElement(
            preview, "Format", CloseAtEndOfJob="true",
            SaveAtEndOfJob="false",
        ).text = str(template_path)
        for tag, value in (
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
        BTXML Format closes at export command completion without saving.
        This is restricted to templates matching supports_gs1_btxml_preview.
        """
        dirname = os.path.dirname(os.path.abspath(output_path))
        preview_template = os.path.join(dirname, "btxml_preview.btw")
        preview_csv = os.path.join(dirname, "preview_data.csv")
        final_image = os.path.join(dirname, "preview_1.png")
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
    @staticmethod
    def build_gs1_btxml_print_request(template_path, csv_path, printer,
                                     job_name, row_count, copies=1):
        """One explicitly-bound BTXML Print for one verified GS1 batch.

        Seagull 2022 documents Print/RecordSet/FileName/RecordRange, and
        Format CloseAtEndOfJob/SaveAtEndOfJob. Never use Databases.Item
        or an implicit document database path.
        """
        import xml.etree.ElementTree as ET

        if not all(isinstance(value, str) and value.strip()
                   for value in (template_path, csv_path, printer, job_name)):
            raise ValueError("BTXML Print: incomplete document, data, printer or job")
        if isinstance(row_count, bool) or int(row_count) < 1:
            raise ValueError("BTXML Print: row_count must be positive")
        if isinstance(copies, bool) or int(copies) != 1:
            raise ValueError("BTXML Print: only one copy per record is supported")

        root = ET.Element("XMLScript", Version="2.0", Name="AutoPrintGS1Batch")
        command = ET.SubElement(root, "Command", Name="GS1Batch")
        request = ET.SubElement(
            command, "Print", JobName=job_name,
            WaitForJobToComplete="false", Timeout="30000",
            ReturnPrintData="false", ReturnSummary="false",
            ReturnLabelData="false",
        )
        ET.SubElement(
            request, "Format", CloseAtEndOfJob="true",
            SaveAtEndOfJob="false",
        ).text = template_path
        recordset = ET.SubElement(
            request, "RecordSet", Name="tmp_batch",
            Type="btTextFile", AddIfNone="false",
        )
        ET.SubElement(recordset, "FileName").text = csv_path
        ET.SubElement(recordset, "Delimitation").text = "btDelimTab"
        ET.SubElement(recordset, "UseFieldNamesFromFirstRecord").text = "true"
        setup = ET.SubElement(request, "PrintSetup")
        for tag, value in (
            ("Printer", printer), ("UseDatabase", "true"),
            ("RecordRange", f"1-{int(row_count)}"),
            ("AutoPrintAgain", "false"), ("EnablePrompting", "false"),
            ("IdenticalCopiesOfLabel", "1"), ("NumberSerializedLabels", "1"),
        ):
            ET.SubElement(setup, tag).text = value
        if len(root.findall(".//Print")) != 1:
            raise RuntimeError("BTXML Print request must contain exactly one submit")
        return ET.tostring(root, encoding="unicode")

    @staticmethod
    def _btxml_batch_print_response(response):
        """Positive spool-submit evidence only; all else ambiguous after send.

        Seagull's XML response documents Print.JobLastStatus='Sent'.
        This does not mean the physical printer completed the labels.
        """
        import xml.etree.ElementTree as ET

        if not isinstance(response, str):
            return "ambiguous"
        try:
            root = ET.fromstring(response)
            if root.tag != "Response" or root.find(".//Error") is not None:
                return "ambiguous"
            commands = root.findall("./Command")
            if len(commands) != 1:
                return "ambiguous"
            prints = commands[0].findall("./Print")
            if len(prints) != 1:
                return "ambiguous"
            for message in root.iter("Message"):
                if str(message.get("Severity") or "").casefold() == "error":
                    return "ambiguous"
            printed = prints[0]
            status = str(printed.get("JobLastStatus") or "").casefold()
            if status != "sent":
                return "ambiguous"
            return "submitted"
        except (ET.ParseError, TypeError, ValueError):
            return "ambiguous"

    @staticmethod
    def _btxml_batch_response_summary(response):
        """Return only allowlisted job metadata; NEVER log the raw XML.

        No Message.Text, file paths, job names, record values, product data
        or marking codes from response are permitted in application logs.
        """
        import xml.etree.ElementTree as ET

        if not isinstance(response, str):
            return "response=non-xml-type"
        try:
            root = ET.fromstring(response)
        except (ET.ParseError, ValueError, TypeError):
            return "response=invalid-xml"
        if root.tag != "Response":
            return "response=unexpected-root"

        commands = root.findall("./Command")
        prints = commands[0].findall("./Print") if len(commands) == 1 else []
        if len(commands) != 1 or len(prints) != 1:
            return (
                f"response=structural-mismatch "
                f"commands={min(len(commands), 99)} prints={min(len(prints), 99)}"
            )
        printed = prints[0]
        status = str(printed.get("JobLastStatus") or "").strip().casefold()
        known_status = {
            "creating", "queued", "sending", "paused", "resumed",
            "cancelled", "sent", "sent (printing verified)", "unknown",
        }
        safe_status = status if status in known_status else (
            "missing" if not status else "other"
        )
        completed = str(printed.get("JobCompleted") or "").strip().casefold()
        safe_completed = completed if completed in {"true", "false"} else (
            "missing" if not completed else "other"
        )
        nested = str(
            printed.findtext("./JobStatus/LastStatus") or ""
        ).strip().casefold()
        safe_nested = nested if nested in known_status else (
            "missing" if not nested else "other"
        )
        severities = []
        message_ids = []
        for message in list(printed.iter("Message"))[:10]:
            value = str(message.get("Severity") or "").strip().casefold()
            if value in {"information", "warning", "error"}:
                severities.append(value)
            else:
                severities.append("other")
            identifier = str(message.get("ID") or "").strip()
            message_ids.append(identifier if identifier.isdecimal()
                               and len(identifier) <= 8 else "other")
        has_error_node = root.find(".//Error") is not None
        return (
            f"response=print job_status={safe_status} "
            f"completed={safe_completed} last_status={safe_nested} "
            f"error_tag={str(has_error_node).lower()} "
            f"message_severities={','.join(severities) or 'none'} "
            f"message_ids={','.join(message_ids) or 'none'}"
        )

    def submit_gs1_btxml_batch(self, btw_path, csv_path, printer,
                               job_name, row_count, *, expected_dm,
                               copies=1, show_dialog=False):
        """Explicit immutable-job-input GS1 BTXML print; max ONE XMLScript.

        Any exception or malformed result after XMLScript starts is ambiguous.
        Inputs remain in owner-profile print-job-inputs, not deleted while
        BarTender/Windows may still have a pending asynchronous job. Cleanup
        needs an independently verified job-lifecycle policy (#31).
        """
        import hashlib
        import tempfile
        from pathlib import Path
        from template_storage import get_user_data_dir

        if (not isinstance(expected_dm, (tuple, list))
                or len(expected_dm) != int(row_count)
                or not all(isinstance(value, str) for value in expected_dm)):
            self.logger.err(
                "BTXML GS1: набор ожидаемых кодов не подтверждён; "
                "отправка запрещена."
            )
            return "failed"
        if show_dialog:
            self.logger.err(
                "BTXML GS1: диалог печати для этого пути не поддерживается; "
                "отправка запрещена до отключения опции."
            )
            return "failed"
        if self.app is None:
            self.logger.err("BTXML GS1: BarTender COM не запущен.")
            return "failed"

        try:
            original = Path(btw_path).resolve(strict=True)
            data = Path(csv_path).resolve(strict=True)
            if original.suffix.casefold() != ".btw" or data.stat().st_size == 0:
                raise ValueError("Missing BTW or empty batch CSV")
            user_area = Path(get_user_data_dir()) / "print-job-inputs"
            user_area.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix="gs1-", dir=str(user_area)))
            copied_btw = directory / original.name
            copied_csv = directory / "tmp_batch.csv"
            shutil.copy2(original, copied_btw)
            shutil.copy2(data, copied_csv)
            if hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(
                copied_btw.read_bytes()
            ).digest():
                raise RuntimeError("BTW copy failed integrity check")
            if hashlib.sha256(data.read_bytes()).digest() != hashlib.sha256(
                copied_csv.read_bytes()
            ).digest():
                raise RuntimeError("CSV copy failed integrity check")

            # No raw GS1 values or BTXML strings in logs. The directory is
            # deliberately retained after an asynchronous print submission.
            with copied_csv.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, delimiter="\t")
                if (not reader.fieldnames
                        or len(reader.fieldnames) != len(set(reader.fieldnames))
                        or "DM" not in reader.fieldnames):
                    raise ValueError("Invalid GS1 text header")
                copied_dm = [row.get("DM") for row in reader]
            if copied_dm != list(expected_dm):
                raise ValueError("Copied GS1 CSV does not match the verified batch")
            xml = self.build_gs1_btxml_print_request(
                str(copied_btw), str(copied_csv),
                printer, job_name, row_count, copies,
            )
            from pythoncom import VT_BYREF, VT_DISPATCH
            from win32com.client import VARIANT
            messages = VARIANT(VT_BYREF | VT_DISPATCH, None)
        except Exception as exc:
            self.logger.err(
                f"BTXML GS1: предварительная подготовка не удалась "
                f"({type(exc).__name__}); задание не отправлено."
            )
            return "failed"

        self.logger.log(
            f"BTXML GS1: начало одной отправки {row_count} записей; "
            f"входные файлы сохранены: {directory}."
        )
        try:
            # This is the SINGLE submission attempt; no retry/fallback,
            # including timeout, COM exception, bad response or status.
            response = self.app.XMLScript(xml, 0, messages)
        except Exception as exc:
            self.logger.err(
                "BTXML GS1: исключение после начала XMLScript "
                f"({type(exc).__name__}); отправка неоднозначна, "
                "повтор запрещён."
            )
            return "ambiguous"
        # A non-waiting BTXML Print may return before job status reaches
        # Sent. Preserve the conservative ambiguous result, but capture
        # safe metadata to diagnose this without another print attempt.
        try:
            self.logger.log(
                "BTXML GS1: " + self._btxml_batch_response_summary(response)
            )
        except Exception:
            pass
        result = self._btxml_batch_print_response(response)
        if result != "submitted":
            self.logger.err(
                "BTXML GS1: отсутствует подтверждение JobLastStatus=Sent; "
                "отправка неоднозначна, повтор запрещён."
            )
        else:
            self.logger.log(
                "BTXML GS1: BarTender сообщил JobLastStatus=Sent "
                "(отправлено в очередь, физическая печать не подтверждена)."
            )
        return result

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
