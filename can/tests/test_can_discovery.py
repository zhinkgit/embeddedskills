from contextlib import ExitStack
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import can_runtime
import can_scan


class CanDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {"can": types.ModuleType("can")}))
        self.usb = self.stack.enter_context(patch.object(can_runtime, "scan_usb_can_devices", return_value=[]))
        self.socketcan = self.stack.enter_context(patch.object(can_runtime, "scan_socketcan", return_value=[]))
        self.stack.enter_context(patch.object(can_runtime, "check_interface_available", return_value=True))

    def test_registered_backends_are_not_detected_devices(self):
        interfaces, error = can_runtime.scan_can_interfaces()
        self.assertIsNone(error)
        self.assertEqual(interfaces, [])
        self.assertIn("virtual", can_runtime.list_registered_backends())

    def test_scan_json_separates_backends_from_device_count(self):
        with patch.object(sys, "argv", ["can_scan.py", "--json"]), patch.object(can_scan, "output_json") as output:
            can_scan.main()
        result = output.call_args.args[0]
        self.assertEqual(result["summary"], "发现 0 个 CAN 接口")
        self.assertEqual(result["details"]["interfaces"], [])
        self.assertIn("virtual", result["details"]["registered_backends"])

    def test_automatic_selection_ignores_registered_backends(self):
        self.socketcan.return_value = [{"interface": "socketcan", "channel": "can0", "status": "up"}]
        with patch.object(can_runtime, "load_local_config", return_value={}), patch.object(
            can_runtime, "load_project_config", return_value={}
        ), patch.object(can_runtime, "load_workspace_state", return_value={}), patch.object(can_runtime, "save_project_config") as save:
            config, sources = can_runtime.get_can_config()
        self.assertEqual((config["interface"], config["channel"]), ("socketcan", "can0"))
        self.assertEqual(sources["channel"], "auto_scan")
        save.assert_called_once()

    def test_no_device_does_not_write_an_empty_channel(self):
        with patch.object(can_runtime, "load_local_config", return_value={}), patch.object(
            can_runtime, "load_project_config", return_value={}
        ), patch.object(can_runtime, "load_workspace_state", return_value={}), patch.object(can_runtime, "save_project_config") as save:
            config, sources = can_runtime.get_can_config()
        self.assertIsNone(config)
        self.assertIn("未找到", sources["error"])
        save.assert_not_called()

    def test_usb_and_socketcan_devices_are_preserved_as_candidates(self):
        self.usb.return_value = [{"interface": "pcan", "channel": "PCAN_USBBUS1", "name": "PCAN", "vid": "0C72", "pid": "000C"}]
        self.socketcan.return_value = [{"interface": "socketcan", "channel": "can0", "status": "up"}]
        interfaces, error = can_runtime.scan_can_interfaces()
        self.assertIsNone(error)
        self.assertEqual([item["channel"] for item in interfaces], ["PCAN_USBBUS1", "can0"])


if __name__ == "__main__":
    unittest.main()
