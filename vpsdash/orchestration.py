from __future__ import annotations

import json
import shlex
import textwrap
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .execution import (
    LINUX_LOCAL_MODES,
    WINDOWS_LOCAL_MODES,
    is_windows_local_mode,
    is_windows_remote_mode,
    run_host_local_command,
    run_local_command,
    run_remote_command,
)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


@dataclass(slots=True)
class CommandStep:
    title: str
    command: str
    run_mode: str
    detail: str = ""
    risky: bool = False
    timeout: int = 120
    use_wsl: bool | None = None
    run_as_root: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload.get("use_wsl") is None:
            payload.pop("use_wsl", None)
        if payload.get("run_as_root") is None:
            payload.pop("run_as_root", None)
        return payload


def _run_mode_for_host(host: dict[str, Any]) -> str:
    host_mode = str(host.get("mode") or host.get("host_mode") or "").strip().lower()
    if host_mode in WINDOWS_LOCAL_MODES or host_mode in LINUX_LOCAL_MODES:
        return "local"
    if host_mode in {"remote-linux", "windows-remote", "windows-wsl-remote"}:
        return "remote"
    return "remote" if host.get("ssh_host") else "local"


def _is_windows_host(host: dict[str, Any]) -> bool:
    return is_windows_local_mode(host) or is_windows_remote_mode(host)


def _windows_native_steps(host: dict[str, Any]) -> list[dict[str, Any]]:
    if not _is_windows_host(host):
        return []
    distro = str(host.get("wsl_distribution") or "Ubuntu").strip() or "Ubuntu"
    run_mode = _run_mode_for_host(host)
    escaped_distro = distro.replace('"', '\\"')
    return [
        CommandStep(
            "Check WSL installation",
            'cmd.exe /d /s /c "(wsl.exe -l -v) 2>&1 & exit /b 0"',
            run_mode,
            detail="Inspect the Windows-side WSL installation and installed distros.",
            timeout=60,
            use_wsl=False,
        ).as_dict(),
        CommandStep(
            "Install WSL distro if missing",
            f'cmd.exe /d /s /c "(wsl.exe -l -q | findstr /ix /c:\\"{escaped_distro}\\" >nul) || wsl.exe --install -d \\"{escaped_distro}\\""',
            run_mode,
            detail="Ensure the selected WSL distro exists before Linux-side hypervisor setup starts.",
            risky=True,
            timeout=2400,
            use_wsl=False,
        ).as_dict(),
    ]


def _host_config(host: dict[str, Any]) -> dict[str, Any]:
    inventory = (host or {}).get("inventory") or {}
    return dict((host or {}).get("config") or inventory.get("config") or {})


def _storage_config(host: dict[str, Any]) -> dict[str, Any]:
    config = _host_config(host)
    return {
        "runtime_root": config.get("runtime_root") or "/var/lib/vpsdash",
        "zfs_pool": config.get("zfs_pool") or "tank",
        "zfs_dataset_root": config.get("zfs_dataset_root") or "doplets",
        "lvm_vg": config.get("lvm_vg") or "vg_vpsdash",
        "lvm_thinpool": config.get("lvm_thinpool") or "thinpool",
        "libvirt_network": config.get("libvirt_network") or "default",
        "zfs_devices": config.get("zfs_devices") or [],
        "lvm_devices": config.get("lvm_devices") or [],
        "lvm_thinpool_size_gb": int(config.get("lvm_thinpool_size_gb") or 200),
    }


def _runtime_root(host: dict[str, Any]) -> str:
    return str(_storage_config(host)["runtime_root"]).rstrip("/") or "/var/lib/vpsdash"


def _runtime_path(host: dict[str, Any], *parts: str) -> str:
    normalized = [str(part).strip("/").replace("\\", "/") for part in parts if str(part).strip("/")]
    if not normalized:
        return _runtime_root(host)
    return f"{_runtime_root(host)}/{'/'.join(normalized)}"


def _instance_root(host: dict[str, Any], slug: str) -> str:
    return _runtime_path(host, "instances", slug)


def _instance_disk_path(host: dict[str, Any], slug: str) -> str:
    return _runtime_path(host, "instances", slug, "root.qcow2")


def _snapshot_disk_path(host: dict[str, Any], slug: str, snapshot_name: str) -> str:
    return _runtime_path(host, "snapshots", slug, f"{snapshot_name}.qcow2")


def _seed_dir(host: dict[str, Any], slug: str) -> str:
    return _runtime_path(host, "seeds", slug)


def _seed_iso_path(host: dict[str, Any], slug: str) -> str:
    return _runtime_path(host, "seeds", slug, "seed.iso")


def _device_xml_path(host: dict[str, Any], slug: str, index: int) -> str:
    return _runtime_path(host, "device-xml", f"{slug}-gpu-{index}.xml")


def _network_xml_path(host: dict[str, Any], slug: str) -> str:
    return _runtime_path(host, "networks", f"{slug}.xml")


def _needs_root(host: dict[str, Any]) -> bool:
    return True


def _zfs_volume_ref(host: dict[str, Any], name: str) -> str:
    storage = _storage_config(host)
    dataset_root = str(storage["zfs_dataset_root"]).strip("/")
    if dataset_root:
        return f"{storage['zfs_pool']}/{dataset_root}/{name}"
    return f"{storage['zfs_pool']}/{name}"


def _zfs_device_path(host: dict[str, Any], name: str) -> str:
    return f"/dev/zvol/{_zfs_volume_ref(host, name)}"


def _lvm_volume_path(host: dict[str, Any], name: str) -> str:
    storage = _storage_config(host)
    return f"/dev/{storage['lvm_vg']}/{name}"


def _lvm_thinpool_ref(host: dict[str, Any]) -> str:
    storage = _storage_config(host)
    return f"{storage['lvm_vg']}/{storage['lvm_thinpool']}"


def _disk_path_for_backend(host: dict[str, Any], slug: str, storage_backend: str) -> str:
    if storage_backend == "zfs":
        return _zfs_device_path(host, f"{slug}-root")
    if storage_backend == "lvm-thin":
        return _lvm_volume_path(host, f"{slug}-root")
    return _instance_disk_path(host, slug)


