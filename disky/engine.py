# SPDX-License-Identifier: GPL-3.0-or-later
"""Fill a folder with test files and read them back. Events: docs/CONTRACT.md."""
import errno
import os
import queue
import re
import stat
import threading
import time

import numpy as np

from . import pattern

CONTRACT_VERSION = 1
GIB = 1 << 30
BLOCK = 1 << 20          # one map block = 1 MiB
CHUNK = 8 * BLOCK        # bytes made, written or read in one go
SYNC_EVERY = 64 * BLOCK  # flush this often so the speed shown is the drive's
READ_PROOF_PCT = 99.0    # below this the read-back did not come from the drive
_NAME = re.compile(r"^([1-9]\d*)\.h2w$")   # 1.h2w, 2.h2w ... never 0001.h2w


class ExistingFiles(Exception):
    """Test files are already in the folder; we never delete or overwrite them."""


def test_files(folder):
    """Return [(number, path)] for N.h2w files, in order. Number 0 is 1.h2w. Plain files only: never a link,
    a folder or anything else that only has the name."""
    found = []
    for name in os.listdir(folder):
        m = _NAME.match(name)
        if not m:
            continue
        path = os.path.join(folder, name)
        try:
            if stat.S_ISREG(os.lstat(path).st_mode):
                found.append((int(m.group(1)) - 1, path))
        except OSError:
            continue
    return sorted(found)


