import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class KeyGenTests(unittest.TestCase):
    @mock.patch("core.run_wg")
    def test_gen_keypair_calls_wg(self, m):
        m.side_effect = ["PRIVKEY\n", "PUBKEY\n"]
        priv, pub = core.gen_keypair()
        self.assertEqual((priv, pub), ("PRIVKEY", "PUBKEY"))
        self.assertEqual(m.call_args_list[0][0][0], ["genkey"])


class ClientStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = core.CLIENTS
        core.CLIENTS = Path(self.tmp.name) / "clients"

    def tearDown(self):
        core.CLIENTS = self._orig
        self.tmp.cleanup()

    def test_save_and_load_meta(self):
        core.save_client("phone", "CONFTEXT", {"name": "phone", "ip": "10.66.66.3"})
        self.assertEqual(core.load_client_meta("phone")["ip"], "10.66.66.3")
        self.assertEqual(core.read_priv("phone"), "")  # conf 里没有 PrivateKey 行
        conf_p, _ = core.client_paths("phone")
        self.assertEqual(conf_p.read_text(encoding="utf-8"), "CONFTEXT")

    def test_drop_client(self):
        core.save_client("phone", "X", {"name": "phone"})
        core.drop_client("phone")
        self.assertIsNone(core.load_client_meta("phone"))

    def test_read_priv_from_conf(self):
        core.save_client("phone", "[Interface]\nPrivateKey = SEC\n", {"name": "phone"})
        self.assertEqual(core.read_priv("phone"), "SEC")


if __name__ == "__main__":
    unittest.main()
