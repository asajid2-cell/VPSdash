import unittest
from unittest.mock import patch

from vpsdash.execution import describe_doplet_terminal


class ExecutionTests(unittest.TestCase):
    def test_windows_local_terminal_prefers_ssh_when_ip_is_discovered(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {"id": 41, "slug": "builder-01", "name": "Builder 01", "status": "running", "bootstrap_user": "ubuntu", "ip_addresses": []}
        with (
            patch("vpsdash.execution.run_host_local_command", return_value={"ok": True, "stdout": " vnet0 ipv4 192.168.122.77/24\n", "stderr": ""}),
            patch("vpsdash.execution._guest_ssh_is_reachable", return_value=True),
            patch("vpsdash.execution._ensure_windows_local_ssh_proxy", return_value=22041),
        ):
            details = describe_doplet_terminal(host, doplet)
        self.assertTrue(details["supported"])
        self.assertEqual(details["transport"], "ssh")
        self.assertEqual(details["ip_addresses"], ["192.168.122.77"])
        self.assertIn("ubuntu@127.0.0.1:22041", details["access_label"])
        self.assertIn("Forwarded to guest IP 192.168.122.77:22", details["access_note"])
        self.assertIn("127.0.0.1", details["preview_command"])

    def test_windows_local_terminal_uses_matching_local_private_key_when_present(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {
            "id": 41,
            "slug": "builder-01",
            "name": "Builder 01",
            "status": "running",
            "bootstrap_user": "ubuntu",
            "ip_addresses": ["192.168.122.77"],
            "metadata_json": {"local_private_key_path": r"C:\Users\tester\.ssh\id_ed25519"},
        }
        with (
            patch("vpsdash.execution._guest_ssh_is_reachable", return_value=True),
            patch("vpsdash.execution._ensure_windows_local_ssh_proxy", return_value=22041),
        ):
            details = describe_doplet_terminal(host, doplet)
        self.assertEqual(details["transport"], "ssh")
        self.assertIn("127.0.0.1", details["preview_command"])
        self.assertIn(r"C:\Users\tester\.ssh\id_ed25519", details["preview_command"])
        self.assertIn("IdentitiesOnly=yes", details["preview_command"])

    def test_windows_local_terminal_uses_console_when_no_ip_is_available(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {"slug": "builder-01", "name": "Builder 01", "status": "running", "bootstrap_user": "ubuntu", "ip_addresses": []}
        with patch("vpsdash.execution.run_host_local_command", return_value={"ok": True, "stdout": "", "stderr": ""}):
            details = describe_doplet_terminal(host, doplet)
        self.assertTrue(details["supported"])
        self.assertEqual(details["transport"], "virsh-console")
        self.assertIn("Console", details["access_label"])

    def test_windows_local_terminal_falls_back_to_console_when_ssh_is_not_reachable(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {"id": 9, "slug": "builder-01", "name": "Builder 01", "status": "running", "bootstrap_user": "ubuntu", "ip_addresses": ["192.168.122.77"]}
        with patch("vpsdash.execution._guest_ssh_is_reachable", return_value=False):
            details = describe_doplet_terminal(host, doplet)
        self.assertEqual(details["transport"], "virsh-console")
        self.assertIn("SSH is not reachable", details["access_note"])

    def test_windows_local_terminal_falls_back_to_wsl_ssh_when_localhost_endpoint_fails(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {
            "id": 41,
            "slug": "builder-01",
            "name": "Builder 01",
            "status": "running",
            "bootstrap_user": "ubuntu",
            "ip_addresses": ["192.168.122.77"],
            "metadata_json": {"local_private_key_path": r"C:\Users\tester\.ssh\id_ed25519"},
        }
        with (
            patch("vpsdash.execution._guest_ssh_is_reachable", return_value=True),
            patch("vpsdash.execution._ensure_windows_local_ssh_proxy", side_effect=RuntimeError("bridge failed")),
        ):
            details = describe_doplet_terminal(host, doplet)
        self.assertEqual(details["launcher"], "windows-wsl")
        self.assertIn("Localhost SSH endpoint is unavailable", details["access_note"])
        self.assertIn("wsl.exe", details["preview_command"])
        self.assertIn("/mnt/c/Users/tester/.ssh/id_ed25519", details["preview_command"])

    def test_windows_local_terminal_info_mode_does_not_try_to_start_bridge(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {
            "id": 41,
            "slug": "builder-01",
            "name": "Builder 01",
            "status": "running",
            "bootstrap_user": "ubuntu",
            "ip_addresses": ["192.168.122.77"],
            "metadata_json": {"local_private_key_path": r"C:\Users\tester\.ssh\id_ed25519"},
        }
        with (
            patch("vpsdash.execution._guest_ssh_is_reachable", return_value=True),
            patch("vpsdash.execution._ensure_windows_local_ssh_proxy") as ensure_proxy,
            patch("vpsdash.execution._windows_local_proxy_is_ready", return_value=False),
            patch("vpsdash.execution._windows_local_native_port_is_ready", return_value=False),
        ):
            details = describe_doplet_terminal(host, doplet, establish_localhost_endpoint=False)
        ensure_proxy.assert_not_called()
        self.assertEqual(details["launcher"], "windows-wsl")
        self.assertEqual(details["forward_port"], 22041)
        self.assertEqual(details["forward_host"], "127.0.0.1")
        self.assertIn("Use Open Terminal to let VPSdash try a localhost endpoint", details["access_note"])

    def test_terminal_description_prefers_freshly_discovered_ip_over_stale_cached_ip(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {
            "id": 41,
            "slug": "builder-01",
            "name": "Builder 01",
            "status": "running",
            "bootstrap_user": "ubuntu",
            "ip_addresses": ["192.168.122.116"],
            "metadata_json": {"local_private_key_path": r"C:\Users\tester\.ssh\id_ed25519"},
        }
        with (
            patch("vpsdash.execution._discover_doplet_ip_addresses", return_value=["192.168.122.219"]),
            patch("vpsdash.execution._guest_ssh_is_reachable", return_value=True),
            patch("vpsdash.execution._ensure_windows_local_ssh_proxy", return_value=22041),
        ):
            details = describe_doplet_terminal(host, doplet)
        self.assertEqual(details["ip_addresses"], ["192.168.122.219"])
        self.assertIn("192.168.122.219", details["access_note"])


if __name__ == "__main__":
    unittest.main()
