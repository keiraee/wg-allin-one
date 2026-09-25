import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


CFG = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
       "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split"}


class ConcurrencyTests(unittest.TestCase):
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
    def test_parallel_adds_get_distinct_ips(self, m_pub, m_set):
        results, errors = [], []

        def worker(i):
            try:
                with mock.patch("core.gen_keypair",
                                return_value=("PRIV%d" % i, "PUB%d" % i)):
                    meta, _ = core.add_peer("dev%d" % i, None, None, None,
                                            None, None, CFG)
                results.append(meta["ip"])
            except Exception as e:  # noqa: BLE001 - 测试收集并发错误
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 8, "并发添加分配到了重复 IP: %s" % results)


if __name__ == "__main__":
    unittest.main()
