import datetime as dt
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autoprint_main
import bartender_gui


class VersionMenuTests(unittest.TestCase):
    def test_runtime_icon_path_uses_pyinstaller_bundle_root(self):
        with patch.object(
            bartender_gui.sys, "_MEIPASS", r"C:\AutoPrint\_internal", create=True
        ):
            icon_path = bartender_gui.application_icon_path()

        self.assertEqual(
            icon_path,
            r"C:\AutoPrint\_internal\assets\icons\autoprint.ico",
        )

    def test_application_window_title_uses_autoprint_identity(self):
        self.assertEqual(
            bartender_gui.APP_TITLE,
            f"AutoPrint V{autoprint_main.APP_VERSION}",
        )

    def test_visible_top_level_menu_contract_is_task_oriented(self):
        self.assertEqual(
            bartender_gui.TOP_LEVEL_MENU_ORDER,
            ("Каталог", "Шаблоны", "Работа с кодами"),
        )
        for legacy_name in ("Вид", "Сервис", "Помощь"):
            self.assertNotIn(legacy_name, bartender_gui.TOP_LEVEL_MENU_ORDER)

    def test_codes_menu_remains_top_level_with_required_item_order(self):
        self.assertEqual(
            bartender_gui.TOP_LEVEL_MENU_ORDER[-1], "Работа с кодами"
        )
        self.assertEqual(
            bartender_gui.CODES_MENU_ORDER,
            ("Проверка кодов", "Отчёт о вводе в оборот"),
        )

    def test_version_identity_uses_runtime_version_and_existing_callback(self):
        app = object.__new__(bartender_gui.App)
        app._open_version_dialog = Mock()

        app._open_version_identity()

        self.assertEqual(
            bartender_gui.version_identity_text(),
            f"AutoPrint {bartender_gui.APP_VERSION}",
        )
        app._open_version_dialog.assert_called_once_with()

    def test_update_button_callback_is_safe_after_version_dialog_is_closed(self):
        app = object.__new__(autoprint_main.AutoPrintApp)
        app._version_dialog = None
        app._version_update_btn = None

        app._set_update_button("Проверка…", enabled=False)

        self.assertEqual(app._update_button_text, "Проверка…")
        self.assertFalse(app._update_button_enabled)

    def test_destroyed_version_button_is_not_configured(self):
        app = object.__new__(autoprint_main.AutoPrintApp)
        button = Mock()
        button.winfo_exists.return_value = False
        app._version_update_btn = button

        app._set_update_button("Обновить", enabled=True)

        button.configure.assert_not_called()


class GlobalTextEditingTests(unittest.TestCase):
    def test_english_layout_uses_native_binding_without_duplicate_paste(self):
        app = object.__new__(bartender_gui.App)
        app._text_edit_action = Mock()
        event = SimpleNamespace(state=0x4, keycode=86, keysym="v", widget=Mock())

        result = app._on_global_text_hotkey(event)

        self.assertIsNone(result)
        app._text_edit_action.assert_not_called()

    def test_non_latin_layout_uses_hardware_keycode_once(self):
        app = object.__new__(bartender_gui.App)
        app._text_edit_action = Mock(return_value="break")
        widget = Mock()
        event = SimpleNamespace(state=0x4, keycode=86, keysym="Cyrillic_em", widget=widget)

        result = app._on_global_text_hotkey(event)

        self.assertEqual(result, "break")
        app._text_edit_action.assert_called_once_with(widget, "paste")


