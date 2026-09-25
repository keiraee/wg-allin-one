import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class TokenTests(unittest.TestCase):
    def test_hash_stable_and_not_plaintext(self):
        h = core.hash_token("secret-token")
        self.assertEqual(h, core.hash_token("secret-token"))
        self.assertNotEqual(h, "secret-token")
        self.assertEqual(len(h), 64)  # sha256 hex

    def test_verify(self):
        h = core.hash_token("secret-token")
        self.assertTrue(core.verify_token("secret-token", h))
        self.assertFalse(core.verify_token("wrong", h))
        self.assertFalse(core.verify_token("secret-token", ""))
        self.assertFalse(core.verify_token("", h))
        self.assertFalse(core.verify_token("secret-token", None))

    def test_verify_tolerates_bad_hash_format(self):
        self.assertFalse(core.verify_token("x", "not-hex-but-string"))


if __name__ == "__main__":
    unittest.main()
