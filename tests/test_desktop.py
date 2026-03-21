import os
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QBoxLayout, QScrollArea, QStatusBar

from vpsdash.desktop import VpsDashWindow
from vpsdash.service import VpsDashService


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.root = Path(__file__).resolve().parents[1]

    def test_template_load_clears_project_id(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window._apply_template("generic-docker-webapp")
            self.assertIsNone(window.current_project.get("id"))
        finally:
            window.close()

    def test_window_does_not_create_bottom_status_bar(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertIsNone(window.findChild(QStatusBar))
            self.assertEqual(window.sidebar_status.text(), "State refreshed")
        finally:
            window.close()

    def test_pending_form_refresh_does_not_clear_generated_plan(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.host_name.setText("Test Host")
            window._handle_form_changed()
            self.assertTrue(window._form_refresh_timer.isActive())
            window._generate_plan()
            self.assertIsNotNone(window.current_plan)
            self.assertFalse(window._form_refresh_timer.isActive())
        finally:
            window.close()

    def test_diagnostics_runs_off_the_ui_thread(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.service.diagnostics = lambda *_args, **_kwargs: (
                time.sleep(0.25),
                {"summary": {"total": 1, "ok": 1, "failed": 0}, "checks": []},
            )[1]
            started = time.perf_counter()
            window._run_diagnostics()
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.15)
            self.assertEqual(window.busy_tasks, 1)

            deadline = time.perf_counter() + 1.5
            while window.busy_tasks and time.perf_counter() < deadline:
                self.app.processEvents()
                time.sleep(0.01)

            self.assertEqual(window.busy_tasks, 0)
            self.assertIn('"summary"', window.diagnostics_output["output"].toPlainText())
        finally:
            window.close()

    def test_setup_cards_stack_when_width_is_tight(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window._update_responsive_layouts(900)
            self.assertEqual(window.setup_cards_row.direction(), QBoxLayout.TopToBottom)
            window._update_responsive_layouts(1400)
            self.assertEqual(window.setup_cards_row.direction(), QBoxLayout.LeftToRight)
        finally:
            window.close()

    def test_defaults_preview_cards_stack_in_narrow_layout(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window._update_responsive_layouts(860)
            self.assertIsNotNone(window.defaults_preview_grid.itemAtPosition(0, 0))
            self.assertIsNotNone(window.defaults_preview_grid.itemAtPosition(1, 0))
            window._update_responsive_layouts(1400)
            self.assertIsNotNone(window.defaults_preview_grid.itemAtPosition(0, 0))
            self.assertIsNotNone(window.defaults_preview_grid.itemAtPosition(0, 1))
        finally:
            window.close()

    def test_metric_cards_reflow_before_clipping(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window._update_responsive_layouts(820)
            self.assertIsNotNone(window.metrics_grid.itemAtPosition(1, 0))
            window._update_responsive_layouts(1400)
            self.assertIsNotNone(window.metrics_grid.itemAtPosition(0, 3))
        finally:
            window.close()

    def test_content_shell_gives_main_column_more_weight_than_side_spacers(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertEqual(window.content_shell_layout.stretch(0), 1)
            self.assertEqual(window.content_shell_layout.stretch(1), 14)
            self.assertEqual(window.content_shell_layout.stretch(2), 1)
        finally:
            window.close()

    def test_template_selection_does_not_overwrite_project_fields(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.project_name.setText("Manual Project")
            window.project_repo_url.setText("https://example.com/manual.git")
            target_index = window.template_select.findData("generic-docker-webapp")
            self.assertGreaterEqual(target_index, 0)
            window.template_select.setCurrentIndex(target_index)
            self.app.processEvents()
            self.assertEqual(window.project_name.text(), "Manual Project")
            self.assertEqual(window.project_repo_url.text(), "https://example.com/manual.git")
        finally:
            window.close()

    def test_adopt_local_machine_for_server_updates_remote_packet(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.local_machine = {
                "hostname": "serverbox",
                "fqdn": "serverbox.local",
                "username": "ops",
                "ip_candidates": ["192.168.1.44"],
            }
            window._adopt_local_machine_for_server()
            self.assertEqual(window.host_device_role.currentData(), "computer-b-server")
            self.assertEqual(window.host_bootstrap_auth.currentData(), "password-bootstrap")
            self.assertEqual(window.host_ssh_host.text(), "192.168.1.44")
            self.assertIn("serverbox", window.remote_packet_output.toPlainText())
        finally:
            window.close()

    def test_adopt_local_windows_machine_for_server_selects_remote_windows_mode(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.local_machine = {
                "hostname": "winbox",
                "fqdn": "winbox.local",
                "username": "ops",
                "platform": "Windows 11",
                "ip_candidates": ["192.168.1.55"],
            }
            window._adopt_local_machine_for_server()
            self.assertEqual(window.host_mode.currentText(), "windows-remote")
            self.assertEqual(window.host_ssh_host.text(), "192.168.1.55")
        finally:
            window.close()

    def test_windows_remote_mode_enables_ssh_and_wsl_fields(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.host_mode.setCurrentText("windows-remote")
            window._host_mode_changed()
            self.assertTrue(window.host_ssh_host.isEnabled())
            self.assertTrue(window.host_ssh_user.isEnabled())
            self.assertTrue(window.host_wsl_distribution.isEnabled())
            self.assertIn("WSL", window.host_mode_hint.text())
        finally:
            window.close()

    def test_windows_local_quick_start_sets_local_host_mode(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.local_machine = {"hostname": "my-win-box", "platform": "Windows 11"}
            window._configure_windows_local_host()
            self.assertEqual(window.host_mode.currentText(), "windows-local")
            self.assertEqual(window.host_name.text(), "my-win-box")
            self.assertEqual(window.pages.currentIndex(), window.PAGE_HARMINOPLETS)
        finally:
            window.close()

    def test_open_doplet_admin_switches_to_native_activity(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            with patch("vpsdash.desktop.webbrowser.open") as mocked:
                window._open_doplet_admin()
                mocked.assert_not_called()
                self.assertEqual(window.pages.currentIndex(), window.PAGE_ACTIVITY)
        finally:
            window.close()

    def test_open_doplet_builder_switches_to_native_workspace(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            with patch("vpsdash.desktop.webbrowser.open") as mocked:
                window._open_doplet_builder()
                mocked.assert_not_called()
                self.assertEqual(window.pages.currentIndex(), window.PAGE_HARMINOPLETS)
        finally:
            window.close()

    def test_loading_default_prefills_host_bootstrap_fields(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            index = window.default_select.findData("builtin-generic-remote-bootstrap")
            self.assertGreaterEqual(index, 0)
            window.default_select.setCurrentIndex(index)
            window._load_selected_default_into_form()

            deadline = time.perf_counter() + 2.0
            while (window._prefill_timer.isActive() or window._prefill_steps) and time.perf_counter() < deadline:
                self.app.processEvents()
                time.sleep(0.01)

            self.assertEqual(window.host_mode.currentText(), "remote-linux")
            self.assertEqual(window.host_device_role.currentData(), "computer-b-server")
            self.assertEqual(window.host_bootstrap_auth.currentData(), "password-bootstrap")
            self.assertIn("Filled", window.default_activity_output.toPlainText())
        finally:
            window.close()

    def test_instance_selection_updates_management_panel(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.instances = [
                {
                    "id": "instance-1",
                    "name": "Sample Instance",
                    "host": {"name": "Sample Host", "mode": "linux-local"},
                    "project": {"name": "Sample Project", "deploy_path": "~/apps/sample", "primary_domain": "example.com"},
                    "backups": [{"id": "backup-1", "created_at": "2026-03-20T00:00:00+00:00", "status": "ok", "artifact_path": "~/backups/sample.tar.gz"}],
                    "updated_at": "2026-03-20T00:00:00+00:00",
                }
            ]
            window._populate_instances()
            item = window.instances_tree.topLevelItem(0)
            window.instances_tree.setCurrentItem(item)
            window._instance_selection_changed()
            self.assertIn("Sample Instance", window.instance_detail_output.toPlainText())
            self.assertTrue(window.backup_instance_button.isEnabled())
        finally:
            window.close()

    def test_desktop_uses_native_doplet_workspace_page(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertEqual(window.pages.count(), 4)
            window._switch_page(window.PAGE_HARMINOPLETS)
            self.assertEqual(window.pages.currentIndex(), window.PAGE_HARMINOPLETS)
            self.assertIsInstance(window.pages.widget(window.PAGE_HARMINOPLETS), QScrollArea)
        finally:
            window.close()

    def test_overview_page_is_scrollable(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertIsInstance(window.pages.widget(window.PAGE_OVERVIEW), QScrollArea)
        finally:
            window.close()

    def test_doplets_page_is_scrollable(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertIsInstance(window.pages.widget(window.PAGE_HARMINOPLETS), QScrollArea)
        finally:
            window.close()

    def test_resources_page_is_scrollable(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertIsInstance(window.pages.widget(window.PAGE_RESOURCES), QScrollArea)
        finally:
            window.close()

    def test_native_platform_views_exist_in_operations_page(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertTrue(hasattr(window, "native_task_tree"))
            self.assertTrue(hasattr(window, "native_asset_tree"))
            self.assertGreaterEqual(window.native_asset_tree.topLevelItemCount(), 0)
        finally:
            window.close()

    def test_operations_page_is_scrollable_and_outputs_have_real_height(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertIsInstance(window.pages.widget(window.PAGE_ACTIVITY), QScrollArea)
            self.assertGreaterEqual(window.diagnostics_output["output"].minimumHeight(), 180)
            self.assertGreaterEqual(window.native_task_detail.minimumHeight(), 220)
        finally:
            window.close()

    def test_layout_audit_reports_no_issues_across_window_sizes(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.show()
            for width in (1280, 1440, 1680):
                window.resize(width, 940)
                for _ in range(25):
                    self.app.processEvents()
                    time.sleep(0.005)
                self.assertEqual(window._last_layout_issues, [], msg=f"layout issues at width {width}: {window._last_layout_issues}")
        finally:
            window.close()

    def test_nav_uses_doplet_label_for_admin_page(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertEqual(window.PAGE_LABELS[1], "Activity")
            self.assertEqual(window.PAGE_LABELS[2], "Resources")
            self.assertEqual(window.PAGE_LABELS[-1], "Doplets")
        finally:
            window.close()

    def test_embedded_web_admin_url_uses_embedded_dashboard_mode(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            full_url = window._web_admin_url("#hosts-panel")
            embedded_url = window._web_admin_url("#hosts-panel", embedded=True)
            self.assertIn("/dashboard#hosts-panel", full_url)
            self.assertIn("/dashboard?embedded=1#hosts-panel", embedded_url)
        finally:
            window.close()

    def test_native_security_card_populates_current_machine_ssh_key_without_inserting_it(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.local_machine = {
                "ssh_public_keys": [
                    {
                        "label": "id_ed25519.pub",
                        "path": "C:/Users/tester/.ssh/id_ed25519.pub",
                        "public_key": "ssh-ed25519 AAAATESTKEY tester@machine",
                    }
                ]
            }
            window.native_doplet_keys.clear()
            window._populate_native_platform_views()
            self.assertEqual(window.current_ssh_key_field.text(), "ssh-ed25519 AAAATESTKEY tester@machine")
            self.assertTrue(window.current_ssh_key_field.isEnabled())
            self.assertEqual(window.native_doplet_keys.toPlainText(), "")
            self.assertIn("id_ed25519.pub", window.current_ssh_key_label.text())
        finally:
            window.close()

    def test_native_doplet_payload_respects_selected_login_method(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.local_machine = {
                "ssh_public_keys": [
                    {
                        "label": "id_ed25519.pub",
                        "path": r"C:/Users/tester/.ssh/id_ed25519.pub",
                        "private_key_path": r"C:/Users/tester/.ssh/id_ed25519",
                        "public_key": "ssh-ed25519 AAAATESTKEY tester@machine",
                    }
                ]
            }
            window.native_doplet_name.setText("Secure Box")
            window.native_doplet_slug.setText("secure-box")
            window.native_doplet_bootstrap_user.setText("ubuntu")
            window.native_doplet_bootstrap_password.setText("super-secret")
            window.native_doplet_keys.setPlainText("ssh-ed25519 AAAATESTKEY tester@machine")

            password_index = window.native_doplet_auth_mode.findData("password")
            ssh_index = window.native_doplet_auth_mode.findData("ssh")
            both_index = window.native_doplet_auth_mode.findData("password+ssh")

            window.native_doplet_auth_mode.setCurrentIndex(password_index)
            password_payload = window._collect_native_doplet_payload()
            self.assertEqual(password_payload["bootstrap_password"], "super-secret")
            self.assertEqual(password_payload["ssh_public_keys"], [])
            self.assertEqual(password_payload["metadata_json"]["auth_mode"], "password")

            window.native_doplet_auth_mode.setCurrentIndex(ssh_index)
            window.native_doplet_bootstrap_password.setText("ignored")
            ssh_payload = window._collect_native_doplet_payload()
            self.assertEqual(ssh_payload["bootstrap_password"], "")
            self.assertEqual(ssh_payload["ssh_public_keys"], ["ssh-ed25519 AAAATESTKEY tester@machine"])
            self.assertEqual(ssh_payload["metadata_json"]["auth_mode"], "ssh")
            self.assertEqual(ssh_payload["metadata_json"]["local_private_key_path"], "C:/Users/tester/.ssh/id_ed25519")

            window.native_doplet_auth_mode.setCurrentIndex(both_index)
            window.native_doplet_bootstrap_password.setText("super-secret")
            both_payload = window._collect_native_doplet_payload()
            self.assertEqual(both_payload["bootstrap_password"], "super-secret")
            self.assertEqual(both_payload["ssh_public_keys"], ["ssh-ed25519 AAAATESTKEY tester@machine"])
            self.assertEqual(both_payload["metadata_json"]["auth_mode"], "password+ssh")
            self.assertEqual(both_payload["metadata_json"]["local_private_key_path"], "C:/Users/tester/.ssh/id_ed25519")
        finally:
            window.close()

    def test_host_and_builder_fields_expose_explanatory_tooltips(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            self.assertIn("machine-friendly identifier", window.native_doplet_slug.toolTip())
            self.assertIn("How VPSdash reaches", window.host_mode.toolTip())
            self.assertTrue(window.pages.widget(window.PAGE_HARMINOPLETS).property("pageScroll"))
        finally:
            window.close()

    def test_initial_setup_saves_local_host_and_launches_prepare(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window._project_source_setup_commands = MagicMock(return_value=None)
            window.service.upsert_platform_host = MagicMock(return_value={"id": 5, "name": "This PC", "mode": "windows-local"})
            window.service.capture_platform_host_inventory = MagicMock(return_value={"host": {"id": 5}})
            window.service.queue_prepare_platform_host = MagicMock(return_value={"id": 17, "status": "planned"})
            window.service.launch_platform_task = MagicMock(return_value={"id": 17, "status": "queued"})

            result = window._perform_local_initial_setup({"name": "This PC", "mode": "windows-local", "wsl_distribution": "Ubuntu"})

            self.assertEqual(result["host"]["id"], 5)
            window.service.upsert_platform_host.assert_called_once()
            window.service.capture_platform_host_inventory.assert_called_once_with(5, actor="desktop")
            window.service.queue_prepare_platform_host.assert_called_once_with(5, actor="desktop")
            window.service.launch_platform_task.assert_called_once_with(17, actor="desktop", dry_run=False)
        finally:
            window.close()

    def test_create_native_doplet_launches_queued_platform_task(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.service.upsert_doplet = MagicMock(return_value={"id": 41, "name": "Smoke Doplet"})
            window.service.queue_doplet_create = MagicMock(return_value={"id": 91, "status": "planned"})
            window.service.launch_platform_task = MagicMock(return_value={"id": 91, "status": "queued"})
            window._load_bootstrap = MagicMock()
            window._run_async_task = lambda **kwargs: kwargs["on_success"](kwargs["work"]())

            window._create_native_doplet()

            window.service.queue_doplet_create.assert_called_once_with(41, actor="desktop")
            window.service.launch_platform_task.assert_called_once_with(91, actor="desktop", dry_run=False)
            self.assertIn(91, window._watched_task_roots)
        finally:
            window.close()

    def test_auto_retry_relaunches_failed_watched_task(self) -> None:
        window = VpsDashWindow(VpsDashService(self.root))
        try:
            window.bootstrap_data = {
                "control_plane": {
                    "tasks": [
                        {
                            "id": 7,
                            "task_type": "create-doplet",
                            "status": "failed",
                            "progress": 10,
                            "target_type": "doplet",
                            "target_id": "22",
                            "result_payload": {},
                        }
                    ]
                }
            }
            window._watched_task_roots = {7}
            window.service.retry_platform_task = MagicMock(return_value={"id": 8, "status": "planned"})
            window.service.launch_platform_task = MagicMock(return_value={"id": 8, "status": "queued"})

            window._maybe_auto_retry_watched_tasks()

            window.service.retry_platform_task.assert_called_once_with(7, actor="desktop")
            window.service.launch_platform_task.assert_called_once_with(8, actor="desktop", dry_run=False)
            self.assertEqual(window._auto_retry_attempts[7], 1)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()

