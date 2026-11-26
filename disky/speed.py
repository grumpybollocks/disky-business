# SPDX-License-Identifier: GPL-3.0-or-later
"""Speed test: one temporary file, four measurements. Events: docs/CONTRACT.md "Speed test".

  seq_write   the file is written front to back; every slice is flushed to the drive before its time is taken
  seq_read    the file is read back front to back, after the kernel was told to forget it
  rand_read   4 KiB reads at random places, past the kernel's cache where the file system allows it
  rand_write  4 KiB writes at random places, each one forced to the drive

The file is the engine's own and is always removed at the end, also after Stop or an error.
"""
import errno
import mmap
import os
import random
import time

from .engine import BLOCK, ExistingFiles, _read_bytes_counter

NAME = "disky-speed.tmp"
SLICE = 32 * BLOCK          # one sample on the sequential graphs
SMALL = 4096                # the "4K" of the random tests
READ_PROOF_PCT = 99.0
TESTS = ("seq_write", "seq_read", "rand_read", "rand_write")


def _aligned(n):
    """A buffer the kernel accepts for direct I/O (page-aligned), filled with random bytes."""
    buf = mmap.mmap(-1, n)
    buf.write(os.urandom(n))
    return buf


def _open_direct(path, flags):
    """Open past the cache if the file system can; say whether it worked."""
    try:
        return os.open(path, flags | os.O_DIRECT), True
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.ENOTSUP):
            raise
        return os.open(path, flags), False


def _free(folder):
    vfs = os.statvfs(folder)
    return vfs.f_bavail * vfs.f_frsize


def _room(folder, need, keep_free):
    """Refuse to write `need` bytes unless that leaves `keep_free` on the drive."""
    if _free(folder) - need < keep_free:
        raise OSError(errno.ENOSPC, "Not enough free space for the speed test", folder)


def _stopped(stop):
    return stop is not None and stop.is_set()


def _seq_write(path, size, emit, stop, clock, keep_free=0):
    emit({"event": "speed_phase", "test": "seq_write", "total_bytes": size, "seconds": None})
    data = os.urandom(BLOCK)
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    done, begin = 0, clock()
    try:
        while done < size and not _stopped(stop):
            t0, n = clock(), min(SLICE, size - done)
            _room(os.path.dirname(path), n, keep_free)      # checked again for every slice, not only at the start
            for _ in range(n // BLOCK):
                os.write(fd, data)
            os.fdatasync(fd)
            emit({"event": "speed_sample", "test": "seq_write", "offset_bytes": done,
                  "mib_per_s": round(n / BLOCK / max(clock() - t0, 1e-9), 1)})
            done += n
    finally:
        os.close(fd)
    return done, done / BLOCK / max(clock() - begin, 1e-9)


def _seq_read(path, size, emit, stop, clock):
    emit({"event": "speed_phase", "test": "seq_read", "total_bytes": size, "seconds": None})
    fd = os.open(path, os.O_RDONLY)
    before = _read_bytes_counter()
    done, begin = 0, clock()
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_SEQUENTIAL)
        while done < size and not _stopped(stop):
            t0, n = clock(), 0
            while n < min(SLICE, size - done):
                got = len(os.read(fd, BLOCK))
                if not got:
                    break
                n += got
            if not n:
                break
            emit({"event": "speed_sample", "test": "seq_read", "offset_bytes": done,
                  "mib_per_s": round(n / BLOCK / max(clock() - t0, 1e-9), 1)})
            done += n
    finally:
        os.close(fd)
    took, after = clock() - begin, _read_bytes_counter()
    pct = None if before is None or after is None or not done else round(100 * (after - before) / done, 1)
    return done / BLOCK / max(took, 1e-9), pct


def _random(path, size, test, seconds, emit, stop, clock, rng):
    emit({"event": "speed_phase", "test": test, "total_bytes": None, "seconds": seconds})
    writing = test == "rand_write"
    fd, direct = _open_direct(path, os.O_WRONLY if writing else os.O_RDONLY)
    buf = _aligned(SMALL)
    places = size // SMALL
    n, begin = 0, clock()
    try:
        if not direct and not writing:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_RANDOM)
        while clock() - begin < seconds and not _stopped(stop):
            at = rng.randrange(places) * SMALL
            if writing:
                os.pwritev(fd, [buf], at)
                if not direct:
                    os.fdatasync(fd)
            else:
                os.preadv(fd, [buf], at)
            n += 1
        if writing:
            os.fdatasync(fd)
    finally:
        os.close(fd)
        buf.close()
    took = max(clock() - begin, 1e-9)
    return n / took, n * SMALL / BLOCK / took, direct


def run(folder, emit, size=1 << 30, seconds=5.0, stop=None, clock=time.perf_counter, rng=None, keep_free=0):
    """Run the four measurements in `folder`. Returns {test: result dict}; emits events on the way."""
    path = os.path.join(folder, NAME)
    if os.path.lexists(path):
        raise ExistingFiles(path)
    size = max(SLICE, size // SLICE * SLICE)
    _room(folder, size, keep_free)
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))   # made by us, so ours to remove
    except FileExistsError:
        raise ExistingFiles(path) from None
    rng = rng or random.Random()
    results = {}

    def result(test, **fields):
        results[test] = fields
        emit({"event": "speed_result", "test": test, **fields})

    try:
        written, rate = _seq_write(path, size, emit, stop, clock, keep_free)
        if written == size:
            result("seq_write", mib_per_s=round(rate, 1), iops=None, direct=None, read_from_drive=None)
        if not _stopped(stop):
            rate, pct = _seq_read(path, size, emit, stop, clock)
            if not _stopped(stop):
                result("seq_read", mib_per_s=round(rate, 1), iops=None, direct=None,
                       read_from_drive=None if pct is None else pct >= READ_PROOF_PCT)
        for test in ("rand_read", "rand_write"):
            if _stopped(stop):
                break
            iops, rate, direct = _random(path, size, test, seconds, emit, stop, clock, rng)
            if not _stopped(stop):
                result(test, mib_per_s=round(rate, 2), iops=round(iops), direct=direct, read_from_drive=None)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    return results
