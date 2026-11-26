# SPDX-License-Identifier: GPL-3.0-or-later
"""TRIM: reading support from a made-up /sys tree, and running fstrim only when it is safe and possible."""
import os
import subprocess
import unittest

from disky import trim
from tests.util import Folder

MOUNTINFO = """\
22 1 0:21 / / rw - btrfs /dev/nvme0n1p6 rw
30 22 8:65 / /media/stick rw - vfat /dev/sde1 rw
31 22 0:30 / /media/net rw - nfs server:/x rw
"""


def make_sys(root, disk, part, max_bytes, rotational):
    real = os.path.join(root, "devices", disk)
    os.makedirs(os.path.join(real, part))
    os.makedirs(os.path.join(real, "queue"))
    open(os.path.join(real, part, "partition"), "w").write("1")
    for name, value in (("discard_max_bytes", max_bytes), ("discard_granularity", 4096), ("rotational", rotational)):
        open(os.path.join(real, "queue", name), "w").write(f"{value}\n")
    links = os.path.join(root, "class")
    os.makedirs(links, exist_ok=True)
    os.symlink(os.path.join(real, part), os.path.join(links, part))
    return links


class Info(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        d = self.folder.__enter__()
        self.addCleanup(self.folder.__exit__, None, None, None)
        self.mi = os.path.join(d, "mountinfo")
        open(self.mi, "w").write(MOUNTINFO)
        make_sys(d, "nvme0n1", "nvme0n1p6", 2199023255040, 0)
        self.sys = make_sys(d, "sde", "sde1", 0, 1)

    def test_supported_and_not(self):
        a = trim.info("/home/x", self.mi, self.sys)
        self.assertEqual((a["mount"], a["device"], a["supported"], a["discard_max_bytes"], a["rotational"]),
                         ("/", "/dev/nvme0n1p6", True, 2199023255040, False))
        b = trim.info("/media/stick/folder", self.mi, self.sys)
        self.assertEqual((b["mount"], b["supported"], b["rotational"]), ("/media/stick", False, True))
        c = trim.info("/media/net/x", self.mi, self.sys)
        self.assertEqual((c["mount"], c["supported"]), ("/media/net", False))    # not a /dev device


class Run(unittest.TestCase):
    YES = staticmethod(lambda p: {"mount": "/m", "supported": True})
    NO = staticmethod(lambda p: {"mount": "/m", "supported": False})

    def test_runs_fstrim_on_the_mount_and_reads_the_amount(self):
        seen = []

        def fake(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "/m: 5 GiB (5368709120 bytes) trimmed\n", "")
        r = trim.run("/m/x", run=fake, is_root=True, info_of=self.YES)
        self.assertEqual(seen, [["fstrim", "-v", "/m"]])
        self.assertEqual(r["trimmed_bytes"], 5368709120)

    def test_nothing_runs_when_unsupported_or_not_root(self):
        ran = []
        with self.assertRaises(ValueError):
            trim.run("/m", run=lambda *a, **k: ran.append(a), is_root=True, info_of=self.NO)
        with self.assertRaises(PermissionError):
            trim.run("/m", run=lambda *a, **k: ran.append(a), is_root=False, info_of=self.YES)
        self.assertEqual(ran, [])

    def test_a_failing_fstrim_is_an_error(self):
        bad = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "fstrim: /m: the discard operation is not supported")  # noqa: E731
        with self.assertRaises(OSError) as ctx:
            trim.run("/m", run=bad, is_root=True, info_of=self.YES)
        self.assertIn("not supported", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
