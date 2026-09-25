import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_sh(*args):
    env = dict(os.environ)
    env.setdefault("WGAIO_ROOT", str(ROOT))
    return subprocess.run(["bash", "wgaio.sh", *args],
                          capture_output=True, text=True, timeout=60,
                          cwd=str(ROOT), env=env, encoding="utf-8")


class EntryTests(unittest.TestCase):
    def test_no_args_shows_usage(self):
        r = run_sh()
        self.assertEqual(r.returncode, 2)
        self.assertIn("用法", r.stderr)

    def test_unknown_subcommand(self):
        r = run_sh("frobnicate")
        self.assertEqual(r.returncode, 2)
        self.assertIn("未知子命令", r.stderr)

    def test_version_flag(self):
        r = run_sh("version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("wgaio", r.stdout)

    def test_user_passthrough_help(self):
        r = run_sh("user", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("add", r.stdout)

    def test_find_python_skips_broken_stub(self):
        import shutil
        import tempfile as _tf
        stubdir = _tf.mkdtemp()
        stub = Path(stubdir) / "python3"
        stub.write_text("#!/bin/sh\nexit 49\n", encoding="utf-8")
        stub.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = stubdir + os.pathsep + env.get("PATH", "")
        env["WGAIO_ROOT"] = str(ROOT)
        r = subprocess.run(["bash", "-c", '. lib/core.sh; find_python'],
                           cwd=str(ROOT), capture_output=True, text=True,
                           timeout=60, env=env, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(stubdir, r.stdout)  # 选中的不是假 stub


if __name__ == "__main__":
    unittest.main()