import unittest
from unittest.mock import patch

from vpsdash.execution import describe_doplet_terminal


class ExecutionTests(unittest.TestCase):
    def test_windows_local_terminal_prefers_ssh_when_ip_is_discovered(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {"slug": "builder-01", "name": "Builder 01", "status": "running", "bootstrap_user": "ubuntu", "ip_addresses": []}
        with patch("vpsdash.execution.run_host_local_command", return_value={"ok": True, "stdout": " vnet0 ipv4 192.168.122.77/24\n", "stderr": ""}):
            details = describe_doplet_terminal(host, doplet)
        self.assertTrue(details["supported"])
        self.assertEqual(details["transport"], "ssh")
        self.assertEqual(details["ip_addresses"], ["192.168.122.77"])
        self.assertIn("ubuntu@192.168.122.77", details["access_label"])
        self.assertIn("WSL/libvirt network", details["access_note"])

    def test_windows_local_terminal_uses_matching_local_private_key_when_present(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {
            "slug": "builder-01",
            "name": "Builder 01",
            "status": "running",
            "bootstrap_user": "ubuntu",
            "ip_addresses": ["192.168.122.77"],
            "metadata_json": {"local_private_key_path": r"C:\Users\tester\.ssh\id_ed25519"},
        }
        details = describe_doplet_terminal(host, doplet)
        self.assertEqual(details["transport"], "ssh")
        self.assertIn("/mnt/c/Users/tester/.ssh/id_ed25519", details["preview_command"])
        self.assertIn("mktemp /tmp/vpsdash-key-", details["preview_command"])
        self.assertIn("IdentitiesOnly=yes", details["preview_command"])

    def test_windows_local_terminal_uses_console_when_no_ip_is_available(self) -> None:
        host = {"mode": "windows-local", "wsl_distribution": "Ubuntu"}
        doplet = {"slug": "builder-01", "name": "Builder 01", "status": "running", "bootstrap_user": "ubuntu", "ip_addresses": []}
        with patch("vpsdash.execution.run_host_local_command", return_value={"ok": True, "stdout": "", "stderr": ""}):
            details = describe_doplet_terminal(host, doplet)
        self.assertTrue(details["supported"])
        self.assertEqual(details["transport"], "virsh-console")
        self.assertIn("Console", details["access_label"])


if __name__ == "__main__":
    unittest.main()
