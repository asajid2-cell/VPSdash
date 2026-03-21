import unittest

from vpsdash.planner import generate_plan, merge_template


class PlannerTests(unittest.TestCase):
    def test_generate_plan_contains_stages(self):
        host = {"mode": "remote-linux", "name": "Prod VPS", "ssh_host": "1.2.3.4", "ssh_user": "root"}
        project = {
            "id": "project-1",
            "name": "Sample",
            "slug": "sample",
            "repo_url": "https://example.com/repo.git",
            "branch": "main",
            "deploy_path": "~/apps/sample",
            "env": [],
            "services": [{"name": "app", "public_port": 5000}],
            "persistent_paths": ["data"],
            "backup_paths": ["data"],
            "health_checks": [{"title": "Health", "command": "echo ok"}],
            "domains": ["example.com"],
            "primary_domain": "example.com"
        }
        plan = generate_plan(host, project)
        self.assertTrue(plan["stages"])
        self.assertEqual(plan["summary"]["host_mode"], "remote-linux")

    def test_merge_template_preserves_project_override(self):
        template = {"id": "generic", "name": "Generic", "env": [{"key": "A", "value": "1"}]}
        project = {"name": "Override", "env": [{"key": "B", "value": "2"}]}
        merged = merge_template(template, project)
        self.assertEqual(merged["name"], "Override")
        self.assertEqual(merged["env"][0]["key"], "B")

    def test_generate_plan_tolerates_empty_services(self):
        host = {"mode": "linux-local", "name": "Local"}
        project = {
            "id": "project-2",
            "name": "No Services Yet",
            "slug": "no-services",
            "repo_url": "",
            "branch": "main",
            "deploy_path": "~/apps/no-services",
            "env": [],
            "services": [],
            "persistent_paths": [],
            "backup_paths": [],
            "health_checks": [],
            "domains": [],
            "primary_domain": "example.com",
        }
        plan = generate_plan(host, project)
        self.assertTrue(plan["stages"])


if __name__ == "__main__":
    unittest.main()
