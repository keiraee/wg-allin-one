import os
import subprocess
import tempfile
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
        wd = [ln.split("=", 1)[1] for ln in unit.splitlines() if ln.startswith("WorkingDirectory=")][0]
        self.assertIn("Environment=WGAIO_BASE=%s" % wd, unit)
        self.assertIn("After=network.target wg-quick@wg0.service", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_panel_status_without_systemd(self):
        env = dict(os.environ, WGAIO_ROOT=str(ROOT))
        r = subprocess.run(["bash", "wgaio.sh", "panel", "status"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           timeout=60, env=env, encoding="utf-8")
        out = r.stdout + r.stderr
        self.assertTrue("wgaio-panel" in out or "systemd" in out, out)

    def test_unit_passes_systemd_verify(self):
        """systemd-analyze 存在时必须校验单元语法(曾因路径引号炸过)。"""
        import shutil
        if not shutil.which("systemd-analyze"):
            self.skipTest("无 systemd-analyze")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "wgaio-panel.service"
        r = subprocess.run(
            ["bash", "-c",
             '. lib/core.sh; . lib/panel.sh; WGAIO_UNIT_OUT="%s" write_panel_unit /opt/wgaio' % out],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        v = subprocess.run(["systemd-analyze", "verify", str(out)],
                           capture_output=True, text=True, timeout=60, encoding="utf-8")
        combined = v.stdout + v.stderr
        self.assertNotIn("fatal error", combined, combined)
        self.assertNotIn("bad unit file setting", combined, combined)


if __name__ == "__main__":
    unittest.main()
