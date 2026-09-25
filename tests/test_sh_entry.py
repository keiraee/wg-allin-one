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


if __name__ == "__main__":
    unittest.main()