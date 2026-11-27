# SPDX-License-Identifier: GPL-3.0-or-later
"""SMART reader, drive-list names and tab, on made-up answers shaped like real ones."""
import json
import os
import subprocess
import tempfile
import unittest

from diskygui import smart, smartdb
from diskygui.drives import Drive, Health

# id, name, flags, value, worst, threshold, pretty, pretty_unit, expansion
ATA = [[5, "reallocated-sector-count", 51, 5, 5, 10, 1960, 3, {}],         # prefail, now at its limit
       [9, "power-on-hours", 50, 100, 100, 0, 89_510_400_000, 2, {}],
       [3, "spin-up-time", 39, 94, 93, 0, 0, 2, {}],
       [194, "temperature-celsius-2", 34, 30, 20, 0, 303_150, 4, {}],
       [190, "airflow-temperature-celsius", 34, 60, 40, 45, 40, 1, {}],    # a second temperature; worst went past
       [148, "attribute-148", 0, 100, 100, 0, 0, 0, {}],
       [12, "power-cycle-count", 50, -1, -1, -1, 4275, 1, {}],             # -1 = not known, never a failure
       [199, "udma-crc-error-count", 50, 200, 200, 0, 4, 1, {}],
       [197, "current-pending-sector", 50, 100, 100, 0, 2, 3, {}],
       [231, "attribute-231", 50, 85, 85, 0, 0, 0, {}]]
NAMES = {148: "Total_SLC_Erase_Ct", 231: "SSD_Life_Left"}

NVME = {"avail_spare": {"type": "y", "data": 5}, "spare_thresh": {"type": "y", "data": 10},
        "percent_used": {"type": "y", "data": 1}, "total_data_written": {"type": "t", "data": 25_053_023_744_000},
        "ctrl_busy_time": {"type": "t", "data": 1967}, "power_cycles": {"type": "t", "data": 866},
        "media_errors": {"type": "t", "data": 0}, "temp_sensors": {"type": "aq", "data": [311, 0]},
        "wctemp": {"type": "q", "data": 363}, "warning_temp_time": {"type": "u", "data": 0}}

DB = r'''
const drive_settings builtin_knowndrives[] = {
  { "VERSION: 7.5", "-", "-", "x", "" },
  { "DEFAULT",
    "-", "-",
    "Default settings",
    "-v 1,raw48,Raw_Read_Error_Rate "
    "-v 191,raw48,G-Sense_Error_Rate "  // a comment with "quotes" and { braces
    "-v 231,raw48,Temperature_Celsius "
    "-v 7,raw48,Seek_Error_Rate,HDD "
    "-v 202,raw48,Percent_Lifetime_Remain,SSD"
  },
  /* { "Commented out", "EXAMPLE SSD 120G", "", "", "-v 231,raw48,Wrong" }, */
  { "Example SSDs", // family
    "EXAMPLE  ?SSD (120|240)G|"
    "OTHER SSD",
    "", "",
    "-v 231,raw48,SSD_Life_Left "
    "-v 170,raw48,Bad_Blk_Ct_Lat/Erl"
  },
  { "Later match", "EXAMPLE SSD 120G", "", "", "-v 231,raw48,Never_Used" },
  { "Fussy", "FUSSY DRIVE", "AB.*", "", "-v 9,raw48,Fussy_Hours" },
  { "Fussy, any firmware", "FUSSY DRIVE", "", "", "-v 9,raw48,Plain_Hours" },
  { "USB: Example; bridge", "0x1234:0x5678", "", "", "-d sat" },
};
'''


def drive(kind="ata", path="/org/freedesktop/UDisks2/drives/X"):
    return Drive("EXAMPLE DRIVE", 1000, "HDD", False, Health(), path=path, smart=kind)


def answer(data, code=0, err=""):
    out = json.dumps({"type": "x", "data": [data]})
    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, code, out, err)


