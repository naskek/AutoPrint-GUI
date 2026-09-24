import datetime as dt
import inspect
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autoprint_main
import bartender_gui


class _EntryStub:
    def __init__(self, value=""):
        self.value = str(value)

    def get(self):
        return self.value

    def delete(self, _start, _end=None):
        self.value = ""

    def insert(self, _index, value):
        self.value = str(value)


class StickerTemplateTests(unittest.TestCase):
    def test_resolver_priority_config_then_templates_then_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            templates_dir = os.path.join(root, "templates")
            os.makedirs(templates_dir, exist_ok=True)
            configured = os.path.join(root, "configured.btw")
            canonical = os.path.join(templates_dir, bartender_gui.STICKER_BTW_NAME)
            legacy = os.path.join(root, bartender_gui.STICKER_BTW_NAME)

            for path, content in (
                (configured, "configured"),
                (canonical, "canonical"),
                (legacy, "legacy"),
            ):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)

            with patch.object(bartender_gui, "BASE_DIR", root), patch.object(
                bartender_gui, "TEMPLATES_DIR", templates_dir
            ):
                cfg = {"sticker_btw": configured}
                self.assertEqual(
                    bartender_gui.resolve_sticker_template_path(cfg), configured
                )

                os.remove(configured)
                self.assertEqual(
                    bartender_gui.resolve_sticker_template_path(cfg), canonical
                )

                os.remove(canonical)
                self.assertEqual(
                    bartender_gui.resolve_sticker_template_path(cfg), legacy
                )

                os.remove(legacy)
                self.assertIsNone(
                    bartender_gui.resolve_sticker_template_path(cfg)
                )

    def test_import_copies_to_canonical_name_and_persists_config(self):
        app = object.__new__(bartender_gui.App)
        app.cfg = {}

        with tempfile.TemporaryDirectory() as root:
            templates_dir = os.path.join(root, "templates")
            source = os.path.join(root, "custom-name.btw")
            with open(source, "w", encoding="utf-8") as fh:
                fh.write("fixture")

            with patch.object(
                bartender_gui, "TEMPLATES_DIR", templates_dir
            ), patch("bartender_gui.save_config") as save_config:
                target = app._import_sticker_template(source)

            self.assertEqual(
                target,
                os.path.join(templates_dir, bartender_gui.STICKER_BTW_NAME),
            )
            with open(target, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "fixture")
            self.assertEqual(app.cfg["sticker_btw"], target)
            save_config.assert_called_once_with(app.cfg)


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

    @patch("autoprint_main.ctk.CTkToplevel")
    def test_version_popup_toggles_closed_on_repeated_button_click(self, toplevel):
        app = object.__new__(autoprint_main.AutoPrintApp)
        app._version_dialog = Mock()
        app._version_dialog.winfo_exists.return_value = True
        app._close_version_dialog = Mock()

        app._open_version_dialog()

        app._close_version_dialog.assert_called_once_with(app._version_dialog)
        toplevel.assert_not_called()

    def test_version_popup_uses_version_button_as_right_aligned_anchor(self):
        source = inspect.getsource(autoprint_main.AutoPrintApp._open_version_dialog)

        self.assertIn("self.version_identity_btn", source)
        self.assertRegex(
            source,
            r"_activate_popover\([\s\S]*self\.version_identity_btn[\s\S]*align_right=True",
        )


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
    def test_startup_removes_customtkinter_global_left_click_focus_hook(self):
        source = inspect.getsource(bartender_gui.App.__init__)

        self.assertLess(
            source.index("super().__init__()"),
            source.index('self.unbind_all("<Button-1>")'),
        )
        self.assertLess(
            source.index('self.unbind_all("<Button-1>")'),
            source.index("self._build_ui()"),
        )

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

        compact_row = app._responsive_csv_dir_text(path, 240)
        self.assertTrue(compact_row.startswith("Текущая папка: C:\\"))
        self.assertIn("...", compact_row)
        self.assertTrue(compact_row.endswith("September"))

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

    def test_csv_file_section_keeps_controls_above_only_flexible_list(self):
        source = inspect.getsource(bartender_gui.App._build_ui)
        section = source.split(
            "csv_files_body = self.csv_files_section.body", 1
        )[1].split("self.params_section =", 1)[0]

        self.assertNotIn('text="Текущая папка:"', section)
        self.assertIn("text=self._responsive_csv_dir_text(self.csv_dir", section)
        self.assertIn("csv_files_body.grid_rowconfigure(2, weight=1)", section)
        self.assertRegex(section, r"csv_file_buttons\.grid\(\s*row=1")
        self.assertRegex(section, r"self\.csv_list_frame\.grid\(\s*row=2")
        self.assertLess(
            section.index("csv_file_buttons.grid("),
            section.index("self.csv_list_frame.grid("),
        )
        actions = section.split(
            'csv_file_buttons = ctk.CTkFrame(csv_files_body', 1
        )[1].split("self.csv_list_frame =", 1)[0]
        self.assertNotIn("height=", actions)


