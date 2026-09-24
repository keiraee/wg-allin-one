import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


def write_cfg(dirpath, data):
    p = Path(dirpath) / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_merges_defaults(self):
        p = write_cfg(self.tmp.name, {"endpoint": "1.2.3.4:51820"})
        cfg = core.load_config(p)
        self.assertEqual(cfg["vpn_cidr"], "10.66.66.0/24")
        self.assertEqual(cfg["default_mode"], "split")
        self.assertEqual(cfg["lan_cidrs"], [])

    def test_reject_bad_vpn_cidr(self):
        p = write_cfg(self.tmp.name, {"endpoint": "1.2.3.4:51820", "vpn_cidr": "nope"})
        with self.assertRaises(core.ApiError):
            core.load_config(p)

    def test_reject_missing_endpoint(self):
        p = write_cfg(self.tmp.name, {"endpoint": ""})
        with self.assertRaises(core.ApiError):
            core.load_config(p)

    def test_reject_bad_mode(self):
        p = write_cfg(self.tmp.name, {"endpoint": "1.2.3.4:51820", "default_mode": "yolo"})
        with self.assertRaises(core.ApiError):
            core.load_config(p)

    def test_reject_bad_lan_cidr(self):
        p = write_cfg(self.tmp.name, {"endpoint": "1.2.3.4:51820", "lan_cidrs": ["x"]})
        with self.assertRaises(core.ApiError):
            core.load_config(p)


if __name__ == "__main__":
    unittest.main()