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

    def test_more_system_places_and_other_uses(self):
        for place in ("/efi", "/tmp", "/opt/tools", "/nix", "/nix/store", "/boot/efi", "/var/lib/x"):
            self.assertTrue(da.in_system_use(Volume("sdz1", 1, "ext4", [place]), HOME), place)
        self.assertFalse(da.in_system_use(Volume("sdz1", 1, "ext4", ["/mnt/data"]), HOME))
        swap = drive(Volume("sdz1", 1, "exfat", []))
        swap.other_use = "swap space"
        run, calls = service()
        self.assertFalse(da.can_remove(swap, HOME))
        self.assertEqual((da.remove(swap, run, HOME), calls),
                         ((False, "It is in use as swap space. Switch that off first."), []))
        from diskygui import drives as dr
        v = lambda x: {"type": "v", "data": x}  # noqa: E731
        P = "org.freedesktop.UDisks2."
        answer = {"data": [{
            "/org/freedesktop/UDisks2/drives/A": {P + "Drive": {"Model": v("A"), "Size": v(10), "ConnectionBus": v("usb")}},
            "/org/freedesktop/UDisks2/drives/B": {P + "Drive": {"Model": v("B"), "Size": v(9), "ConnectionBus": v("usb")}},
            "/org/freedesktop/UDisks2/block_devices/sdy": {P + "Block": {"Drive": v("/org/freedesktop/UDisks2/drives/A")}},
            "/org/freedesktop/UDisks2/block_devices/sdy2": {
                P + "Block": {"Drive": v("/org/freedesktop/UDisks2/drives/A")}, P + "Partition": {},
                P + "Swapspace": {"Active": v(True)}},
            "/org/freedesktop/UDisks2/block_devices/sdz1": {
                P + "Block": {"Drive": v("/org/freedesktop/UDisks2/drives/B")}, P + "Partition": {},
                P + "Encrypted": {"CleartextDevice": v("/org/freedesktop/UDisks2/block_devices/dm_2d0")}},
        }]}
        got = {d.model: (d.device, d.other_use) for d in dr.parse(answer)}
        self.assertEqual(got, {"A": ("sdy", "swap space"), "B": ("", "an unlocked encrypted volume")})

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
                            ("mount: wrong fs type, bad option, bad superblock", "Care tab"),
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
        self.assertEqual((panel.action_note.text(), buttons()), ("Mounting...", (False, False, False)))
        self.assertTrue(panel.settle())
        self.assertEqual(seen, ["mount"])
        self.assertEqual((panel.row(), panel.action_note.text()), (0, "Mounted at /run/media/someone/DATA."))
        self.assertEqual(buttons(), (False, True, True))        # the list was read again: now it can be unmounted
        gone = []
        panel.read_drives = lambda: []                          # the drive was unplugged after the list was read
        Fake.unmount = staticmethod(lambda d: gone.append(d) or (True, "x"))
        panel.unmount_button.click()
        self.assertTrue(panel.settle())
        self.assertEqual((gone, panel.action_note.text()), ([], "That drive is no longer there."))
        panel.read_drives = found
        panel.load_drives()
        panel.drive_list.setCurrentRow(0)
        panel.set_busy(True)
        self.assertEqual(buttons(), (False, False, False))      # never during a job
        panel.set_busy(False)
        panel.drive_list.setCurrentRow(1)
        self.assertEqual(buttons(), (False, False, False))      # the system drive
        self.assertEqual(panel.action_note.text(), "")


    def test_a_slow_drive_does_not_freeze_the_window(self):
        import time
        from PySide6.QtWidgets import QApplication
        from diskygui.drivepanel import DrivePanel
        QApplication.instance() or QApplication([])
        slow_a = drive(Volume("sdy1", 1 << 30, "exfat", []))
        slow_a.path, slow_a.smart, slow_a.model = "/p/a", "ata", "SLOW A"
        quick_b = drive(Volume("sdz1", 1 << 30, "exfat", []))
        quick_b.path, quick_b.model = "/p/b", "QUICK B"
        panel = DrivePanel(lambda: [slow_a, quick_b])
        self.addCleanup(panel.deleteLater)
        from diskygui.smart import Row

        def read(d):
            time.sleep(0.8)
            return [Row("Life left", "12 %")], ""
        panel.read_smart = read

        class Slow:
            can_mount, can_unmount, can_remove = staticmethod(da.can_mount), staticmethod(da.can_unmount), \
                staticmethod(da.can_remove)

            @staticmethod
            def mount(d):
                mounted.append(d.model)
                time.sleep(0.8)
                return True, "Mounted."
        mounted = []
        panel.actions = Slow
        began = time.monotonic()
        panel.drive_list.setCurrentRow(0)               # its health answer takes 0.8 s
        panel.drive_list.setCurrentRow(1)               # the user moves on at once
        self.assertLess(time.monotonic() - began, 0.8)  # picking did not wait for the drive
        self.assertTrue(panel.settle())
        self.assertNotIn("12 %", panel.health.text())   # drive A's late answer is not shown beside drive B
        began = time.monotonic()
        panel.mount_button.click()
        panel.act("mount")                              # asked again while the first still works
        self.assertLess(time.monotonic() - began, 0.8)  # the click did not wait for the drive
        self.assertTrue(panel.settle())
        self.assertGreaterEqual(time.monotonic() - began, 0.8)
        self.assertEqual((panel.action_note.text(), mounted), ("Mounted.", ["QUICK B"]))    # once, the right drive
        panel.drive_list.setCurrentRow(0)
        self.assertTrue(panel.settle())
        self.assertIn("Life left: 12 %", panel.health.text())

if __name__ == "__main__":
    unittest.main()
