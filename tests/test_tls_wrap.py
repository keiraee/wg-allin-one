import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class TlsWrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS, core.BASE)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.BASE = root
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")
        panel = root / "panel"
        panel.mkdir()
        (panel / "index.html").write_text("<!doctype html>", encoding="utf-8")
        (panel / "style.css").write_text(":root{}", encoding="utf-8")
        (panel / "app.js").write_text("// placeholder", encoding="utf-8")
        core._sessions.clear()

        self.cert_dir = root / "certs"
        self.cert_dir.mkdir()
        self.cert_path = str(self.cert_dir / "test.crt")
        self.key_path = str(self.cert_dir / "test.key")

        openssl = shutil.which("openssl")
        if not openssl:
            self.skipTest("需要 openssl 生成测试证书")

        r = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", self.key_path, "-out", self.cert_path,
             "-days", "1", "-nodes", "-subj", "/CN=test.local"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            self.skipTest("openssl 证书生成失败: %s" % r.stderr)

        self.cfg = {
            "vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
            "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split",
            "wg_port": 51820, "panel_bind": "127.0.0.1", "panel_port": 0,
            "panel_token_hash": core.hash_token("topsecret"),
            "tls_cert": self.cert_path,
            "tls_key": self.key_path,
            "tls_cn": "test.local",
        }
        self.httpd = core.start_server(self.cfg)
        self.port = self.httpd.server_address[1]

        # Wrap with TLS
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert_path, self.key_path)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)

        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        core.WG_CONF, core.CLIENTS, core.BASE = self._orig
        self.tmp.cleanup()

    def _https_request(self, method, path, body=None, cookie=None):
        ctx = ssl._create_unverified_context()
        url = "https://127.0.0.1:%d%s" % (self.port, path)
        data, headers = None, {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10, context=ctx) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8")

    def test_tls_static_page(self):
        st, hd, body = self._https_request("GET", "/")
        self.assertEqual(st, 200, body)
        self.assertIn("text/html", hd.get("Content-Type", ""))

    def test_tls_api_status_via_login(self):
        st, hd, body = self._https_request("POST", "/api/login", {"token": "topsecret"})
        self.assertEqual(st, 200, body)
        cookie = hd.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(cookie, "登录后应返回会话 Cookie")

        st, hd, body = self._https_request("GET", "/api/status", cookie=cookie)
        self.assertEqual(st, 200, body)
        data = json.loads(body)
        self.assertTrue(data["ok"])

    def test_tls_config_keys_present(self):
        self.assertIn("tls_cert", self.cfg)
        self.assertTrue(len(self.cfg["tls_cert"]) > 0)
        self.assertTrue(Path(self.cfg["tls_cert"]).is_file())
        self.assertTrue(Path(self.cfg["tls_key"]).is_file())


if __name__ == "__main__":
    unittest.main()
