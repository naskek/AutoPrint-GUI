from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autoprint_main
from build_tools.build_exe import _write_windows_version_file
from updater import (
    UpdateError,
    _select_installer_asset,
    is_newer_version,
    parse_version,
)


class VersionTests(unittest.TestCase):
    def test_parse_semver(self):
        self.assertEqual((3, 2, 0, 0), parse_version("3.2.0"))
        self.assertEqual((3, 2, 1, 0), parse_version("v3.2.1"))

    def test_version_comparison(self):
        self.assertTrue(is_newer_version("3.2.1", "3.2.0"))
        self.assertTrue(is_newer_version("3.3.0", "3.2.9"))
        self.assertFalse(is_newer_version("3.2.0", "3.2.0"))
        self.assertFalse(is_newer_version("3.1.9", "3.2.0"))

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_version("3.2")
        with self.assertRaises(ValueError):
            parse_version("latest")

    def test_windows_version_resource_comes_from_app_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_windows_version_file(Path(tmp), "3.2.0")
            text = path.read_text(encoding="utf-8")
            self.assertIn("filevers=(3, 2, 0, 0)", text)
            self.assertIn("ProductVersion', '3.2.0'", text)


class InstallerAssetSelectionTests(unittest.TestCase):
    def test_autoprint_installer_is_selected(self):
        asset = {"name": "AutoPrint_Setup_3.2.2.exe"}

        self.assertIs(_select_installer_asset([asset]), asset)

    def test_legacy_bartendergui_installer_is_still_accepted(self):
        asset = {"name": "BarTenderGUI_Setup_3.2.2.exe"}

        self.assertIs(_select_installer_asset([asset]), asset)

    def test_autoprint_installer_wins_when_both_are_present(self):
        legacy = {"name": "BarTenderGUI_Setup_3.2.2.exe"}
        current = {"name": "AutoPrint_Setup_3.2.2.exe"}

        self.assertIs(_select_installer_asset([legacy, current]), current)


class ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class StartupUpdateCheckTests(unittest.TestCase):
    def _app(self):
        app = object.__new__(autoprint_main.AutoPrintApp)
        app._version_update_btn = None
        app._update_check_in_progress = False
        app._startup_update_check_started = False
        app._startup_update_prompted = False
        app._shutdown_started = False
        app.logger = Mock()
        app._post_update_ui = lambda callback: callback()
        return app

    def test_startup_check_is_enabled_only_for_frozen_build(self):
        self.assertTrue(autoprint_main.startup_update_check_enabled(True))
        self.assertFalse(autoprint_main.startup_update_check_enabled(False))

    @patch("autoprint_main.startup_update_check_enabled", return_value=False)
    def test_source_run_does_not_schedule_startup_check(self, _enabled):
        app = self._app()
        app.after = Mock()

        self.assertFalse(app._schedule_startup_update_check())
        app.after.assert_not_called()

    @patch("autoprint_main.startup_update_check_enabled", return_value=True)
    def test_frozen_run_schedules_delayed_startup_check(self, _enabled):
        app = self._app()
        app.after = Mock()

        self.assertTrue(app._schedule_startup_update_check())
        app.after.assert_called_once_with(
            autoprint_main.STARTUP_UPDATE_DELAY_MS,
            app._run_startup_update_check,
        )

    @patch("autoprint_main.mb.showerror")
    @patch("autoprint_main.mb.showinfo")
    @patch("autoprint_main.threading.Thread", ImmediateThread)
    @patch("autoprint_main.check_for_update", return_value=None)
    def test_startup_current_version_is_silent(
        self, _check, showinfo, showerror
    ):
        app = self._app()

        app._start_update_check(interactive=False)

        showinfo.assert_not_called()
        showerror.assert_not_called()
        self.assertFalse(app._update_check_in_progress)

    @patch("autoprint_main.mb.showerror")
    @patch("autoprint_main.mb.showinfo")
    @patch("autoprint_main.threading.Thread", ImmediateThread)
    @patch(
        "autoprint_main.check_for_update",
        side_effect=UpdateError("GitHub unavailable"),
    )
    def test_startup_update_error_is_silent(
        self, _check, showinfo, showerror
    ):
        app = self._app()

        app._start_update_check(interactive=False)

        showinfo.assert_not_called()
        showerror.assert_not_called()
        self.assertFalse(app._update_check_in_progress)
        app.logger.log.assert_called_once()

    @patch("autoprint_main.mb.showerror")
    @patch("autoprint_main.mb.showinfo")
    @patch("autoprint_main.threading.Thread", ImmediateThread)
    @patch("autoprint_main.check_for_update", return_value=None)
    def test_manual_current_version_still_shows_info(
        self, _check, showinfo, showerror
    ):
        app = self._app()

        app._start_update_check(interactive=True)

        showinfo.assert_called_once()
        showerror.assert_not_called()
        self.assertFalse(app._update_check_in_progress)

    @patch("autoprint_main.mb.showerror")
    @patch("autoprint_main.threading.Thread", ImmediateThread)
    @patch(
        "autoprint_main.check_for_update",
        side_effect=UpdateError("GitHub unavailable"),
    )
    def test_manual_update_error_still_shows_error(self, _check, showerror):
        app = self._app()

        app._start_update_check(interactive=True)

        showerror.assert_called_once()
        self.assertFalse(app._update_check_in_progress)

    @patch.object(autoprint_main.AutoPrintApp, "_start_update_download")
    @patch.object(autoprint_main.AutoPrintApp, "_open_update_progress_dialog")
    @patch("autoprint_main.mb.askyesno", return_value=True)
    @patch("autoprint_main.threading.Thread", ImmediateThread)
    @patch("autoprint_main.check_for_update")
    def test_startup_release_uses_existing_prompt_and_opens_progress_flow(
        self, check, askyesno, open_progress, start_download
    ):
        release = SimpleNamespace(version="3.2.1", notes="Changes")
        check.return_value = release
        app = self._app()

        app._start_update_check(interactive=False)

        askyesno.assert_called_once()
        open_progress.assert_called_once_with(release)
        start_download.assert_called_once_with(release)
        self.assertTrue(app._startup_update_prompted)

    @patch("autoprint_main.threading.Thread")
    def test_in_progress_guard_prevents_parallel_checks(self, thread_class):
        app = self._app()

        self.assertTrue(app._start_update_check(interactive=False))
        self.assertFalse(app._start_update_check(interactive=True))

        thread_class.assert_called_once()
        thread_class.return_value.start.assert_called_once_with()

    def test_guard_is_reset_by_success_and_error_handlers(self):
        app = self._app()
        app._update_check_in_progress = True

        with patch("autoprint_main.mb.showinfo"):
            app._update_check_finished(None, interactive=True)
        self.assertFalse(app._update_check_in_progress)

        app._update_check_in_progress = True
        with patch("autoprint_main.mb.showerror"):
            app._update_check_failed(UpdateError("failed"), interactive=True)
        self.assertFalse(app._update_check_in_progress)


