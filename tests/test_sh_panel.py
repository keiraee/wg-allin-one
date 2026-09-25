import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PanelUnitTests(unittest.TestCase):
    def test_unit_file_contents(self):
        out_rel = "_test_unit.service"
        out = ROOT / out_rel
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        r = subprocess.run(
            ["bash", "-c",
             '. lib/core.sh; . lib/panel.sh; WGAIO_UNIT_OUT="$PWD/%s" write_panel_unit' % out_rel],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
            encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        unit = out.read_text(encoding="utf-8")
        self.assertIn("ExecStart=", unit)
        self.assertIn("--serve", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("StandardOutput=journal", unit)   # 补上 spec 审查点出的漏断言
        self.assertIn("StandardError=journal", unit)
        self.assertIn("WorkingDirectory=", unit)
        self.assertIn("After=network.target wg-quick@wg0.service", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_panel_status_without_systemd(self):
        env = dict(os.environ, WGAIO_ROOT=str(ROOT))
        r = subprocess.run(["bash", "wgaio.sh", "panel", "status"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           timeout=60, env=env, encoding="utf-8")
        self.assertIn("systemd", (r.stdout + r.stderr).lower())


if __name__ == "__main__":
    unittest.main()
