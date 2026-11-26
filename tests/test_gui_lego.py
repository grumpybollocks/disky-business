# SPDX-License-Identifier: GPL-3.0-or-later
"""The lego rule as a test: a new theme, block style and tab plug in without any other file being edited."""
import dataclasses
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout
    from diskygui import blockstyles, mapmodel as mm, theme as themes
    from diskygui.tabbase import TabBase
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Lego(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def plug(self, table, key, value):
        table[key] = value
        self.addCleanup(table.pop, key, None)

    def test_a_new_theme_block_style_and_tab_plug_in(self):
        def dots(p, s, c):
            p.fillRect(2, 2, s - 4, s - 4, c)
        self.plug(blockstyles.STYLES, "dots", blockstyles.BlockStyle("dots", "Dots", 9, 2, dots))
        mint = dataclasses.replace(themes.W98, name="mint", label="Mint", face="#cfe8d8", map_bg="#103020",
                                   block_style="dots", states=dict(themes.W98.states, good="#40ff90"))
        self.plug(themes.THEMES, "mint", mint)

        class NotesTab(TabBase):
            title = "Notes"
            NOTE = "A tab made inside a test."

            def __init__(self, theme, runner=None, tip=None, drives=None, parent=None):
                super().__init__(theme, runner, tip, drives, parent)
                right = QVBoxLayout()
                self.label = QLabel("nothing picked")
                right.addWidget(self.label)
                self.assemble(right)

            def on_folder(self, folder, hint):
                self.label.setText(folder or "nothing picked")
                self.update_buttons()

        old = MainWindow.TABS
        MainWindow.TABS = old + (NotesTab,)
        self.addCleanup(setattr, MainWindow, "TABS", old)

        win = MainWindow(theme=themes.THEMES["w98"], drives=lambda: [])
        self.addCleanup(win.close)
        win.resize(900, 600)
        win.show()
        # the tab is there, gets the drive panel, and follows the folder picked on another tab
        titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        self.assertEqual(titles[-1], "Notes")
        self.assertEqual(len(titles), len(old) + 1)
        win.pages[0].set_folder("/picked")
        win.tabs.setCurrentIndex(len(titles) - 1)
        self.assertEqual(win.pages[-1].label.text(), "/picked")
        # the theme is reached by the button, and brings its block style and colours to the Test map
        names = []
        for _ in themes.THEMES:
            win.theme_button.click()
            names.append(win.theme.name)
        self.assertEqual(sorted(names), sorted(themes.THEMES))
        win.set_theme(mint)
        self.assertEqual(win.theme_button.text(), "Look: Mint")
        test_map = win.pages[0].map
        self.assertEqual((test_map.style.name, test_map.style.cell), ("dots", 11))
        win.pages[0].model.set_blocks(0, 50, mm.GOOD)
        win.pages[0].model.cursor = None
        win.tabs.setCurrentIndex(0)
        self.app.processEvents()
        test_map.refresh()
        x, y = test_map._xy(0)
        img = test_map.grab().toImage()
        self.assertEqual(img.pixelColor(x + 4, y + 4).name(), "#40ff90")           # the dot, in the new colour
        self.assertEqual(img.pixelColor(x, y).name(), QColor("#103020").name())    # around it, the new background


if __name__ == "__main__":
    unittest.main()
