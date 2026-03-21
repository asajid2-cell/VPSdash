from __future__ import annotations

import json
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .config import PlatformConfig
from .execution import LINUX_LOCAL_MODES, WINDOWS_LOCAL_MODES, execute_plan, fetch_remote_file, resolve_local_wsl_path
from .orchestration import inventory_snapshot
from .security import sign_json_with_key, verify_json_signature


class HostAgentRuntime:
    _POLICY_PREFIXES = {
        "prepare-host": (
            "powershell -NoProfile -Command ",
            "sudo apt ",
            "sudo systemctl ",
            "mkdir -p ",
            "sudo mkdir -p ",
            "python - <<'PY'",
            "python3 - <<'PY'",
            "virsh net-info ",
            "virsh net-define ",
            "virsh net-autostart ",
            "virsh net-start ",
            "sudo zpool ",
            "sudo zfs ",
            "sudo vgs ",
            "sudo pvcreate ",
            "sudo vgcreate ",
            "sudo lvs ",
            "sudo lvcreate ",
            "sudo apt-get ",
        ),
        "create-doplet": (
            "sudo zfs ",
            "sudo lvcreate ",
            "sudo qemu-img ",
            "qemu-img create ",
            "mkdir -p ",
            "python - <<'PY'",
            "python3 - <<'PY'",
            "virt-install ",
            "virsh attach-device ",
            "sudo sh -c ",
        ),
        "backup-doplet": (
            "sudo zfs ",
            "sudo dd ",
            "mkdir -p ",
            "python - <<'PY'",
            "python3 - <<'PY'",
            "virsh dumpxml ",
            "cp ",
            "gzip -c ",
            "printf 'UPLOAD ",
            "printf \"UPLOAD ",
            "sha256sum ",
            "gzip -t ",
        ),
        "snapshot-doplet": ("sudo zfs snapshot ", "sudo lvcreate -s ", "mkdir -p ", "qemu-img convert "),
        "clone-doplet": (
            "sudo zfs ",
            "sudo lvcreate ",
            "sudo dd ",
            "mkdir -p ",
            "qemu-img convert ",
            "virt-install ",
            "python - <<'PY'",
            "python3 - <<'PY'",
            "virsh attach-device ",
            "sudo sh -c ",
        ),
        "restore-doplet": (
            "virsh ",
            "sudo zfs ",
            "sudo dd ",
            "sudo lvcreate ",
            "qemu-img convert ",
            "virt-install ",
            "python - <<'PY'",
            "python3 - <<'PY'",
            "virsh attach-device ",
            "sudo sh -c ",
        ),
        "apply-network": (
            "mkdir -p ",
            "python - <<'PY'",
            "python3 - <<'PY'",
            "virsh net-define ",
            "virsh net-start ",
            "virsh net-autostart ",
            "sudo sysctl ",
            "sudo nft ",
            "sudo ip link ",
            "sudo ip addr ",
            "sudo iptables ",
        ),
        "delete-network-runtime": (
            "virsh net-destroy ",
            "virsh net-undefine ",
            "sudo nft ",
            "sudo ip link ",
        ),
        "resize-doplet": (
            "virsh setvcpus ",
            "virsh setmaxmem ",
            "virsh setmem ",
            "sudo zfs set ",
            "sudo lvextend ",
            "sudo qemu-img resize ",
            "qemu-img resize ",
        ),
    }
    _ALLOWED_STEP_KEYS = {
        "title",
        "command",
        "run_mode",
        "detail",
        "risky",
        "timeout",
        "artifact_path",
        "manifest_path",
        "domain_xml_path",
        "artifact_reference",
        "snapshot_name",
        "storage_backend",
        "use_wsl",
        "run_as_root",
    }
    _ALLOWED_ARTIFACT_PREFIXES = ("~/vpsdash/backups", "/var/lib/vpsdash/backups")

    def __init__(self, config: PlatformConfig) -> None:
        self.config = config

    def capture_inventory(self, host: dict[str, Any]) -> dict[str, Any]:
        return inventory_snapshot(host)

    def execute_task_plan(
        self,
        host: dict[str, Any],
        steps: list[dict[str, Any]],
        *,
        dry_run: bool = False,
        signature: str,
        policy: str,
        target_type: str,
        target_id: str,
        progress_callback: Any | None = None,
        cancel_callback: Any | None = None,
    ) -> list[dict[str, Any]]:
        validated = self._validate_steps(steps)
        self._verify_signed_plan(validated, signature, policy, target_type, target_id)
        self._validate_command_policy(validated, policy)
        if any(step.get("run_mode") == "remote" for step in validated) and not host.get("ssh_host"):
            raise ValueError("Remote task steps require an SSH host.")
        return execute_plan(
            host,
            validated,
            dry_run=dry_run,
            progress_callback=progress_callback,
            should_cancel=cancel_callback,
        )

    def materialize_file(self, host: dict[str, Any], source_path: str, destination: Path) -> Path:
        self._validate_artifact_source(source_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        host_mode = str(host.get("host_mode") or host.get("mode") or "").strip().lower()
        if host.get("ssh_host"):
            result = fetch_remote_file(host, source_path, destination, timeout=7200)
            if not result.get("ok"):
                raise RuntimeError(result.get("stderr") or f"Failed to fetch {source_path}")
            return destination
        if host_mode in {"remote-linux", "windows-remote", "windows-wsl-remote"}:
            raise RuntimeError("Remote artifact materialization requires an SSH host.")

        if host_mode in WINDOWS_LOCAL_MODES:
            source = resolve_local_wsl_path(host, source_path, timeout=120)
        else:
            source = Path(str(source_path).replace("~", str(Path.home())))
        shutil.copy2(source, destination)
        return destination

    def _validate_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(steps) > 128:
            raise ValueError("Task plan too large.")
        validated: list[dict[str, Any]] = []
        for step in steps:
            unknown_keys = set(step) - self._ALLOWED_STEP_KEYS
            if unknown_keys:
                raise ValueError(f"Unsupported task step keys: {sorted(unknown_keys)}")
            run_mode = step.get("run_mode", "local")
            if run_mode not in {"local", "remote"}:
                raise ValueError(f"Unsupported run mode: {run_mode}")
            command = step.get("command", "")
            if not isinstance(command, str) or not command.strip():
                raise ValueError("Task plan commands must be non-empty strings.")
            if len(command) > 8192:
                raise ValueError("Task command exceeds maximum length.")
            if not isinstance(step.get("title", ""), str) or not step.get("title", "").strip():
                raise ValueError("Task steps require a title.")
            if "timeout" in step:
                timeout = int(step.get("timeout") or 0)
                if timeout <= 0 or timeout > 14400:
                    raise ValueError("Task timeout must be between 1 and 14400 seconds.")
            validated.append(step)
        return validated

    def _validate_artifact_source(self, source_path: str) -> None:
        normalized = str(source_path or "").replace("\\", "/").strip()
        if not normalized:
            raise ValueError("Artifact source path is required.")
        parts = PurePosixPath(normalized).parts
        if ".." in parts:
            raise ValueError("Artifact paths may not traverse parent directories.")
        local_allowed = (Path.home() / "vpsdash" / "backups").as_posix()
        allowed_prefixes = [*self._ALLOWED_ARTIFACT_PREFIXES, local_allowed]
        if not any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in allowed_prefixes):
            raise ValueError(f"Artifact path is outside allowed backup roots: {source_path}")

    def _verify_signed_plan(
        self,
        steps: list[dict[str, Any]],
        signature: str,
        policy: str,
        target_type: str,
        target_id: str,
    ) -> None:
        payload = {
            "policy": policy,
            "target_type": target_type,
            "target_id": str(target_id),
            "steps": steps,
        }
        if not verify_json_signature(self.config, payload, signature):
            raise ValueError("Task plan signature verification failed.")

    def _validate_command_policy(self, steps: list[dict[str, Any]], policy: str) -> None:
        if policy.startswith("doplet-") and policy not in self._POLICY_PREFIXES:
            prefixes = ("virsh ",)
        else:
            prefixes = self._POLICY_PREFIXES.get(policy)
        if not prefixes:
            raise ValueError(f"Unsupported task execution policy: {policy}")
        for step in steps:
            command = step.get("command", "").strip()
            if not command.startswith(prefixes):
                raise ValueError(f"Command is outside the allowed policy envelope for {policy}: {command[:120]}")


