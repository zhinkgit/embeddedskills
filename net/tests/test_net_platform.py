import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import net_ping
import net_iface
import net_runtime


class PingTests(unittest.TestCase):
    def test_linux_loopback_reply_and_fractional_rtt(self):
        output = "1 packets transmitted, 1 received, 0% packet loss\nrtt min/avg/max/mdev = 0.020/0.031/0.040/0.010 ms"
        with patch.object(net_ping.platform, "system", return_value="Linux"), patch.object(
            net_ping.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
        ) as run:
            result = net_ping.icmp_ping("127.0.0.1", count=1, timeout_ms=1500)
        self.assertTrue(result["reachable"])
        self.assertEqual((result["sent"], result["received"], result["avg_ms"]), (1, 1, 0.031))
        self.assertEqual(run.call_args.args[0], ["ping", "-n", "-c", "1", "-W", "2", "127.0.0.1"])
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_windows_statistics_remain_supported(self):
        for output in (
            "Packets: Sent = 4, Received = 3, Lost = 1 (25% loss),\nMinimum = 1ms, Maximum = 4ms, Average = 2ms",
            "数据包: 已发送 = 4，已接收 = 3，丢失 = 1 (25% 丢失)，\n最短 = 1ms，最长 = 4ms，平均 = 2ms",
        ):
            with self.subTest(output=output), patch.object(net_ping.platform, "system", return_value="Windows"), patch.object(
                net_ping.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
            ) as run:
                result = net_ping.icmp_ping("127.0.0.1")
            self.assertTrue(result["reachable"])
            self.assertEqual(result["loss_rate"], "25%")
            self.assertEqual(result["avg_ms"], 2)
            self.assertEqual(run.call_args.args[0], ["ping", "-n", "4", "-w", "1000", "127.0.0.1"])

    def test_packet_loss_is_a_valid_measurement(self):
        with patch.object(net_ping.platform, "system", return_value="Linux"), patch.object(
            net_ping.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "4 packets transmitted, 0 received, 100% packet loss", "")
        ):
            result = net_ping.icmp_ping("192.0.2.1")
        self.assertFalse(result["reachable"])
        self.assertEqual(result["loss_rate"], "100%")
        self.assertNotIn("error", result)

    def test_command_errors_are_not_reported_as_packet_loss(self):
        for response in (
            subprocess.CompletedProcess([], 2, "", "ping: operation not permitted"),
            subprocess.CompletedProcess([], 0, "unexpected output", ""),
        ):
            with self.subTest(response=response), patch.object(net_ping.platform, "system", return_value="Linux"), patch.object(
                net_ping.subprocess, "run", return_value=response
            ):
                self.assertIn("error", net_ping.icmp_ping("127.0.0.1"))

    def test_missing_or_timed_out_ping_is_an_error(self):
        for error in (FileNotFoundError("ping not found"), subprocess.TimeoutExpired("ping", 10)):
            with self.subTest(error=error), patch.object(net_ping.subprocess, "run", side_effect=error):
                self.assertIn("error", net_ping.icmp_ping("127.0.0.1"))

    def test_traceroute_uses_platform_command_and_parses_hops(self):
        for system, command in (("Linux", "traceroute"), ("Windows", "tracert")):
            with self.subTest(system=system), patch.object(net_ping.platform, "system", return_value=system), patch.object(
                net_ping.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, " 1  127.0.0.1  0.1 ms\n", "")
            ) as run:
                result = net_ping.traceroute("127.0.0.1", timeout_ms=1500)
            self.assertTrue(result["reachable"])
            self.assertEqual(len(result["hops"]), 1)
            self.assertEqual(run.call_args.args[0][0], command)
            self.assertEqual(run.call_args.args[0][3], "1.5" if system == "Linux" else "1500")

    def test_missing_traceroute_and_nonzero_exit_are_errors(self):
        with patch.object(net_ping.subprocess, "run", side_effect=FileNotFoundError("missing")):
            self.assertIn("error", net_ping.traceroute("127.0.0.1"))
        with patch.object(net_ping.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "permission denied")):
            self.assertEqual(net_ping.traceroute("127.0.0.1")["error"], "permission denied")

    def test_other_platform_is_explicitly_unsupported(self):
        with patch.object(net_ping.platform, "system", return_value="Darwin"):
            self.assertIn("error", net_ping.icmp_ping("127.0.0.1"))
            self.assertIn("error", net_ping.traceroute("127.0.0.1"))

    def test_cli_command_failure_returns_error_and_does_not_update_state(self):
        with patch.object(sys, "argv", ["net_ping.py", "--json"]), patch.object(
            net_ping, "get_net_config", return_value=({"target": "127.0.0.1", "timeout_ms": 1000}, {})
        ), patch.object(net_ping, "save_project_config"), patch.object(
            net_ping, "icmp_ping", return_value={"target": "127.0.0.1", "reachable": False, "error": "ping missing"}
        ), patch.object(net_ping, "update_state_entry") as update, patch("builtins.print") as output:
            with self.assertRaises(SystemExit) as stopped:
                net_ping.main()
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(json.loads(output.call_args.args[0])["status"], "error")
        update.assert_not_called()


