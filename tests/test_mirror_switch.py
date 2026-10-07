"""包源自动换国内镜像: 探测、替换、备份与还原。"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HEAD = 'ROOT="."; . "$ROOT/lib/core.sh"; . "$ROOT/lib/mirror.sh"; '


class MirrorSwitchTests(unittest.TestCase):
    def _sandbox(self, os_release="ID=ubuntu\nVERSION_CODENAME=jammy\n"):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "mirror.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        (root / "os-release").write_text(os_release, encoding="utf-8", newline="\n")
        (root / "sources.list").write_text("deb http://archive.ubuntu.com/ubuntu/ jammy main\n",
                                           encoding="utf-8", newline="\n")
        (root / "sources.list.d").mkdir()
        (root / "sources.list.d" / "extra.list").write_text("deb http://x/ y main\n",
                                                            encoding="utf-8", newline="\n")
        return root

    def _run(self, script, root):
        (root / "run.sh").write_text(script, encoding="utf-8", newline="\n")
        return subprocess.run(["bash", "-c", "bash run.sh"], cwd=str(root),
                              capture_output=True, text=True, timeout=60, encoding="utf-8")

    def _env(self):
        return ('WGAIO_OS_RELEASE="$PWD/os-release" WGAIO_APT_SOURCES="$PWD/sources.list" '
                'WGAIO_APT_PARTS="$PWD/sources.list.d" ')

    def test_switch_rewrites_sources_and_keeps_backup(self):
        root = self._sandbox()
        # 探测函数打桩: 直接给一个镜像
        script = (self._env() + HEAD +
                  'cn_mirror_probe() { printf %s https://mirrors.aliyun.com; }\n'
                  'pkg_mirror_switch\n')
        r = self._run(script, root)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = (root / "sources.list").read_text(encoding="utf-8")
        self.assertIn("https://mirrors.aliyun.com/ubuntu/ jammy main restricted universe multiverse", got)
        self.assertIn("jammy-security", got)
        self.assertTrue((root / "sources.list.wgaio.bak").exists())
        self.assertIn("archive.ubuntu.com",
                      (root / "sources.list.wgaio.bak").read_text(encoding="utf-8"))
        # 其它源文件被挪开
        self.assertFalse((root / "sources.list.d" / "extra.list").exists())
        self.assertTrue((root / "sources.list.d.wgaio-saved" / "extra.list").exists())
        self.assertIn("wgaio mirror restore", r.stdout + r.stderr)

    def test_debian_uses_debian_security(self):
        root = self._sandbox("ID=debian\nVERSION_CODENAME=bookworm\n")
        script = (self._env() + HEAD +
                  'cn_mirror_probe() { printf %s https://mirrors.ustc.edu.cn; }\n'
                  'pkg_mirror_switch\n')
        r = self._run(script, root)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = (root / "sources.list").read_text(encoding="utf-8")
        self.assertIn("https://mirrors.ustc.edu.cn/debian/ bookworm main contrib non-free non-free-firmware", got)
        self.assertIn("https://mirrors.ustc.edu.cn/debian-security/ bookworm-security", got)

    def test_probe_failure_leaves_sources_alone(self):
        root = self._sandbox()
        before = (root / "sources.list").read_text(encoding="utf-8")
        script = (self._env() + HEAD +
                  'cn_mirror_probe() { return 1; }\n'
                  'pkg_mirror_switch || echo "switch-failed"\n')
        r = self._run(script, root)
        self.assertIn("switch-failed", r.stdout)
        self.assertEqual(before, (root / "sources.list").read_text(encoding="utf-8"))
        self.assertFalse((root / "sources.list.wgaio.bak").exists())

    def test_restore_puts_everything_back(self):
        root = self._sandbox()
        original = (root / "sources.list").read_text(encoding="utf-8")
        script = (self._env() + HEAD +
                  'cn_mirror_probe() { printf %s https://mirrors.aliyun.com; }\n'
                  'pkg_mirror_switch\n'
                  'pkg_mirror_restore\n')
        r = self._run(script, root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(original, (root / "sources.list").read_text(encoding="utf-8"))
        self.assertTrue((root / "sources.list.d" / "extra.list").exists())
        self.assertFalse((root / "sources.list.wgaio.bak").exists())

    def test_install_deps_falls_back_to_mirror_switch(self):
        src = (ROOT / "lib" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("deps_install_once", src)
        self.assertIn("pkg_mirror_switch", src)
        self.assertLess(src.index("deps_install_once; then"), src.index("pkg_mirror_switch"))

    def test_cli_exposes_mirror_subcommand(self):
        src = (ROOT / "wgaio.sh").read_text(encoding="utf-8")
        self.assertIn("cmd_mirror", src)
        self.assertIn("mirror [restore]", src)


if __name__ == "__main__":
    unittest.main()
