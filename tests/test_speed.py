# SPDX-License-Identifier: GPL-3.0-or-later
"""The speed test: what it reports, and that its temporary file never stays behind."""
import errno
import io
import json
import os
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout

from disky import cli, contract, speed
from disky.engine import ExistingFiles
from tests.util import MIB, Folder

SIZE = 64 * MIB     # two slices


def collect(folder, **kw):
    events = []
    results = speed.run(folder, events.append, size=kw.pop("size", SIZE), seconds=kw.pop("seconds", 0.2), **kw)
    return events, results


class Speed(unittest.TestCase):
    def test_four_results_that_fit_the_contract_and_no_file_left(self):
        with Folder() as d:
            events, results = collect(d)
            self.assertEqual(os.listdir(d), [])
        self.assertEqual([p for e in events for p in contract.problems(e)], [])
        self.assertEqual(list(results), list(speed.TESTS))
        self.assertEqual([e["test"] for e in events if e["event"] == "speed_phase"], list(speed.TESTS))
        for test, r in results.items():
            self.assertGreater(r["mib_per_s"], 0, test)
        self.assertIsNone(results["seq_write"]["iops"])
        self.assertGreater(results["rand_read"]["iops"], 0)
        self.assertIsInstance(results["rand_write"]["direct"], bool)

    def test_samples_cover_the_whole_file_in_order(self):
        with Folder() as d:
            events, _ = collect(d)
        for test in ("seq_write", "seq_read"):
            offs = [e["offset_bytes"] for e in events if e["event"] == "speed_sample" and e["test"] == test]
            self.assertEqual(offs, list(range(0, SIZE, speed.SLICE)), test)

    def test_the_file_really_is_written_and_read(self):
        """Speed = bytes over time; with a clock that ticks 1 s per call the numbers are known."""
        ticks = iter(range(10_000))
        with Folder() as d:
            seen = {}
            real_read = os.read

            def spy_read(fd, n):
                data = real_read(fd, n)
                seen["read"] = seen.get("read", 0) + len(data)
                return data
            os.read = spy_read
            try:
                events, results = collect(d, clock=lambda: next(ticks), seconds=3)
            finally:
                os.read = real_read
        self.assertEqual(seen["read"], SIZE)
        samples = [e["mib_per_s"] for e in events if e["event"] == "speed_sample" and e["test"] == "seq_write"]
        self.assertEqual(samples, [32.0, 32.0])          # 32 MiB per slice, 1 "second" each

    def test_stop_ends_early_and_removes_the_file(self):
        stop = threading.Event()
        events = []

        def emit(ev):
            events.append(ev)
            if ev["event"] == "speed_sample":
                stop.set()
        with Folder() as d:
            results = speed.run(d, emit, size=SIZE, seconds=0.2, stop=stop)
            self.assertEqual(os.listdir(d), [])
        self.assertEqual(results, {})
        self.assertEqual(len([e for e in events if e["event"] == "speed_sample"]), 1)

    def test_an_error_half_way_still_removes_the_file(self):
        def emit(ev):
            if ev["event"] == "speed_phase" and ev["test"] == "seq_read":
                raise RuntimeError("the window went away")
        with Folder() as d:
            with self.assertRaises(RuntimeError):
                speed.run(d, emit, size=SIZE, seconds=0.2)
            self.assertEqual(os.listdir(d), [])

    def test_a_file_already_there_is_never_touched(self):
        with Folder() as d:
            path = os.path.join(d, speed.NAME)
            with open(path, "w") as f:
                f.write("someone's data")
            with self.assertRaises(ExistingFiles):
                collect(d)
            with open(path) as f:
                self.assertEqual(f.read(), "someone's data")
            os.unlink(path)

    def pretend_free(self, *answers):
        """Make the engine believe the drive has this much room (one answer per question, the last one repeats)."""
        left = list(answers)
        real = speed._free
        speed._free = lambda folder: left.pop(0) if len(left) > 1 else left[0]
        self.addCleanup(setattr, speed, "_free", real)

    def test_too_little_space_writes_nothing(self):
        self.pretend_free(SIZE - 1)
        with Folder() as d:
            with self.assertRaises(OSError) as ctx:
                collect(d)
            self.assertEqual(ctx.exception.errno, errno.ENOSPC)
            self.assertEqual(os.listdir(d), [])

    def test_room_to_keep_free_is_respected(self):
        self.pretend_free(SIZE + 10 * MIB)
        with Folder() as d:
            with self.assertRaises(OSError):
                collect(d, keep_free=11 * MIB)
            self.assertEqual(os.listdir(d), [])

    def test_space_running_out_half_way_stops_the_writing(self):
        self.pretend_free(SIZE, SIZE, 1 * MIB)      # fine at the start and for slice one, then nearly full
        with Folder() as d:
            events = []
            with self.assertRaises(OSError):
                speed.run(d, events.append, size=SIZE, seconds=0.2)
            self.assertEqual(os.listdir(d), [])
        self.assertEqual(len([e for e in events if e["event"] == "speed_sample"]), 1)


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_json_run(self):
        with Folder() as d:
            code, out = self.run_cli("speed", d, "--size", "64M", "--seconds", "0.2", "--json")
        events = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual([p for e in events for p in contract.problems(e)], [])
        self.assertEqual((events[0]["mode"], events[-1]["event"]), ("speed", "done"))
        self.assertEqual(list(events[-1]["results"]), list(speed.TESTS))

    def test_plain_words(self):
        with Folder() as d:
            code, out = self.run_cli("speed", d, "--size", "64M", "--seconds", "0.2")
        self.assertEqual(code, 0)
        self.assertEqual([line.split(":")[0] for line in out.splitlines()],
                         ["Writing in one go", "Reading in one go", "Small reads, random places",
                          "Small writes, random places"])

    def test_leftover_is_reported_and_clean_removes_it(self):
        with Folder() as d:
            path = os.path.join(d, speed.NAME)
            open(path, "w").close()
            code, out = self.run_cli("speed", d, "--size", "64M", "--json")
            self.assertEqual((code, json.loads(out.splitlines()[-1])["code"]), (3, "existing_files"))
            self.assertTrue(os.path.exists(path))
            self.run_cli("clean", d, "--json")
            self.assertTrue(os.path.exists(path))            # listing alone removes nothing
            self.run_cli("clean", d, "--yes", "--json")
            self.assertEqual(os.listdir(d), [])

    def test_contract_rejects_an_unknown_test_name(self):
        self.assertEqual(contract.problems({"event": "speed_sample", "test": "seq_write", "offset_bytes": 0,
                                            "mib_per_s": 1.0}), [])
        self.assertTrue(contract.problems({"event": "speed_sample", "test": "warp", "offset_bytes": 0,
                                           "mib_per_s": 1.0}))


if __name__ == "__main__":
    unittest.main()
