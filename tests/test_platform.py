import json
import re
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from vpsdash.app import create_app
from vpsdash.host_agent import HostAgent
from vpsdash.platform_service import PlatformService
from vpsdash.security import sign_json_payload


class PlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_root = Path(__file__).resolve().parents[1]

    def _isolated_root(self) -> Path:
        temp_root = Path(tempfile.mkdtemp())
        for folder in ["templates", "defaults", "templates_web", "static"]:
            shutil.copytree(self.fixture_root / folder, temp_root / folder)
        return temp_root

    def _login_with_csrf(self, client, username: str, password: str) -> None:
        client.get("/login")
        with client.session_transaction() as session:
            csrf_token = session["csrf_token"]
        response = client.post(
            "/login",
            data={
                "csrf_token": csrf_token,
                "username": username,
                "password": password,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        if "/verify" in response.headers.get("Location", ""):
            outbox_files = sorted(self._current_root.joinpath("data", "outbox").glob("*.json"))
            payload = json.loads(outbox_files[0].read_text(encoding="utf-8"))
            code = re.search(r"(\d{6})", payload["body"]).group(1)
            with client.session_transaction() as session:
                verify_csrf = session["csrf_token"]
            verify_response = client.post(
                "/verify",
                data={
                    "csrf_token": verify_csrf,
                    "code": code,
                },
                follow_redirects=False,
            )
            self.assertEqual(verify_response.status_code, 302)

    def test_capacity_blocks_overallocation(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host(
                {
                    "name": "Host A",
                    "inventory": {
                        "resources": {
                            "cpu_threads_total": 4,
                            "ram_mb_total": 4096,
                            "disk_total_gib": 40.0,
                            "gpu_device_count": 0,
                        }
                    },
                }
            )
            service.upsert_doplet(
                {
                    "name": "VM1",
                    "host_id": host["id"],
                    "vcpu": 2,
                    "ram_mb": 1024,
                    "disk_gb": 10,
                }
            )
            with self.assertRaises(ValueError):
                service.upsert_doplet(
                    {
                        "name": "VM2",
                        "host_id": host["id"],
                        "vcpu": 3,
                        "ram_mb": 3072,
                        "disk_gb": 40,
                    }
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_existing_sqlite_state_migrates_from_harminoplet_schema(self) -> None:
        root = self._isolated_root()
        try:
            data_dir = root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "vpsdash.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE host_nodes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        slug TEXT,
                        name TEXT,
                        host_mode TEXT,
                        distro TEXT,
                        exposure_mode TEXT,
                        primary_storage_backend TEXT,
                        ssh_host TEXT,
                        ssh_user TEXT,
                        ssh_port INTEGER,
                        wsl_distribution TEXT,
                        mixed_use_allowed INTEGER,
                        mixed_use_warning_acknowledged INTEGER,
                        status TEXT,
                        inventory TEXT,
                        warnings TEXT,
                        notes TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE harminoplets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        slug TEXT,
                        name TEXT,
                        host_id INTEGER,
                        image_id INTEGER,
                        flavor_id INTEGER,
                        status TEXT,
                        vcpu INTEGER,
                        ram_mb INTEGER,
                        disk_gb INTEGER,
                        primary_network_id INTEGER,
                        network_ids TEXT,
                        ip_addresses TEXT,
                        storage_backend TEXT,
                        security_tier TEXT,
                        exposure_mode TEXT,
                        bootstrap_user TEXT,
                        ssh_public_keys TEXT,
                        gpu_assignments TEXT,
                        metadata_json TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE backup_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        harminoplet_id INTEGER,
                        provider_id INTEGER,
                        backup_type TEXT,
                        status TEXT,
                        artifact_reference TEXT,
                        manifest TEXT,
                        size_bytes INTEGER,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE snapshot_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        harminoplet_id INTEGER,
                        name TEXT,
                        status TEXT,
                        artifact_reference TEXT,
                        metadata_json TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_type TEXT,
                        target_type TEXT,
                        target_id TEXT,
                        status TEXT,
                        progress INTEGER,
                        command_plan TEXT,
                        result_payload TEXT,
                        log_output TEXT,
                        requested_by_user_id INTEGER,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE audit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        actor TEXT,
                        action TEXT,
                        target_type TEXT,
                        target_id TEXT,
                        summary TEXT,
                        details TEXT,
                        created_at TEXT
                    );
                    INSERT INTO host_nodes (
                        id, slug, name, host_mode, distro, exposure_mode, primary_storage_backend,
                        ssh_host, ssh_user, ssh_port, wsl_distribution, mixed_use_allowed,
                        mixed_use_warning_acknowledged, status, inventory, warnings, notes, created_at, updated_at
                    ) VALUES (
                        1, 'legacy-host', 'Legacy Host', 'windows-local', 'ubuntu-server-lts', 'lan-vpn-only', 'files',
                        '', '', 22, 'Ubuntu', 0, 0, 'ready', '{}', '[]', '', '2026-03-21T00:00:00+00:00', '2026-03-21T00:00:00+00:00'
                    );
                    INSERT INTO harminoplets (
                        id, slug, name, host_id, image_id, flavor_id, status, vcpu, ram_mb, disk_gb,
                        primary_network_id, network_ids, ip_addresses, storage_backend, security_tier,
                        exposure_mode, bootstrap_user, ssh_public_keys, gpu_assignments, metadata_json, created_at, updated_at
                    ) VALUES (
                        1, 'legacy-vm', 'Legacy VM', 1, NULL, NULL, 'running', 1, 1024, 20,
                        NULL, '[]', '[]', 'files', 'standard', 'lan-vpn-only', 'ubuntu', '[]', '[]', '{}',
                        '2026-03-21T00:00:00+00:00', '2026-03-21T00:00:00+00:00'
                    );
                    INSERT INTO backup_records (
                        id, harminoplet_id, provider_id, backup_type, status, artifact_reference, manifest, size_bytes, created_at, updated_at
                    ) VALUES (
                        1, 1, NULL, 'manual', 'complete', '', '{}', 0, '2026-03-21T00:00:00+00:00', '2026-03-21T00:00:00+00:00'
                    );
                    INSERT INTO snapshot_records (
                        id, harminoplet_id, name, status, artifact_reference, metadata_json, created_at, updated_at
                    ) VALUES (
                        1, 1, 'snap-1', 'complete', '', '{}', '2026-03-21T00:00:00+00:00', '2026-03-21T00:00:00+00:00'
                    );
                    INSERT INTO tasks (
                        id, task_type, target_type, target_id, status, progress, command_plan, result_payload, log_output, requested_by_user_id, created_at, updated_at
                    ) VALUES (
                        1, 'create-harminoplet', 'harminoplet', '1', 'complete', 100, '[]', '{}', '', NULL, '2026-03-21T00:00:00+00:00', '2026-03-21T00:00:00+00:00'
                    );
                    INSERT INTO audit_events (
                        id, actor, action, target_type, target_id, summary, details, created_at
                    ) VALUES (
                        1, 'system', 'harminoplet.create', 'harminoplet', '1', 'legacy', '{}', '2026-03-21T00:00:00+00:00'
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()

            service = PlatformService(root)
            bootstrap = service.bootstrap()
            self.assertEqual(bootstrap["counts"]["doplets"], 1)
            self.assertEqual(bootstrap["counts"]["snapshots"], 1)
            self.assertEqual(bootstrap["counts"]["tasks"], 1)
            self.assertEqual(bootstrap["doplets"][0]["slug"], "legacy-vm")
            self.assertEqual(bootstrap["tasks"][0]["target_type"], "doplet")
            self.assertIn("doplet", bootstrap["audit"][0]["action"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_upsert_host_reuses_existing_derived_slug_instead_of_inserting_duplicate(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            first = service.upsert_host(
                {
                    "name": "CrackerBarrel",
                    "host_mode": "windows-local",
                    "distro": "ubuntu-server-lts",
                    "primary_storage_backend": "files",
                    "status": "draft",
                    "ssh_port": 22,
                    "wsl_distribution": "Ubuntu",
                }
            )
            second = service.upsert_host(
                {
                    "name": "CrackerBarrel",
                    "host_mode": "windows-local",
                    "distro": "ubuntu-server-lts",
                    "primary_storage_backend": "files",
                    "status": "draft",
                    "ssh_port": 22,
                    "wsl_distribution": "Ubuntu",
                }
            )
            bootstrap = service.bootstrap()
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(bootstrap["hosts"]), 1)
            self.assertEqual(bootstrap["hosts"][0]["slug"], "crackerbarrel")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_windows_local_create_plan_stays_local_even_when_ssh_target_is_saved(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            image = service.bootstrap()["images"][0]
            host = service.upsert_host(
                {
                    "name": "Windows WSL Host",
                    "host_mode": "windows-local",
                    "status": "ready",
                    "ssh_host": "192.168.1.44",
                    "ssh_user": "ahmed",
                    "wsl_distribution": "Ubuntu",
                    "inventory": {
                        "resources": {
                            "cpu_threads_total": 8,
                            "ram_mb_total": 16384,
                            "disk_total_gib": 200.0,
                            "disk_total_bytes": 200 * 1024 * 1024 * 1024,
                            "virtualization_ready": True,
                            "iommu_groups": 0,
                        }
                    },
                }
            )
            doplet = service.upsert_doplet(
                {
                    "name": "Local Builder",
                    "host_id": host["id"],
                    "image_id": image["id"],
                    "vcpu": 1,
                    "ram_mb": 1024,
                    "disk_gb": 20,
                }
            )
            task = service.queue_doplet_create(doplet["id"])
            self.assertTrue(task["command_plan"])
            self.assertTrue(all(step.get("run_mode") == "local" for step in task["command_plan"]))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_doplet_slug_is_made_unique_and_can_be_updated(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host({"name": "Host A"})
            first = service.upsert_doplet(
                {
                    "name": "New Doplet",
                    "slug": "doplet",
                    "host_id": host["id"],
                }
            )
            second = service.upsert_doplet(
                {
                    "name": "New Doplet",
                    "slug": "doplet",
                    "host_id": host["id"],
                }
            )
            self.assertEqual(first["slug"], "doplet")
            self.assertEqual(second["slug"], "doplet-2")

            updated = service.upsert_doplet(
                {
                    "id": second["id"],
                    "name": "Builder Alpha",
                    "slug": "builder-alpha",
                    "host_id": host["id"],
                }
            )
            self.assertEqual(updated["slug"], "builder-alpha")
            self.assertEqual(updated["name"], "Builder Alpha")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_force_delete_soft_archives_doplet_and_hides_it_from_live_bootstrap(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host(
                {
                    "name": "Host A",
                    "inventory": {"resources": {"cpu_threads_total": 8, "ram_mb_total": 16384, "disk_total_gib": 200.0}},
                }
            )
            doplet = service.upsert_doplet(
                {
                    "name": "Delete Me",
                    "host_id": host["id"],
                    "vcpu": 1,
                    "ram_mb": 1024,
                    "disk_gb": 20,
                }
            )
            pending = service.queue_doplet_lifecycle(doplet["id"], "start")
            result = service.force_delete_doplet(doplet["id"], actor="test")
            self.assertEqual(result["doplet"]["status"], "deleted")
            self.assertIn(int(pending["id"]), result["cancelled_task_ids"])

            bootstrap = service.bootstrap()
            self.assertEqual([item["id"] for item in bootstrap["doplets"] if item["id"] == doplet["id"]], [])
            archived = next(item for item in bootstrap["archived_doplets"] if item["id"] == doplet["id"])
            self.assertEqual(archived["status"], "deleted")
            self.assertTrue(archived["deleted_at"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_run_task_dry_run_preserves_target_status(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host(
                {
                    "name": "Host A",
                    "status": "draft",
                    "inventory": {"resources": {"cpu_threads_total": 8, "ram_mb_total": 16384, "disk_total_gib": 200.0}},
                }
            )
            task = service.queue_prepare_host(host["id"])
            result = service.run_task(task["id"], dry_run=True)
            self.assertEqual(result["status"], "succeeded")
            refreshed = next(item for item in service.bootstrap()["hosts"] if item["id"] == host["id"])
            self.assertEqual(refreshed["status"], "draft")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_run_task_uses_host_agent_boundary(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host({"name": "Host A"})
            task = service.queue_prepare_host(host["id"])
            called = {"count": 0}

            def fake_execute(_host, _steps, dry_run=False, **_kwargs):
                called["count"] += 1
                return [{"title": "fake", "ok": True, "stdout": "", "stderr": "", "command": "", "dry_run": dry_run}]

            original = service.agent.execute_task_plan
            service.agent.execute_task_plan = fake_execute
            try:
                service.run_task(task["id"], dry_run=True)
            finally:
                service.agent.execute_task_plan = original

            self.assertEqual(called["count"], 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_snapshot_clone_restore_workflow_records_and_updates_state(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            images = service.bootstrap()["images"]
            host = service.upsert_host(
                {
                    "name": "Host A",
                    "ssh_host": "10.0.0.22",
                    "ssh_user": "ubuntu",
                    "inventory": {"resources": {"cpu_threads_total": 16, "ram_mb_total": 32768, "disk_total_gib": 500.0}},
                }
            )
            network = service.upsert_network(
                {
                    "name": "Private A",
                    "host_id": host["id"],
                    "mode": "nat",
                    "cidr": "10.44.0.0/24",
                    "nat_enabled": True,
                }
            )
            doplet = service.upsert_doplet(
                {
                    "name": "Builder 01",
                    "host_id": host["id"],
                    "image_id": images[0]["id"],
                    "primary_network_id": network["id"],
                    "vcpu": 2,
                    "ram_mb": 2048,
                    "disk_gb": 30,
                    "storage_backend": "zfs",
                }
            )

            def fake_execute(_host, steps, dry_run=False, **_kwargs):
                return [
                    {
                        "title": step.get("title", "step"),
                        "ok": True,
                        "stdout": "ok",
                        "stderr": "",
                        "command": step.get("command", ""),
                        "dry_run": dry_run,
                    }
                    for step in steps
                ]

            original = service.agent.execute_task_plan
            service.agent.execute_task_plan = fake_execute
            try:
                snapshot_task = service.queue_snapshot(doplet["id"], "pre-upgrade")
                snapshots = service.list_snapshots()
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0]["status"], "planned")

                service.run_task(snapshot_task["id"], dry_run=False)
                snapshots = service.list_snapshots()
                self.assertEqual(snapshots[0]["status"], "complete")
                self.assertEqual(snapshots[0]["name"], "pre-upgrade")

                clone_task = service.queue_clone(
                    doplet["id"],
                    {
                        "name": "Builder 01 Clone",
                        "slug": "builder-01-clone",
                        "host_id": host["id"],
                        "primary_network_id": network["id"],
                    },
                )
                service.run_task(clone_task["id"], dry_run=False)
                clone_target = next(item for item in service.bootstrap()["doplets"] if item["slug"] == "builder-01-clone")
                self.assertEqual(clone_target["status"], "running")

                restore_task = service.queue_restore_snapshot(
                    snapshots[0]["id"],
                    {
                        "name": "Builder 01 Restore",
                        "slug": "builder-01-restore",
                        "host_id": host["id"],
                        "primary_network_id": network["id"],
                    },
                )
                service.run_task(restore_task["id"], dry_run=False)
                restored = next(item for item in service.bootstrap()["doplets"] if item["slug"] == "builder-01-restore")
                self.assertEqual(restored["status"], "running")
            finally:
                service.agent.execute_task_plan = original
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_host_agent_rejects_invalid_remote_steps_and_artifact_sources(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            agent = HostAgent(service.config)
            steps = [{"title": "remote", "command": "sudo apt update", "run_mode": "remote"}]
            signature = sign_json_payload(
                service.config,
                {"policy": "prepare-host", "target_type": "host", "target_id": "1", "steps": steps},
            )
            with self.assertRaises(ValueError):
                agent.execute_task_plan(
                    {},
                    steps,
                    dry_run=True,
                    signature=signature,
                    policy="prepare-host",
                    target_type="host",
                    target_id="1",
                )
            with self.assertRaises(ValueError):
                agent.materialize_file({}, "/etc/shadow", root / "forbidden.txt")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_background_task_launch_updates_status(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host({"name": "Host A"})
            task = service.queue_prepare_host(host["id"])

            def fake_execute(_host, steps, dry_run=False, progress_callback=None, **_kwargs):
                results = []
                for index, step in enumerate(steps):
                    result = {
                        "title": step.get("title", "step"),
                        "ok": True,
                        "stdout": "ok",
                        "stderr": "",
                        "command": step.get("command", ""),
                        "dry_run": dry_run,
                    }
                    results.append(result)
                    if progress_callback:
                        progress_callback(index + 1, len(steps), result, list(results))
                    time.sleep(0.02)
                return results

            original = service.agent.execute_task_plan
            service.agent.execute_task_plan = fake_execute
            try:
                queued = service.launch_task(task["id"])
                self.assertEqual(queued["status"], "queued")
                deadline = time.time() + 2
                latest = queued
                while time.time() < deadline:
                    latest = next(item for item in service.list_tasks() if item["id"] == task["id"])
                    if latest["status"] == "succeeded":
                        break
                    time.sleep(0.05)
                self.assertEqual(latest["status"], "succeeded")
                self.assertEqual(latest["progress"], 100)
            finally:
                service.agent.execute_task_plan = original
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_capacity_allows_mediated_profiles_but_blocks_exhaustion(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host(
                {
                    "name": "GPU Host",
                    "inventory": {
                        "resources": {
                            "cpu_threads_total": 16,
                            "ram_mb_total": 32768,
                            "disk_total_gib": 200.0,
                            "gpu_device_count": 1,
                            "mediated_profiles": [
                                {"profile_id": "nvidia-222", "name": "A16-2Q", "available_instances": 2},
                            ],
                        }
                    },
                }
            )
            service.upsert_doplet(
                {
                    "name": "VGPU-1",
                    "host_id": host["id"],
                    "vcpu": 2,
                    "ram_mb": 2048,
                    "disk_gb": 20,
                    "gpu_assignments": [{"mode": "mediated", "parent_address": "0000:65:00.0", "profile_id": "nvidia-222"}],
                }
            )
            service.upsert_doplet(
                {
                    "name": "VGPU-2",
                    "host_id": host["id"],
                    "vcpu": 2,
                    "ram_mb": 2048,
                    "disk_gb": 20,
                    "gpu_assignments": [{"mode": "mediated", "parent_address": "0000:65:00.0", "profile_id": "nvidia-222"}],
                }
            )
            with self.assertRaises(ValueError):
                service.upsert_doplet(
                    {
                        "name": "VGPU-3",
                        "host_id": host["id"],
                        "vcpu": 2,
                        "ram_mb": 2048,
                        "disk_gb": 20,
                        "gpu_assignments": [{"mode": "mediated", "parent_address": "0000:65:00.0", "profile_id": "nvidia-222"}],
                    }
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_web_login_mfa_and_host_api(self) -> None:
        root = self._isolated_root()
        try:
            self._current_root = root
            app = create_app(root)
            client = app.test_client()

            self._login_with_csrf(client, "owner", "change-me-now")

            with client.session_transaction() as session:
                api_csrf = session["csrf_token"]

            host_response = client.post(
                "/api/hosts",
                data=json.dumps(
                    {
                        "name": "API Host",
                        "host_mode": "linux-hypervisor",
                        "inventory": {"resources": {"cpu_threads_total": 8, "ram_mb_total": 16384, "disk_total_gib": 200.0}},
                    }
                ),
                headers={
                    "Content-Type": "application/json",
                    "X-CSRF-Token": api_csrf,
                },
            )
            self.assertEqual(host_response.status_code, 200)
            host_payload = host_response.get_json()
            self.assertEqual(host_payload["host"]["name"], "API Host")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_web_bootstrap_includes_local_machine_details(self) -> None:
        root = self._isolated_root()
        try:
            self._current_root = root
            app = create_app(root)
            client = app.test_client()
            self._login_with_csrf(client, "owner", "change-me-now")
            response = client.get("/api/bootstrap")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertIn("local_machine", payload)
            self.assertIn("hostname", payload["local_machine"])
            self.assertIn("recommended_wsl_distribution", payload["local_machine"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_repeat_login_from_same_device_does_not_duplicate_trusted_device(self) -> None:
        root = self._isolated_root()
        try:
            self._current_root = root
            app = create_app(root)
            client = app.test_client()
            self._login_with_csrf(client, "owner", "change-me-now")
            self._login_with_csrf(client, "owner", "change-me-now")
            response = client.get("/api/bootstrap")
            self.assertEqual(response.status_code, 200)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_rbac_blocks_viewer_and_allows_operator(self) -> None:
        root = self._isolated_root()
        try:
            self._current_root = root
            service = PlatformService(root)
            service.create_user(
                {
                    "username": "viewer1",
                    "email": "viewer1@example.com",
                    "password": "viewer-pass",
                    "role": "viewer",
                    "mfa_enabled": False,
                }
            )
            service.create_user(
                {
                    "username": "operator1",
                    "email": "operator1@example.com",
                    "password": "operator-pass",
                    "role": "operator",
                    "mfa_enabled": False,
                }
            )

            app = create_app(root)

            viewer = app.test_client()
            self._login_with_csrf(viewer, "viewer1", "viewer-pass")
            with viewer.session_transaction() as session:
                viewer_csrf = session["csrf_token"]
            denied = viewer.post(
                "/api/hosts",
                data=json.dumps({"name": "Viewer Host"}),
                headers={"Content-Type": "application/json", "X-CSRF-Token": viewer_csrf},
            )
            self.assertEqual(denied.status_code, 403)
            allowed_read = viewer.get("/api/bootstrap")
            self.assertEqual(allowed_read.status_code, 200)

            operator = app.test_client()
            self._login_with_csrf(operator, "operator1", "operator-pass")
            with operator.session_transaction() as session:
                operator_csrf = session["csrf_token"]
            allowed = operator.post(
                "/api/hosts",
                data=json.dumps({"name": "Operator Host"}),
                headers={"Content-Type": "application/json", "X-CSRF-Token": operator_csrf},
            )
            self.assertEqual(allowed.status_code, 200)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_web_snapshot_and_restore_routes(self) -> None:
        root = self._isolated_root()
        try:
            self._current_root = root
            service = PlatformService(root)
            service.create_user(
                {
                    "username": "operator2",
                    "email": "operator2@example.com",
                    "password": "operator-pass",
                    "role": "operator",
                    "mfa_enabled": False,
                }
            )
            images = service.bootstrap()["images"]
            host = service.upsert_host(
                {
                    "name": "API Host",
                    "ssh_host": "10.0.0.55",
                    "ssh_user": "ubuntu",
                    "inventory": {"resources": {"cpu_threads_total": 8, "ram_mb_total": 16384, "disk_total_gib": 200.0}},
                }
            )
            doplet = service.upsert_doplet(
                {
                    "name": "API Builder",
                    "host_id": host["id"],
                    "image_id": images[0]["id"],
                    "vcpu": 2,
                    "ram_mb": 2048,
                    "disk_gb": 25,
                }
            )

            app = create_app(root)
            client = app.test_client()
            self._login_with_csrf(client, "operator2", "operator-pass")
            with client.session_transaction() as session:
                csrf_token = session["csrf_token"]

            snapshot_response = client.post(
                f"/api/doplets/{doplet['id']}/snapshot",
                data=json.dumps({"snapshot_name": "api-snap", "launch": True}),
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token},
            )
            self.assertEqual(snapshot_response.status_code, 200)
            self.assertEqual(snapshot_response.get_json()["task"]["status"], "queued")

            snapshots_response = client.get("/api/snapshots")
            self.assertEqual(snapshots_response.status_code, 200)
            snapshots = snapshots_response.get_json()["snapshots"]
            self.assertEqual(len(snapshots), 1)

            restore_response = client.post(
                f"/api/snapshots/{snapshots[0]['id']}/restore",
                data=json.dumps({"name": "API Builder Restore", "slug": "api-builder-restore", "host_id": host["id"]}),
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token},
            )
            self.assertEqual(restore_response.status_code, 200)

            task_id = snapshot_response.get_json()["task"]["id"]
            launch_response = client.post(
                f"/api/tasks/{task_id}/launch",
                data=json.dumps({"dry_run": True}),
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token},
            )
            self.assertEqual(launch_response.status_code, 200)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_web_doplet_open_terminal_route(self) -> None:
        root = self._isolated_root()
        try:
            self._current_root = root
            service = PlatformService(root)
            service.create_user(
                {
                    "username": "operator3",
                    "email": "operator3@example.com",
                    "password": "operator-pass",
                    "role": "operator",
                    "mfa_enabled": False,
                }
            )
            host = service.upsert_host(
                {
                    "name": "Local Windows Host",
                    "host_mode": "windows-local",
                    "wsl_distribution": "Ubuntu",
                }
            )
            doplet = service.upsert_doplet(
                {
                    "name": "Console Test",
                    "host_id": host["id"],
                    "bootstrap_user": "ubuntu",
                }
            )

            app = create_app(root)
            client = app.test_client()
            self._login_with_csrf(client, "operator3", "operator-pass")
            with client.session_transaction() as session:
                csrf_token = session["csrf_token"]

            with patch(
                "vpsdash.platform_service.open_doplet_terminal",
                return_value={
                    "supported": True,
                    "transport": "virsh-console",
                    "launcher": "windows-wsl",
                    "title": "Console Test console",
                    "target": "console-test",
                    "preview_command": 'wsl.exe -d Ubuntu -- bash -lc "virsh console console-test"',
                    "launched": True,
                },
            ) as open_mock:
                response = client.post(
                    f"/api/doplets/{doplet['id']}/open-terminal",
                    data=json.dumps({}),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf_token},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["terminal"]["transport"], "virsh-console")
            open_mock.assert_called_once()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cancel_retry_and_acceptance_report(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host(
                {
                    "name": "Accept Host",
                    "inventory": {
                        "resources": {
                            "cpu_threads_total": 8,
                            "ram_mb_total": 16384,
                            "disk_total_gib": 200.0,
                            "virtualization_ready": True,
                            "iommu_groups": 8,
                            "zfs_pools": [{"name": "tank", "size": "100G", "free": "50G"}],
                            "gpu_devices": [{"pci_address": "0000:65:00.0", "vendor": "nvidia", "name": "A16"}],
                            "mediated_profiles": [{"profile_id": "nvidia-222", "available_instances": 2}],
                        }
                    },
                }
            )
            task = service.queue_prepare_host(host["id"])
            cancelled = service.cancel_task(task["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            retried = service.retry_task(task["id"])
            self.assertEqual(retried["status"], "planned")
            report = service.host_acceptance_report(host["id"])
            self.assertTrue(report["ok"])
            self.assertGreaterEqual(len(report["checks"]), 4)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_windows_host_inventory_capture_uses_host_payload(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            host = service.upsert_host(
                {
                    "name": "Windows Host",
                    "host_mode": "windows-local",
                    "wsl_distribution": "Ubuntu",
                }
            )
            original = service.agent.capture_inventory
            service.agent.capture_inventory = lambda _payload: {
                "wsl_list": {"ok": True},
                "libvirt_validate": {"ok": True},
                "resources": {"virtualization_ready": True, "iommu_groups": 8, "gpu_device_count": 0},
            }
            try:
                refreshed = service.capture_host_inventory(host["id"])
            finally:
                service.agent.capture_inventory = original
            self.assertEqual(refreshed["host_mode"], "windows-local")
            self.assertEqual(refreshed["wsl_distribution"], "Ubuntu")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_backup_scheduler_verify_and_prune(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            provider = service.upsert_backup_provider(
                {
                    "name": "Local Provider",
                    "provider_type": "local",
                    "root_path": str(root / "provider-backups"),
                }
            )
            images = service.bootstrap()["images"]
            host = service.upsert_host(
                {
                    "name": "Scheduler Host",
                    "inventory": {"resources": {"cpu_threads_total": 8, "ram_mb_total": 16384, "disk_total_gib": 200.0}},
                }
            )
            doplet = service.upsert_doplet(
                {
                    "name": "Scheduled VM",
                    "host_id": host["id"],
                    "image_id": images[0]["id"],
                    "backup_policy": {
                        "enabled": True,
                        "schedule_minutes": 1,
                        "retain_count": 1,
                        "provider_ids": [provider["id"]],
                        "verify_after_upload": True,
                    },
                }
            )

            scheduler = service.run_backup_scheduler()
            self.assertEqual(scheduler["count"], 1)
            backup_task = scheduler["queued"][0]

            def fake_execute(_host, steps, dry_run=False, **_kwargs):
                artifact = root / "data" / "downloads" / "fake-backup.img.gz"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(b"backup")
                manifest = artifact.with_suffix(".manifest.json")
                manifest.write_text("{}", encoding="utf-8")
                xml = artifact.with_suffix(".xml")
                xml.write_text("<xml/>", encoding="utf-8")
                results = []
                for step in steps:
                    result = {
                        "title": step.get("title", "step"),
                        "ok": True,
                        "stdout": "",
                        "stderr": "",
                        "command": step.get("command", ""),
                        "dry_run": dry_run,
                    }
                    results.append(result)
                return results

            original_execute = service.agent.execute_task_plan
            original_materialize = service.agent.materialize_file
            service.agent.execute_task_plan = fake_execute
            service.agent.materialize_file = lambda _host, source_path, destination: destination.write_bytes(b"backup") or destination
            try:
                service.run_task(backup_task["id"], dry_run=False)
                backups = service.list_backups()
                self.assertEqual(len(backups), 1)
                verification = service.verify_backup_record(backups[0]["id"])
                self.assertIn("verification", verification)
                pruned = service.prune_backup_records()
                self.assertEqual(pruned["count"], 0)
            finally:
                service.agent.execute_task_plan = original_execute
                service.agent.materialize_file = original_materialize
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_network_apply_and_resize_queue_routes(self) -> None:
        root = self._isolated_root()
        try:
            service = PlatformService(root)
            images = service.bootstrap()["images"]
            host = service.upsert_host(
                {
                    "name": "Net Host",
                    "inventory": {"resources": {"cpu_threads_total": 8, "ram_mb_total": 16384, "disk_total_gib": 200.0}},
                }
            )
            network = service.upsert_network({"name": "net-a", "host_id": host["id"], "mode": "nat", "cidr": "10.20.0.0/24", "nat_enabled": True})
            apply_task = service.queue_apply_network(network["id"])
            self.assertEqual(apply_task["task_type"], "apply-network")

            doplet = service.upsert_doplet(
                {
                    "name": "Resize Me",
                    "host_id": host["id"],
                    "image_id": images[0]["id"],
                    "primary_network_id": network["id"],
                    "vcpu": 2,
                    "ram_mb": 2048,
                    "disk_gb": 20,
                }
            )
            resize_task = service.queue_resize_doplet(doplet["id"], {"vcpu": 3, "ram_mb": 4096, "disk_gb": 30})
            self.assertEqual(resize_task["task_type"], "resize-doplet")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()




