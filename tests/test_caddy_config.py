"""lib/caddy.sh: 面板 HTTPS 的 Caddyfile 生成与端口策略。

注意: 这些脚本一律写进 run.sh 再用 `bash run.sh` 跑。在 Windows 上把带 $VAR 的
脚本直接塞给 `bash -c`，中间那层启动器会先做一次变量展开, 结果和 Linux 不一致。
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CADDY = os.environ.get("WGAIO_CADDY_BIN") or shutil.which("caddy")

HEAD = 'ROOT="."; . "$ROOT/lib/core.sh"; . "$ROOT/lib/caddy.sh"; '


def make_sandbox(tmp):
    root = Path(tmp)
    (root / "lib").mkdir()
    for f in ("core.sh", "caddy.sh"):
        os.symlink(ROOT / "lib" / f, root / "lib" / f)
    return root


class CaddyConfigTests(unittest.TestCase):
    def _sandbox(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        try:
            return make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")

    def _run(self, script, env=None, root=None):
        root = root or self._sandbox()
        (root / "run.sh").write_text(script, encoding="utf-8")
        prefix = ""
        if env:
            prefix = " ".join("%s=%s" % kv for kv in env.items()) + " "
        r = subprocess.run(["bash", "-c", prefix + "bash run.sh"], cwd=str(root),
                           capture_output=True, text=True, timeout=60, encoding="utf-8")
        return r, root

    def _render(self, mode, challenge, bind="0.0.0.0"):
        script = (HEAD + 'render_caddyfile wg.example.com 8443 8888 %s "%s" out.Caddyfile %s '
                  '&& cat out.Caddyfile') % (mode, challenge, bind)
        r, _ = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_acme_http_challenge_never_grabs_443(self):
        out = self._render("acme", "http")
        self.assertIn("wg.example.com:8443", out)
        self.assertIn("disable_tlsalpn_challenge", out)   # 只用 80, 不抢 443
        self.assertNotIn("disable_http_challenge", out)
        self.assertIn("reverse_proxy 127.0.0.1:8888", out)
        self.assertIn("auto_https disable_redirects", out)
        self.assertIn("storage file_system", out)

    def test_acme_tlsalpn_challenge(self):
        out = self._render("acme", "tls-alpn")
        self.assertIn("disable_http_challenge", out)
        self.assertNotIn("disable_tlsalpn_challenge", out)

    def test_internal_self_signed(self):
        out = self._render("internal", "")
        self.assertIn("tls internal", out)
        self.assertNotIn("issuer acme", out)

    def test_off_is_plain_http(self):
        out = self._render("off", "")
        self.assertIn("http://wg.example.com:8443", out)
        self.assertNotIn("issuer acme", out)
        self.assertNotIn("tls internal", out)

    def test_bind_restricts_to_inner_address(self):
        self.assertIn("bind 10.66.66.1", self._render("internal", "", bind="10.66.66.1"))
        self.assertNotIn("bind 0.0.0.0", self._render("internal", ""))

    def test_pick_challenge_prefers_80_then_443(self):
        # 屏蔽 ss, 让结果只由 WGAIO_BUSY_TCP 决定(否则本机真的占着 80 就会飘)
        script = "PATH=/nonexistent; export PATH; " + HEAD + "pick_challenge"
        self.assertEqual(self._run(script, {"WGAIO_BUSY_TCP": ""})[0].stdout.strip(), "http")
        self.assertEqual(self._run(script, {"WGAIO_BUSY_TCP": "80"})[0].stdout.strip(), "tls-alpn")
        self.assertEqual(self._run(script, {"WGAIO_BUSY_TCP": "80,443"})[0].stdout.strip(), "")

    def test_unit_uses_wgaio_own_paths(self):
        script = (HEAD + 'write_wgaio_caddy_unit /usr/local/bin/caddy "./wgaio-caddy.service"; '
                  'cat "./wgaio-caddy.service"')
        r, _ = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wgaio panel HTTPS front", r.stdout)
        self.assertIn("--config /etc/wgaio/Caddyfile", r.stdout)
        self.assertIn("WantedBy=multi-user.target", r.stdout)
        self.assertIn("/var/lib/wgaio-caddy", r.stdout)

    def test_migrate_old_config_to_caddy(self):
        """老配置(没有 panel_backend_port)迁移: 后端留在老端口, 对外换 8443。"""
        root = self._sandbox()
        cfg = {"vpn_cidr": "10.66.66.0/24", "wg_port": 51820,
               "endpoint": "203.0.113.7:51820", "client_dns": "1.1.1.1",
               "lan_cidrs": [], "panel_bind": "0.0.0.0", "panel_port": 8888,
               "panel_token_hash": "x" * 64, "default_mode": "split",
               "tls_mode": "acme", "tls_cn": "wg.example.com",
               "tls_cert": "/opt/wgaio/certs/wgaio.crt",
               "tls_key": "/opt/wgaio/certs/wgaio.key", "panel_path": "wgaio-abc123"}
        (root / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        r, _ = self._run(HEAD + 'migrate_config_for_caddy "./config.json"; cat "./config.json"', root=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(got["panel_backend_port"], 8888)
        self.assertEqual(got["panel_port"], 8443)
        self.assertEqual(got["tls_mode"], "acme")
        self.assertEqual(got["tls_cert"], "")
        self.assertIn("Caddy 前置", r.stdout)

    def test_migrate_is_idempotent(self):
        root = self._sandbox()
        cfg = {"panel_port": 8443, "panel_backend_port": 8888, "tls_mode": "acme",
               "panel_bind": "0.0.0.0", "tls_cn": "wg.example.com"}
        (root / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        r, _ = self._run(HEAD + 'migrate_config_for_caddy "./config.json"; cat "./config.json"', root=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(got["panel_port"], 8443)
        self.assertNotIn("Caddy 前置", r.stdout)

    def test_install_restarts_backend_after_migration(self):
        """迁移过配置后必须重启后端, 否则老进程还绑着 0.0.0.0:老端口(明文对外)。"""
        src = (ROOT / "lib" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("systemctl try-restart wgaio-panel", src)
        self.assertLess(src.index("install_wgaio_caddy"),
                        src.index("systemctl try-restart wgaio-panel"))

    @unittest.skipUnless(CADDY, "需要 caddy 才能校验(设 WGAIO_CADDY_BIN)")
    def test_generated_files_pass_real_caddy(self):
        for mode, challenge in (("acme", "http"), ("acme", "tls-alpn"),
                                ("internal", ""), ("off", "")):
            out = self._render(mode, challenge)
            tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
            self.addCleanup(tmp.cleanup)
            f = Path(tmp.name) / "Caddyfile"
            f.write_text(out, encoding="utf-8")
            r = subprocess.run([CADDY, "validate", "--config", str(f)],
                               capture_output=True, text=True, timeout=60, encoding="utf-8")
            self.assertEqual(r.returncode, 0, "%s: %s" % (mode, r.stderr or r.stdout))


if __name__ == "__main__":
    unittest.main()