class ResizeDebounceTests(unittest.TestCase):
    @staticmethod
    def _scheduled_app():
        app = object.__new__(bartender_gui.App)
        callbacks = []

        def schedule(delay, callback):
            callbacks.append((delay, callback))
            return f"after-{len(callbacks)}"

        app.after = Mock(side_effect=schedule)
        app.after_idle = Mock(return_value="idle")
        app.after_cancel = Mock()
        return app, callbacks

    def test_repeated_path_resize_events_coalesce_and_final_refresh_runs(self):
        app, callbacks = self._scheduled_app()
        app._path_refresh_after_id = None
        app.csv_dir = r"C:\Users\operator\Orders\September"
        app.csv_dir_label = Mock()
        app.csv_dir_label.winfo_width.return_value = 240
        app.csv_dir_tooltip = SimpleNamespace(text="")
        app.source_dir_value_label = None

        app._schedule_path_label_refresh()
        app._schedule_path_label_refresh()

        self.assertEqual(app.after.call_count, 2)
        app.after_cancel.assert_called_once_with("after-1")
        app.csv_dir_label.configure.assert_not_called()

        callbacks[-1][1]()

        app.csv_dir_label.configure.assert_called_once()
        self.assertEqual(app.csv_dir_tooltip.text, app.csv_dir)
        self.assertIsNone(app._path_refresh_after_id)

    def test_repeated_scrollbar_resize_events_coalesce_without_geometry_flush(self):
        app, callbacks = self._scheduled_app()
        app._csv_scroll_refresh_after_id = None
        app.update_idletasks = Mock()
        canvas = Mock()
        canvas.bbox.return_value = (0, 0, 100, 300)
        canvas.winfo_height.return_value = 100
        scrollbar = Mock()
        app.csv_list_frame = SimpleNamespace(
            _parent_canvas=canvas,
            _scrollbar=scrollbar,
        )

        app._schedule_csv_scrollbar_refresh()
        app._schedule_csv_scrollbar_refresh()

        self.assertEqual(app.after.call_count, 2)
        app.after_cancel.assert_called_once_with("after-1")

        callbacks[-1][1]()

        app.update_idletasks.assert_not_called()
        scrollbar.grid.assert_called_once_with()
        self.assertIsNone(app._csv_scroll_refresh_after_id)

    def test_repeated_preview_resize_events_coalesce_before_redraw(self):
        app, callbacks = self._scheduled_app()
        app._preview_resize_after_id = None
        app._preview_resize_busy = False
        app.preview_current_path = bartender_gui.__file__
        app._show_preview_path = Mock()

        app._on_preview_resize()
        app._on_preview_resize()

        self.assertEqual(app.after.call_count, 2)
        app.after_cancel.assert_called_once_with("after-1")
        app._show_preview_path.assert_not_called()

        callbacks[-1][1]()

        app._show_preview_path.assert_called_once_with(app.preview_current_path)
        self.assertIsNone(app._preview_resize_after_id)

    def test_pending_resize_callbacks_are_safe_after_widgets_are_destroyed(self):
        app, callbacks = self._scheduled_app()
        app._path_refresh_after_id = None
        app._csv_scroll_refresh_after_id = None
        app._preview_resize_after_id = None
        app._preview_resize_busy = False
        app.csv_dir = r"C:\Orders"
        app.csv_dir_label = Mock()
        app.csv_dir_label.winfo_width.side_effect = RuntimeError("destroyed")
        app.csv_dir_tooltip = SimpleNamespace(text="")
        app.source_dir_value_label = None
        canvas = Mock()
        canvas.bbox.side_effect = RuntimeError("destroyed")
        app.csv_list_frame = SimpleNamespace(
            _parent_canvas=canvas,
            _scrollbar=Mock(),
        )
        app.preview_current_path = bartender_gui.__file__
        app._show_preview_path = Mock(side_effect=RuntimeError("destroyed"))

        app._schedule_path_label_refresh()
        path_callback = callbacks[-1][1]
        app._schedule_csv_scrollbar_refresh()
        scrollbar_callback = callbacks[-1][1]
        app._on_preview_resize()
        preview_callback = callbacks[-1][1]

        path_callback()
        scrollbar_callback()
        preview_callback()


