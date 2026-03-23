from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from vpsdash.desktop import VpsDashWindow
from vpsdash.service import VpsDashService


def _build_window(root: Path) -> tuple[QApplication, VpsDashWindow]:
    app = QApplication.instance() or QApplication([])
    window = VpsDashWindow(VpsDashService(root))
    return app, window


def _base_host_payload() -> dict[str, object]:
    return {
        "name": "DESKTOP",
        "mode": "Windows host via local WSL",
        "host_mode": "windows-local",
        "wsl_distribution": "Ubuntu",
        "ssh_user": "",
        "ssh_host": "",
        "ssh_port": 22,
        "local_machine_fingerprint": "dryrun-fingerprint",
    }


def _run_reboot_pending(window: VpsDashWindow) -> dict[str, object]:
    host_payload = _base_host_payload()
    progress: list[tuple[int, str]] = []
    state = {"calls": 0}

    window._project_source_setup_needs_install = lambda: None
    window._project_source_setup_commands = lambda: None
    window.service.upsert_platform_host = lambda payload, actor="desktop": {
        "id": 1,
        "name": payload.get("name", "DESKTOP"),
        "status": payload.get("status", "queued"),
        "host_mode": payload.get("host_mode") or payload.get("mode"),
        "inventory": {"config": {"local_machine_fingerprint": payload.get("local_machine_fingerprint", "")}},
        "config": {"local_machine_fingerprint": payload.get("local_machine_fingerprint", "")},
    }

    def fake_wsl_state(_payload: dict[str, object]) -> dict[str, object]:
        state["calls"] += 1
        if state["calls"] == 1:
            return {
                "distro": "Ubuntu",
                "distro_exists": False,
                "distro_ready": False,
                "list_output": "Windows Subsystem for Linux has no installed distributions.",
                "status_output": "Default Version: 2",
                "list_result": {"ok": False},
            }
        return {
            "distro": "Ubuntu",
            "distro_exists": True,
            "distro_ready": False,
            "list_output": "Ubuntu\n",
            "status_output": "Install in progress; reboot required.",
            "list_result": {"ok": True},
        }

    window._windows_local_wsl_state = fake_wsl_state
    window._install_windows_local_wsl = lambda _payload: {
        "ok": True,
        "reboot_required": True,
        "stdout": "WSL installation started.",
        "stderr": "",
    }
    window._run_initial_setup_step_with_retries = (
        lambda label, fn, emit=None, attempts=1, success_check=None, error_detail=None: fn()
    )
    window._persist_host_setup_state = lambda host, setup_state, status=None: {
        **host,
        "status": status or host.get("status", "queued"),
        "config": {**dict(host.get("config") or {}), "setup_state": setup_state},
        "inventory": {
            **dict(host.get("inventory") or {}),
            "config": {**dict((host.get("inventory") or {}).get("config") or {}), "setup_state": setup_state},
        },
    }
    window.service.capture_platform_host_inventory = (
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("inventory should not run in reboot-pending path"))
    )
    window._run_prepare_host_until_complete = (
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("prepare should not run in reboot-pending path"))
    )

    result = window._perform_local_initial_setup_with_progress(host_payload, lambda pct, msg: progress.append((pct, msg)))
    setup_state = ((result["host"].get("config") or {}).get("setup_state") or {})
    return {
        "scenario": "reboot-pending",
        "host_status": result["host"].get("status"),
        "overall_status": setup_state.get("overall_status"),
        "requires_reboot": setup_state.get("requires_reboot"),
        "prepare": result.get("prepare"),
        "last_progress": progress[-1] if progress else None,
        "current_step": setup_state.get("current_step"),
    }


def _run_ready(window: VpsDashWindow) -> dict[str, object]:
    host_payload = _base_host_payload()
    progress: list[tuple[int, str]] = []

    window._project_source_setup_needs_install = lambda: None
    window._project_source_setup_commands = lambda: None
    window.service.upsert_platform_host = lambda payload, actor="desktop": {
        "id": 1,
        "name": payload.get("name", "DESKTOP"),
        "status": payload.get("status", "queued"),
        "host_mode": payload.get("host_mode") or payload.get("mode"),
        "inventory": {"config": {"local_machine_fingerprint": payload.get("local_machine_fingerprint", "")}},
        "config": {"local_machine_fingerprint": payload.get("local_machine_fingerprint", "")},
    }
    window._windows_local_wsl_state = lambda _payload: {
        "distro": "Ubuntu",
        "distro_exists": True,
        "distro_ready": True,
        "list_output": "Ubuntu\n",
        "status_output": "Default Version: 2",
        "list_result": {"ok": True},
    }
    window._run_initial_setup_step_with_retries = (
        lambda label, fn, emit=None, attempts=1, success_check=None, error_detail=None: fn()
    )

    def fake_persist(host: dict[str, object], setup_state: dict[str, object], status: str | None = None) -> dict[str, object]:
        return {
            **host,
            "status": status or host.get("status", "queued"),
            "config": {**dict(host.get("config") or {}), "setup_state": setup_state},
            "inventory": {
                **dict(host.get("inventory") or {}),
                "resources": {"virtualization_ready": False},
                "config": {**dict((host.get("inventory") or {}).get("config") or {}), "setup_state": setup_state},
            },
        }

    window._persist_host_setup_state = fake_persist
    window.service.capture_platform_host_inventory = lambda *args, **kwargs: {
        "inventory": {"resources": {"virtualization_ready": False, "cpu_total": 8, "memory_total_mb": 16384, "storage_total_gb": 500}}
    }
    window._run_prepare_host_until_complete = lambda *args, **kwargs: {"status": "ready"}

    result = window._perform_local_initial_setup_with_progress(host_payload, lambda pct, msg: progress.append((pct, msg)))
    setup_state = ((result["host"].get("config") or {}).get("setup_state") or {})
    return {
        "scenario": "ready",
        "host_status": result["host"].get("status"),
        "overall_status": setup_state.get("overall_status"),
        "requires_reboot": setup_state.get("requires_reboot"),
        "prepare": result.get("prepare"),
        "last_progress": progress[-1] if progress else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a no-side-effect dry run of the VPSdash initial setup pipeline.")
    parser.add_argument(
        "--scenario",
        choices=["reboot-pending", "ready", "all"],
        default="all",
        help="Which setup path to simulate.",
    )
    args = parser.parse_args()

    temp_root = Path(tempfile.mkdtemp(prefix="vpsdash-initial-setup-"))
    try:
        app, window = _build_window(temp_root)
        try:
            scenarios = []
            if args.scenario in {"reboot-pending", "all"}:
                scenarios.append(_run_reboot_pending(window))
            if args.scenario in {"ready", "all"}:
                scenarios.append(_run_ready(window))
            for item in scenarios:
                print(item)
        finally:
            window.close()
            app.quit()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
