import os
import sys
import types
import unittest
from unittest.mock import Mock, patch

from tools import bartender_spooler_smoke


class BarTenderSpoolerSmokeTests(unittest.TestCase):
    def _fake_win32print(self):
        module = types.ModuleType("win32print")
        module.PRINTER_STATUS_PAUSED = 1
        module.JOB_CONTROL_DELETE = 5
        return module

    def test_safe_queue_requires_test_name_dummy_port_pause_and_empty_queue(self):
        fake = self._fake_win32print()
        snapshot = {
            "name": "AutoPrint_TEST_Printronix",
            "port": "AUTOPRINT_TEST_LOOPBACK",
            "driver": "Printronix Driver",
            "status": fake.PRINTER_STATUS_PAUSED,
            "jobs": [],
        }
        with patch.dict(sys.modules, {"win32print": fake}):
            bartender_spooler_smoke._assert_safe_test_queue(snapshot)

    def test_safe_queue_rejects_production_name(self):
        fake = self._fake_win32print()
        snapshot = {
            "name": "Printronix Production",
            "port": "AUTOPRINT_TEST_LOOPBACK",
            "driver": "Printronix Driver",
            "status": fake.PRINTER_STATUS_PAUSED,
            "jobs": [],
        }
        with patch.dict(sys.modules, {"win32print": fake}):
            with self.assertRaisesRegex(RuntimeError, "Unsafe queue name"):
                bartender_spooler_smoke._assert_safe_test_queue(snapshot)

    def test_safe_queue_rejects_non_test_port(self):
        fake = self._fake_win32print()
        snapshot = {
            "name": "AutoPrint_TEST_Printronix",
            "port": "192.168.1.50",
            "driver": "Printronix Driver",
            "status": fake.PRINTER_STATUS_PAUSED,
            "jobs": [],
        }
        with patch.dict(sys.modules, {"win32print": fake}):
            with self.assertRaisesRegex(RuntimeError, "Unsafe queue port"):
                bartender_spooler_smoke._assert_safe_test_queue(snapshot)

    def test_safe_queue_rejects_unpaused_queue(self):
        fake = self._fake_win32print()
        snapshot = {
            "name": "AutoPrint_TEST_Printronix",
            "port": "AUTOPRINT_TEST_LOOPBACK",
            "driver": "Printronix Driver",
            "status": 0,
            "jobs": [],
        }
        with patch.dict(sys.modules, {"win32print": fake}):
            with self.assertRaisesRegex(RuntimeError, "is not paused"):
                bartender_spooler_smoke._assert_safe_test_queue(snapshot)

    def test_safe_queue_rejects_existing_jobs(self):
        fake = self._fake_win32print()
        snapshot = {
            "name": "AutoPrint_TEST_Printronix",
            "port": "AUTOPRINT_TEST_LOOPBACK",
            "driver": "Printronix Driver",
            "status": fake.PRINTER_STATUS_PAUSED,
            "jobs": [{"JobId": 10}],
        }
        with patch.dict(sys.modules, {"win32print": fake}):
            with self.assertRaisesRegex(RuntimeError, "not empty"):
                bartender_spooler_smoke._assert_safe_test_queue(snapshot)

    @patch("tools.bartender_spooler_smoke.time.sleep", return_value=None)
    @patch("tools.bartender_spooler_smoke._delete_test_jobs")
    @patch("tools.bartender_spooler_smoke._submit_one_batch", return_value=0)
    @patch("tools.bartender_spooler_smoke._assert_safe_test_queue")
    @patch("tools.bartender_spooler_smoke._require_live_gate")
    @patch("tools.bartender_spooler_smoke._printer_snapshot")
    def test_main_accepts_exactly_one_spool_job_and_cleans_it(
        self,
        snapshot,
        live_gate,
        safe_queue,
        submit,
        delete_jobs,
        sleep,
    ):
        before = {
            "name": "AutoPrint_TEST_Printronix",
            "port": "AUTOPRINT_TEST_LOOPBACK",
            "driver": "Printronix Driver",
            "status": 1,
            "jobs": [],
        }
        job = {
            "JobId": 42,
            "pDocument": "AutoPrint TEST spooler smoke 1-100",
            "Status": 1,
            "TotalPages": 0,
        }
        after = {**before, "jobs": [job]}
        cleanup = {**before, "jobs": [job]}
        snapshot.side_effect = [before, after, cleanup]

        result = bartender_spooler_smoke.main(
            [
                "--btw",
                "test.btw",
                "--printer",
                "AutoPrint_TEST_Printronix",
                "--record-range",
                "1-100",
                "--settle-seconds",
                "0",
            ]
        )

        self.assertEqual(result, 0)
        live_gate.assert_called_once_with()
        safe_queue.assert_called_once_with(before)
        submit.assert_called_once_with(
            "test.btw",
            "AutoPrint_TEST_Printronix",
            "1-100",
        )
        delete_jobs.assert_called_once_with(
            "AutoPrint_TEST_Printronix",
            [job],
        )

    @patch("tools.bartender_spooler_smoke.time.sleep", return_value=None)
    @patch("tools.bartender_spooler_smoke._delete_test_jobs")
    @patch("tools.bartender_spooler_smoke._submit_one_batch", return_value=0)
    @patch("tools.bartender_spooler_smoke._assert_safe_test_queue")
    @patch("tools.bartender_spooler_smoke._require_live_gate")
    @patch("tools.bartender_spooler_smoke._printer_snapshot")
    def test_main_fails_when_one_batch_creates_multiple_spool_jobs(
        self,
        snapshot,
        live_gate,
        safe_queue,
        submit,
        delete_jobs,
        sleep,
    ):
        before = {
            "name": "AutoPrint_TEST_Printronix",
            "port": "AUTOPRINT_TEST_LOOPBACK",
            "driver": "Printronix Driver",
            "status": 1,
            "jobs": [],
        }
        jobs = [
            {"JobId": 1, "pDocument": "job-1", "Status": 1, "TotalPages": 0},
            {"JobId": 2, "pDocument": "job-2", "Status": 1, "TotalPages": 0},
        ]
        after = {**before, "jobs": jobs}
        cleanup = {**before, "jobs": jobs}
        snapshot.side_effect = [before, after, cleanup]

        result = bartender_spooler_smoke.main(
            [
                "--btw",
                "test.btw",
                "--printer",
                "AutoPrint_TEST_Printronix",
                "--record-range",
                "1-100",
                "--settle-seconds",
                "0",
            ]
        )

        self.assertEqual(result, 1)
        submit.assert_called_once()
        delete_jobs.assert_called_once_with(
            "AutoPrint_TEST_Printronix",
            jobs,
        )

    def test_submit_uses_one_documented_format_print_call(self):
        pythoncom = types.ModuleType("pythoncom")
        pythoncom.VT_BYREF = 0x4000
        pythoncom.VT_DISPATCH = 9
        pythoncom.CoInitialize = Mock()
        pythoncom.CoUninitialize = Mock()

        client = types.ModuleType("win32com.client")
        app = Mock()
        fmt = Mock()
        fmt.PrintSetup = types.SimpleNamespace()
        fmt.Print.return_value = 0
        app.Formats.Open.return_value = fmt
        app.FullVersion = "2022 R5"
        app.Edition = "Enterprise"
        client.Dispatch = Mock(return_value=app)
        messages = object()
        client.VARIANT = Mock(return_value=messages)

        win32com = types.ModuleType("win32com")
        win32com.client = client

        with patch.dict(
            sys.modules,
            {
                "pythoncom": pythoncom,
                "win32com": win32com,
                "win32com.client": client,
            },
        ), patch("os.path.isfile", return_value=True):
            result = bartender_spooler_smoke._submit_one_batch(
                "test.btw",
                "AutoPrint_TEST_Printronix",
                "1-100",
            )

        self.assertEqual(result, 0)
        fmt.Print.assert_called_once_with(
            "AutoPrint TEST spooler smoke 1-100",
            False,
            30000,
            messages,
        )
        self.assertEqual(fmt.PrintSetup.Printer, "AutoPrint_TEST_Printronix")
        self.assertEqual(fmt.PrintSetup.IdenticalCopiesOfLabel, 1)
        self.assertTrue(fmt.UseDatabase)
        self.assertFalse(fmt.SelectRecordsAtPrint)
        self.assertEqual(fmt.RecordRange, "1-100")
        fmt.Close.assert_called_once_with(
            bartender_spooler_smoke.BT_DO_NOT_SAVE_CHANGES
        )
        app.Quit.assert_called_once_with(
            bartender_spooler_smoke.BT_DO_NOT_SAVE_CHANGES
        )


if __name__ == "__main__":
    unittest.main()