class LivePreviewTests(unittest.TestCase):
    @staticmethod
    def _scheduled_app():
        app = object.__new__(bartender_gui.App)
        callbacks = []

        def schedule(delay, callback):
            callbacks.append((delay, callback))
            return f"after-{len(callbacks)}"

        app.after = Mock(side_effect=schedule)
        app.after_cancel = Mock()
        app.logger = Mock()
        app._preview = Mock(return_value=True)
        return app, callbacks

    def test_repeated_changes_coalesce_and_only_latest_preview_runs(self):
        app, callbacks = self._scheduled_app()

        app._schedule_live_preview()
        app._schedule_live_preview()

        self.assertEqual(
            [delay for delay, _callback in callbacks],
            [
                bartender_gui.LIVE_PREVIEW_DEBOUNCE_MS,
                bartender_gui.LIVE_PREVIEW_DEBOUNCE_MS,
            ],
        )
        app.after_cancel.assert_called_once_with("after-1")

        callbacks[0][1]()
        app._preview.assert_not_called()

        callbacks[-1][1]()
        app._preview.assert_called_once_with(silent=True)
        self.assertIsNone(app._live_preview_after_id)
        self.assertFalse(app._live_preview_busy)

    def test_immediate_preview_uses_zero_delay(self):
        app, callbacks = self._scheduled_app()

        app._schedule_live_preview(immediate=True)

        self.assertEqual(callbacks[0][0], 0)
        callbacks[0][1]()
        app._preview.assert_called_once_with(silent=True)

    def test_silent_invalid_preview_row_does_not_show_modal_error(self):
        app = object.__new__(bartender_gui.App)
        app.index_entry = _EntryStub("bad")
        app.csv_rows = [{"GTIN": "1"}]

        with patch("bartender_gui.mb.showerror") as showerror:
            result = app._resolve_preview_index(silent=True)

        self.assertIsNone(result)
        showerror.assert_not_called()

    def test_silent_invalid_date_does_not_show_modal_error(self):
        app = object.__new__(bartender_gui.App)
        app.prod_date_entry = _EntryStub("24.09.")

        with patch("bartender_gui.mb.showerror") as showerror:
            result = app._get_prod_date(silent=True)

        self.assertIsNone(result)
        showerror.assert_not_called()


