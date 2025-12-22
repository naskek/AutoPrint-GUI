# -*- coding: utf-8 -*-

import os
import json
import re
import datetime as dt
from calendar import monthrange
import sys

try:
    import win32print
except ImportError:
    win32print = None


def get_base_dir():
    """Возвращает базовую директорию приложения (для .py и .exe)."""
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return os.getcwd()


def _cfg_dir():
    base = os.path.join(get_base_dir(), "config")
    os.makedirs(base, exist_ok=True)
    return base


def _cfg_path():
    return os.path.join(_cfg_dir(), "config.json")


def load_config():
    p = _cfg_path()
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_config(cfg: dict):
    try:
        with open(_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except IOError:
        pass


def enum_printers():
    if not win32print:
        return []
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    try:
        return [name for (_f, _d, name, _c) in win32print.EnumPrinters(flags)]
    except Exception:
        return []


def only_digits(s):
    return re.sub(r"\D+", "", s or "")


def short_gtin(gtin):
    d = only_digits(gtin)
    return d[-3:].zfill(3) if d else ""


def parse_date_ru(s: str):
    """Строгий парсер ДД.ММ.ГГГГ → date, иначе ValueError."""
    s = (s or "").strip()
    if not s:
        raise ValueError("Пустая дата")
    try:
        return dt.datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        raise ValueError(f"Неверный формат даты: '{s}', нужен ДД.ММ.ГГГГ")


def add_days(date_, days: int):
    return date_ + dt.timedelta(days=days)


def add_months(date_, months: int):
    y = date_.year + (date_.month - 1 + months) // 12
    m = (date_.month - 1 + months) % 12 + 1
    d = min(date_.day, monthrange(y, m)[1])
    return dt.date(y, m, d)


def add_years(date_, years: int):
    y = date_.year + years
    # Коррекция для 29 февраля, если год становится не високосным
    try:
        return dt.date(y, date_.month, date_.day)
    except ValueError:
        # Случай, когда день не существует в новом месяце (например, 29.02)
        d = min(date_.day, monthrange(y, date_.month)[1])
        return dt.date(y, date_.month, d)
