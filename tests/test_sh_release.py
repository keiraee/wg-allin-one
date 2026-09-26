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
                     "lib/logs.sh", "lib/menu.sh", "lib/backup.sh", "lib/qr.py",
                     "bin/wgaio",
                     "panel/index.html", "panel/style.css", "panel/app.js"):
            self.assertIn(name, sums)

    def test_sha256sums_verifies(self):
        import hashlib
        sums = (ROOT / "SHA256SUMS").read_text(encoding="utf-8")
        for line in sums.splitlines():
            line = line.strip()
            if not line:
                continue
            digest, name = line.split(None, 1)
            name = name.lstrip("*").strip()
            p = ROOT / name
            self.assertTrue(p.exists(), "缺少发版文件: %s" % name)
            data = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest,
                             "SHA256SUMS 与文件内容不一致: %s" % name)

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
