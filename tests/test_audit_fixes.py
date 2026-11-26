# SPDX-License-Identifier: GPL-3.0-or-later
"""Findings of the 2026-11-26 code audit, one test each. Each was seen to fail when its fix was undone."""
import errno
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from disky import cli, engine, exfat, frag, mounts, proc, safety, trim
from tests.util import Folder

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_engine.py")
FILE = 1 << 20


def mountinfo(*rows):
    """A made-up /proc/self/mountinfo: rows of (mount point as the kernel writes it, fs type, device)."""
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "w") as f:
        for i, (point, kind, dev) in enumerate(rows):
            f.write(f"{i + 20} 1 0:{i} / {point} rw - {kind} {dev} rw\n")
    return path


def events(argv, **kw):
    out = []
    with mock.patch.object(cli, "Printer", lambda as_json: out.append):
        code = cli.main(argv, **kw)
    return code, out


class Engine(unittest.TestCase):
    def test_E3_mount_paths_with_tabs_and_backslashes(self):
        info = mountinfo(("/", "btrfs", "/dev/a"), ("/run/media/x/My\\011Win\\134dows", "ntfs3", "/dev/b"))
        self.addCleanup(os.remove, info)
        self.assertEqual(frag.fs_type("/run/media/x/My\tWin\\dows/f", info), "ntfs3")
        self.assertEqual(mounts.below("/run/media", info), {"/run/media/x/My\tWin\\dows"})

    def test_E3_folder_scan_never_enters_another_mount(self):
        with Folder() as top:
            os.mkdir(os.path.join(top, "other"))
            with open(os.path.join(top, "other", "f.bin"), "wb") as f:
                f.write(b"x" * 8192)
            looked = []
            real = os.lstat
            with mock.patch.object(frag.mounts, "below", lambda folder: {os.path.join(top, "other")}), \
                    mock.patch.object(frag.os, "lstat", lambda p, *a, **k: looked.append(p) or real(p, *a, **k)):
                try:
                    frag.scan_folder(top)
                except frag.NotSupported:
                    pass                        # this test folder's file system has no extent map: fine
            self.assertNotIn(os.path.join(top, "other"), looked)

    def test_E3_exfat_image_on_ntfs_is_not_read(self):
        with Folder() as d:
            image = os.path.join(d, "volume.img")
            open(image, "wb").close()
            with mock.patch.object(cli.mounts, "holding", lambda p, *a: ("/mnt/w", "ntfs3", "/dev/x")), \
                    mock.patch.object(cli.exfat, "scan", side_effect=AssertionError("must not read")):
                code, out = events(["fragscan", image, "--json"])
        self.assertEqual((code, out[-1]["code"]), (3, "not_supported"))

    def test_E4_stop_ends_a_system_tool(self):
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        t = time.monotonic()
        done = proc.run([sys.executable, "-c", "import time; time.sleep(30)"], stop=stop)
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual(done.ended, "stopped")
        done = proc.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.3)
        self.assertEqual(done.ended, "timeout")
        done = proc.run([sys.executable, "-c", "print('hi')"])
        self.assertEqual((done.ended, done.returncode, done.stdout), ("", 0, "hi\n"))

    def test_E4_trim_and_exfat_scan_obey_stop(self):
        def stopped(cmd, **kw):
            r = subprocess.CompletedProcess(cmd, -2, "", "")
            r.ended = "stopped"
            return r
        yes = lambda p: {"supported": True, "mount": "/m"}  # noqa: E731
        with self.assertRaises(proc.Stopped):
            trim.run("/m", run=stopped, is_root=True, info_of=yes)
        from tests.test_exfat import build
        with Folder() as d:
            path = os.path.join(d, "v.img")
            with open(path, "wb") as f:
                f.write(build())
            stop = threading.Event()
            stop.set()
            with self.assertRaises(proc.Stopped):
                exfat.scan(path, stop=stop)
            code, out = events(["fragscan", path, "--json"], stop=stop)
            self.assertEqual(code, 130)

    def test_E5_defrag_timeout_is_reported_not_a_crash(self):
        def slow(cmd, **kw):
            r = subprocess.CompletedProcess(cmd, -2, "", "")
            r.ended = "timeout"
            return r
        with Folder() as d:
            path = os.path.join(d, "f.bin")
            with open(path, "wb") as f:
                f.write(b"y" * 8192)
            with mock.patch.object(frag, "fs_type", lambda p: "ext4"), \
                    mock.patch.object(frag.shutil, "which", lambda t: "/usr/bin/" + t), \
                    mock.patch.object(frag, "extents", lambda p: [(0, 0, 8192)]):
                r = frag.defrag_file(path, run=slow)
                self.assertEqual(r["ended"], "timeout")
                with mock.patch.object(cli.frag, "defrag_file", lambda p, stop=None: r):
                    code, out = events(["defrag", path, "--json"])
        self.assertEqual(code, 3)
        self.assertIn("timeout", [e.get("code") for e in out])

    def test_E6_verify_alone_notices_a_missing_middle_file(self):
        with Folder() as folder:
            engine.write(folder, lambda e: None, size=3 * FILE, file_bytes=FILE)
            os.remove(os.path.join(folder, "2.h2w"))
            seen = []
            s = engine.verify(folder, seen.append, file_bytes=FILE, drop_cache=False)
        self.assertEqual((s["verdict"], s["missing_bytes"], s["first_bad_offset"]), ("bad", FILE, FILE))
        self.assertIn("missing_file", [e.get("code") for e in seen])

    def test_E7_a_folder_in_memory_gets_the_reserve(self):
        info = mountinfo(("/", "btrfs", "/dev/a"), ("/tmp", "tmpfs", "tmpfs"), ("/data", "ext4", "/dev/b"))
        self.addCleanup(os.remove, info)
        self.assertTrue(safety.is_system_drive("/tmp/x", info, home="/home/u"))
        self.assertFalse(safety.is_system_drive("/data/x", info, home="/home/u"))

    def test_E8_drive_full_found_while_flushing_ends_writing_normally(self):
        real = os.fdatasync
        state = {"n": 0}

        def full_once(fd):
            state["n"] += 1
            if state["n"] == 1:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(fd)
        with Folder() as folder, mock.patch.object(engine.os, "fdatasync", full_once):
            written = engine.write(folder, lambda e: None, size=200 * FILE, file_bytes=100 * FILE)
        self.assertLess(written, 200 * FILE)

    def test_E10_speed_test_never_removes_a_file_it_did_not_make(self):
        from disky import speed
        with Folder() as folder:
            theirs = os.path.join(folder, speed.NAME)
            orig = os.path.lexists
            # Another program makes the file just after the first look.
            with mock.patch.object(speed.os.path, "lexists",
                                   lambda p: (orig(p), open(theirs, "w").write("theirs"))[0]):
                with self.assertRaises(Exception):
                    speed.run(folder, lambda e: None, size=1 << 20, seconds=0.1)
            with open(theirs) as f:
                self.assertEqual(f.read(), "theirs")

    def test_W3_W8_stop_by_input_and_stop_while_starting(self):
        stop = threading.Event()
        with mock.patch.object(cli.sys, "stdin", io.StringIO("hello\nstop\n")):
            cli._listen(stop)
        self.assertTrue(stop.is_set())
        with Folder() as folder:
            code, out = events(["test", folder, "--json", "--keep-free", "0"], stop=stop)
        self.assertEqual((code, [e["event"] for e in out]), (130, ["start", "warning", "done"]))
        self.assertEqual(os.listdir(folder) if os.path.isdir(folder) else [], [])


