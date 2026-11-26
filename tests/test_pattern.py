# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import numpy as np

from disky import pattern

MASK = (1 << 64) - 1


def reference(offset, nbytes):
    """f3's rule written as the plain loop from its source."""
    out = bytearray()
    for off in range(offset, offset + nbytes, 512):
        out += off.to_bytes(8, "little")
        x = off
        for _ in range(63):
            x = (x * 4294967311 + 17) & MASK
            out += x.to_bytes(8, "little")
    return bytes(out)


class Pattern(unittest.TestCase):
    def test_make_matches_the_plain_loop(self):
        for offset in (0, 512, (1 << 30) - 1024, 5 << 30, (1 << 44) + 512):
            self.assertEqual(pattern.make(offset, 4096), reference(offset, 4096), offset)

    def test_make_refuses_odd_sizes(self):
        with self.assertRaises(ValueError):
            pattern.make(1, 512)
        with self.assertRaises(ValueError):
            pattern.make(0, 100)

    def test_clean_data_is_good(self):
        self.assertFalse(pattern.classify(pattern.make(1 << 30, 1 << 20), 1 << 30).any())

    def states(self, change, offset=1 << 20):
        buf = bytearray(pattern.make(offset, 4096))
        change(buf)
        return list(pattern.classify(bytes(buf), offset))

    def test_one_bit_is_changed(self):
        def flip(b): b[512 + 100] ^= 0x01
        self.assertEqual(self.states(flip), [0, pattern.CHANGED, 0, 0, 0, 0, 0, 0])

    def test_seven_bits_changed_eight_bits_bad(self):
        def seven(b): b[100] ^= 0x7F
        def eight(b): b[100] ^= 0xFF
        self.assertEqual(self.states(seven)[0], pattern.CHANGED)
        self.assertEqual(self.states(eight)[0], pattern.BAD)

    def test_scrambled_sector_is_bad(self):
        def scramble(b): b[1024:1536] = bytes(range(256)) * 2
        self.assertEqual(self.states(scramble)[2], pattern.BAD)

    def test_data_from_another_place_is_overwritten(self):
        def wrap(b): b[0:512] = pattern.make(7 << 30, 512)
        self.assertEqual(self.states(wrap), [pattern.OVERWRITTEN, 0, 0, 0, 0, 0, 0, 0])

    def test_swapped_sectors_are_overwritten(self):
        def swap(b): b[0:512], b[512:1024] = b[512:1024], b[0:512]
        self.assertEqual(self.states(swap)[:3], [pattern.OVERWRITTEN, pattern.OVERWRITTEN, 0])

    def test_damaged_offset_field_is_bad(self):
        def hit(b): b[0] ^= 0x01
        self.assertEqual(self.states(hit)[0], pattern.BAD)


if __name__ == "__main__":
    unittest.main()
