"""国内网络: 镜像地址拼装、默认兜底与失败提示。"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ("gh-proxy.com", "ghfast.top", "ghproxy.net")


def make_sandbox(tmp):
    root = Path(tmp)
    (root / "lib").mkdir()
    for f in ("core.sh", "upgrade.sh", "caddy.sh"):
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

    def _urls(self, env=""):
        script = ('ROOT="$PWD"\n. "$ROOT/lib/core.sh"\n. "$ROOT/lib/upgrade.sh"\n'
                  '%s\ngithub_urls https://api.github.com/x\n' % env)
        r = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split()

    def test_direct_first_then_builtin_mirrors(self):
        got = self._urls()
        self.assertEqual(got[0], "https://api.github.com/x")
        self.assertEqual(len(got), 4)                     # 直连 + 3 个默认加速站
        for m in DEFAULTS:
            self.assertIn("https://%s/https://api.github.com/x" % m, got)

    def test_no_mirror_disables_fallback(self):
        self.assertEqual(self._urls("WGAIO_NO_MIRROR=1"), ["https://api.github.com/x"])

    def test_custom_mirror_replaces_defaults(self):
        got = self._urls('WGAIO_MIRROR="https://a.com/ https://b.com"')
        self.assertEqual(got, [
            "https://api.github.com/x",
            "https://a.com/https://api.github.com/x",
            "https://b.com/https://api.github.com/x",
        ])

    def test_bootstrap_has_same_default_list(self):
        src = (ROOT / "wgaio.sh").read_text(encoding="utf-8")
        for name in ("boot_urls()", "boot_get()", "boot_fetch()"):
            self.assertIn(name, src)
        for m in DEFAULTS:
            self.assertIn(m, src)

    def test_caddy_download_uses_mirror_helper(self):
        src = (ROOT / "lib" / "caddy.sh").read_text(encoding="utf-8")
        self.assertIn("github_curl", src)

    def test_ip_detection_has_china_reachable_sources(self):
        src = (ROOT / "lib" / "wizard.sh").read_text(encoding="utf-8")
        for host in ("ip.sb", "myip.ipip.net", "ipinfo.io"):
            self.assertIn(host, src)

    def test_failure_messages_mention_mirror(self):
        # MIRROR_HINT 定义在 core.sh; 引导脚本自带一份; caddy.sh 复用 ${MIRROR_HINT}
        for rel in ("wgaio.sh", "lib/core.sh"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("WGAIO_MIRROR=https://gh-proxy.com/", src, rel)
        self.assertIn("MIRROR_HINT", (ROOT / "lib" / "caddy.sh").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