try:
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as e:
    if "PySide6" not in str(e):
        raise
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, done, seconds=8.0):
        end = time.monotonic() + seconds
        while not done():
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def window(self, title, scenario):
        win = MainWindow(theme=THEMES["w98"], drives=lambda: [],
                         runners={title: EngineRunner([sys.executable, FAKE, scenario])})
        self.addCleanup(win.close)
        return next(p for p in win.pages if p.title == title)

    def test_W3_stop_reaches_an_engine_our_signals_cannot(self):
        r = EngineRunner([sys.executable, FAKE, "waitstop"])
        ended = []
        r.finished.connect(lambda code, crash: ended.append(code))
        r.start("test", "/x")
        self.wait(lambda: r._proc is not None and r._proc.processId() > 0 and r._proc.state() == QProcess.Running)
        with mock.patch("diskygui.runner.os.kill", side_effect=PermissionError):
            r.stop()
        self.wait(lambda: ended)
        self.assertEqual(ended, [130])

    def test_W4_a_malformed_last_event_does_not_lock_the_tabs(self):
        tab = self.window("Test", "badsummary")
        tab.set_folder("/a/folder")
        tab.go.click()
        self.wait(lambda: tab.mode is None)
        self.assertTrue(tab.window().tabs.tabBar().isEnabled())

    def test_W5_defrag_says_when_the_tool_failed(self):
        tab = self.window("Defrag", "toolfail")
        tab.fs_type = lambda path: "ext4"
        tab.set_folder("/a/folder")
        tab.worst = [{"pieces": 64, "bytes": 8 << 20, "path": "/a/folder/big.bin"}]
        tab._show_worst()
        tab.files.setCurrentRow(0)
        tab.confirm = lambda text: True
        tab.defrag_selected()
        self.wait(lambda: tab.mode is None)
        self.assertIn("did not finish", tab.status.text())

    def test_W6_exfat_scan_asks_before_the_password_prompt(self):
        tab = self.window("Defrag", "good")
        tab.fs_type = lambda path: "exfat"
        tab.device_of = lambda path: "/dev/sdz9"
        tab.set_folder("/a/folder")
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        started = []
        tab.runner.start = lambda *a, **k: started.append(a)
        tab.scan_button.click()
        self.assertEqual((len(asked), started, tab.mode), (1, [], None))
        self.assertIn("admin password", asked[0])

    def test_W9_test_folder_that_cannot_be_made_leaves_the_tab_free(self):
        from diskygui import drives as dr
        tab = self.window("Test", "good")
        tab.set_folder(os.path.join("/proc/no-such-place", dr.TEST_FOLDER))
        tab.go.click()
        self.assertIsNone(tab.mode)
        self.assertIn("Could not make the test folder", tab.status.text())


if __name__ == "__main__":
    unittest.main()
