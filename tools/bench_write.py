#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan step 1.8: is the engine as fast as plainly writing ready-made data?

Usage: python3 tools/bench_write.py <folder> [size in MiB, default 512] [rounds, default 3]
Both sides sync every 64 MiB and at the end. Rounds alternate. Test files are removed.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from disky import engine, pattern  # noqa: E402


def plain(folder, chunks):
    path = os.path.join(folder, "plain.bin")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    start = time.perf_counter()
    unsynced = 0
    for data in chunks:
        os.write(fd, data)
        unsynced += len(data)
        if unsynced >= engine.SYNC_EVERY:
            os.fdatasync(fd)
            unsynced = 0
    os.fdatasync(fd)
    took = time.perf_counter() - start
    os.close(fd)
    os.unlink(path)
    return took


def ours(folder, size):
    start = time.perf_counter()
    engine.write(folder, lambda ev: None, size=size)
    took = time.perf_counter() - start
    for _, path in engine.test_files(folder):
        os.unlink(path)
    return took


def main():
    size = int(sys.argv[2]) << 20 if len(sys.argv) > 2 else 512 << 20
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    folder = tempfile.mkdtemp(prefix="disky-bench-", dir=sys.argv[1])
    chunks = [pattern.make(o, engine.CHUNK) for o in range(0, size, engine.CHUNK)]
    mib = size / (1 << 20)
    ratios = []
    try:
        for r in range(rounds):
            p, o = mib / plain(folder, chunks), mib / ours(folder, size)
            ratios.append(o / p * 100)
            print(f"round {r}: plain {p:.0f} MiB/s, engine {o:.0f} MiB/s, {ratios[-1]:.0f} %")
    finally:
        for name in os.listdir(folder):
            os.unlink(os.path.join(folder, name))
        os.rmdir(folder)
    print(f"engine reaches {min(ratios):.0f}-{max(ratios):.0f} % of plain write speed (gate: 90 %)")


if __name__ == "__main__":
    main()
