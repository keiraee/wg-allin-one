"""国内网络：WGAIO_MIRROR 的地址拼装与失败提示。"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_sandbox(tmp):
    root = Path(tmp)
    (root / "lib").mkdir()
    for f in ("core.sh", "upgrade.sh"):
        os.symlink(ROOT / "lib" / f, root / "lib" / f)
    return root


class MirrorTests(unittest.TestCase):
    def _run(self, script):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        (root / "run.sh").write_text(script, encoding="utf-8", newline="\n")
        return subprocess.run(["bash", "-c", "bash run.sh"], cwd=str(root),
                              capture_output=True, text=True, timeout=60,
                              encoding="utf-8")

    def _urls(self, mirror):
        script = ('ROOT="$PWD"\n. "$ROOT/lib/core.sh"\n. "$ROOT/lib/upgrade.sh"\n'
                  'WGAIO_MIRROR="%s"\ngithub_urls https://api.github.com/x\n' % mirror)
        r = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split()

    def test_direct_first_then_mirrors(self):
        self.assertEqual(self._urls(""), ["https://api.github.com/x"])
        self.assertEqual(
            self._urls("https://gh-proxy.com"),
            ["https://api.github.com/x", "https://gh-proxy.com/https://api.github.com/x"])

    def test_trailing_slash_normalized_and_multiple(self):
        got = self._urls("https://a.com/ https://b.com")
        self.assertEqual(got, [
            "https://api.github.com/x",
            "https://a.com/https://api.github.com/x",
            "https://b.com/https://api.github.com/x",
        ])

    def test_bootstrap_has_same_helper(self):
        src = (ROOT / "wgaio.sh").read_text(encoding="utf-8")
        for name in ("boot_urls()", "boot_get()", "boot_fetch()"):
            self.assertIn(name, src)
        self.assertIn("WGAIO_MIRROR", src)
        self.assertIn("boot_fetch \"$archive\"", src)

    def test_failure_messages_mention_mirror(self):
        for rel in ("wgaio.sh", "lib/upgrade.sh"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("WGAIO_MIRROR=https://gh-proxy.com/", src, rel)


if __name__ == "__main__":
    unittest.main()
