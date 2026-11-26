# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading SMART with smartctl: what may be run with admin rights, and what comes back."""
import json
import subprocess
import unittest

from disky import smartread

ATA = {"smartctl": {"exit_status": 0}, "model_name": "EXAMPLE", "smart_status": {"passed": True},
       "ata_smart_attributes": {"table": []}, "local_time": {"asctime": "x"}, "serial_number": "SECRET123"}


def tool(answer, seen):
    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(answer), "")
    return run


class Rules(unittest.TestCase):
    def test_only_whole_drive_names_reach_the_admin_command(self):
        for bad in ("../etc/passwd", "sdb1; reboot", "sdb1", "-d sat", "", "loop0", "/dev/../sdb x"):
            with self.assertRaises(ValueError):
                smartread.device_path(bad)
        self.assertEqual([smartread.device_path(x) for x in ("sdb", "/dev/sdab", "nvme0n1", "mmcblk0")],
                         ["/dev/sdb", "/dev/sdab", "/dev/nvme0n1", "/dev/mmcblk0"])

    def test_reads_only_and_never_passes_the_serial_number_on(self):
        seen = []
        data = smartread.read("sdz", run=tool(ATA, seen), is_root=True, is_block=lambda p: True)
        self.assertEqual(seen, [["smartctl", "-j", "-H", "-A", "-i", "/dev/sdz"]])     # no option that writes
        self.assertEqual(sorted(data), ["ata_smart_attributes", "model_name", "smart_status"])

    def test_refusals(self):
        seen = []
        with self.assertRaises(PermissionError):
            smartread.read("sdz", run=tool(ATA, seen), is_root=False, is_block=lambda p: True)
        with self.assertRaises(ValueError):
            smartread.read("sdz", run=tool(ATA, seen), is_root=True, is_block=lambda p: False)
        self.assertEqual(seen, [])
        nothing = {"smartctl": {"messages": [{"string": "Unknown USB bridge [0x1234:0x5678]", "severity": "error"}]}}
        with self.assertRaises(ValueError) as e:
            smartread.read("sdz", run=tool(nothing, seen), is_root=True, is_block=lambda p: True)
        self.assertIn("Unknown USB bridge", str(e.exception))


if __name__ == "__main__":
    unittest.main()
