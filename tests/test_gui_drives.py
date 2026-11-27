# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive list and health, on made-up udisks2 answers (same shape as `busctl --json=short` gives)."""
import os
import subprocess
import sys
import tempfile
import unittest

from diskygui import drives as dr

P = "org.freedesktop.UDisks2."


def v(data):
    return {"type": "v", "data": data}


def drive(model, size, rpm=0, bus="", **extra):
    out = {P + "Drive": {"Model": v(model), "Size": v(size), "RotationRate": v(rpm), "ConnectionBus": v(bus),
                         "Optical": v(False)}}
    for iface, props in extra.items():
        out[P + iface.replace("_", ".")] = {k: v(x) for k, x in props.items()}
    return out


def block(drv, size, fstype, mounts=None, read_only=False):
    out = {P + "Block": {"Drive": v("/org/freedesktop/UDisks2/drives/" + drv), "Size": v(size),
                         "IdType": v(fstype), "ReadOnly": v(read_only)}}
    if mounts is not None:
        out[P + "Filesystem"] = {"MountPoints": v([list(m.encode()) + [0] for m in mounts])}
    return out


D, B = "/org/freedesktop/UDisks2/drives/", "/org/freedesktop/UDisks2/block_devices/"
ANSWER = {"type": "a{oa{sa{sv}}}", "data": [{
    D + "sata_hdd": drive("EXAMPLE HDD 4T ", 4_000_787_030_016, rpm=5400, Drive_Ata={
        "SmartSupported": True, "SmartEnabled": True, "SmartUpdated": 1, "SmartFailing": False,
        "SmartPowerOnSeconds": 3600 * 64_535, "SmartTemperature": 302.15}),
    D + "sata_dying": drive("EXAMPLE SSD 240G", 240_057_409_536, Drive_Ata={
        "SmartSupported": True, "SmartEnabled": True, "SmartUpdated": 1, "SmartFailing": True,
        "SmartPowerOnSeconds": 7200, "SmartTemperature": 0.0}),
    D + "nvme": drive("EXAMPLE NVME 1TB", 1_000_204_886_016, NVMe_Controller={
        "SmartUpdated": 1, "SmartCriticalWarning": [], "SmartPowerOnHours": 3586, "SmartTemperature": 304}),
    D + "nvme_warn": drive("EXAMPLE NVME 2TB", 2_000_398_934_016, NVMe_Controller={
        "SmartUpdated": 1, "SmartCriticalWarning": ["spare"], "SmartPowerOnHours": 9, "SmartTemperature": 350}),
    D + "usb": drive("EXAMPLE USB SSD", 120_040_980_480, bus="usb"),
    D + "usb_hdd": drive("EXAMPLE USB HDD", 250_059_350_016, rpm=-1, bus="usb"),   # -1: spinning, speed unknown
    D + "dvd": {P + "Drive": {"Model": v("EXAMPLE DVD"), "Size": v(0), "Optical": v(True)}},
    B + "sda": block("sata_hdd", 4_000_787_030_016, ""),
    B + "sda1": block("sata_hdd", 4_000_000_000_000, "ext4", []),
    B + "sdb1": block("sata_dying", 240_000_000_000, "ext4", ["/mnt/old"], read_only=True),
    B + "nvme0n1p1": block("nvme", 500_000_000, "vfat", ["/boot/efi"]),
    B + "nvme0n1p2": block("nvme", 999_000_000_000, "btrfs", ["/", "/home"]),
    B + "nvme1n1p1": block("nvme_warn", 2_000_000_000_000, "ntfs", ["/mnt/games"]),
    B + "sde1": block("usb", 120_039_914_496, "vfat", ["/run/media/someone/STICK"]),
    B + "nvme1n1p2": block("nvme_warn", 300_000_000_000, "ntfs", ["/mnt/windows"]),
    B + "loop0": block("none", 1, "squashfs", ["/snap"]),
}]}


