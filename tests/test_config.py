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
        self.assertIsInstance(cfg["lan_cidrs"], list)

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

    def test_missing_file_is_500(self):
        p = Path(self.tmp.name) / "nope.json"
        with self.assertRaises(core.ApiError) as cm:
            core.load_config(p)
        self.assertEqual(cm.exception.code, 500)

    def test_bad_json_is_500(self):
        p = Path(self.tmp.name) / "config.json"
        p.write_text("{not json", encoding="utf-8")
        with self.assertRaises(core.ApiError) as cm:
            core.load_config(p)
        self.assertEqual(cm.exception.code, 500)

    def test_reject_bool_port(self):
        p = write_cfg(self.tmp.name, {"endpoint": "1.2.3.4:51820", "wg_port": True})
        with self.assertRaises(core.ApiError):
            core.load_config(p)

    def test_null_lan_cidrs_normalizes(self):
        p = write_cfg(self.tmp.name, {"endpoint": "1.2.3.4:51820", "lan_cidrs": None})
        cfg = core.load_config(p)
        self.assertEqual(cfg["lan_cidrs"], [])

    def test_reject_bad_vpn_prefix(self):
        p = write_cfg(self.tmp.name, {"endpoint": "1.2.3.4:51820", "vpn_cidr": "10.0.0.0/33"})
        with self.assertRaises(core.ApiError):
            core.load_config(p)

    def test_reject_bool_panel_port(self):
        p = write_cfg(self.tmp.name, {"endpoint": "1.2.3.4:51820", "panel_port": True})
        with self.assertRaises(core.ApiError):
            core.load_config(p)


if __name__ == "__main__":
    unittest.main()
