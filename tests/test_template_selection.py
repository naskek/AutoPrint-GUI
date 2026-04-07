import unittest

from data_processing import _legacy_template_from_pack, choose_format_for


class TemplateSelectionTests(unittest.TestCase):
    def test_manual_choice_accepts_custom_template(self):
        product_map = {
            "1234567890123": {"TEMPLATE_ID": "30x20"},
        }
        self.assertEqual(
            choose_format_for("1234567890123", product_map, "58x60"),
            "58x60",
        )

    def test_product_map_template_has_priority(self):
        product_map = {
            "1234567890123": {"TEMPLATE_ID": "58x60"},
        }
        self.assertEqual(
            choose_format_for("1234567890123", product_map, "Auto"),
            "58x60",
        )

    def test_auto_falls_back_to_legacy_pack_mapping(self):
        self.assertEqual(_legacy_template_from_pack("Ведро 0,9 кг"), "30x20")
        self.assertEqual(_legacy_template_from_pack("Банка 180 г"), "16x16")


if __name__ == "__main__":
    unittest.main()
