from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import ipaddress
import json
import os
import socket
import subprocess
from typing import Optional

try:
    import win32print
except ImportError:  # pragma: no cover - exercised on non-Windows CI
    win32print = None


STATE_AVAILABLE = "available"
STATE_UNAVAILABLE = "unavailable"
STATE_UNKNOWN = "unknown"

CONNECTION_TCP = "tcp"
CONNECTION_USB = "usb"
CONNECTION_LOCAL = "local"
CONNECTION_SHARED = "shared"
CONNECTION_NETWORK = "network"
CONNECTION_UNKNOWN = "unknown"

# Winspool.h PRINTER_STATUS_* values.
PRINTER_STATUS_PAUSED = 0x00000001
PRINTER_STATUS_ERROR = 0x00000002
PRINTER_STATUS_PENDING_DELETION = 0x00000004
PRINTER_STATUS_PAPER_JAM = 0x00000008
PRINTER_STATUS_PAPER_OUT = 0x00000010
PRINTER_STATUS_PAPER_PROBLEM = 0x00000040
PRINTER_STATUS_OFFLINE = 0x00000080
PRINTER_STATUS_OUTPUT_BIN_FULL = 0x00000800
PRINTER_STATUS_NOT_AVAILABLE = 0x00001000
PRINTER_STATUS_NO_TONER = 0x00040000
PRINTER_STATUS_PAGE_PUNT = 0x00080000
PRINTER_STATUS_USER_INTERVENTION = 0x00100000
PRINTER_STATUS_OUT_OF_MEMORY = 0x00200000
PRINTER_STATUS_DOOR_OPEN = 0x00400000
PRINTER_STATUS_SERVER_UNKNOWN = 0x00800000

# Winspool.h PRINTER_ATTRIBUTE_* values used by the health check.
PRINTER_ATTRIBUTE_NETWORK = 0x00000010
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400

_BLOCKING_STATUS_FLAGS = (
    (PRINTER_STATUS_PAUSED, "очередь приостановлена"),
    (PRINTER_STATUS_ERROR, "ошибка принтера"),
    (PRINTER_STATUS_PENDING_DELETION, "очередь удаляется"),
    (PRINTER_STATUS_PAPER_JAM, "замятие бумаги/этикетки"),
    (PRINTER_STATUS_PAPER_OUT, "нет бумаги/этикеток"),
    (PRINTER_STATUS_PAPER_PROBLEM, "проблема с бумагой/этикетками"),
    (PRINTER_STATUS_OFFLINE, "принтер offline"),
    (PRINTER_STATUS_OUTPUT_BIN_FULL, "выходной лоток заполнен"),
    (PRINTER_STATUS_NOT_AVAILABLE, "принтер недоступен"),
    (PRINTER_STATUS_NO_TONER, "нет расходного материала"),
    (PRINTER_STATUS_PAGE_PUNT, "страница не может быть напечатана"),
    (PRINTER_STATUS_USER_INTERVENTION, "требуется вмешательство оператора"),
    (PRINTER_STATUS_OUT_OF_MEMORY, "недостаточно памяти принтера"),
    (PRINTER_STATUS_DOOR_OPEN, "крышка принтера открыта"),
    (PRINTER_STATUS_SERVER_UNKNOWN, "статус print server неизвестен"),
)

_LOCAL_PORT_PREFIXES = (
    "LPT",
    "COM",
    "FILE:",
    "PORTPROMPT:",
    "NUL:",
    "XPSPORT:",
    "SHRFAX:",
    "LOCALPORT",
)


@dataclass(frozen=True)
class TcpEndpoint:
    host: str
    port: int


@dataclass(frozen=True)
class PrinterHealth:
    printer_name: str
    state: str
    connection: str
    detail: str
    port_name: str = ""
    host: str = ""
    tcp_port: Optional[int] = None


