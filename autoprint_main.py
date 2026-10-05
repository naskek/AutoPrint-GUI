from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox as mb

import customtkinter as ctk

import bartender_gui
from app_version import APP_VERSION
from licensing import ensure_license_or_exit
from updater import UpdateError, check_for_update, download_release, launch_installer


# Keep the legacy module untouched while making the new launcher the single
# place that owns the displayed/runtime version.
bartender_gui.APP_VERSION = APP_VERSION
bartender_gui.APP_TITLE = f"AutoPrint V{APP_VERSION}"

STARTUP_UPDATE_DELAY_MS = 1500
MEBIBYTE = 1024 * 1024
PART_ENTRY_PLACEHOLDER = "Номер партии вручную"


def valid_part_entry_value(value: str) -> bool:
    """Allow CustomTkinter's visual placeholder while keeping user input numeric."""
    text = str(value or "")
    return (
        text == PART_ENTRY_PLACEHOLDER
        or text == ""
        or (text.isascii() and text.isdigit())
    )


def format_download_progress(
    downloaded: int, total: int | None
) -> tuple[float | None, str, str]:
    downloaded_mb = max(0, downloaded) / MEBIBYTE
    if total is None or total <= 0:
        return None, "", f"{downloaded_mb:.1f} МБ"
    fraction = min(1.0, max(0.0, downloaded / total))
    total_mb = total / MEBIBYTE
    return fraction, f"{round(fraction * 100)}%", f"{downloaded_mb:.1f} МБ / {total_mb:.1f} МБ"


def startup_update_check_enabled(frozen=None) -> bool:
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    return bool(frozen)


def license_check_required(frozen=None) -> bool:
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    return bool(frozen)


