# -*- coding: utf-8 -*-
"""
BarTender GUI V3.1 
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
import uuid
import tempfile
import getpass
import threading

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog as fd, messagebox as mb, Menu, simpledialog as sd, ttk
from PIL import Image

from utils import (
    get_base_dir, load_config, save_config, enum_printers,
    parse_date_ru, only_digits
)
from data_processing import (
    load_kontur_raw, read_product_map, enrich_row
)
from bartender_com import BT
from crpt_client import (
    crpt_check_statuses_sync, parse_codes, normalize_code,
    crpt_check_connection_sync
)
from services.crpt_settings import load_crpt_settings, set_runtime_overrides
from services.crpt_token_manager import get_token_manager, CrptTokenError
from services import crpt_auth

BASE_DIR = get_base_dir()
CONFIG_DIR = os.path.join(BASE_DIR, "config")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
PRODUCT_MAP_FILENAME = "product_catalog.xlsx"
PRODUCT_MAP_OLDNAME = "Список товаров.xlsx"
PRODUCT_MAP_DEFAULT = os.path.join(BASE_DIR, PRODUCT_MAP_FILENAME)
PRODUCT_MAP_OLD = os.path.join(BASE_DIR, PRODUCT_MAP_OLDNAME)

APP_TITLE    = "BarTender GUI V3.0"
APP_VERSION = "3.0"
PREVIEW_NAME = "preview.png"
STICKER_BTW_NAME = "sticker60x40.btw"
STATS_PATH = os.path.join(CONFIG_DIR, "stats.json")
CNT_RIBBON_LIMIT_16 = 16000
CATALOG_HEADERS = [
    "GTIN",
    "Наименование",
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
    "ShortName",
    "Шаблон партии",
    "Клиент",
    "Упаковка отображаемая",
]
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


def default_template_path(name: str) -> str:
    """Возвращает путь к шаблону в templates/ или старом корне, если там остался файл."""
    candidates = [
        os.path.join(TEMPLATES_DIR, name),
        os.path.join(BASE_DIR, name),  # обратная совместимость со старым расположением
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return os.path.join(TEMPLATES_DIR, name)


STICKER_BTW_DEFAULT = default_template_path(STICKER_BTW_NAME)



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

class StatsManager:
    """Отвечает за сбор/хранение статистики печати и риббонов."""
    def __init__(self, path, extra_paths=None, logger=None):
        self.path = path
        self.extra_paths = extra_paths or []
        self.logger = logger
        self.data = self._default_data()
        self.current_job_start = None
        self.current_job_name = ""
        self.load()
        self._ensure_ribbon_session()

    def _default_data(self):
        now_iso = dt.datetime.now().isoformat(timespec="seconds")
        sid = str(uuid.uuid4())
        return {
            "labels_total": 0,
            "labels_by_format": {"16x16": 0, "30x20": 0, "other": 0},
            "print_time_seconds": 0,
            "print_time_by_format": {"16x16": 0.0, "30x20": 0.0, "other": 0.0},
            "ribbon": {
                "current_session_id": sid,
                "current_session_started_at": now_iso,
                "labels_on_current_ribbon": 0,
                "last_checkpoint_asked": 0,
            },
            "ribbon_history": [],
            "ribbons": {  # новое: раздельные риббоны по принтерам
                "by_printer": {}
            },
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
                # глубокий merge ribbon
                r_def = self._default_data()["ribbon"]
                r_loaded = loaded.get("ribbon", {})
                r = {k: r_loaded.get(k, r_def.get(k)) for k in r_def.keys()}
                self.data["ribbon"] = r
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
                self.data["ribbon_history"] = loaded.get("ribbon_history", []) or []
                # merge ribbons (по принтерам)
                ribbons_loaded = loaded.get("ribbons", {}) or {}
                self.data["ribbons"] = {
                    "by_printer": ribbons_loaded.get("by_printer", {}) or {}
                }
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

    def _ensure_ribbon_session(self):
        r = self.data.get("ribbon") or {}
        if not r.get("current_session_id") or not r.get("current_session_started_at"):
            r["current_session_id"] = str(uuid.uuid4())
            r["current_session_started_at"] = dt.datetime.now().isoformat(timespec="seconds")
            r["labels_on_current_ribbon"] = int(r.get("labels_on_current_ribbon") or 0)
            r["last_checkpoint_asked"] = int(r.get("last_checkpoint_asked") or 0)
            self.data["ribbon"] = r
            self._save_atomic()
        else:
            self.data["ribbon"] = {
                "current_session_id": r.get("current_session_id"),
                "current_session_started_at": r.get("current_session_started_at"),
                "labels_on_current_ribbon": int(r.get("labels_on_current_ribbon") or 0),
                "last_checkpoint_asked": int(r.get("last_checkpoint_asked") or 0),
            }
        # гарантируем наличие файла на диске
        try:
            self._save_atomic()
        except Exception:
            pass

    def reset_all(self):
        """Полное обнуление статистики."""
        self.data = self._default_data()
        self._ensure_ribbon_session()
        self._save_atomic()

    def save(self):
        """Публичный синоним для сохранения на диск."""
        self._save_atomic()

    def _norm_printer(self, printer):
        try:
            p = (printer or "default").strip()
            if not p:
                p = "default"
            return p
        except Exception:
            return "default"

    def _ensure_printer_ribbon(self, printer):
        """Гарантирует наличие сессии риббона для указанного принтера."""
        key = self._norm_printer(printer)
        ribbons = self.data.setdefault("ribbons", {}).setdefault("by_printer", {})
        entry = ribbons.get(key, {})
        now_iso = dt.datetime.now().isoformat(timespec="seconds")
        cur = entry.get("current") or {}
        if not cur.get("current_session_id") or not cur.get("current_session_started_at"):
            cur = {
                "current_session_id": str(uuid.uuid4()),
                "current_session_started_at": now_iso,
                "labels_on_current_ribbon": int(cur.get("labels_on_current_ribbon") or 0),
                "last_checkpoint_asked": int(cur.get("last_checkpoint_asked") or 0),
            }
        else:
            cur = {
                "current_session_id": cur.get("current_session_id"),
                "current_session_started_at": cur.get("current_session_started_at"),
                "labels_on_current_ribbon": int(cur.get("labels_on_current_ribbon") or 0),
                "last_checkpoint_asked": int(cur.get("last_checkpoint_asked") or 0),
            }
        entry["current"] = cur
        entry.setdefault("history", [])
        entry.setdefault("name", printer or key)
        ribbons[key] = entry
        return key, entry

    def start_print_job(self, job_name=""):
        self.current_job_start = time.time()
        self.current_job_name = job_name or ""
        return self.current_job_start

    def finish_print_job(self, printed_count=0, duration_sec=None, success=True, fmt=None, printer=None):
        try:
            self._ensure_ribbon_session()
            if duration_sec is None:
                duration_sec = 0.0 if not self.current_job_start else max(0.0, time.time() - self.current_job_start)
        finally:
            self.current_job_start = None
        threshold_to_ask = None
        fmt_key = (fmt or "").strip() or "other"
        prn_key = self._norm_printer(printer)
        prn_name = printer or prn_key
        _k, rb_entry = self._ensure_printer_ribbon(prn_name)
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
            # общий счётчик риббона (для совместимости)
            r = self.data.get("ribbon", {})
            r["labels_on_current_ribbon"] = int(r.get("labels_on_current_ribbon", 0)) + int(printed_count)
            self.data["ribbon"] = r
            try:
                threshold_calc = (r["labels_on_current_ribbon"] // 10000) * 10000
                last_chk = int(r.get("last_checkpoint_asked") or 0)
                if threshold_calc >= 10000 and threshold_calc > last_chk:
                    threshold_to_ask = (threshold_calc, prn_name)
            except Exception:
                threshold_to_ask = None
            # риббон по принтеру
            try:
                cur = rb_entry.get("current", {})
                cur["labels_on_current_ribbon"] = int(cur.get("labels_on_current_ribbon", 0)) + int(printed_count)
                threshold_calc_p = (cur["labels_on_current_ribbon"] // 10000) * 10000
                last_chk_p = int(cur.get("last_checkpoint_asked") or 0)
                if threshold_calc_p >= 10000 and threshold_calc_p > last_chk_p:
                    threshold_to_ask = (threshold_calc_p, prn_name)
                rb_entry["current"] = cur
                self.data.setdefault("ribbons", {}).setdefault("by_printer", {})[prn_key] = rb_entry
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
        return threshold_to_ask

    def ribbon_reset(self, printer=None):
        self._ensure_ribbon_session()
        now_iso = dt.datetime.now().isoformat(timespec="seconds")
        r = self.data.get("ribbon", {})
        history = list(self.data.get("ribbon_history") or [])
        history.append({
            "session_id": r.get("current_session_id"),
            "started_at": r.get("current_session_started_at"),
            "ended_at": now_iso,
            "labels": int(r.get("labels_on_current_ribbon") or 0),
        })
        history = history[-50:]  # ограничим историю
        self.data["ribbon_history"] = history
        # открываем новую (общую)
        self.data["ribbon"] = {
            "current_session_id": str(uuid.uuid4()),
            "current_session_started_at": now_iso,
            "labels_on_current_ribbon": 0,
            "last_checkpoint_asked": 0,
        }
        # по принтеру
        key, entry = self._ensure_printer_ribbon(printer)
        hist_p = list(entry.get("history") or [])
        cur = entry.get("current", {})
        hist_p.append({
            "session_id": cur.get("current_session_id"),
            "started_at": cur.get("current_session_started_at"),
            "ended_at": now_iso,
            "labels": int(cur.get("labels_on_current_ribbon") or 0),
            "printer": printer or key,
        })
        entry["history"] = hist_p[-50:]
        entry["current"] = {
            "current_session_id": str(uuid.uuid4()),
            "current_session_started_at": now_iso,
            "labels_on_current_ribbon": 0,
            "last_checkpoint_asked": 0,
        }
        self.data.setdefault("ribbons", {}).setdefault("by_printer", {})[key] = entry
        self._save_atomic()

    def mark_checkpoint(self, threshold, printer=None):
        try:
            self._ensure_ribbon_session()
            r = self.data.get("ribbon", {})
            r["last_checkpoint_asked"] = max(int(r.get("last_checkpoint_asked") or 0), int(threshold or 0))
            self.data["ribbon"] = r
            key, entry = self._ensure_printer_ribbon(printer)
            cur = entry.get("current", {})
            cur["last_checkpoint_asked"] = max(int(cur.get("last_checkpoint_asked") or 0), int(threshold or 0))
            entry["current"] = cur
            self.data.setdefault("ribbons", {}).setdefault("by_printer", {})[key] = entry
            self._save_atomic()
        except Exception:
            pass

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

    # ------------------------ Риббон: упрощённый счётчик ------------------------
    def _load_ribbon_state(self):
        """Загружает счётчик риббона из конфига."""
        try:
            state = dict(self.cfg.get("ribbon_counter") or {})
        except Exception:
            state = {}
        try:
            state["count_16x16"] = int(state.get("count_16x16", 0) or 0)
        except Exception:
            state["count_16x16"] = 0
        state.setdefault("last_reset", None)
        state.setdefault("last_update", None)
        return state

    def _save_ribbon_state(self):
        """Сохраняет счётчик риббона в конфиг."""
        try:
            if not isinstance(self.cfg, dict):
                self.cfg = {}
            self.cfg["ribbon_counter"] = self.ribbon_state
            save_config(self.cfg)
        except Exception:
            pass

    def _reset_ribbon_counter(self):
        """Обнуляет счётчик риббона (16x16)."""
        ts = dt.datetime.now().isoformat(timespec="seconds")
        self.ribbon_state["count_16x16"] = 0
        self.ribbon_state["last_reset"] = ts
        self.ribbon_state["last_update"] = ts
        self._save_ribbon_state()
        self._update_ribbon_label()

    def _increment_ribbon_counter(self, labels, fmt_name):
        """Учитывает напечатанные этикетки 16x16 в счётчике риббона."""
        try:
            if (fmt_name or "").strip() != "16x16":
                return
            delta = max(0, int(labels or 0))
            self.ribbon_state["count_16x16"] = int(self.ribbon_state.get("count_16x16", 0) or 0) + delta
            self.ribbon_state["last_update"] = dt.datetime.now().isoformat(timespec="seconds")
            self._save_ribbon_state()
            self._update_ribbon_label()
        except Exception:
            pass

    def _ensure_ribbon_capacity(self, labels_to_print, fmt_name, printer=None):
        """Проверяет, не приближаемся ли к лимиту риббона, и просит подтверждение."""
        if (fmt_name or "").strip() != "16x16":
            return True
        try:
            current = int(self.ribbon_state.get("count_16x16", 0) or 0)
        except Exception:
            current = 0
        add = max(0, int(labels_to_print or 0))
        total_after = current + add
        warn_at = max(0, self.ribbon_limit_16 - 1000)
        if total_after < warn_at:
            return True
        try:
            msg = (
                f"На риббоне учтено {current} этикеток 16x16.\n"
                f"Текущий пакет добавит {add}, итог будет {total_after}.\n"
                f"Лимит ~{self.ribbon_limit_16}. Риббон заменён?"
            )
            resp = mb.askyesnocancel("Риббон", msg, parent=self)
        except Exception:
            resp = None
        if resp is None:
            try:
                self.logger.log("Печать остановлена: нет подтверждения по риббону.")
            except Exception:
                pass
            return False
        if resp:
            # Пользователь подтвердил замену: обнуляем счётчик
            self._reset_ribbon_counter()
            try:
                self.logger.log("Счётчик риббона обнулён (подтверждена замена).")
            except Exception:
                pass
            return True
        try:
            self.logger.log("Риббон не сброшен, продолжаем печать с текущим счётчиком.")
        except Exception:
            pass
        return True

    def _update_ribbon_label(self):
        """Обновляет отображение счётчика риббона рядом с переключателем темы."""
        try:
            count = int(self.ribbon_state.get("count_16x16", 0) or 0)
        except Exception:
            count = 0
        try:
            txt = f"Риббон 16x16: {count}/{self.ribbon_limit_16}"
            self.ribbon_label.configure(text=txt)
        except Exception:
            pass

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
        self.cancel_requested = False
        self.is_paused = False
        super().__init__()
        self.REQ_COLS = ["ShortName","ShortGTIN","EXP_DATE","PROD_DATE","PART_NUM","DM","NUM"]
        self.title(APP_TITLE)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.scale_var = tk.StringVar(master=self, value="auto")
        self.show_preview_var = tk.BooleanVar(master=self, value=True)
        self.show_log_var = tk.BooleanVar(master=self, value=True)
        self._apply_auto_scaling()

        self.cfg = load_config()
        # статистика печати отключена; используем только счётчик риббона
        self.stats = None
        self.ribbon_limit_16 = CNT_RIBBON_LIMIT_16
        self.ribbon_state = self._load_ribbon_state()
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
        self.is_paused = False
        self.csv_path = ""
        self.csv_rows = []
        self.preview_ctkimg = None
        self.product_map = {}

        self._build_ui()
        self._build_menu()
        self._start_bt()
        self._refresh_printers()
        self._load_presets()
        self._auto_load_product_map()
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

    def _build_ui(self):
        # верхняя панель: выбор формата/шаблонов/CSV
        top = ctk.CTkFrame(self, corner_radius=12)
        top.pack(fill="x", padx=12, pady=(12, 6))

        ctk.CTkLabel(top, text="Формат (Auto/ручной):").pack(side="left", padx=(8, 4), pady=10)
        self.format_combo = ctk.CTkComboBox(top, values=["Auto", "16x16", "30x20"], state="readonly", width=120)
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

        ribbon_box = ctk.CTkFrame(top, fg_color="transparent")
        ribbon_box.pack(side="right", padx=(10, 6), pady=10)
        self.ribbon_label = ctk.CTkLabel(ribbon_box, text="Риббон 16x16: 0/16000")
        self.ribbon_label.pack(side="left", padx=(0, 6))
        ctk.CTkButton(ribbon_box, text="Сброс", width=70, command=self._on_ribbon_reset).pack(side="left")

        self.theme_switch = ctk.CTkSwitch(top, text="Ночная тема", command=self._toggle_theme)
        self.theme_switch.pack(side="right", padx=(20, 10), pady=10)
        self.theme_switch.select()
        self._update_ribbon_label()

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

        # справочник
        prod = ctk.CTkFrame(self, corner_radius=12)
#         prod.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(prod, text=f"{PRODUCT_MAP_FILENAME} (автозагрузка из C:\\auto_print):").pack(side="left", padx=(8, 6), pady=10)
        self.prodmap_entry = ctk.CTkEntry(prod, placeholder_text=PRODUCT_MAP_DEFAULT)
        self.prodmap_entry.pack(side="left", fill="x", expand=True, padx=(4, 4), pady=10)
        self.prodmap_entry.configure(state="disabled")

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
        ctk.CTkButton(
            sticker_row,
            text="Перепечатать стикер",
            width=180,
            command=self._open_sticker_reprint_dialog
        ).pack(side="left")

        # кнопки действий
        btns = ctk.CTkFrame(self)
        btns.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkButton(btns, text="Предосмотр печати", command=self._preview, height=36).pack(side="left", padx=6, pady=8)
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

    def _build_menu(self):
        """Рисуем кастомную (тёмную) псевдо-меню полосу вместо штатного светлого Menu."""
        try:
            # popup-меню
            view_menu = Menu(self, tearoff=0)
            view_menu.add_checkbutton(
                label="Показывать предпросмотр",
                variable=self.show_preview_var,
                command=self._toggle_preview_block,
                onvalue=True,
                offvalue=False
            )
            view_menu.add_checkbutton(
                label="Показывать лог",
                variable=self.show_log_var,
                command=self._toggle_log_block,
                onvalue=True,
                offvalue=False
            )

            scale_menu = Menu(self, tearoff=0)
            scale_menu.add_radiobutton(
                label="Авто (по экрану)",
                value="auto",
                variable=self.scale_var,
                command=self._on_scale_change
            )
            for lbl, val in (("80 %", "0.8"), ("90 %", "0.9"), ("100 %", "1.0"), ("110 %", "1.1")):
                scale_menu.add_radiobutton(
                    label=lbl,
                    value=val,
                    variable=self.scale_var,
                    command=self._on_scale_change
                )
            view_menu.add_separator()
            view_menu.add_cascade(label="Масштаб", menu=scale_menu)

            service_menu = Menu(self, tearoff=0)
            service_menu.add_command(label="Редактор каталога товаров", command=self._open_product_catalog)
            service_menu.add_command(label="Открыть папку шаблонов", command=self._open_templates_dir)
            service_menu.add_command(label="Принтеры по шаблонам", command=self._open_template_printer_settings)
            service_menu.add_command(label="Смена риббона (сброс счётчика)", command=self._on_ribbon_reset)

            codes_menu = Menu(self, tearoff=0)
            codes_menu.add_command(label="Отчет о вводе в оборот", command=self._open_turnover_report_dialog)
            codes_menu.add_command(label="Проверка кодов", command=self._open_crpt_checker_window)

            # Кастомная панель вместо menu-bar
            # создаём бар до остального контента (если уже есть виджеты - вставляем перед первым)
            initial_children = list(self.winfo_children())
            bar = ctk.CTkFrame(self, height=30, corner_radius=0, fg_color="#1f1f1f")
            if initial_children:
                bar.pack(fill="x", side="top", before=initial_children[0])
            else:
                bar.pack(fill="x", side="top")
            btn_style = dict(width=90, height=28, corner_radius=0, fg_color="transparent", hover_color="#2a2a2a")

            def make_btn(text, menu_obj, width=None):
                style = dict(btn_style)
                if width:
                    style["width"] = width
                b = ctk.CTkButton(bar, text=text, **style,
                                   command=lambda m=menu_obj, w=None: self._popup_menu(m, b))
                b.pack(side="left", padx=(0, 2), pady=0)
                return b

            btn_view = make_btn("Вид", view_menu)
            btn_service = make_btn("Сервис", service_menu)
            btn_codes = make_btn("Работа с кодами", codes_menu, width=150)

            self.menu_bar_frame = bar
            self._menus_popup = [view_menu, codes_menu, service_menu]
            self._menu_buttons = [btn_view, btn_service, btn_codes]
            self._update_menu_colors()
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

    def _toggle_theme(self):
        if self.theme_switch.get() == 1:
            ctk.set_appearance_mode("dark")
        else:
            ctk.set_appearance_mode("light")
        
        # Перенастраиваем цвета лога при смене темы
        try:
            if hasattr(self, 'logger'):
                self.logger.configure_tags()
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
        ribbon = stats.get("ribbon", {}) or {}
        labels_by_fmt = stats.get("labels_by_format") or {}
        ctk.CTkLabel(top, text=f"Всего напечатано марок: {stats.get('labels_total', 0)}").pack(anchor="w", pady=2)
        ctk.CTkLabel(top, text=f"16x16: {labels_by_fmt.get('16x16', 0)} | 30x20: {labels_by_fmt.get('30x20', 0)}").pack(anchor="w", pady=2)

        ribbons_frame = ctk.CTkFrame(win)
        ribbons_frame.pack(fill="both", expand=False, padx=12, pady=(4, 4))
        ctk.CTkLabel(ribbons_frame, text="Риббоны по принтерам:").pack(anchor="w")
        rf = ctk.CTkScrollableFrame(ribbons_frame, height=160)
        rf.pack(fill="both", expand=True, pady=(4, 4))
        ribbons_by_prn = ((stats.get("ribbons") or {}).get("by_printer") or {})
        if not ribbons_by_prn:
            ctk.CTkLabel(rf, text="Нет данных.").pack(anchor="w", padx=6, pady=4)
        else:
            for prn_key, entry in ribbons_by_prn.items():
                cur = entry.get("current", {}) or {}
                hist = list(entry.get("history") or [])
                frame_p = ctk.CTkFrame(rf)
                frame_p.pack(fill="x", padx=6, pady=4)
                title = entry.get("name") or prn_key
                ctk.CTkLabel(frame_p, text=f"{title}: {int(cur.get('labels_on_current_ribbon',0))} шт. (старт {cur.get('current_session_started_at','-')})",
                             anchor="w").pack(anchor="w")
                if not hist:
                    ctk.CTkLabel(frame_p, text="  Нет завершённых сессий", anchor="w").pack(anchor="w", padx=4)
                else:
                    for h in list(reversed(hist))[:5]:
                        txt = f"  {h.get('started_at','-')} → {h.get('ended_at','-')}: {h.get('labels',0)} шт."
                        ctk.CTkLabel(frame_p, text=txt, anchor="w").pack(anchor="w")

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
                    f"16x16: {labels_by_fmt.get('16x16', 0)} | 30x20: {labels_by_fmt.get('30x20', 0)}",
                ]
                chunks.append("Риббоны:")
                for prn_key, entry in ribbons_by_prn.items():
                    cur = entry.get("current", {}) or {}
                    title = entry.get("name") or prn_key
                    chunks.append(f" - {title}: {int(cur.get('labels_on_current_ribbon',0))} (с {cur.get('current_session_started_at','-')})")
                    for h in list(reversed(entry.get('history') or []))[:5]:
                        chunks.append(f"    {h.get('started_at','-')} → {h.get('ended_at','-')}: {h.get('labels',0)}")
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

    def _on_ribbon_reset(self):
        """Ручной сброс счётчика риббона."""
        try:
            ok = mb.askyesno("Смена риббона", "Обнулить счётчик риббона (16x16)?", icon="warning")
        except Exception:
            ok = False
        if not ok:
            return
        self._reset_ribbon_counter()
        try:
            self.logger.log("Счётчик риббона обнулён вручную.")
        except Exception:
            pass

    def _open_templates_dir(self):
        """Открывает папку templates (создаёт, если нет)."""
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

    def _open_template_printer_settings(self):
        """Окно настроек принтеров по шаблонам."""
        win = ctk.CTkToplevel(self)
        win.title("Принтеры по шаблонам")
        win.geometry("440x260")
        win.grab_set()
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.after(150, lambda: win.attributes("-topmost", False))
            win.focus_force()
        except Exception:
            pass

        fmt_map = self.cfg.get("format_printers") or {}
        values = self._get_printer_values()
        extras = []
        for v in (fmt_map.get("16x16"), fmt_map.get("30x20"), self.cfg.get("sticker_printer")):
            if v and v not in values and v not in extras:
                extras.append(v)
        values = extras + values
        options = ["(по умолчанию)"] + values if values else ["(по умолчанию)"]

        def _cur_or_default(val):
            return val if val else "(по умолчанию)"

        body = ctk.CTkFrame(win)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.grid_columnconfigure(1, weight=1)

        var_16 = tk.StringVar(value=_cur_or_default(fmt_map.get("16x16")))
        var_30 = tk.StringVar(value=_cur_or_default(fmt_map.get("30x20")))
        var_st = tk.StringVar(value=_cur_or_default(self.cfg.get("sticker_printer")))

        ctk.CTkLabel(body, text="Шаблон 16x16:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        ctk.CTkOptionMenu(body, variable=var_16, values=options).grid(row=0, column=1, sticky="ew", pady=6)

        ctk.CTkLabel(body, text="Шаблон 30x20:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        ctk.CTkOptionMenu(body, variable=var_30, values=options).grid(row=1, column=1, sticky="ew", pady=6)

        ctk.CTkLabel(body, text="Стикер 60x40:").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=6)
        ctk.CTkOptionMenu(body, variable=var_st, values=options).grid(row=2, column=1, sticky="ew", pady=6)

        btns = ctk.CTkFrame(win)
        btns.pack(pady=(0, 12))

        def _apply_format_setting(fmt_key, var):
            val = (var.get() or "").strip()
            if val in ("(по умолчанию)", "(нет принтеров)", ""):
                if fmt_key in fmt_map:
                    fmt_map.pop(fmt_key, None)
            else:
                fmt_map[fmt_key] = val

        def _save():
            _apply_format_setting("16x16", var_16)
            _apply_format_setting("30x20", var_30)
            if fmt_map:
                self.cfg["format_printers"] = fmt_map
            else:
                self.cfg.pop("format_printers", None)

            st_val = (var_st.get() or "").strip()
            if st_val in ("(по умолчанию)", "(нет принтеров)", ""):
                self.cfg.pop("sticker_printer", None)
            else:
                self.cfg["sticker_printer"] = st_val

            save_config(self.cfg)
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

    def _prompt_ribbon_printer(self):
        """Небольшое окно выбора принтера для смены риббона."""
        vals = []
        try:
            vals = list(self.prn_combo.cget("values") or [])
        except Exception:
            vals = []
        cur = self._get_printer()
        if vals and cur and cur not in vals:
            vals.insert(0, cur)
        if not vals:
            vals = [cur or "По умолчанию"]

        win = ctk.CTkToplevel(self)
        win.title("Смена риббона")
        win.geometry("360x140")
        win.grab_set()
        var = tk.StringVar(value=cur or vals[0])
        ctk.CTkLabel(win, text="Выбери принтер для замены риббона:").pack(padx=12, pady=(12, 6), anchor="w")
        opt = ctk.CTkOptionMenu(win, variable=var, values=vals)
        opt.pack(padx=12, pady=6, fill="x")

        result = {"val": None}
        def _ok():
            result["val"] = var.get()
            win.destroy()
        def _cancel():
            win.destroy()
        btns = ctk.CTkFrame(win)
        btns.pack(pady=10)
        ctk.CTkButton(btns, text="OK", command=_ok, width=90).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Отмена", command=_cancel, width=90, fg_color="#444").pack(side="left", padx=6)
        win.wait_window()
        return result["val"]

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
        try:
            self.bt = BT(self.logger)
            self.bt.start()
        except Exception as e:
            self.logger.err(f"COM не стартовал: {e}")
            mb.showerror("BarTender COM", f"Не удалось запустить COM:\n{e}")

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
        try:
            fmt_map = self.cfg.get("format_printers") or {}
        except Exception:
            fmt_map = {}
        return (fmt_map.get(fmt) or "").strip()

    def _refresh_printers(self):
        items = enum_printers() or ["(нет принтеров)"]
        self.prn_combo.configure(values=items)
        cur_fmt = (self.format_combo.get() or "").strip() if hasattr(self, "format_combo") else ""
        target = None
        if cur_fmt in ("16x16", "30x20"):
            target = self._match_printer_value(self._get_format_printer_preference(cur_fmt))
        if not target:
            target = self._pick_printer_by_keywords(("TSC310", "TSC 310", "TSC TE310", "TE310"))
        self.prn_combo.set(target or items[0])

    def _set_default_printer_for_format(self, fmt: str):
        """Выбирает принтер по умолчанию для формата, если он есть в списке."""
        fmt = (fmt or "").strip()
        if fmt not in ("16x16", "30x20"):
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
            if fmt == "16x16":
                self.part_entry.configure(state="disabled")
                self.part_entry.delete(0, "end")
            else:
                self.part_entry.configure(state="normal")
        except Exception:
            pass
        self._set_default_printer_for_format(fmt)

    def _on_format_change(self, value):
        self._apply_format_ui(value)

    def _load_presets(self):
        p16 = (self.cfg.get("formats", {}) or {}).get("16x16", "")
        p30 = (self.cfg.get("formats", {}) or {}).get("30x20", "")
        if p16 and os.path.isfile(p16):
            self.btw16_entry.insert(0, p16)
        if p30 and os.path.isfile(p30):
            self.btw30_entry.insert(0, p30)

    def _catalog_has_data(self, path):
        """Проверяет, есть ли данные (не только заголовки) в каталоге."""
        if not os.path.isfile(path):
            return False
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True, read_only=True)
            ws = wb.active
            if ws.max_row <= 1:
                return False
            for row in ws.iter_rows(min_row=2, values_only=True):
                if any(cell not in (None, "") for cell in row):
                    return True
            return False
        except Exception:
            return False

    def _log_catalog_info(self, path, prefix="Каталог"):
        """Логирует базовую информацию о файле каталога (заголовки, строки, непустые поля)."""
        try:
            from openpyxl import load_workbook
        except Exception as e:
            try:
                if getattr(self, "logger", None):
                    self.logger.err(f"[{prefix}] openpyxl недоступен: {e}")
            except Exception:
                pass
            return
        if not os.path.isfile(path):
            try:
                if getattr(self, "logger", None):
                    self.logger.err(f"[{prefix}] файл не найден: {path}")
            except Exception:
                pass
            return
        try:
            wb = load_workbook(path, data_only=True, read_only=True)
            ws = wb.active
            headers = [str(c.value or "").replace("\n", " ").replace("\r", " ").strip() for c in (ws[1] if ws.max_row else [])]
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            total_rows = len(rows)
            nonempty = {}
            if headers:
                for i, h in enumerate(headers):
                    cnt = 0
                    for r in rows:
                        if i < len(r) and r[i] not in (None, ""):
                            cnt += 1
                    nonempty[h] = cnt
            msg = (f"[{prefix}] {path}: заголовков={len(headers)}, строк={total_rows}, "
                   f"непустые по колонкам={nonempty}")
            if getattr(self, "logger", None):
                self.logger.log(msg)
        except Exception as e:
            try:
                if getattr(self, "logger", None):
                    self.logger.err(f"[{prefix}] Не удалось прочитать {path}: {e}")
            except Exception:
                pass

    def _migrate_product_catalog(self):
        """Если новый каталог пуст/отсутствует, а старый есть — копируем старый как источник данных."""
        new_path = PRODUCT_MAP_DEFAULT
        old_path = PRODUCT_MAP_OLD
        if os.path.isfile(old_path):
            needs_copy = (not os.path.isfile(new_path)) or (not self._catalog_has_data(new_path))
            if needs_copy:
                try:
                    import shutil as _shutil
                    _shutil.copy2(old_path, new_path)
                    try:
                        if getattr(self, "logger", None):
                            self.logger.log(f"[INFO] Справочник скопирован: {PRODUCT_MAP_OLDNAME} -> {PRODUCT_MAP_FILENAME}")
                            self._log_catalog_info(old_path, prefix="Каталог (старый)")
                            self._log_catalog_info(new_path, prefix="Каталог (новый)")
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        self.logger.err(f"Не удалось скопировать старый справочник: {e}")
                    except Exception:
                        pass

    def _auto_load_product_map(self):

        path = PRODUCT_MAP_DEFAULT
        # миграция старого файла, если он есть
        self._migrate_product_catalog()
        # если нет файла - попросим указать и скопируем под новым именем
        if not os.path.isfile(path):
            try:
                self.logger.err(f"Справочник не найден: {path}. Укажи '{PRODUCT_MAP_FILENAME}' (будет скопирован в папку программы).")
            except Exception:
                pass
            p = fd.askopenfilename(title=f"Укажи {PRODUCT_MAP_FILENAME}", filetypes=[("Excel", "*.xlsx")])
            if p and os.path.isfile(p):
                try:
                    os.makedirs(BASE_DIR, exist_ok=True)
                except Exception:
                    pass
                try:
                    import shutil as _shutil
                    _shutil.copy2(p, path)
                except Exception:
                    path = p  # если копия не удалась - используем выбранный путь напрямую

        try:
            self.prodmap_entry.configure(state="normal")
            self.prodmap_entry.delete(0, "end")
            self.prodmap_entry.insert(0, path)
            self.prodmap_entry.configure(state="disabled")
        except Exception:
            pass

        self._reload_product_map_from_path(path)

    def _reload_product_map_from_path(self, path):
        if os.path.isfile(path):
            try:
                self._log_catalog_info(path, prefix="Каталог (загрузка)")
            except Exception:
                pass
            try:
                self.product_map = read_product_map(path) or {}
            except Exception as e:
                self.product_map = {}
                self.logger.err(f"Ошибка чтения справочника: {e}")
            self.cfg["product_map_path"] = path
            save_config(self.cfg)
            cnt = len([k for k in self.product_map.keys() if k != "_HAS_SHORT_COL"])
            has_short = bool(self.product_map.get("_HAS_SHORT_COL"))
            try:
                self.logger.log(f"Справочник загружен: {path} (записей={cnt}); ShortName-столбец: {has_short}")
            except Exception:
                pass
            try:
                self._update_brand_hint()
            except Exception:
                pass
        else:
            try:
                self.logger.err(f"Справочник не найден: {path} или не установлен openpyxl (pip install openpyxl)")
            except Exception:
                pass

    def _ensure_product_catalog_file(self, path=None):
        """Создаёт product_catalog.xlsx с нужными заголовками, если нет."""
        try:
            from openpyxl import Workbook
        except ImportError:
            mb.showerror("Каталог товаров", "Не установлена библиотека openpyxl (нужна для редактирования каталога).")
            return None
        path = path or PRODUCT_MAP_DEFAULT
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except Exception:
            pass
        if not os.path.isfile(path):
            try:
                wb = Workbook()
                ws = wb.active
                ws.append(CATALOG_HEADERS)
                wb.save(path)
                if getattr(self, "logger", None):
                    self.logger.log(f"[INFO] Создан новый каталог товаров: {path}")
            except Exception as e:
                mb.showerror("Каталог товаров", f"Не удалось создать файл каталога:\n{e}")
                return None
        return path

    def _load_catalog_rows(self, path):
        """Читает каталог товаров в виде списка dict по CATALOG_HEADERS."""
        try:
            from openpyxl import load_workbook
        except ImportError:
            mb.showerror("Каталог товаров", "Не установлена библиотека openpyxl.")
            return []
        try:
            wb = load_workbook(path, data_only=True)
            ws = wb.active
            if ws.max_row < 1:
                return []
            def _norm_header(h):
                if h is None:
                    return ""
                h = str(h).replace("\n", " ").replace("\r", " ")
                h = re.sub(r"\s+", " ", h).strip()
                hlow = h.lower()
                aliases = {
                    '"разрешительная документация"': "Разрешительная документация",
                    "разрешительная документация": "Разрешительная документация",
                    "разрешительная док": "Разрешительная документация",
                    "объем": "Вес",
                    "объем, мл": "Вес",
                    "объем мл": "Вес",
                    "объем (мл)": "Вес",
                }
                return aliases.get(hlow, h)

            headers_raw = [c.value for c in ws[1]]
            headers = [_norm_header(v) for v in headers_raw]
            idx = {h.lower(): i for i, h in enumerate(headers)}
            rows = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                item = {}
                for h in CATALOG_HEADERS:
                    col = idx.get(h.lower())
                    v = row[col] if (col is not None and col < len(row)) else ""
                    item[h] = "" if v is None else str(v)
                rows.append(item)
            return rows
        except Exception as e:
            mb.showerror("Каталог товаров", f"Не удалось прочитать файл:\n{e}")
            return []

    def _write_catalog_rows(self, path, rows):
        """Перезаписывает каталог товаров стандартными заголовками."""
        try:
            from openpyxl import Workbook
        except ImportError:
            mb.showerror("Каталог товаров", "Не установлена библиотека openpyxl.")
            return False
        try:
            wb = Workbook()
            ws = wb.active
            ws.append(CATALOG_HEADERS)
            for item in rows:
                ws.append([item.get(h, "") for h in CATALOG_HEADERS])
            wb.save(path)
            return True
        except Exception as e:
            mb.showerror("Каталог товаров", f"Не удалось сохранить файл:\n{e}")
            return False

    def _open_product_catalog(self):
        """Простой редактор каталога товаров (add/edit/delete)."""
        try:
            current_path = (self.prodmap_entry.get() or "").strip()
        except Exception:
            current_path = ""
        path = self._ensure_product_catalog_file(current_path or PRODUCT_MAP_DEFAULT)
        if not path:
            return
        rows = self._load_catalog_rows(path)

        win = ctk.CTkToplevel(self)
        win.title("Каталог товаров")
        win.geometry("920x560")
        win.grab_set()

        cols = [
            "GTIN",
            "Наименование",
            "ShortName",
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

        def edit_item(existing=None, idx=None):
            dlg = ctk.CTkToplevel(win)
            dlg.title("Добавить товар" if existing is None else "Редактировать товар")
            dlg.geometry("540x600")
            entries = {}
            for i, h in enumerate(CATALOG_HEADERS):
                ctk.CTkLabel(dlg, text=h).grid(row=i, column=0, sticky="w", padx=8, pady=4)
                ent = ctk.CTkEntry(dlg, width=380)
                ent.grid(row=i, column=1, sticky="ew", padx=8, pady=4)
                ent.insert(0, (existing or {}).get(h, ""))
                entries[h] = ent
            dlg.grid_columnconfigure(1, weight=1)

            def on_save():
                gtin = entries["GTIN"].get().strip()
                if not gtin:
                    mb.showerror("Каталог товаров", "GTIN обязателен.")
                    return
                if not gtin.isdigit():
                    mb.showerror("Каталог товаров", "GTIN должен содержать только цифры.")
                    return
                item = {h: entries[h].get().strip() for h in CATALOG_HEADERS}
                if existing is None:
                    rows.append(item)
                else:
                    rows[idx] = item
                if self._write_catalog_rows(path, rows):
                    rows[:] = self._load_catalog_rows(path)
                    refresh_tree()
                    self._reload_product_map_from_path(path)
                    apply_filters()
                    try:
                        if getattr(self, "logger", None):
                            self.logger.log(f"[INFO] Каталог обновлён: {path} (товаров={len(rows)})")
                    except Exception:
                        pass
                    dlg.destroy()
                else:
                    mb.showerror("Каталог товаров", "Не удалось сохранить каталог. Проверьте, не открыт ли файл в Excel.")

            btn_frame = ctk.CTkFrame(dlg)
            btn_frame.grid(row=len(CATALOG_HEADERS), column=0, columnspan=2, pady=10)
            ctk.CTkButton(btn_frame, text="Сохранить", command=on_save).pack(side="left", padx=6)
            ctk.CTkButton(btn_frame, text="Отмена", command=dlg.destroy).pack(side="left", padx=6)

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
            rows.pop(idx)
            if self._write_catalog_rows(path, rows):
                rows[:] = self._load_catalog_rows(path)
                refresh_tree()
                self._reload_product_map_from_path(path)
                try:
                    if getattr(self, "logger", None):
                        self.logger.log(f"[INFO] Товар удалён. Всего товаров: {len(rows)}")
                except Exception:
                    pass
            else:
                mb.showerror("Каталог товаров", "Не удалось удалить запись (проверьте, не открыт ли файл).")

        btns = ctk.CTkFrame(win)
        btns.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(btns, text="Добавить", command=on_add).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Редактировать", command=on_edit).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Удалить", command=on_delete).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Закрыть", command=win.destroy).pack(side="right", padx=4)

        refresh_tree()

    def _get_product_catalog_path(self):
        try:
            path = (self.prodmap_entry.get() or "").strip()
        except Exception:
            path = ""
        if not path:
            try:
                path = (self.cfg.get("product_map_path") or "").strip()
            except Exception:
                path = ""
        return path or PRODUCT_MAP_DEFAULT

    def _build_report_catalog_map(self, path):
        rows = []
        try:
            rows = self._load_catalog_rows(path)
        except Exception:
            rows = []
        mapping = {}
        dupes = 0
        for item in rows or []:
            g = only_digits(item.get("GTIN", ""))
            if not g:
                continue
            if g in mapping:
                dupes += 1
            mapping[g] = item
        if dupes:
            try:
                self.logger.log(f"[INFO] Каталог: дубли GTIN: {dupes}")
            except Exception:
                pass
        return mapping

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
        catalog_path = self._get_product_catalog_path()
        if not os.path.isfile(catalog_path):
            mb.showerror("Отчет", f"Файл каталога товаров не найден:\n{catalog_path}")
            return
        catalog_map = self._build_report_catalog_map(catalog_path)
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
        if fmt == "16x16":
            self.btw16_entry.delete(0, "end")
            self.btw16_entry.insert(0, p)
        else:
            self.btw30_entry.delete(0, "end")
            self.btw30_entry.insert(0, p)
        self.cfg.setdefault("formats", {})[fmt] = p
        save_config(self.cfg)
        self.logger.log(f"[Пресет] Сохранён путь для {fmt}: {p}")

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
        half = max(4, max_len // 2 - 2)
        return s[:half] + "..." + s[-half:]

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
                self.csv_dir_label.configure(text=self._shorten_path(self.csv_dir))
        except Exception:
            pass

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
                self.brand_value_label.configure(text=val)
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
                    f"Формат: {fmt}",
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
            self.csv_label.configure(text=f"CSV: {_os.path.basename(self.csv_path)}")
        except Exception:
            pass
        try:
            self.csv_rows = load_kontur_raw(p_abs)
            head = self.csv_rows[0] if self.csv_rows else {"DM": "", "NAME": ""}
            self.logger.log(f"CSV: строк={len(self.csv_rows)}; пример DM='{head.get('DM','')}', NAME='{head.get('NAME','')}'")
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
                try:
                    self.index_entry.delete(0, 'end')
                    self.index_entry.insert(0, '1')
                except Exception:
                    pass
                try:
                    self._preview()
                except Exception as _e:
                    try:
                        self.logger.err(f"Авто-превью: {_e}")
                    except Exception:
                        pass
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
                                 command=lambda b=i: self._reprint_batch(b))
            btn.pack(side="left", padx=6, pady=4)

    def _reprint_batch(self, batch_idx):
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

    def _print_sticker_placeholder(self, bidx, start, end, total, sample_row=None, km_count=None, total_rolls=None):
        """Печать стикера через BarTender, если есть шаблон sticker60x40.btw (или cfg['sticker_btw'])."""
        prn = self._get_sticker_printer()
        if not prn:
            self.logger.err("Стикер: принтер не найден.")
            return
        sticker_path = (self.cfg.get("sticker_btw") or STICKER_BTW_DEFAULT)
        if not os.path.isfile(sticker_path):
            fallback = default_template_path(STICKER_BTW_NAME)
            if os.path.isfile(fallback):
                sticker_path = fallback
            else:
                self.logger.err(f"Стикер: шаблон не найден: {sticker_path}.")
                return
        # Собираем полезные поля для подстановки
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
        try:
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
                    info = self.product_map.get(_od(gtin), {}) if getattr(self, "product_map", None) else {}
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
                try:
                    if getattr(self, "logger", None):
                        self.logger.log(
                            "[СТИКЕР] weight debug: "
                            f"sample_row='{sample_row.get('WEIGHT','')}', "
                            f"map='{info.get('WEIGHT','')}', "
                            f"path='{self.cfg.get('product_map_path','')}'"
                        )
                except Exception:
                    pass
            # клиент задаётся вручную: перекрываем payload
            client_name = self._get_client_name()
            if client_name:
                payload["CLIENT"] = client_name
                payload["Клиент"] = client_name
                try:
                    self.cfg["sticker_client"] = client_name
                    save_config(self.cfg)
                except Exception:
                    pass
        except Exception:
            pass
        if km_count is not None:
            payload["КоличествоКМ"] = str(km_count)
            payload["KM_COUNT"] = str(km_count)
        # Диагностика заполнения полей стикера
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

    def _get_prod_date(self):
        try:
            txt = (self.prod_date_entry.get() or "").strip()
        except Exception:
            txt = ""

        try:
            return parse_date_ru(txt)
        except Exception as e:
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

    def _get_exp_days(self):
        t = (self.exp_days_entry.get() or "").strip()
        if not t:
            return None
        try:
            return max(0, int(t))
        except Exception:
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

    def _get_btw_for_format(self, fmt_name: str):

        """

        BTW ищем в подпапке templates рядом с .exe/.py. Если нет — спрашиваем путь и копируем выбранный файл
        туда под именем по умолчанию (16x16.btw/30x20.btw), чтобы exe всё "запомнил".

        """

        default_name = "16x16.btw" if fmt_name == "16x16" else "30x20.btw"

        default_path = default_template_path(default_name)
        legacy_path = os.path.join(BASE_DIR, default_name)

        for candidate in (default_path, legacy_path):
            if os.path.isfile(candidate):
                return candidate

        # если ранее в полях что-то было - используем
        try:
            path = (self.btw16_entry.get().strip() if fmt_name == "16x16" else self.btw30_entry.get().strip())
        except Exception:
            path = ""
        if path and os.path.isfile(path):
            return path

        try:
            if getattr(self, "logger", None):
                self.logger.err(f"BTW не найден: {default_path}. Укажи путь к шаблону {fmt_name}.")
        except Exception:
            pass

        p = fd.askopenfilename(title=f"Укажи BTW для {fmt_name}", filetypes=[("BarTender Template", "*.btw")])
        if p and os.path.isfile(p):
            # копируем в templates под стандартным именем
            try:
                os.makedirs(TEMPLATES_DIR, exist_ok=True)
            except Exception:
                pass
            try:
                target = os.path.join(TEMPLATES_DIR, default_name)
                import shutil as _shutil
                _shutil.copy2(p, target)
                # запишем в поля на всякий случай
                try:
                    if fmt_name == "16x16":
                        self.btw16_entry.delete(0, "end"); self.btw16_entry.insert(0, target)
                    else:
                        self.btw30_entry.delete(0, "end"); self.btw30_entry.insert(0, target)
                except Exception:
                    pass
                return target
            except Exception:
                return p

        mb.showerror("BTW", f"Шаблон для формата {fmt_name} не найден.")
        return None

    
    def _enrich(self, base_row, idx1, allow_empty_part=False):
        d = self._get_prod_date()
        if not d:
            return None
        enr = enrich_row(
            base_row=base_row,
            idx1=idx1,
            prod_date=d,
            exp_days_override=self._get_exp_days(),
            product_map=self.product_map,
            mode_choice=self.format_combo.get()
        )

        # партия задаётся вручную только для формата 30x20
        manual = (self.part_entry.get() or "").strip()
        if (enr.get("_FORMAT") == "30x20"):
            if not manual and not allow_empty_part:
                # подсветим и сообщим
                self._mark_part_entry(True)
                if not getattr(self, "_part_missing_logged", False):
                    self._part_missing_logged = True
                    self.logger.err("Не заполнен номер партии для формата 30x20.")
                return None
            enr["PART_NUM"] = manual
        else:
            enr["PART_NUM"] = ""
        if manual or allow_empty_part or enr.get("_FORMAT") != "30x20":
            self._mark_part_entry(False)

        gtin_key = only_digits(base_row.get("GTIN", ""))
        info = self.product_map.get(gtin_key, {})
        shelf = info.get("SHELF") or {}
        shelf_desc = shelf.get("raw") or "-"
        self.logger.log(
            f"GTIN lookup: FORMAT='{info.get('FORMAT','-') or '-'}', "
            f"SHELF='{shelf_desc}', SHELF_PARSED={enr.get('_SHELF_LOG','')}, "
            f"PART_TPL={'-' if not info.get('PART_TEMPLATE') else info.get('PART_TEMPLATE')}, "
            f"ShortNameExcel='{(info.get('SHORTNAME') or '').strip()}', GTIN={gtin_key}"
        )

        src = "не задан"
        if enr.get("_SHELF_LOG"):
            src = enr["_SHELF_LOG"]
        elif self._get_exp_days() is not None:
            src = f"override_days={self._get_exp_days()}"
        self.logger.log(f"Срок годности: {src}; EXP_DATE={enr.get('EXP_DATE','')}")

        # источник ShortName
        self.logger.log(f"ShortName источник: {enr.get('_SHORT_SRC','?')} → '{enr.get('ShortName','')}'")
        return enr

    def _show_preview_path(self, path):
        try:
            img = Image.open(path)
            self.preview_label.update_idletasks()
            box_w = max(320, int(self.preview_label.winfo_width() or 560)) - 40
            box_h = max(240, int(self.preview_label.winfo_height() or 360)) - 40
            iw, ih = img.size
            ratio = min(box_w / iw, box_h / ih)
            new_w, new_h = max(1, int(iw * ratio)), max(1, int(ih * ratio))
            self.preview_ctkimg = ctk.CTkImage(light_image=img, dark_image=img, size=(new_w, new_h))
            self.preview_label.configure(image=self.preview_ctkimg, text="")
            self.preview_current_path = path
        except Exception as e:
            self.logger.err(f"Не удалось показать превью: {e}")
            self.preview_label.configure(text="Не удалось показать превью")

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
        try:
            if getattr(self, "_preview_resize_busy", False):
                return
            if not self.preview_current_path:
                return
            self._preview_resize_busy = True
            if os.path.isfile(self.preview_current_path):
                self._show_preview_path(self.preview_current_path)
        except Exception:
            pass
        finally:
            self._preview_resize_busy = False
    
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
    def _open_date_picker(self):
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
    
    # ---------- actions ----------
    
    def _preview(self):
        if not self.csv_path:
            mb.showerror("CSV", "Выбери CSV")
            return
        if not self.csv_rows:
            try:
                self.csv_rows = load_kontur_raw(self.csv_path)
            except Exception as e:
                self.logger.err(f"CSV ошибка: {e}")
                return
        if not self.csv_rows:
            mb.showerror("CSV", "Нет данных")
            return
    
        idx1 = min(self._get_index(), len(self.csv_rows))
        base = self.csv_rows[idx1 - 1]
        enr = self._enrich(base, idx1, allow_empty_part=True)
        if not enr:
            return
    
        src = "manual" if self.format_combo.get() in ("16x16", "30x20") else "auto"
        self.logger.log(f"Выбран формат: {enr['_FORMAT']} (источник: {src}, комбо: {self.format_combo.get()}, GTIN={base.get('GTIN','')})")
        fmt_name = enr["_FORMAT"]
        # если формат вычислен (Auto), синхронизируем UI/принтер под реальный формат
        try:
            self._apply_format_ui(fmt_name)
        except Exception:
            pass
        btw = self._get_btw_for_format(fmt_name)
        if not btw:
            return
    
        try:
            fmt = self.bt.open_format(btw)
            self.bt.set_common_print_flags(fmt)
            fmt.PrintSetup.IdenticalCopiesOfLabel = 1
            ok = self.bt.apply_fields(fmt, enr)
            if not ok:
                self.logger.err("Не удалось подставить значения (проверь имена полей в BTW).")
    
            out = os.path.join(os.path.dirname(btw), PREVIEW_NAME)
            # перезаписываем без вопросов
            try:
                if os.path.exists(out):
                    os.remove(out)
            except Exception:
                pass
    
            if self.bt.export_preview(fmt, out):
                self._show_preview_path(out)
                self.logger.log(f"Превью сохранено: {out} (формат {fmt_name})")
            else:
                self.logger.err("Не удалось сформировать превью.")
        except Exception as e:
            self.logger.err(f"Превью: {e}\n{traceback.format_exc()}")
        finally:
            try:
                fmt.Close(1)
            except Exception:
                pass
    
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
        
        
        - гарантируем BarTender COM (self.app)
        
        
        - если на входе строка пути -> открываем через self.app.Formats.Open(...)
        
        
        - назначаем принтер (оба свойства, для совместимости)
        
        
        - подставляем NamedSubStrings из enr
        
        
        Возвращает объект Format или None.
        
        
        """
        
        
        try:
        
        
            # 0) Инициализация BarTender COM, если не создан
        
        
            bt = getattr(self, "app", None)
        
        
            if bt is None:
        
        
                from win32com.client import Dispatch
        
        
                self.app = Dispatch("BarTender.Application")
        
        
                try:
        
        
                    self.app.Visible = True
        
        
                except Exception:
        
        
                    pass
        
        
                bt = self.app
        
        
        
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
        
        
                    fmt = self.app.Formats.Open(path, False, "")
        
        
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

        LOG_EVERY = 100
        PROGRESS_EVERY = 50
        self.cancel_requested = False

        if not self.bt:
            self.logger.err("BarTender COM не инициализирован (bt is None).")
            return

        if not getattr(self, "csv_path", None):
            mb.showerror("CSV", "Выбери CSV")
            return

        try:
            self._part_missing_logged = False
            cur_fmt = (self.format_combo.get() or "").strip()
            if cur_fmt == "30x20" and not (self.part_entry.get() or "").strip():
                self._mark_part_entry(True)
                self.logger.err("Не заполнен номер партии для формата 30x20.")
                mb.showerror("Партия", "Для формата 30x20 заполни номер партии.")
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

        idx0 = max(0, self._get_index() - 1)
        limit = self._get_limit()
        rows_all = self.csv_rows[idx0:] if not limit else self.csv_rows[idx0:idx0 + limit]
        total = len(rows_all)
        if total == 0:
            self.logger.err("Нет строк для печати в указанном диапазоне.")
            return

        job_name = os.path.basename(self.csv_path) if self.csv_path else ""
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
        ribbon_blocked = False

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
                        enriched.append(enr)
                        if (i + 1) % PROGRESS_EVERY == 0:
                            self._set_progress(i + 1, len(rows), f"Подготовка пакета {bidx}")

                if not enriched:
                    if getattr(self, "_part_missing_logged", False):
                        self.logger.err("Печать остановлена: заполните номер партии для формата 30x20.")
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

                self._pause_wait()
                if self._should_stop():
                    break
                self._write_tmp_batch_csv(enriched, tmp_csv_path, add_calib=False)

                fmt = None
                try:
                    labels_for_ribbon = len(enriched)
                    real_in_batch = len(enriched) - calib_added
                    fmt_name = enriched[0].get("_FORMAT") or "16x16"
                    try:
                        self._apply_format_ui(fmt_name)
                        prn = self._get_printer()
                        last_prn = prn
                        last_fmt = fmt_name
                    except Exception:
                        pass

                    if not self._ensure_ribbon_capacity(labels_for_ribbon, fmt_name, prn):
                        ribbon_blocked = True
                        success = False
                        break

                    btw = self._get_btw_for_format(fmt_name)
                    if not btw:
                        self.logger.err(f"Пакет {bidx}: не найден BTW для формата {fmt_name}.")
                        continue

                    fmt = self.bt.open_format(btw)
                    try:
                        self._rebind_template_db(fmt, tmp_csv_path)
                    except Exception:
                        pass
                    try:
                        self._check_template_db(fmt, tmp_csv_path)
                    except Exception:
                        pass

                    fmt.PrintSetup.Printer = prn
                    fmt.PrintSetup.IdenticalCopiesOfLabel = int(copies)
                    fmt.UseDatabase = True
                    fmt.SelectRecordsAtPrint = False
                    fmt.RecordRange = f"1-{len(enriched)}"

                    self._pause_wait()
                    if self._should_stop():
                        raise Exception("Печать остановлена пользователем.")

                    self.logger.log(f"Отправка на печать: {real_in_batch} строк." + (f" (+{calib_added} калибровочных)" if calib_added else ""))
                    fmt.PrintOut(False, self._dialog_flag())

                    self._increment_ribbon_counter(labels_for_ribbon, fmt_name)
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
                    start_num = idx0 + start + 1
                    end_num = idx0 + end
                    sample_real = None
                    for e in enriched:
                        if not e.get("_CALIB"):
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

            if ribbon_blocked:
                self.logger.log("Печать остановлена: нет подтверждения по риббону.")
                return

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
def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
