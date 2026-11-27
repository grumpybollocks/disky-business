# SPDX-License-Identifier: GPL-3.0-or-later
"""Each damage case is a function that says whether the engine noticed it.

Every case runs twice: on the real engine it must be noticed, and on an engine
broken on purpose it must NOT be noticed. The second run proves the test can fail.
"""
import os
import threading
import unittest
from unittest import mock

import numpy as np

from disky import engine, pattern
from tests.util import FILE, MIB, SIZE, Folder, fs_type, patch_file, read_at

S = 512


def run(folder, damage, **verify_args):
    events = []
    written = engine.write(folder, events.append, size=SIZE, file_bytes=FILE)
    damage(folder)
    verify_args.setdefault("file_bytes", FILE)
    summary = engine.verify(folder, events.append, expect_bytes=written, **verify_args)
    return summary, [e for e in events if e["event"] == "blocks" and e["phase"] == "verify"]


def f(folder, n):
    return os.path.join(folder, f"{n}.h2w")


# --- damage cases: (damage function, what the summary and block events must show) ---

def flipped_bit(folder):
    data = bytearray(read_at(f(folder, 2), MIB + 3 * S + 40, 1))
    data[0] ^= 0x04
    patch_file(f(folder, 2), MIB + 3 * S + 40, bytes(data))


def noticed_flipped_bit(summary, blocks):
    hit = [b for b in blocks if b["state"] == "changed"]
    return (summary["verdict"] == "bad" and summary["sectors"]["changed"] == 1
            and len(hit) == 1 and hit[0]["first"] == 5
            and summary["first_bad_offset"] == FILE + MIB + 3 * S)


def scrambled_sector(folder):
    patch_file(f(folder, 1), 2 * MIB, b"\xa5" * S)


def noticed_scrambled(summary, blocks):
    hit = [b for b in blocks if b["state"] == "bad"]
    return (summary["verdict"] == "bad" and summary["sectors"]["bad"] == 1
            and len(hit) == 1 and hit[0]["first"] == 2 and summary["first_bad_offset"] == 2 * MIB)


def swapped_sectors(folder):
    a, b = read_at(f(folder, 3), 0, S), read_at(f(folder, 3), 10 * S, S)
    patch_file(f(folder, 3), 0, b)
    patch_file(f(folder, 3), 10 * S, a)


def noticed_swapped(summary, blocks):
    hit = [b for b in blocks if b["state"] == "overwritten"]
    return (summary["verdict"] == "bad" and summary["sectors"]["overwritten"] == 2
            and len(hit) == 1 and hit[0]["first"] == 8 and hit[0]["sectors"]["overwritten"] == 2)


def wrap_around(folder):
    # A drive that lies about its size stores late data over early data.
    patch_file(f(folder, 1), MIB, read_at(f(folder, 3), MIB, MIB))


def noticed_wrap(summary, blocks):
    hit = [b for b in blocks if b["state"] == "overwritten"]
    return (summary["verdict"] == "bad" and summary["sectors"]["overwritten"] == MIB // S
            and len(hit) == 1 and hit[0]["first"] == 1)


def cut_middle_file(folder):
    os.truncate(f(folder, 2), FILE - MIB)


def cut_last_file(folder):
    os.truncate(f(folder, 3), FILE - 2 * MIB)


def noticed_missing(amount):
    return lambda summary, blocks: summary["verdict"] == "bad" and summary["missing_bytes"] == amount


