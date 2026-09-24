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


if __name__ == "__main__":
    unittest.main()