def _snapshot_reference_for_backend(host: dict[str, Any], slug: str, storage_backend: str, snapshot_name: str) -> str:
    if storage_backend == "zfs":
        return f"{_zfs_volume_ref(host, f'{slug}-root')}@{snapshot_name}"
    if storage_backend == "lvm-thin":
        return _lvm_volume_path(host, f"{slug}-snap-{snapshot_name}")
    return _snapshot_disk_path(host, slug, snapshot_name)


def _libvirt_network_name(host: dict[str, Any], network: dict[str, Any] | None) -> str:
    if network and network.get("name"):
        return str(network["name"])
    return str(_storage_config(host)["libvirt_network"])


def _image_stage_path(host: dict[str, Any], image: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    source_reference = image.get("local_path") or image.get("source_url") or "/var/lib/vpsdash/images/base.qcow2"
    if not str(source_reference).startswith(("http://", "https://")):
        return str(source_reference), []
    image_slug = image.get("slug") or "base-image"
    local_path = _runtime_path(host, "images", f"{image_slug}.qcow2")
    return local_path, [
        CommandStep(
            "Stage base image locally",
            f"mkdir -p {_runtime_path(host, 'images')} && test -f {local_path} || curl -L {source_reference} -o {local_path}",
            _run_mode_for_host(host),
            risky=True,
            timeout=3600,
            run_as_root=_needs_root(host),
        ).as_dict()
    ]


def _cloud_init_steps(host: dict[str, Any], doplet: dict[str, Any]) -> list[dict[str, Any]]:
    slug = doplet["slug"]
    hostname = slug
    bootstrap_user = doplet.get("bootstrap_user", "ubuntu")
    bootstrap_password = str(doplet.get("bootstrap_password") or "").strip()
    ssh_keys = doplet.get("ssh_public_keys", [])
    metadata = dict(doplet.get("metadata_json") or {})
    auth_mode = str(metadata.get("auth_mode") or "").strip() or ("password+ssh" if ssh_keys else "password")
    payload = {
        "hostname": hostname,
        "bootstrap_user": bootstrap_user,
        "bootstrap_password": bootstrap_password,
        "ssh_keys": ssh_keys,
        "auth_mode": auth_mode,
    }
    payload_json = json.dumps(payload)
    seed_dir = _seed_dir(host, slug)
    seed_iso = _seed_iso_path(host, slug)
    ssh_password_enabled = bool(bootstrap_password) and auth_mode in {"password", "password+ssh"}
    password_preamble = f"ssh_pwauth: {'true' if ssh_password_enabled else 'false'}\\n"
    lock_password_line = "    lock_passwd: false\\n" if bootstrap_password else ""
    python_script = "\n".join(
        [
            "import json, pathlib",
            f"payload=json.loads({json.dumps(payload_json)})",
            f"base=pathlib.Path({json.dumps(seed_dir)})",
            "base.mkdir(parents=True, exist_ok=True)",
            (
                "user_data='''#cloud-config\\n"
                f"preserve_hostname: false\\n"
                f"hostname: {hostname}\\n"
                f"{password_preamble}"
                "users:\\n"
                "  - default\\n"
                f"  - name: {bootstrap_user}\\n"
                "    sudo: ALL=(ALL) ALL\\n"
                "    groups: [sudo]\\n"
                "    shell: /bin/bash\\n"
                f"{lock_password_line}"
                "    ssh_authorized_keys:\\n'''"
            ),
            "keys=payload.get('ssh_keys') or []",
            "for key in keys:",
            "    user_data += f\"      - {key}\\\\n\"",
            "bootstrap_password = str(payload.get('bootstrap_password') or '').strip()",
            "if bootstrap_password:",
            "    user_data += 'chpasswd:\\n  expire: false\\n  list: |\\n'",
            f"    user_data += f'    {bootstrap_user}:{{bootstrap_password}}\\\\n'",
            "(base/'user-data').write_text(user_data, encoding='utf-8')",
            f"(base/'meta-data').write_text('instance-id: {slug}\\\\nlocal-hostname: {hostname}\\\\n', encoding='utf-8')",
        ]
    )
    command = (
        f"mkdir -p {seed_dir} && "
        f"python3 -c {shlex.quote(python_script)} && "
        f"cloud-localds {seed_iso} {seed_dir}/user-data {seed_dir}/meta-data"
    )
    return [
        CommandStep(
            "Render cloud-init seed",
            command,
            _run_mode_for_host(host),
            timeout=300,
            run_as_root=_needs_root(host),
        ).as_dict()
    ]


def _gpu_attachment_steps(host: dict[str, Any], doplet: dict[str, Any]) -> list[dict[str, Any]]:
    slug = doplet["slug"]
    run_mode = _run_mode_for_host(host)
    steps: list[dict[str, Any]] = []
    for index, assignment in enumerate(doplet.get("gpu_assignments") or []):
        mode = assignment.get("mode") or "passthrough"
        xml_path = _device_xml_path(host, slug, index)
        if mode == "passthrough":
            pci_address = assignment.get("pci_address") or assignment.get("parent_address") or ""
            if not pci_address:
                continue
            render_command = textwrap.dedent(
                f"""\
                mkdir -p {_runtime_path(host, 'device-xml')} && python3 - <<'PY'
                import pathlib
                pci = "{pci_address}"
                domain, bus, slot_func = pci.split(":")
                slot, function = slot_func.split(".")
                xml = f\"\"\"<hostdev mode='subsystem' type='pci' managed='yes'>
                  <source>
                    <address domain='0x{{domain}}' bus='0x{{bus}}' slot='0x{{slot}}' function='0x{{function}}'/>
                  </source>
                </hostdev>
                \"\"\"
                path = pathlib.Path("{xml_path}")
                path.write_text(xml, encoding="utf-8")
                PY"""
            ).strip()
            steps.append(CommandStep(f"Render GPU passthrough XML {index + 1}", render_command, run_mode, timeout=120, run_as_root=_needs_root(host)).as_dict())
            steps.append(CommandStep(f"Attach passthrough GPU {index + 1}", f"virsh attach-device {slug} {xml_path} --config --live", run_mode, risky=True, timeout=120, run_as_root=_needs_root(host)).as_dict())
            continue

        parent_address = assignment.get("parent_address") or assignment.get("pci_address") or ""
        profile_id = assignment.get("profile_id") or assignment.get("mdev_type") or ""
        if not parent_address or not profile_id:
            continue
        render_and_create = textwrap.dedent(
            f"""\
            mkdir -p {_runtime_path(host, 'device-xml')} && python3 - <<'PY'
            import pathlib, uuid
            uuid_value = "{assignment.get('mdev_uuid') or ''}".strip() or str(uuid.uuid4())
            base = pathlib.Path("{_runtime_path(host, 'device-xml')}")
            (base / "{slug}-gpu-{index}.uuid").write_text(uuid_value, encoding="utf-8")
            xml = f\"\"\"<hostdev mode='subsystem' type='mdev' model='vfio-pci' managed='yes'>
              <source>
                <address uuid='{{uuid_value}}'/>
              </source>
            </hostdev>
            \"\"\"
            (base / "{slug}-gpu-{index}.xml").write_text(xml, encoding="utf-8")
            PY
            sudo sh -c "cat {_runtime_path(host, 'device-xml', f'{slug}-gpu-{index}.uuid')} > /sys/bus/pci/devices/{parent_address}/mdev_supported_types/{profile_id}/create"
            """
        ).strip()
        steps.append(CommandStep(f"Create mediated GPU {index + 1}", render_and_create, run_mode, risky=True, timeout=180, run_as_root=_needs_root(host)).as_dict())
        steps.append(CommandStep(f"Attach mediated GPU {index + 1}", f"virsh attach-device {slug} {xml_path} --config --live", run_mode, risky=True, timeout=120, run_as_root=_needs_root(host)).as_dict())
    return steps


def _network_bridge_name(network: dict[str, Any]) -> str:
    bridge_name = str(network.get("bridge_name") or "").strip()
    if bridge_name:
        return bridge_name
    slug = str(network.get("slug") or network.get("name") or "vpsh-net").lower().replace("_", "-")
    slug = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in slug).strip("-") or "vpsh-net"
    return f"vpsh-{slug}"[:15]