class AutoPrintApp(bartender_gui.App):
    def __init__(self):
        self._update_ui_queue: queue.Queue = queue.Queue()
        self._version_dialog = None
        self._version_update_btn = None
        self._update_button_text = "Обновить"
        self._update_button_enabled = True
        self._update_check_in_progress = False
        self._startup_update_check_started = False
        self._startup_update_prompted = False
        self._update_progress_dialog = None
        self._update_progress_status_label = None
        self._update_progress_bar = None
        self._update_progress_percent_label = None
        self._update_progress_detail_label = None
        self._update_progress_action_button = None
        self._update_progress_close_button = None
        self._update_progress_indeterminate = False
        self._update_download_total_known = False
        self._pending_installer_path = None
        self._pending_update_release = None
        self._active_update_release = None
        super().__init__()
        self._configure_part_entry_validation()
        self.after(100, self._drain_update_ui_queue)
        self._schedule_startup_update_check()

    def _configure_part_entry_validation(self) -> None:
        """Keep CTk placeholder rendering compatible with digit-only part input."""
        validator = (self.register(valid_part_entry_value), "%P")
        self.part_entry.configure(validate="key", validatecommand=validator)

    def _apply_format_ui(self, fmt: str, *, selected_fmt: str | None = None) -> None:
        """Apply resolved printer choice without letting Auto preview lock part input."""
        resolved_fmt = (fmt or "").strip()
        if selected_fmt is None:
            selected_fmt = resolved_fmt
            try:
                selected_fmt = (self.format_combo.get() or "").strip() or resolved_fmt
            except Exception:
                pass
        selected_fmt = (selected_fmt or "").strip()

        try:
            if not selected_fmt or selected_fmt == "Auto":
                self.part_entry.configure(state="normal")
            elif self._template_requires_manual_part(selected_fmt):
                self.part_entry.configure(state="normal")
            else:
                self.part_entry.configure(state="disabled")
                self.part_entry.delete(0, "end")
        except Exception:
            pass
        self._set_default_printer_for_format(resolved_fmt)

    def _on_format_change(self, value):
        self._apply_format_ui(value, selected_fmt=value)
        self._refresh_runtime_status()
        self._schedule_live_preview()

    def _schedule_startup_update_check(self) -> bool:
        if not startup_update_check_enabled():
            return False
        self.after(STARTUP_UPDATE_DELAY_MS, self._run_startup_update_check)
        return True

    def _run_startup_update_check(self) -> bool:
        if self.__dict__.get("_startup_update_check_started", False):
            return False
        self._startup_update_check_started = True
        return self._start_update_check(interactive=False)

    def _open_version_dialog(self) -> None:
        dialog = self.__dict__.get("_version_dialog")
        try:
            if dialog is not None and dialog.winfo_exists():
                self._close_version_dialog(dialog)
                return
        except Exception:
            self._close_version_dialog(dialog)

        dialog = tk.Toplevel(self)
        self._version_dialog = dialog
        dialog.withdraw()
        dialog.title("AutoPrint")
        dialog.geometry("300x140")
        dialog.resizable(False, False)
        dialog.overrideredirect(True)
        dialog.transient(self)
        content = ctk.CTkFrame(dialog, corner_radius=8, border_width=1)
        content.pack(fill="both", expand=True)
        self._match_plain_popover_background(dialog, content)
        ctk.CTkLabel(content, text=f"Версия: {APP_VERSION}").pack(
            padx=16, pady=(24, 14)
        )
        self._version_update_btn = ctk.CTkButton(
            content,
            text=self._update_button_text,
            state="normal" if self._update_button_enabled else "disabled",
            command=self._check_updates_clicked,
            width=150,
        )
        self._version_update_btn.pack(padx=16, pady=(0, 18))
        self._activate_popover(
            dialog,
            self.version_identity_btn,
            lambda: self._close_version_dialog(dialog),
            align_right=True,
        )

    def _close_version_dialog(self, dialog=None) -> None:
        current = self.__dict__.get("_version_dialog")
        if dialog is None:
            dialog = current
        if dialog is None:
            return
        self._remove_popover_outside_click(dialog)
        if current is dialog:
            self._version_update_btn = None
            self._version_dialog = None
        try:
            dialog.destroy()
        except Exception:
            pass

    def _post_update_ui(self, callback) -> None:
        if self.__dict__.get("_shutdown_started", False):
            self._update_check_in_progress = False
            return
        try:
            self._update_ui_queue.put_nowait(callback)
        except Exception:
            self._update_check_in_progress = False

    def _drain_update_ui_queue(self) -> None:
        if self.__dict__.get("_shutdown_started", False):
            return
        try:
            while True:
                callback = self._update_ui_queue.get_nowait()
                if self.__dict__.get("_shutdown_started", False):
                    return
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
        button = self.__dict__.get("_version_update_btn")
        if button is None:
            return
        try:
            if not button.winfo_exists():
                return
            button.configure(text=text, state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _check_updates_clicked(self) -> None:
        self._start_update_check(interactive=True)

    def _start_update_check(self, *, interactive: bool) -> bool:
        if self.__dict__.get("_update_check_in_progress", False):
            return False
        self._update_check_in_progress = True
        self._set_update_button("Проверка…", enabled=False)

        def worker() -> None:
            try:
                release = check_for_update(APP_VERSION)
            except Exception as exc:
                self._post_update_ui(
                    lambda e=exc: self._update_check_failed(e, interactive=interactive)
                )
                return
            self._post_update_ui(
                lambda r=release: self._update_check_finished(
                    r, interactive=interactive
                )
            )

        try:
            threading.Thread(
                target=worker, name="autoprint-update-check", daemon=True
            ).start()
        except Exception as exc:
            self._update_check_failed(exc, interactive=interactive)
            return False
        return True

    def _update_check_failed(self, exc: Exception, *, interactive: bool = True) -> None:
        self._update_check_in_progress = False
        self._set_update_button("Обновить", enabled=True)
        message = str(exc) if isinstance(exc, UpdateError) else f"Ошибка проверки обновлений: {exc}"
        try:
            if interactive:
                self.logger.err(message)
            else:
                self.logger.log(f"[WARN] Фоновая проверка обновлений: {message}")
        except Exception:
            pass
        if interactive:
            mb.showerror("Обновление AutoPrint", message)

    def _update_check_finished(self, release, *, interactive: bool = True) -> None:
        self._update_check_in_progress = False
        self._set_update_button("Обновить", enabled=True)
        if release is None:
            if interactive:
                mb.showinfo(
                    "Обновление AutoPrint",
                    f"Установлена актуальная версия {APP_VERSION}.",
                )
            return

        if not interactive:
            if self.__dict__.get("_startup_update_prompted", False):
                return
            self._startup_update_prompted = True

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

        self._open_update_progress_dialog(release)
        self._start_update_download(release)

    def _progress_dialog_alive(self) -> bool:
        dialog = self.__dict__.get("_update_progress_dialog")
        if dialog is None:
            return False
        try:
            return bool(dialog.winfo_exists())
        except Exception:
            return False

    def _open_update_progress_dialog(self, release) -> None:
        if self._progress_dialog_alive():
            self._close_update_progress_dialog()

        dialog = ctk.CTkToplevel(self)
        self._update_progress_dialog = dialog
        self._active_update_release = release
        dialog.title("Обновление AutoPrint")
        dialog.geometry("460x230")
        dialog.resizable(False, False)
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        self._update_progress_status_label = ctk.CTkLabel(
            dialog,
            text="Скачивание обновления...",
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self._update_progress_status_label.pack(fill="x", padx=24, pady=(24, 14))
        self._update_progress_bar = ctk.CTkProgressBar(
            dialog, mode="indeterminate"
        )
        self._update_progress_bar.pack(fill="x", padx=24)
        self._update_progress_bar.start()
        self._update_progress_indeterminate = True

        progress_text = ctk.CTkFrame(dialog, fg_color="transparent")
        progress_text.pack(fill="x", padx=24, pady=(8, 18))
        self._update_progress_detail_label = ctk.CTkLabel(
            progress_text, text="0.0 МБ", anchor="w"
        )
        self._update_progress_detail_label.pack(side="left")
        self._update_progress_percent_label = ctk.CTkLabel(
            progress_text, text="", anchor="e"
        )
        self._update_progress_percent_label.pack(side="right")

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(0, 20))
        self._update_progress_action_button = ctk.CTkButton(
            actions, text="", width=210
        )
        self._update_progress_action_button.pack(side="left")
        self._update_progress_action_button.pack_forget()
        self._update_progress_close_button = ctk.CTkButton(
            actions,
            text="Закрыть",
            width=100,
            command=self._close_update_progress_dialog,
        )
        self._update_progress_close_button.pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", self._close_update_progress_dialog)

    def _close_update_progress_dialog(self) -> None:
        dialog = self.__dict__.get("_update_progress_dialog")
        self._update_progress_dialog = None
        self._update_progress_status_label = None
        self._update_progress_bar = None
        self._update_progress_percent_label = None
        self._update_progress_detail_label = None
        self._update_progress_action_button = None
        self._update_progress_close_button = None
        self._update_progress_indeterminate = False
        if not self.__dict__.get("_update_check_in_progress", False):
            self._pending_installer_path = None
            self._pending_update_release = None
        if dialog is not None:
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

    def _start_update_download(self, release) -> None:
        self._active_update_release = release
        self._update_check_in_progress = True
        self._set_update_button("Скачивание…", enabled=False)
        if self._progress_dialog_alive():
            self._update_progress_status_label.configure(
                text="Скачивание обновления...", text_color=("gray10", "gray90")
            )
            self._update_progress_detail_label.configure(text="0.0 МБ")
            self._update_progress_percent_label.configure(text="")
            self._update_progress_bar.configure(mode="indeterminate")
            self._update_progress_bar.start()
            self._update_progress_indeterminate = True
            self._update_progress_action_button.pack_forget()

        try:
            threading.Thread(
                target=lambda r=release: self._download_update_worker(r),
                name="autoprint-update-download",
                daemon=True,
            ).start()
        except Exception as exc:
            self._update_download_failed(exc)

    def _download_update_worker(self, release) -> None:
        def progress(downloaded: int, total: int | None) -> None:
            self._post_update_ui(
                lambda d=downloaded, t=total: self._update_download_progress(d, t)
            )

        try:
            installer = download_release(release, progress=progress)
        except Exception as exc:
            self._post_update_ui(lambda e=exc: self._update_download_failed(e))
            return
        self._post_update_ui(
            lambda p=installer, r=release: self._download_verified(p, r)
        )

    def _update_download_progress(
        self, downloaded: int, total: int | None
    ) -> None:
        if not self._progress_dialog_alive():
            return
        fraction, percentage, detail = format_download_progress(downloaded, total)
        self._update_progress_detail_label.configure(text=detail)
        self._update_progress_percent_label.configure(text=percentage)
        if fraction is None:
            self._update_download_total_known = False
            self._update_progress_bar.configure(mode="indeterminate")
            if not self._update_progress_indeterminate:
                self._update_progress_bar.start()
                self._update_progress_indeterminate = True
            self._update_progress_status_label.configure(
                text="Скачивание обновления..."
            )
            return

        self._update_download_total_known = True
        if self._update_progress_indeterminate:
            self._update_progress_bar.stop()
            self._update_progress_indeterminate = False
        self._update_progress_bar.configure(mode="determinate")
        self._update_progress_bar.set(fraction)
        self._update_progress_status_label.configure(
            text=(
                "Проверка целостности обновления..."
                if fraction >= 1.0
                else "Скачивание обновления..."
            )
        )

    def _download_verified(self, installer_path, release) -> None:
        if self._progress_dialog_alive():
            self._update_progress_status_label.configure(
                text="Проверка целостности обновления..."
            )
            if self._update_progress_indeterminate:
                self._update_progress_bar.stop()
                self._update_progress_indeterminate = False
        try:
            self.after_idle(
                lambda p=installer_path, r=release: self._update_download_ready(p, r)
            )
        except Exception:
            self._update_download_ready(installer_path, release)

    def _update_download_ready(self, installer_path, release) -> None:
        self._update_check_in_progress = False
        self._set_update_button("Обновить", enabled=True)
        if not self._progress_dialog_alive():
            self._pending_installer_path = None
            self._pending_update_release = None
            return

        self._pending_installer_path = installer_path
        self._pending_update_release = release
        self._update_progress_bar.stop()
        self._update_progress_indeterminate = False
        if self._update_download_total_known:
            self._update_progress_bar.configure(mode="determinate")
            self._update_progress_bar.set(1.0)
            self._update_progress_percent_label.configure(text="100%")
        self._update_progress_status_label.configure(
            text="Обновление готово к установке."
        )
        self._update_progress_action_button.configure(
            text="Перезапустить и обновить",
            command=self._restart_and_update,
            state="normal",
        )
        self._update_progress_action_button.pack(side="left")

    def _update_download_failed(self, exc: Exception) -> None:
        self._update_check_in_progress = False
        self._set_update_button("Обновить", enabled=True)
        message = str(exc) if isinstance(exc, UpdateError) else f"Ошибка загрузки обновления: {exc}"
        try:
            self.logger.err(message)
        except Exception:
            pass
        self._pending_installer_path = None
        self._pending_update_release = None
        if not self._progress_dialog_alive():
            return
        try:
            self._update_progress_bar.stop()
            self._update_progress_indeterminate = False
            self._update_progress_status_label.configure(
                text=message, text_color=("#b42318", "#ff8a80")
            )
            self._update_progress_detail_label.configure(
                text="Загрузка не завершена. Можно повторить попытку."
            )
            self._update_progress_percent_label.configure(text="")
            self._update_progress_action_button.configure(
                text="Повторить",
                command=self._retry_update_download,
                state="normal",
            )
            self._update_progress_action_button.pack(side="left")
        except Exception:
            pass

    def _retry_update_download(self) -> None:
        release = self.__dict__.get("_active_update_release")
        if release is not None:
            self._start_update_download(release)

    def _restart_and_update(self) -> None:
        installer_path = self.__dict__.get("_pending_installer_path")
        release = self.__dict__.get("_pending_update_release")
        if installer_path is None or release is None:
            return
        if self._progress_dialog_alive():
            self._update_progress_action_button.configure(state="disabled")
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
    if license_check_required() and ensure_license_or_exit() is None:
        return 1
    app = AutoPrintApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())