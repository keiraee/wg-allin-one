import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_sandbox(tmp):
    root = Path(tmp)
    (root / "lib").mkdir()
    for f in ("core.sh", "core.py", "wizard.sh", "install.sh"):
        os.symlink(ROOT / "lib" / f, root / "lib" / f)
    os.symlink(ROOT / "wgaio.sh", root / "wgaio.sh")
    return root


class WizardTests(unittest.TestCase):
    def _run_wizard(self, heredoc):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        script = ("WGAIO_SKIP_DETECT=1 bash wgaio.sh install --wizard-only <<'EOF'\n"
                  "%s\nEOF" % heredoc)
        r = subprocess.run(["bash", "-c", script], cwd=str(root),
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8")
        return r, root

    def test_wizard_writes_config_and_token(self):
        r, root = self._run_wizard(
            "\n"                      # vpn_cidr 默认
            "\n"                      # wg_port 默认
            "203.0.113.7:51820\n"     # endpoint
            "\n"                      # client_dns 默认
            "\n"                      # lan_cidrs 默认空
            "\n"                      # panel_bind 默认
            "\n"                      # panel_port 默认
            "\n")                     # 流量模式默认
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["endpoint"], "203.0.113.7:51820")
        self.assertEqual(cfg["vpn_cidr"], "10.66.66.0/24")
        self.assertEqual(cfg["wg_port"], 51820)
        self.assertEqual(len(cfg["panel_token_hash"]), 64)
        self.assertIn("令牌", r.stdout)
        self.assertIn("请立即保存", r.stdout)

    def test_wizard_rejects_bad_endpoint(self):
        r, root = self._run_wizard(
            "\n"
            "\n"
            "not-an-endpoint\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("endpoint", r.stderr)

    def test_wizard_rejects_bad_port(self):
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "abc\n" "\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("端口", r.stderr)

    def test_wizard_rejects_bad_mode(self):
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "\n" "foo\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("1 或 2", r.stderr)

    def test_wizard_public_access_bind(self):
        r, root = self._run_wizard(
            "\n"                      # vpn_cidr 默认
            "\n"                      # wg_port 默认
            "203.0.113.7:51820\n"     # endpoint
            "\n"                      # client_dns 默认
            "\n"                      # lan_cidrs 默认空
            "2\n"                     # 面板访问: 公网
            "\n"                      # panel_port 默认
            "\n")                     # 流量模式默认
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["panel_bind"], "0.0.0.0")

    def test_wizard_eof_rejects_empty_endpoint(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        r = subprocess.run(["bash", "-c",
                            "WGAIO_SKIP_DETECT=1 bash wgaio.sh install --wizard-only </dev/null"],
                           cwd=str(root), capture_output=True, text=True,
                           timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, 1)
        self.assertIn("endpoint", r.stderr)


if __name__ == "__main__":
    unittest.main()
