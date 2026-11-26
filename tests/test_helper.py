# SPDX-License-Identifier: GPL-3.0-or-later
"""The admin helper: what it runs, and everything it must refuse while it holds admin rights."""
import io
import json
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from disky import cli, contract, helper


def ask(cmd, target="/x", options=None):
    return json.dumps({"cmd": cmd, "target": target, "options": options or []})


class Requests(unittest.TestCase):
    def test_the_jobs_it_runs(self):
        self.assertEqual(helper.request(ask("trim", "/mnt/data")), ["trim", "/mnt/data", "--json"])
        self.assertEqual(helper.request(ask("selftest", "sdb", ["--kind", "long"])),
                         ["selftest", "sdb", "--json", "--kind", "long"])
        self.assertEqual(helper.request(ask("fsrepair", "sdb1", ["--yes"])), ["fsrepair", "sdb1", "--json", "--yes"])
        self.assertEqual(sorted(helper.ALLOWED), ["fragscan", "fscheck", "fsrepair", "selftest", "smart", "trim"])

    def test_everything_else_is_refused(self):
        bad = [ask(c) for c in ("test", "write", "clean", "speed", "defrag", "verify", "helper", "", "rm", None)]
        bad += [ask("trim", t) for t in ("-rf", "--json", "", "a\nb", "a\0b", 5, None, ["/x"])]
        bad += [ask("trim", "/x", o) for o in (["--size", "1G"], ["--yes"], ["--kind", "short"], "--yes", [5])]
        bad += [ask("selftest", "sdb", o) for o in (["--kind"], ["--kind", "extended"], ["--kind", "--yes"])]
        bad += ["not json", "[1, 2]", '"trim"', ask("trim", "/" + "x" * 9000)]
        for line in bad:
            with self.assertRaises(helper.Refused, msg=line[:60]):
                helper.request(line)


class Serving(unittest.TestCase):
    def serve(self, lines, job, idle=5):
        seen = []
        code = helper.serve(seen.append, job, lines=lines, idle=idle)
        self.assertEqual([p for e in seen for p in contract.problems(e)], [])
        return code, seen

    def test_jobs_one_after_another_and_a_refusal_does_not_end_the_session(self):
        ran = []
        code, seen = self.serve([ask("trim", "/a"), ask("clean", "/a"), ask("smart", "sdb"), ""],
                                lambda argv, stop: ran.append(argv) or len(ran))
        self.assertEqual(ran, [["trim", "/a", "--json"], ["smart", "sdb", "--json"]])
        self.assertEqual([(e["event"], e.get("code")) for e in seen],
                         [("helper_ready", None), ("helper_done", 1), ("error", "refused"), ("helper_done", 3),
                          ("helper_done", 2)])
        self.assertEqual(code, 0)                                   # ends when the input closes

    def test_stop_reaches_only_the_running_job(self):
        def lines():
            yield "stop"                                            # nothing runs yet: must do nothing
            yield ask("trim", "/a")
            time.sleep(0.2)
            yield "stop"
            yield ask("trim", "/b")
        waited = []

        def job(argv, stop):
            waited.append((argv[1], stop.wait(2)))
            return 130 if stop.is_set() else 0
        _, seen = self.serve(lines(), job)
        self.assertEqual(waited, [("/a", True), ("/b", False)])     # the second job was not stopped by the first's stop
        self.assertEqual([e["code"] for e in seen if e["event"] == "helper_done"], [130, 0])

    def test_it_closes_by_itself_when_nothing_is_asked(self):
        never = threading.Event()

        def silent():
            never.wait(3)
            return
            yield
        began = time.monotonic()
        code, seen = self.serve(silent(), lambda a, s: 0, idle=0.3)
        never.set()
        self.assertLess(time.monotonic() - began, 2)
        self.assertEqual((code, seen[-1]), (0, {"event": "helper_closed", "reason": "idle"}))

    def test_a_job_that_breaks_does_not_end_the_session(self):
        def job(argv, stop):
            if argv[1] == "/bad":
                raise RuntimeError("boom")
            return 0
        _, seen = self.serve([ask("trim", "/bad"), ask("trim", "/good")], job)
        self.assertEqual([e.get("code") for e in seen if e["event"] in ("error", "helper_done")], ["os_error", 3, 0])


class CommandLine(unittest.TestCase):
    def run_helper(self, lines, root=True):
        out = io.StringIO()
        with mock.patch.object(cli.os, "geteuid", lambda: 0 if root else 1000), \
                mock.patch.object(helper.sys, "stdin", io.StringIO("\n".join(lines) + "\n")), redirect_stdout(out):
            code = cli.main(["helper", "session", "--json"])
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_not_without_admin_rights(self):
        code, events = self.run_helper([ask("trim", "/x")], root=False)
        self.assertEqual((code, events[-1]["code"]), (3, "needs_admin"))
        self.assertNotIn("helper_ready", [e["event"] for e in events])

    def test_a_job_keeps_the_checks_it_has_on_its_own(self):
        code, events = self.run_helper([ask("smart", "../etc/passwd"), ask("fsrepair", "sdb1"), ask("speed", "/tmp")])
        self.assertEqual([p for e in events for p in contract.problems(e)], [])
        kinds = [(e["event"], e.get("code")) for e in events]
        self.assertIn(("error", "no_smart"), kinds)                 # smartread's own name check ("Not a drive name")
        self.assertEqual([e["code"] for e in events if e["event"] == "helper_done"], [3, 3, 3])
        refused = [e["message"] for e in events if e.get("code") == "refused"]
        self.assertTrue(any("add --yes" in m for m in refused))     # repair still needs its yes
        self.assertTrue(any("does not do 'speed'" in m for m in refused))
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
