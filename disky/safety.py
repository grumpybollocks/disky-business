# SPDX-License-Identifier: GPL-3.0-or-later
"""Rules that keep a test from hurting the running system."""
import os

GIB = 1 << 30
# A first choice; it may change.
RESERVE_FRACTION = 0.05
RESERVE_MIN = 2 * GIB


def _unescape(text):
    return text.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def mount_source(path, mountinfo="/proc/self/mountinfo"):
    """Return the device a path lives on, by longest matching mount point."""
    path = os.path.realpath(path)
    best, source = "", None
    with open(mountinfo) as f:
        for line in f:
            left, _, right = line.partition(" - ")
            point = _unescape(left.split()[4])
            if (path == point or path.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
                best, source = point, right.split()[1]
    return source


MEMORY_FS = {"tmpfs", "ramfs", "devtmpfs"}


def is_system_drive(path, mountinfo="/proc/self/mountinfo", home=None):
    """True when the folder is on the device that holds / or the home folder, or lives in memory (tmpfs),
    where filling it would fill the computer's memory."""
    from .mounts import holding
    if holding(path, mountinfo)[1] in MEMORY_FS:
        return True
    here = mount_source(path, mountinfo)
    system = {mount_source("/", mountinfo), mount_source(home or os.path.expanduser("~"), mountinfo)}
    return here is not None and here in system


def system_reserve(total_bytes):
    """Free space to leave on a system drive."""
    return max(int(total_bytes * RESERVE_FRACTION), RESERVE_MIN)
