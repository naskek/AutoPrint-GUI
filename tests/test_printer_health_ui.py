import unittest
from unittest.mock import Mock, patch

import bartender_gui
from printer_status import (
    CONNECTION_NETWORK,
    CONNECTION_USB,
    CONNECTION_TCP,
    PrinterHealth,
    STATE_AVAILABLE,
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



class PrinterMonitorStableWidgetsTests(unittest.TestCase):
    """A health poll must not destroy and repaint the whole printer panel."""

    def setUp(self):
        self.app = object.__new__(bartender_gui.App)
        self.app.__dict__["printer_monitor_frame"] = Mock()
        self.app.__dict__["printer_monitor_empty_label"] = Mock()
        self.app.__dict__["_printer_monitor_empty_visible"] = True
        self.app.__dict__["_printer_monitor_rows"] = {}
        self.make_row = patch("bartender_gui.ctk.CTkFrame")
        self.make_label = patch("bartender_gui.ctk.CTkLabel")
        self.frame_factory = self.make_row.start()
        self.label_factory = self.make_label.start()
        self.addCleanup(self.make_row.stop)
        self.addCleanup(self.make_label.stop)
        self.frame_factory.side_effect = lambda *a, **kw: Mock()
        self.label_factory.side_effect = lambda *a, **kw: Mock()

    def health(self, name="TSC Network", state=STATE_UNAVAILABLE,
               connection=CONNECTION_TCP):
        return PrinterHealth(
            printer_name=name,
            state=state,
            connection=connection,
            detail="offline" if state == STATE_UNAVAILABLE else "",
        )

    def test_identical_polls_do_not_recreate_rows_or_reconfigure_labels(self):
        health = [
            self.health("TSC Network"),
            self.health("USB", STATE_AVAILABLE, CONNECTION_USB),
        ]
        self.app._apply_printer_monitor(health)
        widgets = dict(self.app._printer_monitor_rows)
        self.assertEqual(len(widgets), 2)
        self.assertEqual(self.frame_factory.call_count, 2)
        self.assertEqual(self.label_factory.call_count, 6)
        self.app.printer_monitor_empty_label.grid_remove.assert_called_once()
        for item in widgets.values():
            item["led"].configure.reset_mock()
            item["protocol"].configure.reset_mock()
            item["frame"].grid_configure.reset_mock()

        for _ in range(6):
            self.app._apply_printer_monitor(list(reversed(health)))

        self.assertEqual(self.frame_factory.call_count, 2)
        self.assertEqual(self.label_factory.call_count, 6)
        self.app.printer_monitor_empty_label.grid_remove.assert_called_once()
        for name, item in widgets.items():
            self.assertIs(self.app._printer_monitor_rows[name], item)
            item["frame"].destroy.assert_not_called()
            item["frame"].grid_configure.assert_not_called()
            item["led"].configure.assert_not_called()
            item["protocol"].configure.assert_not_called()

    def test_only_led_and_protocol_change_when_status_changes(self):
        self.app._apply_printer_monitor([self.health()])
        original = self.app._printer_monitor_rows["TSC Network"]
        self.app._apply_printer_monitor([
            self.health(state=STATE_AVAILABLE)
        ])
        self.assertIs(self.app._printer_monitor_rows["TSC Network"], original)
        original["led"].configure.assert_called_once_with(
            text_color="#4CAF50"
        )
        original["protocol"].configure.assert_not_called()
        self.app._apply_printer_monitor([
            self.health(state=STATE_AVAILABLE, connection=CONNECTION_USB)
        ])
        original["led"].configure.assert_called_once()
        original["protocol"].configure.assert_called_once_with(text="USB")
        self.assertEqual(self.frame_factory.call_count, 1)
        self.assertEqual(self.label_factory.call_count, 3)
        original["frame"].destroy.assert_not_called()

    def test_membership_change_only_removes_missing_rows_and_reuses_empty_label(self):
        self.app._apply_printer_monitor([])
        self.app.printer_monitor_empty_label.grid.assert_not_called()
        self.app._apply_printer_monitor([self.health("Printer Z")])
        saved = self.app._printer_monitor_rows["Printer Z"]
        self.app._apply_printer_monitor([
            self.health("Printer A"),
            self.health("Printer Z"),
        ])
        self.assertIs(self.app._printer_monitor_rows["Printer Z"], saved)
        saved["frame"].grid_configure.assert_called_once_with(row=1)
        saved["frame"].destroy.assert_not_called()
        self.app._apply_printer_monitor([self.health("Printer A")])
        saved["frame"].destroy.assert_called_once()
        self.app._apply_printer_monitor([])
        self.app.printer_monitor_empty_label.grid.assert_called_once()
        self.app._apply_printer_monitor([])
        self.app.printer_monitor_empty_label.grid.assert_called_once()
        self.assertEqual(self.frame_factory.call_count, 2)

    def test_no_frame_still_records_latest_health_without_tk_touch(self):
        del self.app.__dict__["printer_monitor_frame"]
        health = self.health()
        self.app._apply_printer_monitor([health])
        self.assertEqual(self.app._printer_monitor_health, [health])
        self.frame_factory.assert_not_called()
        self.label_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