def _network_firewall_steps(host: dict[str, Any], network: dict[str, Any]) -> list[dict[str, Any]]:
    policy = dict(network.get("firewall_policy") or {})
    ingress = policy.get("ingress") or []
    egress = policy.get("egress") or []
    allowed_tcp_ports = policy.get("allow_tcp_ports") or []
    if not any([ingress, egress, allowed_tcp_ports]):
        return []
    table_name = f"vpsh_{str(network.get('slug') or network.get('name') or 'net').replace('-', '_')}"
    policy_json = json.dumps({"ingress": ingress, "egress": egress, "allow_tcp_ports": allowed_tcp_ports}).replace('"', '\\"')
    command = textwrap.dedent(
        f"""\
        python3 - <<'PY'
        import json, subprocess
        policy = json.loads("{policy_json}")
        table = "{table_name}"
        subprocess.run(["sudo", "nft", "add", "table", "inet", table], check=False)
        subprocess.run(["sudo", "nft", "add", "chain", "inet", table, "input", "{{", "type", "filter", "hook", "input", "priority", "0", ";", "policy", "accept", ";", "}}"], check=False)
        for port in policy.get("allow_tcp_ports", []):
            subprocess.run(["sudo", "nft", "add", "rule", "inet", table, "input", "tcp", "dport", str(port), "accept"], check=False)
        PY"""
    ).strip()
    return [CommandStep("Apply network firewall policy", command, _run_mode_for_host(host), risky=True, timeout=180, run_as_root=_needs_root(host)).as_dict()]


def host_prepare_plan(host: dict[str, Any]) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    distro = (host.get("distro") or "ubuntu-server-lts").lower()
    storage = _storage_config(host)
    primary_backend = (host.get("primary_storage_backend") or "files").lower()
    package_prefix = "sudo apt"
    if "debian" in distro or "ubuntu" in distro:
        package_prefix = "sudo apt"
    runtime_root = _runtime_root(host)
    runtime_dirs = [
        runtime_root,
        _runtime_path(host, "backups"),
        _runtime_path(host, "images"),
        _runtime_path(host, "instances"),
        _runtime_path(host, "seeds"),
        _runtime_path(host, "snapshots"),
        _runtime_path(host, "device-xml"),
        _runtime_path(host, "networks"),
    ]
    steps = [
        CommandStep("Refresh package metadata", f"{package_prefix} update", run_mode, timeout=600, run_as_root=_needs_root(host)),
        CommandStep(
            "Install hypervisor stack",
            f"{package_prefix} install -y curl python3 pciutils qemu-kvm qemu-utils libvirt-daemon-system libvirt-clients virtinst dnsmasq-base bridge-utils ovmf cloud-image-utils genisoimage",
            run_mode,
            timeout=1800,
            risky=True,
            run_as_root=_needs_root(host),
        ),
        CommandStep(
            "Enable libvirtd",
            "sudo systemctl enable --now libvirtd",
            run_mode,
            risky=True,
            timeout=180,
            run_as_root=_needs_root(host),
        ),
        CommandStep(
            "Prepare VPSdash runtime directories",
            " && ".join(f"mkdir -p {path}" for path in runtime_dirs),
            run_mode,
            timeout=120,
            run_as_root=_needs_root(host),
        ),
    ]
    if primary_backend == "zfs" or storage["zfs_devices"]:
        steps.append(
            CommandStep(
                "Install ZFS tooling",
                f"{package_prefix} install -y zfsutils-linux",
                run_mode,
                timeout=1800,
                risky=True,
                run_as_root=_needs_root(host),
            )
        )
        if storage["zfs_devices"]:
            zfs_devices = " ".join(str(device) for device in storage["zfs_devices"])
            steps.append(
                CommandStep(
                    "Ensure ZFS pool exists",
                    f"sudo zpool list {storage['zfs_pool']} >/dev/null 2>&1 || sudo zpool create -f {storage['zfs_pool']} {zfs_devices}",
                    run_mode,
                    risky=True,
                    timeout=2400,
                    run_as_root=_needs_root(host),
                )
            )
        steps.append(
            CommandStep(
                "Ensure ZFS dataset root exists",
                f"sudo zfs list {_zfs_volume_ref(host, '').rstrip('/')} >/dev/null 2>&1 || sudo zfs create {_zfs_volume_ref(host, '').rstrip('/')}",
                run_mode,
                risky=True,
                timeout=1200,
                run_as_root=_needs_root(host),
            )
        )
    if primary_backend == "lvm-thin" or storage["lvm_devices"]:
        steps.append(
            CommandStep(
                "Install LVM tooling",
                f"{package_prefix} install -y lvm2 thin-provisioning-tools",
                run_mode,
                timeout=1800,
                risky=True,
                run_as_root=_needs_root(host),
            )
        )
        if storage["lvm_devices"]:
            pv_targets = " ".join(str(device) for device in storage["lvm_devices"])
            steps.append(
                CommandStep(
                    "Ensure LVM volume group exists",
                    f"sudo vgs {storage['lvm_vg']} >/dev/null 2>&1 || (sudo pvcreate -ff -y {pv_targets} && sudo vgcreate {storage['lvm_vg']} {pv_targets})",
                    run_mode,
                    risky=True,
                    timeout=2400,
                    run_as_root=_needs_root(host),
                )
            )
        steps.append(
            CommandStep(
                "Ensure LVM thinpool exists",
                f"sudo lvs /dev/{storage['lvm_vg']}/{storage['lvm_thinpool']} >/dev/null 2>&1 || sudo lvcreate -L {storage['lvm_thinpool_size_gb']}G -T {storage['lvm_vg']}/{storage['lvm_thinpool']}",
                run_mode,
                risky=True,
                timeout=1200,
                run_as_root=_needs_root(host),
            )
        )
    return [step.as_dict() if hasattr(step, "as_dict") else step for step in steps]


