"""Unit tests for DeviceManager covering PRD Section 3.1 & 4.2."""

import unittest
from typing import Sequence

from shadowkreator.core.database import DatabaseManager
from shadowkreator.core.device_manager import AdbCommandResult, DeviceManager
from shadowkreator.core.models import ConnectionType, DeviceStatus
from shadowkreator.core.qr_pairing import encode_qr_matrix, generate_adb_wifi_qr_credentials


class FakeAdbRunner:
    """Deterministic fake ADB command runner for unit testing DeviceManager."""

    def __init__(self) -> None:
        self.devices_output = (
            "List of devices attached\n"
            "R58R123456A            device usb:1-1 product:a52qnsxx model:SM_A525F device:a52q transport_id:1\n"
            "192.168.1.25:41555     unauthorized transport_id:2\n"
            "192.168.1.99:5555      offline transport_id:3\n"
        )
        self.mdns_output = (
            "List of discovered mdns services\n"
            "adb-pixel7-abc123\t_adb-tls-connect._tcp.\t192.168.1.50:38888\n"
            "adb-galaxy-pair99\t_adb-tls-pairing._tcp.\t192.168.1.60:37111\n"
        )
        self.props = {
            ("R58R123456A", "ro.product.manufacturer"): "samsung",
            ("R58R123456A", "ro.product.model"): "SM-A525F",
            ("R58R123456A", "ro.product.marketname"): "Samsung Galaxy A52",
            ("R58R123456A", "ro.build.version.release"): "13",
            ("R58R123456A", "ro.build.version.sdk"): "33",
        }

    def __call__(self, cmd: Sequence[str], timeout: int = 10) -> AdbCommandResult:
        args = list(cmd)[1:]  # strip adb binary
        if args == ["version"]:
            return AdbCommandResult(0, "Android Debug Bridge version 1.0.41\n", "")
        if args == ["devices", "-l"]:
            return AdbCommandResult(0, self.devices_output, "")
        if args == ["mdns", "services"]:
            return AdbCommandResult(0, self.mdns_output, "")
        if len(args) >= 4 and args[0] == "-s" and args[2] == "shell" and args[3] == "getprop":
            serial = args[1]
            prop = args[4]
            return AdbCommandResult(0, self.props.get((serial, prop), ""), "")
        if len(args) == 3 and args[0] == "pair":
            host_port, code = args[1], args[2]
            if code == "123456":
                return AdbCommandResult(0, f"Successfully paired to {host_port} [guid=adb-123]\n", "")
            return AdbCommandResult(1, "", "Failed: Wrong password or connection was dropped.\n")
        if len(args) == 2 and args[0] == "connect":
            host_port = args[1]
            if host_port == "192.168.1.50:38888":
                self.devices_output += (
                    "192.168.1.50:38888     device model:Pixel_7 transport_id:4\n"
                )
                self.props[("192.168.1.50:38888", "ro.product.manufacturer")] = "Google"
                self.props[("192.168.1.50:38888", "ro.product.model")] = "Pixel 7"
                self.props[("192.168.1.50:38888", "ro.build.version.release")] = "14"
                self.props[("192.168.1.50:38888", "ro.build.version.sdk")] = "34"
                return AdbCommandResult(0, f"connected to {host_port}\n", "")
            return AdbCommandResult(1, f"failed to connect to '{host_port}': Connection refused\n", "")
        if len(args) == 2 and args[0] == "disconnect":
            return AdbCommandResult(0, f"disconnected {args[1]}\n", "")
        return AdbCommandResult(1, "", f"Unsupported command: {args}")


