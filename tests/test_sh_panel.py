import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PanelUnitTests(unittest.TestCase):
    def test_unit_file_contents(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "wgaio-panel.service"
        env = dict(os.environ, WGAIO_ROOT=str(ROOT), WGAIO_UNIT_OUT=str(out))
        r = subprocess.run(
            ["bash", "-c",
             '. lib/core.sh; . lib/panel.sh; write_panel_unit "%s"' % ROOT],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
            env=env, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        unit = out.read_text(encoding="utf-8")
        self.assertIn("ExecStart=", unit)
        self.assertIn("--serve", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("StandardError=journal", unit)   # wg_set_peer 告警进 journal
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_panel_status_without_systemd(self):
        env = dict(os.environ, WGAIO_ROOT=str(ROOT))
        r = subprocess.run(["bash", "wgaio.sh", "panel", "status"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           timeout=60, env=env, encoding="utf-8")
        self.assertIn("systemd", (r.stdout + r.stderr).lower())


if __name__ == "__main__":
    unittest.main()
