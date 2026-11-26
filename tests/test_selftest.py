# SPDX-License-Identifier: GPL-3.0-or-later
"""SMART self-tests against a pretend drive service: what is started, followed, aborted and refused."""
import io
import json
import subprocess
import threading
import unittest
from contextlib import redirect_stdout
from unittest import mock

from disky import cli, contract, selftest

ATA = "org.freedesktop.UDisks2.Drive.Ata"


class Service:
    """Answers busctl. `course` is the list of (status, percent remaining) the drive reports, one per update."""

    def __init__(self, course, iface=ATA, start_error=""):
        self.course, self.iface, self.start_error = list(course), iface, start_error
        self.now = ("success", 0)
        self.calls = []

    def __call__(self, cmd, **kw):
        ok = lambda data=None: subprocess.CompletedProcess(  # noqa: E731
            cmd, 0, "" if data is None else json.dumps({"type": "x", "data": data}), "")
        if "get-property" in cmd:
            prop, iface = cmd[-1], cmd[-2]
            if prop == "Drive":
                return ok("/org/freedesktop/UDisks2/drives/EXAMPLE")
            if iface != self.iface:
                return subprocess.CompletedProcess(cmd, 1, "", "No such interface")
            return ok(self.now[0] if prop == "SmartSelftestStatus" else self.now[1])
        method = cmd[cmd.index("call") + 4]
        self.calls.append((method, cmd[cmd.index("call") + 6] if method == "SmartSelftestStart" else None))
        if method == "SmartSelftestStart" and self.start_error:
            return subprocess.CompletedProcess(cmd, 1, "", self.start_error)
        if method == "SmartUpdate" and self.course:
            self.now = self.course.pop(0)
        return ok()


def go(service, kind="short", stop=None):
    seen = []
    result = selftest.run_test("sdz", kind, seen.append, stop, run=service, sleep=lambda s: None)
    return result, seen


class Rules(unittest.TestCase):
    def test_a_test_is_started_followed_and_judged(self):
        s = Service([("inprogress", 90), ("inprogress", 90), ("inprogress", 40), ("success", 0)])
        result, seen = go(s)
        self.assertEqual(result, (True, "success"))
        self.assertEqual(s.calls[0], ("SmartSelftestStart", "short"))
        self.assertEqual([e["percent"] for e in seen], [0, 10, 60])            # each step said once
        self.assertEqual([p for e in seen for p in contract.problems(e)], [])
        s = Service([("error_read", 30)], iface="org.freedesktop.UDisks2.NVMe.Controller")
        result, _ = go(s, "long")
        self.assertEqual((result, s.calls[0]), ((False, "error_read"), ("SmartSelftestStart", "extended")))

    def test_stop_aborts_the_test_on_the_drive(self):
        stop = threading.Event()
        s = Service([("inprogress", 90)] * 50)
        stop.set()
        result, _ = go(s, stop=stop)
        self.assertEqual(result, (None, "aborted"))
        self.assertEqual([m for m, _ in s.calls], ["SmartSelftestStart", "SmartSelftestAbort"])

    def test_refusals_start_nothing(self):
        for device in ("sdz1", "../x", "loop0"):
            s = Service([])
            with self.assertRaises(selftest.Refused):
                selftest.run_test(device, "short", lambda e: None, run=s)
            self.assertEqual(s.calls, [])
        s = Service([], iface="something.else")
        with self.assertRaises(selftest.Refused) as e:
            go(s)
        self.assertEqual((s.calls, "offers no self-test" in str(e.exception)), ([], True))
        s = Service([], start_error="org.freedesktop.UDisks2.Error.NotAuthorizedDismissed")
        with self.assertRaises(selftest.Refused) as e:
            go(s)
        self.assertIn("password was not given", str(e.exception))

    def test_command_line(self):
        for course, code, word in (([("success", 0)], 0, "Passed"), ([("error_servo", 10)], 1, "FAILED")):
            out = io.StringIO()
            s = Service(course)
            with mock.patch.object(selftest.run_test, "__defaults__", (None, s, lambda x: None, 5.0)), \
                    mock.patch.object(selftest.find, "__defaults__", (s,)), redirect_stdout(out):
                got = cli.main(["selftest", "sdz", "--json"])
            events = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual([p for e in events for p in contract.problems(e)], [])
            self.assertEqual(got, code)
            self.assertIn(word, events[-2]["message"])


if __name__ == "__main__":
    unittest.main()
