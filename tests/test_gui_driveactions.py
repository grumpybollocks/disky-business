# SPDX-License-Identifier: GPL-3.0-or-later
"""Mount, Unmount, Safely remove: what is asked of the drive service, what is refused, what the user is told."""
import json
import os
import subprocess
import unittest

from diskygui import driveactions as da
from diskygui.drives import Drive, Health, Volume

HOME = "/home/someone"


def drive(*volumes, usb=True):
    return Drive("EXAMPLE DRIVE", 1 << 40, "HDD", usb, Health(), list(volumes),
                 path="/org/freedesktop/UDisks2/drives/EXAMPLE")


def service(fail_on=None, error="", answer="/run/media/someone/DATA"):
    calls = []

    def run(cmd, **kw):
        i = cmd.index("call")
        calls.append((cmd[i + 2].rsplit("/", 1)[-1], cmd[i + 4]))
        if fail_on == cmd[i + 4]:
            return subprocess.CompletedProcess(cmd, 1, "", error)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "s", "data": [answer]}), "")
    return run, calls


class Rules(unittest.TestCase):
    def test_mount_skips_windows_partitions_and_says_so(self):
        d = drive(Volume("sdz1", 1, "exfat", []), Volume("sdz2", 1, "ntfs", []), Volume("sdz3", 1, "ext4", ["/x"]))
        run, calls = service()
        ok, text = da.mount(d, run)
        self.assertEqual(calls, [("sdz1", "Mount")])
        self.assertTrue(ok)
        self.assertIn("Mounted at /run/media/someone/DATA.", text)
        self.assertIn("NTFS", text)
        only_windows = drive(Volume("sdz2", 1, "ntfs3", []))
        run, calls = service()
        self.assertEqual((da.mount(only_windows, run)[0], calls, da.can_mount(only_windows)), (False, [], []))

    def test_the_system_and_the_home_folder_are_never_unmounted(self):
        d = drive(Volume("nvme0n1p2", 1, "btrfs", ["/", "/home"]), Volume("nvme0n1p1", 1, "vfat", ["/boot/efi"]),
                  Volume("nvme0n1p3", 1, "ext4", ["/mnt/data"]), usb=False)
        run, calls = service()
        ok, _ = da.unmount(d, run, home=HOME)
        self.assertEqual((ok, calls), (True, [("nvme0n1p3", "Unmount")]))
        homes = drive(Volume("sdz1", 1, "ext4", ["/home/someone"]))
        self.assertEqual((da.can_unmount(homes, HOME), da.can_remove(homes, HOME)), ([], False))
        self.assertFalse(da.can_remove(d, HOME))                # not USB, and the system runs from it

    def test_safely_remove_unmounts_first_and_stops_if_that_fails(self):
        d = drive(Volume("sdz1", 1, "exfat", ["/run/media/someone/DATA"]))
        run, calls = service()
        ok, text = da.remove(d, run, home=HOME)
        self.assertEqual(calls, [("sdz1", "Unmount"), ("EXAMPLE", "PowerOff")])
        self.assertEqual((ok, text), (True, "Switched off. It is safe to unplug it now."))
        run, calls = service(fail_on="Unmount", error="Error: org.freedesktop.UDisks2.Error.DeviceBusy: target is busy")
        ok, text = da.remove(d, run, home=HOME)
        self.assertEqual((ok, calls), (False, [("sdz1", "Unmount")]))       # never switched off while in use
        self.assertIn("in use", text)

    def test_errors_in_plain_words(self):
        d = drive(Volume("sdz1", 1, "exfat", []))
        for error, word in (("org.freedesktop.UDisks2.Error.NotAuthorizedDismissed: x", "password was not given"),
                            ("mount: wrong fs type, bad option, bad superblock", "Repair tab"),
                            ("something new\nlast line", "It did not work: last line")):
            run, _ = service(fail_on="Mount", error=error)
            ok, text = da.mount(d, run)
            self.assertFalse(ok)
            self.assertIn(word, text)

        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 1)
        self.assertEqual(da.mount(d, slow), (False, "The drive did not answer in time."))


@unittest.skipUnless(os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("DISPLAY"), "no screen")
class Panel(unittest.TestCase):
    def test_buttons_follow_the_drive_and_say_what_happened(self):
        from PySide6.QtWidgets import QApplication
        from diskygui.drivepanel import DrivePanel
        QApplication.instance() or QApplication([])
        state = {"mounts": []}

        def found():
            return [drive(Volume("sdz1", 1 << 30, "exfat", list(state["mounts"]))),
                    drive(Volume("nvme0n1p2", 1, "btrfs", ["/"]), usb=False)]
        panel = DrivePanel(found)
        seen = []

        class Fake:
            can_mount, can_unmount, can_remove = staticmethod(da.can_mount), staticmethod(da.can_unmount), \
                staticmethod(da.can_remove)

            @staticmethod
            def mount(d):
                seen.append("mount")
                state["mounts"] = ["/run/media/someone/DATA"]
                return True, "Mounted at /run/media/someone/DATA."
        panel.actions = Fake
        buttons = lambda: (panel.mount_button.isEnabled(), panel.unmount_button.isEnabled(),  # noqa: E731
                           panel.remove_button.isEnabled())
        self.assertEqual(buttons(), (False, False, False))      # no drive picked
        panel.drive_list.setCurrentRow(0)
        self.assertEqual(buttons(), (True, False, True))
        panel.mount_button.click()
        self.assertEqual(seen, ["mount"])
        self.assertEqual((panel.row(), panel.action_note.text()), (0, "Mounted at /run/media/someone/DATA."))
        self.assertEqual(buttons(), (False, True, True))        # the list was read again: now it can be unmounted
        panel.set_busy(True)
        self.assertEqual(buttons(), (False, False, False))      # never during a job
        panel.set_busy(False)
        panel.drive_list.setCurrentRow(1)
        self.assertEqual(buttons(), (False, False, False))      # the system drive
        self.assertEqual(panel.action_note.text(), "")


if __name__ == "__main__":
    unittest.main()
