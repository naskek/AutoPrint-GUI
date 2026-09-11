from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autoprint_main
from build_tools.build_exe import _write_windows_version_file
from updater import UpdateError, is_newer_version, parse_version


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

    @patch.object(autoprint_main.AutoPrintApp, "_install_downloaded_update")
    @patch("autoprint_main.download_release", return_value=Path("setup.exe"))
    @patch("autoprint_main.mb.askyesno", return_value=True)
    @patch("autoprint_main.threading.Thread", ImmediateThread)
    @patch("autoprint_main.check_for_update")
    def test_startup_release_uses_existing_prompt_and_download_pipeline(
        self, check, askyesno, download, install
    ):
        release = SimpleNamespace(version="3.2.1", notes="Changes")
        check.return_value = release
        app = self._app()

        app._start_update_check(interactive=False)

        askyesno.assert_called_once()
        download.assert_called_once_with(release)
        install.assert_called_once_with(Path("setup.exe"), release)
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


if __name__ == "__main__":
    unittest.main()
