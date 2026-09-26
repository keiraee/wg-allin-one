import io
import os
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS, core.CONFIG_PATH)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.CONFIG_PATH = root / "config.json"
        core.CONFIG_PATH.write_text('{"endpoint":"203.0.113.1:51820"}\n', encoding="utf-8")
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")
        core.CLIENTS.mkdir()
        (core.CLIENTS / "phone.conf").write_text("SECRET\n", encoding="utf-8")
        (core.CLIENTS / "phone.json").write_text('{"name":"phone","ip":"10.66.66.2"}\n',
                                                 encoding="utf-8")
        (core.CLIENTS / "skip.txt").write_text("nope\n", encoding="utf-8")
        link = core.CLIENTS / "link.conf"
        try:
            link.symlink_to(core.CONFIG_PATH)
            self.link_ok = True
        except OSError:
            self.link_ok = False

    def tearDown(self):
        core.WG_CONF, core.CLIENTS, core.CONFIG_PATH = self._orig
        self.tmp.cleanup()

    def test_roundtrip_and_retention(self):
        old = core.backup_root()
        old.mkdir(parents=True)
        stale = old / "wgaio-data-20000101-000000.tar.gz"
        stale.write_bytes(b"old")
        os.utime(stale, (time.time() - 40 * 86400, time.time() - 40 * 86400))
        path = core.create_backup(keep_days=14)
        self.assertFalse(stale.exists())
        self.assertTrue(str(path).endswith(".tar.gz"))
        with tarfile.open(path, "r:gz") as tar:
            names = set(tar.getnames())
        self.assertIn("config.json", names)
        self.assertIn("wg0.conf", names)
        self.assertIn("clients/phone.conf", names)
        self.assertIn("clients/phone.json", names)
        self.assertNotIn("clients/skip.txt", names)
        if self.link_ok:
            self.assertNotIn("clients/link.conf", names)
        core.CONFIG_PATH.write_text("{}\n", encoding="utf-8")
        (core.CLIENTS / "phone.conf").write_text("CHANGED\n", encoding="utf-8")
        (core.CLIENTS / "extra.json").write_text("{}\n", encoding="utf-8")
        core.restore_backup(path, restart=False)
        self.assertIn("203.0.113.1", core.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual((core.CLIENTS / "phone.conf").read_text(encoding="utf-8"), "SECRET\n")
        self.assertFalse((core.CLIENTS / "extra.json").exists())
        self.assertTrue((core.CLIENTS / "skip.txt").exists())

    def test_rejects_parent_path(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            payload = b"x"
            info = tarfile.TarInfo("../evil")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        path = Path(self.tmp.name) / "bad.tar.gz"
        path.write_bytes(buf.getvalue())
        with self.assertRaises(core.ApiError) as cm:
            core.restore_backup(path, restart=False)
        self.assertIn("非法路径", str(cm.exception))
        self.assertFalse((Path(self.tmp.name).parent / "evil").exists())


if __name__ == "__main__":
    unittest.main()
