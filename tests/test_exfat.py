# SPDX-License-Identifier: GPL-3.0-or-later
"""The exFAT scan on a tiny volume built by hand here (no mounting, no root).

The volume is made from the published layout, so these tests prove the reader against that reading of the
layout. The check against the kernel's own answer on a real drive was done by hand.
"""
import hashlib
import io
import json
import os
import struct
import unittest
from contextlib import redirect_stderr, redirect_stdout

from disky import cli, contract, exfat
from tests.util import Folder

SECTOR, CLUSTER, COUNT = 512, 4096, 64
FAT_AT, HEAP_AT = 8, 16                  # in sectors
END = 0xFFFFFFFF


def file_set(name, size, first, no_chain, directory=False):
    units = name.encode("utf-16-le")
    names = [units[i:i + 30] for i in range(0, len(units), 30)]
    out = struct.pack("<BBHH", 0x85, 1 + len(names), 0, 0x10 if directory else 0x20).ljust(32, b"\0")
    stream = bytearray(32)
    stream[0], stream[1], stream[3] = 0xC0, 1 | (2 if no_chain else 0), len(name)
    struct.pack_into("<Q", stream, 8, size)
    struct.pack_into("<IQ", stream, 20, first, size)
    out += bytes(stream)
    for part in names:
        out += (b"\xC1\0" + part).ljust(32, b"\0")
    return out


def build(fat_patch=None, magic=b"EXFAT   "):
    """Clusters: 2 bitmap, 3 root, 4-6 file A (one piece), 7+9+10+12 file B (three pieces), 8 folder D,
    13-14 file C inside D (chained, but one piece), 11 marked used with no owner, the rest free."""
    img = bytearray(HEAP_AT * SECTOR + COUNT * CLUSTER)
    boot = bytearray(512)
    boot[0:3], boot[3:11] = b"\xEB\x76\x90", magic
    struct.pack_into("<QQ", boot, 64, 0, len(img) // SECTOR)
    struct.pack_into("<5I", boot, 80, FAT_AT, HEAP_AT - FAT_AT, HEAP_AT, COUNT, 3)
    boot[108], boot[109], boot[110] = 9, 3, 1
    boot[510:512] = b"\x55\xAA"
    img[0:512] = boot
    fat = [0] * (COUNT + 2)
    fat[0], fat[1], fat[2], fat[3] = 0xFFFFFFF8, END, END, END
    fat[7], fat[9], fat[10], fat[12] = 9, 10, 12, END
    fat[13], fat[14] = 14, END
    for k, v in (fat_patch or {}).items():
        fat[k] = v
    img[FAT_AT * SECTOR:FAT_AT * SECTOR + len(fat) * 4] = struct.pack(f"<{len(fat)}I", *fat)

    def put(cluster, data):
        at = HEAP_AT * SECTOR + (cluster - 2) * CLUSTER
        img[at:at + len(data)] = data

    used = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    bitmap = bytearray(COUNT // 8)
    for c in used:
        bitmap[(c - 2) // 8] |= 1 << ((c - 2) % 8)
    put(2, bitmap)
    bitmap_entry = bytearray(32)
    bitmap_entry[0] = 0x81
    struct.pack_into("<IQ", bitmap_entry, 20, 2, len(bitmap))
    deleted = bytearray(file_set("gone.txt", 5000, 20, True))
    deleted[0], deleted[32], deleted[64] = 0x05, 0x40, 0x41
    put(3, bytes(bitmap_entry) + file_set("A.bin", 3 * CLUSTER, 4, True) + bytes(deleted)
        + file_set("B with a long name that needs two name entries.bin", 4 * CLUSTER - 100, 7, False)
        + file_set("D", CLUSTER, 8, True, directory=True) + file_set("empty.txt", 0, 0, False))
    put(8, file_set("C.bin", 2 * CLUSTER, 13, False))
    return bytes(img)


class Scan(unittest.TestCase):
    def scan(self, image):
        with Folder() as d:
            path = os.path.join(d, "volume.img")
            with open(path, "wb") as f:
                f.write(image)
            os.chmod(path, 0o444)                     # the scan must manage with a read-only file
            result = exfat.scan(path)
            with open(path, "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).digest(), hashlib.sha256(image).digest())
            os.chmod(path, 0o644)
        return result

    def test_counts(self):
        r = self.scan(build())
        self.assertEqual({k: r[k] for k in ("cluster_bytes", "clusters", "used_clusters", "files", "folders",
                                            "fragmented_files", "pieces")},
                         {"cluster_bytes": 4096, "clusters": 64, "used_clusters": 13, "files": 4, "folders": 1,
                          "fragmented_files": 1, "pieces": 5})
        self.assertEqual(r["worst"], [(3, 4 * CLUSTER - 100, "/B with a long name that needs two name entries.bin")])
        self.assertEqual(r["fragmented_bytes"], 4 * CLUSTER - 100)

    def test_map(self):
        s = self.scan(build())["state"].tolist()
        W, F, O = exfat.WHOLE, exfat.FRAGMENTED, exfat.OTHER
        #                    2  3  4  5  6  7  8  9 10 11 12 13 14
        self.assertEqual(s[:13], [O, O, W, W, W, F, O, F, F, O, F, W, W])
        self.assertEqual(set(s[13:]), {exfat.FREE})
        self.assertEqual(list(exfat.ranges(self.scan(build())["state"]))[:4],
                         [(0, 2, "other"), (2, 3, "whole"), (5, 1, "fragmented"), (6, 1, "other")])

    def test_a_looping_table_is_refused_not_followed_for_ever(self):
        # file B is 4 clusters long, so a loop inside it is cut by its length; the root directory has no length
        with self.assertRaises(exfat.ExfatError):
            self.scan(build(fat_patch={3: 3}))
        r = self.scan(build(fat_patch={12: 7}))
        self.assertEqual(r["worst"][0][0], 3)

    def test_not_exfat_is_refused(self):
        with self.assertRaises(exfat.ExfatError):
            self.scan(build(magic=b"NTFS    "))
        with self.assertRaises(exfat.ExfatError):
            self.scan(b"\0" * 100)


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_json_and_words(self):
        with Folder() as d:
            path = os.path.join(d, "volume.img")
            with open(path, "wb") as f:
                f.write(build())
            code, out = self.run_cli("fragscan", path, "--json")
            events = [json.loads(line) for line in out.splitlines()]
            self.assertEqual(code, 0)
            self.assertEqual([p for e in events for p in contract.problems(e)], [])
            summary = next(e for e in events if e["event"] == "frag_summary")
            self.assertEqual((summary["files"], summary["fragmented_files"], summary["clusters"]), (4, 1, 64))
            blocks = [e for e in events if e["event"] == "frag_blocks"]
            self.assertEqual(sum(e["count"] for e in blocks), 64)
            code, out = self.run_cli("fragscan", path)
            self.assertIn("1 of 4 files", out)
            code, out = self.run_cli("fragscan", os.path.join(d, "missing.img"), "--json")
            self.assertEqual((code, json.loads(out.splitlines()[-1])["event"]), (3, "error"))
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
