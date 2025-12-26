# -*- coding: utf-8 -*-

from __future__ import annotations

import os
from dataclasses import dataclass

from utils import get_base_dir, load_config, load_env_file

DEFAULT_TRUE_API_BASE_URL = "https://markirovka.crpt.ru/api/v3/true-api"
DEFAULT_PG = ""
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_CODES_PER_REQUEST = 1000
DEFAULT_CONCURRENCY = 3
DEFAULT_RETRIES = 1
DEFAULT_REFRESH_SAFETY_SECONDS = 900

_ENV_LOADED = False
_RUNTIME_OVERRIDES: dict[str, str] = {}


@dataclass
class CrptSettings:
    true_api_base_url: str
    oms_connection: str
    pg: str
    cert_thumbprint: str
    token_provider_inn: str
    token_refresh_safety_seconds: int
    timeout: float
    max_codes_per_request: int
    concurrency: int
    retries: int


def _load_env_once():
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    try:
        env_path = os.path.join(get_base_dir(), ".env")
        load_env_file(env_path, override=False)
    finally:
        _ENV_LOADED = True


def set_runtime_overrides(**kwargs):
    for k, v in kwargs.items():
        if v is None:
            continue
        _RUNTIME_OVERRIDES[str(k)] = str(v)


def clear_runtime_overrides():
    _RUNTIME_OVERRIDES.clear()


def _env_str(name: str) -> str:
    return str(os.environ.get(name, "") or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "") or "").strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "") or "").strip())
    except Exception:
        return default


def _cfg_str(cfg: dict, key: str) -> str:
    try:
        return str(cfg.get(key, "") or "").strip()
    except Exception:
        return ""


def _normalize_thumbprint(tp: str) -> str:
    return "".join((tp or "").split()).upper()


def load_crpt_settings() -> CrptSettings:
    _load_env_once()
    cfg = load_config() or {}

    true_api_base = _env_str("CRPT_TRUE_API_BASE_URL") or _cfg_str(cfg, "crpt_true_api_base_url")
    if not true_api_base:
        true_api_base = DEFAULT_TRUE_API_BASE_URL
    true_api_base = true_api_base.rstrip("/")

    env_pg = _env_str("CRPT_PG")
    env_oms = _env_str("CRPT_OMS_CONNECTION")
    env_cert = _env_str("CRPT_CERT_THUMBPRINT")
    env_inn = _env_str("CRPT_TOKEN_PROVIDER_INN")
    pg = env_pg or _cfg_str(cfg, "crpt_pg")
    oms_connection = env_oms or _cfg_str(cfg, "crpt_oms_connection")
    token_provider_inn = env_inn or _cfg_str(cfg, "crpt_token_provider_inn")
    cert_thumbprint = env_cert or _cfg_str(cfg, "crpt_cert_thumbprint")

    if _RUNTIME_OVERRIDES.get("oms_connection") and not env_oms:
        oms_connection = _RUNTIME_OVERRIDES["oms_connection"]
    if _RUNTIME_OVERRIDES.get("cert_thumbprint") and not env_cert:
        cert_thumbprint = _RUNTIME_OVERRIDES["cert_thumbprint"]

    if not pg:
        pg = DEFAULT_PG

    timeout = _env_float("CRPT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    max_codes = _env_int("CRPT_MAX_CODES_PER_REQUEST", DEFAULT_MAX_CODES_PER_REQUEST)
    if max_codes <= 0:
        max_codes = DEFAULT_MAX_CODES_PER_REQUEST
    concurrency = DEFAULT_CONCURRENCY
    retries = DEFAULT_RETRIES
    refresh_safety = _env_int("CRPT_TOKEN_REFRESH_SAFETY_SECONDS", DEFAULT_REFRESH_SAFETY_SECONDS)
    if refresh_safety < 0:
        refresh_safety = DEFAULT_REFRESH_SAFETY_SECONDS

    return CrptSettings(
        true_api_base_url=true_api_base,
        oms_connection=oms_connection,
        pg=pg,
        cert_thumbprint=_normalize_thumbprint(cert_thumbprint),
        token_provider_inn=token_provider_inn,
        token_refresh_safety_seconds=refresh_safety,
        timeout=timeout,
        max_codes_per_request=max_codes,
        concurrency=concurrency,
        retries=retries,
    )
