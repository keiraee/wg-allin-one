import subprocess
import sys
import tempfile
import threading
import time
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
                meta, _ = core.add_peer("dev%d" % i, None, None, None,
                                        None, None, CFG)
                results.append(meta["ip"])
            except Exception as e:  # noqa: BLE001 - 测试收集并发错误
                errors.append(e)

        import itertools
        counter = itertools.count(1)
        lock = threading.Lock()

        def fake_keypair():
            with lock:
                i = next(counter)
            return ("PRIV%d" % i, "PUB%d" % i)

        orig_gen = core.gen_keypair
        core.gen_keypair = fake_keypair
        try:
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            core.gen_keypair = orig_gen

        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 8, "并发添加分配到了重复 IP: %s" % results)

    def test_other_process_waits_on_file_lock(self):
        lib = str(Path(__file__).resolve().parents[1] / "lib")
        flag = Path(self.tmp.name) / "held"
        script = (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "os.environ['WGAIO_WG_CONF'] = sys.argv[1]\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "import core\n"
            "with core.wg_lock():\n"
            "    Path(sys.argv[3]).write_text('held', encoding='utf-8')\n"
            "    time.sleep(1.2)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(core.WG_CONF), lib, str(flag)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(50):
                if flag.exists():
                    break
                time.sleep(0.05)
            self.assertTrue(flag.exists(), "子进程没有拿到锁")
            started = time.time()
            with core.wg_lock():
                waited = time.time() - started
        finally:
            _out, err = proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 0, err)
        self.assertGreater(waited, 0.4, "另一进程没有被文件锁挡住")


if __name__ == "__main__":
    unittest.main()
