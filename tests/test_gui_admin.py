# SPDX-License-Identifier: GPL-3.0-or-later
"""The admin session: one password prompt for many jobs, shown in the window, ended on request or on close."""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui.adminsession import AdminSession, admin_wrap
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise
    HAVE_QT = False

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")
PROMPT = ["env", "PRETEND_PROMPT=1"]                    # stands in for the password prompt


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Session(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def session(self, scenario="good"):
        s = AdminSession(prefix=PROMPT, command=[sys.executable, FAKE, scenario])
        self.addCleanup(s.end)
        return s

    def runner(self, s, own="good"):
        r = EngineRunner([sys.executable, FAKE, own])
        r.session = s
        r.seen, r.ended = [], []
        r.event.connect(r.seen.append)
        r.finished.connect(lambda code, crash: r.ended.append((code, crash)))
        return r

    def wait(self, done, seconds=8.0):
        end = time.monotonic() + seconds
        while not done():
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def test_one_prompt_for_many_jobs_from_different_tabs(self):
        s = self.session()
        opened = []
        s.changed.connect(opened.append)
        a, b = self.runner(s), self.runner(s)
        self.assertTrue(a.start("trim", "/drive/folder", wrap=PROMPT))
        self.assertFalse(b.start("smart", "sdz", wrap=PROMPT))             # one job at a time
        self.wait(lambda: a.ended)
        self.assertEqual((a.ended, opened, s.launches), ([(0, "")], [True], 1))
        self.assertIn("trim_result", [e["event"] for e in a.seen])
        first = s._proc.processId()
        self.assertTrue(b.start("smart", "sdz", wrap=PROMPT))
        self.wait(lambda: b.ended)
        self.assertEqual(b.ended, [(0, "")])
        self.assertIn("smart_data", [e["event"] for e in b.seen])
        self.assertEqual((s.launches, s._proc.processId()), (1, first))    # the same helper: no second prompt
        self.assertEqual(a.seen.count({"event": "smart_data"}), 0)         # each job's events go to its own tab
        self.assertNotIn("smart_data", [e["event"] for e in a.seen])

    def test_only_jobs_behind_the_real_prompt_and_only_the_helpers_jobs(self):
        s = self.session()
        r = self.runner(s)
        r.start("trim", "/drive/folder", wrap=["env", "ANOTHER=1"])        # not this session's prompt
        self.wait(lambda: r.ended)
        self.assertEqual((s.launches, r.ended), (0, [(0, "")]))            # ran on its own, as before
        r.ended.clear()
        r.start("speed", "/drive/folder", wrap=PROMPT)                     # not a job the helper does
        self.wait(lambda: r.ended)
        self.assertEqual(s.launches, 0)
        r.ended.clear()
        r.start("fscheck", "sdz1", admin="if_open")                        # session closed: runs on its own
        self.wait(lambda: r.ended)
        self.assertEqual(s.launches, 0)
        self.assertNotIn(PROMPT[0], r.last_argv[:1])
        opener = self.runner(s)
        opener.start("trim", "/drive/folder", wrap=PROMPT)
        self.wait(lambda: opener.ended)
        r.ended.clear()
        r.start("fscheck", "sdz1", admin="if_open")                        # session open: no password for it
        self.assertIs(s.client, r)
        self.wait(lambda: r.ended)
        self.assertEqual((s.launches, r.ended), (1, [(0, "")]))

    def test_no_password_given_and_ending_the_session(self):
        s = self.session("nopassword")
        r = self.runner(s)
        r.start("trim", "/drive/folder", wrap=PROMPT)
        self.wait(lambda: r.ended)
        self.assertEqual((r.ended, s.is_open()), ([(126, "")], False))
        s = self.session()
        states = []
        s.changed.connect(states.append)
        r = self.runner(s)
        r.start("trim", "/drive/folder", wrap=PROMPT)
        self.wait(lambda: r.ended)
        self.assertTrue(s.is_open())
        s.end()
        self.wait(lambda: not s.is_open())
        self.assertEqual(states, [True, False])
        r.ended.clear()
        r.start("trim", "/drive/folder", wrap=PROMPT)                      # the next job asks again
        self.wait(lambda: r.ended)
        self.assertEqual(s.launches, 2)

    def test_the_window_shows_the_session_and_ends_it(self):
        s = self.session()
        win = MainWindow(theme=THEMES["w98"], drives=lambda: [], admin=s)
        self.addCleanup(win.close)
        self.assertTrue(all(p.runner.session is s for p in win.pages))
        self.assertTrue(win.admin_button.isHidden())
        care = next(p for p in win.pages if p.title == "Care")
        self.assertIn("once for this session", care.password_words())
        r = self.runner(s)
        r.start("trim", "/drive/folder", wrap=PROMPT)
        self.wait(lambda: r.ended)
        self.assertFalse(win.admin_button.isHidden())
        self.assertIn("no password is asked", care.password_words())
        care.set_busy("check")
        win.admin_button.click()
        self.assertTrue(s.is_open())                                        # not under a running job
        care.set_busy(None)
        win.admin_button.click()
        self.wait(lambda: not s.is_open())
        self.assertTrue(win.admin_button.isHidden())

    def test_the_real_window_uses_the_real_prompt(self):
        win = MainWindow(theme=THEMES["w98"], drives=lambda: [])
        self.addCleanup(win.close)
        self.assertEqual(win.admin.prefix, admin_wrap())
        self.assertEqual(win.admin.prefix[0], "pkexec")
        self.assertEqual(win.admin.launches, 0)                             # nothing asked until a job needs it


if __name__ == "__main__":
    unittest.main()