def is_test_data(path):
    """True when the file is empty or its first sectors hold test data (for any place on the drive).

    Used before removing a file, so a file that only has a test file's name is kept."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        head = os.pread(fd, 8 * pattern.SECTOR, 0)
    except OSError:
        return False
    finally:
        os.close(fd)
    if not head:
        return True
    whole = len(head) - len(head) % pattern.SECTOR
    if not whole:
        return False
    return bool((pattern.classify(head[:whole], 0) != pattern.BAD).any())


def _free(folder):
    vfs = os.statvfs(folder)
    return vfs.f_bavail * vfs.f_frsize


def _read_bytes_counter():
    """Bytes this process made the kernel fetch from storage, or None."""
    try:
        with open("/proc/self/io") as f:
            for line in f:
                if line.startswith("read_bytes:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


class _Progress:
    def __init__(self, emit, phase, total):
        self.emit, self.phase, self.total = emit, phase, total
        self.start = self.last_t = time.monotonic()
        self.done = self.last_done = 0

    def add(self, n, force=False):
        self.done += n
        now = time.monotonic()
        if not force and now - self.last_t < 0.5:
            return
        span = max(now - self.last_t, 1e-9)
        avg = self.done / max(now - self.start, 1e-9)
        left = None if self.total is None or avg <= 0 else round((self.total - self.done) / avg)
        self.emit({
            "event": "progress", "phase": self.phase, "done_bytes": self.done,
            "total_bytes": self.total,
            "mib_per_s": round((self.done - self.last_done) / span / BLOCK, 1),
            "avg_mib_per_s": round(avg / BLOCK, 1), "eta_s": left,
        })
        self.last_t, self.last_done = now, self.done


def _blocks(emit, phase, first, count, state, sectors=None, first_bad_offset=None):
    ev = {"event": "blocks", "phase": phase, "first": first, "count": count, "state": state}
    if sectors is not None:
        ev["sectors"] = sectors
        ev["first_bad_offset"] = first_bad_offset
    emit(ev)


def _maker(jobs, out, stop):
    """Second thread: make the data while the first thread writes it."""
    try:
        for offset, size in jobs:
            if stop.is_set():
                break
            out.put(pattern.make_view(offset, size))
    finally:
        out.put(None)


def write(folder, emit, size=None, reserve=0, file_bytes=GIB, stop=None):
    """Fill the folder with test files. Returns the number of bytes written.

    size: stop after this many bytes (None = until the drive is full, like f3).
    reserve: bytes of free space to leave alone.
    """
    stop = stop or threading.Event()
    if test_files(folder):
        raise ExistingFiles(folder)
    vfs = os.statvfs(folder)
    free = vfs.f_bavail * vfs.f_frsize
    target = None
    if size is not None or reserve:
        target = max(0, free - reserve)
        if size is not None:
            target = min(target, size)
        target -= target % pattern.SECTOR
    # Filling until full: the free space is the best guess of the total.
    goal = target if target is not None else free
    emit({"event": "phase", "phase": "write", "total_bytes": goal, "free_bytes": free})
    prog = _Progress(emit, "write", goal)
    total = 0
    number = 0
    full = False
    while not full and not stop.is_set() and (target is None or total < target):
        want = file_bytes if target is None else min(file_bytes, target - total)
        path = os.path.join(folder, f"{number + 1}.h2w")
        base = number * file_bytes
        jobs = [(base + o, min(CHUNK, want - o)) for o in range(0, want, CHUNK)]
        made = queue.Queue(maxsize=4)
        halt = threading.Event()
        maker = threading.Thread(target=_maker, args=(jobs, made, halt), daemon=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                break
            raise
        emit({"event": "file", "phase": "write", "name": os.path.basename(path)})
        maker.start()
        in_file = unsynced = 0
        try:
            while True:
                data = made.get()
                if data is None:
                    break
                view = data
                sent = 0
                while sent < len(view):
                    if reserve and _free(folder) - (len(view) - sent) < reserve:
                        # Something else used the space meanwhile: stop here, keep what was asked to stay free.
                        emit({"event": "warning", "code": "reserve_reached",
                              "message": "Stopped writing early to keep the free space this drive must keep."})
                        full = True
                        break
                    try:
                        n = os.write(fd, view[sent:])
                    except OSError as e:
                        if e.errno != errno.ENOSPC:
                            raise
                        n = 0
                    if n == 0:
                        full = True
                        break
                    sent += n
                if sent:
                    first = (base + in_file) // BLOCK
                    last = (base + in_file + sent - 1) // BLOCK
                    _blocks(emit, "write", first, last - first + 1, "written")
                in_file += sent
                unsynced += sent
                prog.add(sent)
                if unsynced >= SYNC_EVERY:
                    try:
                        os.fdatasync(fd)
                    except OSError as e:
                        if e.errno != errno.ENOSPC:
                            raise
                        full = True             # the drive found it was full while flushing
                    unsynced = 0
                if full or stop.is_set():
                    break
        finally:
            halt.set()
            while maker.is_alive():      # unblock the maker if it waits on a full queue
                try:
                    made.get(timeout=0.05)
                except queue.Empty:
                    pass
            keep = in_file - in_file % pattern.SECTOR
            if keep != in_file:
                os.ftruncate(fd, keep)   # our own file: keep whole sectors only
                in_file = keep
            try:
                try:
                    os.fdatasync(fd)
                except OSError as e:
                    if e.errno != errno.ENOSPC:
                        raise
                    full = True
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(fd)
        total += in_file
        number += 1
    prog.add(0, force=True)
    return total


def verify(folder, emit, expect_bytes=None, drop_cache=True, file_bytes=GIB, stop=None):
    """Read every test file back and check it. Returns the summary event."""
    stop = stop or threading.Event()
    files = test_files(folder)
    total = sum(os.path.getsize(p) for _, p in files)
    emit({"event": "phase", "phase": "verify", "total_bytes": total, "free_bytes": None})
    prog = _Progress(emit, "verify", total)
    counts = [0, 0, 0, 0]
    missing = 0
    read_total = 0
    first_bad = None
    have = {n for n, _ in files}
    for gone in (n for n in range(max(have, default=-1)) if n not in have):
        # A file in the middle of the numbering is gone: its whole size was lost.
        missing += file_bytes
        first_bad = gone * file_bytes if first_bad is None else min(first_bad, gone * file_bytes)
        emit({"event": "warning", "code": "missing_file", "message": f"{gone + 1}.h2w is missing"})
    counter_start = _read_bytes_counter()
    for idx, (number, path) in enumerate(files):
        if stop.is_set():
            break
        base = number * file_bytes
        emit({"event": "file", "phase": "verify", "name": os.path.basename(path)})
        fd = os.open(path, os.O_RDONLY)
        try:
            if drop_cache:
                os.fdatasync(fd)
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_SEQUENTIAL)
            pos = 0
            while not stop.is_set():
                buf = os.read(fd, CHUNK)
                if not buf:
                    break
                while len(buf) % pattern.SECTOR:     # short read: finish the sector
                    more = os.read(fd, pattern.SECTOR - len(buf) % pattern.SECTOR)
                    if not more:
                        break
                    buf += more
                whole = len(buf) - len(buf) % pattern.SECTOR
                states = pattern.classify(buf[:whole], base + pos)
                run_first, run_len = None, 0
                per_block = BLOCK // pattern.SECTOR
                for b in range(0, len(states), per_block):
                    part = states[b:b + per_block]
                    index = (base + pos + b * pattern.SECTOR) // BLOCK
                    if not part.any():
                        counts[0] += len(part)
                        if run_first is None:
                            run_first = index
                        run_len += 1
                        continue
                    if run_first is not None:
                        _blocks(emit, "verify", run_first, run_len, "good")
                        run_first, run_len = None, 0
                    tally = np.bincount(part, minlength=4)
                    for s in range(4):
                        counts[s] += int(tally[s])
                    bad_at = base + pos + (b + int(np.nonzero(part)[0][0])) * pattern.SECTOR
                    if first_bad is None:
                        first_bad = bad_at
                    worst = "bad" if tally[2] else ("overwritten" if tally[3] else "changed")
                    _blocks(emit, "verify", index, 1, worst,
                            {n: int(tally[i]) for i, n in enumerate(pattern.STATE_NAMES)}, bad_at)
                if run_first is not None:
                    _blocks(emit, "verify", run_first, run_len, "good")
                missing += len(buf) - whole          # a torn sector at the end of a file
                pos += len(buf)
                read_total += len(buf)
                prog.add(len(buf))
            if not stop.is_set() and idx < len(files) - 1 and pos < file_bytes:
                # Every file but the last must be full; a short one lost data.
                gap = file_bytes - pos
                missing += gap
                if first_bad is None:
                    first_bad = base + pos
                emit({"event": "warning", "code": "short_file",
                      "message": f"{os.path.basename(path)} is {gap} bytes short"})
        finally:
            os.close(fd)
    prog.add(0, force=True)
    if expect_bytes is not None and read_total < expect_bytes:
        missing = max(missing, expect_bytes - read_total)
    counter_end = _read_bytes_counter()
    pct = None
    if counter_start is not None and counter_end is not None and read_total:
        pct = round((counter_end - counter_start) * 100 / read_total, 1)
    from_drive = pct is not None and pct >= READ_PROOF_PCT
    damaged = counts[1] + counts[2] + counts[3] or missing
    if stop.is_set():
        verdict = "stopped"
    elif damaged:
        verdict = "bad"
    elif not files or read_total == 0:
        verdict = "nothing_to_check"
    elif not from_drive:
        verdict = "not_verified"
    else:
        verdict = "good"
    summary = {
        "event": "summary", "verdict": verdict, "bytes_read": read_total,
        "sectors": {n: counts[i] for i, n in enumerate(pattern.STATE_NAMES)},
        "missing_bytes": missing, "first_bad_offset": first_bad,
        "read_from_drive_pct": pct, "read_from_drive": from_drive,
    }
    emit(summary)
    return summary
