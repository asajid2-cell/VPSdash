import shutil
import tempfile
import unittest
from pathlib import Path

import vpsdash.service as service_module
from vpsdash.service import VpsDashService


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_root = Path(__file__).resolve().parents[1]

    def _isolated_root(self) -> Path:
        temp_root = Path(tempfile.mkdtemp())
        shutil.copytree(self.fixture_root / "templates", temp_root / "templates")
        shutil.copytree(self.fixture_root / "defaults", temp_root / "defaults")
        return temp_root

    def test_bootstrap_loads_file_backed_builtin_defaults(self) -> None:
        root = self._isolated_root()
        try:
            service = VpsDashService(root)
            bootstrap = service.bootstrap()
            default_ids = {item["id"] for item in bootstrap["defaults"] if item.get("kind") == "builtin"}
            self.assertIn("builtin-generic-remote-bootstrap", default_ids)
            self.assertNotIn("builtin-operator-remote-bootstrap", default_ids)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_custom_default_persists_host_bootstrap_metadata(self) -> None:
        root = self._isolated_root()
        try:
            service = VpsDashService(root)
            saved = service.upsert_default(
                {
                    "name": "My Remote Default",
                    "description": "Custom remote bootstrap preset",
                    "host_defaults": {
                        "name": "Computer B",
                        "mode": "remote-linux",
                        "device_role": "computer-b-server",
                        "bootstrap_auth": "password-bootstrap",
                        "ssh_user": "ops",
                        "ssh_host": "192.168.1.77",
                    },
                    "project_defaults": {
                        "template_id": "generic-docker-webapp",
                        "name": "Custom App",
                        "repo_url": "https://example.com/app.git",
                        "deploy_path": "~/apps/custom-app",
                    },
                }
            )

            bootstrap = service.bootstrap()
            saved_default = next(
                item for item in bootstrap["defaults"] if item.get("id") == saved["default"]["id"]
            )

            self.assertEqual(saved_default["kind"], "custom")
            self.assertEqual(saved_default["host_defaults"]["device_role"], "computer-b-server")
            self.assertEqual(saved_default["host_defaults"]["bootstrap_auth"], "password-bootstrap")
            self.assertEqual(saved_default["host_defaults"]["ssh_host"], "192.168.1.77")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_instance_lifecycle_persists_and_deletes(self) -> None:
        root = self._isolated_root()
        try:
            service = VpsDashService(root)
            response = service.upsert_instance(
                {
                    "name": "Local Sample",
                    "host": {"name": "Local Host", "mode": "linux-local"},
                    "project": {
                        "template_id": "generic-docker-webapp",
                        "name": "Sample App",
                        "repo_url": "https://example.com/app.git",
                        "deploy_path": "~/apps/sample-app",
                    },
                }
            )

            instance_id = response["instance"]["id"]
            self.assertTrue(any(item["id"] == instance_id for item in response["state"]["instances"]))

            deleted = service.delete_instance(instance_id)
            self.assertFalse(any(item["id"] == instance_id for item in deleted["state"]["instances"]))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_create_instance_backup_records_history(self) -> None:
        root = self._isolated_root()
        original_execute_plan = service_module.execute_plan
        try:
            service = VpsDashService(root)
            response = service.upsert_instance(
                {
                    "name": "Backup Sample",
                    "host": {"name": "Local Host", "mode": "linux-local"},
                    "project": {
                        "template_id": "generic-docker-webapp",
                        "name": "Sample App",
                        "repo_url": "https://example.com/app.git",
                        "deploy_path": "~/apps/sample-app",
                    },
                }
            )

            service_module.execute_plan = lambda host, steps, dry_run=False: [
                {
                    "title": steps[0]["title"],
                    "ok": True,
                    "stdout": steps[0].get("artifact_path", ""),
                    "stderr": "",
                    "command": steps[0]["command"],
                }
            ]

            backup_response = service.create_instance_backup(response["instance"]["id"])
            self.assertEqual(backup_response["backup"]["status"], "ok")
            self.assertTrue(backup_response["backup"]["artifact_path"].startswith("~/backups/"))
            self.assertEqual(len(backup_response["instance"]["backups"]), 1)
        finally:
            service_module.execute_plan = original_execute_plan
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

