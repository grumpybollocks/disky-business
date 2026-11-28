# SPDX-License-Identifier: GPL-3.0-or-later
"""TRIM: does a drive accept it, and run the system's `fstrim` on it.

`info()` only reads what the kernel publishes about the drive (no root). `run()` calls `fstrim`, which tells
the drive which space the file system already counts as free; it never touches files. `fstrim` needs root,
so the window starts this command through the system's password prompt.
"""
import os
import re
import subprocess

from . import mounts, proc

_SYS = "/sys/class/block"


def mount_of(path, mountinfo="/proc/self/mountinfo"):
    """(mount point, device) that holds `path`."""
    point, _kind, device = mounts.holding(path, mountinfo)
    return point, device


def auto_discard(point, mountinfo="/proc/self/mountinfo"):
    """True when the file system mounted at `point` already trims by itself as files are deleted (mounted with
    the `discard` option, e.g. btrfs `discard=async`)."""
    found = False
    try:
        with open(mountinfo) as f:
            for line in f:                          # the last mount at a point is the one in use (over-mounts)
                left, _, right = line.partition(" - ")
                fields = left.split()
                if len(fields) > 5 and mounts.unescape(fields[4]) == point:
                    opts = fields[5].split(",") + (right.split()[2].split(",") if len(right.split()) > 2 else [])
                    found = any(o == "discard" or o.startswith("discard=") for o in opts)
    except OSError:
        pass
    return found


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def info(path, mountinfo="/proc/self/mountinfo", sys_root=_SYS):
    """What the kernel says about TRIM for the drive that holds `path`."""
    point, device = mount_of(path, mountinfo)
    out = {"mount": point, "device": device or "", "supported": False, "discard_max_bytes": 0,
           "discard_granularity": 0, "rotational": None, "auto_discard": auto_discard(point, mountinfo)}
    if not device or not device.startswith("/dev/"):
        return out
    node = os.path.join(sys_root, os.path.basename(os.path.realpath(device)))
    real = os.path.realpath(node)
    disk = os.path.dirname(real) if os.path.exists(os.path.join(real, "partition")) else real
    queue = os.path.join(disk, "queue")
    top = _read(os.path.join(queue, "discard_max_bytes"))
    gran = _read(os.path.join(queue, "discard_granularity"))
    rot = _read(os.path.join(queue, "rotational"))
    out["discard_max_bytes"] = int(top) if top and top.isdigit() else 0
    out["discard_granularity"] = int(gran) if gran and gran.isdigit() else 0
    out["rotational"] = None if rot is None else rot == "1"
    out["supported"] = out["discard_max_bytes"] > 0
    return out


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_DAY = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _day(stamp):
    """'Mon 2026-11-23 00:45:25 EET' -> '23 November 2026'; '' when there is none."""
    m = _DAY.search(stamp or "")
    return f"{int(m[3])} {_MONTHS[int(m[2]) - 1]} {m[1]}" if m and 1 <= int(m[2]) <= 12 else ""


def weekly(run=subprocess.run):
    """Linux's own weekly TRIM (systemd's fstrim.timer). It trims every mounted file system that accepts TRIM.
    {"on": True/False, or None when it cannot be read, "last": day it last ran or "", "next": next day or ""}."""
    try:
        out = run(["systemctl", "show", "fstrim.timer", "-p", "ActiveState", "-p", "LastTriggerUSec",
                   "-p", "NextElapseUSecRealtime"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {"on": None, "last": "", "next": ""}
    facts = dict(line.split("=", 1) for line in (out.stdout or "").splitlines() if "=" in line)
    if out.returncode != 0 or "ActiveState" not in facts:
        return {"on": None, "last": "", "next": ""}
    return {"on": facts["ActiveState"] == "active", "last": _day(facts.get("LastTriggerUSec")),
            "next": _day(facts.get("NextElapseUSecRealtime"))}


_TRIMMED = re.compile(r"\((\d+) bytes\) trimmed")


def run(path, run=proc.run, is_root=None, info_of=info, stop=None):
    """Run `fstrim -v` on the mount that holds `path`. Returns {mount, trimmed_bytes, message}.

    Raises PermissionError without root, and ValueError when the drive does not accept TRIM; in both cases
    nothing is run.
    """
    facts = info_of(path)
    if not facts["supported"]:
        raise ValueError("This drive does not accept TRIM (or its USB case does not pass it on). Nothing was done.")
    if not (os.geteuid() == 0 if is_root is None else is_root):
        raise PermissionError("TRIM needs admin rights.")
    done = run(["fstrim", "-v", facts["mount"]], capture_output=True, text=True, timeout=3600, stop=stop)
    text = (done.stdout or done.stderr or "").strip()
    if getattr(done, "ended", ""):
        raise proc.Stopped("TRIM was stopped before it finished." if done.ended == "stopped"
                           else "TRIM took over an hour and was ended.")
    if done.returncode != 0:
        raise OSError(done.returncode, text or "fstrim failed", facts["mount"])
    m = _TRIMMED.search(text)
    return {"mount": facts["mount"], "trimmed_bytes": int(m.group(1)) if m else None, "message": text[-300:]}
