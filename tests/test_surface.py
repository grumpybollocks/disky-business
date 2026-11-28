# SPDX-License-Identifier: GPL-3.0-or-later
"""The surface scan's engine, with a pretend drive and a pretend clock: nothing real is read."""
import threading
import unittest

from disky import contract, helper, surface

MIB = surface.BLOCK


class Drive:
    """A pretend drive: `times[i]` seconds for block i (default 0.01), blocks in `bad` raise an error."""

    def __init__(self, clock, blocks=100, tail=0, times=None, bad=(), short=()):
        self.clock, self.size, self.times, self.bad, self.short = clock, blocks * MIB + tail, times or {}, bad, short
        self.reads, self.closed, self.direct = [], False, True

    def read(self, offset):
        self.reads.append(offset)
        i = offset // MIB
        self.clock.now += self.times.get(i, 0.01)
        if i in self.bad:
            raise OSError(5, "Input/output error")
        return 512 if i in self.short else min(MIB, self.size - offset)

    def close(self):
        self.closed = True


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def run(stop=None, device="sdz", fs=("exfat",), root=True, block=True, **drive):
    clock, events = Clock(), []
    d = Drive(clock, **drive)
    result = surface.scan(device, events.append, stop or threading.Event(), reader=lambda path: d, clock=clock,
                          is_root=root, is_block=lambda p: block, fs_of=lambda p: set(fs))
    return result, events, d


class Surface(unittest.TestCase):
    def test_a_sound_drive_is_read_from_first_block_to_last(self):
        result, events, d = run(blocks=100, tail=4096)
        self.assertEqual(d.reads, [i * MIB for i in range(101)])            # every block once, in order, the tail too
        self.assertEqual(result["blocks"], {"fast": 101, "slow": 0, "very_slow": 0, "unreadable": 0})
        self.assertEqual((result["read_bytes"], result["total_bytes"], result["stopped"]),
                         (100 * MIB + 4096, 100 * MIB + 4096, False))
        self.assertTrue(d.closed)
        self.assertEqual([contract.problems(e) for e in events], [[]] * len(events))
        self.assertEqual((events[0]["event"], events[-1]["event"]), ("surface_start", "surface_result"))
        covered = sum(e["count"] for e in events if e["event"] == "surface_blocks")
        self.assertEqual(covered, 101)                                      # the map gets every block

    def test_slow_very_slow_and_unreadable_blocks_are_named_and_the_scan_goes_on(self):
        result, events, d = run(blocks=300, times={150: 0.2, 200: 0.7, 250: 3.0}, bad={250}, short={260})
        self.assertEqual(result["blocks"], {"fast": 296, "slow": 1, "very_slow": 1, "unreadable": 2})
        self.assertEqual(result["first_unreadable_offset"], 250 * MIB)
        self.assertEqual(result["slowest_ms"], 700.0)                       # of the blocks that could be read
        self.assertEqual(len(d.reads), 300)                                 # an error does not end the scan
        marks = {(e["first"], e["count"], e["state"]) for e in events
                 if e["event"] == "surface_blocks" and e["state"] != "fast"}
        self.assertEqual(marks, {(150, 1, "slow"), (200, 1, "very_slow"), (250, 1, "unreadable"),
                                 (260, 1, "unreadable")})

    def test_slow_is_measured_against_the_drives_own_pace(self):
        self.assertEqual(surface.grade(0.2, None), "fast")                  # nothing to compare with yet
        self.assertEqual(surface.grade(0.2, 0.01), "slow")
        self.assertEqual(surface.grade(0.2, 0.1), "fast")                   # a slow drive at its usual pace
        self.assertEqual(surface.grade(0.03, 0.001), "fast")                # quick in absolute terms
        self.assertEqual(surface.grade(0.5, 0.4), "very_slow")

    def test_stop_ends_it_and_says_so(self):
        stop = threading.Event()
        clock, events = Clock(), []
        d = Drive(clock, blocks=100)
        real = d.read
        d.read = lambda off: (stop.set() if off == 9 * MIB else None, real(off))[1]
        result = surface.scan("sdz", events.append, stop, reader=lambda p: d, clock=clock, is_root=True,
                              is_block=lambda p: True, fs_of=lambda p: set())
        self.assertEqual((len(d.reads), result["stopped"], d.closed), (10, True, True))
        self.assertEqual(result["blocks"]["fast"], 10)

    def test_refusals_come_before_anything_is_read(self):
        for kw, error, word in (({"root": False}, PermissionError, "admin"),
                                ({"block": False}, ValueError, "not a drive"),
                                ({"fs": ("vfat", "ntfs")}, ValueError, "NTFS"),
                                ({"device": "sdz1"}, ValueError, "Not a drive name"),
                                ({"device": "../etc/passwd"}, ValueError, "Not a drive name")):
            opened = []
            with self.assertRaises(error) as caught:
                surface.scan(kw.get("device", "sdz"), lambda e: None, threading.Event(),
                             reader=opened.append, is_root=kw.get("root", True),
                             is_block=lambda p: kw.get("block", True), fs_of=lambda p: set(kw.get("fs", ())))
            self.assertIn(word, str(caught.exception))
            self.assertEqual(opened, [])

    def test_the_real_reader_only_ever_opens_for_reading(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"x" * (MIB + 4096))
            f.flush()
            before = os.stat(f.name).st_mtime_ns
            r = surface.Reader(f.name)
            try:
                self.assertEqual(r.size, MIB + 4096)
                self.assertEqual((r.read(0), r.read(MIB)), (MIB, 4096))
                self.assertEqual(bytes(r.buffer[:4]), b"xxxx")
                with self.assertRaises(OSError):
                    os.write(r.fd, b"no")                                   # the handle cannot write
            finally:
                r.close()
            self.assertEqual(os.stat(f.name).st_mtime_ns, before)

    def test_the_admin_helper_takes_it_with_no_options(self):
        self.assertEqual(helper.request('{"cmd": "surface", "target": "sdz"}'), ["surface", "sdz", "--json"])
        with self.assertRaises(helper.Refused):
            helper.request('{"cmd": "surface", "target": "sdz", "options": ["--yes"]}')


if __name__ == "__main__":
    unittest.main()
