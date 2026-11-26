# SPDX-License-Identifier: GPL-3.0-or-later
"""The crisp old look only when a font made for it is installed; otherwise smooth, slightly larger letters."""
import unittest

from diskygui.chrome import text_look
from diskygui.theme import THEMES


class TextLook(unittest.TestCase):
    def test_w98(self):
        t = THEMES["w98"]
        self.assertEqual(text_look(t, installed=lambda names: True), (11, False))
        self.assertEqual(text_look(t, installed=lambda names: False), (12, True))

    def test_smooth_themes_are_left_alone(self):
        for t in THEMES.values():
            if t.smooth:
                self.assertEqual(text_look(t, installed=lambda names: False), (t.font_px, True))


if __name__ == "__main__":
    unittest.main()
