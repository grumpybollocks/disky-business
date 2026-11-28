# SPDX-License-Identifier: GPL-3.0-or-later
"""Bugs found by the 2026-11-28 review of the window, one test each (B1-B12 in the notes)."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui import drives as dr
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise
    HAVE_QT = False


def drive(model, mounts=(), fstype="ext4", kind="SSD", usb=False, path=None, volumes=None):
    vols = volumes if volumes is not None else [dr.Volume("sdz1", 1 << 30, fstype, list(mounts), False)]
    return dr.Drive(model, 1 << 30, kind, usb, dr.Health(), vols, path=path or "/p/" + model)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Hunt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def folder(self):
        f = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, f)
        return f

    def open(self, found):
        listing = found if callable(found) else (lambda: list(found))
        win = MainWindow(theme=THEMES["w98"], runner=EngineRunner([sys.executable, "-c", "pass"]), drives=listing)
        self.addCleanup(win.close)
        win.confirm_close = lambda text: True
        for page in win.pages:
            page.panel.read_smart = lambda d: ([], "")
            page.panel.settle()
        return win

    def page(self, win, title):
        p = next(p for p in win.pages if p.title == title)
        win.tabs.setCurrentWidget(p)
        p.panel.settle()
        return p

    def pick(self, page, row):
        page.panel.drive_list.setCurrentRow(row)
        page.panel.settle()

    def test_b1_a_new_drive_offers_no_defragment_all_before_its_own_scan(self):
        a, b = self.folder(), self.folder()
        win = self.open([drive("A", [a]), drive("B", [b])])
        tab = self.page(win, "Defrag")
        tab.fs_type = lambda path: "ext4"
        self.pick(tab, 0)
        tab.on_event({"event": "frag_files", "files": 9, "fragmented_files": 5, "file_bytes": 9, "fragmented_bytes": 5,
                      "skipped": 0, "worst": []})
        tab.update_buttons()
        self.assertTrue(tab.defrag_button.isEnabled())
        self.pick(tab, 1)
        self.assertFalse(tab.defrag_button.isEnabled())

    def test_b2_a_changed_drive_list_reaches_the_next_tab(self):
        listed = [[drive("U", path="/p/u", volumes=[dr.Volume("sdz1", 1, "ext4", [], False)])]]
        win = self.open(lambda: list(listed[0]))
        test, speed = win.pages[0], self.page(win, "Speed")
        win.tabs.setCurrentWidget(test)
        self.pick(test, 0)
        win.tabs.setCurrentWidget(speed)
        win.tabs.setCurrentWidget(test)
        listed[0] = [drive("C", path="/p/c", volumes=[dr.Volume("sdy1", 1, "ext4", [], False)])] + listed[0]
        test.panel.refresh.click()
        test.panel.settle()
        self.pick(test, 0)
        win.tabs.setCurrentWidget(speed)
        speed.panel.settle()
        self.assertEqual(speed.panel.current_drive().model, "C")

    def test_b3_nothing_else_can_start_while_a_drive_is_being_mounted(self):
        f = self.folder()
        d = drive("A", [f])
        win = self.open([d])
        tab = win.pages[0]
        self.pick(tab, 0)
        self.assertTrue(tab.go.isEnabled())
        tab.panel.actions = mock.Mock(unmount=lambda x: (time.sleep(0.6), (True, "Unmounted."))[1],
                                      can_mount=lambda x: False, can_unmount=lambda x: True,
                                      can_remove=lambda x: False)
        tab.panel.update_actions()
        tab.panel.unmount_button.click()
        self.assertFalse(tab.go.isEnabled())                    # no test on a drive being unmounted
        self.assertFalse(tab.panel.drive_list.isEnabled())
        win.tabs.setCurrentIndex(1)
        self.assertEqual(win.tabs.currentIndex(), 0)            # and no other tab, with its own Mount button
        tab.panel.settle()
        self.assertTrue(tab.panel.drive_list.isEnabled())

    def test_b4_speed_results_belong_to_their_drive(self):
        a, b = self.folder(), self.folder()
        win = self.open([drive("A", [a]), drive("B", [b])])
        tab = self.page(win, "Speed")
        self.pick(tab, 0)
        empty = tab.bars.values["seq_write"].text()
        tab.bars.set_result("seq_write", 300, "300 MiB/s")
        self.pick(tab, 1)
        self.assertEqual(tab.bars.values["seq_write"].text(), empty)

    def test_b5_the_test_map_belongs_to_its_drive(self):
        a, b = self.folder(), self.folder()
        win = self.open([drive("A", [a]), drive("B", [b])])
        tab = win.pages[0]
        self.pick(tab, 0)
        tab.model.set_total_blocks(10)
        tab.model.set_blocks(0, 10, 5)
        self.pick(tab, 1)
        self.assertFalse(tab.model.states.any())

    def test_b6_unpicking_the_drive_leaves_nothing_to_start(self):
        f = self.folder()
        win = self.open([drive("A", [f])])
        tab = win.pages[0]
        self.pick(tab, 0)
        self.assertTrue(tab.go.isEnabled())
        tab.panel.drive_list.clearSelection()                   # what Ctrl+click does
        tab.panel.settle()
        self.assertEqual((tab.folder, tab.go.isEnabled()), ("", False))

    def test_b7_a_slow_drive_list_does_not_hold_up_the_window(self):
        def slow():
            time.sleep(0.5)
            return [drive("A")]
        import tests
        tests.WAIT_FOR_DRIVE_LIST = False                       # this test is about not waiting
        self.addCleanup(setattr, tests, "WAIT_FOR_DRIVE_LIST", True)
        start = time.monotonic()
        win = MainWindow(theme=THEMES["w98"], runner=EngineRunner([sys.executable, "-c", "pass"]), drives=slow)
        self.addCleanup(win.close)
        win.confirm_close = lambda text: True
        self.assertLess(time.monotonic() - start, 0.4)          # five tabs used to ask one after the other
        tab = win.pages[0]
        self.assertIn("Reading the drive list", tab.panel.health.text())
        self.assertTrue(tab.panel.settle())
        self.assertEqual(tab.panel.drive_list.count(), 1)
        start = time.monotonic()
        tab.panel.refresh.click()
        self.assertLess(time.monotonic() - start, 0.3)
        self.assertTrue(tab.panel.settle())
        for page in win.pages:
            page.panel.settle()

    def test_b8_a_windows_only_drive_is_not_told_to_mount(self):
        win = self.open([drive("W", ["/w"], fstype="ntfs")])
        tab = self.page(win, "Maintenance")
        tab.info_of = lambda place: None
        self.pick(tab, 0)
        self.assertNotIn("Mount", tab.answer.text())
        self.assertIn("Windows", tab.answer.text() + tab.detail.text())

    def test_b9_a_blank_stick_is_told_why_it_cannot_be_used(self):
        blank = drive("STICK", kind="stick", usb=True, volumes=[])
        folder, why = blank.target()
        self.assertIsNone(folder)
        self.assertNotIn("Press Mount", why)
        self.assertIn("no file system", why)
        unformatted = drive("STICK", kind="stick", usb=True, volumes=[dr.Volume("sdz", 1 << 30, "", [], False)])
        self.assertIn("no file system", unformatted.target()[1])

    def test_b10_a_folder_on_another_drive_unpicks_the_listed_drive(self):
        f, elsewhere = self.folder(), self.folder()
        win = self.open([drive("A", [f])])
        tab = win.pages[0]
        self.pick(tab, 0)
        with mock.patch("diskygui.drivepanel.QFileDialog.getExistingDirectory", lambda *a, **k: elsewhere):
            tab.panel.choose.click()
        tab.panel.settle()
        self.assertEqual((tab.folder, tab.panel.current_drive()), (elsewhere, None))
        inside = os.path.join(f, "sub")
        self.pick(tab, 0)
        with mock.patch("diskygui.drivepanel.QFileDialog.getExistingDirectory", lambda *a, **k: inside):
            tab.panel.choose.click()
        self.assertEqual((tab.folder, tab.panel.current_drive().model), (inside, "A"))    # a folder on A keeps A

    def test_b11_closing_during_a_job_asks_first(self):
        win = self.open([])
        win.show()
        asked = []
        win.confirm_close = lambda text: asked.append(text) or False
        win.pages[0].set_busy("test")
        win.close()
        self.assertTrue(win.isVisible())                        # the user said no: the test goes on
        self.assertIn("test", asked[0].lower())
        win.pages[0].set_busy(None)
        win.close()
        self.assertFalse(win.isVisible())
        self.assertEqual(len(asked), 1)                         # nothing running: no question

    def test_b12_stopping_the_speed_test_stays_said(self):
        f = self.folder()
        win = self.open([drive("A", [f])])
        tab = self.page(win, "Speed")
        self.pick(tab, 0)
        tab.set_busy("speed")
        with mock.patch.object(tab.runner, "stop"):
            tab.start_or_stop()
        tab.on_event({"event": "speed_phase", "test": "seq_read", "total_bytes": 1})
        self.assertEqual(tab.status.text(), "Stopping...")
        tab.set_busy(None)


if __name__ == "__main__":
    unittest.main()
