import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("deploy_cpanel.py")
SPEC = importlib.util.spec_from_file_location("deploy_cpanel", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DeployCPanelTests(unittest.TestCase):
    def test_normalize_host_accepts_cpanel_url_and_port(self):
        self.assertEqual(
            MODULE.normalize_host("https://server199.web-hosting.com:2083/"),
            "server199.web-hosting.com",
        )

    def test_normalize_host_rejects_paths(self):
        with self.assertRaises(RuntimeError):
            MODULE.normalize_host("server199.web-hosting.com/cpanel")

    def test_migration_command_is_shell_parseable_and_uses_migrate(self):
        command = MODULE.migration_command(
            "/home/gustzyif/finance_app",
            "/home/gustzyif/virtualenv/finance_app/3.13/bin/python",
            "/home/gustzyif/finance_app/.github-migrate-test.done",
            "/home/gustzyif/finance_app/.github-migrate-test.lock",
            "/home/gustzyif/finance_app/.github-migrate-test.log",
        )
        result = subprocess.run(
            ["/bin/sh", "-n"],
            input=command,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("migrate", command)
        self.assertIn("no_input=True", command)


if __name__ == "__main__":
    unittest.main()
