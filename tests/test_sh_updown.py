import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_bash(script, cwd=None):
    return subprocess.run(["bash", "-c", script], cwd=str(cwd or ROOT),
                          capture_output=True, text=True, timeout=60,
                          encoding="utf-8")


class UpgradeTests(unittest.TestCase):
    def test_sha256sums_check_detects_tamper(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "upgrade.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        (root / "payload.txt").write_text("hello", encoding="utf-8")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; check_sha256',
            cwd=root)
        self.assertEqual(r.returncode, 1)   # 无 SHA256SUMS → 拒绝
        (root / "SHA256SUMS").write_text(
            "0000000000000000000000000000000000000000000000000000000000000000  payload.txt\n",
            encoding="utf-8")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; check_sha256',
            cwd=root)
        self.assertEqual(r.returncode, 1)   # 哈希不符 → 拒绝
        self.assertIn("校验", r.stderr)


class UninstallTests(unittest.TestCase):
    def test_uninstall_dry_run_lists_targets(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "uninstall.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        r = run_bash('ROOT="$(pwd)"; . lib/core.sh; . lib/uninstall.sh; cmd_uninstall --dry-run',
                     cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("config.json", out)
        self.assertIn("--keep-clients", out)


class StatusLogsTests(unittest.TestCase):
    def test_status_shows_summary(self):
        r = run_bash("bash wgaio.sh status")
        self.assertIn("wgaio", (r.stdout + r.stderr).lower())

    def test_logs_mentions_journal(self):
        r = run_bash("bash wgaio.sh logs")
        out = (r.stdout + r.stderr).lower()
        self.assertTrue("journal" in out or "systemd" in out)


if __name__ == "__main__":
    unittest.main()