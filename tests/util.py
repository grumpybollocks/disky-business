# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers. Tests need a folder on a real file system, not a RAM disk."""
import os
import shutil
import tempfile

MIB = 1 << 20
FILE = 4 * MIB      # small "1 GiB" so tests cover several files quickly
SIZE = 12 * MIB     # three files


def fs_type(path):
    best, kind = "", ""
    path = os.path.realpath(path)
    with open("/proc/self/mountinfo") as f:
        for line in f:
            left, _, right = line.partition(" - ")
            point = left.split()[4]
            if (path == point or path.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
                best, kind = point, right.split()[0]
    return kind


def base_dir():
    chosen = os.environ.get("DISKY_TEST_DIR")
    if chosen:
        return chosen
    if fs_type(tempfile.gettempdir()) != "tmpfs":
        return tempfile.gettempdir()
    cache = os.path.expanduser("~/.cache")
    os.makedirs(cache, exist_ok=True)
    return cache


class Folder:
    """A fresh folder for one test, removed afterwards (it only ever holds our own test files)."""

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="disky-test-", dir=base_dir())
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path)


def patch_file(path, offset, data):
    with open(path, "r+b") as f:
        f.seek(offset)
        f.write(data)


def read_at(path, offset, size):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)
