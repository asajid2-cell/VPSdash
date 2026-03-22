from __future__ import annotations

import json
from typing import Any


def _stdout(snapshot: dict[str, Any], key: str) -> str:
    entry = (snapshot or {}).get(key) or {}
    return str(entry.get("stdout") or "").strip()


def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _bytes_to_gib(value: int) -> float:
    if value <= 0:
        return 0.0
    return round(value / (1024**3), 2)


def _parse_json_stdout(snapshot: dict[str, Any], key: str) -> dict[str, Any] | list[Any]:
    raw = _stdout(snapshot, key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _gpu_vendor(line: str) -> str:
    lowered = line.lower()
    if "nvidia" in lowered:
        return "nvidia"
    if "advanced micro devices" in lowered or " amd " in f" {lowered} " or "radeon" in lowered:
        return "amd"
    if "intel" in lowered:
        return "intel"
    return "unknown"


def summarize_inventory(snapshot: dict[str, Any]) -> dict[str, Any]:
    disk_stdout = _stdout(snapshot, "disk")
    disk_parts = [part for part in disk_stdout.split() if part]
    disk_total_bytes = _parse_int(disk_parts[0]) if len(disk_parts) >= 1 else 0
    disk_free_bytes = _parse_int(disk_parts[1]) if len(disk_parts) >= 2 else 0

    gpu_lines = [line.strip() for line in _stdout(snapshot, "gpu").splitlines() if line.strip()]
    mediated_profiles_raw = _parse_json_stdout(snapshot, "mdev")
    mediated_profiles = mediated_profiles_raw if isinstance(mediated_profiles_raw, list) else []
    profiles_by_parent: dict[str, list[dict[str, Any]]] = {}
    for profile in mediated_profiles:
        profiles_by_parent.setdefault(str(profile.get("parent_address") or ""), []).append(profile)
    gpu_devices = [
        {
            "pci_address": line.split()[0] if " " in line else line,
            "name": line.split(" ", 1)[1] if " " in line else line,
            "vendor": _gpu_vendor(line),
            "passthrough_eligible": True,
            "mediated_profiles": profiles_by_parent.get(line.split()[0] if " " in line else line, []),
        }
        for line in gpu_lines
    ]

    zfs_pools: list[dict[str, Any]] = []
    for line in _stdout(snapshot, "zfs").splitlines():
        parts = [part for part in line.split("\t") if part]
        if len(parts) >= 3:
            zfs_pools.append(
                {
                    "name": parts[0],
                    "size": parts[1],
                    "free": parts[2],
                }
            )

    zfs_datasets: list[dict[str, Any]] = []
    for line in _stdout(snapshot, "zfs_datasets").splitlines():
        parts = [part for part in line.split("\t") if part]
        if len(parts) >= 5:
            zfs_datasets.append(
                {
                    "name": parts[0],
                    "used_bytes": _parse_int(parts[1]),
                    "avail_bytes": _parse_int(parts[2]),
                    "volsize_bytes": _parse_int(parts[3]),
                    "mountpoint": parts[4],
                }
            )

    lvm_payload = _parse_json_stdout(snapshot, "lvm")
    vgs_payload = _parse_json_stdout(snapshot, "vgs")
    pvs_payload = _parse_json_stdout(snapshot, "pvs")

    iommu_groups = _parse_int(_stdout(snapshot, "iommu"))
    libvirt_ok = bool((snapshot or {}).get("libvirt", {}).get("ok"))
    mediated_profile_catalog: dict[str, dict[str, Any]] = {}
    for profile in mediated_profiles:
        profile_id = str(profile.get("profile_id") or "")
        if not profile_id:
            continue
        entry = mediated_profile_catalog.setdefault(
            profile_id,
            {
                "profile_id": profile_id,
                "name": profile.get("name") or profile_id,
                "description": profile.get("description") or "",
                "device_api": profile.get("device_api") or "",
                "available_instances": 0,
                "parents": [],
            },
        )
        entry["available_instances"] += int(profile.get("available_instances") or 0)
        parent = profile.get("parent_address")
        if parent and parent not in entry["parents"]:
            entry["parents"].append(parent)

    return {
        "cpu_threads_total": _parse_int(_stdout(snapshot, "cpu")),
        "ram_mb_total": _parse_int(_stdout(snapshot, "memory_mb")),
        "disk_total_bytes": disk_total_bytes,
        "disk_free_bytes": disk_free_bytes,
        "disk_total_gib": _bytes_to_gib(disk_total_bytes),
        "disk_free_gib": _bytes_to_gib(disk_free_bytes),
        "gpu_devices": gpu_devices,
        "gpu_device_count": len(gpu_devices),
        "mediated_profiles": list(mediated_profile_catalog.values()),
        "zfs_pools": zfs_pools,
        "zfs_datasets": zfs_datasets,
        "lvm": lvm_payload,
        "vgs": vgs_payload,
        "pvs": pvs_payload,
        "iommu_groups": iommu_groups,
        "virtualization_ready": libvirt_ok,
        "raw": snapshot,
    }


def gpu_assignment_preflight(host: dict[str, Any], assignments: list[dict[str, Any]] | None) -> dict[str, Any]:
    inventory = (host or {}).get("inventory") or {}
    resources = inventory.get("resources") or {}
    iommu_groups = int(resources.get("iommu_groups") or 0)
    gpu_devices = {item.get("pci_address"): item for item in resources.get("gpu_devices") or [] if item.get("pci_address")}
    mediated_profiles = resources.get("mediated_profiles") or []
    profile_catalog = {str(item.get("profile_id") or ""): item for item in mediated_profiles if item.get("profile_id")}
    parent_profiles: dict[str, set[str]] = {}
    for item in mediated_profiles:
        for parent in item.get("parents") or []:
            parent_profiles.setdefault(str(parent), set()).add(str(item.get("profile_id") or ""))

    warnings: list[str] = []
    errors: list[str] = []
    for assignment in assignments or []:
        mode = str(assignment.get("mode") or "passthrough")
        parent = str(assignment.get("parent_address") or assignment.get("pci_address") or "")
        device = gpu_devices.get(parent)
        if not parent:
            errors.append("GPU assignment is missing a PCI parent address.")
            continue
        if mode == "mediated":
            profile_id = str(assignment.get("profile_id") or assignment.get("mdev_type") or "")
            if not profile_id:
                errors.append(f"Mediated GPU assignment for {parent} is missing a profile id.")
                continue
            if iommu_groups <= 0:
                warnings.append("Mediated GPU assignments usually require IOMMU support; verify the target host configuration before live provisioning.")
            if profile_id not in profile_catalog:
                errors.append(f"Mediated GPU profile {profile_id} is not advertised by the host inventory.")
                continue
            if parent_profiles.get(parent) and profile_id not in parent_profiles.get(parent, set()):
                errors.append(f"Mediated GPU profile {profile_id} is not advertised for parent device {parent}.")
            vendor = (device or {}).get("vendor") or "unknown"
            if vendor == "amd":
                warnings.append(f"AMD mediated profile support is highly stack-specific on {parent}; validate vendor support before live provisioning.")
            elif vendor == "nvidia":
                warnings.append(f"NVIDIA mediated GPU support on {parent} depends on matching host driver and vGPU profile enablement.")
            elif vendor == "intel":
                warnings.append(f"Intel mediated GPU support on {parent} depends on GVT-g or vendor-specific enablement on the target host.")
            else:
                warnings.append(f"Unknown GPU vendor for mediated assignment on {parent}; real-host validation is strongly recommended.")
            continue

        if iommu_groups <= 0:
            errors.append("PCI passthrough requires detectable IOMMU groups on the host.")
        if not device:
            errors.append(f"Passthrough GPU device {parent} is not present in host inventory.")

    return {
        "ok": not errors,
        "warnings": warnings,
        "errors": errors,
    }


def capacity_summary(host: dict[str, Any], doplets: list[dict[str, Any]], *, reserve_cpu: int = 1, reserve_ram_mb: int = 2048, reserve_disk_gb: int = 20) -> dict[str, Any]:
    inventory = (host or {}).get("inventory") or {}
    resources = inventory.get("resources") or {}
    config = (host or {}).get("config") or inventory.get("config") or {}

    reserve_cpu = int(config.get("reserve_cpu_threads") or reserve_cpu)
    reserve_ram_mb = int(config.get("reserve_ram_mb") or reserve_ram_mb)
    reserve_disk_gb = int(config.get("reserve_disk_gb") or reserve_disk_gb)

    total_cpu = int(resources.get("cpu_threads_total") or 0)
    total_ram_mb = int(resources.get("ram_mb_total") or 0)
    total_disk_gb = float(resources.get("disk_total_gib") or 0.0)
    total_gpu = int(resources.get("gpu_device_count") or 0)
    total_mediated_profiles = {
        item.get("profile_id"): int(item.get("available_instances") or 0)
        for item in resources.get("mediated_profiles") or []
        if item.get("profile_id")
    }

    allocated_cpu = sum(int(item.get("vcpu") or 0) for item in doplets)
    allocated_ram_mb = sum(int(item.get("ram_mb") or 0) for item in doplets)
    allocated_disk_gb = sum(float(item.get("disk_gb") or 0) for item in doplets)
    allocated_gpu = 0
    allocated_mediated: dict[str, int] = {}
    for doplet in doplets:
        for assignment in doplet.get("gpu_assignments") or []:
            if (assignment.get("mode") or "passthrough") == "mediated":
                profile_id = str(assignment.get("profile_id") or assignment.get("mdev_type") or "")
                if profile_id:
                    allocated_mediated[profile_id] = allocated_mediated.get(profile_id, 0) + 1
            else:
                allocated_gpu += 1

    usable_cpu = max(total_cpu - reserve_cpu, 0)
    usable_ram_mb = max(total_ram_mb - reserve_ram_mb, 0)
    usable_disk_gb = max(total_disk_gb - reserve_disk_gb, 0.0)
    usable_gpu = total_gpu
    remaining_mediated = {
        profile_id: max(total - allocated_mediated.get(profile_id, 0), 0)
        for profile_id, total in total_mediated_profiles.items()
    }

    return {
        "totals": {
            "cpu_threads": total_cpu,
            "ram_mb": total_ram_mb,
            "disk_gb": total_disk_gb,
            "gpu_devices": total_gpu,
            "mediated_profiles": total_mediated_profiles,
        },
        "reserve": {
            "cpu_threads": reserve_cpu,
            "ram_mb": reserve_ram_mb,
            "disk_gb": reserve_disk_gb,
            "gpu_devices": 0,
            "mediated_profiles": {},
        },
        "usable": {
            "cpu_threads": usable_cpu,
            "ram_mb": usable_ram_mb,
            "disk_gb": usable_disk_gb,
            "gpu_devices": usable_gpu,
            "mediated_profiles": total_mediated_profiles,
        },
        "allocated": {
            "cpu_threads": allocated_cpu,
            "ram_mb": allocated_ram_mb,
            "disk_gb": round(allocated_disk_gb, 2),
            "gpu_devices": allocated_gpu,
            "mediated_profiles": allocated_mediated,
        },
        "remaining": {
            "cpu_threads": max(usable_cpu - allocated_cpu, 0),
            "ram_mb": max(usable_ram_mb - allocated_ram_mb, 0),
            "disk_gb": round(max(usable_disk_gb - allocated_disk_gb, 0.0), 2),
            "gpu_devices": max(usable_gpu - allocated_gpu, 0),
            "mediated_profiles": remaining_mediated,
        },
        "overcommitted": {
            "cpu_threads": allocated_cpu > usable_cpu,
            "ram_mb": allocated_ram_mb > usable_ram_mb,
            "disk_gb": allocated_disk_gb > usable_disk_gb,
            "gpu_devices": allocated_gpu > usable_gpu,
            "mediated_profiles": any(allocated_mediated.get(profile_id, 0) > total for profile_id, total in total_mediated_profiles.items()),
        },
    }


def can_allocate(capacity: dict[str, Any], request: dict[str, Any]) -> tuple[bool, list[str]]:
    totals = capacity.get("totals") or {}
    if (
        int(totals.get("cpu_threads") or 0) <= 0
        or int(totals.get("ram_mb") or 0) <= 0
        or float(totals.get("disk_gb") or 0) <= 0
    ):
        return True, []
    remaining = capacity.get("remaining") or {}
    errors: list[str] = []
    if int(request.get("vcpu") or 0) > int(remaining.get("cpu_threads") or 0):
        errors.append("Requested vCPU exceeds remaining CPU capacity.")
    if int(request.get("ram_mb") or 0) > int(remaining.get("ram_mb") or 0):
        errors.append("Requested RAM exceeds remaining host memory.")
    if float(request.get("disk_gb") or 0) > float(remaining.get("disk_gb") or 0):
        errors.append("Requested disk exceeds remaining storage capacity.")
    physical_gpu_requests = 0
    mediated_requests: dict[str, int] = {}
    for assignment in request.get("gpu_assignments") or []:
        if (assignment.get("mode") or "passthrough") == "mediated":
            profile_id = str(assignment.get("profile_id") or assignment.get("mdev_type") or "")
            if profile_id:
                mediated_requests[profile_id] = mediated_requests.get(profile_id, 0) + 1
        else:
            physical_gpu_requests += 1
    if physical_gpu_requests > int(remaining.get("gpu_devices") or 0):
        errors.append("Requested physical GPU assignments exceed remaining GPU capacity.")
    remaining_profiles = remaining.get("mediated_profiles") or {}
    for profile_id, count in mediated_requests.items():
        if count > int(remaining_profiles.get(profile_id) or 0):
            errors.append(f"Requested mediated GPU profile {profile_id} exceeds remaining host capacity.")
    return (not errors, errors)

