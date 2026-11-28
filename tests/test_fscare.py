# SPDX-License-Identifier: GPL-3.0-or-later
"""Check and repair: what is asked of the drive service, and what is refused before anything is asked."""
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from disky import cli, contract, fscare


def service(fs="vfat", mounted=(), answer=True, fail=""):
    """A pretend drive service. Returns (run, list of methods called)."""
    called = []

    def run(cmd, **kw):
        if "get-property" in cmd:
            prop = cmd[-1]
            data = fs if prop == "IdType" else [list(m.encode()) + [0] for m in mounted]
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "x", "data": data}), "")
        called.append((cmd[cmd.index("call") + 2].rsplit("/", 1)[-1], cmd[cmd.index("call") + 4]))
        if fail:
            return subprocess.CompletedProcess(cmd, 1, "", fail)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "b", "data": [answer]}), "")
    return run, called


class Rules(unittest.TestCase):
    def test_check_and_repair_ask_the_service_and_say_what_it_found(self):
        for action, method in (("check", "Check"), ("repair", "Repair")):
            for answer in (True, False):
                run, called = service(answer=answer)
                r = fscare.act(action, "/dev/sdz1", run)
                self.assertEqual(called, [("sdz1", method)])
                self.assertEqual((r["ok"], r["fs"], r["device"], r["message"]),
                                 (answer, "vfat", "sdz1", fscare.WORDS[(action, answer)]))

    def test_never_on_a_mounted_partition(self):
        for action in ("check", "repair"):
            run, called = service(mounted=["/run/media/someone/DATA"])
            with self.assertRaises(fscare.Refused) as e:
                fscare.act(action, "sdz1", run)
            self.assertEqual(called, [])
            self.assertIn("Unmount it first", str(e.exception))

    def test_never_on_ntfs(self):
        for fs in ("ntfs", "ntfs3", "fuseblk"):
            run, called = service(fs=fs)
            with self.assertRaises(fscare.Refused) as e:
                fscare.act("repair", "sdz1", run)
            self.assertEqual((called, str(e.exception)), ([], fscare.NTFS_WHY))

    def test_errors_in_plain_words(self):
        for error, word in (("org.freedesktop.UDisks2.Error.NotSupported: no", "no tool to repair hfsplus"),
                            ("Error.NotAuthorizedDismissed", "password was not given"),
                            ("odd\nlast line", "It did not work: last line")):
            run, _ = service(fs="hfsplus", fail=error)
            with self.assertRaises(fscare.Refused) as e:
                fscare.act("repair", "sdz1", run)
            self.assertIn(word, str(e.exception))

    def test_command_line_and_contract(self):
        for cmd, answer, code in (("fscheck", True, 0), ("fscheck", False, 1), ("fsrepair", True, 0)):
            run, called = service(answer=answer)
            out = io.StringIO()
            with mock.patch.object(fscare.proc, "run", run), mock.patch.object(fscare.act, "__defaults__", (run, 6 * 3600, None)), \
                    mock.patch.object(fscare.facts, "__defaults__", (run,)), redirect_stdout(out):
                got = cli.main([cmd, "sdz1", "--json"] + (["--yes"] if cmd == "fsrepair" else []))
            events = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual([p for e in events for p in contract.problems(e)], [])
            self.assertEqual((got, events[1]["event"], events[1]["ok"]), (code, "fs_result", answer))
        run, _ = service(fs="ntfs")
        out = io.StringIO()
        with mock.patch.object(fscare.act, "__defaults__", (run, 1, None)), redirect_stdout(out):
            self.assertEqual(cli.main(["fsrepair", "sdz1", "--json", "--yes"]), 3)
        self.assertIn("refused", out.getvalue())

    def test_repair_from_the_command_line_needs_yes(self):
        run, called = service()
        out = io.StringIO()
        with mock.patch.object(fscare.act, "__defaults__", (run, 1, None)), \
                mock.patch.object(fscare.facts, "__defaults__", (run,)), redirect_stdout(out):
            self.assertEqual(cli.main(["fsrepair", "sdz1", "--json"]), 3)
        self.assertEqual(called, [])
        self.assertIn("add --yes", out.getvalue())

    def test_no_answer_about_the_result_is_said_not_crashed(self):
        def run(cmd, **kw):
            if "get-property" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"data": "vfat" if cmd[-1] == "IdType" else []}), "")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with self.assertRaises(fscare.Refused) as e:
            fscare.act("repair", "sdz1", run)
        self.assertIn("gave no answer", str(e.exception))


    def test_only_the_partition_that_was_confirmed(self):
        def sized(size, fs="vfat"):
            called = []

            def run(cmd, **kw):
                if "get-property" in cmd:
                    data = {"IdType": fs, "Size": size}.get(cmd[-1], [])
                    return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "x", "data": data}), "")
                called.append(cmd[cmd.index("call") + 4])
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "b", "data": [True]}), "")
            return run, called
        run, called = sized(1000)
        self.assertTrue(fscare.act("repair", "sdz1", run, expect=("vfat", 1000))["ok"])
        self.assertEqual(called, ["Repair"])
        for other in (sized(2000), sized(1000, "ext4")):            # another drive now answers to sdz1
            run, called = other
            with self.assertRaises(fscare.Refused) as e:
                fscare.act("repair", "sdz1", run, expect=("vfat", 1000))
            self.assertEqual((called, str(e.exception)), ([], fscare.NOT_THE_ONE))
        self.assertEqual((fscare.expected("vfat:1000"), fscare.expected(":5"), fscare.expected("")),
                         (("vfat", 1000), ("", 5), None))
        with self.assertRaises(fscare.Refused):
            fscare.expected("vfat:big")
        from disky import helper
        ask = lambda opts: helper.request(json.dumps({"cmd": "fsrepair", "target": "sdz1", "options": opts}))  # noqa: E731
        self.assertEqual(ask(["--yes", "--expect", "vfat:1000"]), ["fsrepair", "sdz1", "--json", "--yes", "--expect", "vfat:1000"])
        for bad in (["--expect"], ["--expect", "vfat:1000; reboot"], ["--expect", "--yes"], ["--expect", "a:b"],
                    ["--expect", "../x:1"], ["--expect", "vfat:" + "9" * 30]):
            with self.assertRaises(helper.Refused, msg=bad):
                ask(bad)

if __name__ == "__main__":
    unittest.main()