class DriveList(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".h")
        with os.fdopen(fd, "w") as f:
            f.write(DB)
        self.addCleanup(os.remove, path=self.path)

    def test_first_matching_family_on_top_of_default(self):
        self.assertEqual(smartdb.names("EXAMPLE SSD 120G", path=self.path),
                         {1: "Raw_Read_Error_Rate", 191: "G-Sense_Error_Rate", 231: "SSD_Life_Left",
                          170: "Bad_Blk_Ct_Lat/Erl"})
        self.assertEqual(smartdb.names("EXAMPLE  SSD 240G", path=self.path)[231], "SSD_Life_Left")
        self.assertEqual(smartdb.names("OTHER SSD", path=self.path)[231], "SSD_Life_Left")

    def test_the_whole_model_must_match(self):
        self.assertEqual(smartdb.names("EXAMPLE SSD 120GB", path=self.path)[231], "Temperature_Celsius")
        self.assertEqual(smartdb.names("", path=self.path)[231], "Temperature_Celsius")

    def test_hdd_and_ssd_names_only_for_that_kind(self):
        hdd = smartdb.names("NOT LISTED", kind="HDD", path=self.path)
        ssd = smartdb.names("NOT LISTED", kind="NVMe", path=self.path)
        unknown = smartdb.names("NOT LISTED", path=self.path)
        self.assertEqual((hdd.get(7), hdd.get(202)), ("Seek_Error_Rate", None))
        self.assertEqual((ssd.get(7), ssd.get(202)), (None, "Percent_Lifetime_Remain"))
        self.assertEqual((unknown.get(7), unknown.get(202)), (None, None))

    def test_a_family_with_a_firmware_pattern_needs_that_firmware(self):
        self.assertEqual(smartdb.names("FUSSY DRIVE", "AB12", path=self.path)[9], "Fussy_Hours")
        self.assertEqual(smartdb.names("FUSSY DRIVE", "CD34", path=self.path)[9], "Plain_Hours")

    def test_no_list_installed(self):
        self.assertEqual(smartdb.names("EXAMPLE SSD 120G", path=self.path + ".missing"), {})


