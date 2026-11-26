# SPDX-License-Identifier: GPL-3.0-or-later
"""The TRIM tab with made-up drive facts and the pretend engine."""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui.defragtab import admin_wrap
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise
    HAVE_QT = False

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")
YES = {"mount": "/drive", "device": "/dev/fake1", "supported": True, "discard_max_bytes": 1 << 31,
       "discard_granularity": 4096, "rotational": False}
NO = dict(YES, supported=False, discard_max_bytes=0, rotational=True)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TrimTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open(self, scenario, facts):
        self.win = MainWindow(theme=THEMES["w98"], drives=lambda: [],
                              runners={"Care": EngineRunner([sys.executable, FAKE, scenario])})
        self.addCleanup(self.win.close)
        self.win.show()
        tab = next(p for p in self.win.pages if p.title == "Care")
        self.assertEqual(tab.title, "Care")
        tab.info_of = lambda folder: facts
        self.assertEqual(tab.wrap(), admin_wrap())     # the real tab asks through the system prompt
        tab.wrap = lambda: ["env", "PRETEND_PROMPT=1"]   # tests never call the real prompt
        tab.confirm = lambda text: True
        self.win.tabs.setCurrentWidget(tab)
        tab.set_folder("/drive/folder")
        return tab

    def run_it(self, tab):
        tab.go.click()
        end = time.monotonic() + 5
        while tab.mode is not None:
            self.assertLess(time.monotonic(), end)
            self.app.processEvents()
            time.sleep(0.005)

    def test_supported_drive_is_trimmed_through_the_password_prompt(self):
        tab = self.open("good", YES)
        self.assertEqual(tab.answer.text(), "This drive accepts TRIM.")
        self.assertTrue(tab.go.isEnabled())
        self.run_it(tab)
        self.assertEqual(tab.runner.last_argv[:2], ["env", "PRETEND_PROMPT=1"])     # the prompt goes first
        self.assertEqual(tab.runner.last_argv[-4:], ["trim", "/drive/folder", "--json", "--stdin-stop"])
        self.assertEqual(tab.status.text(), "Done: 5.0 GiB of free space trimmed.")

    def test_unsupported_drive_offers_nothing(self):
        tab = self.open("good", NO)
        self.assertEqual(tab.answer.text(), "This drive does not accept TRIM.")
        self.assertFalse(tab.go.isEnabled())
        tab.start()
        self.assertIsNone(tab.mode)

    def test_asks_first_and_says_why_it_did_not_run(self):
        tab = self.open("good", YES)
        tab.confirm = lambda text: False
        tab.go.click()
        self.assertIsNone(tab.mode)
        tab = self.open("nopassword", YES)
        self.run_it(tab)
        self.assertEqual(tab.status.text(), "Not trimmed: the admin password was not given.")
        tab = self.open("refuse", YES)
        self.run_it(tab)
        self.assertEqual(tab.status.text(), "This drive does not accept TRIM.")


    def test_a_picked_drive_without_a_folder_says_why(self):
        from diskygui import drives as dr
        win = MainWindow(theme=THEMES["w98"], drives=lambda: [dr.Drive("EXAMPLE HDD", 1 << 40, "HDD", False,
                                                                       dr.Health())])
        self.addCleanup(win.close)
        tab = next(p for p in win.pages if p.title == "Care")
        tab.panel.drive_list.setCurrentRow(0)
        self.assertEqual(tab.answer.text(), "Nothing to trim on this drive here.")
        self.assertTrue(tab.detail.text())
        self.assertFalse(tab.go.isEnabled())

if __name__ == "__main__":
    unittest.main()
