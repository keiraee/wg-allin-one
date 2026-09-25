import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


CFG = {"vpn_cidr": "10.66.66.0/24"}


class NameTests(unittest.TestCase):
    def test_accept(self):
        for n in ("phone", "pc-windows", "a1_b2", "X" * 15):
            self.assertEqual(core.normalize_name(n), n)

    def test_reject_bad_chars_or_length(self):
        for n in ("", " ", "中文名", "a b", "x" * 16, "a/b", "-x"):
            with self.assertRaises(core.ApiError):
                core.normalize_name(n)


class IpTests(unittest.TestCase):
    def test_accept_free_ip(self):
        self.assertEqual(core.normalize_ip("10.66.66.5", CFG, set()), "10.66.66.5")

    def test_reject_outside_cidr(self):
        with self.assertRaises(core.ApiError):
            core.normalize_ip("192.168.1.5", CFG, set())

    def test_reject_server_ip(self):
        with self.assertRaises(core.ApiError):
            core.normalize_ip("10.66.66.1", CFG, set())

    def test_reject_used(self):
        with self.assertRaises(core.ApiError):
            core.normalize_ip("10.66.66.5", CFG, {"10.66.66.5"})

    def test_allow_reuse_own_current(self):
        got = core.normalize_ip("10.66.66.5", CFG, {"10.66.66.5"}, current="10.66.66.5")
        self.assertEqual(got, "10.66.66.5")

    def test_reject_garbage(self):
        for ip in ("x", "1.2.3", "10.66.66.256"):
            with self.assertRaises(core.ApiError):
                core.normalize_ip(ip, CFG, set())

    def test_missing_vpn_cidr_is_500(self):
        with self.assertRaises(core.ApiError) as cm:
            core.normalize_ip("10.66.66.5", {}, set())
        self.assertEqual(cm.exception.code, 500)


class RouteTests(unittest.TestCase):
    def test_parse_comma_string(self):
        self.assertEqual(core.normalize_routes("192.168.1.0/24, 10.0.0.0/8"),
                         ["192.168.1.0/24", "10.0.0.0/8"])

    def test_none_means_empty(self):
        self.assertEqual(core.normalize_routes(None), [])

    def test_reject_bad(self):
        for r in ("192.168.1.1", "abc/24", "1.2.3.4/33", "0.0.0.0/0"):
            with self.assertRaises(core.ApiError):
                core.normalize_routes(r)


if __name__ == "__main__":
    unittest.main()