class Reader(unittest.TestCase):
    def rows(self):
        rows, msg = smart.read(drive(), run=answer(ATA), names_of=lambda *drive: NAMES)
        self.assertEqual(msg, "")
        return {r.name: r for r in rows}

    def test_ata_in_plain_words(self):
        got = {k: (r.value, r.status, r.main) for k, r in self.rows().items()}
        self.assertEqual(got, {
            "Worn-out spots replaced": ("1 960 sectors", "FAILING", True),
            "Hours switched on": ("24 864 hours", "OK", True),
            "Spin up time": ("0 ms", "OK", False),
            "Temperature": ("30 °C", "OK", True),
            "Air temperature": ("40", "Failed in the past", True),   # the second temperature, named apart
            "Total slc erase ct": ("—", "OK", False),                             # named by the drive list
            "Times switched on": ("4 275", "OK", True),
            "Cable errors": ("4", "Check the cable", True),
            "Spots it could not read": ("2 sectors", "Watch", True),
            "Life left": ("85 %", "OK", True),
        })

    def test_a_real_seagate_hard_disk_explained(self):
        # udisks2's answer for a Seagate hard disk (2026-11-27), the items that need more than a plain number.
        seagate = [[1, "raw-read-error-rate", 15, 84, 64, 6, 242_921_542, 1, {}],
                   [188, "command-timeout", 50, 100, 99, 0, 4_296_933_408, 1, {}],     # three counts in one
                   [190, "airflow-temperature-celsius", 34, 66, 49, 40, 307_150, 4, {}],
                   [193, "load-cycle-count", 50, 1, 1, 0, 366_830, 1, {}],             # score 1 of 100
                   [194, "temperature-celsius-2", 34, 34, 51, 0, 307_150, 4, {}],
                   [241, "total-lbas-written", 0, 100, 253, 0, 2_037_772_822_901, 7, {}]]  # "MB": 2 EB, wrong
        st = Drive("ST1000EX001-0EX000", 2_000_000_000_000, "HDD", False, Health(), path="/x", smart="ata")
        rows, _ = smart.read(st, run=answer(seagate), names_of=lambda *d: {})
        other, _ = smart.read(drive(), run=answer(seagate), names_of=lambda *d: {})     # not a Seagate model
        self.assertEqual({x.name: x.value for x in other}["Commands that timed out"], "4 296 933 408")
        r = {x.name: x for x in rows}
        self.assertEqual((r["Commands that timed out"].value, r["Commands that timed out"].status), ("32", "OK"))
        self.assertIn("three counts here: 32, 30, 1", r["Commands that timed out"].about)
        self.assertEqual((r["Head parkings"].value, r["Head parkings"].status, r["Head parkings"].main),
                         ("366 830", "Near its rated limit", True))
        self.assertIn("Warning: Head parkings: 366 830 (Near its rated limit)", smart.brief(rows))
        self.assertEqual((r["Temperature"].value, r["Air temperature"].value, r["Air temperature"].main),
                         ("34 °C", "34 °C", False))
        self.assertEqual((r["Total lbas written"].value, r["Total lbas written"].about), ("—", smart.NOT_TRUSTED))
        self.assertIn("Seagate counts every operation", r["Raw read error rate"].about)
        self.assertEqual(r["Raw read error rate"].status, "OK")

    def test_a_stick_is_told_it_keeps_no_record_not_blamed_on_a_case(self):
        stick = Drive("EXAMPLE STICK", 62_000_000_000, "stick", True, Health(), path="/x", smart="")
        case = Drive("USB SSD", 120_040_980_480, "SSD", True, Health(), path="/y", smart="")
        self.assertEqual(smart.read(stick, run=lambda *a, **k: self.fail("nothing to ask")), ([], smart.NO_SMART_STICK))
        self.assertEqual(smart.read(case, run=lambda *a, **k: self.fail("nothing to ask")), ([], smart.NO_SMART))
        self.assertIn("Test tab", smart.NO_SMART_STICK)

    def test_rated_items_warn_only_near_their_limit(self):
        def parkings(score, limit=0):
            rows, _ = smart.read(drive(), run=answer([[193, "load-cycle-count", 50, score, score, limit, 9, 1, {}]]),
                                 names_of=lambda *d: {})
            return rows[0].status
        self.assertEqual([parkings(s) for s in (100, 50, 11, 10, 1)],
                         ["OK", "OK", "OK", "Near its rated limit", "Near its rated limit"])
        self.assertEqual(parkings(30, limit=25), "Near its rated limit")
        self.assertEqual(parkings(20, limit=25), "Worn past its limit")     # past the limit: the drive's own word
        timeouts, _ = smart.read(drive(), run=answer([[188, "command-timeout", 50, 100, 100, 0, 7, 1, {}]]),
                                 names_of=lambda *d: {})
        self.assertEqual((timeouts[0].value, "three counts" in timeouts[0].about), ("7", False))   # small: as is

    def test_what_it_means_and_the_score(self):
        r = self.rows()
        self.assertIn("swapped for spare", r["Worn-out spots replaced"].about)
        self.assertEqual(r["Worn-out spots replaced"].detail, "score now 5, worst 5, fails at 10")
        self.assertEqual(r["Total slc erase ct"].about,
                         smart.NOT_DECODED + " Its score now 100, worst 100, fails at 0.")
        self.assertEqual(r["Times switched on"].detail, "")

    def test_without_the_drive_list(self):
        rows, _ = smart.read(drive(), run=answer(ATA), names_of=lambda *drive: {})
        names = [r.name for r in rows]
        self.assertIn("Not named by the maker (148)", names)
        self.assertIn("Not named by the maker (231)", names)

    def test_nvme(self):
        rows, _ = smart.read(drive("nvme"), run=answer(NVME))
        got = {r.name: (r.value, r.status) for r in rows}
        self.assertEqual(got["Spare space left"], ("5 %", "Too low"))
        self.assertEqual(got["Life used"], ("1 %", "OK"))
        self.assertEqual(got["Data written"], ("25.1 TB", "OK"))
        self.assertEqual(got["Time busy"], ("32 hours", "OK"))
        self.assertEqual(got["Temperature"], ("38 °C", "OK"))
        self.assertTrue(all(r.main and r.about for r in rows if r.name != "Times switched on"))

    def test_smartctl_answer_reads_the_same_as_the_drive_service(self):
        table = [{"id": 5, "name": "Reallocated_Sector_Ct", "value": 5, "worst": 5, "thresh": 10,
                  "flags": {"prefailure": True}, "raw": {"value": 1960, "string": "1960"}},
                 {"id": 9, "name": "Power_On_Hours", "value": 100, "worst": 100, "thresh": 0,
                  "flags": {"prefailure": False}, "raw": {"value": 24864, "string": "24864h+12m+01.000s"}},
                 {"id": 194, "name": "Temperature_Celsius", "value": 30, "worst": 20, "thresh": 0,
                  "flags": {"prefailure": False}, "raw": {"value": 85899345950, "string": "30 (Min/Max 18/45)"}},
                 {"id": 199, "name": "UDMA_CRC_Error_Count", "value": 200, "worst": 200, "thresh": 0,
                  "flags": {"prefailure": False}, "raw": {"value": 4, "string": "4"}},
                 {"id": 148, "name": "Unknown_Attribute", "value": 100, "worst": 100, "thresh": 0,
                  "flags": {"prefailure": False}, "raw": {"value": 7, "string": "7"}}]
        got = {r.name: (r.value, r.status, r.score) for r in smart.smartctl_rows({"ata_smart_attributes": {"table": table}})}
        self.assertEqual(got, {"Worn-out spots replaced": ("1 960 sectors", "FAILING", 5),
                               "Hours switched on": ("24 864 hours", "OK", 100),
                               "Temperature": ("30 °C", "OK", 30),
                               "Cable errors": ("4", "Check the cable", 200),
                               "Not named by the maker (148)": ("7", "OK", 100)})
        nv = {"available_spare": 100, "available_spare_threshold": 10, "percentage_used": 3, "temperature": 38,
              "data_units_written": 1_000_000, "power_on_hours": 500, "media_errors": 0}
        got = {r.name: r.value for r in smart.smartctl_rows({"nvme_smart_health_information_log": nv})}
        self.assertEqual(got, {"Life used": "3 %", "Spare space left": "100 %", "Data errors it could not fix": "0",
                               "Hours switched on": "500 hours", "Data written": "512.0 GB", "Temperature": "38 °C"})

    def test_smartctl_overall_verdict_warnings_and_cold_drives(self):
        cold = [{"id": 194, "name": "Temperature_Celsius", "value": 100, "worst": 100, "thresh": 0,
                 "flags": {"prefailure": False}, "raw": {"value": 281470681743355, "string": "-5 (Min/Max -10/40)"}}]
        rows = smart.smartctl_rows({"smart_status": {"passed": False}, "ata_smart_attributes": {"table": cold}})
        self.assertEqual([(r.name, r.value, r.status) for r in rows],
                         [("The drive's own verdict", "FAILED", "FAILING"), ("Temperature", "-5 °C", "OK")])
        ok = smart.smartctl_rows({"smart_status": {"passed": True}, "ata_smart_attributes": {"table": cold}})
        self.assertEqual([r.name for r in ok], ["Temperature"])
        nv = {"critical_warning": 4, "percentage_used": 3}
        rows = smart.smartctl_rows({"smart_status": {"passed": True}, "nvme_smart_health_information_log": nv})
        self.assertEqual([(r.name, r.bad) for r in rows], [("Critical warning", True), ("Life used", False)])

    def test_no_smart_or_no_answer(self):
        def never(*a, **k):
            raise AssertionError("must not ask")
        self.assertEqual(smart.read(drive(""), run=never), ([], smart.NO_SMART))
        rows, msg = smart.read(drive(), run=answer(ATA, 1, "Error: drive asleep"))
        self.assertEqual((rows, msg), ([], "The drive did not answer: Error: drive asleep"))
        rows, msg = smart.read(drive(), run=lambda c, **k: subprocess.CompletedProcess(c, 0, "not json", ""))
        self.assertEqual(rows, [])
        self.assertIn("Could not ask", msg)


