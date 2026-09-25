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
        all_env = {"WGAIO_SKIP_DETECT": "1"}
        if extra_env:
            all_env.update(extra_env)
        env_cmd = " ".join("%s=%s" % kv for kv in all_env.items()) + " "
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

    def test_stage_files_inplace_no_crash(self):
        """Regression: stage_files must not cp file onto itself (the pre-fix crash)."""
        script = (
            'set -Eeuo pipefail; '
            'ROOT="$(pwd)"; '
            '. lib/core.sh; . lib/install.sh; '
            'stage_files "$(pwd)"'
        )
        r = subprocess.run(["bash", "-c", script],
                           capture_output=True, text=True, timeout=60,
                           cwd=str(ROOT), encoding="utf-8")
        self.assertEqual(r.returncode, 0,
                         "stage_files in-place crashed (rc=%d): %s" % (r.returncode, r.stderr))
        self.assertIn("跳过复制", r.stderr + r.stdout)

    def test_sync_config_inplace_no_crash(self):
        """Regression: sync_config must not cp config.json onto itself."""
        cfg = ROOT / "config.json"
        existed = cfg.exists()
        if not existed:
            cfg.write_text('{"test": true}\n', encoding="utf-8")
        try:
            script = (
                'set -Eeuo pipefail; '
                'ROOT="$(pwd)"; '
                '. lib/core.sh; . lib/install.sh; '
                'sync_config "$(pwd)"'
            )
            r = subprocess.run(["bash", "-c", script],
                               capture_output=True, text=True, timeout=60,
                               cwd=str(ROOT), encoding="utf-8")
            self.assertEqual(r.returncode, 0,
                             "sync_config in-place crashed (rc=%d): %s" % (r.returncode, r.stderr))
        finally:
            if not existed:
                cfg.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
