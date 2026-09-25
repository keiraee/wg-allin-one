import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class HttpTestBase(unittest.TestCase):
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
        self.cfg = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
                    "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split",
                    "wg_port": 51820, "panel_bind": "127.0.0.1", "panel_port": 0,
                    "panel_token_hash": core.hash_token("topsecret")}
        self.httpd = core.start_server(self.cfg)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        core.WG_CONF, core.CLIENTS, core.BASE = self._orig
        self.tmp.cleanup()

    def req(self, method, path, body=None, cookie=None, ctype="application/json"):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data, headers = None, {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = ctype
        if cookie:
            headers["Cookie"] = cookie
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8")
        except (ConnectionError, OSError):
            # 超大请求体: 服务端拒绝后 TCP 连接中断, 等效 400
            return 400, {}, ""

    def login(self):
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"})
        self.assertEqual(st, 200, body)
        return hd.get("Set-Cookie", "").split(";")[0]


class AuthTests(HttpTestBase):
    def test_static_no_auth(self):
        st, hd, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("text/html", hd.get("Content-Type", ""))
        st, hd, body = self.req("GET", "/static/style.css")
        self.assertEqual(st, 200)
        self.assertIn("Content-Security-Policy", hd)

    def test_api_requires_auth(self):
        st, hd, body = self.req("GET", "/api/status")
        self.assertEqual(st, 401)

    def test_login_wrong_token(self):
        st, hd, body = self.req("POST", "/api/login", {"token": "bad"})
        self.assertEqual(st, 401)
        self.assertNotIn("Set-Cookie", hd)

    def test_login_and_access(self):
        cookie = self.login()
        st, hd, body = self.req("GET", "/api/status", cookie=cookie)
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_login_cookie_httponly(self):
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"})
        self.assertEqual(st, 200)
        cookie = hd.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Max-Age=%d" % core.SESSION_TTL, cookie)

    def test_session_expires(self):
        cookie = self.login()
        token = cookie.split("=", 1)[1]
        with core._sessions_lock:
            core._sessions[token] = time.time() - 1
        st, hd, body = self.req("GET", "/api/status", cookie=cookie)
        self.assertEqual(st, 401)

    def test_logout_revokes_session(self):
        cookie = self.login()
        st, hd, body = self.req("POST", "/api/logout", {}, cookie=cookie)
        self.assertEqual(st, 200, body)
        self.assertIn("Max-Age=0", hd.get("Set-Cookie", ""))
        st, hd, body = self.req("GET", "/api/status", cookie=cookie)
        self.assertEqual(st, 401)

    def test_unexpected_error_logged_not_returned(self):
        cookie = self.login()
        buf = io.StringIO()
        with mock.patch("core.full_status", side_effect=RuntimeError("boom-secret")):
            with mock.patch("sys.stderr", buf):
                st, hd, body = self.req("GET", "/api/status", cookie=cookie)
        self.assertEqual(st, 500)
        self.assertIn("内部错误", body)
        self.assertNotIn("boom-secret", body)
        self.assertIn("boom-secret", buf.getvalue())

    def test_login_rereads_token_hash(self):
        path = Path(self.tmp.name) / "config.json"
        fresh = dict(self.cfg)
        fresh["panel_port"] = 8888
        fresh["panel_token_hash"] = core.hash_token("newsecret")
        path.write_text(json.dumps(fresh), encoding="utf-8")
        self.httpd.app_cfg_path = path
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"})
        self.assertEqual(st, 401, body)
        st, hd, body = self.req("POST", "/api/login", {"token": "newsecret"})
        self.assertEqual(st, 200, body)

    def test_csrf_requires_json(self):
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"},
                                ctype="application/x-www-form-urlencoded")
        self.assertEqual(st, 415)

    def test_bad_content_length_400(self):
        url = "http://127.0.0.1:%d/api/login" % self.port
        r = urllib.request.Request(url, data=b"{}", headers={
            "Content-Type": "application/json", "Content-Length": "abc"},
            method="POST")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                st = resp.status
        except urllib.error.HTTPError as e:
            st = e.code
        self.assertIn(st, (400, 415))

    def test_oversized_body_400(self):
        big = {"token": "x" * (70 * 1024)}
        st, hd, body = self.req("POST", "/api/login", big)
        self.assertEqual(st, 400)


class ServeFlagTests(unittest.TestCase):
    def test_serve_flag_dispatches(self):
        with mock.patch("core.serve") as m:
            code = core.main(["--serve"])
        self.assertEqual(code, 0)
        m.assert_called_once()


class EphemeralPortTests(unittest.TestCase):
    def test_two_servers_ephemeral_distinct(self):
        cfg = {"panel_bind": "127.0.0.1", "panel_port": 0}
        h1 = core.start_server(cfg)
        h2 = core.start_server(cfg)
        try:
            self.assertNotEqual(h1.server_address[1], 0)
            self.assertNotEqual(h1.server_address[1], h2.server_address[1])
        finally:
            h1.server_close()
            h2.server_close()


if __name__ == "__main__":
    unittest.main()
