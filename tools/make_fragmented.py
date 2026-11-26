#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make one test file that is fragmented on purpose, for trying the defragmenter.

    python3 tools/make_fragmented.py <new file> [--size 64M] [--piece 256K]

Writes the file and a spacer file in turns, forcing each turn to the drive, then removes the spacer; on
copy-on-write file systems it also rewrites every other piece. Only these two files of its own are touched.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from disky.cli import parse_size  # noqa: E402


def make(path, size, piece):
    spacer = path + ".spacer"
    for p in (path, spacer):
        if os.path.lexists(p):
            raise FileExistsError(p)
    a = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    b = os.open(spacer, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        for _ in range(size // piece):
            os.write(a, os.urandom(piece))
            os.fsync(a)
            os.write(b, os.urandom(piece))
            os.fsync(b)
        for i in range(0, size // piece, 2):        # copy-on-write: a rewritten piece lands somewhere new
            os.pwrite(a, os.urandom(piece), i * piece)
            os.fsync(a)
    finally:
        os.close(a)
        os.close(b)
        os.unlink(spacer)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--size", type=parse_size, default=64 << 20)
    ap.add_argument("--piece", type=parse_size, default=256 << 10)
    args = ap.parse_args()
    make(args.file, args.size, args.piece)
    print("made", args.file)
