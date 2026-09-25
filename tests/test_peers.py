import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


CFG = {
    "vpn_cidr": "10.66.66.0/24",
    "endpoint": "203.0.113.1:51820",
    "client_dns": "1.1.1.1",
    "lan_cidrs": ["192.168.1.0/24"],
    "default_mode": "split",
}


class PeerBase(unittest.TestCase):
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
class AddPeerTests(PeerBase):
    def test_add_generates_everything(self, m_gen, m_pub, m_set):
        meta, conf = core.add_peer("phone", ip=None, dns=None, keepalive=None,
                                   mode=None, routes=None, cfg=CFG)
        self.assertEqual(meta["ip"], "10.66.66.2")
        self.assertEqual(meta["mode"], "split")
        self.assertIn("PrivateKey = PRIV", conf)
        _, peers = core.parse_conf()
        self.assertEqual(peers[0]["allowed_ips"], ["10.66.66.2/32"])
        m_set.assert_called_once()
        self.assertTrue(core.client_paths("phone")[0].exists())

    def test_add_gateway_routes(self, m_gen, m_pub, m_set):
        meta, _ = core.add_peer("router", ip=None, dns=None, keepalive=None,
                                mode=None, routes="192.168.1.0/24", cfg=CFG)
        _, peers = core.parse_conf()
        self.assertEqual(peers[0]["allowed_ips"], ["10.66.66.2/32", "192.168.1.0/24"])

    def test_gateway_route_updates_other_clients(self, m_gen, m_pub, m_set):
        core.add_peer("phone", None, None, None, "split", None, CFG)
        core.add_peer("router", None, None, None, "split", "10.1.0.0/16", CFG)
        phone = core.show_conf("phone")
        router = core.show_conf("router")
        self.assertIn("10.1.0.0/16", phone)
        self.assertNotIn("10.1.0.0/16", router)
        core.remove_peer("router", force=True, cfg=CFG)
        self.assertNotIn("10.1.0.0/16", core.show_conf("phone"))

    def test_add_rejects_dup_name(self, m_gen, m_pub, m_set):
        core.add_peer("phone", None, None, None, None, None, CFG)
        with self.assertRaises(core.ApiError):
            core.add_peer("phone", None, None, None, None, None, CFG)

    def test_add_rejects_bad_dns(self, m_gen, m_pub, m_set):
        with self.assertRaises(core.ApiError):
            core.add_peer("phone", None, "not-a-dns", None, None, None, CFG)
        self.assertFalse(core.client_paths("phone")[0].exists())

    def test_add_drops_client_if_conf_write_fails(self, m_gen, m_pub, m_set):
        with mock.patch("core.write_conf", side_effect=core.ApiError("写入失败")):
            with self.assertRaises(core.ApiError):
                core.add_peer("phone", None, None, None, None, None, CFG)
        self.assertFalse(core.client_paths("phone")[0].exists())
        m_set.assert_not_called()


@mock.patch("core.wg_set_peer")
@mock.patch("core.server_pubkey", return_value="SPUB")
@mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
class DelEditTests(PeerBase):
    def test_del_gateway_needs_force(self, m_gen, m_pub, m_set):
        core.add_peer("router", None, None, None, None, "192.168.1.0/24", CFG)
        with self.assertRaises(core.ApiError) as cm:
            core.remove_peer("router", force=False)
        self.assertEqual(cm.exception.code, 409)
        core.remove_peer("router", force=True)
        self.assertEqual(core.parse_conf()[1], [])

    def test_del_normal(self, m_gen, m_pub, m_set):
        core.add_peer("phone", None, None, None, None, None, CFG)
        core.remove_peer("phone", force=False)
        self.assertEqual(core.parse_conf()[1], [])
        self.assertIsNone(core.load_client_meta("phone"))

    def test_edit_name_ip(self, m_gen, m_pub, m_set):
        meta, _ = core.add_peer("phone", None, None, None, None, None, CFG)
        core.update_peer("phone", new_name="phone2", ip="10.66.66.9",
                         dns=None, keepalive=None, mode=None, routes=None, cfg=CFG)
        _, peers = core.parse_conf()
        self.assertEqual(peers[0]["name"], "phone2")
        self.assertEqual(peers[0]["allowed_ips"], ["10.66.66.9/32"])
        self.assertIsNone(core.load_client_meta("phone"))
        self.assertEqual(core.load_client_meta("phone2")["ip"], "10.66.66.9")
        self.assertEqual(core.read_priv("phone2"), "PRIV")

    def test_edit_keeps_gateway_routes(self, m_gen, m_pub, m_set):
        core.add_peer("router", None, None, None, None, "192.168.1.0/24", CFG)
        core.update_peer("router", new_name=None, ip="10.66.66.5",
                         dns=None, keepalive=None, mode=None, routes=None, cfg=CFG)
        _, peers = core.parse_conf()
        self.assertEqual(peers[0]["allowed_ips"], ["10.66.66.5/32", "192.168.1.0/24"])

    def test_rename_keeps_key_when_write_fails(self, m_gen, m_pub, m_set):
        core.add_peer("phone", None, None, None, None, None, CFG)
        with mock.patch("core.write_conf", side_effect=core.ApiError("写入失败", 500)):
            with self.assertRaises(core.ApiError):
                core.update_peer("phone", new_name="phone2", cfg=CFG)
        self.assertEqual(core.read_priv("phone"), "PRIV")
        self.assertFalse(core.client_paths("phone2")[0].exists())

    def test_rename_manual_peer_keeps_meta(self, m_gen, m_pub, m_set):
        iface, peers = core.parse_conf()
        peers.append({"name": "manual", "pubkey": "MPUB",
                      "allowed_ips": ["10.66.66.7/32"], "keepalive": 0, "extra": []})
        core.write_conf(iface, peers)
        core.update_peer("manual", new_name="manual2", cfg=CFG)
        self.assertIsNone(core.load_client_meta("manual"))
        self.assertEqual(core.load_client_meta("manual2")["name"], "manual2")

    def test_edit_clear_routes(self, m_gen, m_pub, m_set):
        core.add_peer("router", None, None, None, None, "192.168.1.0/24", CFG)
        core.update_peer("router", routes="", cfg=CFG)
        _, peers = core.parse_conf()
        self.assertEqual(peers[0]["allowed_ips"], ["10.66.66.2/32"])


if __name__ == "__main__":
    unittest.main()
