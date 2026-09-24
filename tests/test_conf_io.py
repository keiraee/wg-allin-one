import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


SAMPLE = """[Interface]
Address = 10.66.66.1/24
ListenPort = 51820
PrivateKey = SERVER_KEY

[Peer]
# name: home-router
PublicKey = PEER_A
AllowedIPs = 10.66.66.2/32, 192.168.1.0/24
PersistentKeepalive = 25

[Peer]
# name: phone
PublicKey = PEER_B
AllowedIPs = 10.66.66.3/32
PresharedKey = EXTRA_KEY
"""


class ConfIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conf = Path(self.tmp.name) / "wg0.conf"
        self._orig = core.WG_CONF
        core.WG_CONF = self.conf

    def tearDown(self):
        core.WG_CONF = self._orig
        self.tmp.cleanup()

    def test_parse_names_ips_extra(self):
        self.conf.write_text(SAMPLE, encoding="utf-8")
        iface, peers = core.parse_conf()
        self.assertTrue(any("PrivateKey = SERVER_KEY" in l for l in iface))
        self.assertEqual(len(peers), 2)
        self.assertEqual(peers[0]["name"], "home-router")
        self.assertEqual(peers[0]["allowed_ips"], ["10.66.66.2/32", "192.168.1.0/24"])
        self.assertEqual(peers[0]["keepalive"], 25)
        self.assertEqual(peers[1]["extra"], ["PresharedKey = EXTRA_KEY"])

    def test_write_roundtrip(self):
        self.conf.write_text(SAMPLE, encoding="utf-8")
        iface, peers = core.parse_conf()
        core.write_conf(iface, peers)
        iface2, peers2 = core.parse_conf()
        self.assertEqual(iface, iface2)
        self.assertEqual(peers, peers2)

    def test_write_atomic_no_tmp_left(self):
        self.conf.write_text(SAMPLE, encoding="utf-8")
        iface, peers = core.parse_conf()
        core.write_conf(iface, peers)
        leftovers = [p for p in self.conf.parent.iterdir() if p.name != "wg0.conf"]
        self.assertEqual(leftovers, [])

    def test_parse_missing_file_is_empty(self):
        iface, peers = core.parse_conf()
        self.assertEqual((iface, peers), ([], []))

    def test_parse_iface_only_no_peers(self):
        self.conf.write_text("[Interface]\nAddress = 10.66.66.1/24\n", encoding="utf-8")
        iface, peers = core.parse_conf()
        self.assertEqual(peers, [])
        self.assertTrue(any("Address" in l for l in iface))
        core.write_conf(iface, peers)
        iface2, peers2 = core.parse_conf()
        self.assertEqual(peers2, [])
        self.assertTrue(any("Address" in l for l in iface2))

    def test_peer_without_name_comment(self):
        self.conf.write_text(SAMPLE, encoding="utf-8")
        iface, peers = core.parse_conf()
        peers[0]["name"] = ""
        core.write_conf(iface, peers)
        iface2, peers2 = core.parse_conf()
        self.assertEqual(peers2[0]["name"], "PEER_A")  # fallback: pubkey[:8]
        self.assertEqual(peers2[0]["pubkey"], "PEER_A")

    def test_negative_keepalive_clamped(self):
        self.conf.write_text(SAMPLE.replace("PersistentKeepalive = 25",
                                            "PersistentKeepalive = -5"), encoding="utf-8")
        _, peers = core.parse_conf()
        self.assertEqual(peers[0]["keepalive"], 0)

    def test_write_failure_cleans_tmp(self):
        self.conf.write_text(SAMPLE, encoding="utf-8")
        iface, peers = core.parse_conf()
        bad = Path(self.tmp.name) / "missing-dir" / "wg0.conf"
        self._orig2 = core.WG_CONF
        core.WG_CONF = bad  # parent dir missing -> write_text raises OSError
        try:
            with self.assertRaises(core.ApiError):
                core.write_conf(iface, peers)
        finally:
            core.WG_CONF = self._orig2
        self.assertEqual(list(self.conf.parent.iterdir()), [self.conf])  # no orphan .tmp


if __name__ == "__main__":
    unittest.main()
