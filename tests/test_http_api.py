import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")
        core._sessions.clear()
        self.cfg = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
                    "client_dns": "1.1.1.1", "lan_cidrs": ["192.168.1.0/24"],
                    "default_mode": "split", "wg_port": 51820,
                    "panel_bind": "127.0.0.1", "panel_port": 0,
                    "panel_token_hash": core.hash_token("topsecret")}
        self.httpd = core.start_server(self.cfg)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.cookie = ""
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"})
        self.cookie = hd.get("Set-Cookie", "").split(";")[0]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        core.WG_CONF, core.CLIENTS = self._orig
        self.tmp.cleanup()

    def req(self, method, path, body=None, ctype="application/json"):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data, headers = None, {"Cookie": self.cookie}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = ctype
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8")


@mock.patch("core.wg_set_peer")
@mock.patch("core.server_pubkey", return_value="SPUB")
@mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
class ApiTests(ApiTestBase):
    def test_add_status_conf_del(self, m_gen, m_pub, m_set):
        st, hd, body = self.req("POST", "/api/peers", {"name": "phone"})
        self.assertEqual(st, 200, body)
        data = json.loads(body)
        self.assertIn("PrivateKey = PRIV", data["conf"])

        st, hd, body = self.req("GET", "/api/status")
        names = [p["name"] for p in json.loads(body)["peers"]]
        self.assertIn("phone", names)

        st, hd, body = self.req("GET", "/api/peers/phone/conf")
        self.assertEqual(st, 200)
        self.assertIn("attachment", hd.get("Content-Disposition", ""))
        self.assertIn("PrivateKey = PRIV", body)

        st, hd, body = self.req("DELETE", "/api/peers/phone")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_del_gateway_409_then_force(self, m_gen, m_pub, m_set):
        self.req("POST", "/api/peers",
                 {"name": "router", "routes": "192.168.1.0/24"})
        st, hd, body = self.req("DELETE", "/api/peers/router")
        self.assertEqual(st, 409)
        st, hd, body = self.req("DELETE", "/api/peers/router?force=1")
        self.assertEqual(st, 200)

    def test_patch_rename_and_ip(self, m_gen, m_pub, m_set):
        self.req("POST", "/api/peers", {"name": "phone"})
        st, hd, body = self.req("PATCH", "/api/peers/phone",
                                {"new_name": "phone2", "ip": "10.66.66.9"})
        self.assertEqual(st, 200, body)
        st, hd, body = self.req("GET", "/api/status")
        row = [p for p in json.loads(body)["peers"] if p["name"] == "phone2"][0]
        self.assertEqual(row["ip"], "10.66.66.9")

    def test_conf_404_unknown(self, m_gen, m_pub, m_set):
        st, hd, body = self.req("GET", "/api/peers/ghost/conf")
        self.assertEqual(st, 404)

    def test_bad_json_400(self, m_gen, m_pub, m_set):
        st, hd, body = self.req("POST", "/api/peers", "not-dict")
        self.assertIn(st, (400, 415))


if __name__ == "__main__":
    unittest.main()