def blocking_status_reasons(status: int, attributes: int = 0) -> tuple[str, ...]:
    reasons = [
        label
        for flag, label in _BLOCKING_STATUS_FLAGS
        if int(status or 0) & flag
    ]
    if int(attributes or 0) & PRINTER_ATTRIBUTE_WORK_OFFLINE:
        reasons.append("в Windows включён режим «Работать автономно»")
    return tuple(reasons)


def _first_port_name(value: str) -> str:
    return str(value or "").split(",", 1)[0].strip()


def _local_connection_kind(port_name: str) -> Optional[str]:
    upper = str(port_name or "").strip().upper()
    if upper.startswith("USB"):
        return CONNECTION_USB
    if any(upper.startswith(prefix) for prefix in _LOCAL_PORT_PREFIXES):
        return CONNECTION_LOCAL
    return None


def _endpoint_from_port_name(port_name: str) -> Optional[TcpEndpoint]:
    raw = str(port_name or "").strip().rstrip(":")
    candidates = [raw]
    upper = raw.upper()
    for prefix in ("IP_", "TCP_"):
        if upper.startswith(prefix):
            candidates.insert(0, raw[len(prefix):])
            break
    for candidate in candidates:
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return TcpEndpoint(candidate, 9100)
    return None


@lru_cache(maxsize=128)
def resolve_tcp_endpoint(port_name: str) -> Optional[TcpEndpoint]:
    """Resolve a Windows printer port to its configured TCP endpoint.

    Get-PrinterPort is used because arbitrary Standard TCP/IP port names do not
    have to contain the printer IP address. The fallback only handles port names
    that are themselves an IP address (or IP_/TCP_ prefixed).
    """
    port_name = _first_port_name(port_name)
    if not port_name:
        return None

    fallback = _endpoint_from_port_name(port_name)
    if os.name != "nt":
        return fallback

    escaped = port_name.replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop';"
        f"$p=Get-PrinterPort -Name '{escaped}' | Select-Object -First 1;"
        "if ($null -eq $p) { exit 2 };"
        "[pscustomobject]@{"
        "Host=[string]$p.PrinterHostAddress;"
        "Port=[int]$p.PortNumber;"
        "Protocol=[int]$p.Protocol"
        "} | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return fallback

    if completed.returncode != 0:
        return fallback

    try:
        payload = json.loads((completed.stdout or "").strip() or "{}")
    except (TypeError, ValueError):
        return fallback

    host = str(payload.get("Host") or "").strip()
    if not host:
        return fallback

    try:
        port = int(payload.get("Port") or 0)
    except (TypeError, ValueError):
        port = 0
    try:
        protocol = int(payload.get("Protocol") or 0)
    except (TypeError, ValueError):
        protocol = 0

    if port <= 0:
        port = 515 if protocol == 2 else 9100
    return TcpEndpoint(host, port)


def probe_tcp_endpoint(endpoint: TcpEndpoint, timeout: float = 0.75) -> tuple[bool, str]:
    try:
        sock = socket.create_connection(
            (endpoint.host, int(endpoint.port)),
            timeout=max(0.05, float(timeout)),
        )
    except OSError as exc:
        return False, str(exc)
    try:
        return True, ""
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _unavailable(
    printer_name: str,
    detail: str,
    *,
    connection: str = CONNECTION_UNKNOWN,
    port_name: str = "",
    endpoint: Optional[TcpEndpoint] = None,
) -> PrinterHealth:
    return PrinterHealth(
        printer_name=printer_name,
        state=STATE_UNAVAILABLE,
        connection=connection,
        detail=detail,
        port_name=port_name,
        host=endpoint.host if endpoint else "",
        tcp_port=endpoint.port if endpoint else None,
    )


def _available(
    printer_name: str,
    detail: str,
    *,
    connection: str,
    port_name: str = "",
    endpoint: Optional[TcpEndpoint] = None,
) -> PrinterHealth:
    return PrinterHealth(
        printer_name=printer_name,
        state=STATE_AVAILABLE,
        connection=connection,
        detail=detail,
        port_name=port_name,
        host=endpoint.host if endpoint else "",
        tcp_port=endpoint.port if endpoint else None,
    )


