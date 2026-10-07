"""国内网络: 镜像地址拼装、默认兜底与失败提示。"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ("gh-proxy.com", "ghfast.top", "ghproxy.net", "gh.llkk.cc")


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
        self.assertEqual(len(got), len(DEFAULTS) + 1)     # 直连 + 各默认加速站
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

    def test_caddy_binary_uses_resume_and_long_timeout(self):
        """Caddy 包 18MB, 国内慢: 必须断点续传 + 放宽超时, 否则永远下不完。"""
        src = (ROOT / "lib" / "caddy.sh").read_text(encoding="utf-8")
        self.assertIn("-C -", src)
        self.assertIn("--max-time 900", src)

    def test_caddy_download_aborts_slow_direct(self):
        """直连通但很慢时要主动放弃换镜像, 不能干等十几分钟。"""
        src = (ROOT / "lib" / "caddy.sh").read_text(encoding="utf-8")
        self.assertIn("--speed-limit 102400", src)
        self.assertIn("--speed-time 15", src)

    def test_caddy_sha_is_pinned(self):
        """Caddy 的哈希要钉在代码里: 国内连 checksums.txt 都经常拿不到。"""
        src = (ROOT / "lib" / "caddy.sh").read_text(encoding="utf-8")
        self.assertIn("caddy_pinned_sha", src)
        self.assertIn("v2.11.7:amd64", src)
        self.assertIn("a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7", src)
        self.assertNotIn("--retry 3 --retry-delay 2 --connect-timeout 15 --max-time 900", src)

    def test_ip_detection_has_china_reachable_sources(self):
        src = (ROOT / "lib" / "wizard.sh").read_text(encoding="utf-8")
        for host in ("ip.sb", "myip.ipip.net", "ipinfo.io"):
            self.assertIn(host, src)

    def test_no_placeholder_left_in_shipped_files(self):
        """发版文件里不能留下模板占位符(set -u 下会变成 unbound variable)。"""
        bad = "$" + "{D}"
        names = ("wgaio.sh", "bin/wgaio", "lib/core.sh", "lib/install.sh", "lib/upgrade.sh",
                 "lib/wizard.sh", "lib/caddy.sh", "lib/mirror.sh", "lib/panel.sh",
                 "lib/status.sh", "lib/uninstall.sh", "lib/menu.sh", "lib/backup.sh")
        for rel in names:
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn(bad, src, rel)
            self.assertNotIn("@@{", src, rel)

    def test_github_get_runs_under_set_u(self):
        """github_get 曾经留了个占位符, set -u 下直接 unbound variable, 升级全废。"""
        script = ("set -u\n"
                  'ROOT="$PWD"\n. "$ROOT/lib/core.sh"\n'
                  "WGAIO_NO_MIRROR=1\n"
                  'github_get "http://127.0.0.1:9/nope" "$PWD/out.json" || true\n'
                  'echo "HTTP=$GITHUB_HTTP"\n')
        r = self._run(script)
        self.assertNotIn("unbound variable", r.stderr)
        self.assertIn("HTTP=", r.stdout)

    def test_failure_messages_mention_mirror(self):
        # MIRROR_HINT 定义在 core.sh; 引导脚本自带一份; caddy.sh 复用 ${MIRROR_HINT}
        for rel in ("wgaio.sh", "lib/core.sh"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("WGAIO_MIRROR=https://gh-proxy.com/", src, rel)
        self.assertIn("MIRROR_HINT", (ROOT / "lib" / "caddy.sh").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
