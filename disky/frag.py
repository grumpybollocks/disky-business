# SPDX-License-Identifier: GPL-3.0-or-later
"""Fragmentation of files on Linux file systems, and defragmenting them with the system's own tools.

Where a file's pieces lie comes from the kernel (the FIEMAP call, the one `filefrag` uses); no root needed on
ext4, btrfs and XFS. Defragmenting is never done by our own code: it calls `btrfs filesystem defragment`,
`e4defrag` or `xfs_fsr`, and proves afterwards that the file's content is unchanged.
"""
import fcntl
import hashlib
import os
import shutil
import struct

from . import mounts, proc

FS_IOC_FIEMAP = 0xC020660B
_SYNC, _LAST = 1, 1
_HEAD, _EXTENT, _BATCH = 32, 56, 512

HANDS_OFF = {"ntfs", "ntfs3", "fuseblk"}       # never walked (fuseblk = ntfs-3g)

TOOLS = {                       # file system -> command that defragments one file
    "btrfs": ["btrfs", "filesystem", "defragment", "-f"],
    "ext4": ["e4defrag"],
    "xfs": ["xfs_fsr"],
}


class NotSupported(Exception):
    """This file system has no extent map for normal users, or no defragmenter we trust."""


def fs_type(path, mountinfo="/proc/self/mountinfo"):
    return mounts.holding(path, mountinfo)[1]


def extents(path):
    """[(logical, physical, length)] in bytes for one file, in file order."""
    out = []
    fd = os.open(path, os.O_RDONLY)
    try:
        start = 0
        while True:
            buf = bytearray(struct.pack("<QQIIII", start, 0xFFFFFFFFFFFFFFFF - start, _SYNC, 0, _BATCH, 0)
                            + bytes(_EXTENT * _BATCH))
            try:
                fcntl.ioctl(fd, FS_IOC_FIEMAP, buf)
            except OSError as e:
                raise NotSupported(f"This file system does not say where a file's pieces lie ({e.strerror}).")
            mapped = struct.unpack_from("<I", buf, 20)[0]
            last = mapped == 0
            for i in range(mapped):
                logical, physical, length, _, _, flags = struct.unpack_from("<QQQQQI", buf, _HEAD + i * _EXTENT)
                out.append((logical, physical, length))
                start = logical + length
                last = bool(flags & _LAST)
            if last:
                return out
    finally:
        os.close(fd)


def pieces(ext):
    """Number of pieces after joining extents that touch on the drive."""
    n, end = 0, None
    for _, physical, length in ext:
        if physical != end:
            n += 1
        end = physical + length
    return n


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _drop_cache(path):
    """Forget the file's cached pages, so the next read comes from the drive."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def defrag_file(path, run=proc.run, timeout=3600, stop=None):
    """Defragment one file with the system's tool. Returns a dict with pieces before and after.

    Raises NotSupported for file systems without a trusted tool, and RuntimeError if the content changed
    (which would be a bug in the system's tool and must never be hidden).
    """
    if not os.path.isfile(path) or os.path.islink(path):
        raise NotSupported("Only ordinary files are defragmented.")
    kind = fs_type(path)
    tool = TOOLS.get(kind)
    if tool is None:
        raise NotSupported(f"No trusted defragmenter for {kind or 'this file system'}.")
    if shutil.which(tool[0]) is None:
        raise NotSupported(f"The system tool {tool[0]} is not installed.")
    before, digest = pieces(extents(path)), sha256(path)
    done = run(tool + [path], capture_output=True, text=True, timeout=timeout, stop=stop)
    _drop_cache(path)                   # flushes this file only, then the "after" check reads it from the drive
    after = pieces(extents(path))
    if sha256(path) != digest:
        raise RuntimeError(f"The content of {path} changed during defragmenting. Stop using {tool[0]} on this drive.")
    return {"fs": kind, "tool": tool[0], "exit": done.returncode, "before": before, "after": after,
            "ended": getattr(done, "ended", ""),
            "message": (done.stderr or done.stdout).strip()[-300:]}


def scan_folder(folder, worst=50, stop=None):
    """Read-only: how fragmented the files under `folder` are. Stays on the folder's own file system."""
    import stat
    if fs_type(folder) in HANDS_OFF:
        raise NotSupported("NTFS drives are not scanned: Linux may change a damaged NTFS drive just by reading it.")
    device = os.stat(folder).st_dev
    other_mounts = mounts.below(folder)     # never looked at, not even their top folder
    out = {"files": 0, "fragmented_files": 0, "pieces": 0, "file_bytes": 0, "fragmented_bytes": 0, "skipped": 0}
    top = []
    for root, dirs, names in os.walk(folder):
        if stop is not None and stop.is_set():
            break
        dirs[:] = [d for d in dirs if os.path.join(root, d) not in other_mounts
                   and not os.path.islink(os.path.join(root, d)) and os.lstat(os.path.join(root, d)).st_dev == device]
        for name in names:
            path = os.path.join(root, name)
            try:
                st = os.lstat(path)
                if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
                    continue
                n = pieces(extents(path))
            except NotSupported:
                if out["files"] == 0:
                    raise
                out["skipped"] += 1
                continue
            except OSError:
                out["skipped"] += 1
                continue
            out["files"] += 1
            out["pieces"] += n
            out["file_bytes"] += st.st_size
            if n > 1:
                out["fragmented_files"] += 1
                out["fragmented_bytes"] += st.st_size
                top.append((n, st.st_size, path))
                if len(top) > 4 * worst:
                    top = sorted(top, reverse=True)[:worst]
    out["worst"] = [{"pieces": n, "bytes": b, "path": p} for n, b, p in sorted(top, reverse=True)[:worst]]
    return out
