import unittest
from unittest.mock import Mock, patch

import bartender_gui
from printer_status import (
    CONNECTION_NETWORK,
    CONNECTION_TCP,
    PrinterHealth,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)


class PrinterHealthUiTests(unittest.TestCase):
    def _make_app(self, selected_printer="TSC Network"):
        app = object.__new__(bartender_gui.App)
        app.logger = Mock()
        app.prn_combo = Mock()
        app.prn_combo.get.return_value = selected_printer
        app._printer_health_last = None
        return app

    @patch("bartender_gui.mb.showerror")
    @patch("bartender_gui.check_printer")
    def test_unavailable_preflight_blocks_before_submit(self, check_printer, showerror):
        app = self._make_app()
        check_printer.return_value = PrinterHealth(
            printer_name="TSC Network",
            state=STATE_UNAVAILABLE,
            connection=CONNECTION_TCP,
            detail="TCP 192.168.1.38:9100 недоступен",
            port_name="TSC-Port",
            host="192.168.1.38",
            tcp_port=9100,
        )

        allowed = app._ensure_printer_available_for_print(
            "TSC Network",
            "Печать",
        )

        self.assertFalse(allowed)
        check_printer.assert_called_once_with(
            "TSC Network",
            timeout=bartender_gui.PRINTER_HEALTH_TIMEOUT_SEC,
        )
        showerror.assert_called_once()
        self.assertIn("Печать не отправлена", showerror.call_args.args[1])
        self.assertEqual(app._printer_health_last.state, STATE_UNAVAILABLE)

    @patch("bartender_gui.mb.showerror")
    @patch("bartender_gui.check_printer")
    def test_unknown_preflight_warns_in_log_but_allows_print(self, check_printer, showerror):
        app = self._make_app()
        check_printer.return_value = PrinterHealth(
            printer_name="TSC Network",
            state=STATE_UNKNOWN,
            connection=CONNECTION_NETWORK,
            detail="endpoint сетевого порта не определён",
            port_name="WSD-1234",
        )

        allowed = app._ensure_printer_available_for_print(
            "TSC Network",
            "Печать",
        )

        self.assertTrue(allowed)
        showerror.assert_not_called()
        self.assertTrue(app.logger.log.called)

    def test_printer_change_refreshes_status_and_starts_immediate_probe(self):
        app = self._make_app()
        app._refresh_runtime_status = Mock()
        app._schedule_printer_health_check = Mock()

        app._on_printer_change("TSC Network")

        app._refresh_runtime_status.assert_called_once_with()
        app._schedule_printer_health_check.assert_called_once_with(immediate=True)

    def test_sticker_print_stops_before_opening_bartender_when_printer_is_unavailable(self):
        app = self._make_app("Main Printer")
        app._require_print_capabilities = Mock(return_value=True)
        app._get_sticker_printer = Mock(return_value="Sticker Printer")
        app._ensure_printer_available_for_print = Mock(return_value=False)
        app.bt = Mock()

        app._print_sticker_placeholder(1, 1, 10, 10)

        app._ensure_printer_available_for_print.assert_called_once_with(
            "Sticker Printer",
            "Печать стикера",
        )
        app.bt.open_format.assert_not_called()

    def test_main_print_stops_before_sticker_or_bartender_work_when_printer_is_unavailable(self):
        app = self._make_app("Main Printer")
        app._require_print_capabilities = Mock(return_value=True)
        app.csv_path = "input.csv"
        app.csv_rows = [{"DM": "code"}]
        app.format_combo = Mock()
        app.format_combo.get.return_value = "16x16"
        app.part_entry = Mock()
        app._template_requires_manual_part = Mock(return_value=False)
        app._resolve_print_selection_for_action = Mock(return_value=(1, 1, 1))
        app._get_printer = Mock(return_value="Main Printer")
        app._ensure_printer_available_for_print = Mock(return_value=False)
        app._preprint_stickers_for_all_batches = Mock()
        app._catalog_print_active = 0
        app._set_print_active_ui = Mock()

        app._print_range_one_job_via_csv()

        app._ensure_printer_available_for_print.assert_called_once_with(
            "Main Printer",
            "Печать",
        )
        app._preprint_stickers_for_all_batches.assert_not_called()
        app._set_print_active_ui.assert_not_called()


if __name__ == "__main__":
    unittest.main()
