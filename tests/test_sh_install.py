import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_sandbox(tmp):
    root = Path(tmp)
    (root / "lib").mkdir()
    (root / "panel").mkdir()
    for f in ("core.sh", "core.py", "wizard.sh", "install.sh"):
        os.symlink(ROOT / "lib" / f, root / "lib" / f)
    for f in ("index.html", "style.css", "app.js"):
        os.symlink(ROOT / "panel" / f, root / "panel" / f)
    os.symlink(ROOT / "wgaio.sh", root / "wgaio.sh")
    return root


class InstallTests(unittest.TestCase):
    def _run_install(self, extra_env=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        env_cmd = ""
        if extra_env:
            env_cmd = " ".join("%s=%s" % kv for kv in extra_env.items()) + " "
        script = (env_cmd +
                  "bash wgaio.sh install --dry-run <<'EOF'\n"
                  "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "\n" "\nEOF")
        r = subprocess.run(["bash", "-c", script], cwd=str(root),
                           capture_output=True, text=True, timeout=120,
                           encoding="utf-8")
        return r, root

    def test_install_dry_run_writes_layout(self):
        r, root = self._run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((root / "config.json").exists())
        self.assertTrue((root / "_stage" / "lib" / "core.py").exists())
        self.assertTrue((root / "_stage" / "panel" / "index.html").exists())
        self.assertIn("安全组", r.stdout)
        self.assertIn("UDP", r.stdout)

    def test_install_dry_run_ok_without_root(self):
        r, root = self._run_install(extra_env={"WGAIO_FORCE_NONROOT": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