def inventory_snapshot(host: dict[str, Any]) -> dict[str, Any]:
    commands: dict[str, dict[str, Any]] = {
        "cpu": {"command": "nproc || getconf _NPROCESSORS_ONLN"},
        "memory_mb": {"command": "python3 - <<'PY'\nimport os\nprint(int(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1024 / 1024))\nPY"},
        "memory_available_mb": {"command": "python3 - <<'PY'\nimport re\nvalue = 0\nwith open('/proc/meminfo', 'r', encoding='utf-8') as handle:\n    for line in handle:\n        if line.startswith('MemAvailable:'):\n            match = re.search(r'(\\d+)', line)\n            if match:\n                value = int(match.group(1)) // 1024\n            break\nprint(value)\nPY"},
        "disk": {"command": "df -B1 --output=size,avail / | tail -n 1"},
        "libvirt": {"command": "virsh uri"},
        "gpu": {"command": "lspci -D | grep -Ei 'vga|3d|display' || true"},
        "zfs": {"command": "zpool list -H -o name,size,free 2>/dev/null || true"},
        "zfs_datasets": {"command": "zfs list -H -p -t filesystem,volume -o name,used,avail,volsize,mountpoint 2>/dev/null || true"},
        "lvm": {"command": "sudo lvs --reportformat json 2>/dev/null || true"},
        "vgs": {"command": "sudo vgs --reportformat json 2>/dev/null || true"},
        "pvs": {"command": "sudo pvs --reportformat json 2>/dev/null || true"},
        "iommu": {"command": "find /sys/kernel/iommu_groups -maxdepth 1 -mindepth 1 | wc -l"},
        "mdev": {"command": (
            "python3 - <<'PY'\n"
            "import json, pathlib\n"
            "payload=[]\n"
            "root=pathlib.Path('/sys/bus/pci/devices')\n"
            "for device in root.iterdir() if root.exists() else []:\n"
            "    supported=device/'mdev_supported_types'\n"
            "    if not supported.exists():\n"
            "        continue\n"
            "    for profile in supported.iterdir():\n"
            "        payload.append({\n"
            "            'parent_address': device.name,\n"
            "            'profile_id': profile.name,\n"
            "            'available_instances': int((profile/'available_instances').read_text().strip() or '0') if (profile/'available_instances').exists() else 0,\n"
            "            'name': (profile/'name').read_text().strip() if (profile/'name').exists() else profile.name,\n"
            "            'description': (profile/'description').read_text().strip() if (profile/'description').exists() else '',\n"
            "            'device_api': (profile/'device_api').read_text().strip() if (profile/'device_api').exists() else '',\n"
            "        })\n"
            "print(json.dumps(payload))\n"
            "PY"
        )},
    }
    if _is_windows_host(host):
        commands["windows_host"] = {"command": "hostname", "use_wsl": False}
        commands["wsl_list"] = {"command": "wsl.exe -l -v", "use_wsl": False}
    results: dict[str, Any] = {}
    host_mode = str(host.get("mode") or host.get("host_mode") or "").strip().lower()
    is_remote = host_mode in {"remote-linux", "windows-remote", "windows-wsl-remote"}
    if not is_remote and host_mode not in WINDOWS_LOCAL_MODES and host_mode not in LINUX_LOCAL_MODES:
        is_remote = bool(host.get("ssh_host"))
    for key, spec in commands.items():
        command = str(spec.get("command") or "")
        use_wsl = spec.get("use_wsl")
        run_as_root = bool(spec.get("run_as_root", False))
        if _is_windows_host(host) and use_wsl is not False:
            run_as_root = True
        if is_remote:
            results[key] = run_remote_command(host, command, timeout=30, use_wsl=use_wsl, run_as_root=run_as_root)
        elif _is_windows_host(host):
            results[key] = run_host_local_command(host, command, timeout=30, use_wsl=use_wsl, run_as_root=run_as_root)
        else:
            results[key] = run_local_command(command, timeout=30)
    return results


