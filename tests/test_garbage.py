# SPDX-License-Identifier: GPL-3.0-or-later
"""Garbage in: random and malformed data at everything that reads outside input.

A drive service, smartctl, the kernel's mount table, a plugged-in stick: none of them is ours, and one entry in
an unexpected shape must not blank a whole list or view, crash, hang, or (for the admin helper) let anything
through. The same seed every run, so a failure can be repeated. A longer run with other seeds found the faults
fixed on 2026-11-28; this keeps them fixed.
"""
import json
import os
import random
import tempfile
import time
import unittest

from disky import contract, exfat, helper, mounts, trim
from diskygui import deepcard, drives, smart, smartdb
from tests.test_exfat import build

WORDS = ["", "x", "-", "--yes", "/", "sda", "trim", "\0", "\n", "é", "9" * 30, "A" * 3000, "id", "name", "value",
         "worst", "thresh", "raw", "string", "flags", "prefailure", "table", "data", "type", "event", "passed",
         "ata_smart_attributes", "nvme_smart_health_information_log", "smart_status", "model_name", "serial_number",
         "temperature", "ata_smart_error_log", "ata_smart_self_test_log", "ata_device_statistics", "pages", "wwn",
         "org.freedesktop.UDisks2.Drive", "org.freedesktop.UDisks2.Block", "org.freedesktop.UDisks2.Filesystem",
         "Model", "Size", "RotationRate", "ConnectionBus", "Drive", "MountPoints", "IdType", "Optical",
         "/org/freedesktop/UDisks2/drives/A", "/org/freedesktop/UDisks2/block_devices/sda1"]


