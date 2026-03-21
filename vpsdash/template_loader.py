from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TemplateCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.templates_dir = self.root / "templates"

    def list_templates(self) -> list[dict[str, Any]]:
        templates: list[dict[str, Any]] = []
        for path in sorted(self.templates_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                template = json.load(handle)
            template["template_file"] = path.name
            templates.append(template)
        return templates

    def get_template(self, template_id: str) -> dict[str, Any]:
        for template in self.list_templates():
            if template.get("id") == template_id:
                return template
        raise KeyError(f"Unknown template: {template_id}")


class DefaultCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.defaults_dir = self.root / "defaults"

    def list_defaults(self) -> list[dict[str, Any]]:
        defaults: list[dict[str, Any]] = []
        if not self.defaults_dir.exists():
            return defaults
        for path in sorted(self.defaults_dir.glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                default_item = json.load(handle)
            default_item["default_file"] = path.name
            defaults.append(default_item)
        return defaults