@unittest.skipUnless(os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("DISPLAY"), "no screen")
class Tab(unittest.TestCase):
    def test_main_items_problems_first_in_red_and_show_all(self):
        from PySide6.QtWidgets import QApplication
        from diskygui.smarttab import SmartTab
        from diskygui.theme import THEMES, DEFAULT_THEME
        QApplication.instance() or QApplication([])
        t = THEMES[DEFAULT_THEME]
        tab = SmartTab(t, drives=lambda: [drive(), drive("")])
        tab.read_smart = lambda d: smart.read(d, run=answer(ATA), names_of=lambda *drive: NAMES)
        self.assertEqual((tab.answer.text(), tab.again.isEnabled()), ("Choose a drive.", False))   # nothing picked
        tab.panel.drive_list.setCurrentRow(0)
        self.assertEqual(tab.answer.text(), "The drive says it is failing. Copy your files off it now.")
        self.assertEqual(tab.table.rowCount(), 8)                       # the 8 that matter
        self.assertIn('the other 2', tab.detail.text())
        self.assertEqual([tab.table.item(i, 2).text() for i in range(5)],
                         ["FAILING", "Failed in the past", "Check the cable", "Watch", "OK"])
        self.assertEqual(tab.table.item(0, 0).foreground().color().name(), t.states["bad"])
        self.assertNotEqual(tab.table.item(4, 0).foreground().color().name(), t.states["bad"])
        self.assertIn("fails at 10", tab.table.item(0, 0).toolTip())
        self.assertEqual(tab.table.item(0, 6).text(), tab.rows[0].about)
        self.assertEqual([tab.table.item(0, c).text() for c in (3, 4, 5)], ["5", "5", "10"])   # score, worst, fails at
        self.assertFalse(tab.table.isColumnHidden(3))
        shown = [(n.text(), v.text()) for t, v, n in tab.glance_boxes if not t.isHidden()]
        self.assertEqual(shown, [("Life left", "85 %"), ("Temperature", "30 °C"), ("Hours switched on", "24 864 hours"),
                                 ("Times switched on", "4 275")])
        tab.show_all.setChecked(True)
        self.assertEqual(tab.table.rowCount(), len(ATA))
        tab.panel.drive_list.setCurrentRow(1)
        self.assertEqual((tab.answer.text(), tab.detail.text(), tab.table.rowCount()),
                         ("No SMART details for this drive.", smart.NO_SMART, 0))

    def test_a_drive_without_scores_shows_no_empty_score_columns(self):
        from PySide6.QtWidgets import QApplication
        from diskygui.smarttab import SmartTab
        from diskygui.theme import THEMES, DEFAULT_THEME
        QApplication.instance() or QApplication([])
        tab = SmartTab(THEMES[DEFAULT_THEME], drives=lambda: [drive("nvme")])
        tab.read_smart = lambda d: smart.read(d, run=answer(NVME))
        tab.panel.drive_list.setCurrentRow(0)
        self.assertTrue(all(tab.table.isColumnHidden(c) for c in (3, 4, 5)))
        self.assertEqual([n.text() for t, v, n in tab.glance_boxes if not t.isHidden()],
                         ["Life used", "Temperature", "Data written", "Times switched on"])

    def usb_tab(self, scenario):
        import sys
        from PySide6.QtWidgets import QApplication
        from diskygui.runner import EngineRunner
        from diskygui.smarttab import SmartTab
        from diskygui.theme import THEMES, DEFAULT_THEME
        self.app = QApplication.instance() or QApplication([])
        fake = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")
        usb = Drive("EXAMPLE USB", 1000, "HDD", True, Health(), path="/p/usb", smart="", device="sdz")
        tab = SmartTab(THEMES[DEFAULT_THEME], runner=EngineRunner([sys.executable, fake, scenario]),
                       drives=lambda: [usb, drive()])
        self.addCleanup(tab.deleteLater)
        tab.read_smart = lambda d: smart.read(d, run=answer(ATA), names_of=lambda *x: NAMES)
        tab.wrap = lambda: ["env", "PRETEND_PROMPT=1"]
        return tab

    def finish(self, tab):
        import time
        end = time.monotonic() + 5
        while tab.mode is not None:
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def test_usb_drive_is_read_with_the_password_after_asking(self):
        from diskygui.defragtab import admin_wrap
        tab = self.usb_tab("good")
        self.assertEqual(tab.wrap.__name__, "<lambda>")
        from diskygui.smarttab import SmartTab
        from diskygui.theme import THEMES, DEFAULT_THEME
        self.assertIs(SmartTab(THEMES[DEFAULT_THEME], drives=lambda: []).wrap, admin_wrap)   # the real prompt
        tab.panel.drive_list.setCurrentRow(1)                       # the service reads this one itself
        self.assertTrue(tab.usb_button.isHidden())
        tab.panel.drive_list.setCurrentRow(0)
        self.assertFalse(tab.usb_button.isHidden())
        self.assertIn("Read with admin password", tab.detail.text())
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        tab.usb_button.click()
        self.assertEqual((len(asked), tab.mode), (1, None))         # said no: nothing runs
        self.assertIn("only reads", asked[0])
        tab.confirm = lambda text: True
        tab.usb_button.click()
        self.finish(tab)
        self.assertEqual(tab.runner.last_argv[:2] + tab.runner.last_argv[-4:],
                         ["env", "PRETEND_PROMPT=1", "smart", "sdz", "--json", "--stdin-stop"])
        self.assertEqual([(r.name, r.value) for r in tab.rows], [("Hours switched on", "1 234 hours")])
        self.assertEqual(tab.answer.text(), "No warnings from the drive itself.")
        self.assertEqual(tab.panel.health.text(), "Read with the admin password:\nNo warnings.")
        tab.panel.drive_list.setCurrentRow(1)
        tab.panel.drive_list.setCurrentRow(0)
        self.assertEqual(len(tab.rows), 1)                          # kept for the session, not asked again
        asked.clear()
        tab.confirm = lambda text: asked.append(text) or False
        tab.again.click()                                           # fresh values mean asking smartctl again
        self.assertEqual(len(asked), 1)

    def test_usb_read_refused_or_no_password_is_said_plainly(self):
        for scenario, words in (("refuse", "gives no SMART data through its USB case"),
                                ("nopassword", "the admin password was not given")):
            tab = self.usb_tab(scenario)
            tab.panel.drive_list.setCurrentRow(0)
            tab.confirm = lambda text: True
            tab.usb_button.click()
            self.finish(tab)
            self.assertIn(words, tab.detail.text())
            self.assertEqual(tab.rows, [])

    def test_self_tests_ask_first_show_progress_and_the_result(self):
        tab = self.usb_tab("good")
        tab.panel.drives[1].device = "sdy"
        tab.panel.drive_list.setCurrentRow(0)                       # the USB drive: the service offers no self-test
        self.assertTrue(tab.short_button.isHidden() and tab.long_button.isHidden())
        tab.panel.drive_list.setCurrentRow(1)
        self.assertFalse(tab.short_button.isHidden() or tab.long_button.isHidden())
        self.assertTrue(tab.stop_button.isHidden())
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        tab.long_button.click()
        self.assertEqual((len(asked), tab.mode), (1, None))
        self.assertIn("can take hours", asked[0])
        self.assertIn("admin password", asked[0])
        tab.confirm = lambda text: True
        tab.short_button.click()
        self.assertEqual(tab.mode, "selftest")
        self.finish(tab)
        self.assertEqual(tab.runner.last_argv[-6:], ["selftest", "sdy", "--json", "--stdin-stop", "--kind", "short"])
        self.assertEqual(tab.test_status.text(), "Self-test: Passed: the drive found no problem.")
        tab.panel.drive_list.setCurrentRow(0)
        self.assertEqual(tab.test_status.text(), "")               # not shown beside another drive

    def test_a_failed_refused_or_stopped_self_test_is_said_plainly(self):
        import time
        for scenario, words in (("bad", "Self-test: FAILED (reading). Back up this drive now."),
                                ("refuse", "The password was not given, so no self-test was started.")):
            tab = self.usb_tab(scenario)
            tab.panel.drives[1].device = "sdy"
            tab.panel.drive_list.setCurrentRow(1)
            tab.confirm = lambda text: True
            tab.short_button.click()
            self.finish(tab)
            self.assertEqual(tab.test_status.text(), words)
        tab = self.usb_tab("slow")
        tab.panel.drives[1].device = "sdy"
        tab.panel.drive_list.setCurrentRow(1)
        tab.confirm = lambda text: True
        tab.short_button.click()
        end = time.monotonic() + 5
        while "40 %" not in tab.test_status.text():
            self.assertLess(time.monotonic(), end)
            self.app.processEvents()
        self.assertFalse(tab.stop_button.isHidden())
        self.assertTrue(tab.short_button.isHidden())
        tab.stop_button.click()
        self.finish(tab)
        self.assertEqual(tab.test_status.text(), "Self-test: The self-test was stopped before it finished.")

    def test_verdicts(self):
        from diskygui.smarttab import verdict
        self.assertEqual(verdict([smart.Row("a", "1")]), "No warnings from the drive itself.")
        self.assertEqual(verdict([smart.Row("a", "1", "Watch"), smart.Row("b", "1", "Watch")]),
                         "2 things to watch. Keep a backup of this drive.")


if __name__ == "__main__":
    unittest.main()
