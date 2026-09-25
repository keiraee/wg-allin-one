import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_sha256sums_covers_shipped_files(self):
        sums = (ROOT / "SHA256SUMS").read_text(encoding="utf-8")
        for name in ("wgaio.sh", "lib/core.py", "lib/core.sh", "lib/wizard.sh",
                     "lib/install.sh", "lib/user.sh", "lib/panel.sh",
                     "lib/upgrade.sh", "lib/uninstall.sh", "lib/status.sh",
                     "lib/logs.sh", "lib/menu.sh", "bin/wgaio",
                     "panel/index.html", "panel/style.css", "panel/app.js"):
            self.assertIn(name, sums)

    def test_sha256sums_verifies(self):
        r = subprocess.run(["sha256sum", "-c", "SHA256SUMS"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           timeout=120, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ci_workflow_runs_tests(self):
        yml = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("unittest", yml)
        self.assertIn("ubuntu-latest", yml)

    def test_readme_chinese_quickstart(self):
        md = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("快速开始", md)
        self.assertIn("wgaio", md)
        self.assertIn("安全组", md)
        self.assertNotIn("Hcy", md)                                    # real password must not appear


if __name__ == "__main__":
    unittest.main()
