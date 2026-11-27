# SPDX-License-Identifier: GPL-3.0-or-later
"""Without --json the command line speaks to a person: every result an event carries must come out in words.

Found 2026-11-27: Check, Repair, self-tests, Defragment all and SMART printed nothing without --json.
"""
import io
import unittest

from disky import cli, contract

# Events that carry no result of their own: their content is shown by another event, or only the window uses it.
QUIET = {"start", "phase", "file", "blocks", "done", "frag_blocks", "helper_ready", "helper_done", "helper_closed",
         "speed_phase", "speed_sample", "trim_info"}
SPECIAL = {"speed_result": {"test": "seq_write"}, "progress": {"phase": "write"},
           "summary": {"verdict": "good", "sectors": {}}, "smart_data": {"data": {"model_name": "EXAMPLE"}},
           "smart_deep": {"data": {"model_name": "EXAMPLE", "maker": "Example Ltd"}}}


def sample(kind):
    ev = {"event": kind}
    for field, kinds in contract.EVENTS[kind].items():
        first = kinds[0] if isinstance(kinds, tuple) else kinds
        ev[field] = {int: 3, float: 1.5, str: "x", bool: False, dict: {}}[first]
    ev.update(SPECIAL.get(kind, {}))
    return ev


class Words(unittest.TestCase):
    def test_every_result_is_said_in_words(self):
        for kind in contract.EVENTS:
            out, err = io.StringIO(), io.StringIO()
            cli.Printer(False, out, err)(sample(kind))
            said = (out.getvalue() + err.getvalue()).strip()
            if kind in QUIET:
                continue
            self.assertTrue(said, f"{kind} prints nothing without --json")

    def test_the_words(self):
        def say(ev):
            out, err = io.StringIO(), io.StringIO()
            cli.Printer(False, out, err)(ev)
            return out.getvalue() + err.getvalue()
        self.assertIn("Check of sdz1 (vfat): No problems found.",
                      say({"event": "fs_result", "action": "check", "device": "sdz1", "fs": "vfat", "ok": True,
                           "message": "No problems found."}))
        self.assertIn("Self-test (short): Passed", say({"event": "selftest_result", "kind": "short", "status": "s",
                                                        "ok": True, "message": "Passed"}))
        deep = say({"event": "smart_deep", "device": "sdz", "data": {
            "model_name": "EXAMPLE", "maker": "Example Ltd", "smart_status": {"passed": True},
            "power_on_time": {"hours": 12}, "ata_smart_error_log": {"extended": {"count": 3}}}})
        for words in ("sdz: EXAMPLE", "Health: passed", "Maker: Example Ltd", "Hours on: 12",
                      "Errors in the drive's log: 3"):
            self.assertIn(words, deep)
        self.assertNotIn("Serial", deep)


if __name__ == "__main__":
    unittest.main()
