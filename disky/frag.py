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
ENCODED, SHARED = 0x8, 0x2000   # FIEMAP_EXTENT_ENCODED (compressed), FIEMAP_EXTENT_SHARED (snapshot, reflink copy)
# FIEMAP_EXTENT_UNKNOWN, _DELALLOC (not on the disk yet), _DATA_INLINE (inside metadata): no place on the disk
NO_PLACE = 0x2 | 0x4 | 0x200
_HEAD, _EXTENT, _BATCH = 32, 56, 512

HANDS_OFF = {"ntfs", "ntfs3", "fuseblk"}       # never walked (fuseblk = ntfs-3g)

TOOLS = {                       # file system -> command that defragments one file
    "btrfs": ["btrfs", "filesystem", "defragment", "-f"],
    "ext4": ["e4defrag"],
    "xfs": ["xfs_fsr"],
}


class NotSupported(Exception):
    """This file system has no extent map for normal users, or no defragmenter we trust."""


class Shared(NotSupported):
    """Part of the file is shared with a snapshot or a copy; defragmenting would store that part twice."""


def _same_device(path, device):
    """A real folder (not a link) on `device`; False when it vanished or cannot be read meanwhile."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    import stat
    return stat.S_ISDIR(st.st_mode) and st.st_dev == device


def _stamp(path):
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


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
                out.append((logical, physical, length, flags))
                start = logical + length
                last = bool(flags & _LAST)
            if last:
                return out
    finally:
        os.close(fd)


def pieces(ext):
    """Number of pieces after joining extents that touch on the drive."""
    n, start, end, packed = 0, None, None, False
    for e in ext:
        physical, length = e[1], e[2]
        # A compressed extent takes less room on the drive than its length (the length the kernel gives is
        # the uncompressed one), so the next extent starts inside that span when it follows right behind.
        follows = physical == end or (packed and start < physical <= end)
        if not follows:
            n += 1
        start, end, packed = physical, physical + length, bool(len(e) > 3 and e[3] & ENCODED)
    return n


def shared(ext):
    """True when part of the file is shared with a snapshot or a copy: defragmenting would unshare it and
    store that part a second time."""
    return any(len(e) > 3 and e[3] & SHARED for e in ext)


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
    stamp, ext = _stamp(path), extents(path)
    if shared(ext):
        raise Shared(f"{path} shares its data with a snapshot or a copy. Defragmenting would store it a second "
                     "time and use more space, so it was left as it is.")
    before, digest = pieces(ext), sha256(path)
    done = run(tool + [path], capture_output=True, text=True, timeout=timeout, stop=stop)
    _drop_cache(path)                   # flushes this file only, then the "after" check reads it from the drive
    after = pieces(extents(path))
    if sha256(path) != digest:
        # Either way nothing more is touched. Size or time changed too: most likely another program wrote to
        # it (a browser, a database); both unchanged: only the tool can have done it.
        if _stamp(path) != stamp:
            raise RuntimeError(f"The content of {path} changed while it was being defragmented, and so did its "
                               "size or time: most likely another program was writing to it (a browser or a "
                               "database, for example). Nothing more was touched. Close the programs that use "
                               "files here, then run it again.")
        raise RuntimeError(f"The content of {path} changed during defragmenting. Stop using {tool[0]} on this drive.")
    return {"fs": kind, "tool": tool[0], "exit": done.returncode, "before": before, "after": after,
            "ended": getattr(done, "ended", ""),
            "message": _tool_words(done.stderr or done.stdout or "")}


def _tool_words(text):
    """The tool's own words on one line: its error lines when it gives some (btrfs adds 'total 1 failures')."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    errors = [line for line in lines if "error" in line.lower() or "fail" in line.lower() and "total" not in line]
    return "; ".join(errors or lines)[-300:]


LAYOUT_BLOCKS = 100_000         # at most this many blocks in the layout map
LAYOUT_STATES = ("free", "whole", "fragmented")     # by rank: a block shows the highest of what lies in it


def layout(spans, kind, fs_bytes, max_blocks=LAYOUT_BLOCKS):
    """Where the files lie, as a map: (base, block_bytes, numpy array of LAYOUT_STATES indexes).

    spans = [(physical, length, rank)]. On ext4 and XFS the places are offsets on the file system, which starts
    at 0 and is `fs_bytes` long. On btrfs they are places in btrfs's own address space, which does not start at
    0 and is not the order on the disk: the map then spans from the first to the last place found."""
    import numpy as np
    if kind == "btrfs" and spans:
        base = min(p for p, _, _ in spans)
        end = max(p + n for p, n, _ in spans)
    else:
        base = 0
        end = max([fs_bytes] + [p + n for p, n, _ in spans])
    span = max(end - base, 1)
    block = 4096
    while span // block > max_blocks:
        block *= 2
    state = np.zeros(-(-span // block), np.uint8)
    for p, n, rank in spans:
        a, b = (p - base) // block, (p - base + max(n, 1) - 1) // block + 1
        np.maximum(state[a:b], rank, out=state[a:b])
    return base, block, state


def layout_ranges(state):
    """Run-length form: (first block, count, state name), free runs left out."""
    import numpy as np
    if len(state) == 0:
        return
    cuts = np.flatnonzero(np.diff(state)) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [len(state)]))
    for a, b in zip(starts.tolist(), ends.tolist()):
        if state[a]:
            yield a, b - a, LAYOUT_STATES[int(state[a])]