def doplet_create_plan(host: dict[str, Any], doplet: dict[str, Any], image: dict[str, Any], network: dict[str, Any] | None) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    storage_backend = doplet.get("storage_backend") or host.get("primary_storage_backend") or "files"
    slug = doplet["slug"]
    current_status = str(doplet.get("status") or "draft").strip().lower()
    disk_gb = int(doplet.get("disk_gb") or 20)
    vcpu = int(doplet.get("vcpu") or 1)
    ram_mb = int(doplet.get("ram_mb") or 1024)
    network_name = _libvirt_network_name(host, network)
    image_path, image_steps = _image_stage_path(host, image)
    run_as_root = _needs_root(host)
    if storage_backend == "zfs":
        root_volume_name = f"{slug}-root"
        volume_step = CommandStep(
            "Create ZFS volume",
            f"sudo zfs create -V {disk_gb}G {_zfs_volume_ref(host, root_volume_name)}",
            run_mode,
            risky=True,
            run_as_root=run_as_root,
        )
        disk_path = _zfs_device_path(host, root_volume_name)
        disk_format = "raw"
        population_steps = [
            CommandStep(
                "Populate root volume",
                f"sudo qemu-img convert -f qcow2 -O raw {image_path} {disk_path}",
                run_mode,
                risky=True,
                timeout=1800,
                run_as_root=run_as_root,
            ),
        ]
    elif storage_backend == "lvm-thin":
        root_volume_name = f"{slug}-root"
        volume_step = CommandStep(
            "Create LVM thin volume",
            f"sudo lvcreate -V {disk_gb}G -T {_lvm_thinpool_ref(host)} -n {root_volume_name}",
            run_mode,
            risky=True,
            run_as_root=run_as_root,
        )
        disk_path = _lvm_volume_path(host, root_volume_name)
        disk_format = "raw"
        population_steps = [
            CommandStep(
                "Populate root volume",
                f"sudo qemu-img convert -f qcow2 -O raw {image_path} {disk_path}",
                run_mode,
                risky=True,
                timeout=1800,
                run_as_root=run_as_root,
            ),
        ]
    else:
        disk_path = _instance_disk_path(host, slug)
        volume_step = CommandStep(
            "Create file-backed Doplet disk",
            f"mkdir -p {_instance_root(host, slug)} && qemu-img create -f qcow2 -F qcow2 -b {image_path} {disk_path} {disk_gb}G",
            run_mode,
            risky=True,
            timeout=600,
            run_as_root=run_as_root,
        )
        disk_format = "qcow2"
        population_steps = []

    seed_iso = _seed_iso_path(host, slug)
    steps = [
        *image_steps,
    ]
    if current_status in {"draft", "queued", "provisioning", "error"}:
        cleanup_parts = [
            f"virsh destroy {slug} >/dev/null 2>&1 || true",
            f"virsh undefine {slug} --nvram --remove-all-storage >/dev/null 2>&1 || true",
            f"rm -rf {_seed_dir(host, slug)}",
            f"rm -f {seed_iso}",
        ]
        if storage_backend == "files":
            cleanup_parts.extend([f"rm -f {disk_path}", f"mkdir -p {_instance_root(host, slug)}"])
        steps.append(
            CommandStep(
                "Clear stale Doplet runtime artifacts",
                " && ".join(cleanup_parts),
                run_mode,
                risky=True,
                timeout=180,
                run_as_root=run_as_root,
            )
        )
    steps.extend(
        [
            volume_step,
            *population_steps,
            *_cloud_init_steps(host, doplet),
            CommandStep(
                "Define and start doplet",
                (
                    f"virt-install --name {slug} --memory {ram_mb} --vcpus {vcpu} --import "
                    f"--disk path={disk_path},format={disk_format},bus=virtio "
                    f"--disk path={seed_iso},device=cdrom "
                    f"--network network={network_name},model=virtio --os-variant detect=on,name=linux2022 "
                    f"--console pty,target_type=serial --noautoconsole --graphics none"
                ),
                run_mode,
                risky=True,
                timeout=2400,
                run_as_root=run_as_root,
            ),
        ]
    )
    steps.extend(_gpu_attachment_steps(host, doplet))
    return [step.as_dict() if hasattr(step, "as_dict") else step for step in steps]


def doplet_lifecycle_plan(host: dict[str, Any], slug: str, action: str) -> list[dict[str, Any]]:
    shutdown_command = (
        f"virsh shutdown {slug} >/dev/null 2>&1 || true; "
        f"for i in $(seq 1 18); do "
        f"state=$(virsh domstate {slug} 2>/dev/null || true); "
        f"case \"$state\" in *\"shut off\"*|*\"shutoff\"*|\"\") exit 0 ;; esac; "
        f"sleep 5; "
        f"done; "
        f"virsh destroy {slug} >/dev/null 2>&1 || true; "
        f"for i in $(seq 1 10); do "
        f"state=$(virsh domstate {slug} 2>/dev/null || true); "
        f"case \"$state\" in *\"shut off\"*|*\"shutoff\"*|\"\") exit 0 ;; esac; "
        f"sleep 2; "
        f"done; "
        f"echo 'Timed out waiting for Doplet to stop.' >&2; exit 1"
    )
    actions = {
        "start": f"virsh start {slug}",
        "shutdown": shutdown_command,
        "reboot": f"virsh reboot {slug}",
        "force-stop": f"virsh destroy {slug}",
        "delete": f"virsh destroy {slug} || true && virsh undefine {slug} --remove-all-storage || true",
    }
    command = actions[action]
    return [
        CommandStep(
            f"{action.title()} doplet",
            command,
            _run_mode_for_host(host),
            risky=action in {"force-stop", "delete"},
            run_as_root=_needs_root(host),
        ).as_dict()
    ]


