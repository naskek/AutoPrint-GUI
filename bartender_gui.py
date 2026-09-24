# -*- coding: utf-8 -*-
"""
AutoPrint
"""

import os
import csv
import re
import time
import traceback
import datetime as dt
import atexit
import sys
import json
import tempfile
import getpass
import threading

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog as fd, messagebox as mb, Menu, ttk
from PIL import Image

from utils import (
    get_base_dir, load_config, save_config, enum_printers,
    parse_date_ru, only_digits
)
from template_storage import (
    get_bundled_templates_dir,
    get_user_templates_dir,
    migrate_legacy_templates,
)
from data_processing import load_kontur_raw, enrich_row
from catalog_compat import build_product_map
from catalog_excel import (
    CATALOG_HEADERS as SQLITE_CATALOG_HEADERS,
    FIELD_TO_HEADER,
    apply_prepared_import,
    export_catalog_xlsx,
    prepare_excel_import,
)
from catalog_runtime import initialize_catalog_runtime, install_prepared_catalog
from catalog_store import (
    CATALOG_FIELDS,
    CatalogConflictError,
    CatalogError,
    get_catalog_db_path,
)
from bartender_com import BT
from crpt_client import (
    crpt_check_statuses_sync, parse_codes, normalize_code,
    crpt_check_connection_sync
)
from services.crpt_settings import load_crpt_settings, set_runtime_overrides
from services.crpt_token_manager import get_token_manager, CrptTokenError
from services import crpt_auth
from licensing import ensure_license_or_exit

BASE_DIR = get_base_dir()
CONFIG_DIR = os.path.join(BASE_DIR, "config")
BUNDLED_TEMPLATES_DIR = get_bundled_templates_dir(BASE_DIR)
TEMPLATES_DIR = get_user_templates_dir()

APP_TITLE    = "AutoPrint V3.0"
APP_VERSION = "3.0"
DEFAULT_APPEARANCE_MODE = "dark"
PREVIEW_NAME = "preview.png"
STICKER_BTW_NAME = "sticker60x40.btw"
APP_ICON_RELATIVE_PATH = os.path.join("assets", "icons", "autoprint.ico")
STATS_PATH = os.path.join(CONFIG_DIR, "stats.json")
DEFAULT_REQ_COLS = ["ShortName", "ShortGTIN", "EXP_DATE", "PROD_DATE", "PART_NUM", "DM", "NUM"]
PART_MODE_OPTIONS = ("Не использовать", "Ввод вручную")
CATALOG_HEADERS = list(SQLITE_CATALOG_HEADERS)
HEADER_TO_FIELD = {header: field for field, header in FIELD_TO_HEADER.items()}
REPORT_TURNOVER_HEADERS = [
    "Код маркировки",
    "GTIN",
    "Наименование",
    "ТН ВЭД",
    "Тип документа",
    "Номер документа",
    "Дата документа",
]
REPORT_TURNOVER_DOC_TYPE = "Декларация"
XLSX_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MAIN_LAYOUT_SECTION_NAMES = (
    "csv_files_section",
    "params_section",
    "print_section",
    "preview_section",
    "source_section",
    "log_section",
    "status_bar_frame",
)
MAIN_LAYOUT_BLOCK_ORDER = (
    ("left", "csv_files_section"),
    ("left", "params_section"),
    ("left", "print_section"),
    ("center", "preview_section"),
    ("right", "source_section"),
)
PRINT_BATCH_CONTAINER_SCROLLABLE = False
CSV_FILE_LIST_SCROLL_POLICY = "vertical-auto"
PATH_RESIZE_DEBOUNCE_MS = 80
CSV_SCROLLBAR_RESIZE_DEBOUNCE_MS = 120
PREVIEW_RESIZE_DEBOUNCE_MS = 160
LIVE_PREVIEW_DEBOUNCE_MS = 400
TOP_LEVEL_MENU_ORDER = ("Каталог", "Шаблоны", "Работа с кодами")
CODES_MENU_ORDER = ("Проверка кодов", "Отчёт о вводе в оборот")
PRINT_SELECTION_MODE_RANGE = "Диапазон"
PRINT_SELECTION_MODE_COUNT = "Количество"
BT_BATCH_SUBMITTED = "submitted"
BT_BATCH_AMBIGUOUS = "ambiguous"
BT_BATCH_FAILED = "failed"


def application_icon_path() -> str:
    bundle_root = getattr(
        sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))
    )
    return os.path.join(bundle_root, APP_ICON_RELATIVE_PATH)
DEFAULT_PRINT_SELECTION_MODE = PRINT_SELECTION_MODE_RANGE


def version_identity_text(version=None):
    return f"AutoPrint {version or APP_VERSION}"


def count_from_range(start, end):
    start = int(start)
    end = int(end)
    if start < 1:
        raise ValueError("Начальная строка должна быть не меньше 1.")
    if end < start:
        raise ValueError("Конечная строка не может быть меньше начальной.")
    return end - start + 1


def end_from_count(start, count):
    start = int(start)
    count = int(count)
    if start < 1:
        raise ValueError("Начальная строка должна быть не меньше 1.")
    if count <= 0:
        raise ValueError("Количество строк должно быть больше 0.")
    return start + count - 1