class PrintSelectionTests(unittest.TestCase):
    @staticmethod
    def _make_csv_load_app(mode=bartender_gui.PRINT_SELECTION_MODE_RANGE):
        app = object.__new__(bartender_gui.App)
        app.csv_dir = ""
        app.cfg = {}
        app.logger = Mock()
        app.csv_label = Mock()
        app.default_batch_size = 100
        app.batch_entry = _EntryStub()
        app.index_entry = _EntryStub("99")
        app.limit_entry = _EntryStub("7")
        app.print_selection_mode_var = Mock()
        app.print_selection_mode_var.get.return_value = mode
        app.print_selection_summary_label = Mock()
        app._update_csv_dir_label = Mock()
        app._update_brand_hint = Mock()
        app._render_csv_list = Mock()
        app._refresh_runtime_status = Mock()
        app._preview = Mock()
        app._schedule_live_preview = Mock()
        return app

    def test_loading_csv_schedules_immediate_live_preview(self):
        app = self._make_csv_load_app()
        rows = [{"GTIN": "1", "DM": "a", "NAME": "one"}]

        with patch("bartender_gui.save_config"), patch(
            "bartender_gui.load_kontur_raw", return_value=rows
        ):
            app._load_csv_from_path("first.csv", auto_preview=True)

        app._schedule_live_preview.assert_called_once_with(immediate=True)
        app._preview.assert_not_called()

    def test_loading_csv_defaults_selection_to_full_file_and_resets_on_reload(self):
        app = self._make_csv_load_app()
        three_rows = [
            {"GTIN": "1", "DM": "a", "NAME": "one"},
            {"GTIN": "1", "DM": "b", "NAME": "two"},
            {"GTIN": "1", "DM": "c", "NAME": "three"},
        ]
        one_row = [{"GTIN": "2", "DM": "x", "NAME": "single"}]

        with patch("bartender_gui.save_config"), patch(
            "bartender_gui.load_kontur_raw", side_effect=[three_rows, one_row]
        ):
            app._load_csv_from_path("first.csv", auto_preview=False)
            self.assertEqual(app.index_entry.get(), "1")
            self.assertEqual(app.limit_entry.get(), "3")

            app.index_entry.value = "2"
            app.limit_entry.value = "2"
            app._load_csv_from_path("second.csv", auto_preview=False)

        self.assertEqual(app.index_entry.get(), "1")
        self.assertEqual(app.limit_entry.get(), "1")

    def test_full_file_default_is_valid_in_count_mode(self):
        app = self._make_csv_load_app(bartender_gui.PRINT_SELECTION_MODE_COUNT)
        rows = [
            {"GTIN": "1", "DM": str(index), "NAME": "row"}
            for index in range(4)
        ]

        with patch("bartender_gui.save_config"), patch(
            "bartender_gui.load_kontur_raw", return_value=rows
        ):
            app._load_csv_from_path("count.csv", auto_preview=False)

        self.assertEqual(app.index_entry.get(), "1")
        self.assertEqual(app.limit_entry.get(), "4")
        self.assertEqual(
            bartender_gui.resolve_print_selection(
                bartender_gui.PRINT_SELECTION_MODE_COUNT,
                app.index_entry.get(),
                app.limit_entry.get(),
                total_rows=len(rows),
            ),
            (1, 4, 4),
        )
        app.print_selection_summary_label.configure.assert_called_with(
            text="Строки 1–4 · 4 шт. · из 4"
        )

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

    def test_calendar_uses_shared_non_modal_popover_activation(self):
        source = inspect.getsource(bartender_gui.App._open_date_picker)

        self.assertIn("popover = tk.Toplevel(self)", source)
        self.assertNotIn("popover = ctk.CTkToplevel(self)", source)
        self.assertIn("popover.withdraw()", source)
        self.assertIn("self._match_plain_popover_background(popover, panel)", source)
        self.assertIn('anchor = getattr(self, "prod_date_button"', source)
        self.assertIn("self._activate_popover(", source)
        self.assertIn("self._close_date_popover", source)
        self.assertNotIn("grab_set", source)

    def test_calendar_day_selection_writes_date_then_closes(self):
        app = object.__new__(bartender_gui.App)
        app.prod_date_entry = Mock()
        app._mark_prod_date_entry = Mock()
        app._close_date_popover = Mock()

        app._set_production_date_from_picker(dt.date(2026, 9, 12))

        app.prod_date_entry.delete.assert_called_once_with(0, "end")
        app.prod_date_entry.insert.assert_called_once_with(0, "12.09.2026")
        app._mark_prod_date_entry.assert_called_once_with(False)
        app._close_date_popover.assert_called_once_with()


