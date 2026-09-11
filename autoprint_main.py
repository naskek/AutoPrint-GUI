from __future__ import annotations

import queue
import threading
from tkinter import messagebox as mb

import customtkinter as ctk

import bartender_gui
from app_version import APP_VERSION
from licensing import ensure_license_or_exit
from updater import UpdateError, check_for_update, download_release, launch_installer


# Keep the legacy module untouched while making the new launcher the single
# place that owns the displayed/runtime version.
bartender_gui.APP_VERSION = APP_VERSION
bartender_gui.APP_TITLE = f"BarTender GUI V{APP_VERSION}"


class AutoPrintApp(bartender_gui.App):
    def __init__(self):
        self._update_ui_queue: queue.Queue = queue.Queue()
        self._version_dialog = None
        self._version_update_btn = None
        self._update_button_text = "Обновить"
        self._update_button_enabled = True
        super().__init__()
        self.after(100, self._drain_update_ui_queue)

    def _open_version_dialog(self) -> None:
        dialog = getattr(self, "_version_dialog", None)
        try:
            if dialog is not None and dialog.winfo_exists():
                dialog.lift()
                dialog.focus_force()
                return
        except Exception:
            pass

        dialog = ctk.CTkToplevel(self)
        self._version_dialog = dialog
        dialog.title("AutoPrint")
        dialog.geometry("300x140")
        dialog.resizable(False, False)
        dialog.transient(self)

        def close_dialog() -> None:
            if self._version_dialog is not dialog:
                return
            self._version_update_btn = None
            self._version_dialog = None
            try:
                dialog.destroy()
            except Exception:
                pass

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        ctk.CTkLabel(dialog, text=f"Версия: {APP_VERSION}").pack(
            padx=16, pady=(24, 14)
        )
        self._version_update_btn = ctk.CTkButton(
            dialog,
            text=self._update_button_text,
            state="normal" if self._update_button_enabled else "disabled",
            command=self._check_updates_clicked,
            width=150,
        )
        self._version_update_btn.pack(padx=16, pady=(0, 18))
        try:
            dialog.lift()
            dialog.focus_force()
        except Exception:
            pass

    def _post_update_ui(self, callback) -> None:
        try:
            self._update_ui_queue.put_nowait(callback)
        except Exception:
            pass

    def _drain_update_ui_queue(self) -> None:
        try:
            while True:
                callback = self._update_ui_queue.get_nowait()
                try:
                    callback()
                except Exception as exc:
                    try:
                        self.logger.err(f"Updater UI error: {exc}")
                    except Exception:
                        pass
        except queue.Empty:
            pass

        try:
            if self.winfo_exists():
                self.after(100, self._drain_update_ui_queue)
        except Exception:
            pass

    def _set_update_button(self, text: str, enabled: bool = True) -> None:
        self._update_button_text = text
        self._update_button_enabled = bool(enabled)
        button = getattr(self, "_version_update_btn", None)
        if button is None:
            return
        try:
            if not button.winfo_exists():
                return
            button.configure(text=text, state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _check_updates_clicked(self) -> None:
        self._set_update_button("Проверка…", enabled=False)

        def worker() -> None:
            try:
                release = check_for_update(APP_VERSION)
            except Exception as exc:
                self._post_update_ui(lambda e=exc: self._update_check_failed(e))
                return
            self._post_update_ui(lambda r=release: self._update_check_finished(r))

        threading.Thread(target=worker, name="autoprint-update-check", daemon=True).start()

    def _update_check_failed(self, exc: Exception) -> None:
        self._set_update_button("Обновить", enabled=True)
        message = str(exc) if isinstance(exc, UpdateError) else f"Ошибка проверки обновлений: {exc}"
        try:
            self.logger.err(message)
        except Exception:
            pass
        mb.showerror("Обновление AutoPrint", message)

    def _update_check_finished(self, release) -> None:
        self._set_update_button("Обновить", enabled=True)
        if release is None:
            mb.showinfo(
                "Обновление AutoPrint",
                f"Установлена актуальная версия {APP_VERSION}.",
            )
            return

        notes = (release.notes or "").strip()
        if len(notes) > 900:
            notes = notes[:897] + "..."
        details = f"\n\n{notes}" if notes else ""
        install = mb.askyesno(
            "Доступно обновление",
            f"Текущая версия: {APP_VERSION}\n"
            f"Новая версия: {release.version}"
            f"{details}\n\nСкачать и установить обновление?",
        )
        if not install:
            return

        self._set_update_button("Скачивание…", enabled=False)

        def worker() -> None:
            try:
                installer = download_release(release)
            except Exception as exc:
                self._post_update_ui(lambda e=exc: self._update_download_failed(e))
                return
            self._post_update_ui(lambda p=installer, r=release: self._install_downloaded_update(p, r))

        threading.Thread(target=worker, name="autoprint-update-download", daemon=True).start()

    def _update_download_failed(self, exc: Exception) -> None:
        self._set_update_button("Обновить", enabled=True)
        message = str(exc) if isinstance(exc, UpdateError) else f"Ошибка загрузки обновления: {exc}"
        try:
            self.logger.err(message)
        except Exception:
            pass
        mb.showerror("Обновление AutoPrint", message)

    def _install_downloaded_update(self, installer_path, release) -> None:
        try:
            launch_installer(installer_path)
        except Exception as exc:
            self._update_download_failed(exc)
            return

        try:
            self.logger.log(
                f"[INFO] Запущена установка AutoPrint {release.version}. Приложение закрывается."
            )
        except Exception:
            pass

        # Inno Setup waits/retries while the current executable is still in use.
        # Exit immediately after launching Setup, as recommended by Inno Setup.
        try:
            self.after(50, self._shutdown_app)
        except Exception:
            self._shutdown_app()


def main() -> int:
    if ensure_license_or_exit() is None:
        return 1
    app = AutoPrintApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
