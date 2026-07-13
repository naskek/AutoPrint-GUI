from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .license_manager import LicenseErrorCode, LicenseValidationError


_ERROR_MESSAGES = {
    LicenseErrorCode.FILE_NOT_FOUND: "Файл лицензии не найден.",
    LicenseErrorCode.INVALID_JSON: "Файл лицензии содержит некорректный JSON.",
    LicenseErrorCode.INVALID_FORMAT: "Формат файла лицензии недействителен.",
    LicenseErrorCode.UNKNOWN_KEY: "Лицензия подписана неизвестным ключом.",
    LicenseErrorCode.INVALID_SIGNATURE: "Подпись лицензии недействительна. Файл мог быть изменён.",
    LicenseErrorCode.NOT_YET_VALID: "Срок действия лицензии ещё не начался.",
    LicenseErrorCode.EXPIRED: "Срок действия лицензии истёк.",
    LicenseErrorCode.APP_MISMATCH: "Лицензия выпущена для другого приложения.",
    LicenseErrorCode.MACHINE_MISMATCH: "Лицензия выпущена для другого компьютера.",
    LicenseErrorCode.FEATURE_MISSING: "Лицензия не разрешает запуск функций печати.",
    LicenseErrorCode.MACHINE_ID_UNAVAILABLE: "Не удалось получить код этого компьютера.",
}


def show_license_error(
    error: LicenseValidationError,
    machine_hash: str | None,
    license_path: str | Path,
) -> None:
    root = tk.Tk()
    root.title("AutoPrint — лицензия")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = ttk.Frame(root, padding=18)
    frame.grid(row=0, column=0, sticky="nsew")
    ttk.Label(
        frame,
        text="Лицензия не найдена или недействительна.",
        font=("Segoe UI", 12, "bold"),
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
    ttk.Label(
        frame,
        text=_ERROR_MESSAGES.get(error.code, "Не удалось проверить лицензию."),
        wraplength=560,
        justify="left",
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
    ttk.Label(frame, text=f"Ожидаемый файл:\n{Path(license_path)}", justify="left").grid(
        row=2,
        column=0,
        columnspan=2,
        sticky="w",
        pady=(0, 10),
    )

    machine_code = machine_hash or "Недоступен"
    ttk.Label(frame, text=f"Код этого ПК:\n{machine_code}", justify="left").grid(
        row=3,
        column=0,
        columnspan=2,
        sticky="w",
        pady=(0, 12),
    )
    ttk.Label(
        frame,
        text="Отправьте код поставщику для получения license.json.",
        wraplength=560,
    ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 14))

    def copy_machine_code() -> None:
        if machine_hash:
            root.clipboard_clear()
            root.clipboard_append(machine_hash)
            root.update()

    copy_button = ttk.Button(frame, text="Копировать код", command=copy_machine_code)
    copy_button.grid(row=5, column=0, sticky="w")
    if not machine_hash:
        copy_button.state(["disabled"])
    ttk.Button(frame, text="Закрыть", command=root.destroy).grid(row=5, column=1, sticky="e")

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.update_idletasks()
    x = max(0, (root.winfo_screenwidth() - root.winfo_width()) // 2)
    y = max(0, (root.winfo_screenheight() - root.winfo_height()) // 2)
    root.geometry(f"+{x}+{y}")
    root.mainloop()