def resolve_print_selection(mode, start, second_value, total_rows=None):
    try:
        start = int(str(start).strip())
        second_value = int(str(second_value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Заполните параметры строк целыми числами.") from exc
    if mode == PRINT_SELECTION_MODE_RANGE:
        end = second_value
        count = count_from_range(start, end)
    elif mode == PRINT_SELECTION_MODE_COUNT:
        count = second_value
        end = end_from_count(start, count)
    else:
        raise ValueError(f"Неизвестный режим печати: {mode}")
    if total_rows is not None and end > int(total_rows):
        raise ValueError(
            f"Конечная строка {end} превышает количество строк CSV: {total_rows}."
        )
    return start, end, count


def convert_print_selection_value(start, value, from_mode, to_mode):
    start, end, count = resolve_print_selection(from_mode, start, value)
    if to_mode == PRINT_SELECTION_MODE_COUNT:
        return count
    if to_mode == PRINT_SELECTION_MODE_RANGE:
        return end
    raise ValueError(f"Неизвестный режим печати: {to_mode}")


def format_production_date(value):
    return value.strftime("%d.%m.%Y")


def production_date_from_text(value, today=None):
    today = today or dt.date.today()
    try:
        return dt.datetime.strptime(str(value or "").strip(), "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return today


def quick_production_date(day_offset, today=None):
    return (today or dt.date.today()) + dt.timedelta(days=int(day_offset))


def default_template_path(name: str) -> str:
    """Возвращает пользовательский, встроенный или legacy BTW в порядке приоритета."""
    candidates = [
        os.path.join(TEMPLATES_DIR, name),
        os.path.join(BUNDLED_TEMPLATES_DIR, name),
        os.path.join(BASE_DIR, "templates", name),
        os.path.join(BASE_DIR, name),
    ]
    seen = set()
    for p in candidates:
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(p):
            return p
    return os.path.join(BUNDLED_TEMPLATES_DIR, name)


def sticker_template_candidates(cfg=None):
    """Кандидаты для специального маркировочного BTW в порядке приоритета."""
    configured = ""
    if isinstance(cfg, dict):
        configured = str(cfg.get("sticker_btw") or "").strip()
    candidates = [
        configured,
        os.path.join(TEMPLATES_DIR, STICKER_BTW_NAME),
        os.path.join(BUNDLED_TEMPLATES_DIR, STICKER_BTW_NAME),
        os.path.join(BASE_DIR, STICKER_BTW_NAME),
    ]
    result = []
    seen = set()
    for path in candidates:
        if not path:
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def resolve_sticker_template_path(cfg=None):
    """Возвращает первый существующий маркировочный BTW или None."""
    for path in sticker_template_candidates(cfg):
        if os.path.isfile(path):
            return path
    return None


def sticker_template_display_path(cfg=None):
    """Путь, который показываем оператору, даже если файл пока отсутствует."""
    resolved = resolve_sticker_template_path(cfg)
    if resolved:
        return resolved
    if isinstance(cfg, dict):
        configured = str(cfg.get("sticker_btw") or "").strip()
        if configured:
            return configured
    return os.path.join(TEMPLATES_DIR, STICKER_BTW_NAME)



# ------------------------ Логгер ------------------------


class Logger:
    def __init__(self, tb, app_instance):
        self.tb = tb
        self.app = app_instance
        self.configure_tags()

    def configure_tags(self):
        """Настраивает цвета тегов в зависимости от темы."""
        mode = ctk.get_appearance_mode()
        is_dark = (mode == "Dark")
        
        colors = {
            "error": ("#FF5555" if is_dark else "#CC0000"),
            "success": ("#50FA7B" if is_dark else "#007F00"),
            "info": ("#FFB86C" if is_dark else "#FFA500"),
            "warn": ("#F1C40F" if is_dark else "#B58900"),
            "pack": ("#8BE9FD" if is_dark else "#007ACC"),
        }

        try:
            # В CTkTextbox нет tag_configure, только tag_config
            self.tb.tag_config("error", foreground=colors["error"])
            self.tb.tag_config("success", foreground=colors["success"])
            self.tb.tag_config("info", foreground=colors["info"])
            self.tb.tag_config("warn", foreground=colors["warn"])
            self.tb.tag_config("pack", foreground=colors["pack"])
            # гарантируем, что цветовой тег выше стандартного текста
            for _t in ("error", "success", "info", "warn", "pack"):
                try:
                    self.tb.tag_raise(_t)
                except Exception:
                    pass
        except Exception:
            # может падать при старте, если виджет еще не готов
            pass

    def log(self, msg, tags=None):
        ts = time.strftime("%H:%M:%S")
        resolved_tags = []
        if tags:
            if isinstance(tags, (list, tuple, set)):
                resolved_tags = list(tags)
            else:
                resolved_tags = [str(tags)]
        else:
            msg_lower = msg.lower()
            self._ensure_tags_ready()
            # Расширяем ключевые слова, чтобы ловить русские сообщения об ошибках
            error_markers = ("error", "err", "ошибка", "ошибк", "не удалось", "fail", "сбой", "пропущен")
            warn_markers = ("warn", "warning", "предуп", "вниман")
            if any(k in msg_lower for k in error_markers):
                resolved_tags.append("error")
            elif any(k in msg_lower for k in warn_markers):
                resolved_tags.append("warn")
            elif ("отправлен" in msg_lower or "готово" in msg_lower or "записан" in msg_lower
                  or "сохранено" in msg_lower or "запущен" in msg_lower or "завершено" in msg_lower
                  or "csv выбран" in msg_lower):
                resolved_tags.append("success")
            elif "[INFO]" in msg:
                resolved_tags.append("info")
            elif "[PACK]" in msg:
                resolved_tags.append("pack")

        self.tb.configure(state="normal")
        text = f"[{ts}] {msg}\n"
        try:
            # сразу вставляем с тегами, чтобы не зависеть от диапазонов
            self.tb.insert("end", text, tuple(resolved_tags))
        except Exception:
            self.tb.insert("end", text)

        try:
            follow = getattr(self.app, "log_follow", True)
        except Exception:
            follow = True
        if follow:
            self.tb.see("end")
        self.tb.update_idletasks()

    def err(self, msg):
        self.log(f"ERROR: {msg}")

    def _ensure_tags_ready(self):
        """Пере-настраивает цвета, если теги ещё не созданы (редкий случай)."""
        try:
            names = set(self.tb.tag_names())
            if not {"error", "success", "info", "warn", "pack"} <= names:
                self.configure_tags()
        except Exception:
            # если виджет ещё не готов - просто пропускаем
            pass


class SimpleTooltip:
    def __init__(self, widget, text, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._tip = None
        try:
            widget.bind("<Enter>", self._schedule)
            widget.bind("<Leave>", self._hide)
            widget.bind("<ButtonPress>", self._hide)
        except Exception:
            pass

    def _schedule(self, event=None):
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except Exception:
            self._after_id = None

    def _cancel(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 10
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return
        try:
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                tip,
                text=self.text,
                background="#222222",
                foreground="#ffffff",
                relief="solid",
                borderwidth=1,
                padx=6,
                pady=3,
                font=("Arial", 9),
            )
            label.pack()
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self, event=None):
        self._cancel()
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

class StatsManager:
    """Отвечает за сбор/хранение статистики печати."""
    def __init__(self, path, extra_paths=None, logger=None):
        self.path = path
        self.extra_paths = extra_paths or []
        self.logger = logger
        self.data = self._default_data()
        self.current_job_start = None
        self.current_job_name = ""
        self.load()

    def _default_data(self):
        return {
            "labels_total": 0,
            "labels_by_format": {"16x16": 0, "30x20": 0, "other": 0},
            "print_time_seconds": 0,
            "print_time_by_format": {"16x16": 0.0, "30x20": 0.0, "other": 0.0},
            "jobs_history": [],
        }

    def load(self):
        try:
            candidate_paths = [p for p in [self.path] if p] + [p for p in self.extra_paths if p]
            loaded = None
            for p in candidate_paths:
                if os.path.isfile(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            loaded = json.load(f)
                        self.path = p  # переключимся на существующий файл
                        break
                    except Exception:
                        continue
            if loaded is not None:
                # мягкий merge, чтобы не потерять новые поля
                self.data.update({k: loaded.get(k, v) for k, v in self._default_data().items()})
                # merge per-format словарей
                def _merge_dict(defv, loadedv):
                    out = dict(defv or {})
                    for k, v in (loadedv or {}).items():
                        out[k] = v
                    return out
                self.data["labels_by_format"] = _merge_dict(
                    self._default_data().get("labels_by_format"),
                    loaded.get("labels_by_format")
                )
                self.data["print_time_by_format"] = _merge_dict(
                    self._default_data().get("print_time_by_format"),
                    loaded.get("print_time_by_format")
                )
                self.data["jobs_history"] = loaded.get("jobs_history", []) or []
        except Exception:
            self.data = self._default_data()

    def _save_atomic(self):
        """Пытается сохранить в основной путь и копии. Если основной не доступен, пытается копии."""
        paths = [p for p in [self.path] if p] + [p for p in self.extra_paths if p]
        if not paths:
            return
        first_error = None
        wrote_any = False
        for idx, target in enumerate(paths):
            tmp = None
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
            except Exception:
                pass
            try:
                tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=os.path.dirname(target) or None)
                json.dump(self.data, tmp, ensure_ascii=False, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, target)
                wrote_any = True
            except Exception as e:
                first_error = first_error or e
                if tmp:
                    try:
                        tmp.close()
                        os.remove(tmp.name)
                    except Exception:
                        pass
            if self.logger:
                try:
                    self.logger.err(f"Статистика: не удалось записать {target}: {e}")
                except Exception:
                    pass
        if (not wrote_any) and first_error and self.logger:
            try:
                self.logger.err(f"Статистика: не удалось сохранить ни в один путь ({paths}): {first_error}")
            except Exception:
                pass

    def reset_all(self):
        """Полное обнуление статистики."""
        self.data = self._default_data()
        self._save_atomic()

    def save(self):
        """Публичный синоним для сохранения на диск."""
        self._save_atomic()

    def start_print_job(self, job_name=""):
        self.current_job_start = time.time()
        self.current_job_name = job_name or ""
        return self.current_job_start

    def finish_print_job(self, printed_count=0, duration_sec=None, success=True, fmt=None, printer=None):
        try:
            if duration_sec is None:
                duration_sec = 0.0 if not self.current_job_start else max(0.0, time.time() - self.current_job_start)
        finally:
            self.current_job_start = None
        fmt_key = (fmt or "").strip() or "other"
        if success and printed_count > 0:
            self.data["labels_total"] = int(self.data.get("labels_total", 0)) + int(printed_count)
            self.data["print_time_seconds"] = float(self.data.get("print_time_seconds", 0.0)) + float(duration_sec or 0.0)
            # по форматам
            try:
                lb = dict(self.data.get("labels_by_format") or {})
                lb[fmt_key] = int(lb.get(fmt_key, 0)) + int(printed_count)
                self.data["labels_by_format"] = lb
            except Exception:
                pass
            try:
                pt = dict(self.data.get("print_time_by_format") or {})
                pt[fmt_key] = float(pt.get(fmt_key, 0.0)) + float(duration_sec or 0.0)
                self.data["print_time_by_format"] = pt
            except Exception:
                pass
        elif success and duration_sec:
            # если печать не удалась, но время прошло - не учитываем, чтобы не раздувать статистику
            pass

        # логируем задание вне зависимости от success, но labels=0 если не было отправок
        try:
            try:
                user = os.getlogin()
            except Exception:
                try:
                    user = getpass.getuser()
                except Exception:
                    user = "-"
            job_entry = {
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
                "labels": int(printed_count or 0),
                "duration_sec": float(duration_sec or 0.0),
                "job_name": self.current_job_name or "",
                "format": fmt or "",
                "printer": printer or "",
                "success": bool(success),
                "user": user,
            }
            jobs = list(self.data.get("jobs_history") or [])
            jobs.append(job_entry)
            jobs = jobs[-200:]  # храним до 200 записей
            self.data["jobs_history"] = jobs
        except Exception:
            pass

        self._save_atomic()
        return None

class App(ctk.CTk):


    # ---------- helper: normalized checkbox value ----------
    def _dialog_flag(self) -> bool:
        """Читает чекбокс «Показывать диалог BarTender» как bool надёжно."""
        try:
            v = self.show_dialog_var.get()
        except Exception:
            try:
                v = self.default_show_dialog
            except Exception:
                return False
        try:
            if isinstance(v, str):
                s = v.strip().lower()
                if s.isdigit():
                    return bool(int(s))
                return s in ("true","yes","on","1")
            return bool(v)
        except Exception:
            return False

    def _bool_var(self, var, default=False):
        """Надёжно читает Tkinter BooleanVar/строку/число как bool (строка '0' -> False)."""
        try:
            v = var.get() if hasattr(var, "get") else var
        except Exception:
            v = var
        try:
            if isinstance(v, str):
                s = v.strip().lower()
                if s.isdigit():
                    return bool(int(s))
                return s in ("true", "yes", "on", "1")
            if isinstance(v, (int, float, bool)):
                return bool(v)
        except Exception:
            pass
        return bool(default)

    def _apply_log_selection_theme(self):
        """Настраивает цвета выделения в логе, чтобы текст не исчезал."""
        try:
            mode = ctk.get_appearance_mode()
            is_dark = (mode == "Dark")
        except Exception:
            is_dark = True
        sel_bg = "#3b82f6" if is_dark else "#2b6cb0"
        sel_fg = "#ffffff" if is_dark else "#ffffff"
        ins_fg = "#ffffff" if is_dark else "#000000"
        targets = []
        try:
            targets.append(self.logbox)
        except Exception:
            pass
        try:
            tb = getattr(self.logbox, "_textbox", None)
            if tb:
                targets.append(tb)
        except Exception:
            pass
        for t in targets:
            try:
                t.configure(
                    selectbackground=sel_bg,
                    selectforeground=sel_fg,
                    inactiveselectbackground=sel_bg,
                    insertbackground=ins_fg
                )
            except Exception:
                pass

    def _can_write_file(self, path):
        """Проверяет возможность записи в каталог/файл."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write("")
            return True
        except Exception:
            return False

    def _resolve_stats_path(self):
        """Определяет путь stats.json и путь-дубликат, выбирая существующий/последний."""
        primary = os.path.abspath(STATS_PATH)

        def _mtime(p):
            try:
                return os.path.getmtime(p)
            except Exception:
                return -1

        # если оба существуют - берём самый свежий
        candidates = []
        if os.path.isfile(primary):
            candidates.append((primary, _mtime(primary)))
        chosen = None
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            chosen = candidates[0][0]
        # если ни одного файла нет - выбираем первый доступный для записи
        if not chosen:
            if self._can_write_file(primary):
                chosen = primary
            else:
                chosen = primary
        # определяем дубликат (если другой путь доступен)
        mirror = None
        return chosen, mirror

    def _update_log_follow(self):
        """Обновляет флаг автопрокрутки: если внизу - включён, иначе выключаем."""
        try:
            top, bottom = self.logbox.yview()
            self.log_follow = bottom > 0.98
        except Exception:
            self.log_follow = True

    def _bind_log_scroll_events(self):
        """Отслеживает скролл логов, чтобы не прыгать вверх при ручном просмотре."""
        def _schedule_update(event=None):
            try:
                self.after(50, self._update_log_follow)
            except Exception:
                pass
        for ev in ("<MouseWheel>", "<ButtonRelease-1>", "<ButtonRelease-2>", "<ButtonRelease-3>"):
            try:
                self.logbox.bind(ev, _schedule_update)
            except Exception:
                pass
        try:
            self.logbox.bind("<<ScrollbarMoved>>", _schedule_update)
        except Exception:
            pass

    def _write_tmp_batch_csv(self, rows_enriched, path=None, add_calib=True):
        import os, csv
        cols = getattr(self, "REQ_COLS", ["ShortName","ShortGTIN","EXP_DATE","PROD_DATE","PART_NUM","DM","NUM"])
        if path is None:
            path = os.path.join(BASE_DIR, "tmp_batch.csv")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        # Сформируем список строк: при калибровке добавим 6 тестовых в начало
        rows2 = list(rows_enriched)
        try:
            if add_calib and self._bool_var(getattr(self, "calib_var", False)):
                dummy = {k: ("1" if k.upper()=="NUM" else ("000" if k=="ShortGTIN" else "X")) for k in cols}
                rows2 = [dummy.copy() for _ in range(6)] + rows2
        except Exception:
            pass
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols, delimiter="\t")
                w.writeheader()
                for enr in rows2:
                    row = {k: (enr.get(k, "") or "") for k in cols}
                    w.writerow(row)
            if getattr(self, "logger", None):
                try:
                    self.logger.log(f"tmp_batch.csv записан: {path} (строк={len(rows2)})")
                except Exception:
                    pass
            # Дублируем в легаси-директорию (например, D:\AutoPrint V3), если она существует,
            # чтобы шаблон, привязанный к старому пути, тоже получил актуальные данные.
            try:
                legacy_dir = self.cfg.get("legacy_tmp_dir") if isinstance(getattr(self, "cfg", None), dict) else None
            except Exception:
                legacy_dir = None
            if not legacy_dir:
                try:
                    legacy_dir = os.path.abspath(os.path.join(BASE_DIR, "..", "AutoPrint V3"))
                except Exception:
                    legacy_dir = None
            try:
                if legacy_dir and os.path.isdir(legacy_dir):
                    legacy_path = os.path.abspath(os.path.join(legacy_dir, "tmp_batch.csv"))
                    if os.path.normcase(legacy_path) != os.path.normcase(os.path.abspath(path)):
                        import shutil
                        shutil.copyfile(path, legacy_path)
                        if getattr(self, "logger", None):
                            try:
                                self.logger.log(f"tmp_batch.csv продублирован: {legacy_path}")
                            except Exception:
                                pass
            except Exception:
                pass
        except Exception as e:
            try:
                if getattr(self, "logger", None):
                    self.logger.err(f"Не удалось записать tmp_batch.csv: {e}")
            except Exception:
                pass
        return path


    def _rangecsv_repoint_db(self, *args, **kwargs):
        try: self.logger.log("[TMPBATCH] DB rebind отключен (stub)")
        except Exception: pass
        return False

    def __init__(self):
        self._shutdown_started = False
        self.cancel_requested = False
        self.is_paused = False
        super().__init__()
        # CustomTkinter installs a global left-click focus hook for every CTk
        # window. Native Tk widgets already handle click focus, and retaining
        # this hook can redirect focus to CTk implementation canvases.
        self.unbind_all("<Button-1>")
        try:
            self.iconbitmap(default=application_icon_path())
        except (OSError, tk.TclError):
            pass
        self.REQ_COLS = list(DEFAULT_REQ_COLS)
        self.title(APP_TITLE)
        ctk.set_appearance_mode(DEFAULT_APPEARANCE_MODE)
        ctk.set_default_color_theme("blue")
        self.scale_var = tk.StringVar(master=self, value="auto")
        self.show_preview_var = tk.BooleanVar(master=self, value=True)
        self.show_log_var = tk.BooleanVar(master=self, value=True)
        self._apply_auto_scaling()

        self._template_migration_events = []
        if getattr(sys, "frozen", False):
            try:
                self._template_migration_events = migrate_legacy_templates(
                    destination_dir=TEMPLATES_DIR,
                    base_dir=BASE_DIR,
                )
            except Exception as exc:
                self._template_migration_events = [
                    {
                        "status": "error",
                        "source": "",
                        "target": TEMPLATES_DIR,
                        "error": str(exc),
                    }
                ]

        self.cfg = load_config()
        self.label_templates = self._load_label_templates_from_cfg()
        # статистика печати отключена
        self.stats = None
        self.app_started_at = time.time()
        # Параметры по умолчанию
        # batch_size: 0 = пакетирование выключено по умолчанию
        self.default_batch_size = 0
        self.default_show_dialog = bool(self.cfg.get('show_print_dialog', False))
        self.csv_dir = self.cfg.get("csv_dir") or BASE_DIR
        self.csv_stats = self._normalize_csv_stats(self.cfg.get("csv_print_stats", {}))
        self._part_missing_logged = False
        self._stickers_preprinted = False
        self.last_batches = []
        self.last_rows_all = []
        self.last_idx0 = 0
        self.last_sticker_jobs = []
        atexit.register(lambda: save_config(self.cfg))

        self.bt = None
        self.catalog_store = None
        self.catalog_state = "error"
        self.catalog_error = "Каталог ещё не инициализирован."
        self.catalog_revision = None
        self._catalog_print_active = 0
        self.is_paused = False
        self.csv_path = ""
        self.csv_rows = []
        self.preview_ctkimg = None
        self._preview_mode = "label"
        self._live_preview_after_id = None
        self._live_preview_busy = False
        self._live_preview_requested = False
        self._live_preview_generation = 0
        self._preview_temp_dir = None
        self.product_map = {}

        self._build_ui()
        self._log_template_migration()
        self._install_global_text_editing()
        self._build_menu()
        self._initialize_catalog_runtime()
        self._start_bt()
        self.protocol("WM_DELETE_WINDOW", self._shutdown_app)
        self._refresh_printers()
        self._load_presets()
        # инициализируем список клиентов в выпадающем списке (по умолчанию "-")
        try:
            self._part_missing_logged = False
            self.client_entry.delete(0, "end")
        except Exception:
            pass
        self._refresh_client_combo(None)
        try:
            self.logger.log(f"[INFO] Загружено статусов CSV: {len(self.csv_stats)}")
        except Exception:
            pass


    # === PATCH HELPERS (batch printing) ===
    def _dbg(self, s): 
        try:
            self.logger.log(s)
        except Exception:
            print(s)

    def _get_pack_size(self):
        """Возвращает размер пакета из GUI (поле рядом с 'Пакет').
        Пишем диагностику значения и fallback к 0."""
        gui_val_raw = None
        gui_val_int = 0
        try:
            if hasattr(self, "batch_entry") and self.batch_entry:
                gui_val_raw = self.batch_entry.get()
                gui_val_int = int(str(gui_val_raw).strip() or "0")
        except Exception as e:
            self._dbg(f"[DEBUG] PACK GUI parse failed: raw={gui_val_raw!r} err={e}")
            gui_val_int = 0
        try:
            self._dbg(f"[DEBUG] PACK from GUI: raw={gui_val_raw!r} -> int={gui_val_int}")
        except Exception:
            pass
        return max(0, gui_val_int)
    # === END PATCH HELPERS ===


    # индикатор прогресса
    
    def _set_progress(self, cur: int, total: int, phase: str = ""):
        # Прогресс-бар убран — оставляем заглушку
        return

    def _build_legacy_ui(self):
        # верхняя панель: выбор формата/шаблонов/CSV
        top = ctk.CTkFrame(self, corner_radius=12)
        top.pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(top, text="Шаблон (Auto/ручной):").pack(side="left", padx=(8, 4), pady=10)
        self.format_combo = ctk.CTkComboBox(top, values=["Auto"] + self._manual_template_choices(), state="readonly", width=120)
        self.format_combo.set("Auto")
        self.format_combo.pack(side="left", padx=(0, 10), pady=10)
        self.format_combo.configure(command=self._on_format_change)

        self.btw16_entry = ctk.CTkEntry(top, placeholder_text="BTW для 16x16")
#         self.btw16_entry.pack(side="left", fill="x", expand=True, padx=(4, 4), pady=10)
#         ctk.CTkButton(top, text="16x16…", command=lambda: self._choose_btw("16x16")).pack(side="left", padx=(0, 8), pady=10)

        self.btw30_entry = ctk.CTkEntry(top, placeholder_text="BTW для 30x20")
#         self.btw30_entry.pack(side="left", fill="x", expand=True, padx=(4, 4), pady=10)
#         ctk.CTkButton(top, text="30x20…", command=lambda: self._choose_btw("30x20")).pack(side="left", padx=(0, 8), pady=10)

        # Принтер компактно между форматом и CSV
        prn_top = ctk.CTkFrame(top)
        prn_top.pack(side="left", padx=(8, 8), pady=10)
        ctk.CTkLabel(prn_top, text="Принтер:").pack(side="left", padx=(0, 6))
        self.prn_combo = ctk.CTkComboBox(prn_top, values=["(нет принтеров)"], state="readonly", width=240)
        self.prn_combo.pack(side="left")

        self.csv_label = ctk.CTkLabel(top, text="")
        self.csv_label.pack(side="left", padx=(8, 4), pady=10)

        self.theme_switch = ctk.CTkSwitch(top, text="Ночная тема", command=self._toggle_theme)
        self.theme_switch.pack(side="right", padx=(20, 10), pady=10)
        self.theme_switch.select()

        # Вверх: блок параметров (слева) + CSV менеджер (справа), 50/50
        top_split = ctk.CTkFrame(self, corner_radius=12)
        top_split.pack(fill="x", padx=12, pady=(0, 6))

        # Блок параметров (левая половина)
        params_block = ctk.CTkFrame(top_split, corner_radius=12)
        params_block.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8, anchor="nw")

        mid = ctk.CTkFrame(params_block, corner_radius=12)
        mid.pack(anchor="nw", padx=12, pady=12)
        # таблица 2 колонки: поля слева, чекбоксы справа
        grid = ctk.CTkFrame(mid)
        grid.pack()
        ctk.CTkLabel(grid, text="Строка №:", width=90, anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=(0, 4))
        self.index_entry = ctk.CTkEntry(grid, width=80)
        self.index_entry.insert(0, "1")
        self.index_entry.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(0, 4))

        ctk.CTkLabel(grid, text="Лимит:", width=90, anchor="w").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(0, 4))
        self.limit_entry = ctk.CTkEntry(grid, width=80)
        self.limit_entry.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 4))

        # чекбоксы выровнены по верхнему краю полей
        chk_col = ctk.CTkFrame(grid)
        chk_col.grid(row=0, column=2, rowspan=2, sticky="nw", padx=(12, 0), pady=(0, 0))
        self.show_dialog_var = ctk.BooleanVar(value=self.default_show_dialog)
        self.show_dialog_chk = ctk.CTkCheckBox(chk_col, text="Показывать диалог печати BarTender", variable=self.show_dialog_var)
        self.show_dialog_chk.pack(anchor="w", pady=(0, 4))

        self.calib_var = ctk.BooleanVar(value=False)
        self.calib_chk = ctk.CTkCheckBox(chk_col, text="Калибровка TSC: 6 этикеток 'X' перед печатью", variable=self.calib_var)
        self.calib_chk.pack(anchor="w", pady=(0, 4))

        self.skip_sticker_var = ctk.BooleanVar(value=False)
        self.skip_sticker_chk = ctk.CTkCheckBox(chk_col, text="Не печатать маркировочный стикер", variable=self.skip_sticker_var)
        self.skip_sticker_chk.pack(anchor="w", pady=(0, 4))

        # CSV менеджер: папка + список файлов (правая половина)
        csv_block = ctk.CTkFrame(top_split, corner_radius=12)
        csv_block.pack(side="left", fill="both", expand=True, padx=(0, 0), pady=8)
        csv_head = ctk.CTkFrame(csv_block)
        csv_head.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(csv_head, text="Папка CSV:").pack(side="left", padx=(0, 6))
        self.csv_dir_label = ctk.CTkLabel(csv_head, text=self._shorten_path(self.csv_dir), anchor="w")
        self.csv_dir_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(csv_head, text="Вверх", width=80, command=self._go_up_dir).pack(side="left", padx=(6, 4))
        ctk.CTkButton(csv_head, text="Сменить папку", width=110, command=self._choose_csv_dir).pack(side="left", padx=(4, 4))
        ctk.CTkButton(csv_head, text="Обновить", width=90, command=self._render_csv_list).pack(side="left", padx=(4, 0))
        brand_row = ctk.CTkFrame(csv_block, fg_color="transparent")
        brand_row.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkLabel(brand_row, text="Бренд (по GTIN):", font=("Arial", 12, "bold")).pack(side="left")
        self.brand_value_label = ctk.CTkLabel(brand_row, text="-", anchor="w", font=("Arial", 12, "bold"))
        self.brand_value_label.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.csv_list_frame = ctk.CTkScrollableFrame(csv_block, height=200)
        self.csv_list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # блок обогащения: даты + партия
        enr = ctk.CTkFrame(self, corner_radius=12)
        enr.pack(fill="x", padx=12, pady=6)

        # колонки с подписями сверху
        prod_col = ctk.CTkFrame(enr)
        prod_col.pack(side="left", padx=(12, 12), pady=10, fill="y")
        ctk.CTkLabel(prod_col, text="Дата произв. (ДД.ММ.ГГГГ):").pack(anchor="w", pady=(0, 2))
        prod_row = ctk.CTkFrame(prod_col)
        prod_row.pack(fill="x")
        self.prod_date_entry = ctk.CTkEntry(prod_row, width=140)
        self.prod_date_entry.insert(0, dt.date.today().strftime("%d.%m.%Y"))
        self.prod_date_entry.pack(side="left", padx=(0, 6))
        try:
            self.prod_date_border_default = self.prod_date_entry.cget("border_color")
        except Exception:
            self.prod_date_border_default = None
        ctk.CTkButton(prod_row, text="📅", width=36, command=self._open_date_picker).pack(side="left")
        vcmd = (self.register(lambda s: re.fullmatch(r"[0-9.]*", s or "") is not None), "%P")
        self.prod_date_entry.configure(validate="key", validatecommand=vcmd)
        self.prod_date_entry.bind("<KeyRelease>", self._format_prod_date)

        self.exp_days_entry = ctk.CTkEntry(enr, width=120)
#         self.exp_days_entry.pack(side="left", padx=(0, 12), pady=10)

        part_col = ctk.CTkFrame(enr)
        part_col.pack(side="left", padx=(0, 12), pady=10, fill="y")
        ctk.CTkLabel(part_col, text="Номер партии:").pack(anchor="w", pady=(0, 2))
        self.part_entry = ctk.CTkEntry(part_col, width=160, placeholder_text="Номер партии вручную")
        self.part_entry.pack(anchor="w")
        vcmd_part = (self.register(lambda s: re.fullmatch(r"[0-9]*", s or "") is not None), "%P")
        self.part_entry.configure(validate="key", validatecommand=vcmd_part)
        try:
            self.part_entry_border_default = self.part_entry.cget("border_color")
        except Exception:
            self.part_entry_border_default = None
        self.part_entry.bind("<KeyRelease>", lambda e: self._mark_part_entry(False))

        # Клиент для стикера (задаётся вручную, хранится в конфиге)
        client_col = ctk.CTkFrame(enr)
        client_col.pack(side="left", padx=(0, 12), pady=10, fill="y")
        ctk.CTkLabel(client_col, text="Клиент (стикер):").pack(anchor="w", pady=(0, 2))
        client_row = ctk.CTkFrame(client_col)
        client_row.pack(fill="x", pady=(0, 4))
        self.client_entry = ctk.CTkEntry(client_row, width=180, placeholder_text="Название клиента")
        self.client_entry.pack(side="left", padx=(0, 6))
        self.client_combo = ctk.CTkComboBox(client_row, values=[], state="readonly", width=180,
                                            command=lambda v: self._on_client_select(v))
        self.client_combo.pack(side="left")
        ctk.CTkButton(client_row, text="Удалить клиента", width=110, command=self._client_delete).pack(side="left", padx=(6, 0))
        btn_row = ctk.CTkFrame(client_col)
        btn_row.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(btn_row, text="Добавить", width=80, command=self._client_add).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Очистить", width=80, command=self._client_clear).pack(side="left")
        sticker_row = ctk.CTkFrame(client_col)
        sticker_row.pack(fill="x", pady=(6, 0))
        self.sticker_reprint_btn = ctk.CTkButton(
            sticker_row,
            text="Перепечатать стикер",
            width=180,
            command=self._open_sticker_reprint_dialog
        )
        self.sticker_reprint_btn.pack(side="left")

        # кнопки действий
        btns = ctk.CTkFrame(self)
        btns.pack(fill="x", padx=8, pady=(4, 8))
        self.preview_btn = ctk.CTkButton(
            btns, text="Предосмотр печати", command=self._preview, height=36
        )
        self.preview_btn.pack(side="left", padx=6, pady=8)
        self.print_btn = ctk.CTkButton(
            btns, text="Печать", command=self._print_range_one_job_via_csv, height=36,
            fg_color="#198754", hover_color="#157347"
        )
        self.print_btn.pack(side="left", padx=6, pady=8)

        self.cancel_btn = ctk.CTkButton(
            btns, text="Отмена печати", command=self._cancel_print, height=36,
            fg_color="#dc3545", hover_color="#b02a37"
        )
        self.cancel_btn.pack(side="left", padx=6, pady=8)

        self.bt_status_label = ctk.CTkLabel(btns, text="BarTender: проверка…")
        self.bt_status_label.pack(side="right", padx=12, pady=8)
        self.catalog_status_label = ctk.CTkLabel(btns, text="Каталог: проверка…")
        self.catalog_status_label.pack(side="right", padx=12, pady=8)

        # панель пакетной печати
        batch_panel = ctk.CTkFrame(self, corner_radius=8)
        batch_panel.pack(fill="x", padx=12, pady=(0, 8))
        try:
            batch_panel.configure(height=100)
            batch_panel.pack_propagate(False)
        except Exception:
            pass
        batch_header = ctk.CTkFrame(batch_panel, fg_color="transparent")
        batch_header.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(batch_header, text="Пакетная печать:").pack(side="left", padx=(0, 8))
        ctk.CTkLabel(batch_header, text="Пакет по (шт.):").pack(side="left", padx=(0, 6))
        self.batch_entry = ctk.CTkEntry(batch_header, width=100)
        self.batch_entry.insert(0, "0")
        self.batch_entry.pack(side="left")
        self.batch_btns_frame = ctk.CTkFrame(batch_panel)
        self.batch_btns_frame.pack(fill="x", padx=12, pady=(0, 8))

        # низ: превью + лог
        self.bottom_panel = ctk.CTkFrame(self, corner_radius=12)
        self.bottom_panel.pack(fill="both", expand=True, padx=12, pady=(6, 12))

        self.preview_label = ctk.CTkLabel(self.bottom_panel, text="Предпросмотр будет здесь", width=560, height=360, corner_radius=8)
        self.preview_label.pack(side="left", fill="both", expand=True, padx=12, pady=12)
        self.preview_current_path = None
        self._preview_resize_busy = False
        self.preview_label.bind("<Configure>", self._on_preview_resize)

        self.log_frame = ctk.CTkFrame(self.bottom_panel, corner_radius=12)
        self.log_frame.pack(side="left", fill="both", expand=True, padx=(0, 12), pady=12)

        self.logbox = ctk.CTkTextbox(self.log_frame, height=360)
        self.logbox.pack(fill="both", expand=True, padx=12, pady=12)
        try:
            self._apply_log_selection_theme()
        except Exception:
            pass
        self.log_follow = True  # автопрокрутка, если пользователь не листает
        self._bind_log_scroll_events()
        self.logger = Logger(self.logbox, self)
        self._enable_log_copy()
        try:
            self._render_csv_list()
        except Exception:
            pass

        # применим правила формата при старте (Auto не трогаем)
        try:
            self._apply_format_ui(self.format_combo.get())
        except Exception:
            pass
        self._refresh_bottom_layout()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)

        self.menu_bar_frame = ctk.CTkFrame(
            self, height=36, corner_radius=0, fg_color=("#f4f6f8", "#1f1f1f")
        )
        self.menu_bar_frame.grid(row=0, column=0, sticky="ew")
        self.menu_bar_frame.grid_propagate(False)
        self.theme_switch = ctk.CTkSwitch(
            self.menu_bar_frame, text="Тёмная тема", command=self._toggle_theme
        )
        if DEFAULT_APPEARANCE_MODE == "dark":
            self.theme_switch.select()

        self.main_layout_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_layout_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        self.main_layout_frame.grid_columnconfigure(0, weight=0, minsize=350)
        self.main_layout_frame.grid_columnconfigure(1, weight=1, minsize=420)
        self.main_layout_frame.grid_columnconfigure(2, weight=0, minsize=350)
        self.main_layout_frame.grid_rowconfigure(0, weight=1)

        self.left_column_frame = ctk.CTkFrame(self.main_layout_frame, fg_color="transparent")
        self.left_column_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.left_column_frame.grid_columnconfigure(0, weight=1)
        self.left_column_frame.grid_rowconfigure(0, weight=1)

        self.csv_files_section = self._make_main_section(
            self.left_column_frame,
            "CSV-файлы",
        )
        self.csv_files_section.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        csv_files_body = self.csv_files_section.body
        csv_files_body.grid_columnconfigure(0, weight=1)
        csv_files_body.grid_rowconfigure(2, weight=1)
        self.csv_dir_label = ctk.CTkLabel(
            csv_files_body,
            text=self._responsive_csv_dir_text(self.csv_dir),
            anchor="w",
            width=1,
        )
        self.csv_dir_label.grid(
            row=0, column=0, sticky="ew", padx=10, pady=(5, 5)
        )
        self.csv_dir_tooltip = SimpleTooltip(self.csv_dir_label, self.csv_dir)
        csv_files_body.bind("<Configure>", self._schedule_path_label_refresh, add="+")
        csv_file_buttons = ctk.CTkFrame(csv_files_body, fg_color="transparent")
        csv_file_buttons.grid(
            row=1, column=0, sticky="ew", padx=8, pady=(0, 6)
        )
        csv_file_buttons.grid_columnconfigure(0, weight=1)
        csv_file_buttons.grid_columnconfigure(1, weight=1)
        csv_file_buttons.grid_columnconfigure(2, weight=1)
        ctk.CTkButton(
            csv_file_buttons, text="Сменить папку",
            command=self._choose_csv_dir,
        ).grid(row=0, column=0, sticky="ew", padx=2)
        ctk.CTkButton(
            csv_file_buttons, text="Вверх", command=self._go_up_dir
        ).grid(row=0, column=1, sticky="ew", padx=2)
        ctk.CTkButton(
            csv_file_buttons, text="Обновить",
            command=self._render_csv_list,
        ).grid(row=0, column=2, sticky="ew", padx=2)
        self.csv_list_frame = ctk.CTkScrollableFrame(csv_files_body)
        self.csv_list_frame.grid(
            row=2, column=0, sticky="nsew", padx=8, pady=(0, 8)
        )
        try:
            self.csv_list_frame._parent_canvas.bind(
                "<Configure>", self._schedule_csv_scrollbar_refresh, add="+"
            )
        except Exception:
            pass

        self.params_section = self._make_main_section(
            self.left_column_frame,
            "Параметры партии",
        )
        self.params_section.grid(row=1, column=0, sticky="ew", pady=7)
        params = self.params_section.body
        params.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(params, text="Дата производства:", anchor="w").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        date_row = ctk.CTkFrame(params, fg_color="transparent")
        date_row.grid(row=0, column=1, sticky="ew", padx=10, pady=5)
        self.prod_date_entry = ctk.CTkEntry(date_row, width=150)
        self.prod_date_entry.insert(0, dt.date.today().strftime("%d.%m.%Y"))
        self.prod_date_entry.pack(side="left", fill="x", expand=True)
        self.prod_date_button = ctk.CTkButton(
            date_row, text="📅", width=36, command=self._open_date_picker
        )
        self.prod_date_button.pack(side="left", padx=(5, 0))
        try:
            self.prod_date_border_default = self.prod_date_entry.cget("border_color")
        except Exception:
            self.prod_date_border_default = None
        vcmd = (self.register(lambda s: re.fullmatch(r"[0-9.]*", s or "") is not None), "%P")
        self.prod_date_entry.configure(validate="key", validatecommand=vcmd)
        self.prod_date_entry.bind("<KeyRelease>", self._format_prod_date)
        self.prod_date_entry.bind("<KeyRelease>", self._schedule_live_preview, add="+")
        ctk.CTkLabel(params, text="Номер партии:", anchor="w").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        self.part_entry = ctk.CTkEntry(params, placeholder_text="Номер партии вручную")
        self.part_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=5)
        vcmd_part = (self.register(lambda s: re.fullmatch(r"[0-9]*", s or "") is not None), "%P")
        self.part_entry.configure(validate="key", validatecommand=vcmd_part)
        self.part_entry.bind("<KeyRelease>", lambda _e: self._mark_part_entry(False))
        self.part_entry.bind("<KeyRelease>", self._schedule_live_preview, add="+")
        try:
            self.part_entry_border_default = self.part_entry.cget("border_color")
        except Exception:
            self.part_entry_border_default = None
        ctk.CTkLabel(params, text="Клиент (стикер):", anchor="w").grid(row=2, column=0, sticky="w", padx=10, pady=5)
        client_box = ctk.CTkFrame(params, fg_color="transparent")
        client_box.grid(row=2, column=1, sticky="ew", padx=10, pady=5)
        self.client_entry = ctk.CTkEntry(client_box, placeholder_text="Название клиента")
        self.client_entry.pack(fill="x", expand=True)
        self.client_entry.bind("<KeyRelease>", self._schedule_live_preview)
        self.client_combo = ctk.CTkComboBox(
            client_box, values=[], state="readonly", command=lambda value: self._on_client_select(value)
        )
        self.client_combo.pack(fill="x", expand=True, pady=(5, 0))
        client_actions = ctk.CTkFrame(params, fg_color="transparent")
        client_actions.grid(row=3, column=1, sticky="w", padx=8, pady=(0, 5))
        ctk.CTkButton(client_actions, text="Добавить", width=75, command=self._client_add).pack(side="left", padx=2)
        ctk.CTkButton(client_actions, text="Очистить", width=75, command=self._client_clear).pack(side="left", padx=2)
        ctk.CTkButton(client_actions, text="Удалить", width=75, command=self._client_delete).pack(side="left", padx=2)
        self.show_dialog_var = ctk.BooleanVar(value=self.default_show_dialog)
        self.show_dialog_chk = ctk.CTkCheckBox(params, text="Показывать диалог печати BarTender", variable=self.show_dialog_var)
        self.show_dialog_chk.grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=4)
        self.calib_var = ctk.BooleanVar(value=False)
        self.calib_chk = ctk.CTkCheckBox(params, text="Калибровка TSC: 6 этикеток 'X'", variable=self.calib_var)
        self.calib_chk.grid(row=5, column=0, columnspan=2, sticky="w", padx=10, pady=4)
        self.skip_sticker_var = ctk.BooleanVar(value=False)
        self.skip_sticker_chk = ctk.CTkCheckBox(params, text="Не печатать маркировочный стикер", variable=self.skip_sticker_var)
        self.skip_sticker_chk.grid(row=6, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 10))
        self.exp_days_entry = ctk.CTkEntry(params)

        self.print_section = self._make_main_section(
            self.left_column_frame,
            "Печать",
        )
        self.print_section.grid(row=2, column=0, sticky="nsew", pady=(7, 0))
        print_body = self.print_section.body

        mode_row = ctk.CTkFrame(print_body, fg_color="transparent")
        mode_row.pack(fill="x", padx=8, pady=(6, 4))
        ctk.CTkLabel(mode_row, text="Режим печати:").pack(side="left", padx=(2, 6))
        self.print_selection_mode_var = tk.StringVar(
            master=self, value=DEFAULT_PRINT_SELECTION_MODE
        )
        self._print_selection_mode = DEFAULT_PRINT_SELECTION_MODE
        self.print_selection_mode_control = ctk.CTkSegmentedButton(
            mode_row,
            values=[PRINT_SELECTION_MODE_RANGE, PRINT_SELECTION_MODE_COUNT],
            variable=self.print_selection_mode_var,
            command=self._on_print_selection_mode_change,
        )
        self.print_selection_mode_control.pack(side="left", fill="x", expand=True)

        fields_row = ctk.CTkFrame(print_body, fg_color="transparent")
        fields_row.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkLabel(fields_row, text="С строки:").pack(side="left", padx=(2, 4))
        self.index_entry = ctk.CTkEntry(fields_row, width=64)
        self.index_entry.insert(0, "1")
        self.index_entry.pack(side="left", padx=(0, 8))
        self.print_selection_second_label = ctk.CTkLabel(fields_row, text="По строку:")
        self.print_selection_second_label.pack(side="left", padx=(0, 4))
        self.limit_entry = ctk.CTkEntry(fields_row, width=64)
        self.limit_entry.pack(side="left")
        self.index_entry.bind("<KeyRelease>", self._update_print_selection_summary)
        self.index_entry.bind("<KeyRelease>", self._schedule_live_preview, add="+")
        self.limit_entry.bind("<KeyRelease>", self._update_print_selection_summary)
        self.limit_entry.bind("<KeyRelease>", self._schedule_live_preview, add="+")
        try:
            self._selection_entry_border_defaults = (
                self.index_entry.cget("border_color"),
                self.limit_entry.cget("border_color"),
            )
        except Exception:
            self._selection_entry_border_defaults = (None, None)

        batch_row = ctk.CTkFrame(print_body, fg_color="transparent")
        batch_row.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkLabel(batch_row, text="Пакет по (шт.):").pack(side="left", padx=(2, 6))
        self.batch_entry = ctk.CTkEntry(batch_row, width=90)
        self.batch_entry.insert(0, "0")
        self.batch_entry.pack(side="left")
        self.batch_entry.bind("<KeyRelease>", self._schedule_live_preview)

        self.print_selection_summary_label = ctk.CTkLabel(
            print_body, text="—", anchor="w", text_color=("#65717c", "#aeb6bf")
        )
        self.print_selection_summary_label.pack(fill="x", padx=10, pady=(0, 5))

        actions = ctk.CTkFrame(print_body, fg_color="transparent")
        actions.pack(fill="x", padx=8, pady=(0, 5))
        self.print_btn = ctk.CTkButton(
            actions, text="Печать", command=self._print_range_one_job_via_csv,
            height=38, fg_color="#198754", hover_color="#157347"
        )
        self.print_btn.pack(side="left", fill="x", expand=True, padx=(2, 3))
        self.cancel_btn = ctk.CTkButton(
            actions,
            text="Отмена печати",
            command=self._cancel_print,
            height=38,
            state="disabled",
            fg_color=("#a9afb7", "#454a50"),
            hover_color=("#8f969f", "#383c41"),
        )
        self.cancel_btn.pack(side="left", fill="x", expand=True, padx=(3, 2))

        secondary_actions = ctk.CTkFrame(print_body, fg_color="transparent")
        secondary_actions.pack(fill="x", padx=8, pady=(0, 6))
        self.sticker_reprint_btn = ctk.CTkButton(
            secondary_actions,
            text="Перепечатать стикер",
            command=self._open_sticker_reprint_dialog,
            height=32,
            fg_color=("#7b8490", "#3d4248"),
            hover_color=("#69717b", "#34383d"),
        )
        self.sticker_reprint_btn.pack(fill="x", padx=2)

        self.batch_btns_frame = ctk.CTkFrame(print_body, height=54)
        self.batch_btns_frame.pack(fill="x", padx=8, pady=(0, 8))

        self.preview_section = self._make_main_section(
            self.main_layout_frame,
            "Предпросмотр этикетки",
        )
        self.preview_section.grid(row=0, column=1, sticky="nsew", padx=6)
        preview_body = self.preview_section.body
        preview_body.grid_columnconfigure(0, weight=1)
        preview_body.grid_rowconfigure(0, weight=1)
        self.preview_label = ctk.CTkLabel(
            preview_body, text="Предпросмотр будет здесь", corner_radius=8,
            fg_color=("#eef1f4", "#17191c")
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.preview_current_path = None
        self._preview_resize_busy = False
        self.preview_label.bind("<Configure>", self._on_preview_resize)
        self.preview_strip_frame = ctk.CTkFrame(preview_body, height=66)
        self.preview_strip_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.preview_strip_frame.grid_propagate(False)
        self.preview_mode_label = ctk.CTkLabel(
            self.preview_strip_frame, text="Текущая этикетка", anchor="w"
        )
        self.preview_mode_label.pack(side="left", padx=12, pady=10)
        self.preview_mode_btn = ctk.CTkButton(
            self.preview_strip_frame,
            text="Показать маркировочный стикер",
            width=190,
            command=self._toggle_preview_mode,
        )
        self.preview_mode_btn.pack(side="right", padx=12, pady=10)

        self.csv_status_strip_frame = ctk.CTkFrame(preview_body, height=36)
        self.csv_status_strip_frame.grid(
            row=2, column=0, sticky="ew", padx=10, pady=(0, 10)
        )
        self.csv_status_strip_frame.grid_propagate(False)
        self.csv_summary_label = ctk.CTkLabel(
            self.csv_status_strip_frame, text="CSV: не выбран", anchor="w"
        )
        self.csv_summary_label.pack(side="left", padx=(12, 8), pady=7)
        self.csv_rows_count_label = ctk.CTkLabel(
            self.csv_status_strip_frame, text="строк: 0", anchor="w"
        )
        self.csv_rows_count_label.pack(side="left", padx=8, pady=7)
        self.csv_available_label = ctk.CTkLabel(
            self.csv_status_strip_frame, text="к печати: 0", anchor="w"
        )
        self.csv_available_label.pack(side="left", padx=8, pady=7)
        self.csv_skipped_label = ctk.CTkLabel(
            self.csv_status_strip_frame, text="ошибок: —", anchor="w"
        )
        self.csv_skipped_label.pack(side="left", padx=8, pady=7)
        self.brand_value_label = ctk.CTkLabel(
            self.csv_status_strip_frame, text="бренд: —", anchor="w"
        )
        self.brand_value_label.pack(side="left", padx=8, pady=7)

        self.right_column_frame = ctk.CTkFrame(self.main_layout_frame, fg_color="transparent")
        self.right_column_frame.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        self.right_column_frame.grid_columnconfigure(0, weight=1)

        self.source_section = self._make_main_section(
            self.right_column_frame,
            "Шаблон / принтер",
        )
        self.source_section.grid(row=0, column=0, sticky="ew")
        source_body = self.source_section.body
        source_body.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(source_body, text="Выбранный CSV:", anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=5
        )
        self.csv_label = ctk.CTkLabel(source_body, text="Файл не выбран", anchor="w")
        self.csv_label.grid(row=0, column=1, sticky="ew", padx=10, pady=5)
        ctk.CTkLabel(source_body, text="Шаблон:", anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=5
        )
        self.format_combo = ctk.CTkComboBox(
            source_body,
            values=["Auto"] + self._manual_template_choices(),
            state="readonly",
        )
        self.format_combo.set("Auto")
        self.format_combo.grid(row=1, column=1, sticky="ew", padx=10, pady=5)
        self.format_combo.configure(command=self._on_format_change)
        ctk.CTkLabel(source_body, text="Принтер:", anchor="w").grid(
            row=2, column=0, sticky="w", padx=10, pady=(5, 10)
        )
        self.prn_combo = ctk.CTkComboBox(
            source_body,
            values=["(нет принтеров)"],
            state="readonly",
            command=lambda _value: self._refresh_runtime_status(),
        )
        self.prn_combo.grid(row=2, column=1, sticky="ew", padx=10, pady=(5, 10))
        self.btw16_entry = ctk.CTkEntry(source_body)
        self.btw30_entry = ctk.CTkEntry(source_body)

        self.log_section = ctk.CTkFrame(self, corner_radius=10)
        self.log_section.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        log_header = ctk.CTkFrame(self.log_section, fg_color="transparent")
        log_header.pack(fill="x", padx=10, pady=(6, 2))
        ctk.CTkLabel(log_header, text="Лог выполнения", font=("Arial", 14, "bold")).pack(side="left")
        ctk.CTkButton(log_header, text="Очистить", width=80, command=self._clear_log).pack(side="right")
        self.log_frame = ctk.CTkFrame(self.log_section, fg_color="transparent")
        self.log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.logbox = ctk.CTkTextbox(self.log_frame, height=125)
        self.logbox.pack(fill="both", expand=True)
        self.log_follow = True
        self._bind_log_scroll_events()
        self.logger = Logger(self.logbox, self)
        self._enable_log_copy()
        try:
            self._apply_log_selection_theme()
        except Exception:
            pass

        self.status_bar_frame = ctk.CTkFrame(self, height=30, corner_radius=0)
        self.status_bar_frame.grid(row=3, column=0, sticky="ew")
        self.status_ready_label = ctk.CTkLabel(self.status_bar_frame, text="Готовность: запуск…")
        self.status_ready_label.pack(side="left", padx=(12, 8))
        self.status_csv_label = ctk.CTkLabel(self.status_bar_frame, text="CSV: —")
        self.status_csv_label.pack(side="left", padx=8)
        self.status_rows_label = ctk.CTkLabel(self.status_bar_frame, text="Строк: 0")
        self.status_rows_label.pack(side="left", padx=8)
        self.status_template_label = ctk.CTkLabel(self.status_bar_frame, text="Шаблон: Auto")
        self.status_template_label.pack(side="left", padx=8)
        self.status_printer_label = ctk.CTkLabel(self.status_bar_frame, text="Принтер: —")
        self.status_printer_label.pack(side="left", padx=8)
        self.bt_status_label = ctk.CTkLabel(self.status_bar_frame, text="BarTender: проверка…")
        self.bt_status_label.pack(side="right", padx=12)
        self.catalog_status_label = ctk.CTkLabel(self.status_bar_frame, text="Каталог: проверка…")
        self.catalog_status_label.pack(side="right", padx=8)
        self.main_layout_sections = {
            name: getattr(self, name) for name in MAIN_LAYOUT_SECTION_NAMES
        }
        self.main_layout_block_order = MAIN_LAYOUT_BLOCK_ORDER

        try:
            self._render_csv_list()
            self._apply_format_ui(self.format_combo.get())
        except Exception:
            pass
        self._refresh_runtime_status()

    def _make_main_section(self, parent, title):
        section = ctk.CTkFrame(parent, corner_radius=10)
        header = ctk.CTkFrame(section, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(7, 3))
        ctk.CTkLabel(
            header, text=title, font=("Arial", 14, "bold")
        ).pack(side="left")
        body = ctk.CTkFrame(section, fg_color="transparent")
        body.pack(fill="both", expand=True)
        section.body = body
        return section

    def _fit_current_preview(self):
        path = getattr(self, "preview_current_path", None)
        if path:
            self._show_preview_path(path)

    def _update_preview_mode_ui(self):
        mode = self.__dict__.get("_preview_mode", "label")
        button_text = (
            "Показать этикетку"
            if mode == "sticker"
            else "Показать маркировочный стикер"
        )
        strip_text = "Маркировочный стикер" if mode == "sticker" else "Текущая этикетка"
        try:
            self.preview_mode_btn.configure(text=button_text)
        except Exception:
            pass
        try:
            self.preview_mode_label.configure(text=strip_text)
        except Exception:
            pass

    def _toggle_preview_mode(self):
        state = self.__dict__
        current = state.get("_preview_mode", "label")
        if current == "label":
            cfg = state.get("cfg") or {}
            if not resolve_sticker_template_path(cfg):
                mb.showerror(
                    "Предпросмотр стикера",
                    f"Шаблон не найден: {sticker_template_display_path(cfg)}.",
                )
                return
            state["_preview_mode"] = "sticker"
        else:
            state["_preview_mode"] = "label"
        self._update_preview_mode_ui()
        self._schedule_live_preview(immediate=True)

    def _schedule_live_preview(self, event=None, immediate=False):
        """Debounce automatic preview generation while keeping BarTender COM on the Tk thread."""
        state = self.__dict__
        generation = int(state.get("_live_preview_generation", 0)) + 1
        state["_live_preview_generation"] = generation

        pending = state.get("_live_preview_after_id")
        if pending is not None:
            try:
                object.__getattribute__(self, "after_cancel")(pending)
            except Exception:
                pass
        state["_live_preview_after_id"] = None

        def refresh():
            if generation != state.get("_live_preview_generation", 0):
                return
            state["_live_preview_after_id"] = None
            if state.get("_live_preview_busy", False):
                state["_live_preview_requested"] = True
                return

            state["_live_preview_busy"] = True
            state["_live_preview_requested"] = False
            try:
                self._preview(silent=True)
            except Exception as exc:
                try:
                    self.logger.err(f"Авто-превью: {exc}")
                except Exception:
                    pass
            finally:
                state["_live_preview_busy"] = False
                if state.get("_live_preview_requested", False):
                    state["_live_preview_requested"] = False
                    self._schedule_live_preview(immediate=True)

        try:
            delay = 0 if immediate else LIVE_PREVIEW_DEBOUNCE_MS
            after = object.__getattribute__(self, "after")
            state["_live_preview_after_id"] = after(delay, refresh)
        except Exception:
            state["_live_preview_after_id"] = None

    def _on_print_selection_mode_change(self, mode):
        previous = getattr(self, "_print_selection_mode", DEFAULT_PRINT_SELECTION_MODE)
        if mode not in (PRINT_SELECTION_MODE_RANGE, PRINT_SELECTION_MODE_COUNT):
            return
        if mode != previous:
            start = (self.index_entry.get() or "").strip()
            value = (self.limit_entry.get() or "").strip()
            if start and value:
                try:
                    converted = convert_print_selection_value(
                        start, value, previous, mode
                    )
                    self.limit_entry.delete(0, "end")
                    self.limit_entry.insert(0, str(converted))
                except (TypeError, ValueError):
                    pass
        self._print_selection_mode = mode
        self.print_selection_second_label.configure(
            text="По строку:" if mode == PRINT_SELECTION_MODE_RANGE else "Количество:"
        )
        self._update_print_selection_summary()
        self._schedule_live_preview()

    def _update_print_selection_summary(self, event=None):
        state = self.__dict__
        mode_var = state.get("print_selection_mode_var")
        index_entry = state.get("index_entry")
        limit_entry = state.get("limit_entry")
        summary_label = state.get("print_selection_summary_label")
        if any(
            widget is None
            for widget in (mode_var, index_entry, limit_entry, summary_label)
        ):
            return
        try:
            mode = mode_var.get() or DEFAULT_PRINT_SELECTION_MODE
            start, end, count = resolve_print_selection(
                mode, index_entry.get(), limit_entry.get()
            )
            total_rows = len(state.get("csv_rows") or [])
            text = f"Строки {start}–{end} · {count} шт."
            if total_rows:
                text += f" · из {total_rows}"
        except (TypeError, ValueError):
            text = "—"
        try:
            summary_label.configure(text=text)
        except Exception:
            pass

    def _reset_print_selection_for_loaded_csv(self):
        total_rows = len(getattr(self, "csv_rows", []) or [])
        try:
            self.index_entry.delete(0, "end")
            self.index_entry.insert(0, "1")
            self.limit_entry.delete(0, "end")
            if total_rows:
                self.limit_entry.insert(0, str(total_rows))
        except Exception:
            return
        self._update_print_selection_summary()

    def _mark_print_selection_valid(self, valid):
        defaults = getattr(self, "_selection_entry_border_defaults", (None, None))
        for entry, default in zip((self.index_entry, self.limit_entry), defaults):
            try:
                entry.configure(border_color=default if valid and default else "#dc3545")
            except Exception:
                pass

    def _resolve_print_selection_for_action(self):
        try:
            selection = resolve_print_selection(
                self.print_selection_mode_var.get(),
                self.index_entry.get(),
                self.limit_entry.get(),
                total_rows=len(self.csv_rows),
            )
        except (TypeError, ValueError) as exc:
            self._mark_print_selection_valid(False)
            mb.showerror("Диапазон печати", str(exc))
            return None
        self._mark_print_selection_valid(True)
        return selection

    def _resolve_preview_index(self, silent=False):
        raw = (self.index_entry.get() or "").strip()
        try:
            index = int(raw)
        except (TypeError, ValueError):
            if not silent:
                mb.showerror("Предпросмотр", "Начальная строка должна быть целым числом.")
            return None
        if index < 1 or index > len(self.csv_rows):
            if not silent:
                mb.showerror(
                    "Предпросмотр",
                    f"Начальная строка должна быть от 1 до {len(self.csv_rows)}.",
                )
            return None
        return index

    def _clear_log(self):
        try:
            state = self.logbox.cget("state")
            self.logbox.configure(state="normal")
            self.logbox.delete("1.0", "end")
            self.logbox.configure(state=state)
        except Exception:
            pass

    def _refresh_runtime_status(self):
        csv_path = getattr(self, "csv_path", "") or ""
        rows = len(getattr(self, "csv_rows", []) or [])
        try:
            template = self.format_combo.get() or "Auto"
        except Exception:
            template = "Auto"
        try:
            printer = self.prn_combo.get() or "—"
        except Exception:
            printer = "—"
        try:
            self.status_ready_label.configure(text="Готовность: готово")
            self.status_csv_label.configure(text=f"CSV: {os.path.basename(csv_path) if csv_path else '—'}")
            self.status_rows_label.configure(text=f"Строк: {rows}")
            self.status_template_label.configure(text=f"Шаблон: {template}")
            self.status_printer_label.configure(text=f"Принтер: {printer}")
            self.csv_summary_label.configure(
                text=f"CSV: {os.path.basename(csv_path) if csv_path else 'не выбран'}"
            )
            self.csv_rows_count_label.configure(text=f"строк: {rows}")
            self.csv_available_label.configure(text=f"к печати: {rows}")
            self.csv_skipped_label.configure(text="ошибок: —")
            self._schedule_path_label_refresh()
        except Exception:
            pass
        self._update_print_selection_summary()

    def _build_menu(self):
        """Build the compact task-oriented navigation bar."""
        try:
            catalog_menu = Menu(self, tearoff=0)
            catalog_menu.add_command(
                label="Редактор каталога товаров",
                command=self._open_product_catalog,
            )

            templates_menu = Menu(self, tearoff=0)
            templates_menu.add_command(
                label="Редактор шаблонов", command=self._open_template_editor
            )
            templates_menu.add_command(
                label="Принтеры по шаблонам",
                command=self._open_template_printer_settings,
            )
            templates_menu.add_separator()
            templates_menu.add_command(
                label="Открыть папку шаблонов", command=self._open_templates_dir
            )

            codes_menu = Menu(self, tearoff=0)
            codes_menu.add_command(
                label=CODES_MENU_ORDER[0], command=self._open_crpt_checker_window
            )
            codes_menu.add_command(
                label=CODES_MENU_ORDER[1],
                command=self._open_turnover_report_dialog,
            )

            bar = self.menu_bar_frame
            button_style = dict(
                width=90,
                height=28,
                corner_radius=0,
                fg_color="transparent",
                hover_color="#2a2a2a",
            )

            def make_button(text, menu, width=None):
                style = dict(button_style)
                if width is not None:
                    style["width"] = width
                button = ctk.CTkButton(bar, text=text, **style)
                button.configure(
                    command=lambda popup=menu, anchor=button: self._popup_menu(
                        popup, anchor
                    )
                )
                button.pack(side="left", padx=(0, 2), pady=0)
                return button

            catalog_button = make_button(TOP_LEVEL_MENU_ORDER[0], catalog_menu)
            templates_button = make_button(TOP_LEVEL_MENU_ORDER[1], templates_menu)
            codes_button = make_button(TOP_LEVEL_MENU_ORDER[2], codes_menu, width=150)

            self.version_identity_btn = ctk.CTkButton(
                bar,
                text=version_identity_text(),
                width=112,
                height=28,
                corner_radius=0,
                fg_color="transparent",
                hover_color=("#dde2e7", "#2a2a2a"),
                text_color=("#5f6b76", "#aeb6bf"),
                font=("Arial", 11),
                command=self._open_version_identity,
            )
            self.version_identity_btn.pack(side="right", padx=(4, 12), pady=0)
            self.theme_switch.pack(side="right", padx=(8, 4), pady=4)

            self._menus_popup = [catalog_menu, templates_menu, codes_menu]
            self._menu_buttons = [catalog_button, templates_button, codes_button]
            self._update_menu_colors()
        except Exception:
            pass

    def _open_version_identity(self):
        callback = getattr(self, "_open_version_dialog", None)
        if callable(callback):
            callback()

    @staticmethod
    def _widget_is_inside_popover(widget, popover):
        try:
            if widget is not None and widget.winfo_toplevel() is popover:
                return True
        except Exception:
            pass

        current = widget
        seen = set()
        while current is not None and id(current) not in seen:
            if current is popover:
                return True
            seen.add(id(current))
            namespace = getattr(current, "__dict__", {})
            if "master" in namespace:
                current = namespace["master"]
            else:
                try:
                    current = getattr(current, "master", None)
                except Exception:
                    current = None
        return False

    def _remove_popover_outside_click(self, popover):
        records = self.__dict__.get("_popover_outside_bindings", {})
        record = records.pop(id(popover), None)
        if record is None:
            return

        after_id = record.get("after_id")
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass

        bind_id = record.get("bind_id")
        if bind_id is not None:
            try:
                self._unbind(("bind", "all", "<Button-1>"), bind_id)
            except Exception:
                pass

    def _install_popover_outside_click(self, popover, close_callback):
        records = self.__dict__.setdefault("_popover_outside_bindings", {})
        self._remove_popover_outside_click(popover)
        record = {"popover": popover, "after_id": None, "bind_id": None}
        records[id(popover)] = record

        def outside_click(event):
            widget = getattr(event, "widget", None)
            try:
                widget = self.winfo_containing(
                    int(event.x_root), int(event.y_root)
                ) or widget
            except Exception:
                pass
            if self._widget_is_inside_popover(widget, popover):
                return None
            close_callback()
            return None

        def install_binding():
            record["after_id"] = None
            if records.get(id(popover)) is not record:
                return
            try:
                if not popover.winfo_exists():
                    self._remove_popover_outside_click(popover)
                    return
                record["bind_id"] = self.bind_all(
                    "<Button-1>", outside_click, add="+"
                )
            except Exception:
                self._remove_popover_outside_click(popover)

        try:
            record["after_id"] = self.after_idle(install_binding)
        except Exception:
            self._remove_popover_outside_click(popover)

    def _cleanup_popover_outside_clicks(self):
        records = list(
            self.__dict__.get("_popover_outside_bindings", {}).values()
        )
        popovers = [record.get("popover") for record in records]
        for popover in popovers:
            if popover is not None:
                self._remove_popover_outside_click(popover)

    def _screen_work_area_at(self, x, y):
        try:
            import ctypes
            from ctypes import wintypes

            class Point(ctypes.Structure):
                _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))

            class Rect(ctypes.Structure):
                _fields_ = (
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                )

            class MonitorInfo(ctypes.Structure):
                _fields_ = (
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", Rect),
                    ("rcWork", Rect),
                    ("dwFlags", wintypes.DWORD),
                )

            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = (Point, wintypes.DWORD)
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            user32.GetMonitorInfoW.argtypes = (
                ctypes.c_void_p,
                ctypes.POINTER(MonitorInfo),
            )
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            monitor = user32.MonitorFromPoint(Point(int(x), int(y)), 2)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work = info.rcWork
                return work.left, work.top, work.right, work.bottom
        except Exception:
            pass

        try:
            left = int(self.winfo_vrootx())
            top = int(self.winfo_vrooty())
            return (
                left,
                top,
                left + int(self.winfo_vrootwidth()),
                top + int(self.winfo_vrootheight()),
            )
        except Exception:
            return 0, 0, int(self.winfo_screenwidth()), int(self.winfo_screenheight())

    def _position_popover(self, popover, anchor, *, align_right=False):
        popover.update_idletasks()
        width = max(
            1,
            int(popover.winfo_reqwidth()),
            int(popover.winfo_width()),
        )
        height = max(
            1,
            int(popover.winfo_reqheight()),
            int(popover.winfo_height()),
        )
        anchor_x = int(anchor.winfo_rootx())
        anchor_y = int(anchor.winfo_rooty())
        anchor_width = int(anchor.winfo_width())
        anchor_height = int(anchor.winfo_height())
        left, top, right, bottom = self._screen_work_area_at(
            anchor_x + anchor_width // 2,
            anchor_y + anchor_height // 2,
        )

        x = anchor_x + anchor_width - width if align_right else anchor_x
        x = min(max(x, left), max(left, right - width))
        y = anchor_y + anchor_height + 4
        if y + height > bottom:
            y = anchor_y - height - 4
        y = min(max(y, top), max(top, bottom - height))
        popover.geometry(f"+{x}+{y}")

    @staticmethod
    def _match_plain_popover_background(popover, content):
        color = content.cget("fg_color")
        if isinstance(color, (tuple, list)):
            color = color[1] if ctk.get_appearance_mode() == "Dark" else color[0]
        popover.configure(
            background=color,
            borderwidth=0,
            highlightthickness=0,
        )

    def _activate_popover(
        self, popover, anchor, close_callback, *, align_right=False
    ):
        self._position_popover(popover, anchor, align_right=align_right)
        popover.bind("<Escape>", lambda _event: close_callback(), add="+")

        def destroyed(event):
            if getattr(event, "widget", None) is not popover:
                return
            self._remove_popover_outside_click(popover)
            if self.__dict__.get("_date_popover") is popover:
                self._date_popover = None
            if self.__dict__.get("_version_dialog") is popover:
                self._version_dialog = None
                self._version_update_btn = None

        popover.bind("<Destroy>", destroyed, add="+")
        try:
            popover.protocol("WM_DELETE_WINDOW", close_callback)
        except Exception:
            pass
        self._install_popover_outside_click(popover, close_callback)
        try:
            popover.deiconify()
            popover.lift()
            popover.focus_force()
        except Exception:
            pass

    def _popup_menu(self, menu_obj, widget):
        """Показывает всплывающее меню c tk_popup (с нашими цветами)."""
        try:
            mode = ctk.get_appearance_mode()
            is_dark = (mode == "Dark")
            bg = "#1b1b1f" if is_dark else "#f2f2f2"
            fg = "#f5f5f5" if is_dark else "#000000"
            abg = "#0d6efd" if is_dark else "#2b579a"
            afg = "#ffffff"
            try:
                menu_obj.configure(
                    background=bg,
                    foreground=fg,
                    activebackground=abg,
                    activeforeground=afg,
                    disabledforeground=fg,
                    bd=0, borderwidth=0, relief="flat",
                    activeborderwidth=0, highlightthickness=0
                )
            except Exception:
                pass
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height()
            menu_obj.tk_popup(x, y)
        finally:
            try:
                menu_obj.grab_release()
            except Exception:
                pass

    def _install_global_text_editing(self):
        self._text_context_target = None
        menu = Menu(self, tearoff=0)
        menu.add_command(
            label="Вырезать",
            command=lambda: self._text_edit_action(self._text_context_target, "cut"),
        )
        menu.add_command(
            label="Копировать",
            command=lambda: self._text_edit_action(self._text_context_target, "copy"),
        )
        menu.add_command(
            label="Вставить",
            command=lambda: self._text_edit_action(self._text_context_target, "paste"),
        )
        menu.add_separator()
        menu.add_command(
            label="Выделить всё",
            command=lambda: self._text_edit_action(self._text_context_target, "select_all"),
        )
        self._text_context_menu = menu
        self.bind_all("<KeyPress>", self._on_global_text_hotkey, add="+")
        self.bind_all("<Button-3>", self._show_text_context_menu, add="+")

    def _is_text_widget(self, widget):
        return isinstance(
            widget,
            (tk.Entry, tk.Text, tk.Spinbox, ttk.Entry, ttk.Combobox, ttk.Spinbox),
        )

    def _is_editable_text_widget(self, widget):
        if not self._is_text_widget(widget):
            return False
        try:
            return str(widget.cget("state")) not in ("disabled", "readonly")
        except Exception:
            return True

    def _is_log_text_widget(self, widget):
        logbox = getattr(self, "logbox", None)
        return widget is logbox or widget is getattr(logbox, "_textbox", None)

    def _selected_text(self, widget):
        try:
            if isinstance(widget, tk.Text):
                return widget.get("sel.first", "sel.last")
            if widget.selection_present():
                return widget.get()[widget.index("sel.first"):widget.index("sel.last")]
        except Exception:
            pass
        return ""

    def _delete_text_selection(self, widget):
        try:
            if isinstance(widget, tk.Text):
                widget.delete("sel.first", "sel.last")
            elif widget.selection_present():
                widget.delete("sel.first", "sel.last")
        except Exception:
            pass

    def _text_edit_action(self, widget, action):
        if not self._is_text_widget(widget):
            return None
        editable = self._is_editable_text_widget(widget)
        if action in ("cut", "paste") and not editable:
            return "break"
        if action == "select_all":
            try:
                if isinstance(widget, tk.Text):
                    widget.tag_add("sel", "1.0", "end-1c")
                    widget.mark_set("insert", "1.0")
                else:
                    widget.selection_range(0, "end")
                    widget.icursor("end")
            except Exception:
                pass
            return "break"
        if action in ("copy", "cut"):
            text = self._selected_text(widget)
            if text:
                try:
                    self.clipboard_clear()
                    self.clipboard_append(text)
                except Exception:
                    pass
                if action == "cut" and editable:
                    self._delete_text_selection(widget)
            return "break"
        if action == "paste":
            try:
                text = self.clipboard_get()
            except Exception:
                text = ""
            if text:
                self._delete_text_selection(widget)
                try:
                    widget.insert("insert", text)
                except Exception:
                    pass
            return "break"
        return None

    def _on_global_text_hotkey(self, event):
        if not (int(getattr(event, "state", 0)) & 0x4):
            return None
        actions = {65: ("a", "select_all"), 67: ("c", "copy"),
                   86: ("v", "paste"), 88: ("x", "cut")}
        binding = actions.get(int(getattr(event, "keycode", -1)))
        if binding is None:
            return None
        latin_key, action = binding
        if str(getattr(event, "keysym", "")).lower() == latin_key:
            return None
        return self._text_edit_action(getattr(event, "widget", None), action)

    def _show_text_context_menu(self, event):
        widget = getattr(event, "widget", None)
        if self._is_log_text_widget(widget) or not self._is_editable_text_widget(widget):
            return None
        self._text_context_target = widget
        try:
            widget.focus_set()
            if isinstance(widget, tk.Text):
                widget.mark_set("insert", f"@{event.x},{event.y}")
            else:
                widget.icursor(f"@{event.x}")
            self._text_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._text_context_menu.grab_release()
            except Exception:
                pass
        return "break"

    def _toggle_theme(self):
        if self.theme_switch.get() == 1:
            ctk.set_appearance_mode("dark")
        else:
            ctk.set_appearance_mode("light")

        # Перенастраиваем цвета лога при смене темы
        try:
            logger = self.__dict__.get("logger")
            if logger is not None:
                logger.configure_tags()
        except Exception:
            pass
        # Цвет выделения лога под тему
        try:
            self._apply_log_selection_theme()
        except Exception:
            pass
        # Обновим цвета статусов CSV-листа под тему
        try:
            self._render_csv_list()
        except Exception:
            pass
        self._update_menu_colors()
        self._refresh_bottom_layout()

    def _refresh_bottom_layout(self):
        """Подгоняет нижнюю панель, когда скрываем/показываем лог или превью."""
        if hasattr(self, "main_layout_frame"):
            return
        try:
            preview_widget = getattr(self, "preview_label", None)
            log_widget = getattr(self, "log_frame", None)
            has_preview = bool(preview_widget and preview_widget.winfo_ismapped())
            has_log = bool(log_widget and log_widget.winfo_ismapped())
            if has_preview and preview_widget:
                try:
                    preview_widget.pack_configure(side="left", fill="both", expand=True, padx=12, pady=12)
                except Exception:
                    pass
            if has_log and log_widget:
                try:
                    pad = (0, 12) if has_preview else (12, 12)
                    log_widget.pack_configure(side="left", fill="both", expand=True, padx=pad, pady=12)
                except Exception:
                    pass
            if not has_preview and not has_log:
                try:
                    self.bottom_panel.configure(height=32)
                except Exception:
                    pass
        except Exception:
            pass

    def _toggle_preview_block(self):
        section = getattr(self, "preview_section", None)
        if section is not None:
            try:
                show = bool(self.show_preview_var.get())
                if show:
                    self.main_layout_frame.grid_columnconfigure(1, weight=1, minsize=420)
                    section.grid()
                else:
                    section.grid_remove()
                    self.main_layout_frame.grid_columnconfigure(1, weight=0, minsize=0)
            except Exception:
                pass
            return
        widget = getattr(self, "preview_label", None)
        if not widget:
            return
        show = True
        try:
            show = bool(self.show_preview_var.get())
        except Exception:
            pass
        if show:
            if not widget.winfo_ismapped():
                widget.pack(side="left", fill="both", expand=True, padx=12, pady=12)
        else:
            try:
                widget.pack_forget()
            except Exception:
                pass
        self._refresh_bottom_layout()

    def _toggle_log_block(self):
        section = getattr(self, "log_section", None)
        if section is not None:
            try:
                section.grid() if bool(self.show_log_var.get()) else section.grid_remove()
            except Exception:
                pass
            return
        frame = getattr(self, "log_frame", None)
        if not frame:
            return
        show = True
        try:
            show = bool(self.show_log_var.get())
        except Exception:
            pass
        if show:
            if not frame.winfo_ismapped():
                frame.pack(side="left", fill="both", expand=True, padx=12, pady=12)
        else:
            try:
                frame.pack_forget()
            except Exception:
                pass
        self._refresh_bottom_layout()

    def _on_scale_change(self):
        choice = None
        try:
            choice = self.scale_var.get()
        except Exception:
            choice = "auto"
        if choice == "auto":
            self._apply_auto_scaling()
        else:
            self._apply_manual_scale(choice)

    def _update_menu_colors(self):
        """Перекрашивает кастомный бар и всплывающие меню под тему."""
        try:
            mode = ctk.get_appearance_mode()
            is_dark = (mode == "Dark")
            bg = "#1b1b1f" if is_dark else "#f2f2f2"
            fg = "#f5f5f5" if is_dark else "#000000"
            abg = "#0d6efd" if is_dark else "#2b579a"   # яркая подсветка hover
            afg = "#ffffff"

            # popup-меню
            for m in getattr(self, "_menus_popup", []) or []:
                try:
                    m.configure(
                        background=bg,
                        foreground=fg,
                        activebackground=abg,
                        activeforeground=afg,
                        disabledforeground=fg,
                        tearoff=0,
                        borderwidth=0,
                        bd=0,
                        activeborderwidth=0,
                        relief="flat",
                        highlightthickness=0
                    )
                except Exception:
                    pass
            # кнопки и фон бара
            try:
                bar = getattr(self, "menu_bar_frame", None)
                if bar:
                    bar.configure(fg_color=bg)
            except Exception:
                pass
            for b in getattr(self, "_menu_buttons", []) or []:
                try:
                    b.configure(fg_color=bg, hover_color=abg, text_color=fg)
                except Exception:
                    pass
            # глобальные опции для новых меню (на случай, если tk_popup игнорирует локальные)
            try:
                opts = {
                    "*Menu.background": bg,
                    "*Menu.foreground": fg,
                    "*Menu.activeBackground": abg,
                    "*Menu.activeForeground": afg,
                    "*Menu.disabledForeground": fg,
                    "*Menu.relief": "flat",
                    "*Menu.bd": 0,
                    "*Menu.borderWidth": 0,
                    "*Menu.activeBorderWidth": 0,
                    "*Menu.highlightThickness": 0,
                }
                for k, v in opts.items():
                    self.option_add(k, v)
            except Exception:
                pass
        except Exception:
            pass

    def _fmt_seconds(self, sec):
        try:
            total = int(sec or 0)
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            return f"{h:02d}:{m:02d}:{s:02d}"
        except Exception:
            return "-"

    def _open_stats_window(self):
        data = getattr(self, "stats", None)
        if not data:
            return
        stats = data.data
        win = ctk.CTkToplevel(self)
        win.title("Статистика печати")
        win.geometry("780x620")
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.after(150, lambda: win.attributes("-topmost", False))
            win.focus_force()
        except Exception:
            pass
        top = ctk.CTkFrame(win)
        top.pack(fill="x", padx=12, pady=(10, 6))
        labels_by_fmt = stats.get("labels_by_format") or {}
        format_chunks = []
        for key in sorted(labels_by_fmt.keys(), key=lambda x: (x == "other", str(x))):
            val = labels_by_fmt.get(key, 0)
            if val or key != "other":
                format_chunks.append(f"{key}: {val}")
        ctk.CTkLabel(top, text=f"Всего напечатано марок: {stats.get('labels_total', 0)}").pack(anchor="w", pady=2)
        ctk.CTkLabel(top, text=" | ".join(format_chunks) if format_chunks else "По шаблонам: нет данных").pack(anchor="w", pady=2)

        jobs_frame = ctk.CTkFrame(win)
        jobs_frame.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        ctk.CTkLabel(jobs_frame, text="Последние задания (20):").pack(anchor="w")
        jf = ctk.CTkScrollableFrame(jobs_frame, height=240)
        jf.pack(fill="both", expand=True, pady=(4, 4))
        jobs = list(stats.get("jobs_history") or [])
        for j in list(reversed(jobs))[:20]:
            status_txt = "OK" if j.get("success") else "FAIL"
            txt = (
                f"{j.get('ts','-')} | "
                f"labels={j.get('labels',0)} | "
                f"fmt={j.get('format','-')} | "
                f"prn={j.get('printer','-')} | "
                f"job={j.get('job_name','') or '-'} | "
                f"статус={status_txt}"
            )
            ctk.CTkLabel(jf, text=txt, anchor="w", justify="left").pack(anchor="w", fill="x", padx=6, pady=2)

        def _copy_stats():
            try:
                chunks = [
                    f"Всего марок: {stats.get('labels_total',0)}",
                    " | ".join(format_chunks) if format_chunks else "По шаблонам: нет данных",
                ]
                chunks.append("Задания:")
                for j in list(reversed(jobs))[:20]:
                    status_txt = "OK" if j.get("success") else "FAIL"
                    chunks.append(
                        f" - {j.get('ts','-')} labels={j.get('labels',0)} fmt={j.get('format','-')} prn={j.get('printer','-')} job={j.get('job_name','') or '-'} статус={status_txt}"
                    )
                txt = "\n".join(chunks)
                self.clipboard_clear()
                self.clipboard_append(txt)
            except Exception:
                pass
        ctk.CTkButton(win, text="Скопировать в буфер", command=_copy_stats).pack(pady=(4, 6))

    def _log_template_migration(self):
        logger = getattr(self, "logger", None)
        if logger is None:
            return
        for event in getattr(self, "_template_migration_events", []) or []:
            status = event.get("status")
            source = event.get("source") or "-"
            target = event.get("target") or "-"
            if status == "error":
                try:
                    logger.err(
                        f"[Шаблоны] Ошибка миграции {source} -> {target}: "
                        f"{event.get('error') or 'unknown error'}"
                    )
                except Exception:
                    pass
            elif status in ("copied", "conflict-preserved"):
                try:
                    logger.log(f"[Шаблоны] Миграция {status}: {source} -> {target}")
                except Exception:
                    pass

    def _open_templates_dir(self):
        """Открывает persistent-папку пользовательских шаблонов."""
        try:
            os.makedirs(TEMPLATES_DIR, exist_ok=True)
        except Exception:
            pass
        path = os.path.abspath(TEMPLATES_DIR)
        try:
            os.startfile(path)
            return
        except Exception:
            pass
        try:
            import subprocess
            subprocess.run(["explorer", path], check=False)
        except Exception as e:
            try:
                mb.showerror("Templates", f"Не удалось открыть папку шаблонов:\n{e}")
            except Exception:
                pass

    def _part_mode_label(self, mode: str) -> str:
        mapping = {
            "blank": "Не использовать",
            "manual": "Ввод вручную",
        }
        return mapping.get(self._normalize_part_mode(mode), "Не использовать")

    def _part_mode_from_label(self, label: str) -> str:
        reverse = {
            "Не использовать": "blank",
            "Ввод вручную": "manual",
        }
        return reverse.get(str(label or "").strip(), "blank")

    def _template_required_fields(self, part_mode: str):
        required = ["ShortName", "ShortGTIN", "EXP_DATE", "PROD_DATE", "DM", "NUM"]
        if self._normalize_part_mode(part_mode) == "manual":
            required.append("PART_NUM")
        return required

    def _validate_template_file(self, template_path: str, part_mode: str = "blank"):
        if not self._bartender_available():
            raise RuntimeError("BarTender недоступен: невозможно проверить BTW-шаблон.")
        if not template_path or not os.path.isfile(template_path):
            raise FileNotFoundError(template_path or "Файл шаблона не найден")

        fmt = None
        try:
            fmt = self.bt.open_format(template_path)
            report = self._template_batch_report(fmt, part_mode=part_mode)
            dm_report = self._template_dm_validation_report(fmt, batch_report=report)
        finally:
            try:
                if fmt:
                    fmt.Close(1)
            except Exception:
                pass

        return {
            "ok": not report["missing_named"],
            "fields": report["named_fields"],
            "required": report["required"],
            "missing": report["missing_named"],
            "batch_ok": report["can_batch"],
            "batch_message": report["batch_message"],
            "db_fields": report["db_fields"],
            "missing_db": report["missing_db"],
            "template_only": report["template_only"],
            "db_count": report["db_count"],
            "use_database": report["use_database"],
            "dm_present": dm_report["present"],
            "dm_message": dm_report["message"],
            "dm_details": dm_report["details"],
            "dm_com_values": dm_report["com_values"],
        }

    def _open_template_editor(self):
        win = ctk.CTkToplevel(self)
        win.title("Редактор шаблонов")
        win.geometry("980x700")
        win.grab_set()
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.after(150, lambda: win.attributes("-topmost", False))
            win.focus_force()
        except Exception:
            pass

        ctk.CTkLabel(
            win,
            text=(
                "Встроенные шаблоны поставляются с AutoPrint. "
                "Импортированные BTW копируются в пользовательское хранилище AutoPrint."
            ),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        frame = ctk.CTkFrame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        cols = ("id", "name", "file", "part_mode", "printer", "status")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.heading("id", text="ID")
        tree.heading("name", text="Название")
        tree.heading("file", text="BTW файл")
        tree.heading("part_mode", text="Партия")
        tree.heading("printer", text="Принтер")
        tree.heading("status", text="Статус")
        tree.column("id", width=110, anchor="w")
        tree.column("name", width=180, anchor="w")
        tree.column("file", width=180, anchor="w")
        tree.column("part_mode", width=150, anchor="w")
        tree.column("printer", width=220, anchor="w")
        tree.column("status", width=120, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        def _template_status(template_id):
            path = self._resolve_template_btw_path(template_id)
            return "OK" if path else "Файл не найден"

        def refresh_tree():
            tree.delete(*tree.get_children())
            for template_id, meta in self._iter_label_templates():
                tree.insert(
                    "",
                    "end",
                    iid=template_id,
                    values=(
                        template_id,
                        meta.get("name") or template_id,
                        meta.get("file_name") or "",
                        self._part_mode_label(meta.get("part_mode")),
                        meta.get("printer") or "(по умолчанию)",
                        _template_status(template_id),
                    ),
                )

        def edit_template(existing_id=None):
            existing = self._get_label_template(existing_id) or {}
            dlg = ctk.CTkToplevel(win)
            dlg.title("Новый шаблон" if not existing_id else f"Шаблон: {existing_id}")
            dlg.geometry("700x360")
            dlg.grab_set()
            try:
                dlg.lift()
                dlg.attributes("-topmost", True)
                dlg.after(150, lambda: dlg.attributes("-topmost", False))
                dlg.focus_force()
            except Exception:
                pass

            body = ctk.CTkFrame(dlg)
            body.pack(fill="both", expand=True, padx=12, pady=12)
            body.grid_columnconfigure(1, weight=1)

            row = 0
            ctk.CTkLabel(body, text="ID шаблона:").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
            id_entry = ctk.CTkEntry(body, width=220)
            id_entry.grid(row=row, column=1, sticky="ew", pady=6)
            id_entry.insert(0, existing_id or "")
            if existing_id:
                id_entry.configure(state="disabled")

            row += 1
            ctk.CTkLabel(body, text="Название:").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
            name_entry = ctk.CTkEntry(body)
            name_entry.grid(row=row, column=1, sticky="ew", pady=6)
            name_entry.insert(0, existing.get("name") or existing_id or "")

            row += 1
            ctk.CTkLabel(body, text="BTW файл:").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
            file_row = ctk.CTkFrame(body, fg_color="transparent")
            file_row.grid(row=row, column=1, sticky="ew", pady=6)
            file_row.grid_columnconfigure(0, weight=1)
            file_entry = ctk.CTkEntry(file_row)
            file_entry.grid(row=0, column=0, sticky="ew")
            file_entry.insert(0, existing.get("file_name") or "")

            row += 1
            ctk.CTkLabel(body, text="Режим партии:").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
            part_options = list(PART_MODE_OPTIONS)
            part_var = tk.StringVar(value=self._part_mode_label(existing.get("part_mode")))
            ctk.CTkOptionMenu(body, variable=part_var, values=part_options).grid(row=row, column=1, sticky="w", pady=6)

            row += 1
            ctk.CTkLabel(body, text="Принтер по умолчанию:").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
            printer_values = self._get_printer_values()
            current_printer = str(existing.get("printer") or "").strip()
            extras = [current_printer] if current_printer and current_printer not in printer_values else []
            printer_options = ["(по умолчанию)"] + extras + printer_values if printer_values else ["(по умолчанию)"]
            printer_var = tk.StringVar(value=current_printer or "(по умолчанию)")
            ctk.CTkOptionMenu(body, variable=printer_var, values=printer_options).grid(row=row, column=1, sticky="ew", pady=6)

            row += 1
            status_label = ctk.CTkLabel(body, text="Подсказка: импортируйте BTW, затем проверьте поля.")
            status_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))

            def _current_template_id():
                if existing_id:
                    return existing_id
                return self._normalize_template_id(id_entry.get())

            def _current_template_path():
                template_id = _current_template_id()
                file_name = self._sanitize_template_filename(file_entry.get() or "", template_id)
                if file_name:
                    path = default_template_path(file_name)
                    if os.path.isfile(path):
                        return path
                return self._resolve_template_btw_path(template_id)

            def on_import():
                template_id = _current_template_id()
                src = fd.askopenfilename(
                    title="Выбери BTW шаблон",
                    filetypes=[("BarTender Template", "*.btw")],
                )
                if not src:
                    return
                try:
                    target, file_name = self._copy_template_into_workspace(src, template_id)
                    file_entry.delete(0, "end")
                    file_entry.insert(0, file_name)
                    status_label.configure(text=f"Импортировано в AutoPrint: {os.path.basename(target)}")
                except Exception as e:
                    mb.showerror("Шаблон", f"Не удалось импортировать BTW:\n{e}")

            def on_validate():
                template_id = _current_template_id()
                if not template_id:
                    mb.showerror("Шаблон", "Сначала укажите ID шаблона.")
                    return
                path = _current_template_path()
                if not path:
                    mb.showerror("Шаблон", "Файл BTW не найден. Сначала импортируйте или укажите файл.")
                    return
                try:
                    report = self._validate_template_file(path, self._part_mode_from_label(part_var.get()))
                    fields_preview = ", ".join(report["fields"][:12]) or "-"
                    if len(report["fields"]) > 12:
                        fields_preview += ", ..."
                    lines = [f"Найдены поля ({len(report['fields'])}): {fields_preview}"]
                    if report["missing"]:
                        lines.insert(0, f"Не хватает NamedSubStrings: {', '.join(report['missing'])}")
                    else:
                        lines.insert(0, "Шаблон принимает нужные поля для прямой подстановки.")

                    lines.append("")
                    lines.append("Пакетная печать:")
                    lines.append(report.get("batch_message") or "-")

                    missing_db = report.get("missing_db") or []
                    if missing_db:
                        lines.append("")
                        lines.append("Не хватает Database Fields: " + ", ".join(missing_db))

                    template_only = report.get("template_only") or []
                    if template_only:
                        lines.append(
                            "Внимание: эти поля заведены как Template Field, а не Database Field: "
                            + ", ".join(template_only)
                        )

                    dm_details = report.get("dm_details") or []
                    if dm_details:
                        lines.append("")
                        lines.append("DataMatrix / Честный Знак:")
                        lines.extend(dm_details)

                    text = "\n".join(lines)
                    if report["missing"] or not report.get("batch_ok"):
                        status_label.configure(
                            text=(
                                f"Проверка: поля OK, но batch нет"
                                if (not report["missing"] and not report.get("batch_ok"))
                                else f"Проверка: не хватает полей ({', '.join(report['missing'])})"
                            )
                        )
                        mb.showwarning("Проверка шаблона", text)
                    else:
                        ok_text = f"Проверка OK: batch готов, полей {len(report['fields'])}"
                        if report.get("dm_present"):
                            ok_text += "; DM проверьте вручную"
                        status_label.configure(text=ok_text)
                        mb.showinfo("Проверка шаблона", text)
                except Exception as e:
                    status_label.configure(text=f"Проверка не удалась: {e}")
                    mb.showerror("Проверка шаблона", f"Не удалось проверить шаблон:\n{e}")

            def on_save():
                template_id = _current_template_id()
                if not template_id:
                    mb.showerror("Шаблон", "ID шаблона обязателен.")
                    return
                if (not existing_id) and template_id in (self.label_templates or {}):
                    mb.showerror("Шаблон", f"Шаблон '{template_id}' уже существует.")
                    return
                file_name = self._sanitize_template_filename(file_entry.get() or "", template_id)
                if not file_name:
                    file_name = self._sanitize_template_filename(f"{template_id}.btw", template_id)
                meta = {
                    "id": template_id,
                    "name": self._normalize_template_id(name_entry.get()) or template_id,
                    "file_name": file_name,
                    "printer": "" if printer_var.get() in ("(по умолчанию)", "(нет принтеров)", "") else printer_var.get(),
                    "part_mode": self._part_mode_from_label(part_var.get()),
                }
                self.label_templates[template_id] = meta
                self._save_label_templates_to_cfg()
                self._refresh_format_choices(preserve_value=self.format_combo.get())
                self._refresh_printers()
                try:
                    self._apply_format_ui(self.format_combo.get())
                except Exception:
                    pass
                refresh_tree()
                try:
                    self.logger.log(f"[Шаблон] Сохранён шаблон '{template_id}' ({file_name}).")
                except Exception:
                    pass
                dlg.destroy()

            ctk.CTkButton(file_row, text="Импорт…", width=110, command=on_import).grid(row=0, column=1, padx=(8, 0))

            btns = ctk.CTkFrame(dlg)
            btns.pack(pady=(0, 12))
            validate_btn = ctk.CTkButton(
                btns,
                text="Проверить поля",
                command=on_validate,
                width=130,
                state="normal" if self._bartender_available() else "disabled",
            )
            validate_btn.pack(side="left", padx=6)
            ctk.CTkButton(btns, text="Сохранить", command=on_save, width=120).pack(side="left", padx=6)
            ctk.CTkButton(btns, text="Отмена", command=dlg.destroy, width=90, fg_color="#444").pack(side="left", padx=6)

        def on_add():
            edit_template()

        def on_edit():
            sel = tree.selection()
            if not sel:
                return
            edit_template(sel[0])

        def on_delete():
            sel = tree.selection()
            if not sel:
                return
            template_id = sel[0]
            if template_id in ("16x16", "30x20"):
                mb.showerror("Шаблон", "Базовые шаблоны 16x16 и 30x20 удалять нельзя.")
                return
            if not self._require_catalog_ready("Проверка использования шаблона"):
                return
            try:
                usage_count = self.catalog_store.count_template_usage(template_id)
            except Exception as exc:
                self._set_catalog_status("error", f"Ошибка чтения каталога: {exc}")
                mb.showerror("Шаблон", f"Не удалось проверить использование шаблона:\n{exc}")
                return
            prompt = f"Удалить шаблон '{template_id}'?"
            if usage_count:
                prompt += f"\nОн назначен товарам: {usage_count}."
            if not mb.askyesno("Шаблон", prompt):
                return
            self.label_templates.pop(template_id, None)
            self._save_label_templates_to_cfg()
            self._refresh_format_choices(preserve_value=self.format_combo.get())
            self._refresh_printers()
            refresh_tree()

        sticker_frame = ctk.CTkFrame(win)
        sticker_frame.pack(fill="x", padx=12, pady=(0, 10))
        sticker_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            sticker_frame,
            text="Маркировочный стикер",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 4))

        ctk.CTkLabel(sticker_frame, text="BTW файл:").grid(
            row=1, column=0, sticky="w", padx=(10, 8), pady=4
        )
        sticker_path_label = ctk.CTkLabel(sticker_frame, text="", anchor="w")
        sticker_path_label.grid(row=1, column=1, sticky="ew", pady=4)

        sticker_status_label = ctk.CTkLabel(sticker_frame, text="", width=120)
        sticker_status_label.grid(row=1, column=2, sticky="e", padx=(8, 10), pady=4)

        ctk.CTkLabel(sticker_frame, text="Принтер:").grid(
            row=2, column=0, sticky="w", padx=(10, 8), pady=(4, 10)
        )
        sticker_printers = self._get_printer_values()
        sticker_current_printer = str(self.cfg.get("sticker_printer") or "").strip()
        sticker_extra = (
            [sticker_current_printer]
            if sticker_current_printer and sticker_current_printer not in sticker_printers
            else []
        )
        sticker_printer_options = ["(Авто: TE210 / выбранный)"] + sticker_extra + sticker_printers
        sticker_printer_var = tk.StringVar(
            value=sticker_current_printer or "(Авто: TE210 / выбранный)"
        )

        def on_sticker_printer_change(value):
            value = str(value or "").strip()
            if value == "(Авто: TE210 / выбранный)":
                self.cfg.pop("sticker_printer", None)
            else:
                self.cfg["sticker_printer"] = value
            save_config(self.cfg)

        ctk.CTkOptionMenu(
            sticker_frame,
            variable=sticker_printer_var,
            values=sticker_printer_options,
            command=on_sticker_printer_change,
        ).grid(row=2, column=1, sticky="ew", pady=(4, 10))

        def refresh_sticker_section():
            resolved = resolve_sticker_template_path(self.cfg)
            display_path = sticker_template_display_path(self.cfg)
            sticker_path_label.configure(text=display_path)
            sticker_status_label.configure(
                text="OK" if resolved else "Файл не найден"
            )

        def on_sticker_import():
            src = fd.askopenfilename(
                title="Выбери BTW маркировочного стикера",
                filetypes=[("BarTender Template", "*.btw")],
            )
            if not src:
                return
            try:
                target = self._import_sticker_template(src)
                refresh_sticker_section()
                self.logger.log(
                    f"[Шаблон] Маркировочный стикер импортирован: {target}"
                )
            except Exception as e:
                mb.showerror(
                    "Маркировочный стикер",
                    f"Не удалось импортировать BTW:\n{e}",
                )

        ctk.CTkButton(
            sticker_frame,
            text="Импортировать…",
            width=130,
            command=on_sticker_import,
        ).grid(row=2, column=2, sticky="e", padx=(8, 10), pady=(4, 10))

        refresh_sticker_section()

        btns = ctk.CTkFrame(win)
        btns.pack(pady=(0, 12))
        ctk.CTkButton(btns, text="Добавить", command=on_add, width=110).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Редактировать", command=on_edit, width=130).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Удалить", command=on_delete, width=110).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Открыть папку", command=self._open_templates_dir, width=120).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Закрыть", command=win.destroy, width=90, fg_color="#444").pack(side="left", padx=6)

        tree.bind("<Double-1>", lambda _e: on_edit())
        refresh_tree()

    def _open_template_printer_settings(self):
        """Окно настроек принтеров по шаблонам."""
        win = ctk.CTkToplevel(self)
        win.title("Принтеры по шаблонам")
        win.geometry("520x420")
        win.grab_set()
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.after(150, lambda: win.attributes("-topmost", False))
            win.focus_force()
        except Exception:
            pass

        values = self._get_printer_values()
        extras = []
        for _template_id, meta in self._iter_label_templates():
            v = str(meta.get("printer") or "").strip()
            if v and v not in values and v not in extras:
                extras.append(v)
        sticker_printer = str(self.cfg.get("sticker_printer") or "").strip()
        if sticker_printer and sticker_printer not in values and sticker_printer not in extras:
            extras.append(sticker_printer)
        values = extras + values
        options = ["(по умолчанию)"] + values if values else ["(по умолчанию)"]

        body = ctk.CTkScrollableFrame(win)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.grid_columnconfigure(1, weight=1)

        template_vars = {}
        row = 0
        for template_id, meta in self._iter_label_templates():
            current = str(meta.get("printer") or "").strip() or "(по умолчанию)"
            template_vars[template_id] = tk.StringVar(value=current)
            ctk.CTkLabel(
                body,
                text=f"{template_id} ({self._template_display_name(template_id)})",
            ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
            ctk.CTkOptionMenu(body, variable=template_vars[template_id], values=options).grid(
                row=row, column=1, sticky="ew", pady=6
            )
            row += 1

        var_st = tk.StringVar(value=sticker_printer or "(по умолчанию)")
        ctk.CTkLabel(body, text="Стикер 60x40").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        ctk.CTkOptionMenu(body, variable=var_st, values=options).grid(row=row, column=1, sticky="ew", pady=6)

        btns = ctk.CTkFrame(win)
        btns.pack(pady=(0, 12))

        def _save():
            for template_id, var in template_vars.items():
                val = str(var.get() or "").strip()
                meta = self.label_templates.get(template_id) or {"id": template_id}
                meta["printer"] = "" if val in ("(по умолчанию)", "(нет принтеров)", "") else val
                self.label_templates[template_id] = meta
            st_val = str(var_st.get() or "").strip()
            if st_val in ("(по умолчанию)", "(нет принтеров)", ""):
                self.cfg.pop("sticker_printer", None)
            else:
                self.cfg["sticker_printer"] = st_val

            self._save_label_templates_to_cfg()
            try:
                self.logger.log("[Пресет] Принтеры по шаблонам сохранены.")
            except Exception:
                pass
            try:
                self._set_default_printer_for_format((self.format_combo.get() or "").strip())
            except Exception:
                pass
            win.destroy()

        ctk.CTkButton(btns, text="Сохранить", command=_save, width=120).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Отмена", command=win.destroy, width=90, fg_color="#444").pack(side="left", padx=6)

    def _enable_log_copy(self):
        def _copy():
            try:
                sel = self.logbox.get("sel.first", "sel.last")
            except Exception:
                sel = ""
            if sel:
                self.clipboard_clear()
                self.clipboard_append(sel)

        def _all():
            try:
                self.logbox.tag_add("sel", "1.0", "end-1c")
            except Exception:
                pass
            return "break"

        def _prevent_typing(event):
            # Allow navigation keys
            if event.keysym in ('Left', 'Right', 'Up', 'Down', 'Home', 'End', 'Prior', 'Next',
                                'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R'):
                return

            # On Windows, Ctrl key press sets state to 4
            is_ctrl_pressed = (event.state & 0x4) != 0
            key_sym = event.keysym.lower()
            key_code = event.keycode  # hardware code, одинаковый в любой раскладке

            # Handle Ctrl+C (Copy) for both English and Russian layouts
            if is_ctrl_pressed and (key_sym in ('c', 'с') or key_code in (67,)):
                _copy()
                return "break"

            # Handle Ctrl+A (Select All) for both English and Russian layouts
            if is_ctrl_pressed and (key_sym in ('a', 'ф') or key_code in (65,)):
                _all()
                return "break"

            # Block any other key that would modify the text
            # Check for printable characters, excluding control characters
            if len(event.char) > 0 and event.char.isprintable():
                return "break"
            
            # Block specific modification keys
            if event.keysym in ('BackSpace', 'Delete', 'Return', 'Tab', 'Escape'):
                return "break"
        
        # We only need one binding to handle making the widget read-only and allowing shortcuts
        self.logbox.bind("<KeyPress>", _prevent_typing)

        m = Menu(self, tearoff=0)
        m.add_command(label="Копировать", command=_copy)
        m.add_command(label="Выделить всё", command=_all)
        m.add_separator()
        m.add_command(label="Сохранить лог…", command=self._save_log)
        self.logbox.bind("<Button-3>", lambda e: (m.tk_popup(e.x_root, e.y_root), m.grab_release()))

    def _start_bt(self):
        bt = BT(self.logger)
        try:
            bt.start()
        except Exception as e:
            try:
                bt.stop()
            finally:
                self.bt = None
            self.logger.err(f"COM не стартовал: {e}")
            self._set_bartender_status(False)
            return False
        self.bt = bt
        self._set_bartender_status(True)
        return True

    def _bartender_available(self):
        bt = getattr(self, "bt", None)
        return bt is not None and getattr(bt, "app", None) is not None

    def _set_bartender_status(self, available: bool):
        try:
            self.bt_status_label.configure(
                text="BarTender: доступен" if available else "BarTender: недоступен"
            )
        except Exception:
            pass
        self._update_action_capabilities()

    def _require_bartender(self, action: str):
        if self._bartender_available():
            return True
        message = (
            f"{action} недоступен: BarTender не установлен или COM-сервер не отвечает. "
            "Установите или восстановите BarTender и перезапустите AutoPrint."
        )
        try:
            self.logger.err(message)
        except Exception:
            pass
        mb.showwarning("BarTender недоступен", message)
        return False

    def _require_print_capabilities(self, action: str):
        return self._require_catalog_ready(action) and self._require_bartender(action)

    def _shutdown_app(self):
        state = self.__dict__
        if state.get("_shutdown_started", False):
            return
        state["_shutdown_started"] = True
        pending_preview = state.get("_live_preview_after_id")
        if pending_preview is not None:
            try:
                object.__getattribute__(self, "after_cancel")(pending_preview)
            except Exception:
                pass
        state["_live_preview_after_id"] = None
        self._cleanup_preview_temp_dir()
        self._close_date_popover()
        version_closer = getattr(type(self), "_close_version_dialog", None)
        if callable(version_closer):
            version_closer(self)
        self._cleanup_popover_outside_clicks()
        bt = getattr(self, "bt", None)
        self.bt = None
        try:
            if bt is not None:
                bt.stop()
        finally:
            super().destroy()

    def _normalize_template_id(self, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())

    def _sanitize_template_filename(self, value: str, template_id: str = "") -> str:
        raw = os.path.basename(str(value or "").strip())
        if not raw and template_id:
            raw = f"{self._normalize_template_id(template_id)}.btw"
        raw = re.sub(r'[\\/:*?"<>|]+', "_", raw).strip(" .")
        if raw and not raw.lower().endswith(".btw"):
            raw += ".btw"
        return raw

    def _normalize_part_mode(self, value: str) -> str:
        mode = str(value or "").strip().lower()
        aliases = {
            "manual": "manual",
            "вручную": "manual",
            "ручной": "manual",
            "blank": "blank",
            "empty": "blank",
            "пусто": "blank",
            "не использовать": "blank",
            "catalog": "blank",
            "auto": "blank",
            "из шаблона партии": "blank",
        }
        return aliases.get(mode, "blank")

    def _default_label_templates(self):
        old_printers = self.cfg.get("format_printers") or {}
        defaults = {}
        for template_id, name, file_name, part_mode in (
            ("16x16", "Банка 16x16", "16x16.btw", "blank"),
            ("30x20", "Ведро 30x20", "30x20.btw", "manual"),
            ("58x60", "Ведро 58x60", "58x60.btw", "manual"),
        ):
            defaults[template_id] = {
                "id": template_id,
                "name": name,
                "file_name": file_name,
                "printer": str(old_printers.get(template_id) or "").strip(),
                "part_mode": part_mode,
            }
        return defaults

    def _load_label_templates_from_cfg(self):
        templates = self._default_label_templates()
        raw = self.cfg.get("label_templates") or {}
        items = []
        if isinstance(raw, dict):
            items = list(raw.items())
        elif isinstance(raw, list):
            items = [(item.get("id"), item) for item in raw if isinstance(item, dict)]

        for key, meta in items:
            if not isinstance(meta, dict):
                continue
            template_id = self._normalize_template_id(meta.get("id") or key)
            if not template_id:
                continue
            current = dict(templates.get(template_id) or {})
            current["id"] = template_id
            current["name"] = self._normalize_template_id(meta.get("name") or current.get("name") or template_id) or template_id
            current["file_name"] = self._sanitize_template_filename(
                meta.get("file_name") or meta.get("filename") or current.get("file_name") or "",
                template_id,
            )
            current["printer"] = str(meta.get("printer") or current.get("printer") or "").strip()
            current["part_mode"] = self._normalize_part_mode(meta.get("part_mode") or current.get("part_mode"))
            templates[template_id] = current
        return templates

    def _save_label_templates_to_cfg(self):
        payload = {}
        format_printers = {}
        for template_id, meta in self._iter_label_templates():
            payload[template_id] = {
                "id": template_id,
                "name": self._normalize_template_id(meta.get("name") or template_id) or template_id,
                "file_name": self._sanitize_template_filename(meta.get("file_name") or "", template_id),
                "printer": str(meta.get("printer") or "").strip(),
                "part_mode": self._normalize_part_mode(meta.get("part_mode")),
            }
            if payload[template_id]["printer"]:
                format_printers[template_id] = payload[template_id]["printer"]
        self.label_templates = payload
        self.cfg["label_templates"] = payload
        if format_printers:
            self.cfg["format_printers"] = format_printers
        else:
            self.cfg.pop("format_printers", None)
        save_config(self.cfg)

    def _iter_label_templates(self):
        templates = self.label_templates if isinstance(getattr(self, "label_templates", None), dict) else {}
        return [(k, dict(v or {})) for k, v in templates.items()]

    def _manual_template_choices(self):
        return [template_id for template_id, _meta in self._iter_label_templates()]

    def _refresh_format_choices(self, preserve_value=None):
        combo = getattr(self, "format_combo", None)
        if not combo:
            return
        values = ["Auto"] + self._manual_template_choices()
        current = self._normalize_template_id(preserve_value if preserve_value is not None else combo.get())
        combo.configure(values=values)
        combo.set(current if current in values else "Auto")

    def _get_label_template(self, template_id: str):
        key = self._normalize_template_id(template_id)
        if not key:
            return None
        meta = (self.label_templates or {}).get(key)
        return dict(meta or {}) if isinstance(meta, dict) else None

    def _template_display_name(self, template_id: str) -> str:
        meta = self._get_label_template(template_id) or {}
        return str(meta.get("name") or template_id or "").strip()

    def _resolve_template_btw_path(self, template_id: str) -> str:
        template_id = self._normalize_template_id(template_id)
        if not template_id:
            return ""
        meta = self._get_label_template(template_id) or {}
        candidates = []

        file_name = self._sanitize_template_filename(meta.get("file_name") or "", template_id)
        if file_name:
            candidates.append(default_template_path(file_name))
            candidates.append(os.path.join(TEMPLATES_DIR, file_name))

        legacy_formats = self.cfg.get("formats") or {}
        legacy_path = str(legacy_formats.get(template_id) or "").strip()
        if legacy_path:
            candidates.append(legacy_path)

        if template_id == "16x16":
            candidates.append(str(self.cfg.get("btw_16") or "").strip())
        elif template_id == "30x20":
            candidates.append(str(self.cfg.get("btw_30") or "").strip())

        candidates.append(default_template_path(f"{template_id}.btw"))

        seen = set()
        for candidate in candidates:
            path = str(candidate or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            if os.path.isfile(path):
                return path
        return ""

    def _import_sticker_template(self, src_path: str):
        src_path = str(src_path or "").strip()
        if not src_path or not os.path.isfile(src_path):
            raise FileNotFoundError(src_path or "Sticker template file is missing")
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        target = os.path.join(TEMPLATES_DIR, STICKER_BTW_NAME)
        import shutil as _shutil
        if os.path.normcase(os.path.abspath(src_path)) != os.path.normcase(os.path.abspath(target)):
            _shutil.copy2(src_path, target)
        self.cfg["sticker_btw"] = target
        save_config(self.cfg)
        return target

    def _copy_template_into_workspace(self, src_path: str, template_id: str = ""):
        src_path = str(src_path or "").strip()
        if not src_path or not os.path.isfile(src_path):
            raise FileNotFoundError(src_path or "Template file is missing")
        meta = self._get_label_template(template_id) or {}
        file_name = self._sanitize_template_filename(
            meta.get("file_name") or os.path.basename(src_path),
            template_id,
        )
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        target = os.path.join(TEMPLATES_DIR, file_name)
        import shutil as _shutil
        if os.path.normcase(os.path.abspath(src_path)) != os.path.normcase(os.path.abspath(target)):
            _shutil.copy2(src_path, target)
        return target, file_name

    def _template_part_mode(self, template_id: str) -> str:
        meta = self._get_label_template(template_id) or {}
        return self._normalize_part_mode(meta.get("part_mode"))

    def _template_requires_manual_part(self, template_id: str) -> bool:
        return self._template_part_mode(template_id) == "manual"

    def _get_printer_values(self):
        try:
            values = list(self.prn_combo.cget("values") or [])
        except Exception:
            values = []
        if not values or values == ["(нет принтеров)"]:
            try:
                values = enum_printers() or []
            except Exception:
                values = []
        seen = set()
        ordered = []
        for v in values:
            if v and v not in seen:
                ordered.append(v)
                seen.add(v)
        return ordered

    def _match_printer_value(self, preferred):
        if not preferred:
            return None
        values = self._get_printer_values()
        for v in values:
            if v == preferred:
                return v
        pref_lower = preferred.lower()
        for v in values:
            if v.lower() == pref_lower:
                return v
        for v in values:
            if pref_lower in v.lower():
                return v
        return None

    def _get_format_printer_preference(self, fmt: str):
        meta = self._get_label_template(fmt) or {}
        printer = str(meta.get("printer") or "").strip()
        if printer:
            return printer
        try:
            fmt_map = self.cfg.get("format_printers") or {}
        except Exception:
            fmt_map = {}
        return str(fmt_map.get(fmt) or "").strip()

    def _refresh_printers(self):
        items = enum_printers() or ["(нет принтеров)"]
        self.prn_combo.configure(values=items)
        cur_fmt = (self.format_combo.get() or "").strip() if hasattr(self, "format_combo") else ""
        target = None
        if cur_fmt and cur_fmt != "Auto":
            target = self._match_printer_value(self._get_format_printer_preference(cur_fmt))
        if not target:
            target = self._pick_printer_by_keywords(("TSC310", "TSC 310", "TSC TE310", "TE310"))
        self.prn_combo.set(target or items[0])

    def _set_default_printer_for_format(self, fmt: str):
        """Выбирает принтер по умолчанию для формата, если он есть в списке."""
        fmt = (fmt or "").strip()
        if not fmt or fmt == "Auto":
            return
        pref = self._get_format_printer_preference(fmt)
        target = self._match_printer_value(pref)
        if target:
            try:
                self.prn_combo.set(target)
            except Exception:
                pass
            return
        preferred = ("TSC310", "TSC 310", "TSC TE310", "TE310")
        target = self._pick_printer_by_keywords(preferred)
        if target:
            try:
                self.prn_combo.set(target)
            except Exception:
                pass

    def _pick_printer_by_keywords(self, keywords):
        values = self._get_printer_values()
        for key in keywords or []:
            for v in values:
                if key.lower() in v.lower():
                    return v
        return None

    def _apply_format_ui(self, fmt: str):
        """Переключает доступность поля партии и дефолтный принтер под выбранный формат."""
        fmt = (fmt or "").strip()
        try:
            if not fmt or fmt == "Auto":
                self.part_entry.configure(state="normal")
            elif self._template_requires_manual_part(fmt):
                self.part_entry.configure(state="normal")
            else:
                self.part_entry.configure(state="disabled")
                self.part_entry.delete(0, "end")
        except Exception:
            pass
        self._set_default_printer_for_format(fmt)

    def _on_format_change(self, value):
        self._apply_format_ui(value)
        self._refresh_runtime_status()
        self._schedule_live_preview()

    def _load_presets(self):
        p16 = self._resolve_template_btw_path("16x16")
        p30 = self._resolve_template_btw_path("30x20")
        if p16 and os.path.isfile(p16):
            self.btw16_entry.insert(0, p16)
        if p30 and os.path.isfile(p30):
            self.btw30_entry.insert(0, p30)

    def _set_catalog_status(self, state, error=""):
        self.catalog_state = state
        self.catalog_error = str(error or "")
        text = "Каталог: доступен" if state == "ready" else "Каталог: недоступен"
        try:
            self.catalog_status_label.configure(text=text)
        except Exception:
            pass
        self._update_action_capabilities()

    def _update_action_capabilities(self):
        available = self._bartender_available() and self.catalog_state == "ready"
        for name in ("preview_btn", "preview_mode_btn", "print_btn", "sticker_reprint_btn"):
            try:
                button = getattr(self, name, None)
                if button:
                    button.configure(state="normal" if available else "disabled")
            except Exception:
                pass

    def _set_print_active_ui(self, active):
        """Enable cancellation only while a print operation is active."""
        try:
            self.cancel_btn.configure(state="normal" if active else "disabled")
        except Exception:
            pass

    def _initialize_catalog_runtime(self):
        result = initialize_catalog_runtime(BASE_DIR)
        self.catalog_store = result.store
        self.catalog_revision = (
            result.snapshot.revision if result.snapshot is not None else None
        )
        self.product_map = result.product_map if result.ready else {}
        self._set_catalog_status(result.state, result.error)
        if result.ready:
            count = len(result.snapshot.products) if result.snapshot else 0
            message = f"Каталог SQLite загружен: {get_catalog_db_path(BASE_DIR)} (товаров={count})"
            if result.migration_source:
                message += f"; миграция из {result.migration_source}"
            if result.duplicate_count:
                message += f"; дубли GTIN: {result.duplicate_count} (последняя строка использована)"
            self.logger.log(message)
            self._update_brand_hint()
        else:
            self.logger.err(result.error)

    def _refresh_catalog_snapshot(self):
        if self._catalog_print_active:
            raise CatalogError("Каталог нельзя обновлять во время печати.")
        if self.catalog_store is None:
            raise CatalogError(self.catalog_error or "Каталог недоступен.")
        try:
            snapshot = self.catalog_store.read_snapshot()
            product_map = build_product_map(
                snapshot.products, has_short_col=snapshot.has_short_col
            )
        except Exception as exc:
            self._set_catalog_status("error", f"Ошибка обновления каталога: {exc}")
            raise
        self.catalog_revision = snapshot.revision
        self.product_map = product_map
        self._set_catalog_status("ready")
        self._update_brand_hint()
        return snapshot

    def _require_catalog_ready(self, action):
        if self.catalog_state == "ready" and self.catalog_store is not None:
            return True
        message = f"{action} недоступен: {self.catalog_error or 'каталог товаров не открыт.'}"
        try:
            self.logger.err(message)
        except Exception:
            pass
        mb.showwarning("Каталог недоступен", message)
        return False

    @staticmethod
    def _catalog_product_to_row(product):
        row = {
            FIELD_TO_HEADER[field]: str(getattr(product, field, "") or "")
            for field in CATALOG_FIELDS
        }
        row["_id"] = product.id
        return row

    @staticmethod
    def _catalog_row_to_values(row):
        return {
            HEADER_TO_FIELD[header]: str(row.get(header, "") or "").strip()
            for header in CATALOG_HEADERS
        }

    def _open_product_catalog(self):
        """Простой редактор каталога товаров (add/edit/delete)."""
        rows = []
        editor_revision = None
        if self.catalog_store is not None and self.catalog_state == "ready":
            try:
                snapshot = self.catalog_store.read_snapshot()
                rows = [self._catalog_product_to_row(item) for item in snapshot.products]
                editor_revision = snapshot.revision
            except Exception as exc:
                self._set_catalog_status("error", f"Ошибка чтения каталога: {exc}")

        win = ctk.CTkToplevel(self)
        win.title("Каталог товаров")
        win.geometry("920x560")
        win.grab_set()

        cols = [
            "GTIN",
            "Наименование",
            "ShortName",
            "Шаблон печати",
            "Бренд",
            "Вес",
            "Срок годности",
            "Разрешительная документация",
            "от",
            "до",
            "Упаковка",
            "Материал",
            "Нанесение кодов",
            "ТН ВЭД",
        ]

        tree_frame = ctk.CTkFrame(win)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=8)

        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=16)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        filter_state = {c: "(Все)" for c in cols}

        for c in cols:
            width = 180 if c == "Разрешительная документация" else 140
            tree.heading(c, text=f"{c} ▼", command=lambda col=c: open_filter_menu(col))
            tree.column(c, width=width, anchor="w")

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        def row_matches(item, state, skip_col=None):
            for c, val in state.items():
                if c == skip_col or val in ("", "(Все)"):
                    continue
                cell = (item.get(c, "") or "")
                if val.lower() not in cell.lower():
                    return False
            return True

        def open_filter_menu(col):
            menu = tk.Menu(tree, tearoff=0)
            try:
                menu.add_command(label="(Все)", command=lambda: set_filter(col, "(Все)"))
                values = set()
                for item in rows:
                    if not row_matches(item, filter_state, skip_col=col):
                        continue
                    cell = (item.get(col, "") or "").strip()
                    if cell:
                        values.add(cell)
                for val in sorted(values, key=lambda v: v.lower()):
                    menu.add_command(label=val, command=lambda v=val: set_filter(col, v))
                menu.tk_popup(tree.winfo_pointerx(), tree.winfo_pointery())
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass

        def set_filter(col, val):
            filter_state[col] = val or "(Все)"
            refresh_tree()

        def update_headings():
            for c in cols:
                suffix = "" if filter_state.get(c, "(Все)") in ("", "(Все)") else f" [{filter_state[c]}]"
                tree.heading(c, text=f"{c} ▼{suffix}", command=lambda col=c: open_filter_menu(col))

        def apply_filters():
            tree.delete(*tree.get_children())
            for i, item in enumerate(rows):
                matched = True
                for c, val in filter_state.items():
                    if val in ("", "(Все)"):
                        continue
                    cell = (item.get(c, "") or "")
                    if val.lower() not in cell.lower():
                        matched = False
                        break
                if matched:
                    tree.insert("", "end", iid=str(i), values=[item.get(c, "") for c in cols])

        def refresh_tree():
            update_headings()
            apply_filters()

        def reload_from_store():
            nonlocal editor_revision
            if self.catalog_store is None:
                rows[:] = []
                editor_revision = None
            else:
                snapshot = self.catalog_store.read_snapshot()
                rows[:] = [self._catalog_product_to_row(item) for item in snapshot.products]
                editor_revision = snapshot.revision
            refresh_tree()

        def handle_catalog_error(exc):
            if isinstance(exc, CatalogConflictError):
                mb.showwarning(
                    "Каталог товаров",
                    "Каталог был изменён. Данные обновлены; повторите действие.",
                )
                try:
                    reload_from_store()
                except Exception as refresh_exc:
                    self._set_catalog_status("error", str(refresh_exc))
                return
            mb.showerror("Каталог товаров", f"Операция не выполнена:\n{exc}")

        def edit_item(existing=None, idx=None):
            dlg = ctk.CTkToplevel(win)
            dlg.title("Добавить товар" if existing is None else "Редактировать товар")
            dlg.transient(win)
            dialog_closed = False

            def close_dialog():
                nonlocal dialog_closed
                if dialog_closed:
                    return
                dialog_closed = True
                try:
                    dlg.grab_release()
                except Exception:
                    pass
                try:
                    dlg.destroy()
                finally:
                    try:
                        if win.winfo_exists():
                            win.grab_set()
                            win.lift()
                            win.focus_force()
                    except Exception:
                        pass

            def activate_dialog():
                if dialog_closed:
                    return
                try:
                    dlg.grab_set()
                    dlg.lift()
                    dlg.focus_force()
                except Exception:
                    pass

            dlg.protocol("WM_DELETE_WINDOW", close_dialog)
            dlg.after(20, activate_dialog)
            screen_h = 760
            try:
                screen_h = max(520, int(dlg.winfo_screenheight() or 760) - 120)
            except Exception:
                pass
            dlg.geometry(f"760x{min(820, screen_h)}")
            try:
                dlg.minsize(680, 520)
            except Exception:
                pass

            outer = ctk.CTkFrame(dlg)
            outer.pack(fill="both", expand=True, padx=10, pady=10)
            form = ctk.CTkScrollableFrame(outer)
            form.pack(fill="both", expand=True)

            entries = {}
            template_label_to_id = {"(не выбран)": ""}
            template_options = ["(не выбран)"]
            for template_id, meta in self._iter_label_templates():
                name = self._template_display_name(template_id)
                label = template_id if not name or name == template_id else f"{template_id} | {name}"
                template_label_to_id[label] = template_id
                template_options.append(label)
            existing_template_id = self._normalize_template_id((existing or {}).get("Шаблон печати", ""))
            if existing_template_id and existing_template_id not in (self.label_templates or {}):
                missing_label = f"{existing_template_id} | не найден"
                template_label_to_id[missing_label] = existing_template_id
                template_options.append(missing_label)

            form.grid_columnconfigure(1, weight=1)
            for i, h in enumerate(CATALOG_HEADERS):
                ctk.CTkLabel(form, text=h, width=180, anchor="w").grid(row=i, column=0, sticky="w", padx=8, pady=4)
                if h == "Шаблон печати":
                    ent = ctk.CTkComboBox(form, width=420, values=template_options, state="readonly")
                    ent.grid(row=i, column=1, sticky="ew", padx=8, pady=4)
                    selected_label = "(не выбран)"
                    if existing_template_id:
                        for label, template_id in template_label_to_id.items():
                            if template_id == existing_template_id:
                                selected_label = label
                                break
                    ent.set(selected_label)
                else:
                    ent = ctk.CTkEntry(form, width=420)
                    ent.grid(row=i, column=1, sticky="ew", padx=8, pady=4)
                    ent.insert(0, (existing or {}).get(h, ""))
                entries[h] = ent

            def on_save():
                gtin = entries["GTIN"].get().strip()
                if not gtin:
                    mb.showerror("Каталог товаров", "GTIN обязателен.")
                    return
                if not gtin.isdigit():
                    mb.showerror("Каталог товаров", "GTIN должен содержать только цифры.")
                    return
                selected_template_label = entries["Шаблон печати"].get()
                template_id = self._normalize_template_id(template_label_to_id.get(selected_template_label, ""))
                if template_id and template_id not in (self.label_templates or {}):
                    mb.showerror(
                        "Каталог товаров",
                        f"Шаблон '{template_id}' не найден в редакторе шаблонов.\n"
                        "Выберите существующий шаблон из списка или очистите поле.",
                    )
                    return
                item = {h: entries[h].get().strip() for h in CATALOG_HEADERS}
                item["Шаблон печати"] = template_id
                if self._catalog_print_active:
                    mb.showwarning("Каталог товаров", "Дождитесь завершения печати.")
                    return
                try:
                    if self.catalog_store is None:
                        raise CatalogError(self.catalog_error or "Каталог недоступен.")
                    values = self._catalog_row_to_values(item)
                    if existing is None:
                        self.catalog_store.create_product(
                            values, expected_revision=editor_revision
                        )
                    else:
                        self.catalog_store.update_product(
                            existing["_id"],
                            values,
                            expected_revision=editor_revision,
                        )
                    self._refresh_catalog_snapshot()
                    reload_from_store()
                    self.logger.log(f"[INFO] Каталог SQLite обновлён (товаров={len(rows)})")
                    close_dialog()
                except Exception as exc:
                    handle_catalog_error(exc)

            btn_frame = ctk.CTkFrame(dlg)
            btn_frame.pack(fill="x", padx=10, pady=(0, 10))
            ctk.CTkButton(btn_frame, text="Сохранить", command=on_save).pack(side="left", padx=6)
            ctk.CTkButton(btn_frame, text="Отмена", command=close_dialog).pack(side="left", padx=6)

        def on_add():
            edit_item()

        def on_edit():
            sel = tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            edit_item(existing=rows[idx], idx=idx)

        def on_delete():
            sel = tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            if not mb.askyesno("Каталог товаров", "Удалить выбранный товар?"):
                return
            if self._catalog_print_active:
                mb.showwarning("Каталог товаров", "Дождитесь завершения печати.")
                return
            try:
                if self.catalog_store is None:
                    raise CatalogError(self.catalog_error or "Каталог недоступен.")
                self.catalog_store.delete_product(
                    rows[idx]["_id"], expected_revision=editor_revision
                )
                self._refresh_catalog_snapshot()
                reload_from_store()
                self.logger.log(f"[INFO] Товар удалён. Всего товаров: {len(rows)}")
            except Exception as exc:
                handle_catalog_error(exc)

        def on_import():
            nonlocal editor_revision
            if self._catalog_print_active:
                mb.showwarning("Каталог товаров", "Дождитесь завершения печати.")
                return
            source = fd.askopenfilename(
                title="Импорт каталога из Excel",
                filetypes=[("Excel", "*.xlsx")],
            )
            if not source:
                return
            try:
                prepared = prepare_excel_import(source)
            except Exception as exc:
                mb.showerror("Импорт каталога", f"Не удалось прочитать файл:\n{exc}")
                return
            warnings = []
            if prepared.duplicates:
                warnings.append(
                    f"Дубли GTIN: {len(prepared.duplicates)} (используется последняя строка)"
                )
            if prepared.unknown_headers:
                warnings.append(
                    "Неизвестные колонки: " + ", ".join(prepared.unknown_headers)
                )
            if prepared.errors:
                details = "\n".join(issue.message for issue in prepared.errors[:10])
                mb.showerror(
                    "Импорт каталога",
                    f"Файл не прошёл проверку. Изменений нет.\n\n{details}",
                )
                return
            try:
                if self.catalog_store is None:
                    db_path = get_catalog_db_path(BASE_DIR)
                    if db_path.exists():
                        raise CatalogError(
                            "Существующий catalog.sqlite3 недоступен. "
                            "Импорт не будет заменять его автоматически."
                        )
                    preview = None
                else:
                    preview = self.catalog_store.preview_import(
                        prepared.records,
                        present_fields=prepared.present_fields,
                        has_short_col=prepared.has_short_col,
                        expected_revision=editor_revision,
                    )
            except Exception as exc:
                handle_catalog_error(exc)
                return
            lines = [
                f"Файл: {prepared.source_path.name}",
                f"Валидных товаров: {len(prepared.records)}",
            ]
            if preview is not None:
                lines.extend(
                    (
                        f"Будет добавлено: {preview.added}",
                        f"Будет обновлено: {preview.updated}",
                        f"Без изменений: {preview.unchanged}",
                    )
                )
            else:
                lines.append(f"Будет добавлено: {len(prepared.records)}")
            lines.extend(warnings)
            lines.append("\nПрименить импорт?")
            if not mb.askyesno("Импорт каталога", "\n".join(lines)):
                return
            try:
                if self.catalog_store is None:
                    result = install_prepared_catalog(prepared, get_catalog_db_path(BASE_DIR))
                    if not result.ready:
                        raise CatalogError(result.error)
                    self.catalog_store = result.store
                    self.product_map = result.product_map
                    self.catalog_revision = result.snapshot.revision
                    self._set_catalog_status("ready")
                else:
                    apply_prepared_import(
                        self.catalog_store,
                        prepared,
                        expected_revision=editor_revision,
                    )
                    self._refresh_catalog_snapshot()
                reload_from_store()
                mb.showinfo("Импорт каталога", "Импорт успешно завершён.")
            except Exception as exc:
                handle_catalog_error(exc)

        def on_export():
            if self.catalog_store is None or self.catalog_state != "ready":
                mb.showwarning(
                    "Экспорт каталога", self.catalog_error or "Каталог недоступен."
                )
                return
            path = fd.asksaveasfilename(
                title="Экспорт каталога в Excel",
                defaultextension=".xlsx",
                filetypes=[("Excel", "*.xlsx")],
                initialfile="product_catalog_export.xlsx",
            )
            if not path:
                return
            try:
                snapshot = self.catalog_store.read_snapshot()
                export_catalog_xlsx(snapshot.products, path)
                mb.showinfo("Экспорт каталога", f"Каталог экспортирован:\n{path}")
            except Exception as exc:
                mb.showerror("Экспорт каталога", f"Экспорт не выполнен:\n{exc}")

        btns = ctk.CTkFrame(win)
        btns.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(btns, text="Добавить", command=on_add).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Редактировать", command=on_edit).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Удалить", command=on_delete).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Импорт Excel", command=on_import).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Экспорт Excel", command=on_export).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Закрыть", command=win.destroy).pack(side="right", padx=4)

        refresh_tree()

    def _build_report_catalog_map(self):
        if not self._require_catalog_ready("Формирование отчёта"):
            return None
        try:
            snapshot = self.catalog_store.read_snapshot()
            return {
                product.gtin: self._catalog_product_to_row(product)
                for product in snapshot.products
            }
        except Exception as exc:
            self._set_catalog_status("error", f"Ошибка чтения каталога: {exc}")
            mb.showerror("Отчет", self.catalog_error)
            return None

    def _open_turnover_report_dialog(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Отчет о вводе в оборот")
        dlg.geometry("420x180")
        try:
            dlg.resizable(False, False)
        except Exception:
            pass
        try:
            dlg.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(dlg, text="Сформировать отчет о вводе в оборот.").pack(padx=12, pady=(12, 6), anchor="w")

        separate_var = tk.BooleanVar(master=dlg, value=False)
        chk = ctk.CTkCheckBox(dlg, text="Отдельные файлы (отчет для каждого файла)", variable=separate_var)
        chk.pack(padx=12, pady=(0, 6), anchor="w")

        btns = ctk.CTkFrame(dlg)
        btns.pack(fill="x", padx=12, pady=(8, 12))

        def choose_files():
            try:
                dlg.grab_release()
            except Exception:
                pass
            init_dir = self.csv_dir if os.path.isdir(self.csv_dir) else BASE_DIR
            paths = fd.askopenfilenames(
                title="Выбери файлы для отчета",
                filetypes=[("CSV/TSV", "*.csv;*.tsv;*.txt"), ("Все файлы", "*.*")],
                initialdir=init_dir
            )
            if not paths:
                try:
                    dlg.grab_set()
                except Exception:
                    pass
                return
            dlg.destroy()
            self._create_turnover_report(list(paths), separate_files=self._bool_var(separate_var, False))

        ctk.CTkButton(btns, text="Выбрать файлы", command=choose_files).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Отмена", command=dlg.destroy).pack(side="left", padx=6)

    def _report_filename_for_input(self, input_path):
        base = os.path.splitext(os.path.basename(input_path or ""))[0]
        if not base:
            base = "report"
        return f"{base}_vvod_v_oborot.xlsx"

    def _get_report_initial_dir(self, input_paths):
        saved = ""
        try:
            saved = (self.cfg.get("report_dir") or "").strip()
        except Exception:
            saved = ""
        if saved and os.path.isdir(saved):
            return saved
        if input_paths:
            p = os.path.dirname(input_paths[0])
            if os.path.isdir(p):
                return p
        return BASE_DIR

    def _remember_report_dir(self, path):
        try:
            if not path:
                return
            self.cfg["report_dir"] = path
            save_config(self.cfg)
        except Exception:
            pass

    def _build_turnover_report_rows(self, input_rows, catalog_map):
        def _clean(v):
            if v is None:
                return ""
            s = str(v)
            try:
                s = XLSX_ILLEGAL_RE.sub("", s)
            except Exception:
                pass
            return s.strip()

        def _format_doc_date(v):
            if v is None:
                return ""
            if isinstance(v, dt.datetime):
                return v.date().strftime("%Y-%m-%d")
            if isinstance(v, dt.date):
                return v.strftime("%Y-%m-%d")
            s = _clean(v)
            if not s:
                return ""
            m = re.match(r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})", s)
            if m:
                try:
                    y, mo, d = (int(x) for x in m.groups())
                    return dt.date(y, mo, d).strftime("%Y-%m-%d")
                except Exception:
                    pass
            m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
            if m:
                try:
                    d, mo, y = (int(x) for x in m.groups())
                    return dt.date(y, mo, d).strftime("%Y-%m-%d")
                except Exception:
                    pass
            return s

        out = []
        missing = 0
        for r in input_rows or []:
            km = _clean(r.get("DM", ""))
            gtin = _clean(r.get("GTIN", ""))
            name = _clean(r.get("NAME", ""))
            gkey = only_digits(gtin)
            cat = catalog_map.get(gkey) if gkey else None
            if gkey and not cat:
                missing += 1
            tnved = _clean(cat.get("ТН ВЭД")) if cat else ""
            doc_num = _clean(cat.get("Разрешительная документация")) if cat else ""
            doc_date = _format_doc_date(cat.get("от")) if cat else ""
            out.append([km, gtin, name, tnved, REPORT_TURNOVER_DOC_TYPE, doc_num, doc_date])
        return out, missing

    def _write_turnover_report_xlsx(self, path, rows):
        try:
            from openpyxl import Workbook
        except Exception:
            mb.showerror("Отчет", "Не установлена библиотека openpyxl (нужна для отчета).")
            return False
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except Exception:
            pass
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Ввод в оборот"
            ws.append(REPORT_TURNOVER_HEADERS)
            for row in rows:
                ws.append(row)
            wb.save(path)
            return True
        except Exception as e:
            mb.showerror("Отчет", f"Не удалось сохранить файл:\n{e}")
            return False

    def _create_turnover_report(self, input_paths, separate_files=False):
        paths = [p for p in (input_paths or []) if p]
        if not paths:
            return
        catalog_map = self._build_report_catalog_map()
        if catalog_map is None:
            return
        if not catalog_map:
            try:
                mb.showwarning("Отчет", "Каталог товаров пуст. Поля ТН ВЭД и документа будут пустыми.")
            except Exception:
                pass

        if separate_files and len(paths) > 1:
            init_dir = self._get_report_initial_dir(paths)
            out_dir = fd.askdirectory(title="Папка для отчетов", initialdir=init_dir)
            if not out_dir:
                return
            self._remember_report_dir(out_dir)
            ok_files = 0
            total_rows = 0
            total_missing = 0
            for p in paths:
                try:
                    rows_in = load_kontur_raw(p)
                except Exception as e:
                    try:
                        self.logger.err(f"Отчет: не удалось прочитать {p}: {e}")
                    except Exception:
                        pass
                    continue
                if not rows_in:
                    try:
                        self.logger.err(f"Отчет: файл пуст: {p}")
                    except Exception:
                        pass
                    continue
                report_rows, missing = self._build_turnover_report_rows(rows_in, catalog_map)
                if not report_rows:
                    continue
                out_path = os.path.join(out_dir, self._report_filename_for_input(p))
                if self._write_turnover_report_xlsx(out_path, report_rows):
                    ok_files += 1
                    total_rows += len(report_rows)
                    total_missing += missing
                    try:
                        self.logger.log(f"Отчет создан: {out_path} (строк={len(report_rows)})")
                    except Exception:
                        pass
            if ok_files <= 0:
                mb.showerror("Отчет", "Не удалось сформировать отчеты (нет строк или ошибка чтения).")
                return
            if total_missing:
                try:
                    self.logger.log(f"Отчет: не найдено в каталоге GTIN: {total_missing}")
                except Exception:
                    pass
            mb.showinfo("Отчет", f"Готово. Создано отчетов: {ok_files}.\nПапка: {out_dir}")
            return

        init_dir = self._get_report_initial_dir(paths)
        default_name = self._report_filename_for_input(paths[0]) if len(paths) == 1 else "vvod_v_oborot_report.xlsx"
        out_path = fd.asksaveasfilename(
            title="Сохранить отчет",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialdir=init_dir,
            initialfile=default_name
        )
        if not out_path:
            return
        self._remember_report_dir(os.path.dirname(out_path))

        combined_rows = []
        total_missing = 0
        for p in paths:
            try:
                rows_in = load_kontur_raw(p)
            except Exception as e:
                try:
                    self.logger.err(f"Отчет: не удалось прочитать {p}: {e}")
                except Exception:
                    pass
                continue
            if not rows_in:
                try:
                    self.logger.err(f"Отчет: файл пуст: {p}")
                except Exception:
                    pass
                continue
            report_rows, missing = self._build_turnover_report_rows(rows_in, catalog_map)
            combined_rows.extend(report_rows)
            total_missing += missing

        if not combined_rows:
            mb.showerror("Отчет", "Нет строк для отчета.")
            return
        if self._write_turnover_report_xlsx(out_path, combined_rows):
            try:
                self.logger.log(f"Отчет создан: {out_path} (строк={len(combined_rows)})")
            except Exception:
                pass
            if total_missing:
                try:
                    self.logger.log(f"Отчет: не найдено в каталоге GTIN: {total_missing}")
                except Exception:
                    pass
            mb.showinfo("Отчет", f"Отчет создан:\n{out_path}\nСтрок: {len(combined_rows)}")

    # --- CRPT (GIS MT) code status checker ---

    def _open_crpt_checker_window(self):
        try:
            existing = getattr(self, "_crpt_win", None)
            if existing and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass

        win = ctk.CTkToplevel(self)
        self._crpt_win = win
        win.title("Проверка кодов (ГИС МТ)")
        win.geometry("980x640")
        try:
            win.minsize(760, 520)
        except Exception:
            pass
        try:
            win.transient(self)
            win.lift()
            win.attributes("-topmost", True)
            win.after(150, lambda: win.attributes("-topmost", False))
            win.focus_force()
        except Exception:
            pass

        status_row = ctk.CTkFrame(win, corner_radius=12)
        status_row.pack(fill="x", padx=12, pady=(12, 6))
        self._crpt_ind_crypto_dot = ctk.CTkLabel(status_row, text="●", font=("Arial", 14))
        self._crpt_ind_crypto_dot.pack(side="left", padx=(8, 4), pady=6)
        self._crpt_ind_crypto_label = ctk.CTkLabel(status_row, text="CryptoPro")
        self._crpt_ind_crypto_label.pack(side="left", padx=(0, 12), pady=6)
        self._crpt_ind_cert_dot = ctk.CTkLabel(status_row, text="●", font=("Arial", 14))
        self._crpt_ind_cert_dot.pack(side="left", padx=(0, 4), pady=6)
        self._crpt_ind_cert_label = ctk.CTkLabel(status_row, text="Сертификат")
        self._crpt_ind_cert_label.pack(side="left", padx=(0, 12), pady=6)
        self._crpt_ind_token_dot = ctk.CTkLabel(status_row, text="●", font=("Arial", 14))
        self._crpt_ind_token_dot.pack(side="left", padx=(0, 4), pady=6)
        self._crpt_ind_token_label = ctk.CTkLabel(status_row, text="Токен")
        self._crpt_ind_token_label.pack(side="left", padx=(0, 12), pady=6)
        self._crpt_conn_dot = ctk.CTkLabel(status_row, text="●", font=("Arial", 14))
        self._crpt_conn_dot.pack(side="left", padx=(0, 4), pady=6)
        self._crpt_conn_label = ctk.CTkLabel(status_row, text="ГИС МТ")
        self._crpt_conn_label.pack(side="left", padx=(0, 12), pady=6)
        self._crpt_health_label = ctk.CTkLabel(status_row, text="Статус: -")
        self._crpt_health_label.pack(side="right", padx=10, pady=6)

        input_block = ctk.CTkFrame(win, corner_radius=12)
        input_block.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(input_block, text="Коды (по одному в строке):").pack(anchor="w", padx=10, pady=(8, 4))
        self._crpt_input_text = ctk.CTkTextbox(input_block, height=120)
        self._crpt_input_text.pack(fill="x", padx=10, pady=(0, 8))
        for seq in ("<Control-c>", "<Control-C>"):
            self._crpt_input_text.bind(seq, self._crpt_input_copy)
        for seq in ("<Control-v>", "<Control-V>"):
            self._crpt_input_text.bind(seq, self._crpt_input_paste)
        for seq in ("<Control-x>", "<Control-X>"):
            self._crpt_input_text.bind(seq, self._crpt_input_cut)
        input_actions = ctk.CTkFrame(input_block, fg_color="transparent")
        input_actions.pack(fill="x", padx=10, pady=(0, 8))
        self._crpt_btn_from_csv = ctk.CTkButton(
            input_actions, text="Из CSV файла", command=self._crpt_import_from_csv, width=140
        )
        self._crpt_btn_from_csv.pack(side="left", padx=6)
        self._crpt_btn_from_current = ctk.CTkButton(
            input_actions, text="Из текущего CSV", command=self._crpt_import_from_current_csv, width=160
        )
        self._crpt_btn_from_current.pack(side="left", padx=6)

        opts = ctk.CTkFrame(win, corner_radius=12)
        opts.pack(fill="x", padx=12, pady=(0, 8))
        self._crpt_dedupe_var = tk.BooleanVar(master=win, value=True)
        ctk.CTkCheckBox(opts, text="Удалять дубли", variable=self._crpt_dedupe_var).pack(side="left", padx=10, pady=8)
        self._crpt_use_info_var = tk.BooleanVar(master=win, value=False)
        ctk.CTkCheckBox(
            opts, text="Расширенный запрос (info)", variable=self._crpt_use_info_var
        ).pack(side="left", padx=10, pady=8)
        ctk.CTkLabel(opts, text="GS (0x1D) допускается как \\u001d").pack(side="left", padx=10, pady=8)

        btns = ctk.CTkFrame(win, corner_radius=12)
        btns.pack(fill="x", padx=12, pady=(0, 8))
        self._crpt_btn_check = ctk.CTkButton(btns, text="Проверить", command=self._crpt_run_check, width=120)
        self._crpt_btn_check.pack(side="left", padx=8, pady=8)
        self._crpt_btn_connect = ctk.CTkButton(
            btns, text="Подключиться к ГИС МТ", command=self._crpt_connect, width=190
        )
        self._crpt_btn_connect.pack(side="left", padx=8, pady=8)
        self._crpt_btn_clear = ctk.CTkButton(btns, text="Очистить", command=self._crpt_clear_results, width=100)
        self._crpt_btn_clear.pack(side="left", padx=8, pady=8)
        self._crpt_btn_copy_details = ctk.CTkButton(
            btns, text="Скопировать детали", command=self._crpt_copy_error_details, state="disabled", width=160
        )
        self._crpt_btn_copy_details.pack(side="left", padx=8, pady=8)
        self._crpt_status_label = ctk.CTkLabel(btns, text="Готово")
        self._crpt_status_label.pack(side="right", padx=10, pady=8)

        health_actions = ctk.CTkFrame(win, corner_radius=12)
        health_actions.pack(fill="x", padx=12, pady=(0, 8))
        self._crpt_btn_sign_settings = ctk.CTkButton(
            health_actions, text="Выбор подписи", command=self._crpt_open_sign_settings, width=170
        )
        self._crpt_btn_sign_settings.pack(side="left", padx=8, pady=8)
        self._crpt_btn_refresh_token = ctk.CTkButton(
            health_actions, text="Обновить токен", command=self._crpt_refresh_token, width=140
        )
        self._crpt_btn_refresh_token.pack(side="left", padx=8, pady=8)


        diag = ctk.CTkFrame(win, corner_radius=12)
        diag.pack(fill="x", padx=12, pady=(0, 8))
        self._crpt_diag_label = ctk.CTkLabel(diag, text="Диагностика: -", anchor="w", justify="left")
        self._crpt_diag_label.pack(fill="x", padx=10, pady=6)

        table_block = ctk.CTkFrame(win, corner_radius=12)
        table_block.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        cols = ("normalized", "status", "explain")
        self._crpt_tree = ttk.Treeview(table_block, columns=cols, show="headings", height=16)
        vsb = ttk.Scrollbar(table_block, orient="vertical", command=self._crpt_tree.yview)
        self._crpt_tree.configure(yscrollcommand=vsb.set)

        headings = {
            "normalized": "Normalized",
            "status": "status",
            "explain": "explain",
        }
        widths = {
            "normalized": 360,
            "status": 110,
            "explain": 420,
        }
        for c in cols:
            self._crpt_tree.heading(c, text=headings.get(c, c))
            self._crpt_tree.column(c, width=widths.get(c, 140), anchor="w")

        self._crpt_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        table_block.grid_rowconfigure(0, weight=1)
        table_block.grid_columnconfigure(0, weight=1)

        try:
            self._crpt_tree.tag_configure("ok", foreground="#198754")
            self._crpt_tree.tag_configure("warn", foreground="#d39e00")
            self._crpt_tree.tag_configure("error", foreground="#dc3545")
            self._crpt_tree.tag_configure("info", foreground="#0d6efd")
        except Exception:
            pass

        self._crpt_raw_map = {}
        self._crpt_error_details = ""
        self._crpt_tree.bind("<Double-1>", lambda e: self._crpt_show_raw_for_selected())
        self._crpt_connected = False
        self._crpt_set_connection_state("disconnected")
        self._crpt_refresh_health()
        try:
            self.after(200, self._crpt_auto_refresh_token_on_open)
        except Exception:
            pass

    def _crpt_display_code(self, value):
        try:
            s = str(value or "")
            s = re.sub(r"\\+u001d", "[GS]", s, flags=re.IGNORECASE)
            s = re.sub(r"\\+x1d", "[GS]", s, flags=re.IGNORECASE)
            return s.replace(chr(29), "[GS]")
        except Exception:
            return value or ""

    def _crpt_format_raw(self, raw):
        if raw is None:
            return "", ""
        try:
            full = json.dumps(raw, ensure_ascii=False, indent=2)
        except Exception:
            full = str(raw)
        preview = full.replace("\r", " ").replace("\n", " ")
        if len(preview) > 160:
            preview = preview[:157] + "..."
        return preview, full

    def _crpt_set_connection_state(self, state: str, message: str | None = None):
        colors = {
            "connected": "#198754",
            "checking": "#ffc107",
            "disconnected": "#6c757d",
            "error": "#dc3545",
        }
        color = colors.get(state, "#6c757d")
        try:
            if getattr(self, "_crpt_conn_dot", None):
                self._crpt_conn_dot.configure(text_color=color)
        except Exception:
            pass
        try:
            self._crpt_connected = (state == "connected")
        except Exception:
            pass
        if message:
            try:
                self._crpt_status_label.configure(text=message)
            except Exception:
                pass

    def _crpt_set_indicator(self, dot, label, state: str, text: str):
        colors = {
            "ok": "#198754",
            "warn": "#ffc107",
            "error": "#dc3545",
            "off": "#6c757d",
        }
        color = colors.get(state, "#6c757d")
        try:
            if dot:
                dot.configure(text_color=color)
        except Exception:
            pass
        try:
            if label:
                label.configure(text=text)
        except Exception:
            pass

    def _crpt_log_error(self, message: str, details: str | None = None):
        logger = getattr(self, "logger", None)
        if not logger:
            return
        key = (str(message), str(details or ""))
        if getattr(self, "_crpt_last_log_error", None) == key:
            return
        self._crpt_last_log_error = key
        try:
            logger.err(f"[CRPT] {message}")
        except Exception:
            return
        if details:
            det = str(details).replace("\r", " ").replace("\n", " ")
            if len(det) > 800:
                det = det[:797] + "..."
            try:
                logger.err(f"[CRPT] Детали: {det}")
            except Exception:
                pass

    def _crpt_set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for btn in (getattr(self, "_crpt_btn_check", None),
                    getattr(self, "_crpt_btn_clear", None),
                    getattr(self, "_crpt_btn_connect", None),
                    getattr(self, "_crpt_btn_from_csv", None),
                    getattr(self, "_crpt_btn_from_current", None),
                    getattr(self, "_crpt_btn_sign_settings", None),
                    getattr(self, "_crpt_btn_refresh_token", None)):
            try:
                if btn:
                    btn.configure(state=state)
            except Exception:
                pass
        try:
            if busy:
                self._crpt_status_label.configure(text="Проверка...")
        except Exception:
            pass

    def _crpt_append_codes(self, codes, clear=False):
        box = getattr(self, "_crpt_input_text", None)
        if not box:
            return
        try:
            if clear:
                box.delete("1.0", "end")
        except Exception:
            pass
        text = "\n".join([c for c in codes if c])
        if not text:
            return
        try:
            existing = box.get("1.0", "end-1c").strip()
        except Exception:
            existing = ""
        try:
            if existing:
                box.insert("end", "\n" + text)
            else:
                box.insert("end", text)
        except Exception:
            pass

    def _crpt_input_copy(self, event=None):
        box = getattr(self, "_crpt_input_text", None)
        if not box:
            return "break"
        try:
            text = box.get("sel.first", "sel.last")
        except Exception:
            text = ""
        if not text:
            return "break"
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass
        return "break"

    def _crpt_input_paste(self, event=None):
        box = getattr(self, "_crpt_input_text", None)
        if not box:
            return "break"
        try:
            data = self.clipboard_get()
        except Exception:
            data = ""
        if not data:
            return "break"
        try:
            box.insert("insert", data)
        except Exception:
            try:
                box.insert("end", data)
            except Exception:
                pass
        return "break"

    def _crpt_input_cut(self, event=None):
        box = getattr(self, "_crpt_input_text", None)
        if not box:
            return "break"
        try:
            text = box.get("sel.first", "sel.last")
        except Exception:
            text = ""
        if not text:
            return "break"
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass
        try:
            box.delete("sel.first", "sel.last")
        except Exception:
            pass
        return "break"

    def _crpt_paste_to_input(self):
        try:
            data = self.clipboard_get()
        except Exception:
            data = ""
        if not data:
            return
        box = getattr(self, "_crpt_input_text", None)
        if not box:
            return
        try:
            box.insert("insert", data)
        except Exception:
            try:
                box.insert("end", data)
            except Exception:
                pass

    def _crpt_copy_input_codes(self):
        box = getattr(self, "_crpt_input_text", None)
        if not box:
            return
        try:
            text = box.get("sel.first", "sel.last")
        except Exception:
            try:
                text = box.get("1.0", "end-1c")
            except Exception:
                text = ""
        dedupe = self._bool_var(getattr(self, "_crpt_dedupe_var", False), False)
        codes = parse_codes(text or "", dedupe=dedupe)
        if not codes:
            mb.showinfo("Проверка кодов", "Список кодов пуст.")
            return
        out = "\n".join(codes)
        try:
            self.clipboard_clear()
            self.clipboard_append(out)
        except Exception:
            pass
        try:
            if getattr(self, "_crpt_status_label", None):
                self._crpt_status_label.configure(text=f"Скопировано кодов: {len(codes)}")
        except Exception:
            pass

    def _crpt_import_from_csv(self):
        init_dir = self.csv_dir if os.path.isdir(self.csv_dir) else BASE_DIR
        p = fd.askopenfilename(
            title="Выбери kontur.csv/tsv",
            filetypes=[("CSV/TSV", "*.csv;*.tsv;*.txt"), ("Все файлы", "*.*")],
            initialdir=init_dir
        )
        if not p:
            return
        try:
            rows = load_kontur_raw(p)
        except Exception as e:
            mb.showerror("Проверка кодов", f"Не удалось прочитать CSV:\n{e}")
            return
        codes = [r.get("DM", "") for r in rows if r.get("DM")]
        if not codes:
            mb.showinfo("Проверка кодов", "Коды не найдены в выбранном CSV.")
            return
        self._crpt_append_codes(codes, clear=False)
        try:
            self._crpt_status_label.configure(text=f"Добавлено кодов: {len(codes)}")
        except Exception:
            pass

    def _crpt_import_from_current_csv(self):
        rows = []
        try:
            if getattr(self, "csv_rows", None):
                rows = list(self.csv_rows)
        except Exception:
            rows = []
        if not rows:
            try:
                p = getattr(self, "csv_path", "") or ""
            except Exception:
                p = ""
            if p and os.path.isfile(p):
                try:
                    rows = load_kontur_raw(p)
                except Exception:
                    rows = []
        if not rows:
            mb.showinfo("Проверка кодов", "Текущий CSV не выбран или пуст.")
            return
        codes = [r.get("DM", "") for r in rows if r.get("DM")]
        if not codes:
            mb.showinfo("Проверка кодов", "Коды не найдены в текущем CSV.")
            return
        self._crpt_append_codes(codes, clear=False)
        try:
            self._crpt_status_label.configure(text=f"Добавлено кодов: {len(codes)}")
        except Exception:
            pass

    def _crpt_apply_connection_result(self, ok, message, severity, details):
        state = "connected" if ok else "error"
        self._crpt_set_connection_state(state, message)
        if not ok:
            try:
                self._crpt_log_error(f"Подключение не удалось: {message}", details)
            except Exception:
                pass
        if details:
            try:
                self._crpt_error_details += f"{details}\n"
            except Exception:
                pass
            try:
                if getattr(self, "_crpt_btn_copy_details", None):
                    self._crpt_btn_copy_details.configure(state="normal")
            except Exception:
                pass

    def _crpt_apply_health(self, health, certs, err=None):
        if err:
            try:
                mb.showerror("Проверка кодов", f"{err.user_message if hasattr(err,'user_message') else err}")
            except Exception:
                pass
            try:
                self._crpt_log_error(
                    getattr(err, "user_message", None) or str(err),
                    getattr(err, "details", None),
                )
            except Exception:
                pass
        crypto_ok = bool(health.get("cryptoProAvailable"))
        cert_selected = bool(health.get("selectedCert"))
        cert_ok = bool(health.get("certOk"))
        token_present = bool(health.get("tokenPresent"))
        seconds_left = health.get("secondsLeft")
        sign_ok = bool(health.get("signTestOk"))
        base_url = str(health.get("baseUrl") or "-")
        pg = str(health.get("pg") or "-")
        auth_mode = str(health.get("authMode") or "-")
        token_len = int(health.get("tokenLen") or 0)
        token_jwt_dots = int(health.get("tokenJwtDots") or 0)
        token_type = str(health.get("tokenType") or "")
        token_received_at = str(health.get("tokenReceivedAt") or "-")
        selected_cert = health.get("selectedCert") or {}

        if not crypto_ok:
            state = "error"
        elif not cert_selected:
            state = "disconnected"
        elif not cert_ok:
            state = "error"
        elif token_present:
            if seconds_left is None or seconds_left > 0:
                state = "connected"
            else:
                state = "error"
        else:
            state = "checking"
        try:
            self._crpt_set_connection_state(state)
        except Exception:
            pass
        try:
            crypto_text = "CryptoPro: ok" if crypto_ok else "CryptoPro: нет"
            self._crpt_set_indicator(
                getattr(self, "_crpt_ind_crypto_dot", None),
                getattr(self, "_crpt_ind_crypto_label", None),
                "ok" if crypto_ok else "error",
                crypto_text,
            )
            if not cert_selected:
                cert_state = "warn"
                cert_text = "Сертификат: не выбран"
            elif cert_ok:
                cert_state = "ok"
                cert_text = "Сертификат: ok"
            else:
                cert_state = "error"
                cert_text = "Сертификат: нет ключа"
            self._crpt_set_indicator(
                getattr(self, "_crpt_ind_cert_dot", None),
                getattr(self, "_crpt_ind_cert_label", None),
                cert_state,
                cert_text,
            )
            if token_present:
                if isinstance(seconds_left, int):
                    if seconds_left > 0:
                        mins = max(0, seconds_left // 60)
                        token_text = f"Токен: {mins} мин"
                    else:
                        token_text = "Токен: истек"
                else:
                    token_text = "Токен: есть"
                token_state = "ok" if (seconds_left is None or seconds_left > 0) else "error"
            else:
                token_text = "Токен: нет"
                token_state = "warn"
            self._crpt_set_indicator(
                getattr(self, "_crpt_ind_token_dot", None),
                getattr(self, "_crpt_ind_token_label", None),
                token_state,
                token_text,
            )

            label_parts = []
            if sign_ok:
                label_parts.append("Подпись: ok")
            if health.get("lastError"):
                label_parts.append(f"Ошибка: {health.get('lastError')}")
            if not label_parts:
                label_parts.append("Статус: -")
            if getattr(self, "_crpt_health_label", None):
                self._crpt_health_label.configure(text=" | ".join(label_parts))

            def _short_tp(tp: str) -> str:
                s = tp or ""
                return s if len(s) <= 12 else f"{s[:6]}...{s[-6:]}"

            cert_text = "не выбран"
            if selected_cert:
                subject = str(selected_cert.get("subject") or "")
                valid_to = str(selected_cert.get("validTo") or "")
                tp_short = _short_tp(str(selected_cert.get("thumbprint") or ""))
                parts = [p for p in (subject, f"до {valid_to}" if valid_to else "", tp_short) if p]
                cert_text = " | ".join(parts) if parts else "выбран"

            if token_present:
                if token_type == "jwt" and isinstance(seconds_left, int):
                    mins = max(0, seconds_left // 60)
                    token_line = f"Токен: len={token_len} dots={token_jwt_dots} получен={token_received_at} осталось={mins} мин"
                else:
                    token_kind = token_type or "opaque"
                    token_line = f"Токен: len={token_len} dots={token_jwt_dots} получен={token_received_at} тип={token_kind}"
            else:
                token_line = "Токен: нет"
            line1 = f"API: {base_url} | pg: {pg} | auth: {auth_mode} | CryptoPro: {'ok' if crypto_ok else 'нет'} | Подпись: {'ok' if sign_ok else 'нет'}"
            line2 = f"Сертификат: {cert_text}"
            diag_text = f"{line1}\n{line2}\n{token_line}"
            if getattr(self, "_crpt_diag_label", None):
                self._crpt_diag_label.configure(text=diag_text)
        except Exception:
            pass
        last_err = health.get("lastError")
        if last_err:
            prev = getattr(self, "_crpt_last_health_error", None)
            if prev != last_err:
                self._crpt_last_health_error = last_err
                self._crpt_log_error(str(last_err))
        else:
            self._crpt_last_health_error = None

    def _crpt_refresh_health(self, sign_test=False, refresh_token=False, check_connection=False):
        self._crpt_set_busy(True)
        use_info = self._bool_var(getattr(self, "_crpt_use_info_var", False), False) if check_connection else False

        def worker():
            tm = get_token_manager()
            err = None
            if refresh_token:
                try:
                    tm.force_refresh_sync()
                except CrptTokenError as e:
                    err = e
            health, certs = tm.health_sync(sign_test=sign_test)
            self.after(0, lambda: self._crpt_apply_health(health, certs, err))
            if check_connection and not err:
                ok, msg, severity, details = crpt_check_connection_sync(use_info=use_info)
                self.after(0, lambda: self._crpt_apply_connection_result(ok, msg, severity, details))
            self.after(0, lambda: self._crpt_set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _crpt_auto_refresh_token_on_open(self):
        def worker():
            settings = load_crpt_settings()
            if not settings.cert_thumbprint:
                return
            if not (settings.oms_connection or settings.token_provider_inn):
                return
            tm = get_token_manager()
            err = None
            try:
                tm.get_token_sync()
            except CrptTokenError as e:
                err = e
            health, certs = tm.health_sync(sign_test=False)
            self.after(0, lambda: self._crpt_apply_health(health, certs, None))
            if err:
                msg = getattr(err, "user_message", None) or str(err)
                try:
                    if getattr(self, "logger", None):
                        self.after(0, lambda m=msg: self.logger.err(f"[CRPT] Автообновление токена не удалось: {m}"))
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _crpt_open_sign_settings(self):
        try:
            existing = getattr(self, "_crpt_sign_win", None)
            if existing and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass

        win = ctk.CTkToplevel(self)
        self._crpt_sign_win = win
        win.title("Настройка подписи")
        win.geometry("920x540")
        try:
            parent = getattr(self, "_crpt_win", None)
            if parent and parent.winfo_exists():
                win.transient(parent)
            else:
                win.transient(self)
            win.lift()
            win.attributes("-topmost", True)
            win.after(150, lambda: win.attributes("-topmost", False))
            win.focus_force()
        except Exception:
            pass

        settings = load_crpt_settings()
        env_oms = (os.environ.get("CRPT_OMS_CONNECTION") or "").strip()
        env_cert = (os.environ.get("CRPT_CERT_THUMBPRINT") or "").strip()

        top = ctk.CTkFrame(win, corner_radius=12)
        top.pack(fill="x", padx=12, pady=(12, 8))
        ctk.CTkLabel(top, text="OMS connection (UUID):").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        oms_entry = ctk.CTkEntry(top, width=420)
        oms_entry.grid(row=0, column=1, sticky="w", padx=8, pady=8)
        if settings.oms_connection:
            oms_entry.insert(0, settings.oms_connection)

        def save_oms():
            value = (oms_entry.get() or "").strip()
            self.cfg["crpt_oms_connection"] = value
            save_config(self.cfg)
            if not env_oms:
                set_runtime_overrides(oms_connection=value)
            self._crpt_refresh_health()

        ctk.CTkButton(top, text="Сохранить", command=save_oms, width=120).grid(row=0, column=2, sticky="w", padx=8, pady=8)
        hint_text = "Приоритет: .env" if env_oms else "Источник: config.json"
        ctk.CTkLabel(top, text=hint_text).grid(row=1, column=1, sticky="w", padx=8, pady=(0, 8))
        ctk.CTkLabel(top, text="Выберите сертификат УКЭП (CurrentUser\\My).").grid(
            row=2, column=0, sticky="w", padx=8, pady=(0, 8)
        )

        cert_frame = ctk.CTkFrame(win, corner_radius=12)
        cert_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        cols = ("subject", "valid_to", "thumbprint", "key")
        tree = ttk.Treeview(cert_frame, columns=cols, show="headings", height=12)
        vsb = ttk.Scrollbar(cert_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.heading("subject", text="Subject")
        tree.heading("valid_to", text="ValidTo")
        tree.heading("thumbprint", text="Thumbprint")
        tree.heading("key", text="Key")
        tree.column("subject", width=420, anchor="w")
        tree.column("valid_to", width=140, anchor="w")
        tree.column("thumbprint", width=200, anchor="w")
        tree.column("key", width=60, anchor="center")
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        cert_frame.grid_rowconfigure(0, weight=1)
        cert_frame.grid_columnconfigure(0, weight=1)
        try:
            tree.tag_configure("cert_ok", foreground="#198754")
            tree.tag_configure("cert_expired", foreground="#dc3545")
        except Exception:
            pass

        self._crpt_sign_certs = []

        def _short_tp(tp: str) -> str:
            s = tp or ""
            return s if len(s) <= 12 else f"{s[:6]}...{s[-6:]}"

        def _parse_cert_date(value: str):
            s = str(value or "").strip()
            if not s:
                return None
            try:
                return dt.datetime.fromisoformat(s)
            except Exception:
                pass
            for fmt in (
                "%d.%m.%Y %H:%M:%S",
                "%d.%m.%Y",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %I:%M:%S %p",
                "%m/%d/%Y",
            ):
                try:
                    return dt.datetime.strptime(s, fmt)
                except Exception:
                    pass
            return None

        def apply_certs(certs, err=None):
            tree.delete(*tree.get_children())
            self._crpt_sign_certs = certs or []
            if err:
                msg = getattr(err, "user_message", None) or str(err)
                mb.showerror("Сертификаты", msg)
            if not certs:
                return
            for idx, c in enumerate(certs):
                key_ok = "OK" if c.has_private_key else "-"
                valid_to_dt = _parse_cert_date(c.valid_to)
                expired = False
                if valid_to_dt:
                    now = dt.datetime.now(valid_to_dt.tzinfo) if valid_to_dt.tzinfo else dt.datetime.now()
                    expired = valid_to_dt < now
                tag = "cert_expired" if expired else "cert_ok"
                tree.insert(
                    "", "end", iid=str(idx),
                    values=(c.subject, c.valid_to, _short_tp(c.thumbprint), key_ok),
                    tags=(tag,),
                )
            current_tp = load_crpt_settings().cert_thumbprint
            if current_tp:
                for idx, c in enumerate(certs):
                    if c.thumbprint == current_tp:
                        tree.selection_set(str(idx))
                        tree.see(str(idx))
                        break

        def load_certs_async():
            def worker():
                try:
                    certs = crpt_auth.list_certificates()
                    err = None
                except Exception as e:
                    certs = []
                    err = e
                self.after(0, lambda: apply_certs(certs, err))
            threading.Thread(target=worker, daemon=True).start()

        def on_select():
            sel = tree.selection()
            if not sel:
                mb.showinfo("Сертификаты", "Сертификат не выбран.")
                return
            idx = int(sel[0])
            cert = self._crpt_sign_certs[idx]
            self.cfg["crpt_cert_thumbprint"] = cert.thumbprint
            save_config(self.cfg)
            if not env_cert:
                set_runtime_overrides(cert_thumbprint=cert.thumbprint)
            self._crpt_refresh_health()

        btns = ctk.CTkFrame(win, corner_radius=12)
        btns.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkButton(btns, text="Обновить список", command=load_certs_async, width=160).pack(side="left", padx=6, pady=6)
        ctk.CTkButton(btns, text="Выбрать сертификат", command=on_select, width=170).pack(side="left", padx=6, pady=6)
        ctk.CTkButton(btns, text="Закрыть", command=win.destroy, width=120).pack(side="right", padx=6, pady=6)

        note_text = "Если значение задано в .env, оно имеет приоритет над config.json."
        ctk.CTkLabel(win, text=note_text).pack(anchor="w", padx=14, pady=(0, 10))

        load_certs_async()

    def _crpt_refresh_token(self):
        self._crpt_refresh_health(refresh_token=True, check_connection=True)

    def _crpt_connect(self):
        use_info = self._bool_var(getattr(self, "_crpt_use_info_var", False), False)
        settings = load_crpt_settings()
        if not settings.pg:
            mb.showerror(
                "Проверка кодов",
                "Не задан CRPT_PG (product group).\nУкажите в .env, например: CRPT_PG=grocery"
            )
            return
        if not settings.cert_thumbprint:
            try:
                open_settings = mb.askyesno(
                    "Проверка кодов",
                    "Не выбран сертификат.\nОткрыть настройку подписи?"
                )
            except Exception:
                open_settings = False
            if open_settings:
                self._crpt_open_sign_settings()
            return
        if not (settings.oms_connection or settings.token_provider_inn):
            mb.showerror(
                "Проверка кодов",
                "Не задан CRPT_OMS_CONNECTION или CRPT_TOKEN_PROVIDER_INN.\nУкажите в .env."
            )
            return
        self._crpt_set_busy(True)
        self._crpt_set_connection_state("checking", "Подключение...")

        def worker():
            ok, msg, severity, details = crpt_check_connection_sync(use_info=use_info)
            self.after(0, lambda: self._crpt_apply_connection_result(ok, msg, severity, details))
            self.after(0, lambda: self._crpt_set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _crpt_clear_results(self):
        try:
            tree = getattr(self, "_crpt_tree", None)
            if tree:
                tree.delete(*tree.get_children())
        except Exception:
            pass
        try:
            self._crpt_raw_map = {}
            self._crpt_error_details = ""
            if getattr(self, "_crpt_btn_copy_details", None):
                self._crpt_btn_copy_details.configure(state="disabled")
            if getattr(self, "_crpt_status_label", None):
                self._crpt_status_label.configure(text="Готово")
        except Exception:
            pass

    def _crpt_run_check(self):
        try:
            text = self._crpt_input_text.get("1.0", "end-1c")
        except Exception:
            text = ""
        dedupe = self._bool_var(getattr(self, "_crpt_dedupe_var", False), False)
        use_info = self._bool_var(getattr(self, "_crpt_use_info_var", False), False)
        codes = parse_codes(text or "", dedupe=dedupe)
        if not codes:
            mb.showinfo("Проверка кодов", "Список кодов пуст.")
            return
        settings = load_crpt_settings()
        if not settings.pg:
            mb.showerror(
                "Проверка кодов",
                "Не задан CRPT_PG (product group).\nУкажите в .env, например: CRPT_PG=grocery"
            )
            return
        if not settings.cert_thumbprint:
            try:
                open_settings = mb.askyesno(
                    "Проверка кодов",
                    "Не выбран сертификат.\nОткрыть настройку подписи?"
                )
            except Exception:
                open_settings = False
            if open_settings:
                self._crpt_open_sign_settings()
            return
        if not (settings.oms_connection or settings.token_provider_inn):
            mb.showerror(
                "Проверка кодов",
                "Не задан CRPT_OMS_CONNECTION или CRPT_TOKEN_PROVIDER_INN.\nУкажите в .env."
            )
            return
        self._crpt_start_check(codes, show_raw=False, use_info=use_info)

    def _crpt_check_single_prompt(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Проверка кода")
        dlg.geometry("460x220")
        try:
            dlg.resizable(False, False)
        except Exception:
            pass
        try:
            dlg.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(dlg, text="Введите один код КМ:").pack(anchor="w", padx=12, pady=(12, 4))
        box = ctk.CTkTextbox(dlg, height=80)
        box.pack(fill="x", padx=12, pady=(0, 8))

        btns = ctk.CTkFrame(dlg)
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def paste_from_clipboard():
            try:
                data = self.clipboard_get()
            except Exception:
                data = ""
            if not data:
                return
            try:
                box.insert("insert", data)
            except Exception:
                pass

        def on_ok():
            try:
                text = box.get("1.0", "end-1c")
            except Exception:
                text = ""
            codes = parse_codes(text or "", dedupe=True)
            if not codes:
                mb.showinfo("Проверка кодов", "Код не задан.")
                return
            use_info = self._bool_var(getattr(self, "_crpt_use_info_var", False), False)
            settings = load_crpt_settings()
            if not settings.pg:
                mb.showerror(
                    "Проверка кодов",
                    "Не задан CRPT_PG (product group).\nУкажите в .env, например: CRPT_PG=grocery"
                )
                return
            if not settings.cert_thumbprint:
                try:
                    open_settings = mb.askyesno(
                        "Проверка кодов",
                        "Не выбран сертификат.\nОткрыть настройку подписи?"
                    )
                except Exception:
                    open_settings = False
                if open_settings:
                    self._crpt_open_sign_settings()
                return
            if not (settings.oms_connection or settings.token_provider_inn):
                mb.showerror(
                    "Проверка кодов",
                    "Не задан CRPT_OMS_CONNECTION или CRPT_TOKEN_PROVIDER_INN.\nУкажите в .env."
                )
                return
            dlg.destroy()
            self._crpt_start_check([codes[0]], show_raw=True, use_info=use_info)

        ctk.CTkButton(btns, text="Вставить из буфера", command=paste_from_clipboard).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Проверить", command=on_ok, width=100).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Отмена", command=dlg.destroy, width=100).pack(side="left", padx=6)

    def _crpt_start_check(self, codes, show_raw=False, use_info=False):
        self._crpt_clear_results()
        self._crpt_set_busy(True)
        self._crpt_set_connection_state("checking", "Подключение...")

        def worker():
            err = None
            results = []
            logger_cb = None
            if getattr(self, "logger", None):
                def logger_cb(msg, _self=self):
                    try:
                        _self.after(0, lambda m=msg: _self.logger.log(m))
                    except Exception:
                        pass
            ok, msg, severity, details = crpt_check_connection_sync(use_info=use_info)
            self.after(0, lambda: self._crpt_apply_connection_result(ok, msg, severity, details))
            if not ok:
                err = msg
                self.after(0, lambda: self._crpt_apply_results([], err, show_raw))
                return
            try:
                results = crpt_check_statuses_sync(codes, use_info=use_info, logger=logger_cb)
            except Exception as e:
                err = str(e)
            self.after(0, lambda: self._crpt_apply_results(results, err, show_raw))

        threading.Thread(target=worker, daemon=True).start()

    def _crpt_apply_results(self, results, err=None, show_raw=False):
        self._crpt_set_busy(False)
        if err:
            try:
                self._crpt_status_label.configure(text="Ошибка запроса")
            except Exception:
                pass
            try:
                if getattr(self, "logger", None):
                    self.logger.err(f"[CRPT] Ошибка: {err}")
            except Exception:
                pass
            mb.showerror("Проверка кодов", f"Ошибка:\n{err}")
            return
        tree = getattr(self, "_crpt_tree", None)
        if not tree:
            return
        tree.delete(*tree.get_children())
        self._crpt_raw_map = {}
        self._crpt_error_details = ""
        ok = warn = err_cnt = 0
        for r in results or []:
            norm_disp = self._crpt_display_code(normalize_code(r.normalized))
            preview, full = self._crpt_format_raw(r.raw)
            tags = ()
            if getattr(r, "severity", ""):
                tags = (r.severity,)
                if r.severity == "ok":
                    ok += 1
                elif r.severity == "warn":
                    warn += 1
                elif r.severity == "error":
                    err_cnt += 1
            iid = tree.insert("", "end", values=(
                norm_disp,
                r.status or "",
                r.explain or "",
            ), tags=tags)
            if full:
                self._crpt_raw_map[iid] = full
            if getattr(r, "error_details", None):
                self._crpt_error_details += f"{r.error_details}\n"

        total = len(results or [])
        try:
            self._crpt_status_label.configure(
                text=f"Готово: {total} (ok={ok}, warn={warn}, err={err_cnt})"
            )
        except Exception:
            pass
        try:
            if getattr(self, "logger", None):
                tag = "success"
                if err_cnt > 0:
                    tag = "error"
                elif warn > 0:
                    tag = "warn"
                self.logger.log(
                    f"[CRPT] Проверка завершена: total={total}, ok={ok}, warn={warn}, err={err_cnt}",
                    tags=tag,
                )
        except Exception:
            pass
        if self._crpt_error_details:
            self._crpt_log_error("\u0414\u0438\u0430\u0433\u043d\u043e\u0441\u0442\u0438\u043a\u0430", self._crpt_error_details)
        if self._crpt_error_details or self._crpt_raw_map:
            try:
                self._crpt_btn_copy_details.configure(state="normal")
            except Exception:
                pass
        if show_raw and results:
            self._crpt_show_raw_text(self._crpt_raw_map.get(tree.get_children()[0], ""))

    def _crpt_show_raw_text(self, text: str):
        if not text:
            mb.showinfo("Сырой ответ", "Нет данных для отображения.")
            return
        win = ctk.CTkToplevel(self)
        win.title("Сырой ответ")
        win.geometry("760x520")
        box = ctk.CTkTextbox(win)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _crpt_show_raw_for_selected(self):
        tree = getattr(self, "_crpt_tree", None)
        if not tree:
            return
        sel = tree.selection()
        if not sel:
            return
        text = self._crpt_raw_map.get(sel[0], "")
        self._crpt_show_raw_text(text)

    def _crpt_copy_error_details(self):
        details = getattr(self, "_crpt_error_details", "") or ""
        if not details.strip():
            tree = getattr(self, "_crpt_tree", None)
            text = ""
            if tree:
                sel = tree.selection()
                if sel:
                    text = self._crpt_raw_map.get(sel[0], "")
            if not text and len(getattr(self, "_crpt_raw_map", {}) or {}) == 1:
                try:
                    text = next(iter(self._crpt_raw_map.values()))
                except Exception:
                    text = ""
            if not text:
                mb.showinfo("Проверка кодов", "Деталей нет.")
                return
            details = text
        try:
            self.clipboard_clear()
            self.clipboard_append(details)
        except Exception:
            pass
        mb.showinfo("Проверка кодов", "Детали скопированы в буфер.")

    def _prompt_csv_on_launch(self):
        if self.csv_path:
            return
        p = fd.askopenfilename(title="Выбери kontur.csv/tsv",
                               filetypes=[("CSV/TSV", "*.csv;*.tsv;*.txt"), ("Все файлы", "*.*")])
        if not p:
            try:
                self.logger.err("CSV не выбран.")
            except Exception:
                pass
            return
        self._load_csv_from_path(p, auto_preview=True)

    def _choose_btw(self, fmt):
        p = fd.askopenfilename(title=f"Выбери BTW для {fmt}", filetypes=[("BarTender Template", "*.btw")])
        if not p:
            return
        try:
            target, file_name = self._copy_template_into_workspace(p, fmt)
            meta = self._get_label_template(fmt) or {"id": fmt, "name": fmt, "part_mode": "blank", "printer": ""}
            meta["file_name"] = file_name
            self.label_templates[fmt] = meta
            self._save_label_templates_to_cfg()
            if fmt == "16x16":
                self.btw16_entry.delete(0, "end")
                self.btw16_entry.insert(0, target)
            elif fmt == "30x20":
                self.btw30_entry.delete(0, "end")
                self.btw30_entry.insert(0, target)
            self.logger.log(f"[Шаблон] Сохранён BTW для {fmt}: {target}")
        except Exception as e:
            mb.showerror("BTW", f"Не удалось сохранить шаблон:\n{e}")

    def _choose_csv(self):
        p = fd.askopenfilename(title="Выбери kontur.csv/tsv",
                               filetypes=[("CSV/TSV", "*.csv;*.tsv;*.txt"), ("Все файлы", "*.*")])
        if not p:
            return
        self._load_csv_from_path(p, auto_preview=True)

    # --- CSV файловый менеджер ---
    def _shorten_path(self, path, max_len=64):
        s = path or ""
        if len(s) <= max_len:
            return s

        separator = "\\" if "\\" in s else "/"
        drive, tail = os.path.splitdrive(s)
        absolute = tail.startswith(("\\", "/"))
        parts = [part for part in re.split(r"[\\/]", tail) if part]
        if not parts:
            return s[:max_len]

        final_component = parts[-1]
        prefix = drive + separator if drive or absolute else ""
        shortened = prefix + "..." + separator + final_component

        for component in parts[:-1]:
            candidate_prefix = prefix + component + separator
            candidate = candidate_prefix + "..." + separator + final_component
            if len(candidate) > max_len:
                break
            prefix = candidate_prefix
            shortened = candidate

        if len(shortened) <= max_len:
            return shortened

        fixed = prefix + "..." + separator
        available = max(1, max_len - len(fixed))
        return fixed + final_component[-available:]

    def _responsive_path_text(self, path, pixel_width):
        max_len = max(18, int(max(1, pixel_width) / 7))
        return self._shorten_path(path, max_len=max_len)

    def _responsive_csv_dir_text(self, path, pixel_width=None):
        prefix = "Текущая папка: "
        if pixel_width is None:
            path_text = self._shorten_path(path)
        else:
            path_width = max(1, int(pixel_width) - len(prefix) * 7)
            path_text = self._responsive_path_text(path, path_width)
        return prefix + path_text

    def _schedule_path_label_refresh(self, event=None):
        pending = getattr(self, "_path_refresh_after_id", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except Exception:
                pass
        self._path_refresh_after_id = None

        def refresh():
            self._path_refresh_after_id = None
            path = getattr(self, "csv_dir", "") or ""
            for label_name, tooltip_name in (
                ("csv_dir_label", "csv_dir_tooltip"),
                ("source_dir_value_label", "source_dir_tooltip"),
            ):
                label = getattr(self, label_name, None)
                if label is None:
                    continue
                try:
                    width = max(1, int(label.winfo_width()))
                    if label_name == "csv_dir_label":
                        text = self._responsive_csv_dir_text(path, width)
                    else:
                        text = self._responsive_path_text(path, width)
                    label.configure(text=text)
                except Exception:
                    pass
                tooltip = getattr(self, tooltip_name, None)
                if tooltip is not None:
                    tooltip.text = path

        try:
            self._path_refresh_after_id = self.after(
                PATH_RESIZE_DEBOUNCE_MS, refresh
            )
        except Exception:
            self._path_refresh_after_id = None

    def _schedule_csv_scrollbar_refresh(self, event=None):
        pending = getattr(self, "_csv_scroll_refresh_after_id", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except Exception:
                pass
        self._csv_scroll_refresh_after_id = None

        def refresh():
            self._csv_scroll_refresh_after_id = None
            frame = getattr(self, "csv_list_frame", None)
            canvas = getattr(frame, "_parent_canvas", None)
            scrollbar = getattr(frame, "_scrollbar", None)
            if canvas is None or scrollbar is None:
                return
            try:
                bounds = canvas.bbox("all")
                content_height = 0 if not bounds else max(0, bounds[3] - bounds[1])
                needs_scrollbar = content_height > max(1, canvas.winfo_height())
                if needs_scrollbar:
                    scrollbar.grid()
                else:
                    scrollbar.grid_remove()
            except Exception:
                pass

        try:
            self._csv_scroll_refresh_after_id = self.after(
                CSV_SCROLLBAR_RESIZE_DEBOUNCE_MS, refresh
            )
        except Exception:
            self._csv_scroll_refresh_after_id = None

    def _norm_csv_path(self, p):
        try:
            return os.path.normcase(os.path.abspath(p or ""))
        except Exception:
            return p or ""

    def _list_csv_entries(self):
        """Возвращает список (path, kind) где kind in {'dir','file'}; файлы - только .csv."""
        try:
            if not os.path.isdir(self.csv_dir):
                return []
            dirs = [os.path.join(self.csv_dir, d) for d in os.listdir(self.csv_dir)
                    if os.path.isdir(os.path.join(self.csv_dir, d))]
            files = [os.path.join(self.csv_dir, f) for f in os.listdir(self.csv_dir)
                     if f.lower().endswith(".csv") and os.path.isfile(os.path.join(self.csv_dir, f))]
            # Папки — по убыванию (свежие/важные сверху), файлы — как раньше, по возрастанию
            entries = [(d, "dir") for d in sorted(dirs, key=lambda x: os.path.basename(x).lower(), reverse=True)]
            entries += [(f, "file") for f in sorted(files, key=lambda x: os.path.basename(x).lower())]
            return entries
        except Exception:
            return []

    def _normalize_csv_stats(self, stats):
        if not isinstance(stats, dict):
            return {}
        out = {}
        for k, v in stats.items():
            key = self._norm_csv_path(k)
            if not key:
                continue
            if isinstance(v, dict):
                out[key] = v
        return out

    def _check_template_db(self, fmt, expected_path: str):
        """
        Проверяет, привязан ли шаблон BarTender к ожидаемому tmp_batch.csv.
        Возвращает True, если связь найдена или не удалось проверить (чтобы не блокировать печать).
        """
        try:
            conns = getattr(fmt, "DatabaseConnections", None)
            if not conns:
                # Шаблон без подключений: ничего не проверяем
                return True
            try:
                count = conns.Count
            except Exception:
                count = 0
            if count <= 0:
                return True
            expected_norm = os.path.normcase(os.path.abspath(expected_path or ""))
            found = False
            for i in range(1, count + 1):
                try:
                    conn = conns.Item(i)
                except Exception:
                    continue
                fname = None
                try:
                    tf = getattr(conn, "TextFile", None)
                    if tf:
                        fname = getattr(tf, "FileName", None)
                except Exception:
                    pass
                if not fname:
                    try:
                        fname = getattr(conn, "FileName", None)
                    except Exception:
                        pass
                if fname:
                    norm = os.path.normcase(os.path.abspath(str(fname)))
                    if norm == expected_norm:
                        found = True
                        break
            if not found:
                self.logger.err(f"Шаблон не привязан к tmp_batch.csv ({expected_norm}). Проверь привязку в BarTender.")
            return found
        except Exception:
            # не валим печать, если не смогли проверить
            return True

    def _template_db_connection_count(self, fmt) -> int:
        total = 0
        for attr in ("DatabaseConnections", "Databases"):
            try:
                coll = getattr(fmt, attr, None)
            except Exception:
                coll = None
            if not coll:
                continue
            try:
                total += int(coll.Count or 0)
            except Exception:
                pass
        return total

    def _template_used_names(self, fmt, field_type: int):
        try:
            text = fmt.UsedNames(field_type, "\n")
        except Exception:
            return []
        result = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if line:
                result.append(line)
        return result

    def _normalize_used_field_name(self, raw_name: str) -> str:
        text = str(raw_name or "").strip()
        if not text:
            return ""
        parts = [part for part in text.split(".") if part]
        return parts[-1] if parts else text

    def _template_batch_report(self, fmt, part_mode: str = "blank", recordset_name: str = "tmp_batch"):
        available_runtime_fields = set(getattr(self, "REQ_COLS", DEFAULT_REQ_COLS) or DEFAULT_REQ_COLS)
        available_runtime_fields.update({"ShirtName"})
        named_fields = []
        try:
            named_fields = sorted({s.Name for s in fmt.NamedSubStrings})
        except Exception:
            named_fields = []

        db_used_names = self._template_used_names(fmt, 1)
        template_used_names = self._template_used_names(fmt, 2)
        tmp_prefix = ((recordset_name or "tmp_batch").strip() or "tmp_batch").lower() + "."

        db_fields = sorted(
            {
                self._normalize_used_field_name(name)
                for name in db_used_names
                if self._normalize_used_field_name(name)
            }
        )
        template_fields = sorted(
            {
                self._normalize_used_field_name(name)
                for name in template_used_names
                if self._normalize_used_field_name(name)
            }
        )

        db_count = self._template_db_connection_count(fmt)
        try:
            use_database = bool(fmt.UseDatabase)
        except Exception:
            use_database = False

        has_tmp_batch_refs = any(name.lower().startswith(tmp_prefix) for name in (db_used_names + template_used_names))
        raw_used_fields = sorted(
            {
                name
                for name in (db_fields + template_fields)
                if name
            }
        )
        if not raw_used_fields:
            raw_used_fields = sorted(
                {
                    name
                    for name in named_fields
                    if name
                }
            )
        used_fields = [name for name in raw_used_fields if name in available_runtime_fields]
        missing_named = [name for name in used_fields if name not in named_fields]
        missing_db = [name for name in used_fields if name not in db_fields]
        template_only = [name for name in missing_db if name in template_fields]
        missing_runtime = [name for name in raw_used_fields if name not in available_runtime_fields]
        has_db_runtime = bool(db_count > 0 or use_database)
        can_batch = bool(has_db_runtime and has_tmp_batch_refs and not missing_db and not missing_runtime and used_fields)

        reasons = []
        if not has_db_runtime:
            if has_tmp_batch_refs:
                reasons.append("tmp_batch упоминается в шаблоне, но рабочая база данных BarTender не включена")
            else:
                reasons.append("шаблон не связан с tmp_batch")
        if missing_db:
            reasons.append("в Database Fields отсутствуют поля: " + ", ".join(missing_db))
        if template_only:
            reasons.append("эти поля заданы как Template Field, а не как Database Field: " + ", ".join(template_only))
        if missing_runtime:
            reasons.append("в программе не формируются поля: " + ", ".join(missing_runtime))

        if can_batch:
            batch_message = "Шаблон готов к пакетной печати через tmp_batch."
        else:
            batch_message = "; ".join(reasons) if reasons else "Шаблон будет печататься по одной этикетке."

        return {
            "required": used_fields,
            "used_fields": used_fields,
            "named_fields": named_fields,
            "missing_named": missing_named,
            "db_fields": db_fields,
            "missing_db": missing_db,
            "template_fields": template_fields,
            "template_only": template_only,
            "missing_runtime": missing_runtime,
            "db_count": db_count,
            "use_database": use_database,
            "has_tmp_batch_refs": has_tmp_batch_refs,
            "can_batch": can_batch,
            "batch_message": batch_message,
        }

    def _template_dm_validation_report(self, fmt, batch_report=None):
        batch_report = batch_report or {}
        used_names = set(batch_report.get("used_fields") or [])
        used_names.update(batch_report.get("named_fields") or [])
        used_names.update(batch_report.get("db_fields") or [])
        used_names.update(batch_report.get("template_fields") or [])

        present = "DM" in used_names
        if not present:
            return {
                "present": False,
                "message": "",
                "details": [],
                "com_values": {},
            }

        details = [
            'Для ЧЗ проверьте в свойствах DataMatrix -> "Метод печати": '
            '"Штрих-коды и читаемый текст контролируются программой".'
        ]
        com_values = {}
        try:
            pm = getattr(getattr(fmt, "PrintSetup", None), "PrintMethod", None)
        except Exception:
            pm = None

        if pm is not None:
            for attr in ("Unsupported2DBarCodes", "Unsupported1DBarCodes", "TrueTypeText"):
                try:
                    com_values[attr] = int(getattr(pm, attr))
                except Exception:
                    pass

        if com_values:
            details.append(
                "COM-диагностика формата: "
                + ", ".join(f"{key}={value}" for key, value in com_values.items())
            )

        details.append(
            "BarTender COM не отдаёт точное состояние этого параметра у самого объекта DM, "
            "поэтому эта часть проверки остаётся ручной."
        )

        return {
            "present": True,
            "message": details[0],
            "details": details,
            "com_values": com_values,
        }

    def _print_rows_direct(self, fmt, rows_enriched, prn_name: str, batch_label: str = "") -> int:
        rows_seq = list(rows_enriched or [])
        if not rows_seq:
            return 0
        try:
            self.bt.set_common_print_flags(fmt)
        except Exception:
            pass
        try:
            fmt.PrintSetup.Printer = prn_name
        except Exception:
            pass
        try:
            fmt.PrintSetup.PrinterName = prn_name
        except Exception:
            pass
        prompt = bool(self._dialog_flag())
        if prompt and len(rows_seq) > 1:
            try:
                self.logger.log(
                    f"{batch_label}Шаблон печатается без tmp_batch.csv, поэтому диалог печати отключён для пакетной печати."
                )
            except Exception:
                pass
            prompt = False

        printed = 0
        for idx, enr in enumerate(rows_seq, start=1):
            self._pause_wait()
            if self._should_stop():
                raise Exception("Печать остановлена пользователем.")
            try:
                fmt.PrintSetup.IdenticalCopiesOfLabel = 1
            except Exception:
                pass
            applied = self.bt.apply_fields(fmt, enr)
            if not applied:
                try:
                    self.logger.err(
                        f"{batch_label}Строка {idx}: не удалось подставить поля в шаблон."
                    )
                except Exception:
                    pass
            ok = self._bt_print(fmt, 1, prompt)
            if not ok:
                raise Exception(f"Строка {idx}: BarTender не подтвердил печать.")
            prompt = False
            printed += 1
        return printed

    def _collect_bt_messages(self, messages_obj):
        lines = []
        coll = None
        try:
            coll = getattr(messages_obj, "value", None)
        except Exception:
            coll = None
        if coll is None:
            coll = messages_obj
        try:
            count = int(coll.Count or 0)
        except Exception:
            count = 0
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
            prefix = " / ".join([part for part in (type_text, severity) if part])
            lines.append(f"{prefix}: {text}" if prefix else text)
        return lines

    def _disable_auto_print_again(self, fmt):
        """Disable BarTender's automatic repeat option on every supported COM surface."""
        disabled = False
        errors = []
        try:
            fmt.AutoPrintAgain = False
            disabled = True
        except Exception as e:
            errors.append(f"Format.AutoPrintAgain: {e}")
        try:
            fmt.PrintSetup.AutoPrintAgain = False
            disabled = True
        except Exception as e:
            errors.append(f"PrintSetup.AutoPrintAgain: {e}")
        if errors:
            try:
                self.logger.log(
                    "BT: часть AutoPrintAgain-настроек недоступна: " + "; ".join(errors)
                )
            except Exception:
                pass
        return disabled

    def _bt_batch_print(self, fmt, prn_name: str, print_job_name: str, copies: int, show_dialog: bool):
        try:
            fmt.PrintSetup.Printer = prn_name
        except Exception:
            pass
        try:
            fmt.PrintSetup.PrinterName = prn_name
        except Exception:
            pass
        try:
            fmt.PrintSetup.IdenticalCopiesOfLabel = int(copies)
        except Exception:
            pass
        self._disable_auto_print_again(fmt)

        if show_dialog:
            try:
                self.logger.log("BT Batch Print: включён диалог печати, использую совместимый PrintOut.")
            except Exception:
                pass
            return (
                BT_BATCH_SUBMITTED
                if self._bt_print(fmt, copies, show_dialog)
                else BT_BATCH_FAILED
            )

        try:
            import pythoncom
            from win32com.client import VARIANT

            messages = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_DISPATCH, None)
        except Exception as e:
            try:
                self.logger.err(
                    "BT Batch Print: не удалось подготовить COM Messages до отправки; "
                    "автоматический fallback отключён, чтобы исключить двойную печать: "
                    f"{e}"
                )
            except Exception:
                pass
            return BT_BATCH_FAILED

        # AutoPrint needs confirmation that BarTender accepted/submitted the job,
        # not synchronous completion of a potentially very large print run.
        # Seagull documents WaitForJobToComplete=False as the non-waiting mode.
        timeout_ms = 30000
        try:
            self.logger.log(
                f"BT Batch Print: Print(Job='{print_job_name}', "
                f"WaitForJobToComplete=False, TimeoutMs={timeout_ms})"
            )
            result = fmt.Print(print_job_name or "", False, timeout_ms, messages)
        except Exception as e:
            try:
                self.logger.err(
                    "BT Batch Print: исключение после начала Print(...); "
                    "результат отправки неоднозначен, повторная отправка отключена: "
                    f"{e}"
                )
            except Exception:
                pass
            return BT_BATCH_AMBIGUOUS

        try:
            message_lines = self._collect_bt_messages(messages)
        except Exception as e:
            message_lines = []
            try:
                self.logger.log(f"BT Batch Print: не удалось прочитать Messages: {e}")
            except Exception:
                pass
        for line in message_lines:
            try:
                self.logger.log(f"BT Batch Message: {line}")
            except Exception:
                pass

        try:
            result_code = int(result)
        except Exception as e:
            try:
                self.logger.err(
                    "BT Batch Print: некорректный BtPrintResult после Print(...); "
                    "результат отправки неоднозначен, повторная отправка отключена: "
                    f"{result!r} ({e})"
                )
            except Exception:
                pass
            return BT_BATCH_AMBIGUOUS

        if result_code == 0:
            return BT_BATCH_SUBMITTED
        if result_code == 1:
            try:
                self.logger.log(
                    "BT Batch Print: BarTender вернул btTimeout (1). "
                    "Задание могло уже быть создано и продолжать печататься; "
                    "статус считается неоднозначным, повторная отправка отключена."
                )
            except Exception:
                pass
            return BT_BATCH_AMBIGUOUS
        if result_code == 2:
            try:
                self.logger.err(
                    "BT Batch Print: BarTender вернул btFailure (2). "
                    "После попытки Print(...) автоматический retry запрещён, "
                    "чтобы не создать дубликат."
                )
            except Exception:
                pass
            return BT_BATCH_FAILED

        try:
            self.logger.err(
                f"BT Batch Print: неизвестный BtPrintResult={result_code}; "
                "результат отправки неоднозначен, повторная отправка отключена."
            )
        except Exception:
            pass
        return BT_BATCH_AMBIGUOUS

    def _rebind_template_db(self, fmt, csv_path: str):
        """
        Принудительно перепривязывает текстовые подключения шаблона к переданному csv_path.
        Возвращает True, если удалось изменить хотя бы одно подключение.
        """
        import os
        try:
            target = os.path.normcase(os.path.abspath(csv_path or ""))
            conns = getattr(fmt, "DatabaseConnections", None)
            changed = False
            seen_paths = []
            if conns:
                try:
                    count = conns.Count
                except Exception:
                    count = 0
                for i in range(1, count + 1):
                    try:
                        conn = conns.Item(i)
                    except Exception:
                        continue
                    try:
                        seen_paths.append(str(getattr(conn, "FileName", "") or getattr(getattr(conn, "TextFile", None) or {}, "FileName", "")))
                    except Exception:
                        pass
                    # Устанавливаем путь в TextFile.FileName, если доступно
                    try:
                        tf = getattr(conn, "TextFile", None)
                    except Exception:
                        tf = None
                    if tf:
                        try:
                            cur = os.path.normcase(os.path.abspath(str(getattr(tf, "FileName", ""))))
                        except Exception:
                            cur = None
                        try:
                            tf.FileName = csv_path
                            if cur != target:
                                changed = True
                        except Exception:
                            pass
                    # Также пробуем conn.FileName на случай другого API
                    try:
                        cur2 = os.path.normcase(os.path.abspath(str(getattr(conn, "FileName", ""))))
                    except Exception:
                        cur2 = None
                    try:
                        conn.FileName = csv_path
                        if cur2 != target:
                            changed = True
                    except Exception:
                        pass

            # BarTender старых версий может использовать коллекцию Databases
            dbs = getattr(fmt, "Databases", None)
            if dbs:
                try:
                    dcount = dbs.Count
                except Exception:
                    dcount = 0
                for j in range(1, dcount + 1):
                    try:
                        db = dbs.Item(j)
                    except Exception:
                        continue
                    try:
                        seen_paths.append(str(getattr(db, "FileName", "") or getattr(getattr(db, "TextFile", None) or {}, "FileName", "")))
                    except Exception:
                        pass
                    try:
                        cur3 = os.path.normcase(os.path.abspath(str(getattr(db, "FileName", ""))))
                    except Exception:
                        cur3 = None
                    try:
                        db.FileName = csv_path
                        if cur3 != target:
                            changed = True
                    except Exception:
                        pass
                    # Некоторые текстовые БД имеют TextFile.FileName
                    try:
                        tf2 = getattr(db, "TextFile", None)
                    except Exception:
                        tf2 = None
                    if tf2:
                        try:
                            cur4 = os.path.normcase(os.path.abspath(str(getattr(tf2, "FileName", ""))))
                        except Exception:
                            cur4 = None
                        try:
                            tf2.FileName = csv_path
                            if cur4 != target:
                                changed = True
                        except Exception:
                            pass

            if changed:
                try:
                    self.logger.log(f"Подключение шаблона перепривязано к {csv_path}")
                except Exception:
                    pass
            return changed
        except Exception as e:
            try:
                self.logger.err(f"Не удалось перепривязать шаблон к {csv_path}: {e}")
            except Exception:
                pass
            return False

    def _update_csv_dir_label(self):
        try:
            if hasattr(self, "csv_dir_label"):
                self.csv_dir_label.configure(
                    text=self._responsive_csv_dir_text(
                        self.csv_dir, self.csv_dir_label.winfo_width()
                    )
                )
        except Exception:
            pass
        self._schedule_path_label_refresh()

    def _update_brand_hint(self, gtin=None):
        """Показывает бренд из справочника для текущего GTIN CSV."""
        val = "-"
        try:
            g = gtin
            if not g and getattr(self, "csv_rows", None):
                for r in self.csv_rows:
                    g = r.get("GTIN") or ""
                    g = only_digits(str(g))
                    if g:
                        break
            g = only_digits(str(g or ""))
            if g and getattr(self, "product_map", None):
                info = self.product_map.get(g, {}) or {}
                brand = info.get("BRAND") or info.get("Бренд") or ""
                if brand:
                    val = brand
                else:
                    val = "Не найден в справочнике"
        except Exception:
            val = "-"
        try:
            if hasattr(self, "brand_value_label"):
                self.brand_value_label.configure(text=f"бренд: {val}")
        except Exception:
            pass

    def _open_print_history(self, path):
        """Окно с историей печати выбранного CSV."""
        try:
            key = self._norm_csv_path(path)
        except Exception:
            key = path
        info = self.csv_stats.get(key, {}) if isinstance(self.csv_stats, dict) else {}
        history = list(info.get("history") or [])
        # если история пустая, но есть агрегированные поля - создадим запись и сохраним
        if (not history) and info.get("printed") and info.get("last_print"):
            history = [{
                "ts": info.get("last_print"),
                "printer": info.get("last_printer") or info.get("printer"),
                "fmt": info.get("last_fmt"),
                "sent": info.get("last_sent"),
                "calib": info.get("last_calib"),
                "user": info.get("last_user") or "-",
            }]
            try:
                info["history"] = history
                self.csv_stats[key] = info
                self.cfg["csv_print_stats"] = self.csv_stats
                save_config(self.cfg)
            except Exception:
                pass
        win = ctk.CTkToplevel(self)
        try:
            win.title(f"История печати: {os.path.basename(path)}")
        except Exception:
            win.title("История печати")
        win.geometry("560x360")
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.after(150, lambda: win.attributes("-topmost", False))
            win.focus_force()
        except Exception:
            pass
        body = ctk.CTkScrollableFrame(win, height=280)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 4))

        if not history:
            ctk.CTkLabel(body, text="Нет данных по печати этого файла.").pack(anchor="w", padx=8, pady=6)
        else:
            for h in sorted(history, key=lambda x: str(x.get("ts", "")), reverse=True):
                ts = h.get("ts", "")
                user = h.get("user") or "-"
                printer = h.get("printer") or "-"
                fmt = h.get("fmt") or "-"
                sent = h.get("sent")
                calib = h.get("calib")
                parts = [
                    f"{ts}",
                    f"Пользователь: {user}",
                    f"Принтер: {printer}",
                    f"Шаблон: {fmt}",
                    f"Строк отправлено: {sent if sent is not None else '-'}",
                ]
                if calib:
                    parts.append(f"Калибровочных: {calib}")
                ctk.CTkLabel(body, text=" | ".join(parts), anchor="w", justify="left").pack(anchor="w", fill="x", padx=8, pady=3)

        ctk.CTkButton(win, text="Закрыть", command=win.destroy).pack(pady=(4, 12))

    def _choose_csv_dir(self):
        init_dir = self.csv_dir if os.path.isdir(self.csv_dir) else BASE_DIR
        p = fd.askdirectory(title="Папка с CSV", initialdir=init_dir)
        if not p:
            return
        self._switch_csv_dir(p)

    def _switch_csv_dir(self, path):
        if not path:
            return
        self.csv_dir = path
        self.cfg["csv_dir"] = path
        save_config(self.cfg)
        self._update_csv_dir_label()
        self._render_csv_list()
        self._update_brand_hint()

    def _go_up_dir(self):
        try:
            parent = os.path.abspath(os.path.join(self.csv_dir, os.pardir))
            if parent and os.path.isdir(parent) and parent != self.csv_dir:
                self._switch_csv_dir(parent)
        except Exception:
            pass

    def _list_csv_files(self):
        try:
            if not os.path.isdir(self.csv_dir):
                return []
            return sorted(
                [os.path.join(self.csv_dir, f) for f in os.listdir(self.csv_dir) if f.lower().endswith(".csv")],
                key=lambda x: os.path.basename(x).lower()
            )
        except Exception:
            return []

    def _render_csv_list(self):
        self._schedule_path_label_refresh()
        self._schedule_csv_scrollbar_refresh()
        frame = getattr(self, "csv_list_frame", None)
        if not frame:
            return
        try:
            for w in frame.winfo_children():
                w.destroy()
        except Exception:
            pass
        if not os.path.isdir(self.csv_dir):
            ctk.CTkLabel(frame, text="Папка CSV не найдена").pack(anchor="w", padx=6, pady=4)
            return

        entries = self._list_csv_entries()
        if not entries:
            ctk.CTkLabel(frame, text="CSV не найдены").pack(anchor="w", padx=6, pady=4)
            return

        cur_norm = self._norm_csv_path(getattr(self, "csv_path", ""))
        # сброс hover
        self._fm_hover_row = None
        for full, kind in entries:
            is_cur = (kind == "file" and self._norm_csv_path(full) == cur_norm)
            # fg_color ожидает (light, dark)
            fg = ("#e0ecff", "#3a4a6a") if is_cur else None  # подсветка заметнее в тёмной теме
            row = ctk.CTkFrame(frame, fg_color=fg)
            row.pack(fill="x", padx=6, pady=2)
            try:
                row._base_fg = row.cget("fg_color")
            except Exception:
                row._base_fg = fg
            fname = os.path.basename(full)
            status = ""
            printed = False
            if kind == "file":
                info = self.csv_stats.get(self._norm_csv_path(full), {}) if isinstance(self.csv_stats, dict) else {}
                printed = bool(info.get("printed"))
                ts = info.get("last_print", "")
                if printed:
                    status = f"Печатался {ts}" if ts else "Печатался"
                else:
                    status = "Не печатался"

            # Цвет текста под выбранный фон: (light_mode, dark_mode)
            name_color = ("#0b2540", "#ffffff") if is_cur else None
            icon = "📁 " if kind == "dir" else "📄 "
            lbl = ctk.CTkLabel(
                row,
                text=icon + fname,
                anchor="w",
                text_color=name_color
            )
            lbl.pack(side="left", fill="x", expand=True, padx=(0, 6))
            lbl.bind("<Button-1>", lambda e, p=full, k=kind: (self._switch_csv_dir(p) if k == "dir" else self._select_csv_from_list(p)))
            try:
                lbl.configure(cursor="hand2")
            except Exception:
                pass
            ok_color = ("#28a745", "#1e7e34")  # зелёный для обеих тем
            cur_color = ("#0d6efd", "#0d6efd")
            if kind == "file":
                ctk.CTkLabel(row, text=status, anchor="w",
                             text_color=(ok_color if printed else (cur_color if is_cur else None))).pack(side="left", padx=(8, 8))
                cm = Menu(self, tearoff=0)
                cm.add_command(label="История печати", command=lambda p=full: self._open_print_history(p))
                def _popup_menu(event, menu=cm):
                    try:
                        menu.tk_popup(event.x_root, event.y_root)
                    finally:
                        menu.grab_release()
                row.bind("<Button-3>", _popup_menu)
                lbl.bind("<Button-3>", _popup_menu)

            # Hover-подсветка строки
            hover_fg = ("#dbe9ff", "#4b5c80")
            def _enter(e, r=row, hf=hover_fg):
                try:
                    prev = getattr(self, "_fm_hover_row", None)
                    if prev and prev is not r:
                        try:
                            prev.configure(fg_color=getattr(prev, "_base_fg", None))
                        except Exception:
                            pass
                    r.configure(fg_color=hf)
                    self._fm_hover_row = r
                except Exception:
                    pass
            def _leave(e, r=row):
                try:
                    if getattr(self, "_fm_hover_row", None) is r:
                        r.configure(fg_color=getattr(r, "_base_fg", None))
                        self._fm_hover_row = None
                except Exception:
                    pass
            row.bind("<Enter>", _enter)
            row.bind("<Leave>", _leave)
            lbl.bind("<Enter>", _enter); lbl.bind("<Leave>", _leave)

        # Сброс скролла к началу после перерисовки
        try:
            frame._parent_canvas.yview_moveto(0)
        except Exception:
            pass

    def _select_csv_from_list(self, path):
        self._load_csv_from_path(path, auto_preview=True)

    def _load_csv_from_path(self, p, auto_preview=True):
        if not p:
            return
        try:
            p_abs = os.path.abspath(p)
        except Exception:
            p_abs = p
        self.csv_path = p_abs
        try:
            self.csv_dir = os.path.dirname(p_abs) or self.csv_dir
        except Exception:
            pass
        self.cfg["csv_dir"] = self.csv_dir
        save_config(self.cfg)
        self._update_csv_dir_label()
        # Показываем только имя файла в UI, полный путь - в лог
        import os as _os
        try:
            self.logger.log(f"CSV выбран: {p_abs}")
        except Exception:
            pass
        try:
            self.csv_label.configure(text=_os.path.basename(self.csv_path))
        except Exception:
            pass
        try:
            self.csv_rows = load_kontur_raw(p_abs)
            head = self.csv_rows[0] if self.csv_rows else {"DM": "", "NAME": ""}
            self.logger.log(f"CSV: строк={len(self.csv_rows)}; пример DM='{head.get('DM','')}', NAME='{head.get('NAME','')}'")
            self._reset_print_selection_for_loaded_csv()
            try:
                if self.csv_rows:
                    suggested = min(len(self.csv_rows), self.default_batch_size)
                    self.batch_entry.delete(0, "end")
                    self.batch_entry.insert(0, str(suggested))
            except Exception:
                pass
            try:
                sample_gtin = (head.get("GTIN") or "")
            except Exception:
                sample_gtin = ""
            self._update_brand_hint(sample_gtin)

            if auto_preview and self.csv_rows:
                self._schedule_live_preview(immediate=True)
        except Exception as e:
            try:
                self.logger.err(f"Ошибка CSV: {e}")
                mb.showerror("CSV", f"Не удалось прочитать файл:\n{e}")
            except Exception:
                pass
        try:
            self._render_csv_list()
        except Exception:
            pass
        self._refresh_runtime_status()

    def _mark_csv_printed(self, path, sent_total=0, printer=None, fmt=None, calib_total=0):
        key = self._norm_csv_path(path)
        if not key:
            return
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            info = self.csv_stats if isinstance(self.csv_stats, dict) else {}
            cur = info.get(key, {})
            runs = int(cur.get("print_runs") or 0)
            # История запусков
            history = list(cur.get("history") or [])
            try:
                user = os.getlogin()
            except Exception:
                try:
                    user = getpass.getuser()
                except Exception:
                    user = "-"
            history.append({
                "ts": now,
                "printer": printer,
                "fmt": fmt,
                "sent": int(sent_total or 0),
                "calib": int(calib_total or 0),
                "user": user,
            })
            history = history[-50:]  # ограничиваем размер истории
            cur.update({
                "printed": True,
                "last_print": now,
                "print_runs": runs + 1,
                "last_sent": int(sent_total or 0),
                "last_printer": printer,
                "last_fmt": fmt,
                "last_calib": int(calib_total or 0),
                "last_user": user,
                "history": history,
            })
            info[key] = cur
            self.csv_stats = info
            self.cfg["csv_print_stats"] = self.csv_stats
            save_config(self.cfg)
        except Exception:
            pass
        try:
            self._render_csv_list()
        except Exception:
            pass

    # ---------- helpers ----------
    def _get_batch_size(self):
        t = (self.batch_entry.get() or "").strip()
        if not t or t == "0":
            return None
        try:
            v = int(t)
            return v if v > 0 else None
        except Exception:
            mb.showerror("Пакет", f"Ожидается целое число, получено: '{t}'")
            return None

    def _can_fast_enrich(self, rows):
        """Проверяем, одинаковый ли GTIN во всём пакете (можно обогащать один раз)."""
        try:
            gtins = set()
            for r in rows:
                g = only_digits((r.get("GTIN", "") or "").strip())
                if g:
                    gtins.add(g)
                else:
                    return False  # пустой GTIN — не рискуем ускорять
                if len(gtins) > 1:
                    return False
            return bool(gtins)
        except Exception:
            return False

    def _render_batch_buttons(self, batches, idx0):
        # очистка
        try:
            for w in self.batch_btns_frame.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass
        except Exception:
            pass
        if not batches:
            ctk.CTkLabel(self.batch_btns_frame, text="Нет пакетов для печати.").pack(anchor="w", padx=4, pady=4)
            return
        for i, (start, end) in enumerate(batches, start=1):
            label = f"Пакет {i} ({idx0 + start + 1}-{idx0 + end})"
            btn = ctk.CTkButton(self.batch_btns_frame, text=label, height=32,
                                 command=lambda b=i: self._reprint_batch(b),
                                 state="normal" if (
                                     self._bartender_available()
                                     and self.catalog_state == "ready"
                                 ) else "disabled")
            btn.pack(side="left", padx=6, pady=4)

    def _reprint_batch(self, batch_idx):
        if not self._require_print_capabilities("Повторная печать пакета"):
            return
        try:
            batches = self.last_batches or []
        except Exception:
            batches = []
        if not batches or batch_idx < 1 or batch_idx > len(batches):
            try:
                self.logger.err("Нет данных для повторной печати этого пакета.")
            except Exception:
                pass
            return
        self.logger.log(f"Повторная печать пакета {batch_idx}.")
        try:
            self._print_range_one_job_via_csv(target_batches=[batches[batch_idx - 1]])
        except Exception as e:
            try:
                self.logger.err(f"Повторная печать пакета {batch_idx} не удалась: {e}")
            except Exception:
                pass


    def _get_printer(self):
        prn = self.prn_combo.get()
        if not prn or prn == "(нет принтеров)":
            mb.showerror("Принтер", "Принтер не выбран.")
            return None
        return prn

    def _get_sticker_printer(self):
        """Ищем принтер для стикеров: в конфиге или по умолчанию TE210."""
        try:
            cfg_val = (self.cfg.get("sticker_printer") or "").strip()
        except Exception:
            cfg_val = ""
        try:
            values = list(self.prn_combo.cget("values") or [])
        except Exception:
            values = []
        candidates = []
        if cfg_val:
            candidates.append(cfg_val)
        # по умолчанию ищем TE210
        candidates.append("TE210")
        for cand in candidates:
            for v in values:
                if cand.lower() in v.lower():
                    return v
        # запасной вариант - выбранный принтер
        try:
            return self._get_printer()
        except Exception:
            return None

    def _record_sticker_job(self, bidx, start, end, total, sample_row, km_count, total_rolls):
        if not sample_row:
            return
        try:
            sample_copy = dict(sample_row)
        except Exception:
            sample_copy = sample_row
        job = {
            "bidx": int(bidx),
            "start": int(start),
            "end": int(end),
            "total": int(total),
            "sample_row": sample_copy,
            "km_count": int(km_count or 0),
            "total_rolls": int(total_rolls or 0),
            "label": f"Пакет {bidx} ({start}-{end})",
        }
        for i, existing in enumerate(self.last_sticker_jobs):
            if existing.get("bidx") == job["bidx"]:
                self.last_sticker_jobs[i] = job
                return
        self.last_sticker_jobs.append(job)

    def _open_sticker_reprint_dialog(self):
        if not self._require_print_capabilities("Повторная печать стикера"):
            return
        jobs = list(self.last_sticker_jobs or [])
        if not jobs:
            mb.showerror("Стикер", "Нет данных для повторной печати стикера. Сначала напечатайте коды.")
            return
        jobs.sort(key=lambda j: j.get("bidx", 0))
        options = [j.get("label") or f"Пакет {j.get('bidx','?')}" for j in jobs]

        win = ctk.CTkToplevel(self)
        win.title("Повторная печать стикера")
        win.geometry("420x180")
        win.grab_set()
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.after(150, lambda: win.attributes("-topmost", False))
            win.focus_force()
        except Exception:
            pass

        body = ctk.CTkFrame(win)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(body, text="Выбери пакет для печати стикера:").pack(anchor="w", pady=(0, 6))
        var = tk.StringVar(value=options[0])
        ctk.CTkOptionMenu(body, variable=var, values=options).pack(fill="x", pady=(0, 8))

        btns = ctk.CTkFrame(win)
        btns.pack(pady=(0, 10))

        def _print_one():
            label = var.get()
            job = next((j for j in jobs if j.get("label") == label), None)
            if not job:
                return
            self._print_sticker_placeholder(
                job["bidx"],
                job["start"],
                job["end"],
                job["total"],
                sample_row=job.get("sample_row"),
                km_count=job.get("km_count"),
                total_rolls=job.get("total_rolls"),
            )

        def _print_all():
            for job in jobs:
                self._print_sticker_placeholder(
                    job["bidx"],
                    job["start"],
                    job["end"],
                    job["total"],
                    sample_row=job.get("sample_row"),
                    km_count=job.get("km_count"),
                    total_rolls=job.get("total_rolls"),
                )

        ctk.CTkButton(btns, text="Печать", command=_print_one, width=110).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Печать всех", command=_print_all, width=110).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Закрыть", command=win.destroy, width=90, fg_color="#444").pack(side="left", padx=6)

    def _build_sticker_payload(
        self,
        bidx,
        start,
        end,
        total,
        sample_row=None,
        km_count=None,
        total_rolls=None,
        client_name=None,
        log_details=False,
    ):
        """Build the exact Named Data Source payload shared by sticker print and preview."""
        payload = {
            "PKG": str(bidx),
            "PACKAGE": str(bidx),
            "RANGE": f"{start}-{end}",
            "KM_RANGE": f"{start}-{end}",
            "START": str(start),
            "KM_FROM": str(start),
            "END": str(end),
            "KM_TO": str(end),
            "TOTAL": str(total),
            "KM_TOTAL": str(total),
        }
        if total_rolls:
            payload["ROLL"] = str(bidx)
            payload["ROLL_TOTAL"] = str(total_rolls)
            payload["ROLL_OF"] = f"{bidx} из {total_rolls}"

        info = {}
        if sample_row:
            gtin = sample_row.get("GTIN", "")
            payload.update({
                "GTIN": gtin,
                "ShortNAME": sample_row.get("ShortName", ""),
                "ShortName": sample_row.get("ShortName", ""),
                "DM": sample_row.get("DM", ""),
                "NAME": sample_row.get("NAME", ""),
                "PROD_DATE": sample_row.get("PROD_DATE", ""),
                "EXP_DATE": sample_row.get("EXP_DATE", ""),
                "PART_NUM": sample_row.get("PART_NUM", ""),
                "BRAND": sample_row.get("BRAND", ""),
                "Бренд": sample_row.get("BRAND", ""),
                "CLIENT": sample_row.get("CLIENT", ""),
                "Клиент": sample_row.get("CLIENT", ""),
                "PACKAGING": sample_row.get("PACKAGING", ""),
                "Упаковка": sample_row.get("PACKAGING", ""),
                "WEIGHT": sample_row.get("WEIGHT", ""),
                "Вес": sample_row.get("WEIGHT", ""),
            })
            try:
                from utils import only_digits as _od
                product_map = self.__dict__.get("product_map") or {}
                info = product_map.get(_od(gtin), {})
            except Exception:
                info = {}
            payload.update({
                "Бренд": info.get("BRAND", "") or info.get("Бренд", "") or payload.get("BRAND", ""),
                "Бренд_": info.get("BRAND", "") or payload.get("BRAND", ""),
                "BRAND": info.get("BRAND", "") or payload.get("BRAND", ""),
                "Клиент": info.get("CLIENT", "") or info.get("Клиент", "") or payload.get("CLIENT", ""),
                "CLIENT": info.get("CLIENT", "") or payload.get("CLIENT", ""),
                "Упаковка": info.get("PACKAGING", "") or info.get("PACK", "") or payload.get("PACKAGING", ""),
                "PACKAGING": info.get("PACKAGING", "") or payload.get("PACKAGING", ""),
                "Вес": info.get("WEIGHT", "") or payload.get("WEIGHT", ""),
                "WEIGHT": info.get("WEIGHT", "") or payload.get("WEIGHT", ""),
            })
            if log_details:
                try:
                    self.logger.log(
                        "[СТИКЕР] weight debug: "
                        f"sample_row='{sample_row.get('WEIGHT','')}', "
                        f"map='{info.get('WEIGHT','')}', "
                        f"path='{(self.__dict__.get('cfg') or {}).get('product_map_path','')}'"
                    )
                except Exception:
                    pass

        if client_name is None:
            client_name = self._get_client_name()
        client_name = str(client_name or "").strip()
        if client_name:
            payload["CLIENT"] = client_name
            payload["Клиент"] = client_name

        if km_count is not None:
            payload["КоличествоКМ"] = str(km_count)
            payload["KM_COUNT"] = str(km_count)

        if log_details:
            try:
                self.logger.log(
                    f"[СТИКЕР] GTIN={payload.get('GTIN','')} "
                    f"BRAND='{payload.get('BRAND','')}' "
                    f"CLIENT='{payload.get('CLIENT','')}' "
                    f"PACKAGING='{payload.get('PACKAGING','')}' "
                    f"WEIGHT='{payload.get('WEIGHT','')}'"
                )
            except Exception:
                pass
        return payload

    def _resolve_sticker_preview_context(self, silent=False):
        rows = self.__dict__.get("csv_rows") or []
        if not rows:
            return None
        try:
            start_row, _end_row, limit = resolve_print_selection(
                self.print_selection_mode_var.get(),
                self.index_entry.get(),
                self.limit_entry.get(),
                total_rows=len(rows),
            )
        except (TypeError, ValueError) as exc:
            if not silent:
                mb.showerror("Предпросмотр стикера", str(exc))
            return None

        idx0 = start_row - 1
        selected = rows[idx0:idx0 + limit]
        total = len(selected)
        if total <= 0:
            return None

        try:
            raw_batch = (self.batch_entry.get() or "").strip()
        except Exception:
            raw_batch = ""
        if not raw_batch or raw_batch == "0":
            batch_size = total
        else:
            try:
                parsed_batch = int(raw_batch)
            except (TypeError, ValueError):
                if not silent:
                    mb.showerror(
                        "Предпросмотр стикера",
                        f"Пакет должен быть целым числом, получено: '{raw_batch}'.",
                    )
                return None
            batch_size = parsed_batch if parsed_batch > 0 else total

        first_batch = selected[:batch_size]
        if not first_batch:
            return None
        sample_row = self._enrich(
            first_batch[0],
            start_row,
            allow_empty_part=True,
            silent_validation=silent,
        )
        if not sample_row:
            return None

        total_rolls = (total + batch_size - 1) // batch_size
        return {
            "bidx": 1,
            "start": start_row,
            "end": start_row + len(first_batch) - 1,
            "total": total,
            "sample_row": sample_row,
            "km_count": len(first_batch),
            "total_rolls": total_rolls,
        }

    def _preview_marking_sticker(self, silent=False):
        if silent:
            if self.__dict__.get("catalog_state") != "ready" or not self._bartender_available():
                return False
        elif not self._require_print_capabilities("Предпросмотр маркировочного стикера"):
            return False

        if not self.__dict__.get("csv_path"):
            if not silent:
                mb.showerror("CSV", "Выбери CSV")
            return False
        if not self.__dict__.get("csv_rows"):
            try:
                self.csv_rows = load_kontur_raw(self.csv_path)
            except Exception as exc:
                self.logger.err(f"CSV ошибка: {exc}")
                return False
        if not self.csv_rows:
            if not silent:
                mb.showerror("CSV", "Нет данных")
            return False

        cfg = self.__dict__.get("cfg") or {}
        sticker_path = resolve_sticker_template_path(cfg)
        if not sticker_path:
            if not silent:
                mb.showerror(
                    "Предпросмотр стикера",
                    f"Шаблон не найден: {sticker_template_display_path(cfg)}.",
                )
            return False

        context = self._resolve_sticker_preview_context(silent=silent)
        if not context:
            return False
        payload = self._build_sticker_payload(
            context["bidx"],
            context["start"],
            context["end"],
            context["total"],
            sample_row=context["sample_row"],
            km_count=context["km_count"],
            total_rolls=context["total_rolls"],
            client_name=self._get_client_name(),
        )

        fmt = None
        preview_dir = None
        try:
            preview_dir = tempfile.mkdtemp(prefix="autoprint-sticker-preview-")
            out_template = os.path.join(preview_dir, "preview_%PageNumber%.png")
            fmt = self.bt.open_format(sticker_path)
            self.bt.set_common_print_flags(fmt)
            fmt.PrintSetup.IdenticalCopiesOfLabel = 1
            if not self.bt.apply_fields(fmt, payload):
                self.logger.err(
                    "Стикер preview: не удалось подставить значения "
                    "(проверь имена полей в BTW)."
                )
            if not self.bt.export_preview(fmt, out_template):
                self.logger.err("Не удалось сформировать preview маркировочного стикера.")
                return False

            generated = sorted(
                os.path.join(preview_dir, name)
                for name in os.listdir(preview_dir)
                if name.lower().endswith(".png")
            )
            if not generated:
                self.logger.err(
                    "BarTender сообщил успех, но файл preview стикера не найден."
                )
                return False
            if not self._show_preview_path(generated[0], preserve_on_error=silent):
                return False

            old_dir = self.__dict__.get("_preview_temp_dir")
            self._preview_temp_dir = preview_dir
            preview_dir = None
            if old_dir and old_dir != self._preview_temp_dir:
                self._cleanup_preview_temp_dir(old_dir)
            self.logger.log(
                "Предпросмотр маркировочного стикера обновлён "
                f"(пакет {context['bidx']}, строки {context['start']}-{context['end']})."
            )
            return True
        except Exception as exc:
            self.logger.err(
                f"Предпросмотр маркировочного стикера: {exc}\n{traceback.format_exc()}"
            )
            return False
        finally:
            if fmt is not None:
                try:
                    fmt.Close(1)
                except Exception:
                    pass
            if preview_dir:
                self._cleanup_preview_temp_dir(preview_dir)

    def _print_sticker_placeholder(self, bidx, start, end, total, sample_row=None, km_count=None, total_rolls=None):
        """Печать стикера через BarTender, если есть шаблон sticker60x40.btw (или cfg['sticker_btw'])."""
        if not self._require_print_capabilities("Печать стикера"):
            return
        prn = self._get_sticker_printer()
        if not prn:
            self.logger.err("Стикер: принтер не найден.")
            return
        sticker_path = resolve_sticker_template_path(self.cfg)
        if not sticker_path:
            self.logger.err(
                f"Стикер: шаблон не найден: {sticker_template_display_path(self.cfg)}."
            )
            return
        client_name = self._get_client_name()
        payload = self._build_sticker_payload(
            bidx,
            start,
            end,
            total,
            sample_row=sample_row,
            km_count=km_count,
            total_rolls=total_rolls,
            client_name=client_name,
            log_details=True,
        )
        if client_name:
            try:
                self.cfg["sticker_client"] = client_name
                save_config(self.cfg)
            except Exception:
                pass
        try:
            fmt = self.bt.open_format(sticker_path)
            self.bt.set_common_print_flags(fmt)
            fmt.PrintSetup.Printer = prn
            fmt.PrintSetup.IdenticalCopiesOfLabel = 1
            try:
                self.bt.apply_fields(fmt, payload)
            except Exception:
                pass
            fmt.PrintOut(False, False)
            self.logger.log(f"Стикер отправлен: пакет {bidx} ({start}-{end}) → принтер '{prn}' (шаблон: {sticker_path}).")
        except Exception as e:
            try:
                self.logger.err(f"Стикер не напечатан: {e}")
            except Exception:
                pass
        finally:
            try:
                fmt.Close(1)
            except Exception:
                pass

    def _get_prod_date(self, silent=False):
        try:
            txt = (self.prod_date_entry.get() or "").strip()
        except Exception:
            txt = ""

        try:
            return parse_date_ru(txt)
        except Exception as e:
            if not silent:
                mb.showerror("Дата производства", str(e))
            return None

    def _get_client_name(self):
        try:
            return (self.client_entry.get() or "").strip()
        except Exception:
            return ""

    def _mark_part_entry(self, error=False):
        """Подсветка поля партии, если нужно обратить внимание."""
        try:
            if error:
                self.part_entry.configure(border_color="#CC0000")
                self._part_missing_logged = True
            else:
                # вернуть дефолтный цвет
                if getattr(self, "part_entry_border_default", None) is not None:
                    self.part_entry.configure(border_color=self.part_entry_border_default)
                else:
                    self.part_entry.configure(border_color=None)
                self._part_missing_logged = False
        except Exception:
            pass

    def _mark_prod_date_entry(self, error=False):
        try:
            if error:
                self.prod_date_entry.configure(border_color="#CC0000")
            else:
                if getattr(self, "prod_date_border_default", None) is not None:
                    self.prod_date_entry.configure(border_color=self.prod_date_border_default)
                else:
                    self.prod_date_entry.configure(border_color=None)
        except Exception:
            pass

    def _format_prod_date(self, event=None):
        """Форматирует ввод даты ДД.ММ.ГГГГ и подсвечивает при ошибке."""
        try:
            cur_text = self.prod_date_entry.get() or ""
            try:
                caret_old = int(self.prod_date_entry.index("insert"))
            except Exception:
                caret_old = len(cur_text)
            digits_before = sum(1 for ch in cur_text[:caret_old] if ch.isdigit())

            typing_at_end = (caret_old >= len(cur_text))

            digits_all = re.sub(r"\D", "", cur_text)[:8]
            if not digits_all:
                self.prod_date_entry.delete(0, "end")
                self._mark_prod_date_entry(False)
                return

            # Если пользователь набирает подряд без точек — разбиваем по позициям,
            # иначе используем реальные сегменты, чтобы цифры не сдвигались.
            if "." not in cur_text:
                d_raw = digits_all
                dd = d_raw[:2]
                mm = d_raw[2:4]
                yyyy = d_raw[4:8]
            else:
                parts = (cur_text.split(".") + ["", "", ""])[:3]
                dd = re.sub(r"\D", "", parts[0])[:2]
                mm = re.sub(r"\D", "", parts[1])[:2]
                yyyy = re.sub(r"\D", "", parts[2])[:4]

            total_digits = len(dd) + len(mm) + len(yyyy)

            # Добавляем точки только если сегмент заполнен или есть следующие сегменты
            text = dd
            if len(dd) == 2 or mm or yyyy or ("." in cur_text and len(cur_text.split(".")) > 1):
                text += "."
            text += mm
            if len(mm) == 2 or yyyy or ("." in cur_text and len(cur_text.split(".")) > 2):
                text += "."
            text += yyyy

            if text != cur_text:
                self.prod_date_entry.delete(0, "end")
                self.prod_date_entry.insert(0, text)
                # восстановим каретку по количеству цифр слева
                target_digits = min(digits_before, total_digits)
                caret_new = len(text) if (typing_at_end and target_digits == total_digits) else 0
                if caret_new == 0:
                    seen = 0
                    for idx, ch in enumerate(text):
                        if ch.isdigit():
                            seen += 1
                        if seen >= target_digits:
                            caret_new = idx + 1
                            break
                    else:
                        caret_new = len(text)
                try:
                    self.prod_date_entry.icursor(caret_new)
                except Exception:
                    pass

            # валидация
            if total_digits != 8:
                self._mark_prod_date_entry(True)
            else:
                try:
                    parse_date_ru(text.replace("..", ".").rstrip("."))
                    self._mark_prod_date_entry(False)
                except Exception:
                    self._mark_prod_date_entry(True)
        except Exception:
            pass

    def _apply_auto_scaling(self):
        """Авто-масштабирование под разрешение экрана (полезно для 1366x768)."""
        try:
            try:
                # фиксируем оконный DPI, чтобы не накапливался двойной масштаб Windows + customtkinter
                ctk.set_window_scaling(1.0)
            except Exception:
                pass
            sw = max(1, int(self.winfo_screenwidth()))
            sh = max(1, int(self.winfo_screenheight()))
            target_w, target_h = 1300, 900
            # если экран небольшой (<=1600x900), принудительно уменьшаем до ~80%
            if sw <= 1600 or sh <= 900:
                scale = 0.8
            else:
                width = min(target_w, sw - 40)
                height = min(target_h, sh - 80)
                scale = min(width / target_w, height / target_h, 1.0)
                scale = max(scale, 0.8)
            ctk.set_widget_scaling(scale)
            geom = f"{int(target_w * scale)}x{int(target_h * scale)}"
            self.geometry(geom)
            self.minsize(int(800 * scale), int(600 * scale))
            try:
                self.scale_var.set("auto")
            except Exception:
                pass
        except Exception:
            # если что-то пошло не так - оставляем дефолт
            try:
                self.geometry("1300x900")
            except Exception:
                pass

    def _apply_manual_scale(self, scale):
        """Ручной масштаб, если авто вдруг «поплыл»."""
        try:
            scale_f = float(scale)
        except Exception:
            return self._apply_auto_scaling()
        scale_f = min(max(scale_f, 0.7), 1.4)
        try:
            ctk.set_window_scaling(1.0)
        except Exception:
            pass
        target_w, target_h = 1300, 900
        ctk.set_widget_scaling(scale_f)
        geom = f"{int(target_w * scale_f)}x{int(target_h * scale_f)}"
        try:
            self.geometry(geom)
            self.minsize(int(800 * scale_f), int(600 * scale_f))
        except Exception:
            pass

    # ---------- client list helpers ----------
    def _clients_list(self):
        try:
            lst = self.cfg.get("clients_list") or []
            return list(lst)
        except Exception:
            return []

    def _save_clients_list(self, lst):
        try:
            self.cfg["clients_list"] = lst
            save_config(self.cfg)
        except Exception:
            pass

    def _refresh_client_combo(self, select_value=None):
        try:
            values = sorted(set(self._clients_list()), key=lambda s: s.lower())
            values_with_dash = ["Выбери клиента"] + values
            self.client_combo.configure(values=values_with_dash)
            target = "Выбери клиента"  # по умолчанию подсказка
            if select_value and select_value in values_with_dash:
                target = select_value
            self.client_combo.set(target)
        except Exception:
            pass

    def _on_client_select(self, value):
        try:
            v = (value or "").strip()
            self.client_entry.delete(0, "end")
            if v and v != "Выбери клиента":
                self.client_entry.insert(0, v)
                # убираем подсказку из списка после выбора реального клиента
                vals = [x for x in (self.client_combo.cget("values") or []) if x != "Выбери клиента"]
                if vals:
                    self.client_combo.configure(values=vals)
                    self.client_combo.set(v)
        except Exception:
            pass
        self._schedule_live_preview()

    def _client_add(self):
        name = self._get_client_name()
        if not name:
            mb.showerror("Клиент", "Введите название клиента.")
            return
        lst = self._clients_list()
        if name not in lst:
            lst.append(name)
            lst = sorted(set(lst), key=lambda s: s.lower())
            self._save_clients_list(lst)
        try:
            self._refresh_client_combo(name)
        except Exception:
            pass

    def _client_delete(self):
        try:
            cur = (self.client_combo.get() or "").strip()
        except Exception:
            cur = ""
        if cur == "-" or cur == "":
            return
        try:
            if not mb.askyesno("Удалить клиента", f"Удалить клиента '{cur}' из списка?"):
                return
        except Exception:
            pass
        lst = self._clients_list()
        if cur and cur in lst:
            lst = [x for x in lst if x != cur]
            self._save_clients_list(lst)
        try:
            self._refresh_client_combo(None)
        except Exception:
            pass

    def _client_clear(self):
        try:
            self.client_entry.delete(0, "end")
        except Exception:
            pass
        try:
            # вернём подсказку в комбо
            self._refresh_client_combo(None)
        except Exception:
            pass
        self._schedule_live_preview()

    def _get_exp_days(self, silent=False):
        t = (self.exp_days_entry.get() or "").strip()
        if not t:
            return None
        try:
            return max(0, int(t))
        except Exception:
            if not silent:
                mb.showerror("Срок годности (дней)", f"Ожидается целое число дней, получено: '{t}'")
            return None

    def _get_index(self):
        t = (self.index_entry.get() or "").strip()
        try:
            i = int(t or "1")
        except Exception:
            i = 1
        return max(1, i)

    def _get_limit(self):
        t = (self.limit_entry.get() or "").strip()
        if t == "" or t == "0":
            return None
        try:
            v = int(t)
            if v <= 0:
                return None
            return v
        except Exception:
            mb.showerror("Лимит", f"Ожидается целое число, получено: '{t}'")
            return None
        try:
            return max(1, int(t))
        except Exception:
            mb.showerror("Лимит", f"Ожидается целое число, получено: '{t}'")
            return None

    def _get_btw_for_format(self, fmt_name: str, interactive=True):

        """

        BTW ищем по описанию шаблона в templates рядом с .exe/.py.
        Если файла нет — спрашиваем путь и копируем выбранный BTW в templates
        только для явного интерактивного действия.

        """

        fmt_name = self._normalize_template_id(fmt_name)
        resolved = self._resolve_template_btw_path(fmt_name)
        if resolved:
            return resolved

        # если ранее в скрытых полях что-то было - используем для старых 16x16/30x20
        try:
            if fmt_name == "16x16":
                path = self.btw16_entry.get().strip()
            elif fmt_name == "30x20":
                path = self.btw30_entry.get().strip()
            else:
                path = ""
        except Exception:
            path = ""
        if path and os.path.isfile(path):
            return path

        if not interactive:
            return None

        try:
            if getattr(self, "logger", None):
                self.logger.err(f"BTW для шаблона '{fmt_name}' не найден. Укажи путь к файлу BTW.")
        except Exception:
            pass

        p = fd.askopenfilename(title=f"Укажи BTW для {fmt_name}", filetypes=[("BarTender Template", "*.btw")])
        if p and os.path.isfile(p):
            try:
                target, file_name = self._copy_template_into_workspace(p, fmt_name)
                meta = self._get_label_template(fmt_name) or {
                    "id": fmt_name,
                    "name": fmt_name,
                    "part_mode": "blank",
                    "printer": "",
                }
                meta["file_name"] = file_name
                self.label_templates[fmt_name] = meta
                self._save_label_templates_to_cfg()
                try:
                    if fmt_name == "16x16":
                        self.btw16_entry.delete(0, "end")
                        self.btw16_entry.insert(0, target)
                    elif fmt_name == "30x20":
                        self.btw30_entry.delete(0, "end")
                        self.btw30_entry.insert(0, target)
                except Exception:
                    pass
                return target
            except Exception:
                return p

        mb.showerror("BTW", f"Шаблон BTW для '{fmt_name}' не найден.")
        return None

    
    def _enrich(self, base_row, idx1, allow_empty_part=False, silent_validation=False):
        d = self._get_prod_date(silent=silent_validation)
        if not d:
            return None
        exp_days_override = self._get_exp_days(silent=silent_validation)
        enr = enrich_row(
            base_row=base_row,
            idx1=idx1,
            prod_date=d,
            exp_days_override=exp_days_override,
            product_map=self.product_map,
            mode_choice=self.format_combo.get()
        )

        # режим партии определяется шаблоном печати
        manual = (self.part_entry.get() or "").strip()
        template_id = self._normalize_template_id(enr.get("_FORMAT"))
        part_mode = self._template_part_mode(template_id)
        if part_mode == "manual":
            if not manual and not allow_empty_part:
                self._mark_part_entry(True)
                if not self.__dict__.get("_part_missing_logged", False):
                    self._part_missing_logged = True
                    self.logger.err(f"Не заполнен номер партии для шаблона {template_id}.")
                return None
            enr["PART_NUM"] = manual
        else:
            enr["PART_NUM"] = ""
        if manual or allow_empty_part or part_mode != "manual":
            self._mark_part_entry(False)

        gtin_key = only_digits(base_row.get("GTIN", ""))
        info = self.product_map.get(gtin_key, {})
        shelf = info.get("SHELF") or {}
        shelf_desc = shelf.get("raw") or "-"
        self.logger.log(
            f"GTIN lookup: TEMPLATE='{info.get('TEMPLATE_ID') or info.get('FORMAT','-') or '-'}', "
            f"SHELF='{shelf_desc}', SHELF_PARSED={enr.get('_SHELF_LOG','')}, "
            f"ShortNameExcel='{(info.get('SHORTNAME') or '').strip()}', GTIN={gtin_key}"
        )

        src = "не задан"
        if enr.get("_SHELF_LOG"):
            src = enr["_SHELF_LOG"]
        elif exp_days_override is not None:
            src = f"override_days={exp_days_override}"
        self.logger.log(f"Срок годности: {src}; EXP_DATE={enr.get('EXP_DATE','')}")

        # источник ShortName
        self.logger.log(f"ShortName источник: {enr.get('_SHORT_SRC','?')} → '{enr.get('ShortName','')}'")
        return enr

    def _show_preview_path(self, path, preserve_on_error=False):
        try:
            with Image.open(path) as source:
                img = source.copy()
            self.preview_label.update_idletasks()
            box_w = max(320, int(self.preview_label.winfo_width() or 560)) - 40
            box_h = max(240, int(self.preview_label.winfo_height() or 360)) - 40
            iw, ih = img.size
            ratio = min(box_w / iw, box_h / ih)
            new_w, new_h = max(1, int(iw * ratio)), max(1, int(ih * ratio))
            self.preview_ctkimg = ctk.CTkImage(light_image=img, dark_image=img, size=(new_w, new_h))
            self.preview_label.configure(image=self.preview_ctkimg, text="")
            self.preview_current_path = path
            return True
        except Exception as e:
            self.logger.err(f"Не удалось показать превью: {e}")
            if not preserve_on_error:
                self.preview_label.configure(text="Не удалось показать превью")
            return False

    def _cleanup_preview_temp_dir(self, path=None):
        state = self.__dict__
        target = path if path is not None else state.get("_preview_temp_dir")
        if not target:
            return
        if path is None or target == state.get("_preview_temp_dir"):
            state["_preview_temp_dir"] = None
        try:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
        except Exception:
            pass

    def _preprint_stickers_for_all_batches(self, batches, rows_all, idx0, total, total_rolls, record_jobs=False):
        """Печать стикеров сразу на все пакеты перед основной печатью."""
        try:
            if self._bool_var(getattr(self, "skip_sticker_var", False)):
                return False
            printed = 0
            for bidx, (start, end) in enumerate(batches, start=1):
                try:
                    rows = rows_all[start:end]
                    if not rows:
                        continue
                    idx1 = idx0 + start + 1
                    enr = self._enrich(rows[0], idx1)
                    if not enr:
                        self.logger.err(f"Стикер пакет {bidx}: данные не сформированы (пропуск).")
                        continue
                    if record_jobs:
                        self._record_sticker_job(
                            bidx,
                            idx0 + start + 1,
                            idx0 + end,
                            total,
                            enr,
                            len(rows),
                            total_rolls,
                        )
                    self._print_sticker_placeholder(
                        bidx,
                        idx0 + start + 1,
                        idx0 + end,
                        total,
                        sample_row=enr,
                        km_count=len(rows),
                        total_rolls=total_rolls,
                    )
                    printed += 1
                except Exception as e:
                    try:
                        self.logger.err(f"Стикер пакет {bidx}: ошибка предварительной печати: {e}")
                    except Exception:
                        pass
            return printed > 0
        except Exception:
            return False

    def _on_preview_resize(self, event=None):
        """Пересчитывает превью при изменении размеров контейнера (например, при переносе на другой монитор)."""
        if getattr(self, "_preview_resize_busy", False):
            return

        pending = getattr(self, "_preview_resize_after_id", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except Exception:
                pass
        self._preview_resize_after_id = None

        if not getattr(self, "preview_current_path", None):
            return

        def refresh():
            self._preview_resize_after_id = None
            if getattr(self, "_preview_resize_busy", False):
                return
            path = getattr(self, "preview_current_path", None)
            if not path:
                return
            self._preview_resize_busy = True
            try:
                if os.path.isfile(path):
                    self._show_preview_path(path)
            except Exception:
                pass
            finally:
                self._preview_resize_busy = False

        try:
            self._preview_resize_after_id = self.after(
                PREVIEW_RESIZE_DEBOUNCE_MS, refresh
            )
        except Exception:
            self._preview_resize_after_id = None
    
    def _maybe_calibrate(self, fmt, copies):
        """
        Новая логика: никаких отдельных калибровочных заданий.
        6 строк 'X' будут добавлены в enriched_rows/tmp_batch перед печатью каждого батча.
        Здесь только логируем.
        """
        try:
            if bool(self.calib_var.get()):
                self.logger.log("КАЛИБРОВКА: будет выполнена через tmp_batch (6 строк 'X').")
            else:
                self.logger.log("КАЛИБРОВКА: выключена.")
        except Exception:
            pass
        return
    
    
    def _bt_print(self, fmt, copies: int, show_dialog: bool):
    
        self._disable_auto_print_again(fmt)

        # Если выбран режим "Одно задание" — используем IdenticalCopiesOfLabel
        try:
            sj = False
            if hasattr(self, "single_job_var"):
                if hasattr(self.single_job_var, "get"):
                    sj = bool(self.single_job_var.get())
                else:
                    sj = bool(self.single_job_var)
            if sj and not show_dialog:
                try:
                    fmt.IdenticalCopiesOfLabel = int(copies)
                except Exception:
                    pass
                try:
                    self.logger.log(f"SingleJob: IdenticalCopiesOfLabel={copies}")
                except Exception:
                    pass
                try:
                    # Одно задание, без диалогов
                    fmt.PrintOut(False, True) if self._dialog_flag() else fmt.PrintOut(False, False)
                    return True
                except Exception as e:
                    try:
                        self.logger.err(f"SingleJob PrintOut error: {e}")
                    except Exception:
                        pass
                    # Падать не будем — ниже сработают обычные пути
        except Exception:
            pass
        if getattr(self, 'cancel_requested', False):
            try:
                self.logger.log('Печать отменена пользователем (до отправки).')
            except Exception:
                pass
            return False
        """
        Если show_dialog=True: пробуем 2-булевые перегрузки (A1/A2) и затем трёхаргументную (B).
        Если show_dialog=False: пропускаем A1/A2 полностью, используем B или тихий C.
        Ошибку 'Недопустимое число параметров' в B понижаем до инфо-лога, если show_dialog=False.
        """
        prompt = bool(show_dialog)
    
        if False and prompt:
            # A1: две булевые с ожиданием
            try:
                self.logger.log("BT Print: A1 -> PrintOut(True, True)")
                fmt.PrintOut(True, True)
                return True
            except Exception as e:
                self.logger.err(f"BT Print A1 ошибка: {e}")
            # A2: две булевые без ожидания
            try:
                self.logger.log("BT Print: A2 -> PrintOut(True, False)")
                fmt.PrintOut(True, False)
                return True
            except Exception as e:
                self.logger.err(f"BT Print A2 ошибка: {e}")
    
        # B: трёхаргументная перегрузка (copies, serialized, showDialog)
        try:
            self.logger.log(f"BT Print: B -> PrintOut(Copies={int(copies)}, Serialized=False, ShowDialog={prompt})")
            fmt.PrintOut(int(copies), False, prompt)
            return True
        except Exception as e:
            if False and prompt:
                self.logger.err(f"BT Print B ошибка: {e}")
            else:
                self.logger.log(f"BT Print: B недоступна (перехожу в C): {e}")
    
        # C: fallback — полностью тихо
        try:
            self.logger.log("BT Print: C -> PrintOut(False, False) (тихо)")
            if self._should_stop():
                return False
            fmt.PrintOut(False, True) if self._dialog_flag() else fmt.PrintOut(False, False)
            if False and prompt:
                self.logger.err("Диалог печати не поддерживается COM — выполнена тихая печать.")
            return True
        except Exception as e:
            self.logger.err(f"Печать не удалась: {e}")
            return False
    
    
    # ---------- pause helpers ----------
    def _toggle_pause(self):
        # Переключатель Пауза / Продолжить (без отмены заданий)
        try:
            self.is_paused = not self.is_paused
            if getattr(self, "pause_btn", None):
                self.pause_btn.configure(text=("Продолжить" if self.is_paused else "Пауза"))
            if getattr(self, "logger", None):
                try:
                    self.logger.log("Пауза включена." if self.is_paused else "Пауза снята.")
                except Exception:
                    pass
        except Exception:
            pass
    
    def _pause_wait(self):
        # Мягкая пауза: пока включена — поддерживаем отзывчивость GUI
        while self.is_paused:
            try:
                self.update_idletasks()
                self.update()
            except Exception:
                pass
            if getattr(self, "cancel_requested", False):
                break
            time.sleep(0.02)
    
    def _should_stop(self):
        return bool(getattr(self, "cancel_requested", False))
    
    
    # ---------- date picker ----------
    def _open_date_picker_legacy(self):
        """
        Открывает выбор даты. Если установлен пакет tkcalendar - используем его.
        Иначе показываем встроенный календарь без внешних зависимостей.
        """
        try:
            import tkcalendar  # type: ignore
            top = ctk.CTkToplevel(self)
            top.title("Выбор даты производства")
            cal = tkcalendar.Calendar(top, selectmode='day', date_pattern='dd.mm.yyyy')
            cal.pack(padx=10, pady=10)
            def on_ok():
                self.prod_date_entry.delete(0, "end")
                self.prod_date_entry.insert(0, cal.get_date())
                top.destroy()
            ctk.CTkButton(top, text="OK", command=on_ok).pack(pady=8)
            ctk.CTkButton(top, text="Отмена", command=top.destroy).pack(pady=4)
            top.grab_set()
            return
        except Exception as e:
            if getattr(self, "logger", None):
                try:
                    self.logger.err(f"Календарь: tkcalendar недоступен: {e}")
                except Exception:
                    pass

        # Фолбэк: встроенный календарь
        import datetime as _dt
        import calendar as _cal
        top = ctk.CTkToplevel(self)
        top.title("Выбор даты")
        top.resizable(False, False)

        # Текущая дата из поля
        cur = (self.prod_date_entry.get() or "").strip()
        try:
            d, m, y = [int(x) for x in cur.split(".")]
            _dt.date(y, m, d)
        except Exception:
            today = _dt.date.today()
            d, m, y = today.day, today.month, today.year

        state = {"day": d, "month": m, "year": y}
        month_names = [
            "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
        ]
        weekday_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

        header = ctk.CTkFrame(top)
        header.pack(padx=12, pady=(12, 4), fill="x")
        month_label = ctk.CTkLabel(header, text="")

        days_header = ctk.CTkFrame(top)
        days_header.pack(padx=12)
        for i, name in enumerate(weekday_names):
            ctk.CTkLabel(days_header, text=name, width=28).grid(row=0, column=i, padx=2, pady=2)

        days_frame = ctk.CTkFrame(top)
        days_frame.pack(padx=12, pady=(0, 6))

        info = ctk.CTkLabel(top, text="")
        info.pack(pady=(0, 4))

        def update_info():
            info.configure(text=f"Выбрано: {state['day']:02d}.{state['month']:02d}.{state['year']:04d}")

        def select_day(day):
            state["day"] = day
            update_info()

        def build_calendar():
            month_label.configure(text=f"{month_names[state['month'] - 1]} {state['year']}")
            for child in days_frame.winfo_children():
                child.destroy()
            cal = _cal.Calendar(firstweekday=0)
            weeks = cal.monthdayscalendar(state["year"], state["month"])
            for r, week in enumerate(weeks):
                for c, day in enumerate(week):
                    if day == 0:
                        ctk.CTkLabel(days_frame, text=" ", width=28).grid(row=r, column=c, padx=2, pady=2)
                        continue
                    btn = ctk.CTkButton(days_frame, text=str(day), width=28, command=lambda d=day: select_day(d))
                    btn.grid(row=r, column=c, padx=2, pady=2)

        def shift_month(delta):
            year = state["year"]
            month = state["month"] + delta
            if month < 1:
                month = 12
                year -= 1
            elif month > 12:
                month = 1
                year += 1
            state["year"] = year
            state["month"] = month
            max_day = _cal.monthrange(year, month)[1]
            if state["day"] > max_day:
                state["day"] = max_day
            build_calendar()
            update_info()

        ctk.CTkButton(header, text="◀", width=28, command=lambda: shift_month(-1)).pack(side="left")
        month_label.pack(side="left", expand=True, padx=6)
        ctk.CTkButton(header, text="▶", width=28, command=lambda: shift_month(1)).pack(side="right")

        def on_ok():
            try:
                _dt.date(state["year"], state["month"], state["day"])
                self.prod_date_entry.delete(0, "end")
                self.prod_date_entry.insert(0, f"{state['day']:02d}.{state['month']:02d}.{state['year']:04d}")
                top.destroy()
            except Exception:
                mb.showerror("Дата", "Выберите корректную дату.")

        btns = ctk.CTkFrame(top)
        btns.pack(pady=(2, 10))
        ctk.CTkButton(btns, text="OK", command=on_ok).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Отмена", command=top.destroy).pack(side="left", padx=6)

        build_calendar()
        update_info()
        top.grab_set()
    
    def _close_date_popover(self):
        popover = self.__dict__.get("_date_popover")
        self._date_popover = None
        if popover is None:
            return
        self._remove_popover_outside_click(popover)
        try:
            popover.destroy()
        except Exception:
            pass

    def _set_production_date_from_picker(self, value):
        self.prod_date_entry.delete(0, "end")
        self.prod_date_entry.insert(0, format_production_date(value))
        self._mark_prod_date_entry(False)
        self._close_date_popover()
        self._schedule_live_preview()

    def _open_date_picker(self):
        existing = getattr(self, "_date_popover", None)
        try:
            if existing is not None and existing.winfo_exists():
                self._close_date_popover()
                return
        except Exception:
            self._close_date_popover()

        import calendar as _calendar

        selected = production_date_from_text(self.prod_date_entry.get())
        today = dt.date.today()
        state = {"year": selected.year, "month": selected.month}
        month_names = (
            "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
        )

        popover = tk.Toplevel(self)
        self._date_popover = popover
        popover.withdraw()
        popover.overrideredirect(True)
        popover.transient(self)
        panel = ctk.CTkFrame(popover, corner_radius=8, border_width=1)
        panel.pack(fill="both", expand=True)
        self._match_plain_popover_background(popover, panel)
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(8, 4))
        month_label = ctk.CTkLabel(header, text="", font=("Arial", 12, "bold"))
        days_frame = ctk.CTkFrame(panel, fg_color="transparent")

        def select_day(day):
            self._set_production_date_from_picker(
                dt.date(state["year"], state["month"], day)
            )

        def render_month():
            month_label.configure(
                text=f"{month_names[state['month'] - 1]} {state['year']}"
            )
            for child in days_frame.winfo_children():
                child.destroy()
            for column, name in enumerate(("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")):
                ctk.CTkLabel(days_frame, text=name, width=30).grid(
                    row=0, column=column, padx=1, pady=1
                )
            weeks = _calendar.Calendar(firstweekday=0).monthdayscalendar(
                state["year"], state["month"]
            )
            for row_index, week in enumerate(weeks, start=1):
                for column, day in enumerate(week):
                    if not day:
                        ctk.CTkLabel(days_frame, text="", width=30).grid(
                            row=row_index, column=column, padx=1, pady=1
                        )
                        continue
                    value = dt.date(state["year"], state["month"], day)
                    style = {"fg_color": "transparent", "text_color": ("#20252a", "#e6e9ec")}
                    if value == today:
                        style = {"fg_color": ("#dce8f8", "#29466c")}
                    if value == selected:
                        style = {"fg_color": "#2f80ed", "text_color": "#ffffff"}
                    ctk.CTkButton(
                        days_frame,
                        text=str(day),
                        width=30,
                        height=26,
                        corner_radius=5,
                        command=lambda chosen=day: select_day(chosen),
                        **style,
                    ).grid(row=row_index, column=column, padx=1, pady=1)

        def shift_month(delta):
            month = state["month"] + delta
            if month < 1:
                state["year"] -= 1
                month = 12
            elif month > 12:
                state["year"] += 1
                month = 1
            state["month"] = month
            render_month()

        ctk.CTkButton(
            header, text="◀", width=28, height=26,
            command=lambda: shift_month(-1),
        ).pack(side="left")
        month_label.pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkButton(
            header, text="▶", width=28, height=26,
            command=lambda: shift_month(1),
        ).pack(side="right")
        days_frame.pack(padx=8, pady=2)
        quick_actions = ctk.CTkFrame(panel, fg_color="transparent")
        quick_actions.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkButton(
            quick_actions,
            text="Сегодня",
            height=28,
            command=lambda: self._set_production_date_from_picker(
                quick_production_date(0)
            ),
        ).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkButton(
            quick_actions,
            text="Вчера",
            height=28,
            command=lambda: self._set_production_date_from_picker(
                quick_production_date(-1)
            ),
        ).pack(side="left", fill="x", expand=True, padx=(3, 0))

        render_month()
        anchor = getattr(self, "prod_date_button", self.prod_date_entry)
        self._activate_popover(
            popover,
            anchor,
            self._close_date_popover,
        )

    # ---------- actions ----------
    
    def _preview(self, silent=False):
        if self.__dict__.get("_preview_mode", "label") == "sticker":
            return self._preview_marking_sticker(silent=silent)

        if silent:
            if getattr(self, "catalog_state", None) != "ready" or not self._bartender_available():
                return False
        elif not self._require_print_capabilities("Предпросмотр печати"):
            return False

        if not self.csv_path:
            if not silent:
                mb.showerror("CSV", "Выбери CSV")
            return False
        if not self.csv_rows:
            try:
                self.csv_rows = load_kontur_raw(self.csv_path)
            except Exception as e:
                self.logger.err(f"CSV ошибка: {e}")
                return False
        if not self.csv_rows:
            if not silent:
                mb.showerror("CSV", "Нет данных")
            return False

        idx1 = self._resolve_preview_index(silent=silent)
        if idx1 is None:
            return False
        base = self.csv_rows[idx1 - 1]
        enr = self._enrich(
            base,
            idx1,
            allow_empty_part=True,
            silent_validation=silent,
        )
        if not enr:
            return False

        src = "manual" if (self.format_combo.get() or "").strip() != "Auto" else "auto"
        self.logger.log(
            f"Выбран шаблон: {enr['_FORMAT']} "
            f"(источник: {src}, комбо: {self.format_combo.get()}, GTIN={base.get('GTIN','')})"
        )
        fmt_name = enr["_FORMAT"]
        try:
            self._apply_format_ui(fmt_name)
        except Exception:
            pass
        btw = self._get_btw_for_format(fmt_name, interactive=not silent)
        if not btw:
            return False

        fmt = None
        preview_dir = None
        try:
            preview_dir = tempfile.mkdtemp(prefix="autoprint-preview-")
            out_template = os.path.join(preview_dir, "preview_%PageNumber%.png")

            fmt = self.bt.open_format(btw)
            self.bt.set_common_print_flags(fmt)
            fmt.PrintSetup.IdenticalCopiesOfLabel = 1
            ok = self.bt.apply_fields(fmt, enr)
            if not ok:
                self.logger.err("Не удалось подставить значения (проверь имена полей в BTW).")

            if not self.bt.export_preview(fmt, out_template):
                self.logger.err("Не удалось сформировать превью.")
                return False

            generated = sorted(
                os.path.join(preview_dir, name)
                for name in os.listdir(preview_dir)
                if name.lower().endswith(".png")
            )
            if not generated:
                self.logger.err("BarTender сообщил успех, но файл превью не найден.")
                return False

            if not self._show_preview_path(generated[0], preserve_on_error=silent):
                return False

            old_dir = getattr(self, "_preview_temp_dir", None)
            self._preview_temp_dir = preview_dir
            preview_dir = None
            if old_dir and old_dir != self._preview_temp_dir:
                self._cleanup_preview_temp_dir(old_dir)

            self.logger.log(f"Превью обновлено (шаблон {fmt_name})")
            return True
        except Exception as e:
            self.logger.err(f"Превью: {e}\n{traceback.format_exc()}")
            return False
        finally:
            if fmt is not None:
                try:
                    fmt.Close(1)
                except Exception:
                    pass
            if preview_dir:
                self._cleanup_preview_temp_dir(preview_dir)

    def _print_all(self):
        """Устаревшая полная печать через BarTender. Оставлена как заглушка."""
        try:
            self.logger.log("Режим 'Печать всё' отключён, используйте диапазонную печать.")
        except Exception:
            pass
        return

    # ---------- save log ----------
    
    def _save_log(self):
        data = self.logbox.get("1.0", "end-1c")
        if not data.strip():
            mb.showinfo("Сохранение лога", "Журнал пуст.")
            return
        path = fd.asksaveasfilename(title="Сохранить журнал", defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        mb.showinfo("Сохранение лога", f"Сохранено:\n{path}")
        
    def _cancel_print(self):
        """Пользовательская отмена печати: ставит флаг, сбрасывает паузу и чистит очередь."""
        try:
            self.cancel_requested = True
            if getattr(self, "pause_btn", None):
                try:
                    self.is_paused = False
                    self.pause_btn.configure(text="Пауза")
                except Exception:
                    pass
            if getattr(self, "logger", None):
                self.logger.log("Отмена печати: флаг установлен. Пытаюсь очистить очередь...")
        except Exception:
            pass
        try:
            prn = self._get_printer()
        except Exception:
            prn = None
        if prn:
            try:
                self._purge_printer_queue(prn)
            except Exception as e:
                try:
                    self.logger.err(f"Очистка очереди не удалась: {e}")
                except Exception:
                    pass
        
        
    def _purge_printer_queue(self, prn_name):
        """Очистить очередь печати средствами Windows (PowerShell)."""
        import subprocess
        try:
            ps = f'Get-PrintJob -PrinterName "{prn_name}" -ErrorAction SilentlyContinue | Remove-PrintJob -Confirm:$false'
            cmd = ["powershell", "-NoProfile", "-Command", ps]
            subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if getattr(self, "logger", None):
                self.logger.log(f"Очередь принтера '{prn_name}' очищена (если были задания).")
        except Exception as e:
            try:
                subprocess.run(["powershell","-NoProfile","-Command","Stop-Service Spooler -Force"], check=False)
                subprocess.run(["powershell","-NoProfile","-Command","Start-Service Spooler"], check=False)
                if getattr(self, "logger", None):
                    self.logger.log("Служба печати перезапущена.")
            except Exception:
                raise e
        
        
        
    def _prepare_btw(self, fmt_or_path, enr, prn_name):
        
        
        """
        
        
        Подготовка BTW:
        
        
        - используем уже проверенный BarTender capability-state
        
        
        - если на входе строка пути -> открываем через BarTender Formats.Open(...)
        
        
        - назначаем принтер (оба свойства, для совместимости)
        
        
        - подставляем NamedSubStrings из enr
        
        
        Возвращает объект Format или None.
        
        
        """

        if not self._require_bartender("Подготовка шаблона"):
            return None


        try:
        
        
            bt = self.bt.app
        
        
        
            # 1) Открыть BTW если передан путь
        
        
            fmt = fmt_or_path
        
        
            if isinstance(fmt_or_path, str):
        
        
                path = fmt_or_path
            try:
                import os
                # нормализуем на Windows
                norm = os.path.normpath(path)
                path = norm.replace('/', '\\')
                try:
                    self.logger.log(f"Нормализованный путь: {path}")
                except Exception:
                    pass
                if not os.path.isfile(path):
                    try:
                        self.logger.err(f"BTW файл не найден на диске: {path}")
                    except Exception:
                        pass
            except Exception:
                pass
        
        
                try:
        
        
                    self.logger.log(f"Открытие шаблона: {path}")
        
        
                except Exception:
        
        
                    pass
        
        
                try:
        
        
                    fmt = bt.Formats.Open(path, False, "")
        
        
                except Exception as e:
        
        
                    try:
        
        
                        self.logger.err(f"Не удалось открыть BTW: {e}")
        
        
                    except Exception:
        
        
                        pass
        
        
                    return None
        
        
        
            # 2) Назначить принтер
        
        
            try:
        
        
                fmt.PrintSetup.PrinterName = prn_name
        
        
            except Exception:
        
        
                pass
        
        
            try:
        
        
                fmt.PrintSetup.Printer = prn_name
        
        
            except Exception:
        
        
                pass
        
        
        
            # 2.1) Безопасные флаги
        
        
            for a, v in (("UseDatabase", False), ("SelectRecordsAtPrint", False)):
        
        
                try:
        
        
                    setattr(fmt, a, v)
        
        
                except Exception:
        
        
                    pass
        
        
        
            # 3) Подставить NamedSubStrings
        
        
            try:
        
        
                names = [ss.Name for ss in fmt.NamedSubStrings]
        
        
                try:
        
        
                    self.logger.log(f"NamedSubStrings: {names}")
        
        
                except Exception:
        
        
                    pass
        
        
                set_ok = 0
        
        
                for name in names:
        
        
                    if name in enr:
        
        
                        try:
        
        
                            fmt.NamedSubStrings[name].Value = str(enr[name])
        
        
                            set_ok += 1
        
        
                        except Exception:
        
        
                            pass
        
        
                missed = [k for k in ['GTIN','NAME','ShortName','ShortGTIN','EXP_DATE','PROD_DATE','PART_NUM','DM','NUM'] if k not in names]
        
        
                try:
        
        
                    if missed:
        
        
                        self.logger.log(f"Подстановка: пропущены поля (нет в шаблоне): {missed}")
        
        
                    self.logger.log(f"Подстановка полей: всего={len(names)}, успешно={set_ok}")
        
        
                except Exception:
        
        
                    pass
        
        
            except Exception:
        
        
                pass
        
        
        
            return fmt
        
        
        except Exception as e:
        
        
            import traceback
        
        
            try:
        
        
                self.logger.err(f"_prepare_btw error: {e}\n{traceback.format_exc()}")
        
        
            except Exception:
        
        
                pass
        
        
            return None
        
        # === UNUSED HELPERS REMOVED ===
        
        
        

    def _print_range_one_job_via_csv(self, target_batches=None):
        import os, time, traceback

        if not self._require_print_capabilities("Печать"):
            return

        LOG_EVERY = 100
        PROGRESS_EVERY = 50
        self.cancel_requested = False

        if not getattr(self, "csv_path", None):
            mb.showerror("CSV", "Выбери CSV")
            return

        try:
            self._part_missing_logged = False
            cur_fmt = (self.format_combo.get() or "").strip()
            if cur_fmt and cur_fmt != "Auto" and self._template_requires_manual_part(cur_fmt) and not (self.part_entry.get() or "").strip():
                self._mark_part_entry(True)
                self.logger.err(f"Не заполнен номер партии для шаблона {cur_fmt}.")
                mb.showerror("Партия", f"Для шаблона {cur_fmt} заполни номер партии.")
                return
        except Exception:
            pass

        if not getattr(self, "csv_rows", None):
            try:
                self.csv_rows = load_kontur_raw(self.csv_path)
            except Exception as e:
                self.logger.err(f"CSV ошибка: {e}")
                return
        if not self.csv_rows:
            mb.showerror("CSV", "Нет данных")
            return

        selection = self._resolve_print_selection_for_action()
        if selection is None:
            return
        start_row, _end_row, limit = selection
        idx0 = start_row - 1
        rows_all = self.csv_rows[idx0:idx0 + limit]
        total = len(rows_all)
        if total == 0:
            self.logger.err("Нет строк для печати в указанном диапазоне.")
            return

        job_started_at = time.time()

        prn = self._get_printer()
        if not prn:
            return

        copies = 1
        batch_size = self._get_batch_size() or total
        batches_full = [(i, min(i + batch_size, total)) for i in range(0, total, batch_size)]
        batches = target_batches if target_batches else batches_full
        self.logger.log(f"Всего пакетов: {len(batches_full)}; размер пакета={batch_size}; лимит={total}")
        record_jobs = target_batches is None
        if record_jobs:
            self.last_sticker_jobs = []

        sent_total = 0
        calib_total = 0
        last_prn = prn
        last_fmt = None
        success = False
        tmp_csv_path = os.path.abspath(os.path.join(BASE_DIR, "tmp_batch.csv"))
        total_rolls = len(batches_full) if batches_full else len(batches)
        self._catalog_print_active += 1
        self._set_print_active_ui(True)
        try:
            self._stickers_preprinted = False
            if target_batches is None and not self._bool_var(getattr(self, "skip_sticker_var", False)):
                if self._preprint_stickers_for_all_batches(
                    batches_full,
                    rows_all,
                    idx0,
                    total,
                    total_rolls,
                    record_jobs=record_jobs,
                ):
                    self._stickers_preprinted = True

            if target_batches is None:
                self.last_batches = batches_full
                self.last_rows_all = rows_all
                self.last_idx0 = idx0
                self._render_batch_buttons(batches_full, idx0)
            else:
                try:
                    if self.last_batches:
                        total_rolls = len(self.last_batches)
                except Exception:
                    pass

            for bidx, (start, end) in enumerate(batches, start=1):
                self._pause_wait()
                if self._should_stop():
                    break
                if self.cancel_requested:
                    self.logger.log("Печать отменена пользователем.")
                    break

                rows = rows_all[start:end]
                self.logger.log(f"[Пакет {bidx}/{len(batches)}] Строки {idx0 + start + 1}-{idx0 + end}")
                self._set_progress(0, len(rows), f"Пакет {bidx}")

                enriched = []
                fast_used = False
                if rows and self._can_fast_enrich(rows):
                    idx1_first = idx0 + start + 1
                    base_enr = self._enrich(rows[0], idx1_first)
                    if base_enr:
                        fast_used = True
                        fmt_fast = base_enr.get("_FORMAT")
                        for i, base in enumerate(rows):
                            self._pause_wait()
                            if self._should_stop():
                                break
                            try:
                                self.update_idletasks(); self.update()
                            except Exception:
                                pass
                            idx1 = idx0 + start + i + 1
                            enr = dict(base_enr)
                            enr["DM"] = base.get("DM", "")
                            enr["NUM"] = str(idx1)
                            enr["_FORMAT"] = fmt_fast
                            enr["_SOURCE_ROW_NO"] = idx1
                            enriched.append(enr)
                            if (i + 1) % PROGRESS_EVERY == 0:
                                self._set_progress(i + 1, len(rows), f"Подготовка пакета {bidx}")
                        try:
                            self.logger.log(f"Ускорение: GTIN одинаковый, обогащение выполнено один раз (пакет {bidx}).")
                        except Exception:
                            pass

                if not fast_used:
                    for i, base in enumerate(rows):
                        self._pause_wait()
                        if self._should_stop():
                            break
                        try:
                            self.update_idletasks(); self.update()
                        except Exception:
                            pass
                        idx1 = idx0 + start + i + 1
                        enr = self._enrich(base, idx1)
                        if not enr:
                            if not getattr(self, "_part_missing_logged", False):
                                self.logger.err(f"Строка {idx1}: данные не сформированы - пропуск")
                            continue
                        try:
                            enr["_SOURCE_ROW_NO"] = idx1
                        except Exception:
                            pass
                        enriched.append(enr)
                        if (i + 1) % PROGRESS_EVERY == 0:
                            self._set_progress(i + 1, len(rows), f"Подготовка пакета {bidx}")

                if not enriched:
                    if getattr(self, "_part_missing_logged", False):
                        self.logger.err("Печать остановлена: заполните номер партии для выбранного шаблона.")
                        break
                    else:
                        self.logger.err(f"Пакет {bidx}: нет валидных строк для печати.")
                    continue

                calib_added = 0
                if self._bool_var(getattr(self, "calib_var", False)):
                    fmt0 = enriched[0].get("_FORMAT", "16x16")
                    cols = getattr(self, "REQ_COLS", [])
                    dummy = {k: ("1" if k.upper() == "NUM" else ("000" if k == "ShortGTIN" else "X")) for k in cols}
                    dummy.update({"_FORMAT": fmt0})
                    enriched = [{**dummy, "_CALIB": True} for _ in range(6)] + enriched
                    calib_added = 6
                    self.logger.log("КАЛИБРОВКА: 6 строк 'X' добавлены в начало пакета.")

                fmt_name = enriched[0].get("_FORMAT") or "16x16"
                real_in_batch = len(enriched) - calib_added

                self._pause_wait()
                if self._should_stop():
                    break
                self._write_tmp_batch_csv(enriched, tmp_csv_path, add_calib=False)

                fmt = None
                batch_start_num = idx0 + start + 1
                batch_end_num = idx0 + end
                try:
                    try:
                        self._apply_format_ui(fmt_name)
                        prn = self._get_printer()
                        last_prn = prn
                        last_fmt = fmt_name
                    except Exception:
                        pass

                    btw = self._get_btw_for_format(fmt_name)
                    if not btw:
                        self.logger.err(f"Пакет {bidx}: не найден BTW для шаблона {fmt_name}.")
                        continue

                    fmt = self.bt.open_format(btw)
                    try:
                        batch_report = self._template_batch_report(
                            fmt,
                            part_mode=self._template_part_mode(fmt_name),
                        )
                    except Exception:
                        batch_report = {
                            "can_batch": False,
                            "batch_message": "Не удалось проанализировать шаблон для пакетной печати.",
                        }

                    can_use_tmp_batch = False
                    if batch_report.get("can_batch"):
                        try:
                            self._rebind_template_db(fmt, tmp_csv_path)
                        except Exception:
                            pass
                        try:
                            can_use_tmp_batch = bool(self._check_template_db(fmt, tmp_csv_path))
                        except Exception:
                            can_use_tmp_batch = False
                    else:
                        try:
                            self.logger.log(
                                f"Шаблон {fmt_name} не готов к пакетной печати через tmp_batch: "
                                f"{batch_report.get('batch_message')}. "
                                f"Переключаю печать на прямую подстановку полей."
                            )
                        except Exception:
                            pass

                    self._pause_wait()
                    if self._should_stop():
                        raise Exception("Печать остановлена пользователем.")

                    extra_info = ""
                    if calib_added:
                        extra_info += f" (+{calib_added} калибровочных)"

                    if can_use_tmp_batch:
                        fmt.UseDatabase = True
                        fmt.SelectRecordsAtPrint = False
                        fmt.RecordRange = f"1-{len(enriched)}"
                        self.logger.log(f"Отправка на печать: {real_in_batch} строк через tmp_batch." + extra_info)
                        batch_job_name = f"AutoPrint {fmt_name} {batch_start_num}-{batch_end_num}"
                        batch_outcome = self._bt_batch_print(
                            fmt,
                            prn,
                            batch_job_name,
                            copies,
                            self._dialog_flag(),
                        )
                        if batch_outcome == BT_BATCH_AMBIGUOUS:
                            self.logger.log(
                                f"Пакет {bidx}: задание могло быть отправлено в BarTender, "
                                "но подтверждение результата не получено. "
                                "Повторная отправка и автоматическое продолжение отключены."
                            )
                            break
                        if batch_outcome != BT_BATCH_SUBMITTED:
                            raise Exception("BarTender сообщил об ошибке пакетной печати.")
                    else:
                        self.logger.log(
                            f"Отправка на печать: {real_in_batch} строк прямой подстановкой полей." + extra_info
                        )
                        self._print_rows_direct(
                            fmt,
                            enriched,
                            prn,
                            batch_label=f"Пакет {bidx}: ",
                        )

                    sent_total += real_in_batch
                    calib_total += calib_added
                    success = True
                    self.logger.log(f"Пакет {bidx} отправлен. Всего отправлено: {sent_total}/{total}" + (f"; калибровочных: {calib_total}" if calib_added else ""))
                    self._set_progress(len(rows), len(rows), f"Пакет {bidx}")
                except Exception as e:
                    self.logger.err(f"Сбой печати пакета {bidx}: {e}\\n{traceback.format_exc()}")
                finally:
                    if fmt:
                        try:
                            fmt.Close(1)
                        except Exception:
                            pass

                try:
                    start_num = batch_start_num
                    end_num = batch_end_num
                    sample_real = None
                    for e in enriched:
                        if e.get("_CALIB"):
                            continue
                        sample_real = e
                        break
                    if not sample_real and enriched:
                        sample_real = enriched[0]
                    if record_jobs:
                        self._record_sticker_job(
                            bidx,
                            start_num,
                            end_num,
                            total,
                            sample_real,
                            real_in_batch,
                            total_rolls,
                        )
                    if (not self._stickers_preprinted) and (not self._bool_var(getattr(self, "skip_sticker_var", False))):
                        self._print_sticker_placeholder(
                            bidx, start_num, end_num, total,
                            sample_row=sample_real,
                            km_count=real_in_batch,
                            total_rolls=total_rolls
                        )
                except Exception as e:
                    try:
                        self.logger.err(f"Стикер не напечатан: {e}")
                    except Exception:
                        pass

                if target_batches is None and bidx < len(batches):
                    cont = False
                    try:
                        cont = mb.askyesno("Продолжить печать?",
                                           f"Пакет {bidx} завершён. Печатать следующий пакет ({bidx+1}/{len(batches)})?")
                    except Exception:
                        cont = False
                    if not cont:
                        self.logger.log("Печать остановлена по запросу пользователя.")
                        break

            if getattr(self, "_part_missing_logged", False) and target_batches is None:
                try:
                    self.last_batches = []
                    self._render_batch_buttons([], idx0)
                except Exception:
                    pass

            if sent_total > 0 and getattr(self, "csv_path", None):
                try:
                    self._mark_csv_printed(self.csv_path, sent_total, printer=last_prn, fmt=last_fmt, calib_total=calib_total)
                except Exception:
                    pass

            if calib_total:
                self.logger.log(f"Завершено. Всего отправлено на печать: {sent_total} из {total} строк и {calib_total} калибровочных.")
            else:
                self.logger.log(f"Завершено. Всего отправлено на печать: {sent_total} из {total} строк.")
        finally:
            duration = max(0.0, time.time() - job_started_at)
            self._catalog_print_active = max(0, self._catalog_print_active - 1)
            self._set_print_active_ui(self._catalog_print_active > 0)
def main():
    if ensure_license_or_exit() is None:
        return 1
    app = App()
    app.mainloop()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