class TestDeviceManager(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DatabaseManager(":memory:")
        self.runner = FakeAdbRunner()
        self.manager = DeviceManager(db=self.db, adb_path="adb", runner=self.runner)

    def test_scan_devices_and_status_mapping(self) -> None:
        devices = self.manager.scan_devices()
        self.assertEqual(len(devices), 3)

        usb_dev = devices[0]
        self.assertEqual(usb_dev.device_serial, "R58R123456A")
        self.assertEqual(usb_dev.device_name, "Samsung Galaxy A52")
        self.assertEqual(usb_dev.android_version, "13")
        self.assertEqual(usb_dev.connection_type, ConnectionType.USB.value)
        self.assertEqual(usb_dev.connection_status, DeviceStatus.CONNECTED.value)
        self.assertTrue(usb_dev.is_ready_for_scraping)

        unauth_dev = devices[1]
        self.assertEqual(unauth_dev.connection_type, ConnectionType.WIRELESS.value)
        self.assertEqual(unauth_dev.connection_status, DeviceStatus.UNAUTHORIZED.value)
        self.assertFalse(unauth_dev.is_ready_for_scraping)

        offline_dev = devices[2]
        self.assertEqual(offline_dev.connection_status, DeviceStatus.OFFLINE.value)

    def test_wireless_pairing_and_connection(self) -> None:
        ok_bad, _ = self.manager.pair_wireless_device("192.168.1.50:37111", "000000")
        self.assertFalse(ok_bad)

        ok_pair, msg = self.manager.pair_wireless_device("192.168.1.50:37111", "123456")
        self.assertTrue(ok_pair)
        self.assertIn("Successfully paired", msg)

        ok_conn, _, dev = self.manager.connect_wireless_device("192.168.1.50:38888")
        self.assertTrue(ok_conn)
        self.assertEqual(dev.device_name, "Google Pixel 7")
        self.assertEqual(dev.android_version, "14")
        self.assertEqual(dev.connection_status, DeviceStatus.CONNECTED.value)

        # Refused connection maps to Pairing Required
        ok_refused, _, dev_refused = self.manager.connect_wireless_device("192.168.1.77:39999")
        self.assertFalse(ok_refused)
        self.assertEqual(dev_refused.connection_status, DeviceStatus.PAIRING_REQUIRED.value)

    def test_scan_wireless_devices_mdns_auto_discovery(self) -> None:
        devices, services = self.manager.scan_wireless_devices(auto_connect=True)
        self.assertEqual(len(services), 2)

        by_serial = {d.device_serial: d for d in devices}
        # Connect service 192.168.1.50:38888 was auto-connected via mDNS discovery
        self.assertIn("192.168.1.50:38888", by_serial)
        self.assertEqual(by_serial["192.168.1.50:38888"].connection_status, DeviceStatus.CONNECTED.value)

        # Pairing service 192.168.1.60:37111 was discovered with status Pairing Required
        self.assertIn("192.168.1.60:37111", by_serial)
        self.assertEqual(
            by_serial["192.168.1.60:37111"].connection_status,
            DeviceStatus.PAIRING_REQUIRED.value,
        )

    def test_adb_wifi_qr_payload_and_matrix(self) -> None:
        svc, pwd, payload = generate_adb_wifi_qr_credentials("studio-test1234", "654321")
        self.assertEqual(svc, "studio-test1234")
        self.assertEqual(pwd, "654321")
        self.assertEqual(payload, "WIFI:T:ADB;S:studio-test1234;P:654321;;")

        matrix = encode_qr_matrix(payload)
        self.assertEqual(len(matrix), 29)
        self.assertEqual(len(matrix[0]), 29)
        # Finder pattern top-left corner and center must be dark (True)
        self.assertTrue(matrix[0][0])
        self.assertTrue(matrix[3][3])

    def test_validate_device_for_scraping_rules(self) -> None:
        devices = self.manager.scan_devices()
        ok, _ = self.manager.validate_device_for_scraping(devices[0])
        self.assertTrue(ok)

        # Unauthorized should be rejected
        ok_unauth, msg_unauth = self.manager.validate_device_for_scraping(devices[1])
        self.assertFalse(ok_unauth)
        self.assertIn("Unauthorized", msg_unauth)

        # Android 11 (< 12) should be rejected per PRD Section 2.2
        old_dev = devices[0]
        old_dev.android_version = "11"
        old_dev.sdk_int = 30
        ok_old, msg_old = self.manager.validate_device_for_scraping(old_dev)
        self.assertFalse(ok_old)
        self.assertIn("Android 12+", msg_old)


if __name__ == "__main__":
    unittest.main()
