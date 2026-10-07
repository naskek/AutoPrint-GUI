import unittest
from unittest.mock import Mock, patch

import printer_status


class PrinterStatusTests(unittest.TestCase):
    def _win32print(self, info):
        api = Mock()
        api.OpenPrinter.return_value = object()
        api.GetPrinter.return_value = dict(info)
        return api

    def test_blocking_status_reports_offline_and_work_offline(self):
        reasons = printer_status.blocking_status_reasons(
            printer_status.PRINTER_STATUS_OFFLINE,
            printer_status.PRINTER_ATTRIBUTE_WORK_OFFLINE,
        )

        self.assertIn("принтер offline", reasons)
        self.assertIn("Работать автономно", " ".join(reasons))

    def test_usb_printer_uses_spooler_without_network_probe(self):
        api = self._win32print(
            {
                "pPortName": "USB001",
                "Status": 0,
                "Attributes": 0,
                "pServerName": "",
            }
        )
        with patch.object(printer_status, "win32print", api), patch.object(
            printer_status, "resolve_tcp_endpoint"
        ) as resolve, patch.object(printer_status, "probe_tcp_endpoint") as probe:
            result = printer_status.check_printer("TSC USB")

        self.assertEqual(result.state, printer_status.STATE_AVAILABLE)
        self.assertEqual(result.connection, printer_status.CONNECTION_USB)
        resolve.assert_not_called()
        probe.assert_not_called()
        api.ClosePrinter.assert_called_once()

    def test_offline_spooler_state_blocks_before_tcp_probe(self):
        api = self._win32print(
            {
                "pPortName": "192.168.1.38",
                "Status": printer_status.PRINTER_STATUS_OFFLINE,
                "Attributes": 0,
                "pServerName": "",
            }
        )
        with patch.object(printer_status, "win32print", api), patch.object(
            printer_status, "resolve_tcp_endpoint"
        ) as resolve, patch.object(printer_status, "probe_tcp_endpoint") as probe:
            result = printer_status.check_printer("TSC Network")

        self.assertEqual(result.state, printer_status.STATE_UNAVAILABLE)
        self.assertIn("offline", result.detail)
        resolve.assert_not_called()
        probe.assert_not_called()

    def test_tcp_printer_is_available_when_configured_endpoint_accepts_connection(self):
        api = self._win32print(
            {
                "pPortName": "TSC-Production",
                "Status": 0,
                "Attributes": 0,
                "pServerName": "",
            }
        )
        endpoint = printer_status.TcpEndpoint("192.168.1.38", 9100)
        with patch.object(printer_status, "win32print", api), patch.object(
            printer_status, "resolve_tcp_endpoint", return_value=endpoint
        ) as resolve, patch.object(
            printer_status, "probe_tcp_endpoint", return_value=(True, "")
        ) as probe:
            result = printer_status.check_printer("TSC Network", timeout=0.2)

        self.assertEqual(result.state, printer_status.STATE_AVAILABLE)
        self.assertEqual(result.connection, printer_status.CONNECTION_TCP)
        self.assertEqual(result.host, "192.168.1.38")
        self.assertEqual(result.tcp_port, 9100)
        resolve.assert_called_once_with("TSC-Production")
        probe.assert_called_once_with(endpoint, timeout=0.2)

    def test_tcp_printer_is_unavailable_when_endpoint_refuses_connection(self):
        api = self._win32print(
            {
                "pPortName": "TSC-Production",
                "Status": 0,
                "Attributes": 0,
                "pServerName": "",
            }
        )
        endpoint = printer_status.TcpEndpoint("192.168.1.38", 9100)
        with patch.object(printer_status, "win32print", api), patch.object(
            printer_status, "resolve_tcp_endpoint", return_value=endpoint
        ), patch.object(
            printer_status,
            "probe_tcp_endpoint",
            return_value=(False, "connection refused"),
        ):
            result = printer_status.check_printer("TSC Network")

        self.assertEqual(result.state, printer_status.STATE_UNAVAILABLE)
        self.assertEqual(result.connection, printer_status.CONNECTION_TCP)
        self.assertIn("192.168.1.38:9100", result.detail)

    def test_wsd_network_port_is_unknown_instead_of_blocked(self):
        api = self._win32print(
            {
                "pPortName": "WSD-1234",
                "Status": 0,
                "Attributes": printer_status.PRINTER_ATTRIBUTE_NETWORK,
                "pServerName": "",
            }
        )
        with patch.object(printer_status, "win32print", api), patch.object(
            printer_status, "resolve_tcp_endpoint", return_value=None
        ):
            result = printer_status.check_printer("WSD Printer")

        self.assertEqual(result.state, printer_status.STATE_UNKNOWN)
        self.assertEqual(result.connection, printer_status.CONNECTION_NETWORK)

    def test_unc_shared_queue_is_classified_without_tcp_probe(self):
        api = self._win32print(
            {
                "pPortName": "Ne01:",
                "Status": 0,
                "Attributes": printer_status.PRINTER_ATTRIBUTE_NETWORK,
                "pServerName": "",
            }
        )
        with patch.object(printer_status, "win32print", api), patch.object(
            printer_status, "resolve_tcp_endpoint"
        ) as resolve:
            result = printer_status.check_printer(r"\\server\printer")

        self.assertEqual(result.state, printer_status.STATE_AVAILABLE)
        self.assertEqual(result.connection, printer_status.CONNECTION_SHARED)
        resolve.assert_not_called()

    def test_open_printer_failure_is_definitive_unavailable(self):
        api = Mock()
        api.OpenPrinter.side_effect = RuntimeError("queue missing")
        with patch.object(printer_status, "win32print", api):
            result = printer_status.check_printer("Missing Printer")

        self.assertEqual(result.state, printer_status.STATE_UNAVAILABLE)
        self.assertIn("queue missing", result.detail)

    def test_ip_named_port_falls_back_to_raw_9100(self):
        endpoint = printer_status._endpoint_from_port_name("IP_192.168.1.50")

        self.assertEqual(
            endpoint,
            printer_status.TcpEndpoint("192.168.1.50", 9100),
        )


if __name__ == "__main__":
    unittest.main()
