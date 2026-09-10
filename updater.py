from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app_version import UPDATE_API_URL


_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$")
_INSTALLER_RE = re.compile(r"^BarTenderGUI_Setup(?:[_-].*)?\.exe$", re.IGNORECASE)


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    installer_name: str
    download_url: str
    sha256: str | None = None
    notes: str = ""
    html_url: str = ""


def parse_version(value: str) -> tuple[int, int, int, int]:
    text = str(value or "").strip()
    match = _VERSION_RE.fullmatch(text)
    if not match:
        raise ValueError(f"Некорректная версия: {value!r}")
    parts = [int(x) if x is not None else 0 for x in match.groups()]
    return tuple(parts)  # type: ignore[return-value]


def is_newer_version(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _request(url: str) -> Request:
    return Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AutoPrint-Updater",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    )


def fetch_latest_release(timeout: int = 8) -> ReleaseInfo:
    try:
        with urlopen(_request(UPDATE_API_URL), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "Канал обновлений пока не опубликован. "
                "Создайте публичный репозиторий AutoPrint-GUI-Releases и первый Release."
            ) from exc
        raise UpdateError(f"GitHub вернул HTTP {exc.code} при проверке обновлений.") from exc
    except URLError as exc:
        raise UpdateError(f"Не удалось подключиться к GitHub: {exc.reason}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Не удалось прочитать информацию об обновлении: {exc}") from exc

    tag = str(payload.get("tag_name") or "").strip()
    try:
        parsed = parse_version(tag)
    except ValueError as exc:
        raise UpdateError(f"У последнего Release некорректный тег версии: {tag!r}") from exc
    version = ".".join(str(x) for x in parsed[:3])

    installer_asset = None
    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "").strip()
        if _INSTALLER_RE.fullmatch(name):
            installer_asset = asset
            break

    if installer_asset is None:
        raise UpdateError("В последнем Release не найден установщик BarTenderGUI_Setup_*.exe.")

    download_url = str(installer_asset.get("browser_download_url") or "").strip()
    if not download_url.startswith("https://"):
        raise UpdateError("У Release отсутствует корректная HTTPS-ссылка на установщик.")

    digest = str(installer_asset.get("digest") or "").strip().lower()
    sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
    if sha256 and not re.fullmatch(r"[0-9a-f]{64}", sha256):
        sha256 = None

    return ReleaseInfo(
        version=version,
        installer_name=str(installer_asset.get("name") or "BarTenderGUI_Setup.exe"),
        download_url=download_url,
        sha256=sha256,
        notes=str(payload.get("body") or "").strip(),
        html_url=str(payload.get("html_url") or "").strip(),
    )


def check_for_update(current_version: str, timeout: int = 8) -> ReleaseInfo | None:
    latest = fetch_latest_release(timeout=timeout)
    return latest if is_newer_version(latest.version, current_version) else None


def download_release(
    release: ReleaseInfo,
    timeout: int = 30,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    target = Path(tempfile.gettempdir()) / f"AutoPrint_Update_{release.version}.exe"
    partial = target.with_suffix(".download")

    hasher = hashlib.sha256()
    downloaded = 0
    total: int | None = None

    try:
        with urlopen(_request(release.download_url), timeout=timeout) as response:
            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    total = int(raw_length)
                except ValueError:
                    total = None

            with partial.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded, total)
    except HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Не удалось скачать обновление: HTTP {exc.code}.") from exc
    except URLError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Не удалось скачать обновление: {exc.reason}") from exc
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Не удалось сохранить обновление: {exc}") from exc

    actual_sha256 = hasher.hexdigest().lower()
    if release.sha256 and actual_sha256 != release.sha256.lower():
        partial.unlink(missing_ok=True)
        raise UpdateError(
            "Контрольная сумма обновления не совпала. Установка отменена."
        )

    os.replace(partial, target)
    return target


def launch_installer(installer_path: str | Path) -> None:
    if os.name != "nt":
        raise UpdateError("Автоматическая установка поддерживается только в Windows.")

    installer = Path(installer_path)
    if not installer.is_file():
        raise UpdateError(f"Установщик не найден: {installer}")

    args = [
        str(installer),
        "/SP-",
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
    ]
    try:
        subprocess.Popen(args, cwd=str(installer.parent), close_fds=True)
    except OSError as exc:
        raise UpdateError(f"Не удалось запустить установщик: {exc}") from exc