class HostAgent:
    def __init__(self, config: PlatformConfig) -> None:
        self.config = config
        self.runtime = HostAgentRuntime(config)

    def capture_inventory(self, host: dict[str, Any]) -> dict[str, Any]:
        endpoint = self._agent_endpoint(host)
        if not endpoint:
            return self.runtime.capture_inventory(host)
        payload = self._daemon_json(host, "/inventory", {})
        return payload["snapshot"]

    def execute_task_plan(
        self,
        host: dict[str, Any],
        steps: list[dict[str, Any]],
        *,
        dry_run: bool = False,
        signature: str,
        policy: str,
        target_type: str,
        target_id: str,
        progress_callback: Any | None = None,
        cancel_callback: Any | None = None,
    ) -> list[dict[str, Any]]:
        endpoint = self._agent_endpoint(host)
        if not endpoint:
            return self.runtime.execute_task_plan(
                host,
                steps,
                dry_run=dry_run,
                signature=signature,
                policy=policy,
                target_type=target_type,
                target_id=target_id,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        payload = self._daemon_json(
            host,
            "/execute",
            {
                "steps": steps,
                "dry_run": dry_run,
                "signature": signature,
                "policy": policy,
                "target_type": target_type,
                "target_id": str(target_id),
            },
        )
        results = payload.get("results") or []
        total = max(len(results), 1)
        if progress_callback:
            seen: list[dict[str, Any]] = []
            for index, result in enumerate(results, start=1):
                seen.append(result)
                progress_callback(index, total, result, list(seen))
        return results

    def materialize_file(self, host: dict[str, Any], source_path: str, destination: Path) -> Path:
        endpoint = self._agent_endpoint(host)
        if not endpoint:
            return self.runtime.materialize_file(host, source_path, destination)
        raw = self._daemon_binary(host, "/materialize", {"source_path": source_path})
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return destination

    def _agent_endpoint(self, host: dict[str, Any]) -> str:
        settings = self._agent_settings(host)
        explicit = str(settings.get("agent_endpoint") or "").strip()
        if explicit:
            return explicit.rstrip("/")
        host_mode = str(host.get("host_mode") or host.get("mode") or "").strip().lower()
        if host_mode in {*LINUX_LOCAL_MODES, *WINDOWS_LOCAL_MODES}:
            return f"http://{self.config.host_agent_bind_host}:{self.config.host_agent_port}"
        if host.get("ssh_host"):
            return ""
        return ""

    def _agent_settings(self, host: dict[str, Any]) -> dict[str, Any]:
        inventory = host.get("inventory") or {}
        config = inventory.get("config") or host.get("config") or {}
        merged = dict(config)
        for key in ("agent_endpoint", "agent_secret", "agent_mode"):
            if host.get(key):
                merged[key] = host.get(key)
        return merged

    def _agent_secret(self, host: dict[str, Any]) -> str:
        settings = self._agent_settings(host)
        return str(settings.get("agent_secret") or self.config.agent_secret_key)

    def _daemon_json(self, host: dict[str, Any], path: str, body: dict[str, Any]) -> dict[str, Any]:
        request_url, payload, headers = self._daemon_request_parts(host, path, body)
        request = urllib.request.Request(request_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=7200) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Host agent request failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError:
            if str(self._agent_settings(host).get("agent_mode") or "").strip().lower() == "required":
                raise
            return self._local_fallback(host, path, body)

    def _daemon_binary(self, host: dict[str, Any], path: str, body: dict[str, Any]) -> bytes:
        request_url, payload, headers = self._daemon_request_parts(host, path, body)
        request = urllib.request.Request(request_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=7200) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Host agent request failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError:
            if str(self._agent_settings(host).get("agent_mode") or "").strip().lower() == "required":
                raise
            if path == "/materialize":
                with tempfile.TemporaryDirectory(prefix="vpsh-agent-") as temp_dir:
                    temp_path = Path(temp_dir) / Path(str(body.get("source_path") or "").replace("~", "")).name
                    self.runtime.materialize_file(host, str(body.get("source_path") or ""), temp_path)
                    return temp_path.read_bytes()
            raise RuntimeError(f"Unsupported local agent binary fallback path: {path}")

    def _daemon_request_parts(self, host: dict[str, Any], path: str, body: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, str]]:
        endpoint = self._agent_endpoint(host)
        timestamp = int(time.time())
        payload = {
            "timestamp": timestamp,
            "body": {
                "host": host,
                **body,
            },
        }
        headers = {
            "Content-Type": "application/json",
            "X-VpsH-Agent-Signature": sign_json_with_key(self._agent_secret(host), payload),
            "X-VpsH-Agent-Timestamp": str(timestamp),
        }
        return f"{endpoint}{path}", payload, headers

    def _local_fallback(self, host: dict[str, Any], path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/inventory":
            return {"snapshot": self.runtime.capture_inventory(host)}
        if path == "/execute":
            return {
                "results": self.runtime.execute_task_plan(
                    host,
                    body.get("steps") or [],
                    dry_run=bool(body.get("dry_run")),
                    signature=str(body.get("signature") or ""),
                    policy=str(body.get("policy") or ""),
                    target_type=str(body.get("target_type") or ""),
                    target_id=str(body.get("target_id") or ""),
                )
            }
        raise RuntimeError(f"Unsupported local agent fallback path: {path}")


def materialize_via_runtime(config: PlatformConfig, host: dict[str, Any], source_path: str) -> bytes:
    runtime = HostAgentRuntime(config)
    with tempfile.TemporaryDirectory(prefix="vpsh-agent-") as temp_dir:
        temp_path = Path(temp_dir) / Path(str(source_path).replace("~", "")).name
        runtime.materialize_file(host, source_path, temp_path)
        return temp_path.read_bytes()

