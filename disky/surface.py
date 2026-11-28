# SPDX-License-Identifier: GPL-3.0-or-later
"""Surface scan: read a whole drive from first block to last, time every block, and say which ones were slow or
could not be read.

Only reads. The drive is opened read-only and nothing is ever written to it; a mounted drive can be scanned.
Reading a raw drive needs admin rights, so the window runs this through the admin session; because of that
the device name gets the same strict check as the SMART read. The read goes past the system's cache (direct
reading) where the drive allows it, so the drive itself is timed and not the memory.

What the words mean, per 1 MiB block:
  fast        read in good time
  slow        took over SLOW_TIMES this drive's usual time for a block, and at least SLOW_FLOOR seconds
  very_slow   took over VERY_SLOW seconds: the drive had to try hard
  unreadable  the drive reported an error; the scan goes on with the next block
A drive with an NTFS partition is refused: this program leaves Windows drives alone.
"""
import collections
import errno
import mmap
import os
import stat
import statistics
import time

from . import proc
from .smartread import device_path

BLOCK = 1 << 20
SLOW_TIMES, SLOW_FLOOR, VERY_SLOW = 4, 0.05, 0.5
USUAL_OVER = 200                # the usual time is the median of this many blocks just read
USUAL_FROM = 20                 # and is trusted once this many were read
REPORT_EVERY = 0.25             # seconds between reports to the window
STATES = ("fast", "slow", "very_slow", "unreadable")


def grade(seconds, usual):
    """The word for a block that was read in `seconds`, given the drive's usual time (None: not known yet)."""
    if seconds >= VERY_SLOW:
        return "very_slow"
    if usual is not None and seconds >= SLOW_FLOOR and seconds > SLOW_TIMES * usual:
        return "slow"
    return "fast"


def file_systems(path, run=proc.run):
    """The file systems on a drive and its partitions, as lsblk names them (lower case)."""
    done = run(["lsblk", "-n", "-o", "FSTYPE", path], capture_output=True, text=True, timeout=30)
    return {line.strip().lower() for line in (done.stdout or "").splitlines() if line.strip()}


class Reader:
    """A drive opened read-only. `read(offset)` reads one block there and returns how many bytes came."""

    def __init__(self, path):
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        try:
            self.fd = os.open(path, flags | getattr(os, "O_DIRECT", 0))
            self.direct = bool(getattr(os, "O_DIRECT", 0))
        except OSError as e:
            if e.errno != errno.EINVAL:
                raise
            self.fd = os.open(path, flags)              # this drive does not take direct reading
            self.direct = False
        self.size = os.lseek(self.fd, 0, os.SEEK_END)
        self.buffer = mmap.mmap(-1, BLOCK)              # page-aligned, as direct reading demands

    def read(self, offset):
        return os.preadv(self.fd, [self.buffer], offset)

    def close(self):
        os.close(self.fd)
        self.buffer.close()


def scan(device, emit, stop, reader=None, clock=time.monotonic, is_root=None, is_block=None, fs_of=file_systems):
    """Read the whole drive, reporting as it goes. Returns the result (a dict, also sent as `surface_result`).
    Raises PermissionError and ValueError before anything is read."""
    path = device_path(device)
    if not (os.geteuid() == 0 if is_root is None else is_root):
        raise PermissionError("The surface scan needs admin rights.")
    block = is_block or (lambda p: os.path.exists(p) and stat.S_ISBLK(os.stat(p).st_mode))
    if not block(path):
        raise ValueError(f"{path} is not a drive (was it unplugged?).")
    if "ntfs" in fs_of(path):
        raise ValueError("This drive holds a Windows (NTFS) partition. This program leaves such drives alone.")
    r = reader(path) if reader else Reader(path)
    try:
        return _scan(os.path.basename(path), r, emit, stop, clock)
    finally:
        r.close()


def _scan(name, r, emit, stop, clock):
    total = r.size
    blocks = -(-total // BLOCK)
    emit({"event": "surface_start", "device": name, "total_bytes": total, "blocks": blocks,
          "direct": bool(getattr(r, "direct", False))})
    counts = dict.fromkeys(STATES, 0)
    recent = collections.deque(maxlen=USUAL_OVER)
    usual = None
    runs = []                                   # [first, count, state], not yet reported
    slowest, first_bad, read_bytes, index = 0.0, None, 0, 0
    began = last = clock()
    last_bytes = 0

    def report(now, final=False):
        nonlocal runs, last, last_bytes
        for first, count, state in runs:
            emit({"event": "surface_blocks", "first": first, "count": count, "state": state})
        runs = []
        done = min(index * BLOCK, total)
        span, whole = now - last, now - began
        rate = (done - last_bytes) / span / BLOCK if span > 0 else 0.0
        avg = done / whole / BLOCK if whole > 0 else 0.0
        emit({"event": "surface_progress", "done_bytes": done, "total_bytes": total, "mib_per_s": round(rate, 2),
              "eta_s": int((total - done) / (avg * BLOCK)) if avg > 0 and not final else None})
        last, last_bytes = now, done

    while index < blocks and not stop.is_set():
        offset = index * BLOCK
        want = min(BLOCK, total - offset)
        t0 = clock()
        try:
            got = r.read(offset)
            state = None if got >= want else "unreadable"       # a short read in the middle of a drive
        except OSError:
            got, state = 0, "unreadable"
        took = clock() - t0
        if state is None:
            state = grade(took, usual)
            recent.append(took)
            if len(recent) >= USUAL_FROM and index % 16 == 0:
                usual = statistics.median(recent)
            slowest = max(slowest, took)
        elif first_bad is None:
            first_bad = offset
        read_bytes += max(got, 0) if state != "unreadable" else 0
        counts[state] += 1
        if runs and runs[-1][2] == state and runs[-1][0] + runs[-1][1] == index:
            runs[-1][1] += 1
        else:
            runs.append([index, 1, state])
        index += 1
        now = clock()
        if now - last >= REPORT_EVERY:
            report(now)
    report(clock(), final=True)
    result = {"device": name, "total_bytes": total, "read_bytes": read_bytes, "blocks": counts,
              "slowest_ms": round(slowest * 1000, 1), "first_unreadable_offset": first_bad,
              "stopped": index < blocks}
    emit({"event": "surface_result", **result})
    return result