def snapshot_plan(host: dict[str, Any], doplet: dict[str, Any], snapshot_name: str) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    slug = doplet["slug"]
    storage_backend = doplet.get("storage_backend") or host.get("primary_storage_backend") or "files"
    if storage_backend == "zfs":
        artifact_reference = _snapshot_reference_for_backend(host, slug, storage_backend, snapshot_name)
        command = f"sudo zfs snapshot {artifact_reference}"
    elif storage_backend == "lvm-thin":
        artifact_reference = _snapshot_reference_for_backend(host, slug, storage_backend, snapshot_name)
        command = f"sudo lvcreate -s -n {slug}-snap-{snapshot_name} -L 5G {_lvm_volume_path(host, f'{slug}-root')}"
    else:
        artifact_reference = _snapshot_reference_for_backend(host, slug, storage_backend, snapshot_name)
        command = f"mkdir -p {_runtime_path(host, 'snapshots', slug)} && qemu-img convert -f qcow2 -O qcow2 {_instance_disk_path(host, slug)} {artifact_reference}"
    return [
        {
            **CommandStep(
                "Create doplet snapshot",
                command,
                run_mode,
                risky=True,
                timeout=1800,
                run_as_root=_needs_root(host),
            ).as_dict(),
            "snapshot_name": snapshot_name,
            "artifact_reference": artifact_reference,
            "storage_backend": storage_backend,
        }
    ]


def clone_plan(
    host: dict[str, Any],
    source_doplet: dict[str, Any],
    target_doplet: dict[str, Any],
    network: dict[str, Any] | None = None,
    *,
    snapshot_reference: str | None = None,
) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    source_slug = source_doplet["slug"]
    target_slug = target_doplet["slug"]
    storage_backend = target_doplet.get("storage_backend") or host.get("primary_storage_backend") or "files"
    disk_gb = int(target_doplet.get("disk_gb") or source_doplet.get("disk_gb") or 20)
    vcpu = int(target_doplet.get("vcpu") or source_doplet.get("vcpu") or 1)
    ram_mb = int(target_doplet.get("ram_mb") or source_doplet.get("ram_mb") or 1024)
    network_name = network.get("name") if network else "default"
    network_name = _libvirt_network_name(host, network)
    run_as_root = _needs_root(host)
    if storage_backend == "zfs":
        source_reference = snapshot_reference or f"{_zfs_volume_ref(host, f'{source_slug}-root')}@clone-{_now_stamp()}"
        steps: list[dict[str, Any]] = []
        if not snapshot_reference:
            steps.append(
                CommandStep(
                    "Create source snapshot for clone",
                    f"sudo zfs snapshot {source_reference}",
                    run_mode,
                    risky=True,
                    timeout=1800,
                    run_as_root=run_as_root,
                ).as_dict()
            )
        steps.append(
            {
                **CommandStep(
                    "Clone ZFS volume",
                    f"sudo zfs clone {source_reference} {_zfs_volume_ref(host, f'{target_slug}-root')}",
                    run_mode,
                    risky=True,
                    timeout=1800,
                    run_as_root=run_as_root,
                ).as_dict(),
                "artifact_reference": _zfs_device_path(host, f"{target_slug}-root"),
            }
        )
        disk_path = _zfs_device_path(host, f"{target_slug}-root")
        disk_format = "raw"
    elif storage_backend == "lvm-thin":
        disk_path = _lvm_volume_path(host, f"{target_slug}-root")
        source_device = snapshot_reference or _lvm_volume_path(host, f"{source_slug}-root")
        steps = [
            CommandStep(
                "Create clone destination volume",
                f"sudo lvcreate -V {disk_gb}G -T {_lvm_thinpool_ref(host)} -n {target_slug}-root",
                run_mode,
                risky=True,
                timeout=1800,
                run_as_root=run_as_root,
            ).as_dict(),
            {
                **CommandStep(
                    "Copy source volume into clone",
                    f"sudo dd if={source_device} of={disk_path} bs=4M status=none",
                    run_mode,
                    risky=True,
                    timeout=3600,
                    run_as_root=run_as_root,
                ).as_dict(),
                "artifact_reference": disk_path,
            },
        ]
        disk_format = "raw"
    else:
        disk_path = _instance_disk_path(host, target_slug)
        source_device = snapshot_reference or _instance_disk_path(host, source_slug)
        steps = [
            {
                **CommandStep(
                    "Create file-backed clone disk",
                    f"mkdir -p {_instance_root(host, target_slug)} && qemu-img convert -f qcow2 -O qcow2 {source_device} {disk_path}",
                    run_mode,
                    risky=True,
                    timeout=3600,
                    run_as_root=run_as_root,
                ).as_dict(),
                "artifact_reference": disk_path,
            }
        ]
        disk_format = "qcow2"

    steps.append(
        CommandStep(
            "Define and start cloned doplet",
            (
                f"virt-install --name {target_slug} --memory {ram_mb} --vcpus {vcpu} --import "
                f"--disk path={disk_path},format={disk_format},bus=virtio "
                f"--network network={network_name},model=virtio --os-variant detect=on,name=linux2022 "
                f"--console pty,target_type=serial --noautoconsole --graphics none"
            ),
            run_mode,
            risky=True,
            timeout=2400,
            run_as_root=run_as_root,
        ).as_dict()
    )
    steps.extend(_gpu_attachment_steps(host, target_doplet))
    return steps