class Parse(unittest.TestCase):
    def setUp(self):
        self.d = {x.model: x for x in dr.parse(ANSWER)}

    def test_which_drives_and_in_which_order(self):
        self.assertEqual([x.model for x in dr.parse(ANSWER)],
                         ["EXAMPLE USB HDD", "EXAMPLE USB SSD", "EXAMPLE HDD 4T", "EXAMPLE NVME 2TB", "EXAMPLE NVME 1TB",
                          "EXAMPLE SSD 240G"])     # removable first, then by size; no DVD, no loop device

    def test_nvme_with_no_drive_size_takes_it_from_its_namespace(self):
        # Real udisks2 answer (Manjaro, 2026-11-27): the NVMe Drive says Size 0, its nvme0n1 block has the size.
        # Empty card readers say Size 0 everywhere and stay hidden.
        answer = {"data": [{
            D + "nvme0": drive("EXAMPLE NVME 500G", 0, NVMe_Controller={"SmartUpdated": 1}),
            D + "reader": drive("Generic MassStorageClass", 0, bus="usb"),
            B + "nvme0n1": block("nvme0", 500_107_862_016, ""),
            B + "sde": block("reader", 0, ""),
        }]}
        found = dr.parse(answer)
        self.assertEqual([(x.model, x.size, x.device, x.smart) for x in found],
                         [("EXAMPLE NVME 500G", 500_107_862_016, "nvme0n1", "nvme")])

    def test_kind_and_label(self):
        self.assertEqual({m: x.kind_text() for m, x in self.d.items()},
                         {"EXAMPLE USB HDD": "USB HDD", "EXAMPLE USB SSD": "USB SSD", "EXAMPLE HDD 4T": "HDD", "EXAMPLE NVME 2TB": "NVMe",
                          "EXAMPLE NVME 1TB": "NVMe", "EXAMPLE SSD 240G": "SSD"})
        self.assertTrue(self.d["EXAMPLE HDD 4T"].spinning)
        self.assertFalse(self.d["EXAMPLE NVME 1TB"].spinning)
        self.assertEqual(self.d["EXAMPLE USB SSD"].label, "120 GB  USB SSD\nEXAMPLE USB SSD")
        self.assertEqual(self.d["EXAMPLE HDD 4T"].label, "4.0 TB  HDD\nEXAMPLE HDD 4T")

    def test_health(self):
        self.assertEqual(self.d["EXAMPLE HDD 4T"].health.lines(), ["Health: OK", "29 °C   64 535 hours"])
        h = self.d["EXAMPLE SSD 240G"].health
        self.assertEqual((h.state, h.lines()), ("failing", ["Health: FAILING", "2 hours"]))
        self.assertEqual(self.d["EXAMPLE NVME 1TB"].health.lines(), ["Health: OK", "31 °C   3 586 hours"])
        h = self.d["EXAMPLE NVME 2TB"].health
        self.assertEqual((h.state, h.lines()[0]), ("failing", "Health: WARNING (spare)"))
        h = self.d["EXAMPLE USB SSD"].health
        self.assertEqual((h.state, h.lines()), ("unknown", ["Health not available"]))

    def test_where_a_test_would_write(self):
        yes, no = (lambda p: True), (lambda p: False)
        self.assertEqual(self.d["EXAMPLE USB SSD"].target("/home/x", yes), ("/run/media/someone/STICK", ""))
        folder, why = self.d["EXAMPLE HDD 4T"].target("/home/x", yes)
        self.assertEqual((folder, why[:12]), (None, "Not mounted."))
        folder, why = self.d["EXAMPLE SSD 240G"].target("/home/x", yes)
        self.assertEqual((folder, "read-only" in why), (None, True))
        # the system drive: its own mount points are not writable, the home folder on it is
        only_home = lambda p: p == "/home/x"                                     # noqa: E731
        self.assertEqual(self.d["EXAMPLE NVME 1TB"].target("/home/x", only_home),
                         ("/home/x/" + dr.TEST_FOLDER, ""))
        # a drive that does not hold the home folder never gets the home folder
        folder, why = self.d["EXAMPLE USB SSD"].target("/home/x", only_home)
        self.assertEqual((folder, why[:9]), (None, "No folder"))
        self.assertEqual(self.d["EXAMPLE USB SSD"].target("/home/x", no)[0], None)

    def test_ntfs_volumes_are_never_touched_automatically(self):
        touched = []
        spy = lambda p: touched.append(p) or True                                  # noqa: E731
        d = self.d["EXAMPLE NVME 2TB"]                                             # ntfs (2 TB) + ntfs (300 GB)
        folder, why = d.target("/home/x", spy)
        self.assertEqual((folder, touched), (None, []))
        self.assertIn("NTFS", why)

    def test_system_volume_is_recognised(self):
        self.assertEqual([vol.is_system for vol in self.d["EXAMPLE NVME 1TB"].volumes], [False, True])

    def test_no_service_gives_an_empty_list(self):
        def missing(*a, **k):
            raise FileNotFoundError("busctl")
        self.assertEqual(dr.read(missing), [])
        self.assertEqual(dr.read(lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no such service")), [])
        self.assertEqual(dr.read(lambda *a, **k: subprocess.CompletedProcess(a, 0, "not json", "")), [])
        self.assertEqual(dr.parse({}), [])


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtWidgets import QApplication
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise                   # our own code failing to import must fail loudly, never skip
    HAVE_QT = False



class ReadOnlyMounts(unittest.TestCase):
    def test_a_read_only_mount_says_why(self):
        mac = dr.Drive("EXAMPLE MAC DISK", 250 << 30, "HDD", True, dr.Health(),
                       [dr.Volume("sdz2", 250 << 30, "hfsplus", ["/run/media/someone/MAC"], False)])
        folder, why = mac.target(writable=lambda p: False, mounted_ro=lambda p: True)
        self.assertIsNone(folder)
        self.assertIn("Mounted read-only", why)
        self.assertIn("Mac (HFS+)", why)
        folder, why = mac.target(writable=lambda p: True, mounted_ro=lambda p: False)
        self.assertEqual((folder, why), ("/run/media/someone/MAC", ""))


class WhichFolder(unittest.TestCase):
    """A volume can be mounted in several places. The app must never pick somebody else's."""
    SYSTEM = ["/mnt/games/library/shared", "/", "/home", "/var/cache"]

    def drive(self, mounts, fstype="btrfs"):
        return dr.Drive("EXAMPLE", 1 << 40, "NVMe", False, dr.Health(), [dr.Volume("nvme0n1p2", 1 << 40, fstype, mounts)])

    def test_the_system_volume_only_ever_gets_our_folder_in_the_home_folder(self):
        can = lambda *ok: (lambda p: p in ok)  # noqa: E731
        d = self.drive(self.SYSTEM)
        folder, why = d.target("/home/x", can("/mnt/games/library/shared", "/home/x", "/var/cache"),
                               mounted_ro=lambda p: False)
        self.assertEqual((folder, why), ("/home/x/" + dr.TEST_FOLDER, ""))
        folder, why = d.target("/home/x", can("/mnt/games/library/shared", "/var/cache"),
                               mounted_ro=lambda p: False)
        self.assertIsNone(folder)                       # the home folder is not writable: nothing else is taken
        self.assertIn("Other folder", why)
        home_only = self.drive(["/srv/library/bind", "/home"])
        folder, _ = home_only.target("/home/x", can("/srv/library/bind", "/home/x"), mounted_ro=lambda p: False)
        self.assertEqual(folder, "/home/x/" + dr.TEST_FOLDER)

    def test_another_volume_gets_its_top_mount_not_one_buried_in_a_folder_tree(self):
        d = self.drive(["/mnt/big/library/shared/deep", "/mnt/data", "/mnt/data/again"], "ext4")
        folder, _ = d.target("/home/x", lambda p: True, mounted_ro=lambda p: False)
        self.assertEqual(folder, "/mnt/data")
        self.assertEqual(dr.top_mounts(["/a/b/c", "/", "/a"]), ["/", "/a", "/a/b/c"])

@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class InTheWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open(self, found):
        win = MainWindow(theme=THEMES["w98"], runner=EngineRunner([sys.executable, "-c", "pass"]), drives=lambda: found)
        self.addCleanup(win.close)
        return win.pages[0]

    def test_list_health_and_folder(self):
        found = dr.parse(ANSWER)
        tab = self.open(found)
        self.assertEqual([tab.drive_list.item(i).text() for i in range(tab.drive_list.count())],
                         [d.label for d in found])
        self.assertEqual((tab.drive_list.currentRow(), tab.go.isEnabled(), tab.health.text()), (-1, False, ""))  # nothing is picked for the user
        tab.drive_list.setCurrentRow(2)                         # the unmounted HDD
        self.assertEqual(tab.health.text(), "Health: OK\n29 °C   64 535 hours")
        self.assertEqual((tab.folder, tab.go.isEnabled()), ("", False))
        self.assertIn("Press Mount", tab.status.text())
        tab.drive_list.setCurrentRow(5)                         # the failing, read-only SSD
        self.assertIn("FAILING", tab.health.text())
        self.assertFalse(tab.go.isEnabled())

    def mounted(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, folder)
        return [dr.Drive("EXAMPLE USB", 1 << 30, "SSD", True, dr.Health(),
                         [dr.Volume("sdz1", 1 << 30, "ext4", [folder], False)])], folder

    def test_keyboard_focus_picks_no_drive(self):
        found, _ = self.mounted()
        tab = self.open(found)
        QApplication.sendEvent(tab.drive_list, QFocusEvent(QEvent.FocusIn, Qt.TabFocusReason))
        self.assertEqual((tab.folder, tab.go.isEnabled()), ("", False))
        tab.drive_list.setCurrentRow(0)                         # a real pick still works
        self.assertTrue(tab.folder)

    def test_refresh_forgets_the_picked_drive_folder(self):
        found, _ = self.mounted()
        tab = self.open(found)
        tab.drive_list.setCurrentRow(0)
        self.assertTrue(tab.go.isEnabled())
        tab.refresh.click()
        self.assertEqual((tab.folder, tab.go.isEnabled()), ("", False))

    def test_no_other_tab_opens_while_one_works_not_even_by_keyboard(self):
        tab = self.open([])
        win = tab.window()
        tab.set_busy("test")
        win.tabs.setCurrentIndex(1)                             # what Ctrl+Tab does
        self.assertEqual(win.tabs.currentIndex(), 0)
        tab.set_busy(None)
        win.tabs.setCurrentIndex(1)
        self.assertEqual(win.tabs.currentIndex(), 1)

    def test_folder_line_only_where_a_tab_uses_one(self):
        tab = self.open([])
        smart_tab = next(p for p in tab.window().pages if p.title == "SMART")
        self.assertTrue(smart_tab.panel.folder_box.isHidden() and smart_tab.panel.choose.isHidden())
        self.assertFalse(tab.panel.folder_box.isHidden() or tab.panel.choose.isHidden())
        self.assertEqual(tab.panel.choose.text(), "Other folder...")

    def test_health_box_shows_wear_and_warnings(self):
        from diskygui.smart import Row
        ssd = dr.Drive("EXAMPLE SSD", 1 << 30, "SSD", False, dr.Health("ok", "Health: OK", 30, 100), smart="ata",
                       path="/p")
        usb = dr.Drive("EXAMPLE USB", 1 << 30, "SSD", True, dr.Health())
        tab = self.open([ssd, usb])
        asked = []
        rows = [Row("Life left", "85 %"), Row("Worn-out spots replaced", "0 sectors"), Row("Spin up time", "0 ms"),
                Row("Cable errors", "4", "Check the cable"), Row("Data written", "—"),
                Row("Spare space left", "5 %", "Too low")]
        tab.panel.read_smart = lambda d: (asked.append(d.model), (rows, ""))[1]
        tab.drive_list.setCurrentRow(0)
        self.assertTrue(tab.panel.settle())
        self.assertEqual(tab.health.text().split("\n"),
                         ["Health: OK", "30 °C   100 hours", "Life left: 85 %", "Worn-out spots replaced: 0 sectors",
                          "Warning: Cable errors: 4 (Check the cable)",
                          "Warning: Spare space left: 5 % (Too low)"])      # a warning is listed once, as a warning
        tab.drive_list.setCurrentRow(1)
        self.assertEqual(asked, ["EXAMPLE SSD"])                    # nothing is asked of a drive with no SMART
        self.assertEqual(tab.health.text(), "No health data: this USB case does not pass it on.")

    def test_a_long_reason_never_forces_the_window_wide(self):
        long_reason = dr.Drive("EXAMPLE WINDOWS DISK", 250 << 30, "HDD", True, dr.Health(),
                               [dr.Volume("sdz2", 250 << 30, "ntfs", ["/run/media/someone/WIN"], False)])
        tab = self.open([long_reason])
        win = tab.window()
        win.show()                                      # a hidden window does not work out its layout
        self.app.processEvents()
        before = win.minimumSizeHint().width()
        for page in win.pages:
            win.tabs.setCurrentWidget(page)
            page.panel.drive_list.setCurrentRow(0)
            page.panel.settle()
            self.app.processEvents()
            self.assertLess(win.minimumSizeHint().width(), 800, page.title)
            self.assertLess(win.minimumSizeHint().width(), before + 120, page.title)
        self.assertGreater(len(win.pages[0].status.text()), 150)        # the long reason is really on screen
        self.assertIn("NTFS", win.pages[0].status.text())
        # The tip line at the bottom was the real cause: a long tip or message must wrap, not widen the window.
        from disky import tips
        for text in list(tips.TIPS) + ["A long message from the engine. " * 12]:
            win.show_tip(text)
            self.app.processEvents()
            self.assertLess(win.minimumSizeHint().width(), 800, text[:40])
        self.assertLess(before, 800)                                    # fits a small laptop screen

    def test_the_open_tab_stands_out_in_every_look(self):
        from diskygui.chrome import stylesheet
        for theme in THEMES.values():
            css = stylesheet(theme)
            on = css.split("QTabBar::tab:selected {")[1].split("}")[0]
            off = css.split("QTabBar::tab {")[1].split("}")[0]
            if theme.flat:                              # modern looks: an underline in the accent colour, bold
                self.assertIn(f"border-bottom: 3px solid {theme.tab_on}", on, theme.name)
                self.assertIn("font-weight: bold", on, theme.name)
                self.assertIn("border-bottom: 3px solid transparent", off, theme.name)
                self.assertNotEqual(theme.tab_on_text, theme.dark, theme.name)      # and another text colour
                continue
            picture = lambda rule: rule.split("border-image: url(")[1].split(")")[0]  # noqa: E731
            self.assertNotEqual(picture(on), picture(off), theme.name)
            with open(picture(on), "rb") as a, open(picture(off), "rb") as b:
                self.assertNotEqual(a.read(), b.read(), theme.name)     # really drawn differently

    def test_no_drives_still_lets_you_choose_a_folder(self):
        tab = self.open([])
        self.assertIn("not available", tab.health.text())
        tab.set_folder("/somewhere")
        self.assertTrue(tab.go.isEnabled())


if __name__ == "__main__":
    unittest.main()
