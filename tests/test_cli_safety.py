# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from disky import cli, contract, safety
from tests.util import MIB, Folder, patch_file

MOUNTS = """\
30 1 0:27 /@ / rw shared:1 - btrfs /dev/sysdisk2 rw,subvol=/@
31 30 0:28 /@home /home rw shared:2 - btrfs /dev/sysdisk2 rw,subvol=/@home
40 30 8:65 / /run/media/someone/USB\\040STICK rw shared:3 - vfat /dev/usb1 rw
41 30 8:17 / /mnt/data rw shared:4 - ext4 /dev/data1 rw
"""


def disky(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue()


class Safety(unittest.TestCase):
    def setUp(self):
        self.info = tempfile.NamedTemporaryFile("w", suffix=".mountinfo", delete=False)
        self.info.write(MOUNTS)
        self.info.close()
        self.addCleanup(os.unlink, self.info.name)

    def test_mount_source_picks_the_longest_match(self):
        m = self.info.name
        self.assertEqual(safety.mount_source("/home/someone/x", m), "/dev/sysdisk2")
        self.assertEqual(safety.mount_source("/mnt/data/tests", m), "/dev/data1")
        self.assertEqual(safety.mount_source("/run/media/someone/USB STICK", m), "/dev/usb1")
        self.assertEqual(safety.mount_source("/mnt/database", m), "/dev/sysdisk2")

    def test_system_drive_is_recognised(self):
        m = self.info.name
        self.assertTrue(safety.is_system_drive("/home/someone/x", m, home="/home/someone"))
        self.assertTrue(safety.is_system_drive("/var/tmp", m, home="/home/someone"))
        self.assertFalse(safety.is_system_drive("/mnt/data", m, home="/home/someone"))
        self.assertFalse(safety.is_system_drive("/run/media/someone/USB STICK", m, home="/home/someone"))

    def test_reserve_is_five_percent_or_two_gib(self):
        self.assertEqual(safety.system_reserve(10 << 30), 2 << 30)
        self.assertEqual(safety.system_reserve(1000 << 30), 50 << 30)


class CommandLine(unittest.TestCase):
    def test_good_run_fits_the_contract(self):
        with Folder() as folder:
            code, out = disky("test", folder, "--size", "4M", "--json", "--keep-free", "0")
            events = [json.loads(line) for line in out.splitlines()]
            self.assertEqual([p for e in events for p in contract.problems(e)], [])
            self.assertEqual(events[0]["event"], "start")
            self.assertEqual(events[-1]["event"], "summary")
            self.assertIn(code, (0, 2))        # 2 only where the read cannot be proven
            self.assertEqual(code, cli.EXIT[events[-1]["verdict"]])

    def test_contract_checker_rejects_broken_events(self):
        good = {"event": "blocks", "phase": "verify", "first": 3, "count": 2, "state": "good"}
        self.assertEqual(contract.problems(good), [])
        for broken in (
            {"event": "bloks", "phase": "verify", "first": 3, "count": 2, "state": "good"},
            {"event": "blocks", "phase": "verify", "first": 3, "state": "good"},
            {"event": "blocks", "phase": "verify", "first": "3", "count": 2, "state": "good"},
            {"event": "blocks", "phase": "verify", "first": 3, "count": 2, "state": "fine"},
            {"event": "blocks", "phase": "verify", "first": 3, "count": 1, "state": "bad"},
            {"event": "blocks", "phase": "format", "first": 3, "count": 2, "state": "good"},
            {"event": "progress", "phase": "write", "done_bytes": True, "total_bytes": None,
             "mib_per_s": 1, "avg_mib_per_s": 1, "eta_s": None},
            "not an object",
        ):
            self.assertNotEqual(contract.problems(broken), [], broken)

    def test_damage_gives_exit_1_and_plain_words(self):
        with Folder() as folder:
            self.assertEqual(disky("write", folder, "--size", "4M", "--keep-free", "0")[0], 0)
            patch_file(os.path.join(folder, "1.h2w"), 2 * MIB, b"\x00" * 512)
            code, out = disky("verify", folder)
            self.assertEqual(code, 1)
            self.assertIn("Drive is BAD", out)
            self.assertIn("Block 2", out)
            self.assertIn("another port and another cable", out)              # H2testw's advice

    def test_second_run_refuses_and_keeps_files(self):
        with Folder() as folder:
            disky("write", folder, "--size", "1M", "--keep-free", "0")
            before = os.path.getmtime(os.path.join(folder, "1.h2w"))
            code, out = disky("test", folder, "--size", "1M", "--keep-free", "0")
            self.assertEqual(code, 3)
            self.assertIn("never overwrites", out)
            self.assertEqual(os.path.getmtime(os.path.join(folder, "1.h2w")), before)

    def test_clean_lists_first_and_removes_only_test_files(self):
        with Folder() as folder:
            disky("write", folder, "--size", "1M", "--keep-free", "0")
            for name in ("notes.txt", "x.h2w", "1.h2w.bak"):
                open(os.path.join(folder, name), "w").close()
            code, out = disky("clean", folder)
            self.assertIn("Would remove", out)
            self.assertIn("1.h2w", os.listdir(folder))
            disky("clean", folder, "--yes")
            self.assertEqual(sorted(os.listdir(folder)), ["1.h2w.bak", "notes.txt", "x.h2w"])

    def test_clean_never_removes_what_only_has_the_name(self):
        with Folder() as folder, Folder() as elsewhere:
            disky("write", folder, "--size", "2M", "--keep-free", "0")
            mine = os.path.join(elsewhere, "precious.txt")
            with open(mine, "w") as f:
                f.write("somebody's own words\n" * 100)
            with open(os.path.join(folder, "3.h2w"), "w") as f:   # a user's file that has a test file's name
                f.write("not test data\n" * 100)
            os.symlink(mine, os.path.join(folder, "2.h2w"))     # a link to a file somewhere else
            os.mkdir(os.path.join(folder, "4.h2w"))             # a folder with the name
            with open(os.path.join(folder, "4.h2w", "inside.txt"), "w") as f:
                f.write("x")
            os.link(os.path.join(folder, "1.h2w"), os.path.join(folder, "0001.h2w"))  # not a name we make
            code, out = disky("clean", folder, "--yes", "--json")
            events = [json.loads(line) for line in out.splitlines()]
            self.assertEqual([p for e in events for p in contract.problems(e)], [])
            self.assertEqual(code, 0)
            self.assertEqual(sorted(os.listdir(folder)), ["0001.h2w", "2.h2w", "3.h2w", "4.h2w"])
            self.assertTrue(os.path.exists(mine))
            self.assertTrue(os.path.exists(os.path.join(folder, "4.h2w", "inside.txt")))
            self.assertEqual([e["code"] for e in events if e.get("code") == "kept_file"], ["kept_file"])
            self.assertEqual((events[-1]["removed_files"], events[-1]["kept_files"]), (1, 1))

    def test_verify_ignores_a_folder_with_the_name(self):
        with Folder() as folder:
            disky("write", folder, "--size", "1M", "--keep-free", "0")
            os.mkdir(os.path.join(folder, "2.h2w"))
            code, out = disky("verify", folder)
            self.assertIn(code, (0, 2))

    def test_empty_folder_and_missing_folder(self):
        with Folder() as folder:
            code, out = disky("verify", folder)
            self.assertEqual(code, 3)
            self.assertIn("No test files", out)
        self.assertEqual(disky("verify", os.path.join(folder, "gone"))[0], 3)

    def test_system_drive_gets_the_tip_and_continues(self):
        with Folder() as folder:
            if not safety.is_system_drive(folder):
                self.skipTest("test folder is not on the system drive")
            code, out = disky("test", folder, "--size", "1M")
            self.assertIn("Tip:", out)
            self.assertIn(code, (0, 2))

    def test_tips_are_short_one_liners(self):
        from disky import tips
        for tip in tips.TIPS + (tips.SYSTEM_DRIVE.format(free="18.5 GiB"),):
            self.assertTrue(tip.startswith("Tip: "), tip)
            self.assertLessEqual(len(tip), 110, tip)
            self.assertNotIn("\n", tip)
        self.assertIn("Tip: switching to your pistol is always faster than reloading.", tips.TIPS)
        self.assertEqual(len(set(tips.TIPS)), len(tips.TIPS))          # no tip twice

    def test_sizes(self):
        self.assertEqual(cli.parse_size("500M"), 500 << 20)
        self.assertEqual(cli.parse_size("2GiB"), 2 << 30)
        self.assertEqual(cli.parse_size("1.5g"), 3 << 29)
        self.assertEqual(cli.parse_size("4096"), 4096)


@unittest.skipUnless(shutil.which("f3read"), "f3 is not installed")
class F3(unittest.TestCase):
    """Our files must be accepted by f3's own reader, and damage must be seen by both."""

    def f3read(self, folder):
        return subprocess.run(["f3read", folder], capture_output=True, text=True).stdout

    def test_f3read_accepts_our_files_and_sees_our_damage(self):
        with Folder() as folder:
            disky("write", folder, "--size", "16M", "--keep-free", "0")
            report = self.f3read(folder)
            self.assertRegex(report, r"Data OK: 16\.00 MB \(32768 sectors\)")
            self.assertRegex(report, r"Corrupted: 0\.00 Byte \(0 sectors\)")
            patch_file(os.path.join(folder, "1.h2w"), 5 * MIB, b"\x5a" * 512)
            self.assertRegex(self.f3read(folder), r"Corrupted: 512\.00 Byte \(1 sectors\)")
            self.assertEqual(disky("verify", folder)[0], 1)


if __name__ == "__main__":
    unittest.main()