def _unknown(
    printer_name: str,
    detail: str,
    *,
    connection: str = CONNECTION_UNKNOWN,
    port_name: str = "",
) -> PrinterHealth:
    return PrinterHealth(
        printer_name=printer_name,
        state=STATE_UNKNOWN,
        connection=connection,
        detail=detail,
        port_name=port_name,
    )


def check_printer(printer_name: str, timeout: float = 0.75) -> PrinterHealth:
    """Check Windows queue state and, for Standard TCP/IP ports, TCP reachability.

    The function never submits print data. Unknown status is deliberately
    distinct from unavailable so unsupported/custom port monitors do not block
    printing merely because AutoPrint cannot prove reachability.
    """
    printer_name = str(printer_name or "").strip()
    if not printer_name or printer_name == "(нет принтеров)":
        return _unknown(printer_name, "принтер не выбран")

    if win32print is None:
        return _unknown(
            printer_name,
            "Windows spooler API недоступен",
        )

    handle = None
    try:
        handle = win32print.OpenPrinter(printer_name)
        info = win32print.GetPrinter(handle, 2) or {}
    except Exception as exc:
        return _unavailable(
            printer_name,
            f"очередь Windows недоступна: {exc}",
        )
    finally:
        if handle is not None:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                pass

    port_name = _first_port_name(info.get("pPortName") or "")
    status = int(info.get("Status") or 0)
    attributes = int(info.get("Attributes") or 0)
    server_name = str(info.get("pServerName") or "").strip()

    local_kind = _local_connection_kind(port_name)
    connection = local_kind or CONNECTION_UNKNOWN
    reasons = blocking_status_reasons(status, attributes)
    if reasons:
        if not local_kind and (
            server_name
            or printer_name.startswith("\\")
            or (attributes & PRINTER_ATTRIBUTE_NETWORK)
        ):
            connection = CONNECTION_SHARED
        return _unavailable(
            printer_name,
            "; ".join(reasons),
            connection=connection,
            port_name=port_name,
        )

    if local_kind == CONNECTION_USB:
        return _available(
            printer_name,
            f"USB-порт {port_name}; очередь Windows готова",
            connection=CONNECTION_USB,
            port_name=port_name,
        )

    if local_kind == CONNECTION_LOCAL:
        return _available(
            printer_name,
            f"локальный порт {port_name}; очередь Windows готова",
            connection=CONNECTION_LOCAL,
            port_name=port_name,
        )

    if server_name or printer_name.startswith("\\"):
        return _available(
            printer_name,
            "удалённая очередь Windows доступна",
            connection=CONNECTION_SHARED,
            port_name=port_name,
        )

    endpoint = resolve_tcp_endpoint(port_name)
    if endpoint is not None:
        ok, error = probe_tcp_endpoint(endpoint, timeout=timeout)
        endpoint_text = f"{endpoint.host}:{endpoint.port}"
        if ok:
            return _available(
                printer_name,
                f"TCP {endpoint_text} доступен",
                connection=CONNECTION_TCP,
                port_name=port_name,
                endpoint=endpoint,
            )
        return _unavailable(
            printer_name,
            f"TCP {endpoint_text} недоступен: {error or 'нет соединения'}",
            connection=CONNECTION_TCP,
            port_name=port_name,
            endpoint=endpoint,
        )

    if port_name.upper().startswith("WSD-") or (
        attributes & PRINTER_ATTRIBUTE_NETWORK
    ):
        return _unknown(
            printer_name,
            f"очередь Windows готова, но endpoint сетевого порта {port_name or '—'} не определён",
            connection=CONNECTION_NETWORK,
            port_name=port_name,
        )

    return _unknown(
        printer_name,
        f"очередь Windows готова, но тип порта {port_name or '—'} не распознан",
        port_name=port_name,
    )