def restore_plan(
    host: dict[str, Any],
    target_doplet: dict[str, Any],
    snapshot: dict[str, Any],
    network: dict[str, Any] | None = None,
    *,
    in_place: bool,
) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    target_slug = target_doplet["slug"]
    storage_backend = target_doplet.get("storage_backend") or host.get("primary_storage_backend") or "files"
    snapshot_reference = snapshot.get("artifact_reference", "")
    run_as_root = _needs_root(host)
    if in_place:
        if storage_backend == "zfs":
            steps = [
                CommandStep(
                    "Stop target doplet",
                    f"virsh destroy {target_slug} || true",
                    run_mode,
                    risky=True,
                    run_as_root=run_as_root,
                ).as_dict(),
                CommandStep(
                    "Rollback target volume to snapshot",
                    f"sudo zfs rollback -r {snapshot_reference}",
                    run_mode,
                    risky=True,
                    timeout=1800,
                    run_as_root=run_as_root,
                ).as_dict(),
                CommandStep(
                    "Start restored doplet",
                    f"virsh start {target_slug}",
                    run_mode,
                    risky=True,
                    run_as_root=run_as_root,
                ).as_dict(),
            ]
        elif storage_backend == "lvm-thin":
            steps = [
                CommandStep(
                    "Stop target doplet",
                    f"virsh destroy {target_slug} || true",
                    run_mode,
                    risky=True,
                    run_as_root=run_as_root,
                ).as_dict(),
                CommandStep(
                    "Restore snapshot device onto target",
                    f"sudo dd if={snapshot_reference} of={_lvm_volume_path(host, f'{target_slug}-root')} bs=4M status=none",
                    run_mode,
                    risky=True,
                    timeout=3600,
                    run_as_root=run_as_root,
                ).as_dict(),
                CommandStep(
                    "Start restored doplet",
                    f"virsh start {target_slug}",
                    run_mode,
                    risky=True,
                    run_as_root=run_as_root,
                ).as_dict(),
            ]
        else:
            steps = [
                CommandStep(
                    "Stop target doplet",
                    f"virsh destroy {target_slug} || true",
                    run_mode,
                    risky=True,
                    run_as_root=run_as_root,
                ).as_dict(),
                CommandStep(
                    "Restore snapshot file onto target disk",
                    f"qemu-img convert -f qcow2 -O qcow2 {snapshot_reference} {_instance_disk_path(host, target_slug)}",
                    run_mode,
                    risky=True,
                    timeout=3600,
                    run_as_root=run_as_root,
                ).as_dict(),
                CommandStep(
                    "Start restored doplet",
                    f"virsh start {target_slug}",
                    run_mode,
                    risky=True,
                    run_as_root=run_as_root,
                ).as_dict(),
            ]
        return steps

    return clone_plan(host, target_doplet, target_doplet, network, snapshot_reference=snapshot_reference)