def blind(buf, offset):
    """A broken checker: calls everything good."""
    return np.zeros(len(buf) // S, np.uint8)


class Damage(unittest.TestCase):
    CASES = [
        ("flipped bit", flipped_bit, noticed_flipped_bit),
        ("scrambled sector", scrambled_sector, noticed_scrambled),
        ("swapped sectors", swapped_sectors, noticed_swapped),
        ("wrap-around", wrap_around, noticed_wrap),
    ]

    def test_real_engine_notices_each_damage(self):
        for name, damage, noticed in self.CASES:
            with self.subTest(name), Folder() as folder:
                self.assertTrue(noticed(*run(folder, damage)))

    def test_broken_engine_misses_each_damage(self):
        for name, damage, noticed in self.CASES:
            with self.subTest(name), Folder() as folder, mock.patch.object(pattern, "classify", blind):
                self.assertFalse(noticed(*run(folder, damage)))

    def test_cut_short_middle_file(self):
        with Folder() as folder:
            self.assertTrue(noticed_missing(MIB)(*run(folder, cut_middle_file)))

    def test_cut_short_last_file(self):
        with Folder() as folder:
            self.assertTrue(noticed_missing(2 * MIB)(*run(folder, cut_last_file)))

    def test_every_loss_says_where_it_starts(self):
        # Found on a real drive folder (2026-11-27): a last file torn mid-sector, or data that simply ends early,
        # gave "bad" with no place, and the command line's verdict crashed on it.
        def torn(folder):
            os.truncate(f(folder, 3), os.path.getsize(f(folder, 3)) - 300)
        for damage in (torn, cut_last_file):
            with self.subTest(damage.__name__), Folder() as folder:
                summary, _ = run(folder, damage)
                self.assertEqual(summary["verdict"], "bad")
                self.assertIsNotNone(summary["first_bad_offset"])
                self.assertLessEqual(summary["first_bad_offset"], 2 * FILE + os.path.getsize(f(folder, 3)))
                from disky import cli
                self.assertIn("Drive is BAD", cli.Printer.verdict(summary))
                self.assertIn("Drive is BAD", cli.Printer.verdict(dict(summary, first_bad_offset=None)))

    def test_engine_that_ignores_sizes_misses_cut_files(self):
        # Broken on purpose: no expected size and no file size to compare with.
        for damage, amount in ((cut_middle_file, MIB), (cut_last_file, 2 * MIB)):
            with self.subTest(damage.__name__), Folder() as folder:
                events = []
                engine.write(folder, events.append, size=SIZE, file_bytes=FILE)
                damage(folder)
                summary = engine.verify(folder, events.append, expect_bytes=None, file_bytes=0)
                self.assertFalse(noticed_missing(amount)(summary, []))

    def test_undamaged_run_has_no_bad_blocks(self):
        with Folder() as folder:
            summary, blocks = run(folder, lambda _: None)
            self.assertEqual(summary["sectors"], {"good": SIZE // S, "changed": 0, "bad": 0, "overwritten": 0})
            self.assertEqual(summary["missing_bytes"], 0)
            self.assertEqual({b["state"] for b in blocks}, {"good"})
            self.assertEqual(sum(b["count"] for b in blocks), SIZE // MIB)


class ReadProof(unittest.TestCase):
    def test_fresh_read_comes_from_the_drive(self):
        with Folder() as folder:
            if fs_type(folder) == "tmpfs" or engine._read_bytes_counter() is None:
                self.skipTest("needs a real file system and /proc/self/io")
            summary, _ = run(folder, lambda _: None)
            self.assertTrue(summary["read_from_drive"], summary)
            self.assertEqual(summary["verdict"], "good")

    def test_read_from_memory_is_not_verified(self):
        with Folder() as folder:
            if engine._read_bytes_counter() is None:
                self.skipTest("needs /proc/self/io")

            def warm(folder):
                for n in (1, 2, 3):
                    read_at(f(folder, n), 0, FILE)
            summary, _ = run(folder, warm, drop_cache=False)
            self.assertFalse(summary["read_from_drive"], summary)
            self.assertEqual(summary["verdict"], "not_verified")


class Writing(unittest.TestCase):
    def test_files_match_f3_layout(self):
        with Folder() as folder:
            engine.write(folder, lambda e: None, size=SIZE, file_bytes=FILE)
            self.assertEqual(sorted(os.listdir(folder)), ["1.h2w", "2.h2w", "3.h2w"])
            self.assertEqual(read_at(f(folder, 2), 0, 4096), pattern.make(FILE, 4096))

    def test_existing_test_files_are_never_touched(self):
        with Folder() as folder:
            with open(f(folder, 1), "wb") as fh:
                fh.write(b"someone else's file")
            with self.assertRaises(engine.ExistingFiles):
                engine.write(folder, lambda e: None, size=SIZE, file_bytes=FILE)
            self.assertEqual(read_at(f(folder, 1), 0, 100), b"someone else's file")
            self.assertEqual(os.listdir(folder), ["1.h2w"])

    def test_other_files_are_left_alone(self):
        with Folder() as folder:
            with open(os.path.join(folder, "holiday.jpg"), "wb") as fh:
                fh.write(b"photo")
            engine.write(folder, lambda e: None, size=MIB, file_bytes=FILE)
            self.assertEqual(read_at(os.path.join(folder, "holiday.jpg"), 0, 10), b"photo")

    def test_reserve_is_respected(self):
        with Folder() as folder:
            vfs = os.statvfs(folder)
            free = vfs.f_bavail * vfs.f_frsize
            written = engine.write(folder, lambda e: None, reserve=free - 2 * MIB, file_bytes=FILE)
            self.assertLessEqual(written, 2 * MIB)

    def test_reserve_is_checked_again_while_writing(self):
        with Folder() as folder:
            seen = []
            calls = {"n": 0}

            def shrinking(_folder):              # another program fills the drive after the first check
                calls["n"] += 1
                return (1 << 40) if calls["n"] == 1 else 0
            with mock.patch.object(engine, "_free", shrinking):
                written = engine.write(folder, seen.append, size=8 * FILE, reserve=1, file_bytes=FILE)
            self.assertLess(written, 8 * FILE)
            self.assertEqual([e["code"] for e in seen if e["event"] == "warning"], ["reserve_reached"])

    def test_reserve_larger_than_free_writes_nothing(self):
        with Folder() as folder:
            written = engine.write(folder, lambda e: None, reserve=1 << 60, file_bytes=FILE)
            self.assertEqual(written, 0)
            self.assertEqual(os.listdir(folder), [])

    def test_stop_ends_cleanly(self):
        with Folder() as folder:
            stop = threading.Event()
            seen = []

            def emit(ev):
                seen.append(ev)
                if ev["event"] == "blocks":
                    stop.set()
            written = engine.write(folder, emit, size=10 * FILE, file_bytes=FILE, stop=stop)
            self.assertLess(written, 10 * FILE)
            self.assertEqual(written % S, 0)
            self.assertEqual(written, sum(os.path.getsize(os.path.join(folder, n)) for n in os.listdir(folder)))
            summary = engine.verify(folder, lambda e: None, file_bytes=FILE)
            self.assertEqual(summary["sectors"]["good"], written // S)


if __name__ == "__main__":
    unittest.main()
