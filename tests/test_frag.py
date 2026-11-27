# SPDX-License-Identifier: GPL-3.0-or-later
"""Fragmentation reading and defragmenting, on real files in the test folder's file system."""
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout

from disky import cli, contract, frag
from tests.util import Folder, base_dir

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import make_fragmented  # noqa: E402

FS = frag.fs_type(base_dir())
CAN_DEFRAG = FS in frag.TOOLS and shutil.which(frag.TOOLS[FS][0]) is not None
SIZE, PIECE = 8 << 20, 128 << 10


def fragmented(folder):
    path = os.path.join(folder, "fragmented.bin")
    make_fragmented.make(path, SIZE, PIECE)
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)                    # this file only; a whole-system flush waits on every drive
    finally:
        os.close(fd)
    return path


class Reading(unittest.TestCase):
    def test_extents_cover_the_file_and_agree_with_filefrag(self):
        with Folder() as d:
            path = fragmented(d)
            try:
                ext = frag.extents(path)
            except frag.NotSupported:
                self.skipTest(f"{FS} gives no extent map")
            self.assertEqual(sum(e[2] for e in ext), SIZE)
            self.assertEqual([e[0] for e in ext], sorted(e[0] for e in ext))
            if shutil.which("filefrag"):
                said = subprocess.run(["filefrag", path], capture_output=True, text=True).stdout
                self.assertEqual(len(ext), int(said.rsplit(":", 1)[1].split()[0]))
            self.assertEqual(os.listdir(d), ["fragmented.bin"])     # the spacer file is gone
            os.unlink(path)

    def test_pieces_joins_what_touches(self):
        self.assertEqual(frag.pieces([(0, 100, 10), (10, 110, 10), (20, 500, 10), (30, 510, 5)]), 2)
        self.assertEqual(frag.pieces([]), 0)
        self.assertEqual(frag.pieces([(0, 100, 10), (10, 90, 10)]), 2)

    def test_compressed_extents_laid_one_after_another_are_one_piece(self):
        # A real btrfs zstd file (filefrag -v, 2026-11-27): 128 KiB extents, each stored in less room than
        # its length, every next one starting inside the last one's span. One piece, not one per extent.
        E = frag.ENCODED | frag.SHARED
        k = 4096
        real = [(0, 30518786 * k, 32 * k, E), (32 * k, 30518791 * k, 32 * k, E), (64 * k, 30518795 * k, 32 * k, E),
                (96 * k, 30518799 * k, 32 * k, E), (128 * k, 30518802 * k, 32 * k, E)]
        self.assertEqual(frag.pieces(real), 1)
        far = real + [(160 * k, 99999999 * k, 32 * k, E)]
        self.assertEqual(frag.pieces(far), 2)
        backwards = [(0, 1000 * k, 32 * k, E), (32 * k, 900 * k, 32 * k, E)]
        self.assertEqual(frag.pieces(backwards), 2)
        plain = [(0, 1000 * k, 32 * k, 0), (32 * k, 1010 * k, 32 * k, 0)]       # not compressed: overlap = 2
        self.assertEqual(frag.pieces(plain), 2)
        self.assertTrue(frag.shared(real))
        self.assertFalse(frag.shared(plain))


