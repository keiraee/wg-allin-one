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
        self.assertIn("logs", r.stderr)
        self.assertIn("status|install", r.stderr)

    def test_cert_without_config(self):
        r = run_sh("cert")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("还没有配置", r.stderr)

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

    def test_bare_version_no_download(self):
        """Bootstrap mode: bare wgaio.sh (no lib/) prints version without downloading."""
        import tempfile as _tf
        tmp = _tf.mkdtemp()
        bare = Path(tmp) / "wgaio.sh"
        bare.write_bytes((ROOT / "wgaio.sh").read_bytes())
        r = subprocess.run(["bash", "wgaio.sh", "version"],
                           capture_output=True, text=True, timeout=30,
                           cwd=tmp, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wgaio", r.stdout)
        self.assertIn("0.2.3", r.stdout)
        # Must not have created any download artifacts
        self.assertFalse((Path(tmp) / "lib").exists())

    def test_mismatch_offline_exits_with_hint(self):
        """套件缺失时自愈被 WGAIO_OFFLINE 拦住 → rc 1, stderr 提示。"""
        import tempfile as _tf
        tmp = _tf.mkdtemp()
        root = Path(tmp)
        (root / "lib").mkdir()
        (root / "wgaio.sh").write_bytes((ROOT / "wgaio.sh").read_bytes())
        # 有 core.sh 但缺 core.py / wizard.sh，视为套件不完整
        (root / "lib" / "core.sh").write_text("# stub\n", encoding="utf-8")
        r = subprocess.run(
            ["bash", "-c", 'WGAIO_OFFLINE=1 exec bash wgaio.sh status'],
            capture_output=True, text=True, timeout=30,
            cwd=str(root), encoding="utf-8")
        self.assertEqual(r.returncode, 1, "expected rc 1 for offline incomplete suite")
        self.assertIn("离线模式", r.stderr)

    def test_matching_suite_no_bootstrap(self):
        """套件齐全 → 不进入引导模式。"""
        r = run_sh("version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0.2.3", r.stdout)
        self.assertNotIn("引导模式", r.stdout)

    def test_find_python_skips_broken_stub(self):
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
