import json
import os
import re
import shutil
import subprocess
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


class JoinScriptTests(unittest.TestCase):
    def test_quoted_heredoc_keeps_shell_metacharacters(self):
        conf = (
            "[Interface]\n"
            "PrivateKey = $(reboot)\n"
            "Address = 10.66.66.2/32\n"
            "\n"
            "[Peer]\n"
            "PublicKey = `id`\n"
            "AllowedIPs = 10.66.66.0/24\n"
        )
        script = core.build_join_script("linux", conf)
        self.assertIn("PrivateKey = $(reboot)\n", script)
        self.assertIn("PublicKey = `id`\n", script)
        self.assertIn("/etc/wireguard/wgaio.conf", script)
        self.assertNotIn("wg0.conf", script)
        self.assertNotIn("全隧道", script)
        self.assertIn("apt-get", script)
        self.assertIn("dnf install", script)
        self.assertIn("pacman -Sy", script)
        self.assertIn("apk add", script)
        self.assertIn("wg-quick@wgaio", script)
        head, _, tail = script.partition("<<'WGAIO_CONF'\n")
        body, sep, _rest = tail.partition("\nWGAIO_CONF\n")
        self.assertTrue(sep)
        self.assertIn("$(reboot)", body)
        self.assertNotIn("$(reboot)", head)
        self.assert_shell_syntax(script)

    def assert_shell_syntax(self, script):
        bash = shutil.which("bash") or shutil.which("sh")
        if not bash:
            return
        fd, name = tempfile.mkstemp(suffix=".sh")
        try:
            os.write(fd, script.encode("utf-8"))
            os.close(fd)
            fd = None
            path = name
            low = bash.replace("/", "\\").lower()
            if os.name == "nt" and low.endswith("system32\\bash.exe"):
                drive, rest = os.path.splitdrive(os.path.abspath(name))
                path = "/mnt/%s%s" % (drive[0].lower(), rest.replace("\\", "/"))
            r = subprocess.run([bash, "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            if fd is not None:
                os.close(fd)
            os.unlink(name)

    def test_mac_script_uses_brew_not_linux_packages(self):
        conf = "[Interface]\nPrivateKey = PRIV\nAllowedIPs = 0.0.0.0/0\n"
        script = core.build_join_script("mac", conf)
        self.assertIn("brew", script)
        self.assertIn("App Store", script)
        self.assertIn("全隧道", script)
        self.assertNotIn("apt-get", script)
        self.assertNotIn("systemctl", script)
        self.assertIn("/etc/wireguard/wgaio.conf", script)
        self.assert_shell_syntax(script)

    def test_signature_expires_and_rejects_a_swap(self):
        secret = core.hash_token("topsecret")
        mac = core.join_mac(secret, "phone", "linux", 1700000000)
        self.assertTrue(core.join_fresh(1700000000, 1700000060))
        self.assertFalse(core.join_fresh(1700000000, 1700000061))
        self.assertFalse(core.join_fresh(1700000000, 1699999999))
        cfg = {"panel_token_hash": secret}
        conf_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(conf_dir, ignore_errors=True))
        orig = core.CLIENTS
        core.CLIENTS = conf_dir
        self.addCleanup(setattr, core, "CLIENTS", orig)
        core.save_client("phone", "[Interface]\nPrivateKey = PRIV\n",
                         {"name": "phone", "disabled": False})
        script = core.render_join_script("phone", "linux", "1700000000", mac, cfg, now=1700000060)
        self.assertIn("PrivateKey = PRIV", script)
        self.assertIsNone(core.render_join_script(
            "phone", "linux", "1700000000", mac, cfg, now=1700000061))
        self.assertIsNone(core.render_join_script(
            "phone", "mac", "1700000000", mac, cfg, now=1700000000))
        self.assertIsNone(core.render_join_script(
            "other", "linux", "1700000000", mac, cfg, now=1700000000))
        bad = mac[:-1] + ("0" if mac[-1] != "0" else "1")
        self.assertIsNone(core.render_join_script(
            "phone", "linux", "1700000000", bad, cfg, now=1700000000))


class JoinHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")
        core._sessions.clear()
        self.cfg = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
                    "client_dns": "1.1.1.1", "lan_cidrs": [],
                    "default_mode": "split", "wg_port": 51820,
                    "panel_bind": "127.0.0.1", "panel_port": 0,
                    "panel_token_hash": core.hash_token("topsecret")}
        self.httpd = core.start_server(self.cfg)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.cookie = ""
        st, hd, _body = self.req("POST", "/api/login", {"token": "topsecret"})
        self.assertEqual(st, 200)
        self.cookie = hd.get("Set-Cookie", "").split(";")[0]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        core.WG_CONF, core.CLIENTS = self._orig
        core._sessions.clear()
        self.tmp.cleanup()

    def req(self, method, path, body=None, cookie=None, host=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data, headers = None, {}
        if cookie is not None:
            headers["Cookie"] = cookie
        elif self.cookie and not path.startswith("/wgaio-join"):
            headers["Cookie"] = self.cookie
        if host:
            headers["Host"] = host
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif method == "POST":
            data = b""
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8")

    def _url_path(self, command):
        m = re.search(r'curl -fsSL "(http://[^"]+)"', command)
        self.assertIsNotNone(m, command)
        url = m.group(1)
        self.assertNotIn("PrivateKey", command)
        prefix = "http://127.0.0.1:%d" % self.port
        self.assertTrue(url.startswith(prefix), url)
        return url[len(prefix):]

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    def test_linux_command_expires_in_one_minute(self, _gen, _pub, _set):
        st, _hd, body = self.req("POST", "/api/peers", {"name": "phone"})
        self.assertEqual(st, 200, body)
        base = int(time.time())
        with mock.patch("core.time.time", return_value=base) as clock:
            st, _hd, body = self.req("POST", "/api/peers/phone/join?os=linux")
            self.assertEqual(st, 200, body)
            data = json.loads(body)
            self.assertEqual(data["expires_in"], 60)
            self.assertTrue(data["plain_http"])
            self.assertTrue(data["loopback"])
            self.assertFalse(data["full"])
            self.assertNotIn("PRIV", data["command"])
            self.assertIn("sudo sh -c", data["command"])
            self.assertIn("mktemp", data["command"])
            path = self._url_path(data["command"])
            st, hd, script = self.req("GET", path)
            self.assertEqual(st, 200, script)
            self.assertIn("text/x-shellscript", hd.get("Content-Type", ""))
            self.assertIn("PrivateKey = PRIV", script)
            self.assertIn("/etc/wireguard/wgaio.conf", script)
            self.assertNotIn("wg0.conf", script)
            st, _hd, again = self.req("GET", path)
            self.assertEqual(st, 200, again)
            clock.return_value = base + 60
            st, _hd, still = self.req("GET", path)
            self.assertEqual(st, 200, still)
            clock.return_value = base + 61
            st, _hd, dead = self.req("GET", path)
            self.assertEqual(st, 403)
            self.assertEqual(dead, "加入命令已失效\n")
            bad = path.replace("os=linux", "os=mac")
            clock.return_value = base
            st, _hd, swapped = self.req("GET", bad)
            self.assertEqual(st, 403)
            self.assertEqual(swapped, dead)

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    def test_disabled_bad_host_and_secret_path(self, _gen, _pub, _set):
        self.req("POST", "/api/peers", {"name": "phone", "mode": "full"})
        st, _hd, body = self.req("POST", "/api/peers/phone/join?os=mac")
        self.assertEqual(st, 200, body)
        data = json.loads(body)
        self.assertTrue(data["full"])
        path = self._url_path(data["command"])
        _st, _hd, script = self.req("GET", path)
        self.assertIn("brew", script)
        self.assertIn("全隧道", script)
        self.assertNotIn("apt-get", script)
        st, _hd, body = self.req("POST", "/api/peers/phone/disable")
        self.assertEqual(st, 200, body)
        st, _hd, body = self.req("POST", "/api/peers/phone/join?os=linux")
        self.assertEqual(st, 400, body)
        self.assertIn("已停用", json.loads(body)["error"])
        st, _hd, body = self.req("GET", path)
        self.assertEqual(st, 403)
        self.assertEqual(body, "加入命令已失效\n")
        st, _hd, body = self.req("POST", "/api/peers/phone/join?os=linux",
                                 host="evil$(id).example")
        self.assertEqual(st, 500, body)
        self.assertIn("无法确定面板地址", body)
        self.assertNotIn("evil", body)

        self.httpd.shutdown()
        self.httpd.server_close()
        self.cfg["panel_path"] = "wgaio-secret"
        self.httpd = core.start_server(self.cfg)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        st, _hd, body = self.req("GET", "/")
        self.assertEqual(st, 404)
        st, _hd, body = self.req("POST", "/api/peers/phone/enable")
        self.assertEqual(st, 404, body)
        st, hd, body = self.req("POST", "/wgaio-secret/api/login", {"token": "topsecret"})
        self.assertEqual(st, 200, body)
        self.cookie = hd.get("Set-Cookie", "").split(";")[0]
        st, _hd, body = self.req("POST", "/wgaio-secret/api/peers/phone/enable")
        self.assertEqual(st, 200, body)
        st, _hd, body = self.req("POST", "/wgaio-secret/api/peers/phone/join?os=linux")
        self.assertEqual(st, 200, body)
        path = self._url_path(json.loads(body)["command"])
        self.assertTrue(path.startswith("/wgaio-join/"))
        st, _hd, script = self.req("GET", path)
        self.assertEqual(st, 200, script)
        self.assertIn("PrivateKey = PRIV", script)
        st, _hd, body = self.req("GET", "/wgaio-join/phone")
        self.assertEqual(st, 403)
        self.assertNotIn("PrivateKey", body)

    def test_join_post_requires_login(self):
        self.cookie = ""
        st, _hd, body = self.req("POST", "/api/peers/phone/join?os=linux", cookie="")
        self.assertEqual(st, 401, body)


if __name__ == "__main__":
    unittest.main()
