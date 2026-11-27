# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep scan with `smartctl -x -j`: what may be run with admin rights, what comes back, what is left out.

The drive shapes are made up, with the structure of real smartctl 7.5 output.
"""
import json
import os
import subprocess
import tempfile
import unittest

from disky import cli, contract, helper, smartdeep

DISK = {"smartctl": {"exit_status": 64, "argv": ["smartctl"]}, "local_time": {"asctime": "x"},
        "json_format_version": [1, 0], "ata_log_directory": {}, "seagate_farm_log": {"supported": True},
        "model_family": "Example Family", "model_name": "EXAMPLE DISK 1000", "serial_number": "SECRET123",
        "wwn": {"naa": 5, "oui": 0xABCDEF, "id": 123456789}, "firmware_version": "FW01",
        "rotation_rate": 7200, "power_on_time": {"hours": 1234},
        "ata_smart_error_log": {"extended": {"count": 1, "table": [{"error_number": 1, "lifetime_hours": 1000}]}}}
NVME = {"smartctl": {"exit_status": 0}, "model_name": "EXAMPLE NVME", "serial_number": "SECRET456",
        "nvme_ieee_oui_identifier": 0x123456, "nvme_self_test_log": {"table": []}}
OUI = "AB-CD-EF   (hex)\t\tExample Drives Ltd\nABCDEF     (base 16)\t\tExample Drives Ltd\n" \
      "12-34-56   (hex)\t\tFlash Example Inc\n"


def tool(answer, seen):
    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 64, json.dumps(answer), "")
    return run


class Deep(unittest.TestCase):
    def setUp(self):
        fd, self.oui = tempfile.mkstemp()
        os.write(fd, OUI.encode())
        os.close(fd)
        self.addCleanup(os.remove, self.oui)

    def deep(self, answer, **kw):
        seen = []
        data = smartdeep.read("sdz", run=tool(answer, seen), is_root=True, is_block=lambda p: True,
                              oui_file=self.oui, **kw)
        return data, seen

    def test_reads_only_and_keeps_the_sections(self):
        data, seen = self.deep(DISK)
        self.assertEqual(seen, [["smartctl", "-x", "-j", "/dev/sdz"]])           # no option that writes
        self.assertEqual(data["model_family"], "Example Family")
        self.assertEqual(data["ata_smart_error_log"]["extended"]["count"], 1)    # old errors do not stop the read
        for gone in ("smartctl", "local_time", "json_format_version", "ata_log_directory", "seagate_farm_log"):
            self.assertNotIn(gone, data)
        self.assertIsNone(data["production_date"])

    def test_nothing_that_identifies_the_drive_unless_asked(self):
        data, _ = self.deep(DISK)
        self.assertNotIn("serial_number", data)
        self.assertEqual(data["wwn"], {"naa": 5, "oui": 0xABCDEF})               # unique part never
        self.assertNotIn("SECRET", json.dumps(data))
        self.assertNotIn("123456789", json.dumps(data))
        data, _ = self.deep(DISK, serial=True)
        self.assertEqual(data["serial_number"], "SECRET123")

    def test_maker_from_the_world_wide_name_or_the_nvme_number(self):
        self.assertEqual(self.deep(DISK)[0]["maker"], "Example Drives Ltd")
        self.assertEqual(self.deep(NVME)[0]["maker"], "Flash Example Inc")
        self.assertIsNone(self.deep({"model_name": "X", "wwn": {"oui": 0x999999}})[0]["maker"])
        self.assertIsNone(smartdeep.maker("AB-CD-EF", self.oui))
        self.assertIsNone(smartdeep.maker(0xABCDEF, "/nonexistent/oui.txt"))

    def test_refusals(self):
        seen = []
        with self.assertRaises(PermissionError):
            smartdeep.read("sdz", run=tool(DISK, seen), is_root=False, is_block=lambda p: True)
        with self.assertRaises(ValueError):
            smartdeep.read("sdz", run=tool(DISK, seen), is_root=True, is_block=lambda p: False)
        for bad in ("sdz1", "sda; reboot", "-d sat"):
            with self.assertRaises(ValueError):
                smartdeep.read(bad, run=tool(DISK, seen), is_root=True, is_block=lambda p: True)
        self.assertEqual(seen, [])
        nothing = {"smartctl": {"messages": [{"string": "Unknown USB bridge", "severity": "error"}]}}
        with self.assertRaises(ValueError) as e:
            self.deep(nothing)
        self.assertIn("Unknown USB bridge", str(e.exception))


class CommandLine(unittest.TestCase):
    def test_the_helper_accepts_the_deep_options_and_nothing_more(self):
        line = json.dumps({"cmd": "smart", "target": "sdb", "options": ["--deep", "--serial"]})
        self.assertEqual(helper.request(line), ["smart", "sdb", "--json", "--deep", "--serial"])
        for o in (["--deep", "x"], ["-x"], ["--all"]):
            with self.assertRaises(helper.Refused):
                helper.request(json.dumps({"cmd": "smart", "target": "sdb", "options": o}))

    def test_the_event_follows_the_contract(self):
        ev = {"event": "smart_deep", "device": "sdz", "data": {"model_name": "X"}}
        self.assertEqual(contract.problems(ev), [])

    def test_serial_without_deep_is_refused(self):
        seen = []
        args = cli._parser().parse_args(["smart", "sdz", "--serial", "--json"])
        self.assertEqual(cli._smart(args, seen.append, None), 2)
        self.assertEqual(seen[0]["code"], "bad_options")


if __name__ == "__main__":
    unittest.main()