class InterfaceTests(unittest.TestCase):
    def test_linux_addresses_masks_gateways_and_down_interfaces(self):
        entries = [
            {"ifname": "lo", "flags": ["UP", "LOOPBACK"], "operstate": "UNKNOWN", "link_type": "loopback", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
            {"ifname": "eth0", "flags": ["UP"], "operstate": "UP", "address": "00:11:22:33:44:55", "addr_info": [
                {"family": "inet", "local": "192.0.2.2", "prefixlen": 24, "dynamic": True},
                {"family": "inet", "local": "198.51.100.2", "prefixlen": 16},
                {"family": "inet6", "local": "::1", "prefixlen": 128},
            ]},
            {"ifname": "eth1", "flags": [], "operstate": "DOWN", "addr_info": []},
        ]
        responses = [subprocess.CompletedProcess([], 0, json.dumps(data), "") for data in (
            entries, [{"dev": "eth0", "gateway": "192.0.2.1"}],
        )]
        with patch.object(net_runtime.platform, "system", return_value="Linux"), patch.object(net_runtime.subprocess, "run", side_effect=responses) as run:
            interfaces = net_runtime.list_network_interfaces()
        self.assertEqual(len(interfaces), 3)
        self.assertEqual(interfaces[0]["status"], "up")
        self.assertEqual(interfaces[1]["ipv4_list"], ["192.0.2.2", "198.51.100.2"])
        self.assertEqual(interfaces[1]["subnet_list"], ["255.255.255.0", "255.255.0.0"])
        self.assertEqual(interfaces[1]["gateway"], "192.0.2.1")
        self.assertEqual(interfaces[2]["status"], "down")
        self.assertEqual(run.call_args_list[0].args[0], ["ip", "-j", "addr"])

    def test_windows_ipconfig_is_preserved(self):
        output = "Ethernet adapter Ethernet:\n   IPv4 Address. . . . . . . . . . . : 192.0.2.2\n   Subnet Mask . . . . . . . . . . . : 255.255.255.0\n   Default Gateway . . . . . . . . . : 192.0.2.1\n"
        with patch.object(net_runtime.platform, "system", return_value="Windows"), patch.object(
            net_runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
        ):
            interfaces = net_runtime.list_network_interfaces()
        self.assertEqual(interfaces[0]["name"], "Ethernet")
        self.assertEqual(interfaces[0]["ipv4"], "192.0.2.2")

    def test_missing_failed_and_malformed_commands_are_not_empty_success(self):
        for system in ("Linux", "Windows"):
            with self.subTest(system=system), patch.object(net_runtime.platform, "system", return_value=system):
                with patch.object(net_runtime.subprocess, "run", side_effect=FileNotFoundError("missing")):
                    with self.assertRaises(OSError):
                        net_runtime.list_network_interfaces()
                with patch.object(net_runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "failed")):
                    with self.assertRaises(RuntimeError):
                        net_runtime.list_network_interfaces()
        with patch.object(net_runtime.platform, "system", return_value="Linux"), patch.object(
            net_runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "not json", "")
        ):
            with self.assertRaises(ValueError):
                net_runtime.list_network_interfaces()

    def test_cli_missing_interface_tool_returns_error(self):
        with patch.object(sys, "argv", ["net_iface.py", "--json"]), patch.object(
            net_iface, "list_network_interfaces", side_effect=FileNotFoundError("ip missing")
        ), patch("builtins.print") as output:
            with self.assertRaises(SystemExit) as stopped:
                net_iface.main()
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(json.loads(output.call_args.args[0])["error"]["code"], "interface_scan_failed")


@unittest.skipUnless(platform.system() in {"Windows", "Linux"}, "requires Windows or Linux")
class LocalNetworkSmokeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ping"), "ping is not installed")
    def test_real_loopback_ping(self):
        result = net_ping.icmp_ping("127.0.0.1", count=1)
        self.assertTrue(result["reachable"], result)
        self.assertEqual(result["received"], 1)

    @unittest.skipUnless(shutil.which("ipconfig" if platform.system() == "Windows" else "ip"), "interface tool is not installed")
    def test_real_interface_listing(self):
        self.assertTrue(net_runtime.list_network_interfaces())

    @unittest.skipUnless(shutil.which("tracert" if platform.system() == "Windows" else "traceroute"), "traceroute tool is not installed")
    def test_real_loopback_traceroute(self):
        result = net_ping.traceroute("127.0.0.1")
        self.assertTrue(result.get("reachable"), result)


if __name__ == "__main__":
    unittest.main()
