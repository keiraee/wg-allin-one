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
        # "hello" 对应规范最优掩码 7（旧实现按 python-qrcode 的 N3 会选到掩码 0）
        digest, size = _digest("hello")
        self.assertEqual(size, 21)
        self.assertEqual(digest, "1193a98e9acd91c1a850105b67ec324d33ff4bfef4feb63262394e1ebfb46068")
        digest, size = _digest("k" * 140)
        self.assertEqual(size, 49)
        self.assertEqual(digest, "a880963d8516da5d1891de6c25eeb24b6bc3a12bd1a4fc1074747aa2149260f0")

    def test_penalty_matches_reference_scores(self):
        """8 个掩码的惩罚分要和 segno 1.6.6 的 encoder.mask_scores 逐值相同。

        这组数值是外部金标准：旧实现（照抄 python-qrcode 的 N3）在 "hello" 上
        会给出不同的分数并把掩码选成 0，而不是规范最优的 7。
        """
        text = "hello"
        ver = qr._choose_version(text)
        bits = qr._payload_bits(text, ver)
        words = qr._blocks(ver, bits)
        base, func = qr._draw_patterns(ver)
        qr._place(base, func, words)
        size = len(base)
        got = []
        for mask in range(8):
            cand = qr._apply_mask(base, func, mask)
            cand[size - 8][8] = False
            got.append(qr._penalty(cand))
        self.assertEqual(got, [1101, 1171, 1113, 1158, 1164, 1130, 1178, 1084])
        self.assertEqual(min(range(8), key=lambda m: got[m]), 7)

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