class MainLayoutContractTests(unittest.TestCase):
    def test_main_layout_declares_all_approved_sections(self):
        self.assertEqual(
            bartender_gui.MAIN_LAYOUT_SECTION_NAMES,
            (
                "csv_files_section",
                "params_section",
                "print_section",
                "preview_section",
                "source_section",
                "csv_status_section",
                "log_section",
                "status_bar_frame",
            ),
        )

    def test_csv_file_manager_is_the_first_left_column_block(self):
        self.assertEqual(
            bartender_gui.MAIN_LAYOUT_BLOCK_ORDER,
            (
                ("left", "csv_files_section"),
                ("left", "params_section"),
                ("left", "print_section"),
                ("center", "preview_section"),
                ("right", "source_section"),
                ("right", "csv_status_section"),
            ),
        )
        self.assertNotEqual(
            bartender_gui.MAIN_LAYOUT_BLOCK_ORDER[0][1], "source_section"
        )

    def test_main_sections_have_unique_sequential_numbers(self):
        numbers = [
            bartender_gui.MAIN_LAYOUT_SECTION_NUMBERS[name]
            for _column, name in bartender_gui.MAIN_LAYOUT_BLOCK_ORDER
        ]
        self.assertEqual(numbers, ["1", "2", "3", "4", "5", "6"])
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_runtime_status_updates_without_rebuilding_widgets(self):
        app = object.__new__(bartender_gui.App)
        app.csv_path = r"D:\input\batch.csv"
        app.csv_rows = [{"GTIN": "1"}, {"GTIN": "2"}]
        app.format_combo = Mock()
        app.format_combo.get.return_value = "Auto"
        app.prn_combo = Mock()
        app.prn_combo.get.return_value = "TSC"
        for name in (
            "status_ready_label",
            "status_csv_label",
            "status_rows_label",
            "status_template_label",
            "status_printer_label",
            "csv_summary_label",
            "csv_rows_count_label",
            "csv_available_label",
        ):
            setattr(app, name, Mock())

        app._refresh_runtime_status()

        app.status_rows_label.configure.assert_called_once_with(text="Строк: 2")
        app.status_template_label.configure.assert_called_once_with(text="Шаблон: Auto")
        app.status_printer_label.configure.assert_called_once_with(text="Принтер: TSC")

    def test_path_shortening_responds_to_available_width(self):
        app = object.__new__(bartender_gui.App)
        path = r"C:\Users\operator\Documents\Orders\2026\September"

        narrow = app._responsive_path_text(path, 160)
        wide = app._responsive_path_text(path, 800)

        self.assertIn("...", narrow)
        self.assertTrue(narrow.startswith("C:\\"))
        self.assertTrue(narrow.endswith("September"))
        self.assertEqual(wide, path)

    def test_print_and_csv_scroll_policies_are_explicit(self):
        self.assertFalse(bartender_gui.PRINT_BATCH_CONTAINER_SCROLLABLE)
        self.assertEqual(
            bartender_gui.CSV_FILE_LIST_SCROLL_POLICY, "vertical-auto"
        )

    def test_csv_file_actions_use_three_equal_responsive_grid_columns(self):
        source = inspect.getsource(bartender_gui.App._build_ui)
        actions = source.split(
            'csv_file_buttons = ctk.CTkFrame(csv_files_body', 1
        )[1].split("self.csv_list_frame =", 1)[0]

        for column in range(3):
            self.assertIn(
                f"csv_file_buttons.grid_columnconfigure({column}, weight=1)",
                actions,
            )
        self.assertNotIn("width=", actions)
        self.assertEqual(actions.count('.grid(row=0, column='), 3)


class PrintSelectionTests(unittest.TestCase):
    def test_range_math_is_inclusive(self):
        self.assertEqual(bartender_gui.count_from_range(1, 1), 1)
        self.assertEqual(bartender_gui.count_from_range(25, 74), 50)

    def test_count_math_resolves_inclusive_end(self):
        self.assertEqual(bartender_gui.end_from_count(25, 50), 74)
        self.assertEqual(bartender_gui.end_from_count(100, 35), 134)

    def test_invalid_range_and_count_are_rejected(self):
        with self.assertRaises(ValueError):
            bartender_gui.count_from_range(25, 24)
        with self.assertRaises(ValueError):
            bartender_gui.end_from_count(25, 0)

    def test_default_mode_is_range(self):
        self.assertEqual(
            bartender_gui.DEFAULT_PRINT_SELECTION_MODE,
            bartender_gui.PRINT_SELECTION_MODE_RANGE,
        )

    def test_mode_conversion_preserves_selected_rows(self):
        count = bartender_gui.convert_print_selection_value(
            25,
            74,
            bartender_gui.PRINT_SELECTION_MODE_RANGE,
            bartender_gui.PRINT_SELECTION_MODE_COUNT,
        )
        end = bartender_gui.convert_print_selection_value(
            25,
            count,
            bartender_gui.PRINT_SELECTION_MODE_COUNT,
            bartender_gui.PRINT_SELECTION_MODE_RANGE,
        )
        self.assertEqual(count, 50)
        self.assertEqual(end, 74)

    def test_resolved_selection_exposes_effective_limit_for_existing_pipeline(self):
        self.assertEqual(
            bartender_gui.resolve_print_selection(
                bartender_gui.PRINT_SELECTION_MODE_RANGE, 25, 74, total_rows=100
            ),
            (25, 74, 50),
        )