class Defrag(unittest.TestCase):
    @unittest.skipUnless(CAN_DEFRAG, f"no defragmenter for {FS} here")
    def test_fewer_pieces_same_content(self):
        with Folder() as d:
            path = fragmented(d)
            before = frag.sha256(path)
            r = frag.defrag_file(path)
            self.assertEqual((r["exit"], frag.sha256(path)), (0, before))
            self.assertLessEqual(r["after"], r["before"])
            if FS == "btrfs":
                self.assertGreater(r["before"], 8)
                self.assertLess(r["after"], r["before"] // 4)
            os.unlink(path)

    @unittest.skipUnless(CAN_DEFRAG, f"no defragmenter for {FS} here")
    def test_changed_content_is_never_hidden(self):
        def bad_tool(cmd, **kw):         # damages the data the way a tool would: size and time stay
            st = os.stat(cmd[-1])
            with open(cmd[-1], "r+b") as f:
                f.write(b"oops")
            os.utime(cmd[-1], ns=(st.st_atime_ns, st.st_mtime_ns))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with Folder() as d:
            path = fragmented(d)
            with self.assertRaises(RuntimeError):
                frag.defrag_file(path, run=bad_tool)
            os.unlink(path)

    @unittest.skipUnless(CAN_DEFRAG, f"no defragmenter for {FS} here")
    def test_a_file_another_program_writes_meanwhile_is_left_alone_not_blamed_on_the_tool(self):
        def busy_program(cmd, **kw):     # e.g. a browser writing its database while the tool works
            with open(cmd[-1], "ab") as f:
                f.write(b"new data")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with Folder() as d:
            path = fragmented(d)
            with self.assertRaises(frag.InUse):
                frag.defrag_file(path, run=busy_program)
            os.unlink(path)

    def test_a_file_shared_with_a_snapshot_is_never_defragmented(self):
        ran = []
        with Folder() as d:
            path = os.path.join(d, "file.bin")
            with open(path, "wb") as f:
                f.write(b"x" * 4096)
            with mock.patch.object(frag, "fs_type", lambda p: "btrfs"), \
                    mock.patch.object(frag.shutil, "which", lambda t: "/usr/bin/" + t), \
                    mock.patch.object(frag, "extents", lambda p: [(0, 4096, 4096, frag.SHARED | 1)]):
                with self.assertRaises(frag.Shared):
                    frag.defrag_file(path, run=lambda *a, **k: ran.append(a))
            os.unlink(path)
        self.assertEqual(ran, [])

    def test_ntfs_is_never_walked(self):
        real = frag.fs_type
        frag.fs_type = lambda p: "ntfs3"
        self.addCleanup(setattr, frag, "fs_type", real)
        walked = []
        real_walk = frag.os.walk
        frag.os.walk = lambda *a, **k: walked.append(a) or iter(())
        self.addCleanup(setattr, frag.os, "walk", real_walk)
        with self.assertRaises(frag.NotSupported):
            frag.scan_folder("/mnt/windows")
        self.assertEqual(walked, [])

    def test_refuses_what_it_should_not_touch(self):
        real = frag.fs_type
        frag.fs_type = lambda p: "exfat"
        self.addCleanup(setattr, frag, "fs_type", real)
        ran = []
        with Folder() as d:
            path = os.path.join(d, "file.bin")
            with open(path, "wb") as f:
                f.write(b"x" * 4096)
            link = os.path.join(d, "link")
            os.symlink(path, link)
            for target in (path, link, d):
                with self.assertRaises(frag.NotSupported):
                    frag.defrag_file(target, run=lambda *a, **k: ran.append(a))
            os.unlink(link)
            os.unlink(path)
        self.assertEqual(ran, [])

    @unittest.skipUnless(CAN_DEFRAG, f"no defragmenter for {FS} here")
    def test_command_line(self):
        out = io.StringIO()
        with Folder() as d:
            path = fragmented(d)
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cli.main(["defrag", path, "--json"])
            events = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual(code, 0)
            self.assertEqual([p for e in events for p in contract.problems(e)], [])
            result = next(e for e in events if e["event"] == "defrag_result")
            self.assertEqual((result["path"], result["changed_content"]), (path, False))
            out2 = io.StringIO()
            with redirect_stdout(out2), redirect_stderr(io.StringIO()):
                code = cli.main(["defrag", os.path.join(d, "missing"), "--json"])
            self.assertEqual((code, json.loads(out2.getvalue().splitlines()[-1])["event"]), (3, "error"))
            os.unlink(path)



class DefragAll(unittest.TestCase):
    def files(self, n):
        return {"worst": [{"pieces": 10 * (n - i), "bytes": 1, "path": f"/d/f{i}"} for i in range(n)]}

    def test_every_file_in_turn_with_totals(self):
        seen, order = [], []

        def one(path, stop=None):
            order.append(path)
            if path == "/d/f1":
                raise frag.NotSupported("no")
            return {"before": 30, "after": 30 if path == "/d/f2" else 1, "ended": ""}
        r = frag.defrag_all("/d", seen.append, scan=lambda f, worst, stop: self.files(4), one=one)
        self.assertEqual(order, ["/d/f0", "/d/f1", "/d/f2", "/d/f3"])
        self.assertEqual(r, {"files": 4, "done": 3, "improved": 2, "skipped": 1, "pieces_before": 90,
                             "pieces_after": 32, "stopped": False, "shared": 0, "in_use": 0})
        self.assertEqual([(e["index"], e["total"], e["skipped"]) for e in seen],
                         [(1, 4, False), (2, 4, True), (3, 4, False), (4, 4, False)])
        from disky import contract
        self.assertEqual([p for e in seen for p in contract.problems(e)], [])

    def test_shared_files_never_reach_the_tool_and_in_use_files_are_counted_apart(self):
        found = {"worst": [{"pieces": 9, "bytes": 1, "path": "/d/snap", "shared": True},
                           {"pieces": 8, "bytes": 1, "path": "/d/busy", "shared": False},
                           {"pieces": 7, "bytes": 1, "path": "/d/odd", "shared": False},
                           {"pieces": 6, "bytes": 1, "path": "/d/ok", "shared": False}]}
        order = []

        def one(path, stop=None):
            order.append(path)
            if path == "/d/busy":
                raise frag.InUse("changed meanwhile")
            if path == "/d/odd":
                raise frag.NotSupported("no")
            return {"before": 6, "after": 1, "ended": ""}
        r = frag.defrag_all("/d", lambda e: None, scan=lambda f, worst, stop: found, one=one)
        self.assertEqual(order, ["/d/busy", "/d/odd", "/d/ok"])           # the shared file is never touched
        self.assertEqual((r["files"], r["done"], r["shared"], r["in_use"], r["skipped"]), (3, 1, 1, 1, 1))

    def test_stop_ends_it_after_the_file_in_hand(self):
        import threading
        stop, order = threading.Event(), []

        def one(path, stop=None):
            order.append(path)
            stop.set()
            return {"before": 5, "after": 1, "ended": ""}
        r = frag.defrag_all("/d", lambda e: None, stop, scan=lambda f, worst, stop: self.files(5), one=one)
        self.assertEqual((order, r["stopped"], r["done"]), (["/d/f0"], True, 1))

    def test_a_changed_file_ends_everything_at_once(self):
        order = []

        def one(path, stop=None):
            order.append(path)
            raise RuntimeError("The content of /d/f0 changed")
        with self.assertRaises(RuntimeError):
            frag.defrag_all("/d", lambda e: None, scan=lambda f, worst, stop: self.files(3), one=one)
        self.assertEqual(order, ["/d/f0"])                      # nothing more is touched

    def test_ntfs_is_refused_before_anything(self):
        with mock.patch.object(frag, "fs_type", lambda p: "ntfs3"):
            with self.assertRaises(frag.NotSupported):
                frag.defrag_all("/mnt/windows", lambda e: None, one=lambda *a, **k: self.fail("must not run"))

if __name__ == "__main__":
    unittest.main()
