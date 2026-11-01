"""Regression coverage for the actual base bartender_gui.App launcher.

Closed #56/PR #57 fixed the autoprint_main.AutoPrintApp subclass, but an
isolated developer source-run instantiates bartender_gui.App directly.
Auto preview resolves internal BTW IDs and must not disable part input.
"""
import unittest
from unittest.mock import Mock, call

import bartender_gui


class BasePartEntryAutoStateTests(unittest.TestCase):
    def make_app(self, selected="Auto"):
        app = Mock()
        app.format_combo.get.return_value = selected
        app._template_requires_manual_part.side_effect = (
            lambda name: name == "30x20"
        )
        app._apply_format_ui.side_effect = (
            lambda fmt, **kwargs: bartender_gui.App._apply_format_ui(
                app, fmt, **kwargs
            )
        )
        return app

    def test_auto_resolved_blank_then_manual_remains_editable(self):
        app = self.make_app("Auto")
        bartender_gui.App._apply_format_ui(app, "16x16")
        bartender_gui.App._apply_format_ui(app, "30x20")

        self.assertEqual(
            app.part_entry.configure.call_args_list,
            [call(state="normal"), call(state="normal")],
        )
        app.part_entry.delete.assert_not_called()
        app._template_requires_manual_part.assert_not_called()
        self.assertEqual(
            app._set_default_printer_for_format.call_args_list,
            [call("16x16"), call("30x20")],
        )

    def test_auto_preview_repair_preserves_existing_part_number(self):
        app = self.make_app("Auto")
        app.part_entry.get.return_value = "622"
        bartender_gui.App._apply_format_ui(app, "16x16")
        self.assertEqual(
            app.part_entry.configure.call_args_list,
            [call(state="normal")],
        )
        app.part_entry.delete.assert_not_called()

    def test_explicit_blank_disables_despite_stale_auto_combo(self):
        app = self.make_app("Auto")
        bartender_gui.App._on_format_change(app, "16x16")
        app._apply_format_ui.assert_called_once_with(
            "16x16", selected_fmt="16x16"
        )
        app.part_entry.configure.assert_called_once_with(state="disabled")
        app.part_entry.delete.assert_called_once_with(0, "end")
        app._set_default_printer_for_format.assert_called_once_with("16x16")
        app._refresh_runtime_status.assert_called_once()
        app._schedule_live_preview.assert_called_once()

    def test_explicit_manual_stays_editable_despite_stale_auto_combo(self):
        app = self.make_app("Auto")
        bartender_gui.App._on_format_change(app, "30x20")
        app.part_entry.configure.assert_called_once_with(state="normal")
        app.part_entry.delete.assert_not_called()
        app._template_requires_manual_part.assert_called_once_with("30x20")

    def test_explicit_manual_blank_manual_and_auto_transitions(self):
        app = self.make_app("Auto")
        for selected in ("30x20", "16x16", "30x20", "Auto"):
            bartender_gui.App._on_format_change(app, selected)
        self.assertEqual(
            app.part_entry.configure.call_args_list,
            [
                call(state="normal"),
                call(state="disabled"),
                call(state="normal"),
                call(state="normal"),
            ],
        )
        app.part_entry.delete.assert_called_once_with(0, "end")
        self.assertEqual(
            app._set_default_printer_for_format.call_args_list,
            [call("30x20"), call("16x16"), call("30x20"), call("Auto")],
        )

    def test_template_editor_reapply_auto_restores_editability(self):
        app = self.make_app("Auto")
        app.part_entry.configure(state="disabled")
        app.part_entry.configure.reset_mock()

        bartender_gui.App._apply_format_ui(app, "Auto")

        app.part_entry.configure.assert_called_once_with(state="normal")
        app.part_entry.delete.assert_not_called()

    def test_preview_resolved_printer_separate_from_manual_mode_policy(self):
        app = self.make_app("30x20")
        bartender_gui.App._apply_format_ui(app, "16x16")

        app.part_entry.configure.assert_called_once_with(state="normal")
        app._template_requires_manual_part.assert_called_once_with("30x20")
        app._set_default_printer_for_format.assert_called_once_with("16x16")


if __name__ == "__main__":
    unittest.main()
