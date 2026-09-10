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
        super().__init__()
        self.after(100, self._drain_update_ui_queue)

    def _build_menu(self):
        super()._build_menu()
        try:
            bar = self.menu_bar_frame
            self.version_label = ctk.CTkLabel(
                bar,
                text=f"v{APP_VERSION}",
                width=58,
                height=28,
            )
            self.version_label.pack(side="right", padx=(2, 8), pady=0)

            self.update_btn = ctk.CTkButton(
                bar,
                text="Обновить",
                width=105,
                height=28,
                corner_radius=0,
                command=self._check_updates_clicked,
            )
            self.update_btn.pack(side="right", padx=(2, 0), pady=0)
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
        try:
            self.update_btn.configure(text=text, state="normal" if enabled else "disabled")
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
            self.after(50, self.destroy)
        except Exception:
            self.destroy()


def main() -> int:
    if ensure_license_or_exit() is None:
        return 1
    app = AutoPrintApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
