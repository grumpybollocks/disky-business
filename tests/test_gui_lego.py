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



@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ModernLook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_first_start_gets_the_modern_look_light_or_dark(self):
        from diskygui.theme import default_theme
        self.assertEqual((default_theme(False), default_theme(True)), ("modern", "modern-dark"))
        self.assertTrue(themes.THEMES["modern"].flat and themes.THEMES["modern-dark"].flat)
        self.assertFalse(themes.THEMES["w98"].flat or themes.THEMES["dark"].flat or themes.THEMES["violet"].flat)   # the old looks stay

    def test_modern_looks_are_rounded_and_mark_the_main_button(self):
        from diskygui.chrome import stylesheet
        for name in ("modern", "modern-dark"):
            t = themes.THEMES[name]
            css = stylesheet(t)
            self.assertIn("border-radius", css, name)
            self.assertNotIn("border-image", css, name)                # no bevel pictures
            self.assertIn(f"QPushButton#primary {{ background: {t.title}", css, name)
            from PySide6.QtGui import QColor

            def contrast(one, two):                                     # the usual contrast ratio, 1 to 21
                def light(c):
                    parts = [v / 255 for v in QColor(c).getRgb()[:3]]
                    parts = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in parts]
                    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
                hi, lo = sorted((light(one), light(two)), reverse=True)
                return (hi + 0.05) / (lo + 0.05)
            self.assertGreater(contrast(t.text, t.face), 7, name)       # normal text reads easily
            self.assertGreater(contrast(t.muted, t.face), 4.5, name)    # quieter text is still readable
            self.assertGreater(contrast(t.title_text, t.title), 4.5, name)      # the main button's words
            self.assertGreater(contrast(t.faint, t.face), 1.8, name)    # switched-off things stay visible
        self.assertIn("border-image", stylesheet(themes.THEMES["w98"]))       # the old look is drawn as before

    def test_every_button_has_its_icon_in_the_modern_looks_and_none_in_the_old(self):
        from PySide6.QtWidgets import QPushButton
        from diskygui import icons
        from diskygui.window import MainWindow
        self.assertEqual(sorted(set(icons.BUTTON_ICONS.values()) - set(icons.SHAPES)), [])     # every icon exists
        for name in icons.SHAPES:
            img = icons.icon(name, "#000000").pixmap(18, 18).toImage()
            dark = sum(img.pixelColor(x, y).alpha() > 0 for x in range(img.width()) for y in range(img.height()))
            self.assertGreater(dark, 10, name)                         # it really draws something
        self.assertTrue(icons.icon("no such icon", "#000000").isNull())
        win = MainWindow(theme=themes.THEMES["modern"], drives=lambda: [])
        self.addCleanup(win.close)
        plain = ("Look: ", "Admin session")
        for b in win.findChildren(QPushButton):
            if b.text().startswith(plain):
                continue
            self.assertIn(b.text(), icons.BUTTON_ICONS, b.text())      # no button forgotten
            self.assertFalse(b.icon().isNull(), b.text())
        test = win.pages[0]

        def picture(button):
            img = button.icon().pixmap(18, 18).toImage()
            return bytes(img.constBits())
        play = picture(test.go)
        test.set_busy("test")                                           # Start test became Stop test
        self.assertEqual(test.go.text(), "Stop test")
        self.assertFalse(test.go.icon().isNull())
        self.assertNotEqual(picture(test.go), play)                     # and its icon changed with it
        test.set_busy(None)
        win.set_theme(themes.THEMES["w98"])
        self.assertTrue(all(b.icon().isNull() for b in win.findChildren(QPushButton)))
        self.assertEqual([p.go.objectName() for p in win.pages[:2]], ["primary", "primary"])

if __name__ == "__main__":
    unittest.main()
