# SPDX-License-Identifier: GPL-3.0-or-later
"""After every test: let the window's background jobs end, carry out pending widget deletions, and collect
garbage on the main thread.

Found 2026-11-27: the suite crashed now and then (Segmentation fault, about 1 run in 5, on main too). Widgets of
earlier tests were left as garbage; when Python's garbage collector happened to run on a background thread (a
drive job), PySide queued their deletion to the main thread and Qt freed an already freed label. Collecting on
the main thread at the end of each test leaves nothing for a background thread to collect.
"""
import gc
import threading
import time
import unittest

_run = unittest.TestCase.run


def _settle(seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and any(t is not threading.main_thread() and t.name.endswith("(_run)")
                                         for t in threading.enumerate()):
        time.sleep(0.01)
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
    except ImportError:
        return
    app = QCoreApplication.instance()
    if app is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    import sys
    panel = sys.modules.get("diskygui.drivepanel")
    if panel is not None:
        panel._resume_collection()                  # the jobs are over: collection back on, as the window does
    gc.collect()


def _run_then_settle(self, result=None):
    try:
        return _run(self, result)
    finally:
        _settle()


unittest.TestCase.run = _run_then_settle


def _no_real_question_boxes():
    """A question box that nobody answers would hang the whole test run. In tests every real one answers
    "No" at once; a test that wants "Yes" sets the tab's or the window's own confirm function."""
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:
        return
    QMessageBox.question = staticmethod(lambda *args, **kwargs: QMessageBox.No)


_no_real_question_boxes()


WAIT_FOR_DRIVE_LIST = True      # a test that measures the waiting itself sets this to False for its duration


def _first_drive_list_before_the_test_goes_on():
    """The drive list is read off the window's thread, so a window is there before its drives are. Tests
    written as "open the window, then look at the list" would look too early: after a drive panel is built in
    a test, wait until its first list has arrived. The app itself never waits; that is tested on its own."""
    try:
        from diskygui import drivepanel
    except ImportError:
        return
    built = drivepanel.DrivePanel.__init__

    def build(self, *args, **kwargs):
        built(self, *args, **kwargs)
        if WAIT_FOR_DRIVE_LIST:
            self.settle()
    drivepanel.DrivePanel.__init__ = build


_first_drive_list_before_the_test_goes_on()


def others_locked(win, tab):
    """While `tab` works: every other tab can be opened, and on each the drive panel and every button is off."""
    from PySide6.QtWidgets import QPushButton
    others = [p for p in win.pages if p is not tab]
    return bool(others) and all(
        p.locked_by == tab.title and not p.panel.isEnabled() and not p.lock_note.isHidden()
        and not any(w.isEnabled() for w in vars(p).values() if isinstance(w, QPushButton)) for p in others)


def none_locked(win):
    return all(p.locked_by is None and p.panel.isEnabled() and p.lock_note.isHidden() for p in win.pages)
