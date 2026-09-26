import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import qr


def _digest(text):
    modules = qr.encode_modules(text)
    bits = "".join("1" if cell else "0" for row in modules for cell in row)
    return hashlib.sha256(bits.encode("ascii")).hexdigest(), len(modules)


class QrTests(unittest.TestCase):
    def test_known_matrices(self):
        digest, size = _digest("hello")
        self.assertEqual(size, 21)
        self.assertEqual(digest, "99ccedcf0d82e92a63894c8b42e405a3ac50eafa33ee2ce68fa895813fdcb386")
        digest, size = _digest("k" * 140)
        self.assertEqual(size, 49)
        self.assertEqual(digest, "a880963d8516da5d1891de6c25eeb24b6bc3a12bd1a4fc1074747aa2149260f0")

    def test_svg_is_one_path(self):
        svg = qr.qr_svg("hello")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('shape-rendering="crispEdges"', svg)
        self.assertEqual(svg.count("<path"), 1)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            qr.encode_modules("")


if __name__ == "__main__":
    unittest.main()