class PopoverInteractionTests(unittest.TestCase):
    def test_version_popover_does_not_add_customtkinter_global_focus_hook(self):
        source = inspect.getsource(autoprint_main.AutoPrintApp._open_version_dialog)

        self.assertIn("dialog = tk.Toplevel(self)", source)
        self.assertNotIn("dialog = ctk.CTkToplevel(self)", source)
        self.assertIn("dialog.withdraw()", source)
        self.assertIn("content = ctk.CTkFrame(dialog", source)
        self.assertIn('content.pack(fill="both", expand=True)', source)
        self.assertIn("self._match_plain_popover_background(dialog, content)", source)

    @staticmethod
    def _outside_click_app():
        app = object.__new__(bartender_gui.App)
        idle_callbacks = []
        app.after_idle = Mock(
            side_effect=lambda callback: idle_callbacks.append(callback) or "install-1"
        )
        app.after_cancel = Mock()
        app.bind_all = Mock(return_value="outside-binding")
        app._unbind = Mock()
        return app, idle_callbacks

    def test_calendar_outside_click_closes_without_changing_date(self):
        app, idle_callbacks = self._outside_click_app()
        popover = Mock()
        popover.winfo_exists.return_value = True
        app._date_popover = popover
        app.prod_date_entry = Mock()

        app._install_popover_outside_click(popover, app._close_date_popover)
        idle_callbacks[0]()
        outside_handler = app.bind_all.call_args.args[1]
        event = SimpleNamespace(widget=Mock(master=app), x_root=50, y_root=60)
        app.winfo_containing = Mock(return_value=event.widget)
        result = outside_handler(event)

        self.assertIsNone(result)
        app.prod_date_entry.delete.assert_not_called()
        app.prod_date_entry.insert.assert_not_called()
        popover.destroy.assert_called_once_with()
        self.assertIsNone(app._date_popover)
        app._unbind.assert_called_once()

    def test_inside_click_does_not_close_popover(self):
        app, idle_callbacks = self._outside_click_app()
        popover = Mock()
        popover.winfo_exists.return_value = True
        close = Mock()
        child = Mock(master=popover)

        app._install_popover_outside_click(popover, close)
        idle_callbacks[0]()
        outside_handler = app.bind_all.call_args.args[1]
        app.winfo_containing = Mock(return_value=child)
        result = outside_handler(
            SimpleNamespace(widget=Mock(master=app), x_root=100, y_root=120)
        )

        self.assertIsNone(result)
        close.assert_not_called()

    def test_outside_click_closes_without_consuming_main_window_click(self):
        app, idle_callbacks = self._outside_click_app()
        popover = Mock()
        popover.winfo_exists.return_value = True
        close = Mock()
        main_button = Mock(master=app)
        app.winfo_containing = Mock(return_value=main_button)

        app._install_popover_outside_click(popover, close)
        idle_callbacks[0]()
        outside_handler = app.bind_all.call_args.args[1]
        result = outside_handler(
            SimpleNamespace(widget=main_button, x_root=400, y_root=300)
        )

        close.assert_called_once_with()
        self.assertIsNone(result)

    def test_escape_closes_popover_and_removes_outside_binding(self):
        app, idle_callbacks = self._outside_click_app()
        app._position_popover = Mock()
        popover = Mock()
        popover.winfo_exists.return_value = True
        anchor = Mock()
        close = Mock(side_effect=lambda: app._remove_popover_outside_click(popover))

        app._activate_popover(popover, anchor, close)
        idle_callbacks[0]()
        escape_handler = next(
            call.args[1] for call in popover.bind.call_args_list
            if call.args[0] == "<Escape>"
        )
        escape_handler(SimpleNamespace())

        close.assert_called_once_with()
        app._unbind.assert_called_once_with(
            ("bind", "all", "<Button-1>"), "outside-binding"
        )

    def test_right_aligned_position_is_derived_from_anchor_and_clamped(self):
        app = object.__new__(bartender_gui.App)
        app._screen_work_area_at = Mock(return_value=(0, 0, 1000, 700))
        anchor = Mock()
        anchor.winfo_rootx.return_value = 500
        anchor.winfo_rooty.return_value = 200
        anchor.winfo_width.return_value = 100
        anchor.winfo_height.return_value = 28
        popover = Mock()
        popover.winfo_reqwidth.return_value = 300
        popover.winfo_reqheight.return_value = 140
        popover.winfo_width.return_value = 300
        popover.winfo_height.return_value = 140

        app._position_popover(popover, anchor, align_right=True)

        app._screen_work_area_at.assert_called_once_with(550, 214)
        popover.geometry.assert_called_once_with("+300+232")

    @patch("bartender_gui.ctk.get_appearance_mode", return_value="Dark")
    def test_plain_popover_native_background_matches_ctk_content(self, _mode):
        popover = Mock()
        content = Mock()
        content.cget.return_value = ("#f0f0f0", "#202020")

        bartender_gui.App._match_plain_popover_background(popover, content)

        popover.configure.assert_called_once_with(
            background="#202020",
            borderwidth=0,
            highlightthickness=0,
        )

    def test_popover_moves_above_anchor_at_monitor_bottom_edge(self):
        app = object.__new__(bartender_gui.App)
        app._screen_work_area_at = Mock(return_value=(0, 0, 1000, 700))
        anchor = Mock()
        anchor.winfo_rootx.return_value = 950
        anchor.winfo_rooty.return_value = 680
        anchor.winfo_width.return_value = 40
        anchor.winfo_height.return_value = 20
        popover = Mock()
        popover.winfo_reqwidth.return_value = 300
        popover.winfo_reqheight.return_value = 140
        popover.winfo_width.return_value = 300
        popover.winfo_height.return_value = 140

        app._position_popover(popover, anchor, align_right=True)

        popover.geometry.assert_called_once_with("+690+536")

    def test_version_outside_click_closes_and_clears_reference(self):
        app = object.__new__(autoprint_main.AutoPrintApp)
        idle_callbacks = []
        app.after_idle = Mock(
            side_effect=lambda callback: idle_callbacks.append(callback) or "install-1"
        )
        app.after_cancel = Mock()
        app.bind_all = Mock(return_value="outside-binding")
        app._unbind = Mock()
        popover = Mock()
        popover.winfo_exists.return_value = True
        app._version_dialog = popover
        app._version_update_btn = Mock()

        app._install_popover_outside_click(
            popover, lambda: app._close_version_dialog(popover)
        )
        idle_callbacks[0]()
        outside_handler = app.bind_all.call_args.args[1]
        outside_handler(SimpleNamespace(widget=Mock(master=app)))

        self.assertIsNone(app._version_dialog)
        self.assertIsNone(app._version_update_btn)
        popover.destroy.assert_called_once_with()
        app._unbind.assert_called_once()

    def test_destroyed_popover_cleanup_is_safe(self):
        app, idle_callbacks = self._outside_click_app()
        popover = Mock()
        popover.winfo_exists.side_effect = RuntimeError("destroyed")
        close = Mock()

        app._install_popover_outside_click(popover, close)
        idle_callbacks[0]()
        app._remove_popover_outside_click(popover)

        close.assert_not_called()

    def test_closed_popover_cannot_install_deferred_stale_binding(self):
        app, idle_callbacks = self._outside_click_app()
        popover = Mock()
        close = Mock()

        app._install_popover_outside_click(popover, close)
        app._remove_popover_outside_click(popover)
        idle_callbacks[0]()

        app.after_cancel.assert_called_once_with("install-1")
        app.bind_all.assert_not_called()
        self.assertEqual(app.__dict__.get("_popover_outside_bindings"), {})

    def test_no_global_click_binding_exists_without_open_popover(self):
        app, _idle_callbacks = self._outside_click_app()

        app._cleanup_popover_outside_clicks()

        app.bind_all.assert_not_called()
        app._unbind.assert_not_called()


class ThemeStartupTests(unittest.TestCase):
    def test_dark_theme_is_the_application_default(self):
        self.assertEqual(bartender_gui.DEFAULT_APPEARANCE_MODE, "dark")

    @patch("bartender_gui.ctk.set_appearance_mode")
    def test_theme_switch_is_direct_and_preserves_post_theme_refreshes(self, set_mode):
        app = object.__new__(bartender_gui.App)
        app.theme_switch = Mock()
        app.theme_switch.get.return_value = 1
        app.logger = Mock()
        app._apply_log_selection_theme = Mock()
        app._render_csv_list = Mock()
        app._update_menu_colors = Mock()
        app._refresh_bottom_layout = Mock()

        app._toggle_theme()

        set_mode.assert_called_once_with("dark")
        app.logger.configure_tags.assert_called_once_with()
        app._apply_log_selection_theme.assert_called_once_with()
        app._render_csv_list.assert_called_once_with()
        app._update_menu_colors.assert_called_once_with()
        app._refresh_bottom_layout.assert_called_once_with()


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