class UpdateProgressFlowTests(unittest.TestCase):
    def _app(self):
        app = object.__new__(autoprint_main.AutoPrintApp)
        app._update_progress_dialog = Mock()
        app._update_progress_dialog.winfo_exists.return_value = True
        app._update_progress_status_label = Mock()
        app._update_progress_bar = Mock()
        app._update_progress_percent_label = Mock()
        app._update_progress_detail_label = Mock()
        app._update_progress_action_button = Mock()
        app._update_progress_close_button = Mock()
        app._update_progress_indeterminate = False
        app._update_download_total_known = False
        app._pending_installer_path = None
        app._pending_update_release = None
        app._update_check_in_progress = True
        app._set_update_button = Mock()
        app._shutdown_app = Mock()
        app.after = Mock()
        app.after_idle = lambda callback: callback()
        app.logger = Mock()
        return app

    def test_determinate_progress_formats_percentage_and_megabytes(self):
        self.assertEqual(
            autoprint_main.format_download_progress(5 * 1024 * 1024, 10 * 1024 * 1024),
            (0.5, "50%", "5.0 МБ / 10.0 МБ"),
        )

    def test_unknown_total_uses_downloaded_megabytes_without_percentage(self):
        self.assertEqual(
            autoprint_main.format_download_progress(5 * 1024 * 1024, None),
            (None, "", "5.0 МБ"),
        )

        app = self._app()
        app._update_download_progress(5 * 1024 * 1024, None)

        app._update_progress_bar.configure.assert_called_with(mode="indeterminate")
        app._update_progress_bar.start.assert_called_once_with()
        app._update_progress_detail_label.configure.assert_called_with(text="5.0 МБ")
        app._update_progress_percent_label.configure.assert_called_with(text="")

    def test_verified_download_passes_through_integrity_check_state(self):
        app = self._app()
        app.after_idle = Mock()
        installer = Path("setup.exe")
        release = SimpleNamespace(version="3.2.2")

        app._download_verified(installer, release)

        app._update_progress_status_label.configure.assert_called_with(
            text="Проверка целостности обновления..."
        )
        ready_callback = app.after_idle.call_args.args[0]
        ready_callback()
        self.assertEqual(app._pending_installer_path, installer)

    @patch("autoprint_main.download_release")
    def test_progress_callback_posts_ui_updates_to_queue(self, download):
        release = SimpleNamespace(version="3.2.2")
        installer = Path("setup.exe")

        def download_with_progress(_release, progress):
            progress(5, 10)
            return installer

        download.side_effect = download_with_progress
        app = self._app()
        app._post_update_ui = Mock()
        app._update_download_progress = Mock()
        app._download_verified = Mock()

        app._download_update_worker(release)

        self.assertEqual(app._post_update_ui.call_count, 2)
        app._post_update_ui.call_args_list[0].args[0]()
        app._update_download_progress.assert_called_once_with(5, 10)
        app._post_update_ui.call_args_list[1].args[0]()
        app._download_verified.assert_called_once_with(installer, release)

    @patch("autoprint_main.launch_installer")
    def test_success_becomes_ready_without_launching_installer(self, launch):
        app = self._app()
        installer = Path("setup.exe")
        release = SimpleNamespace(version="3.2.2")

        app._update_download_ready(installer, release)

        launch.assert_not_called()
        self.assertEqual(app._pending_installer_path, installer)
        app._update_progress_status_label.configure.assert_called_with(
            text="Обновление готово к установке."
        )
        self.assertEqual(
            app._update_progress_action_button.configure.call_args.kwargs["text"],
            "Перезапустить и обновить",
        )

    @patch("autoprint_main.launch_installer")
    def test_installer_launch_requires_explicit_restart_action(self, launch):
        app = self._app()
        installer = Path("setup.exe")
        release = SimpleNamespace(version="3.2.2")
        app._update_download_ready(installer, release)

        app._restart_and_update()

        launch.assert_called_once_with(installer)
        app.after.assert_called_once_with(50, app._shutdown_app)

    @patch("autoprint_main.launch_installer")
    def test_download_error_leaves_application_running(self, launch):
        app = self._app()

        app._update_download_failed(UpdateError("network failed"))

        launch.assert_not_called()
        app._shutdown_app.assert_not_called()
        app._set_update_button.assert_called_with("Обновить", enabled=True)
        app._update_progress_status_label.configure.assert_called_with(
            text="network failed", text_color=("#b42318", "#ff8a80")
        )

    def test_destroyed_progress_dialog_ignores_late_worker_updates(self):
        app = self._app()
        app._update_progress_dialog.winfo_exists.return_value = False

        app._update_download_progress(5, 10)

        app._update_progress_bar.configure.assert_not_called()
        app._update_progress_status_label.configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
