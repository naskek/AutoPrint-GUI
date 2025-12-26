# -*- coding: utf-8 -*-

import unittest

from crpt_client import normalize_code, parse_codes, interpret_status


class TestCrptParsing(unittest.TestCase):
    def test_normalize_code_u001d(self):
        src = "01ABC\\u001d21DEF"
        out = normalize_code(src)
        self.assertIn(chr(29), out)
        self.assertNotIn("\\u001d", out)

    def test_parse_codes_dedupe(self):
        text = "A\n\nA \nB"
        codes = parse_codes(text, dedupe=True)
        self.assertEqual(codes, ["A", "B"])


class TestCrptStatus(unittest.TestCase):
    def test_interpret_status_known(self):
        label, introduced, severity = interpret_status("INTRODUCED", None)
        self.assertTrue(introduced)
        self.assertEqual(severity, "ok")
        label, introduced, severity = interpret_status("APPLIED", None)
        self.assertFalse(introduced)
        self.assertEqual(severity, "warn")

    def test_interpret_status_unknown(self):
        label, introduced, severity = interpret_status("SOMETHING_NEW", None)
        self.assertIsNone(introduced)
        self.assertEqual(severity, "info")
        self.assertIn("SOMETHING_NEW", label)


if __name__ == "__main__":
    unittest.main()
