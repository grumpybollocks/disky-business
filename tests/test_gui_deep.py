# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep scan in the window: Identity card, history cards, the report, and the button on the SMART tab.

Drive data is made up, with the structure of real smartctl 7.5 output.
"""
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from diskygui import deepcard  # noqa: E402

DISK = {"maker": "Example Drives Ltd", "model_family": "Example Family", "model_name": "EXAMPLE DISK 1000",
        "firmware_version": "FW01", "serial_number": "EXAMPLE-SERIAL-1", "production_date": None,
        "user_capacity": {"bytes": 4_000_787_030_016}, "device": {"protocol": "ATA"},
        "sata_version": {"string": "SATA 3.1"}, "rotation_rate": 7200, "form_factor": {"name": "3.5 inches"},
        "interface_speed": {"max": {"string": "6.0 Gb/s"}, "current": {"string": "1.5 Gb/s"}},
        "trim": {"supported": False}, "power_on_time": {"hours": 20_000}, "in_smartctl_database": True,
        "logical_block_size": 512,
        "ata_smart_error_log": {"extended": {"count": 2, "table": [
            {"lifetime_hours": 1500, "completion_registers": {"error": 0x40}},
            {"lifetime_hours": 900, "completion_registers": {"error": 0x84}}]}},
        "ata_smart_self_test_log": {"extended": {"table": [
            {"type": {"string": "Short offline"}, "status": {"string": "Aborted by host"}, "lifetime_hours": 1600},
            {"type": {"string": "Short offline"}, "status": {"string": "Completed without error", "passed": True},
             "lifetime_hours": 1590}]}},
        "ata_device_statistics": {"pages": [{"table": [
            {"name": "Logical Sectors Written", "value": 2_000_000_000, "flags": {"valid": True}},
            {"name": "Spindle Motor Power-on Hours", "value": 123456789, "flags": {"valid": True}},
            {"name": "Date and Time TimeStamp", "value": 5, "flags": {"valid": True}},
            {"name": "Percentage Used Endurance Indicator", "value": 13, "flags": {"valid": True}},
            {"name": "Highest Temperature", "value": 51, "flags": {"valid": False}}]}]},
        "sata_phy_event_counters": {"table": [
            {"name": "Device-to-host register FISes sent due to a COMRESET", "value": 3},
            {"name": "Command failed due to ICRC error", "value": 7}]},
        "ata_sct_temperature_history": {"logging_interval_minutes": 60, "table": [30, None, 35, 41]},
        "temperature": {"current": 33, "op_limit_max": 70}}
NVME = {"model_name": "EXAMPLE NVME", "device": {"protocol": "NVMe"}, "nvme_version": {"string": "2.0"},
        "nvme_total_capacity": 1_000_204_886_016, "maker": None, "production_date": None,
        "nvme_error_information_log": {"size": 256}, "nvme_smart_health_information_log": {
            "num_err_log_entries": 0, "data_units_written": 1000, "media_errors": 0},
        "nvme_self_test_log": {"table": [
            {"self_test_code": {"string": "Short"}, "self_test_result": {"value": 7, "string": "Failed"},
             "power_on_hours": 10},
            {"self_test_code": {"string": "Short"}, "self_test_result": {"value": 1, "string": "Aborted"},
             "power_on_hours": 9}]}}


class Cards(unittest.TestCase):
    def test_identity_in_plain_words_and_the_serial_hidden_unless_asked(self):
        rows = dict(deepcard.identity(DISK))
        self.assertEqual(rows["Maker"], "Example Drives Ltd")
        self.assertEqual(rows["Size"], "4.0 TB")
        self.assertEqual(rows["Kind"], "spinning disk, 7 200 rpm")
        self.assertIn("1.5 Gb/s now (the drive can do 6.0 Gb/s", rows["Interface"])     # a slow link is said
        self.assertEqual(rows["Hours on"], "20 000 hours (about 2.3 years)")
        self.assertTrue(rows["Production date"].startswith("not reported"))
        self.assertEqual(rows["Serial number"], deepcard.HIDDEN)
        self.assertEqual(dict(deepcard.identity(DISK, show_serial=True))["Serial number"], "EXAMPLE-SERIAL-1")
        nvme = dict(deepcard.identity(NVME))
        self.assertEqual((nvme["Maker"], nvme["Interface"], nvme["Kind"]),
                         ("not reported", "NVMe 2.0", "solid state (no moving parts)"))
        self.assertNotIn("Serial number", nvme)

    def test_history_cards(self):
        cards = {t: (lines, meaning) for t, lines, meaning in deepcard.history(DISK)}
        lines, meaning = cards["Errors the drive logged"]
        self.assertEqual(lines, ["At hour 1 500: could not read a spot (uncorrectable)",
                                 "At hour 900: data damaged on the way (cable or port), command refused"])
        self.assertTrue(meaning.startswith("2 errors"))
        self.assertEqual(cards["Self-test history"][1], "None failed; 1 of 2 was stopped before the end.")
        self.assertEqual(cards["Lifetime statistics"][0], ["Data written: 1.0 TB", "Wear used: 13 %"])  # plain only
        self.assertEqual(cards["Link errors (cable and port)"][0], ["Data damaged on the cable: 7"])
        self.assertIn("cable or port", cards["Link errors (cable and port)"][1])
        self.assertEqual(cards["Temperature record"][0],
                         ["Last 4 hours: lowest 30 °C, highest 41 °C", "Now: 33 °C (the drive's limit: 70 °C)"])

    def test_nvme_failed_and_stopped_self_tests_are_told_apart(self):
        cards = {t: (lines, meaning) for t, lines, meaning in deepcard.history(NVME)}
        self.assertTrue(cards["Self-test history"][1].startswith("1 of 2 failed"))
        self.assertEqual(cards["Errors the drive logged"][0], ["None logged."])
        self.assertIn("Data written: 512.0 MB", cards["Lifetime statistics"][0])

    def test_ata_self_test_status_code_decides_not_the_passed_flag(self):
        def log(*values):
            return {"ata_smart_self_test_log": {"standard": {"table": [
                {"type": {"string": "Short"}, "status": {"value": v, "string": "x", "passed": True},
                 "lifetime_hours": 5} for v in values]}}}
        meaning = lambda d: dict((t, m) for t, _, m in deepcard.history(d))["Self-test history"]  # noqa: E731
        self.assertEqual(meaning(log(0x00, 0x10, 0x20)), "None failed; 2 of 3 were stopped before the end.")
        self.assertTrue(meaning(log(0x00, 0x74)).startswith("1 of 2 failed"))        # read failure, 40 % left
        self.assertEqual(meaning(log(0x00)), "Every self-test the drive remembers passed.")

    def test_odd_answers_do_not_break_the_cards(self):
        odd = {"ata_smart_error_log": {"extended": {"count": 2, "table": [
                   {"lifetime_hours": None, "completion_registers": {"error": 0x40}}, "junk"]}},
               "ata_smart_self_test_log": {"extended": {"table": ["junk", {"status": {"value": 0}}]}},
               "sata_phy_event_counters": {"table": ["junk", {"name": "x", "value": None},
                                                     {"name": "Transition from drive PhyRdy to drive PhyNRdy",
                                                      "value": 4}]}}
        cards = {t: lines for t, lines, _ in deepcard.history(odd)}
        self.assertEqual(cards["Errors the drive logged"], ["At hour 0: could not read a spot (uncorrectable)"])
        self.assertEqual(cards["Self-test history"], ["Test at hour 0: ?"])
        self.assertEqual(cards["Link errors (cable and port)"], ["Link lost and found again: 4"])   # plain words

    def test_a_drive_that_gives_little_gets_only_the_cards_it_has(self):
        self.assertEqual(deepcard.history({"model_name": "X"}), [])
        self.assertEqual(dict(deepcard.identity({"model_name": "X"})),
                         {"Maker": "not reported", "Model": "X",
                          "Production date": "not reported (only SAS drives report one)"})

    def test_report_holds_what_the_screen_shows_serial_hidden_by_default(self):
        text = deepcard.report("EXAMPLE DISK 1000", DISK, when=0)
        for k, v in deepcard.identity(DISK):
            self.assertIn(f"  {k}: {v}", text)
        for title, lines, meaning in deepcard.history(DISK):
            self.assertIn(title, text)
            for line in lines:
                self.assertIn(line, text)
        self.assertNotIn("EXAMPLE-SERIAL-1", text)
        self.assertIn("EXAMPLE-SERIAL-1", deepcard.report("x", DISK, show_serial=True))


class Window(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_view_hides_the_serial_until_ticked_and_saves_what_it_shows(self):
        from diskygui.deepview import DeepView
        view = DeepView("EXAMPLE DISK 1000", DISK, "sdz")
        self.addCleanup(view.deleteLater)
        shown = lambda: " ".join(view.grid.itemAt(i).widget().text() for i in range(view.grid.count()))  # noqa
        self.assertNotIn("EXAMPLE-SERIAL-1", shown())
        self.assertEqual(len(view.cards), 5)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = tmp.name

        def saved():
            with open(os.path.join(folder, "r.txt"), encoding="utf-8") as f:
                return f.read()
        asked = []
        view.ask_path = lambda suggested: asked.append(suggested) or os.path.join(folder, "r.txt")
        view.save.click()
        self.assertTrue(asked[0].endswith("disky-deep-scan-sdz.txt"))
        text = saved()
        self.assertNotIn("EXAMPLE-SERIAL-1", text)
        self.assertIn("Example Drives Ltd", text)
        view.show_serial.setChecked(True)
        self.app.processEvents()
        self.assertIn("EXAMPLE-SERIAL-1", shown())
        view.save.click()
        self.assertIn("EXAMPLE-SERIAL-1", saved())
        view.ask_path = lambda suggested: ""                     # cancelled: nothing written
        os.remove(os.path.join(folder, "r.txt"))
        view.save.click()
        self.assertEqual(os.listdir(folder), [])

    def tab(self, scenario):
        from diskygui.drives import Drive, Health
        from diskygui.runner import EngineRunner
        from diskygui.smarttab import SmartTab
        from diskygui.theme import THEMES, DEFAULT_THEME
        fake = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")
        usb = Drive("EXAMPLE USB", 1000, "HDD", True, Health(), path="/p/usb", smart="", device="sdz")
        tab = SmartTab(THEMES[DEFAULT_THEME], runner=EngineRunner([sys.executable, fake, scenario]),
                       drives=lambda: [usb])
        self.addCleanup(tab.deleteLater)
        tab.wrap = lambda: ["env", "PRETEND_PROMPT=1"]
        tab.opened = []
        tab.open_deep = lambda d, data: tab.opened.append((d.device, data))
        tab.panel.drive_list.setCurrentRow(0)
        return tab

    def finish(self, tab):
        end = time.monotonic() + 5
        while tab.mode is not None:
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def test_deep_scan_button_asks_first_and_opens_the_result(self):
        tab = self.tab("good")
        self.assertFalse(tab.deep_button.isHidden())
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        tab.deep_button.click()
        self.assertEqual((len(asked), tab.mode), (1, None))          # said no: nothing runs
        self.assertIn("only reads", asked[0])
        tab.confirm = lambda text: True
        tab.deep_button.click()
        self.assertFalse(tab.deep_button.isEnabled())                # busy
        self.finish(tab)
        self.assertEqual(tab.runner.last_argv[:2] + tab.runner.last_argv[-6:],      # behind the password prompt
                         ["env", "PRETEND_PROMPT=1", "smart", "sdz", "--json", "--stdin-stop", "--deep", "--serial"])
        self.assertEqual(len(tab.opened), 1)
        self.assertEqual(tab.opened[0][0], "sdz")
        self.assertEqual(tab.opened[0][1]["maker"], "Example Drives Ltd")
        self.assertTrue(tab.deep_button.isEnabled())

    def test_deep_scan_refused_or_no_password_is_said_plainly(self):
        for scenario, words in (("refuse", "gives no SMART data"), ("nopassword", "admin password was not given")):
            tab = self.tab(scenario)
            tab.confirm = lambda text: True
            tab.deep_button.click()
            self.finish(tab)
            self.assertEqual(tab.opened, [])
            self.assertIn(words, tab.detail.text())
            self.assertTrue(tab.detail.text().startswith("Deep scan: "))



class StopIsSeen(unittest.TestCase):
    """Pressing Stop must show at once on the progress bar that covers the status line (review, 2026-11-27)."""
    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_self_test_and_defragment_all(self):
        from diskygui.defragtab import DefragTab
        from diskygui.smarttab import SmartTab
        from diskygui.theme import THEMES, DEFAULT_THEME
        for tab, mode, stop, line in ((SmartTab, "selftest", "stop_test", "test_progress"),
                                      (DefragTab, "defrag", "defrag_or_stop", "progress")):
            t = tab(THEMES[DEFAULT_THEME], drives=lambda: [])
            self.addCleanup(t.deleteLater)
            t.set_busy(mode)
            getattr(t, stop)()
            self.assertTrue(getattr(t, line).title.text().startswith("Stopping"), mode)
            t.set_busy(None)
            self.assertEqual(getattr(t, line).stopping, "", mode)


if __name__ == "__main__":
    unittest.main()
