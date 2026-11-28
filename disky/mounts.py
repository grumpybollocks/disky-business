# SPDX-License-Identifier: GPL-3.0-or-later
"""The mount table (/proc/self/mountinfo), with mount paths decoded the way the kernel encodes them."""
import os
import re

_OCTAL = re.compile(r"\\([0-7]{3})")


def unescape(text):
    """The kernel writes space, tab, newline and backslash in paths as \\040, \\011, \\012 and \\134."""
    return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), text)


def table(mountinfo="/proc/self/mountinfo"):
    """Yield (mount point, file system type, device) for every mount."""
    with open(mountinfo) as f:
        for line in f:
            left, _, right = line.partition(" - ")
            head, r = left.split(), right.split()
            if len(head) < 5 or not r:
                continue                            # not a line the kernel writes: skipped, never a crash
            yield unescape(head[4]), r[0], r[1] if len(r) > 1 else ""


def holding(path, mountinfo="/proc/self/mountinfo"):
    """(mount point, file system type, device) of the mount that holds `path`; ("", "", None) if none."""
    path = os.path.realpath(path)
    best = ("", "", None)
    for point, kind, device in table(mountinfo):
        if (path == point or path.startswith(point.rstrip("/") + "/")) and len(point) >= len(best[0]):
            best = (point, kind, device)
    return best


def below(folder, mountinfo="/proc/self/mountinfo"):
    """Mount points strictly inside `folder`."""
    top = os.path.realpath(folder).rstrip("/") + "/"
    return {point for point, _, _ in table(mountinfo) if point.startswith(top)}