class CalendarPopoverContractTests(unittest.TestCase):
    def test_date_helpers_format_and_fall_back_to_today(self):
        today = dt.date(2026, 9, 11)
        self.assertEqual(
            bartender_gui.production_date_from_text("05.09.2026", today),
            dt.date(2026, 9, 5),
        )
        self.assertEqual(
            bartender_gui.production_date_from_text("invalid", today), today
        )
        self.assertEqual(bartender_gui.format_production_date(today), "11.09.2026")

    def test_today_and_yesterday_targets(self):
        today = dt.date(2026, 9, 11)
        self.assertEqual(
            bartender_gui.quick_production_date(0, today), today
        )
        self.assertEqual(
            bartender_gui.quick_production_date(-1, today), dt.date(2026, 9, 10)
        )


class ThemeStartupTests(unittest.TestCase):
    def test_dark_theme_is_the_application_default(self):
        self.assertEqual(bartender_gui.DEFAULT_APPEARANCE_MODE, "dark")


class PartModeContractTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(bartender_gui.App)

    def test_only_blank_and_manual_are_selectable(self):
        self.assertEqual(
            bartender_gui.PART_MODE_OPTIONS,
            ("Не использовать", "Ввод вручную"),
        )
        self.assertNotIn("Из шаблона партии", bartender_gui.PART_MODE_OPTIONS)

    def test_part_mode_normalization_keeps_only_supported_modes(self):
        self.assertEqual(self.app._normalize_part_mode("blank"), "blank")
        self.assertEqual(self.app._normalize_part_mode("manual"), "manual")
        self.assertEqual(self.app._normalize_part_mode("catalog"), "blank")
        self.assertEqual(self.app._normalize_part_mode("auto"), "blank")
        self.assertEqual(
            self.app._normalize_part_mode("Из шаблона партии"), "blank"
        )

    def test_only_manual_templates_require_part_num(self):
        self.assertNotIn("PART_NUM", self.app._template_required_fields("blank"))
        self.assertIn("PART_NUM", self.app._template_required_fields("manual"))
        self.assertNotIn("PART_NUM", self.app._template_required_fields("catalog"))

    def _prepare_enrichment(self, mode, manual_value):
        self.app._get_prod_date = Mock(return_value=dt.date(2026, 9, 11))
        self.app._get_exp_days = Mock(return_value=None)
        self.app.format_combo = Mock()
        self.app.format_combo.get.return_value = "Auto"
        self.app.part_entry = Mock()
        self.app.part_entry.get.return_value = manual_value
        self.app.product_map = {"123": {"TEMPLATE_ID": "16x16", "SHELF": {}}}
        self.app._template_part_mode = Mock(return_value=mode)
        self.app._mark_part_entry = Mock()
        self.app.logger = Mock()

    def test_blank_mode_always_clears_part_num(self):
        self._prepare_enrichment("blank", "stale-value")

        enriched = self.app._enrich(
            {"DM": "code", "GTIN": "123", "NAME": "Product"}, 1
        )

        self.assertEqual(enriched["PART_NUM"], "")

    def test_manual_mode_uses_only_manual_value(self):
        self._prepare_enrichment("manual", "42001")

        enriched = self.app._enrich(
            {"DM": "code", "GTIN": "123", "NAME": "Product"}, 1
        )

        self.assertEqual(enriched["PART_NUM"], "42001")

    def test_empty_manual_value_blocks_enrichment(self):
        self._prepare_enrichment("manual", "")

        enriched = self.app._enrich(
            {"DM": "code", "GTIN": "123", "NAME": "Product"}, 1
        )

        self.assertIsNone(enriched)


if __name__ == "__main__":
    unittest.main()
