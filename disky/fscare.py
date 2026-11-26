# SPDX-License-Identifier: GPL-3.0-or-later
"""Check and repair a partition's file system, by asking the system's drive service (udisks2).

The service runs the file system's own official tool (the one GNOME Disks uses too) and asks for the admin
password itself where the system's rules want one. Check only reads. Repair changes the drive, so the window
warns and asks before it calls this. Nothing here is done on a mounted partition, and never on NTFS.
"""
import json
import os

from . import frag, proc

_BUS = "org.freedesktop.UDisks2"
_BLOCK = "/org/freedesktop/UDisks2/block_devices/"
METHOD = {"check": "Check", "repair": "Repair"}

NTFS_WHY = ("Windows (NTFS) partitions are not checked or repaired here: the Linux tool for NTFS only hides "
            "problems, it does not fix them. Use Windows' own disk check (chkdsk /f) on this drive.")
MOUNTED = "It is mounted. Unmount it first (the Unmount button on the left); a file system in use cannot be {what}."
NO_TOOL = "The system has no tool to {action} {fs} file systems, so nothing was done."
WORDS = {
    ("check", True): "No problems found.",
    ("check", False): "Problems found. Repair can try to fix them. Copy off the files you can still read first.",
    ("repair", True): "Repaired. Check it once more to be sure, then mount it.",
    ("repair", False): "It could not be repaired. Copy off what you can still read; the drive may need "
                       "formatting or replacing.",
}


class Refused(Exception):
    """Not done, for a reason the user should read."""


def _ask(args, run, timeout=30):
    done = run(["busctl", "--system", "--json=short"] + args, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise Refused(_plain(done.stderr or done.stdout or "", "read", ""))
    return json.loads(done.stdout).get("data")


def _plain(error, action, fs):
    if "NotSupported" in error or "not supported" in error.lower():
        return NO_TOOL.format(action=action, fs=fs or "these")
    if "NotAuthorizedDismissed" in error:
        return "The password was not given, so nothing was done."
    if "NotAuthorized" in error:
        return "The system did not allow it."
    if "DeviceBusy" in error or "is mounted" in error:
        return MOUNTED.format(what="checked" if action == "check" else "repaired")
    if "UnknownObject" in error or "No such" in error:
        return "The system does not know this partition (was the drive unplugged?)."
    last = error.strip().splitlines()[-1] if error.strip() else "no answer"
    return "It did not work: " + last[:200]


def facts(device, run=proc.run):
    """{device, fs, mounted} of a partition named like sdb1 or /dev/sdb1, as the drive service sees it."""
    name = os.path.basename(device)
    path = _BLOCK + name
    fs = _ask(["get-property", _BUS, path, _BUS + ".Block", "IdType"], run) or ""
    points = _ask(["get-property", _BUS, path, _BUS + ".Filesystem", "MountPoints"], run) or []
    mounted = [bytes(p).rstrip(b"\0").decode("utf-8", "replace") for p in points]
    return {"device": name, "fs": fs, "mounted": mounted}


def act(action, device, run=proc.run, timeout=6 * 3600):
    """Check or repair. Returns {action, device, fs, ok, message}; raises Refused when nothing is done.

    There is no Stop: a check is left to finish, and a repair must never be cut short."""
    f = facts(device, run)
    if f["fs"] in frag.HANDS_OFF:
        raise Refused(NTFS_WHY)
    if f["mounted"]:
        raise Refused(MOUNTED.format(what="checked" if action == "check" else "repaired"))
    done = run(["busctl", "--system", "--json=short", "--allow-interactive-authorization=yes",
                f"--timeout={timeout}", "call", _BUS, _BLOCK + f["device"], _BUS + ".Filesystem",
                METHOD[action], "a{sv}", "0"], capture_output=True, text=True, timeout=timeout + 60)
    if getattr(done, "ended", ""):
        raise Refused("The system took too long to answer. The drive may be failing; look at the SMART tab.")
    if done.returncode != 0:
        raise Refused(_plain(done.stderr or done.stdout or "", action, f["fs"]))
    try:
        ok = bool((json.loads(done.stdout).get("data") or [False])[0])
    except ValueError:
        raise Refused("The system gave no answer about the result. Check the partition to see its state.") from None
    return {"action": action, "device": f["device"], "fs": f["fs"], "ok": ok, "message": WORDS[(action, ok)]}
