import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


CFG = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
       "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split",
       "wg_port": 51820}


class ListShowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")

    def tearDown(self):
        core.WG_CONF, core.CLIENTS = self._orig
        self.tmp.cleanup()

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    @mock.patch("core.live_status")
    def test_list_merges_live(self, m_live, m_gen, m_pub, m_set):
        now = int(time.time())
        m_live.return_value = ({"PUB": {"handshake": now, "rx": 100, "tx": 200}}, 51820)
        core.add_peer("phone", None, None, None, None, None, CFG)
        rows = core.list_peers()
        self.assertEqual(rows[0]["name"], "phone")
        self.assertEqual(rows[0]["state"], "ok")
        self.assertEqual(rows[0]["rx"], 100)
        self.assertTrue(rows[0]["has_client"])
        self.assertEqual(rows[0]["mode"], "split")

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    @mock.patch("core.live_status", return_value=({}, 51820))
    def test_list_offline(self, m_live, m_gen, m_pub, m_set):
        core.add_peer("phone", None, None, None, None, None, CFG)
        self.assertEqual(core.list_peers()[0]["state"], "off")

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    @mock.patch("core.live_status", return_value=({}, 51820))
    def test_list_tolerates_bad_name(self, m_live, m_gen, m_pub, m_set):
        iface, peers = core.parse_conf()
        peers.append({"name": "a b", "pubkey": "BPUB",
                      "allowed_ips": ["10.66.66.8/32"], "keepalive": 0, "extra": []})
        core.write_conf(iface, peers)
        rows = core.list_peers()
        bad = [r for r in rows if r["pubkey"] == "BPUB"][0]
        self.assertFalse(bad["has_client"])
        self.assertEqual(bad["name"], "a b")

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    def test_show_conf(self, m_gen, m_pub, m_set):
        core.add_peer("phone", None, None, None, None, None, CFG)
        self.assertIn("PrivateKey = PRIV", core.show_conf("phone"))
        with self.assertRaises(core.ApiError):
            core.show_conf("ghost")

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    @mock.patch("core.live_status", return_value=({}, 51820))
    def test_full_status(self, m_live, m_gen, m_pub, m_set):
        core.add_peer("phone", None, None, None, None, None, CFG)
        st = core.full_status(CFG)
        self.assertTrue(st["ok"])
        self.assertEqual(st["next_ip"], "10.66.66.3")
        self.assertEqual(len(st["peers"]), 1)
        self.assertEqual(st["endpoint"], "203.0.113.1:51820")
        self.assertEqual(st["panel_bind"], "")
        public = core.full_status(dict(CFG, panel_bind="0.0.0.0"))
        self.assertEqual(public["panel_bind"], "0.0.0.0")
        self.assertEqual(st["iface"]["name"], "wg0")

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    @mock.patch("core.live_status")
    def test_list_stale(self, m_live, m_gen, m_pub, m_set):
        stale_hs = int(time.time()) - 300  # 5 分钟前
        m_live.return_value = ({"PUB": {"handshake": stale_hs, "rx": 10, "tx": 20}}, 51820)
        core.add_peer("phone", None, None, None, None, None, CFG)
        rows = core.list_peers()
        self.assertEqual(rows[0]["state"], "stale")

    @mock.patch("core.next_ip", side_effect=core.ApiError("已用尽"))
    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.live_status", return_value=({}, 51820))
    def test_full_status_pool_exhausted(self, m_live, m_pub, m_set, m_next):
        # 写入手工对等端（add_peer 也依赖 next_ip，不能在全局 mock 下使用）
        core.WG_CONF.write_text(
            "[Interface]\nPrivateKey = S\n\n[Peer]\n# name: phone\n"
            "PublicKey = PUB\nAllowedIPs = 10.66.66.2/32\n", encoding="utf-8")
        st = core.full_status(CFG)
        self.assertTrue(st["ok"])
        self.assertIsNone(st["next_ip"])


if __name__ == "__main__":
    unittest.main()
