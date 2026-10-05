import unittest
from unittest.mock import Mock, call

import autoprint_main


class PartEntryStateTests(unittest.TestCase):
    def _make_app(self, selected="Auto"):
        app = object.__new__(autoprint_main.AutoPrintApp)
        app.format_combo = Mock()
        app.format_combo.get.return_value = selected
        app.part_entry = Mock()
        app._template_requires_manual_part = Mock(
            side_effect=lambda template_id: template_id == "30x20"
        )
        app._set_default_printer_for_format = Mock()
        app._refresh_runtime_status = Mock()
        app._schedule_live_preview = Mock()
        return app

    def test_part_validator_accepts_placeholder_empty_and_digits_only(self):
        self.assertTrue(
            autoprint_main.valid_part_entry_value(
                autoprint_main.PART_ENTRY_PLACEHOLDER
            )
        )
        self.assertTrue(autoprint_main.valid_part_entry_value(""))
        self.assertTrue(autoprint_main.valid_part_entry_value("123456"))
        self.assertFalse(autoprint_main.valid_part_entry_value("123A"))
        self.assertFalse(autoprint_main.valid_part_entry_value("Номер"))

    def test_part_validation_installs_ctk_compatible_validator(self):
        app = self._make_app()
        app.register = Mock(return_value="part-validator")

        app._configure_part_entry_validation()

        app.register.assert_called_once_with(autoprint_main.valid_part_entry_value)
        app.part_entry.configure.assert_called_once_with(
            validate="key",
            validatecommand=("part-validator", "%P"),
        )

    def test_auto_mode_resolved_blank_template_keeps_part_entry_editable(self):
        app = self._make_app("Auto")

        app._apply_format_ui("16x16")

        app.part_entry.configure.assert_called_once_with(state="normal")
        app.part_entry.delete.assert_not_called()
        app._set_default_printer_for_format.assert_called_once_with("16x16")

    def test_auto_mode_stays_editable_across_blank_and_manual_resolved_templates(self):
        app = self._make_app("Auto")

        app._apply_format_ui("16x16")
        app._apply_format_ui("30x20")

        self.assertEqual(
            app.part_entry.configure.call_args_list,
            [call(state="normal"), call(state="normal")],
        )
        app.part_entry.delete.assert_not_called()
        self.assertEqual(
            app._set_default_printer_for_format.call_args_list,
            [call("16x16"), call("30x20")],
        )

    def test_explicit_blank_template_disables_and_clears_part_entry(self):
        app = self._make_app("Auto")

        app._on_format_change("16x16")

        app.part_entry.configure.assert_called_once_with(state="disabled")
        app.part_entry.delete.assert_called_once_with(0, "end")
        app._set_default_printer_for_format.assert_called_once_with("16x16")
        app._refresh_runtime_status.assert_called_once_with()
        app._schedule_live_preview.assert_called_once_with()

    def test_explicit_manual_template_keeps_part_entry_editable(self):
        app = self._make_app("Auto")

        app._on_format_change("30x20")

        app.part_entry.configure.assert_called_once_with(state="normal")
        app.part_entry.delete.assert_not_called()
        app._set_default_printer_for_format.assert_called_once_with("30x20")


if __name__ == "__main__":
    unittest.main()
