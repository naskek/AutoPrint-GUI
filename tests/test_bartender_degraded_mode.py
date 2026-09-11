import unittest
from unittest.mock import Mock, patch

import customtkinter as ctk

import autoprint_main
import bartender_gui


class BarTenderDegradedModeTests(unittest.TestCase):
    def _make_app(self):
        app = object.__new__(bartender_gui.App)
        app.bt = None
        app.catalog_state = "ready"
        app.catalog_error = ""
        app.catalog_store = Mock()
        app.logger = Mock()
        app.preview_btn = Mock()
        app.print_btn = Mock()
        app.sticker_reprint_btn = Mock()
        app.cancel_btn = Mock()
        app.bt_status_label = Mock()
        return app

    @patch("bartender_gui.mb.showerror")
    @patch("bartender_com.Dispatch", side_effect=RuntimeError("COM class is unavailable"))
    def test_missing_com_keeps_gui_in_explicit_degraded_state(self, dispatch, showerror):
        app = self._make_app()

        app._start_bt()

        dispatch.assert_called_once_with("BarTender.Application")
        self.assertIsNone(app.bt)
        for button in (app.preview_btn, app.print_btn, app.sticker_reprint_btn):
            button.configure.assert_called_once_with(state="disabled")
        app.cancel_btn.configure.assert_not_called()
        status_calls = app.bt_status_label.configure.call_args_list
        self.assertTrue(
            any(call.kwargs.get("text") == "BarTender: недоступен" for call in status_calls)
        )
        showerror.assert_not_called()

    @patch("bartender_com.Dispatch")
    def test_successful_com_start_obeys_catalog_capability(self, dispatch):
        app = self._make_app()
        app.catalog_state = "error"
        com_app = dispatch.return_value

        app._start_bt()

        self.assertIs(app.bt.app, com_app)
        for button in (app.preview_btn, app.print_btn, app.sticker_reprint_btn):
            button.configure.assert_called_once_with(state="disabled")
        app.cancel_btn.configure.assert_not_called()
        status_calls = app.bt_status_label.configure.call_args_list
        self.assertTrue(
            any(call.kwargs.get("text") == "BarTender: доступен" for call in status_calls)
        )

    @patch("bartender_gui.mb.showwarning")
    def test_catalog_error_blocks_print_entry_points(self, showwarning):
        app = self._make_app()
        app.catalog_state = "error"
        app.catalog_error = "повреждена база каталога"
        app.bt = Mock(app=Mock())

        app._preview()
        app._print_range_one_job_via_csv()
        app._reprint_batch(1)

        self.assertEqual(showwarning.call_count, 3)
        for call in showwarning.call_args_list:
            self.assertEqual(call.args[0], "Каталог недоступен")
            self.assertIn("повреждена база каталога", call.args[1])

    @patch("bartender_gui.mb.showwarning")
    def test_bartender_actions_stop_safely_when_com_is_unavailable(self, showwarning):
        app = self._make_app()

        app._preview()
        app._print_range_one_job_via_csv()
        app._print_sticker_placeholder(1, 1, 1, 1)
        app._prepare_btw("template.btw", {}, "Printer")

        self.assertEqual(showwarning.call_count, 4)
        for call in showwarning.call_args_list:
            self.assertEqual(call.args[0], "BarTender недоступен")
            self.assertIn("перезапустите AutoPrint", call.args[1])

    def test_template_validation_reports_unavailable_bartender(self):
        app = self._make_app()

        with self.assertRaisesRegex(RuntimeError, "BarTender недоступен"):
            app._validate_template_file("template.btw")

    @patch.object(ctk.CTk, "destroy")
    def test_shutdown_stops_bartender_and_destroys_gui_once(self, destroy):
        app = self._make_app()
        app._shutdown_started = False
        bt = Mock()
        app.bt = bt

        app._shutdown_app()
        app._shutdown_app()

        self.assertIsNone(app.bt)
        bt.stop.assert_called_once_with()
        destroy.assert_called_once_with()

    @patch("autoprint_main.launch_installer")
    def test_updater_schedules_shared_shutdown_path(self, launch_installer):
        app = object.__new__(autoprint_main.AutoPrintApp)
        app.logger = Mock()
        app.after = Mock()
        release = Mock(version="3.2.1")

        app._install_downloaded_update("setup.exe", release)

        launch_installer.assert_called_once_with("setup.exe")
        app.after.assert_called_once_with(50, app._shutdown_app)


if __name__ == "__main__":
    unittest.main()
