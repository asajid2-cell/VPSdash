from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_STATE: dict[str, Any] = {
    "hosts": [],
    "projects": [],
    "instances": [],
    "defaults": [],
    "settings": {
        "preferred_theme": "signal",
    },
}


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.data_dir = self.root / "data"
        self.state_path = self.data_dir / "state.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self.write(DEFAULT_STATE)

    def read(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return deepcopy(DEFAULT_STATE)
        with self.state_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        merged = deepcopy(DEFAULT_STATE)
        merged.update(data or {})
        merged["hosts"] = data.get("hosts", []) if isinstance(data, dict) else []
        merged["projects"] = data.get("projects", []) if isinstance(data, dict) else []
        merged["instances"] = data.get("instances", []) if isinstance(data, dict) else []
        merged["defaults"] = data.get("defaults", []) if isinstance(data, dict) else []
        merged["settings"] = {
            **DEFAULT_STATE["settings"],
            **(data.get("settings", {}) if isinstance(data, dict) else {}),
        }
        return merged

    def write(self, state: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(DEFAULT_STATE)
        payload.update(state or {})
        with self.state_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return payload

    def upsert_host(self, host: dict[str, Any]) -> dict[str, Any]:
        state = self.read()
        hosts = state["hosts"]
        index = next((i for i, item in enumerate(hosts) if item.get("id") == host.get("id")), None)
        if index is None:
            hosts.append(host)
        else:
            hosts[index] = host
        return self.write(state)

    def upsert_project(self, project: dict[str, Any]) -> dict[str, Any]:
        state = self.read()
        projects = state["projects"]
        index = next((i for i, item in enumerate(projects) if item.get("id") == project.get("id")), None)
        if index is None:
            projects.append(project)
        else:
            projects[index] = project
        return self.write(state)

    def upsert_default(self, default_profile: dict[str, Any]) -> dict[str, Any]:
        state = self.read()
        defaults = state["defaults"]
        index = next((i for i, item in enumerate(defaults) if item.get("id") == default_profile.get("id")), None)
        if index is None:
            defaults.append(default_profile)
        else:
            defaults[index] = default_profile
        return self.write(state)

    def upsert_instance(self, instance: dict[str, Any]) -> dict[str, Any]:
        state = self.read()
        instances = state["instances"]
        index = next((i for i, item in enumerate(instances) if item.get("id") == instance.get("id")), None)
        if index is None:
            instances.append(instance)
        else:
            instances[index] = instance
        return self.write(state)

    def delete_instance(self, instance_id: str) -> dict[str, Any]:
        state = self.read()
        state["instances"] = [item for item in state["instances"] if item.get("id") != instance_id]
        return self.write(state)