def scan_folder(folder, worst=50, stop=None, with_layout=False):
    """Read-only: how fragmented the files under `folder` are. Stays on the folder's own file system.
    With `with_layout`, also where their pieces lie ("layout": see layout())."""
    import stat
    kind = fs_type(folder)
    if kind in HANDS_OFF:
        raise NotSupported("NTFS drives are not scanned: Linux may change a damaged NTFS drive just by reading it.")
    device = os.stat(folder).st_dev
    other_mounts = mounts.below(folder)     # never looked at, not even their top folder
    out = {"files": 0, "fragmented_files": 0, "pieces": 0, "file_bytes": 0, "fragmented_bytes": 0, "skipped": 0,
           "shared_files": 0, "shared_bytes": 0,     # fragmented files shared with a snapshot or a copy
           "skipped_files": []}                       # the first few that could not be read, and why
    top, spans, seen = [], [], set()

    def unreadable_folder(e):
        _skip(out, e.filename or "?", f"folder not opened: {e.strerror or e}")
    for root, dirs, names in os.walk(folder, onerror=unreadable_folder):
        if stop is not None and stop.is_set():
            break
        dirs[:] = [d for d in dirs if os.path.join(root, d) not in other_mounts
                   and _same_device(os.path.join(root, d), device)]
        for name in names:
            path = os.path.join(root, name)
            try:
                st = os.lstat(path)
                # Sizes count what lies on the disk: a sparse file (a disk image) can be far bigger than the
                # space it uses, and a file with several names (hard links) is counted once. Found 2026-11-27: a
                # 500 GB drive's home folder showed 1 TiB of files because of two sparse images.
                used = min(st.st_size, st.st_blocks * 512)
                if not stat.S_ISREG(st.st_mode) or used == 0:
                    continue
                if st.st_nlink > 1:
                    if st.st_ino in seen:
                        continue
                    seen.add(st.st_ino)
                ext = extents(path)
                n = pieces(ext)
            except NotSupported as e:
                if out["files"] == 0:
                    raise
                _skip(out, path, str(e))
                continue
            except OSError as e:
                _skip(out, path, e.strerror or str(e))
                continue
            out["files"] += 1
            out["pieces"] += n
            out["file_bytes"] += used
            if with_layout:
                spans.extend((p, length, 2 if n > 1 else 1) for _, p, length, *flags in ext
                             if not (flags and flags[0] & NO_PLACE))
            if n > 1:
                out["fragmented_files"] += 1
                out["fragmented_bytes"] += used
                common = shared(ext)
                if common:
                    out["shared_files"] += 1
                    out["shared_bytes"] += used
                top.append((n, used, path, common))
                if len(top) > 4 * worst:
                    top = sorted(top, reverse=True)[:worst]
    out["worst"] = [{"pieces": n, "bytes": b, "path": p, "shared": c} for n, b, p, c in sorted(top, reverse=True)[:worst]]
    if with_layout:
        vfs = os.statvfs(folder)
        out["layout"] = layout(spans, kind, vfs.f_blocks * vfs.f_frsize) + (kind,)
    return out


def _skip(out, path, why):
    out["skipped"] += 1
    if len(out["skipped_files"]) < 5:
        out["skipped_files"].append({"path": path, "why": why})


def defrag_all(folder, emit, stop=None, scan=None, one=None):
    """Defragment every fragmented file under `folder`, the most fragmented first, with the system's tool.

    Emits `defrag_progress` after each file and returns the totals. Stop ends it after the file in hand (the
    tool itself is also told to stop). Files are counted apart: `skipped` (the tool cannot handle it),
    `shared` (shared with a snapshot or a copy: never given to the tool, because it would store it a second
    time). `files` counts the files that were tried. If a file's
    content changed under the tool, RuntimeError goes up at once: nothing more is touched.
    """
    scan, one = scan or scan_folder, one or defrag_file
    everything = scan(folder, worst=10**9, stop=stop)["worst"]
    found = [w for w in everything if not w.get("shared")]    # never handed to the tool: it would unshare them
    out = {"files": len(found), "done": 0, "improved": 0, "skipped": 0, "pieces_before": 0, "pieces_after": 0,
           "stopped": False, "shared": len(everything) - len(found)}
    for i, item in enumerate(found):
        if stop is not None and stop.is_set():
            out["stopped"] = True
            break
        try:
            r = one(item["path"], stop=stop)
        except (NotSupported, OSError) as e:
            out["shared" if isinstance(e, Shared) else "skipped"] += 1
            emit({"event": "defrag_progress", "index": i + 1, "total": len(found), "path": item["path"],
                  "before": item["pieces"], "after": item["pieces"], "skipped": True})
            continue
        if r.get("exit", 0) != 0 and not r.get("ended"):  # the tool refused or failed: not done (content unchanged)
            out["skipped"] += 1
            emit({"event": "defrag_progress", "index": i + 1, "total": len(found), "path": item["path"],
                  "before": r["before"], "after": r["after"], "skipped": True})
            continue
        out["done"] += 1
        out["pieces_before"] += r["before"]
        out["pieces_after"] += r["after"]
        out["improved"] += r["after"] < r["before"]
        emit({"event": "defrag_progress", "index": i + 1, "total": len(found), "path": item["path"],
              "before": r["before"], "after": r["after"], "skipped": False})
        if r.get("ended") == "stopped":
            out["stopped"] = True
            break
    return out