def backup_plan(host: dict[str, Any], doplet: dict[str, Any], providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    slug = doplet["slug"]
    timestamp = _now_stamp()
    manifest_path = _runtime_path(host, "backups", f"{slug}-{timestamp}.manifest.json")
    storage_backend = doplet.get("storage_backend") or host.get("primary_storage_backend") or "files"
    run_as_root = _needs_root(host)
    if storage_backend == "zfs":
        artifact_path = _runtime_path(host, "backups", f"{slug}-{timestamp}.zfs.gz")
        archive_command = (
            f"sudo zfs snapshot {_zfs_volume_ref(host, f'{slug}-root')}@backup-{timestamp} && "
            f"sudo zfs send {_zfs_volume_ref(host, f'{slug}-root')}@backup-{timestamp} | gzip > {artifact_path}"
        )
    elif storage_backend == "lvm-thin":
        artifact_path = _runtime_path(host, "backups", f"{slug}-{timestamp}.img.gz")
        archive_command = (
            f"sudo dd if={_lvm_volume_path(host, f'{slug}-root')} bs=4M status=none | gzip > {artifact_path}"
        )
    else:
        artifact_path = _runtime_path(host, "backups", f"{slug}-{timestamp}.qcow2.gz")
        archive_command = f"gzip -c {_instance_disk_path(host, slug)} > {artifact_path}"
    xml_path = _runtime_path(host, "backups", f"{slug}-{timestamp}.xml")
    manifest = {
        "doplet": slug,
        "timestamp": timestamp,
        "artifact_path": artifact_path,
        "domain_xml_path": xml_path,
        "storage_backend": storage_backend,
        "providers": [provider.get("slug") for provider in providers],
        "sha256": {},
    }
    manifest_json = json.dumps(manifest).replace('"', '\\"')

    steps: list[dict[str, Any]] = [
        CommandStep(
            "Create backup manifest",
            (
                f"mkdir -p {_runtime_path(host, 'backups')} && "
                f"python3 - <<'PY'\nimport json, pathlib\nmanifest=json.loads(\"{manifest_json}\")\n"
                f"path=pathlib.Path('{manifest_path}')\n"
                "path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')\nPY"
            ),
            run_mode,
            timeout=60,
            run_as_root=run_as_root,
        ).as_dict(),
        CommandStep(
            "Export doplet domain XML",
            f"mkdir -p {_runtime_path(host, 'backups')} && virsh dumpxml {slug} > {xml_path}",
            run_mode,
            timeout=120,
            run_as_root=run_as_root,
        ).as_dict(),
        {
            **CommandStep(
                "Create backup artifact",
                archive_command,
                run_mode,
                risky=True,
                timeout=7200,
                run_as_root=run_as_root,
            ).as_dict(),
            "artifact_path": artifact_path,
            "manifest_path": manifest_path,
            "domain_xml_path": xml_path,
        },
        CommandStep(
            "Finalize backup manifest with checksums",
            (
                f"python3 - <<'PY'\n"
                f"import hashlib, json, pathlib\n"
                f"manifest_path = pathlib.Path('{manifest_path}')\n"
                f"artifact_path = pathlib.Path('{artifact_path}')\n"
                f"xml_path = pathlib.Path('{xml_path}')\n"
                f"manifest = json.loads(manifest_path.read_text(encoding='utf-8'))\n"
                f"for key, path in [('artifact_path', artifact_path), ('domain_xml_path', xml_path), ('manifest_path', manifest_path)]:\n"
                f"    digest = hashlib.sha256(path.read_bytes()).hexdigest()\n"
                f"    manifest.setdefault('sha256', {{}})[key] = digest\n"
                f"manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')\n"
                f"PY"
            ),
            run_mode,
            timeout=180,
            run_as_root=run_as_root,
        ).as_dict(),
    ]

    for provider in providers:
        if provider.get("provider_type") == "local":
            target = provider.get("root_path") or "~/vpsdash/provider-backups"
            steps.append(
                CommandStep(
                    f"Copy manifest to provider {provider.get('name', provider.get('slug', 'local'))}",
                    f"mkdir -p {target} && cp {manifest_path} {xml_path} {artifact_path} {target}/",
                    run_mode,
                    risky=True,
                    run_as_root=run_as_root,
                ).as_dict()
            )
        else:
            steps.append(
                CommandStep(
                    f"Queue object upload for {provider.get('name', provider.get('slug', 'object'))}",
                    f"printf 'UPLOAD {manifest_path} TO {provider.get('slug')}\\n'",
                    run_mode,
                    detail="Object uploads are handled by the control-plane provider adapter.",
                    run_as_root=run_as_root,
                ).as_dict()
            )
    return steps


def network_apply_plan(host: dict[str, Any], network: dict[str, Any]) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    slug = str(network.get("slug") or network.get("name") or "network")
    bridge_name = _network_bridge_name(network)
    xml_path = _network_xml_path(host, slug)
    run_as_root = _needs_root(host)
    policy_json = json.dumps(
        {
            "name": slug,
            "mode": network.get("mode") or "nat",
            "cidr": network.get("cidr") or "",
            "bridge_name": bridge_name,
            "nat_enabled": bool(network.get("nat_enabled", True)),
        }
    ).replace('"', '\\"')
    render_xml = textwrap.dedent(
        f"""\
        mkdir -p {_runtime_path(host, 'networks')} && python3 - <<'PY'
        import ipaddress, json, pathlib
        payload = json.loads("{policy_json}")
        mode = payload.get("mode") or "nat"
        cidr = payload.get("cidr") or ""
        network = ipaddress.ip_network(cidr, strict=False) if cidr else None
        bridge_name = payload.get("bridge_name") or "vpsh-net"
        gateway = str(next(network.hosts())) if network else "192.168.250.1"
        netmask = str(network.netmask) if network else "255.255.255.0"
        if mode == "bridge":
            xml = f\"\"\"<network>
  <name>{{payload['name']}}</name>
  <forward mode='bridge'/>
  <bridge name='{{bridge_name}}'/>
</network>
\"\"\"
        else:
            forward = "nat" if payload.get("nat_enabled", True) and mode == "nat" else None
            forward_xml = f"<forward mode='{{forward}}'/>" if forward else ""
            xml = f\"\"\"<network>
  <name>{{payload['name']}}</name>
  {{forward_xml}}
  <bridge name='{{bridge_name}}' stp='on' delay='0'/>
  <ip address='{{gateway}}' netmask='{{netmask}}'/>
</network>
\"\"\"
        path = pathlib.Path("{xml_path}")
        path.write_text(xml, encoding="utf-8")
        PY"""
    ).strip()
    steps: list[dict[str, Any]] = []
    if str(network.get("mode") or "nat") == "bridge":
        steps.extend(
            [
                CommandStep("Ensure Linux bridge exists", f"sudo ip link add name {bridge_name} type bridge || true", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
                CommandStep("Bring Linux bridge up", f"sudo ip link set {bridge_name} up", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
            ]
        )
    else:
        steps.append(CommandStep("Enable IPv4 forwarding", "sudo sysctl -w net.ipv4.ip_forward=1", run_mode, risky=True, timeout=60, run_as_root=run_as_root).as_dict())
    steps.extend(
        [
            CommandStep("Render libvirt network XML", render_xml, run_mode, timeout=180, run_as_root=run_as_root).as_dict(),
            CommandStep("Define libvirt network", f"virsh net-define {xml_path}", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
            CommandStep("Start libvirt network", f"virsh net-start {slug} || true", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
            CommandStep("Autostart libvirt network", f"virsh net-autostart {slug}", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
        ]
    )
    steps.extend(_network_firewall_steps(host, network))
    return steps


def network_delete_plan(host: dict[str, Any], network: dict[str, Any]) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    bridge_name = _network_bridge_name(network)
    name = network.get("slug") or network.get("name") or "network"
    table_name = f"vpsh_{str(network.get('slug') or network.get('name') or 'net').replace('-', '_')}"
    run_as_root = _needs_root(host)
    steps: list[dict[str, Any]] = [
        CommandStep("Stop libvirt network", f"virsh net-destroy {name} || true", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
        CommandStep("Undefine libvirt network", f"virsh net-undefine {name} || true", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
        CommandStep("Delete network firewall table", f"sudo nft delete table inet {table_name} || true", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
    ]
    if str(network.get("mode") or "nat") == "bridge":
        steps.append(CommandStep("Remove Linux bridge", f"sudo ip link delete {bridge_name} || true", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict())
    return steps


def resize_plan(host: dict[str, Any], doplet: dict[str, Any], *, target_vcpu: int, target_ram_mb: int, target_disk_gb: int) -> list[dict[str, Any]]:
    run_mode = _run_mode_for_host(host)
    slug = doplet["slug"]
    storage_backend = doplet.get("storage_backend") or host.get("primary_storage_backend") or "files"
    run_as_root = _needs_root(host)
    steps: list[dict[str, Any]] = [
        CommandStep("Resize vCPU allocation", f"virsh setvcpus {slug} {target_vcpu} --config --live", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
        CommandStep("Resize memory ceiling", f"virsh setmaxmem {slug} {target_ram_mb * 1024} --config", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
        CommandStep("Resize live memory", f"virsh setmem {slug} {target_ram_mb * 1024} --config --live", run_mode, risky=True, timeout=120, run_as_root=run_as_root).as_dict(),
    ]
    if storage_backend == "zfs":
        steps.append(
            CommandStep(
                "Resize ZFS root volume",
                f"sudo zfs set volsize={target_disk_gb}G {_zfs_volume_ref(host, f'{slug}-root')}",
                run_mode,
                risky=True,
                timeout=1800,
                run_as_root=run_as_root,
            ).as_dict()
        )
    elif storage_backend == "lvm-thin":
        steps.append(
            CommandStep(
                "Resize LVM thin root volume",
                f"sudo lvextend -L {target_disk_gb}G {_lvm_volume_path(host, f'{slug}-root')} -y",
                run_mode,
                risky=True,
                timeout=1800,
                run_as_root=run_as_root,
            ).as_dict()
        )
    else:
        steps.append(
            CommandStep(
                "Resize qcow2 root disk",
                f"qemu-img resize {_instance_disk_path(host, slug)} {target_disk_gb}G",
                run_mode,
                risky=True,
                timeout=1800,
                run_as_root=run_as_root,
            ).as_dict()
        )
    return steps


