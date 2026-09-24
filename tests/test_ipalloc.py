import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


CFG = {
    "vpn_cidr": "10.66.66.0/24",
    "endpoint": "203.0.113.1:51820",
    "client_dns": "1.1.1.1",
    "lan_cidrs": ["192.168.1.0/24"],
}


class AllocTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = core.CLIENTS
        core.CLIENTS = Path(self.tmp.name)

    def tearDown(self):
        core.CLIENTS = self._orig
        self.tmp.cleanup()

    def test_next_ip_skips_used(self):
        peers = [{"allowed_ips": ["10.66.66.2/32"]},
                 {"allowed_ips": ["10.66.66.3/32", "192.168.1.0/24"]}]
        self.assertEqual(core.next_ip(CFG, peers), "10.66.66.4")

    def test_next_ip_skips_client_meta(self):
        (core.CLIENTS / "x.json").write_text('{"ip": "10.66.66.2"}', encoding="utf-8")
        self.assertEqual(core.next_ip(CFG, []), "10.66.66.3")

    def test_next_ip_exhausted(self):
        peers = [{"allowed_ips": ["10.66.66.%d/32" % i]} for i in range(2, 255)]
        with self.assertRaises(core.ApiError):
            core.next_ip(CFG, peers)


class ClientConfTests(unittest.TestCase):
    def test_split_mode(self):
        text = core.build_client_conf("PRIV", "10.66.66.9", CFG, mode="split",
                                      server_pub="SPUB", keepalive=25)
        self.assertIn("PrivateKey = PRIV", text)
        self.assertIn("Address = 10.66.66.9/32", text)
        self.assertIn("DNS = 1.1.1.1", text)
        self.assertIn("PublicKey = SPUB", text)
        self.assertIn("AllowedIPs = 10.66.66.0/24, 192.168.1.0/24", text)
        self.assertIn("Endpoint = 203.0.113.1:51820", text)
        self.assertIn("PersistentKeepalive = 25", text)

    def test_full_mode(self):
        text = core.build_client_conf("PRIV", "10.66.66.9", CFG, mode="full",
                                      server_pub="SPUB", keepalive=25)
        self.assertIn("AllowedIPs = 0.0.0.0/0, ::/0", text)


if __name__ == "__main__":
    unittest.main()