class Garbage(unittest.TestCase):
    def setUp(self):
        self.rnd = random.Random(20261128)

    def thing(self, depth=0):
        r, rnd = self.rnd.random(), self.rnd
        if depth > 4 or r < 0.30:
            return rnd.choice([None, True, False, 0, 1, -1, 2**63, -2**63, 2**70, 3.5, 1e308, rnd.randint(-999, 99999),
                               rnd.choice(WORDS), rnd.choice(WORDS) + str(rnd.randint(0, 9)), [], {}])
        if r < 0.65:
            return {rnd.choice(WORDS): self.thing(depth + 1) for _ in range(rnd.randint(0, 6))}
        return [self.thing(depth + 1) for _ in range(rnd.randint(0, 5))]

    def survive(self, fn, make, n, allowed=()):
        for _ in range(n):
            x = make()
            began = time.monotonic()
            try:
                fn(x)
            except allowed:
                pass
            except Exception as e:                      # the failure message shows the input that did it
                self.fail(f"{type(e).__name__}: {e} on {x!r}"[:400])
            self.assertLess(time.monotonic() - began, 3, repr(x)[:200])

    def test_events_from_the_engine(self):
        self.survive(contract.problems, self.thing, 600)

    def test_the_drive_list(self):
        rnd = self.rnd

        def shaped():
            objs = {}
            for k in range(rnd.randint(0, 5)):
                path = rnd.choice(["/org/freedesktop/UDisks2/drives/A", f"/org/freedesktop/UDisks2/block_devices/sda{k}",
                                   rnd.choice(WORDS)])
                objs[path] = {rnd.choice(WORDS[-13:-2] + WORDS[:8]): (
                    {rnd.choice(WORDS[-11:-2]): {"type": "v", "data": self.thing(3)} for _ in range(rnd.randint(0, 6))}
                    if rnd.random() < .8 else self.thing(3)) for _ in range(rnd.randint(0, 4))}
            return {"data": [objs]}
        self.survive(drives.parse, lambda: {"data": [self.thing()]} if rnd.random() < .7 else self.thing(), 500)
        self.survive(drives.parse, shaped, 900)
        self.survive(lambda x: self.assertIsInstance(drives.parse(x), list), shaped, 200)

    def test_one_unreadable_device_does_not_hide_the_others(self):
        P = "org.freedesktop.UDisks2."
        v = lambda x: {"type": "v", "data": x}  # noqa: E731
        good = {P + "Drive": {"Model": v("GOOD DRIVE"), "Size": v(1000), "ConnectionBus": v("usb"),
                              "RotationRate": v(0), "Optical": v(False)}}
        odd = {P + "Drive": {"Model": v(["not", "a", "name"]), "Size": v("big"), "RotationRate": v({}),
                             "MediaCompatibility": v(7)}}
        got = drives.parse({"data": [{"/org/freedesktop/UDisks2/drives/good": good,
                                      "/org/freedesktop/UDisks2/drives/odd": odd}]})
        self.assertEqual([d.model for d in got], ["GOOD DRIVE"])

    def test_smart_answers(self):
        rnd = self.rnd

        def smartctl():
            at = lambda: {k: self.thing(3) for k in rnd.sample(["id", "name", "value", "worst", "thresh", "flags", "raw"],  # noqa: E731
                                                                rnd.randint(0, 7))}
            return rnd.choice([self.thing(), {"ata_smart_attributes": {"table": [at() for _ in range(rnd.randint(0, 4))]},
                                              "smart_status": self.thing(2)},
                               {"ata_smart_attributes": self.thing(2)},
                               {"nvme_smart_health_information_log": self.thing(2), "smart_status": self.thing(2)}])
        nvme_keys = ["avail_spare", "spare_thresh", "percent_used", "media_errors", "power_cycles", "temp_sensors",
                     "wctemp", "total_data_read", "ctrl_busy_time", "power_on_hours", "warning_temp_time"]
        self.survive(smart.ata_rows, lambda: [[self.thing(4) for _ in range(rnd.choice([9, 9, 9, 8, 10, 0]))]
                                              for _ in range(rnd.randint(0, 4))], 600)
        self.survive(smart.nvme_rows, lambda: self.thing() if rnd.random() < .2
                     else {rnd.choice(nvme_keys): self.thing(3) for _ in range(6)}, 900)
        self.survive(lambda x: (smart.glance(smart.smartctl_rows(x)), smart.brief(smart.smartctl_rows(x))), smartctl, 900)

    def test_one_unreadable_attribute_does_not_cost_the_rest(self):
        good = [9, "power-on-hours", 50, 100, 100, 0, 3_600_000 * 5, 2, {}]
        rows = smart.ata_rows([["nine", None], good, [5, "reallocated-sector-count", "x", 1, 1, 1, 1, 3, {}]])
        self.assertEqual([(r.name, r.value) for r in rows], [("Hours switched on", "5 hours")])
        table = [{"id": "five"}, None, {"id": 9, "name": "Power_On_Hours", "value": 99, "worst": 99, "thresh": 0,
                                        "raw": {"value": 7, "string": "7"}}, {"id": 194, "name": 194, "raw": 5}]
        rows = smart.smartctl_rows({"smart_status": "fine", "ata_smart_attributes": {"table": table}})
        self.assertEqual([(r.name, r.value) for r in rows], [("Hours switched on", "7 hours")])
        self.assertEqual([r.name for r in smart.nvme_rows({"percent_used": 3, "temp_sensors": 311, "media_errors": "no"})],
                         ["Life used"])

    def test_the_deep_scan(self):
        self.survive(deepcard.identity, self.thing, 700)
        self.survive(deepcard.history, self.thing, 700)
        self.survive(lambda x: deepcard.report("drive", x), self.thing, 500)
        self.assertEqual(deepcard.identity(["not", "a", "record"])[0][0], "Maker")      # an empty record, not a crash

    def test_the_admin_helper_lets_nothing_else_through(self):
        rnd = self.rnd

        def ask(x):
            try:
                argv = helper.request(x if isinstance(x, str) else json.dumps(x))
            except helper.Refused:
                return
            self.assertIn(argv[0], helper.ALLOWED)
            self.assertFalse(argv[1].startswith("-"))
            self.assertEqual(argv[2], "--json")
            rest, i = argv[3:], 0
            while i < len(rest):
                self.assertIn(rest[i], helper.ALLOWED[argv[0]], argv)
                i += 1 if helper.ALLOWED[argv[0]][rest[i]] is None else 2
        self.survive(ask, self.thing, 800)
        self.survive(ask, lambda: {"cmd": rnd.choice(list(helper.ALLOWED) + WORDS), "target": self.thing(4),
                                   "options": self.thing(3)}, 4000)

    def test_mount_table_timer_and_drive_list_files(self):
        rnd = self.rnd

        def mountinfo():
            fd, path = tempfile.mkstemp()
            self.addCleanup(lambda p=path: os.path.exists(p) and os.remove(p))
            lines = []
            for _ in range(rnd.randint(0, 6)):
                parts = [rnd.choice(["36", "x", ""]), "1", "0:1", "/", rnd.choice(["/", "/mnt/a\\\\040b", "", "/x"])]
                lines.append(" ".join(parts[:rnd.randint(0, 5)] + ["rw"] * rnd.randint(0, 2))
                             + rnd.choice([" - ", " -", " ", " - - "])
                             + " ".join(rnd.sample(["ext4", "/dev/a", "rw", "discard", ""], rnd.randint(0, 4))))
            os.write(fd, ("\\n".join(lines) + rnd.choice(["", "\\n", "\\n\\n"])).encode())
            os.close(fd)
            return path
        self.survive(lambda p: (mounts.holding("/mnt/a b/c", p), mounts.below("/mnt", p), trim.auto_discard("/x", p)),
                     mountinfo, 400)

        class Answer:
            def __init__(self, text, code):
                self.stdout, self.stderr, self.returncode = text, "", code
        texts = ["", "x", "ActiveState=active\\nLastTriggerUSec=Sun 2026-11-22 00:00:00 UTC\\nNextElapseUSecRealtime=n/a\\n",
                 "=\\n=\\n", "A=" + "9" * 400, "LastTriggerUSec=garbage\\nActiveState=\\n", "\\x00\\xff"]
        self.survive(lambda x: trim.weekly(run=lambda *a, **k: x),
                     lambda: Answer(rnd.choice(texts) + str(self.thing(4))[:80], rnd.choice([0, 1, 3])), 500)
        bits = ['{', '}', '"', ',', '//', '/*', '*/', '\\n', '-v 5,raw48,Name ', '\\\\', 'x', ' ']
        self.survive(lambda x: [smartdb.presets(" ".join(f)) for f in smartdb.entries(x)],
                     lambda: "".join(rnd.choice(bits) for _ in range(rnd.randint(0, 200))), 500)

    def test_a_damaged_exfat_volume_is_refused_or_read_never_a_crash_or_a_hang(self):
        rnd, base = self.rnd, build()

        def image():
            b = bytearray(base)
            kind = rnd.random()
            if kind < 0.15:
                return bytes(rnd.getrandbits(8) for _ in range(rnd.choice([0, 1, 100, 512, 4096])))
            for _ in range(rnd.choice([1, 1, 2, 4, 16, 64])):
                pos = rnd.randrange(len(b)) if rnd.random() < .5 else rnd.randrange(min(len(b), 2048))
                b[pos] = rnd.choice([0, 0xff, 0x80, 1, rnd.getrandbits(8)])
            return bytes(b[:rnd.randrange(len(b))] if kind > 0.9 else b)

        def scan(data):
            fd, path = tempfile.mkstemp()
            try:
                os.write(fd, data)
                os.close(fd)
                list(exfat.ranges(exfat.scan(path)["state"]))
            finally:
                os.remove(path)
        self.survive(scan, image, 150, (exfat.ExfatError,))


if __name__ == "__main__":
    unittest.main()
