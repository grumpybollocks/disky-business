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


FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")


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


    # -- audit of 2026-11-28 (C1-C8) ---------------------------------------------------------------------------
    def maintenance(self, drives):
        win = self.open(drives)
        tab = self.page(win, "Maintenance")
        self.pick(tab, 0)
        return win, tab

    def test_c1_no_check_repair_or_trim_while_the_drive_is_being_mounted(self):
        win, tab = self.maintenance([drive("A", [], usb=True)])
        self.assertTrue(tab.check_button.isEnabled() and tab.repair_button.isEnabled())
        tab.panel.actions = mock.Mock(mount=lambda x: (time.sleep(0.6), (True, "Mounted."))[1],
                                      can_mount=lambda x: True, can_unmount=lambda x: False,
                                      can_remove=lambda x: False)
        tab.panel.update_actions()
        tab.panel.mount_button.click()
        self.assertTrue(tab.panel.acting)
        self.assertEqual((tab.check_button.isEnabled(), tab.repair_button.isEnabled(), tab.go.isEnabled()),
                         (False, False, False))                 # the mount would land on a repair
        started = []
        tab.runner.start = lambda *a, **k: started.append(a)
        tab.confirm = lambda text: True
        tab.targets = ["/somewhere"]                            # even if TRIM had something to do
        tab.check()
        tab.repair()
        tab.start()
        self.assertFalse(tab.verifying)
        tab.panel.settle()
        self.assertEqual(started, [])
        self.assertFalse(tab.panel.acting)

    def test_c2_c7_no_other_tab_and_no_silent_close_while_the_drive_is_read_again(self):
        d = drive("A", [], usb=True)
        win, tab = self.maintenance([d])
        tab.panel.read_drives = lambda: (time.sleep(0.5), [d])[1]
        tab.confirm = lambda text: False                        # the question comes; the answer is no
        here = win.tabs.currentIndex()
        tab.repair_button.click()
        self.assertTrue(tab.verifying and tab.working)
        self.assertFalse(win.tabs.tabBar().isEnabled())
        win.tabs.setCurrentIndex(1)                             # what Ctrl+Tab does
        self.assertEqual(win.tabs.currentIndex(), here)         # the question never appears over another tab
        asked = []
        win.confirm_close = lambda text: asked.append(text) or False
        win.close()
        from diskygui import window
        self.assertEqual(asked, [window.CLOSE_DRIVE])
        tab.panel.settle()
        self.assertFalse(tab.working)
        self.assertTrue(win.tabs.tabBar().isEnabled())
        win.confirm_close = lambda text: True

    def test_c3_the_drive_is_read_once_more_after_the_yes(self):
        first, second = drive("A", [], usb=True, path="/p/a"), drive("B", [], usb=True, path="/p/b")
        now = [first]
        win, tab = self.maintenance(lambda: list(now))
        started = []
        tab.runner.start = lambda *a, **k: started.append(a)

        def yes_but_the_drive_was_swapped_meanwhile(text):
            now[:] = [second]                                   # same name sdX1, another drive
            return True
        tab.confirm = yes_but_the_drive_was_swapped_meanwhile
        tab.repair_button.click()
        tab.panel.settle()
        self.assertEqual(started, [])                           # nothing runs on a drive that was not confirmed
        self.assertIn("The drives changed", tab.maint_status.text())
        self.assertFalse(tab.working)

    def test_c4_c5_switching_tabs_while_drive_lists_are_on_their_way(self):
        import tests
        tests.WAIT_FOR_DRIVE_LIST = False                       # this test is about lists still in flight
        self.addCleanup(setattr, tests, "WAIT_FOR_DRIVE_LIST", True)
        d = drive("A", [self.folder()])
        win = MainWindow(theme=THEMES["w98"], runner=EngineRunner([sys.executable, "-c", "pass"]),
                         drives=lambda: (time.sleep(0.4), [d])[1])
        self.addCleanup(win.close)
        win.confirm_close = lambda text: True
        for page in win.pages:
            page.panel.read_smart = lambda d: ([], "")
        win.tabs.setCurrentIndex(1)                             # the first tab is still reading its list
        second = win.pages[1]
        self.assertTrue(second.panel.settle())
        self.assertEqual(second.panel.drive_list.count(), 1)    # not an empty list waiting for ever
        self.assertNotIn("Reading", second.panel.health.text())
        for page in win.pages:
            page.panel.settle()
        self.pick(second, 0)
        third = win.pages[2]
        third.panel.load_drives()                               # its own list is on its way ...
        win.tabs.setCurrentIndex(2)                             # ... when the picked drive arrives from the last tab
        self.assertTrue(third.panel.settle())
        self.assertEqual((third.panel.row(), third.panel.drive_list.count()), (0, 1))   # the late list did not unpick it

    def test_c6_a_drive_action_always_ends_and_one_bad_answer_does_not_hold_the_rest(self):
        win = self.open([drive("A", [self.folder()])])
        panel = win.pages[0].panel
        panel.acting = True
        panel.load_drives(lambda: setattr(panel, "acting", False))  # what a drive action waits for
        panel.load_drives()                                     # and something else reads the list again
        self.assertTrue(panel.settle())
        self.assertFalse(panel.acting)                          # the first was not dropped
        ran = []
        panel.later(lambda: 1, lambda result: 1 / 0)            # an answer that cannot be shown
        panel.later(lambda: 2, ran.append)
        panel.acting = True
        self.assertTrue(panel.settle())
        self.assertEqual((ran, len(panel.failed), panel.acting), ([2], 1, False))

    def test_c8_closing_during_a_repair_gets_the_stronger_warning(self):
        from diskygui import window
        win = self.open([])
        win.show()
        asked = []
        win.confirm_close = lambda text: asked.append(text) or False
        page = self.page(win, "Maintenance")
        page.set_busy("repair")
        win.close()
        self.assertEqual(asked, [window.CLOSE_REPAIR])
        self.assertNotEqual(window.CLOSE_REPAIR, window.CLOSE_DRIVE)
        self.assertTrue(win.isVisible())
        page.set_busy(None)

    # -- usability (review items Q1-Q11) -----------------------------------------------------------------------
    def test_q1_no_start_over_old_test_files_and_no_red_alarm(self):
        f = self.folder()
        open(os.path.join(f, "1.h2w"), "w").close()
        win = self.open([drive("A", [f])])
        tab = win.pages[0]
        self.pick(tab, 0)
        self.assertFalse(tab.go.isEnabled())                    # check or delete them first
        self.assertTrue(tab.check.isEnabled() and tab.remove.isEnabled())
        self.assertIn("already holds 1 Disky test file", tab.plan.text())
        self.assertIn("Check test files again", tab.go.toolTip())
        os.remove(os.path.join(f, "1.h2w"))
        tab.confirm = lambda text: True
        tab.remove.click()                                      # the pretend engine reports them deleted
        end = time.monotonic() + 5
        while tab.mode is not None and time.monotonic() < end:
            self.app.processEvents()
        tab.panel.settle()
        self.assertTrue(tab.go.isEnabled())                     # now a new test can start

    def test_q2_q11_a_result_names_its_partition_goes_with_it_and_a_repair_is_remembered(self):
        from diskygui import maintenancetab
        two = dr.Drive("A", 1 << 36, "HDD", True, dr.Health(), [dr.Volume("sdz1", 1 << 30, "vfat", []),
                                                                dr.Volume("sdz2", 1 << 30, "ext4", [])], path="/p/a")
        win = MainWindow(theme=THEMES["w98"], drives=lambda: [two],
                         runners={"Maintenance": EngineRunner([sys.executable, FAKE, "bad"])})
        self.addCleanup(win.close)
        win.confirm_close = lambda text: True
        tab = self.page(win, "Maintenance")
        self.pick(tab, 0)
        tab.parts.setCurrentRow(0)
        tab.check_button.click()
        self.finish(tab)
        self.assertEqual(tab.maint_status.text(), "sdz1: Problems found.")
        tab.parts.setCurrentRow(1)
        self.assertEqual(tab.maint_status.text(), "")           # not shown beside another partition
        tab.parts.setCurrentRow(0)
        asked = []
        tab.confirm = lambda text: asked.append(text) or True
        tab.repair_button.click()
        self.finish(tab)
        self.assertEqual(tab.maint_status.text(), "sdz1: Repaired.")
        self.assertIn("The last check found problems.", asked[0])
        tab.confirm = lambda text: asked.append(text) or False
        tab.repair_button.click()
        tab.panel.settle()
        self.assertIn("repaired in this session and has not been checked since", asked[1])
        self.assertNotIn("found problems", asked[1])
        self.assertEqual(maintenancetab.MaintenanceTab.NO_STOP, ("check", "repair"))

    def finish(self, tab, seconds=6.0):
        tab.panel.settle()
        end = time.monotonic() + seconds
        while tab.mode is not None:
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)
        tab.panel.settle()

    def test_q3_maintenance_shows_no_folder_line(self):
        win = self.open([])
        tab = self.page(win, "Maintenance")
        self.assertTrue(tab.panel.folder_box.isHidden() and tab.panel.choose.isHidden())
        self.assertFalse(win.pages[0].panel.folder_box.isHidden())

    def test_q4_keys_refresh_stop_and_quit(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        def press(win, key, modifiers=Qt.NoModifier):
            win.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, modifiers))
        calls = []
        win = self.open(lambda: calls.append(1) or [])
        win.show()
        before = len(calls)
        press(win, Qt.Key_F5)
        win.pages[0].panel.settle()
        self.assertEqual(len(calls), before + 1)                # the drive list was read again
        tab = win.pages[0]
        stopped = []
        tab.runner.running = lambda: True
        tab.runner.stop = lambda: stopped.append("stop")
        tab.mode = "test"
        press(win, Qt.Key_Escape)
        self.assertEqual(stopped, ["stop"])
        tab.mode, tab.NO_STOP = "repair", ("repair",)
        press(win, Qt.Key_Escape)
        self.assertEqual(stopped, ["stop"])                     # a repair is never cut short
        tab.mode = None
        del tab.runner.running
        press(win, Qt.Key_Q)                                    # a plain Q is not a command
        self.assertTrue(win.isVisible())
        win.confirm_close = lambda text: True
        press(win, Qt.Key_Q, Qt.ControlModifier)
        self.assertFalse(win.isVisible())

    def test_q6_q8_the_speed_test_shows_how_far_it_is(self):
        f = self.folder()
        win = MainWindow(theme=THEMES["w98"], drives=lambda: [drive("A", [f])],
                         runners={"Speed": EngineRunner([sys.executable, FAKE, "slow"])})
        self.addCleanup(win.close)
        win.confirm_close = lambda text: True
        tab = self.page(win, "Speed")
        self.pick(tab, 0)
        self.assertEqual(tab.go.text(), "Start speed test")
        self.assertTrue(tab.progress.isHidden())
        tab.go.click()
        end = time.monotonic() + 5
        while "measurement 1 of 4" not in tab.progress.left.text() and time.monotonic() < end:
            self.app.processEvents()
        self.assertFalse(tab.progress.isHidden())
        self.assertEqual((tab.progress.title.text(), tab.progress.left.text()), ("Writing in one go", "measurement 1 of 4"))
        tab.show_progress(0.5)
        self.assertEqual(tab.progress.bar.value(), 12)          # half of the first of four
        tab.go.click()
        self.assertEqual(tab.progress.title.text(), "Stopping")
        self.finish(tab)
        self.assertTrue(tab.progress.isHidden())

    def test_q7_end_says_why_it_waits(self):
        win = self.open([])
        self.assertTrue(win.admin_button.isEnabled())
        win.pages[0].set_busy("test")
        self.assertFalse(win.admin_button.isEnabled())
        self.assertIn("A job is running", win.admin_button.toolTip())
        win.pages[0].set_busy(None)
        self.assertTrue(win.admin_button.isEnabled())
        self.assertIn("password was given once", win.admin_button.toolTip())

    def test_q9_no_deep_scan_for_sticks_and_cards(self):
        stick = drive("STICK", usb=True, kind="stick")
        stick.device = "sdz"
        disk = drive("DISK", usb=True, path="/p/disk")
        disk.device = "sdy"
        win = self.open([stick, disk])
        tab = self.page(win, "SMART")
        self.pick(tab, 0)
        self.assertTrue(tab.deep_button.isHidden())
        self.pick(tab, 1)
        self.assertFalse(tab.deep_button.isHidden())

    def test_words_times_switched_on_is_explained_and_defrag_says_it_once(self):
        from diskygui import defragtab, smart
        rows = smart.ata_rows([[12, "power-cycle-count", 50, 99, 99, 0, 7, 1, {}]])
        self.assertEqual((rows[0].name, rows[0].about), ("Times switched on", "How often the drive was powered up."))
        self.assertTrue(all(r.about for r in smart.nvme_rows({"power_cycles": 7})))
        self.assertNotIn("Scanning only reads", defragtab.DefragTab.NOTE)

if __name__ == "__main__":
    unittest.main()
