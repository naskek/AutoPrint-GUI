"""UI feedback when a selected label template discards manually entered PART_NUM."""
import unittest
from unittest.mock import Mock, call

import bartender_gui
import autoprint_main


class PartUsageHintTests(unittest.TestCase):
    def make_app(self, selected="Auto", value=""):
        app = Mock()
        app.format_combo.get.return_value = selected
        app.part_entry.get.return_value = value
        app._normalize_template_id.side_effect = lambda template: (template or "").strip()
        app._template_part_mode.side_effect = (
            lambda template: "manual" if template == "30x20" else "blank"
        )
        app.part_usage_hint = Mock()
        app.part_usage_hint.winfo_manager.return_value = ""
        app._update_part_usage_hint.side_effect = (
            lambda resolved_fmt=None: bartender_gui.App._update_part_usage_hint(
                app, resolved_fmt
            )
        )
        return app

    def test_auto_with_blank_template_explains_disabled_lot_before_entry(self):
        app = self.make_app(value="")
        bartender_gui.App._update_part_usage_hint(app, "16x16")
        args = app.part_usage_hint.configure.call_args.kwargs
        self.assertIn("16x16", args["text"])
        self.assertIn("отключён", args["text"])
        app.part_usage_hint.pack.assert_called_once_with(
            anchor="w", pady=(3, 0)
        )

    def test_auto_with_entered_lot_warns_it_will_not_print(self):
        app = self.make_app(value="555")
        bartender_gui.App._update_part_usage_hint(app, "16x16")
        txt = app.part_usage_hint.configure.call_args.kwargs["text"]
        self.assertNotIn("555", txt)
        self.assertIn("не попадёт на этикетку", txt)
        self.assertIn("16x16", txt)
        self.assertEqual(app.part_entry.get.call_count, 1)

    def test_auto_switch_to_manual_hides_warning_without_erasing_lot(self):
        app = self.make_app(value="555")
        bartender_gui.App._update_part_usage_hint(app, "16x16")
        app.part_usage_hint.reset_mock()
        bartender_gui.App._update_part_usage_hint(app, "30x20")
        app.part_usage_hint.pack_forget.assert_called_once()
        app.part_usage_hint.configure.assert_not_called()
        app.part_entry.delete.assert_not_called()

    def test_explicit_blank_template_describes_reason_disabled(self):
        app = self.make_app(selected="16x16", value="")
        bartender_gui.App._update_part_usage_hint(app, "16x16")
        txt = app.part_usage_hint.configure.call_args.kwargs["text"]
        self.assertIn("номер партии отключён", txt)

    def test_auto_with_no_resolved_template_does_not_guess(self):
        app = self.make_app()
        bartender_gui.App._update_part_usage_hint(app)
        app.part_usage_hint.pack_forget.assert_called_once()
        app.part_usage_hint.configure.assert_not_called()

    def test_part_entry_keypress_binding_and_source_row_update_are_wired(self):
        from pathlib import Path
        tree = (Path(__file__).resolve().parents[1] / "bartender_gui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'self.part_entry.bind("<KeyRelease>", '
            'lambda _e: self._update_part_usage_hint(), add="+")',
            tree
        )
        self.assertIn("self._update_part_usage_hint(template_id)", tree)

    def test_production_subclass_reuses_same_hint_logic(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "autoprint_main.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self._update_part_usage_hint(resolved_fmt)", text)
        self.assertTrue(issubclass(autoprint_main.AutoPrintApp, bartender_gui.App))


if __name__ == "__main__":
    unittest.main()
