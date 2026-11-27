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
