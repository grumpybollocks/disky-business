# SPDX-License-Identifier: GPL-3.0-or-later
"""Mount, unmount and safely remove a drive, by asking the system's drive service (udisks2). No Qt.

The service does the work and asks for the admin password itself where the system's rules want one (for
example for a drive that is not removable). Each function returns (done, message in plain words).
"""
import json
import os
import subprocess

from .drives import NTFS

_BUS = "org.freedesktop.UDisks2"
_BLOCK = "/org/freedesktop/UDisks2/block_devices/"
SYSTEM_PLACES = ("/", "/boot", "/efi", "/home", "/usr", "/var", "/etc", "/opt", "/root", "/srv", "/tmp", "/nix")
_SYSTEM_TREES = ("/boot", "/efi", "/usr", "/var", "/opt", "/nix")

# The service's error names, in plain words.
ERRORS = (
    ("DeviceBusy", "It is in use. Close the files, folders or programs open on it, then try again."),
    ("target is busy", "It is in use. Close the files, folders or programs open on it, then try again."),
    ("NotAuthorizedDismissed", "The password was not given, so nothing was done."),
    ("NotAuthorized", "The system did not allow it."),
    ("AlreadyMounted", "It is already mounted."),
    ("NotMounted", "It is not mounted."),
    ("NotSupported", "The system cannot do this with this drive."),
    ("wrong fs type", "The system could not read its file system. It may be damaged: the Care tab can check and repair it."),
    ("Timeout", "The drive did not answer in time."),
)


def in_system_use(volume, home=None):
    """True for a partition the running system or the home folder lives on: never unmounted from here."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    for m in volume.mounts:
        if m in SYSTEM_PLACES or any(m.startswith(p + "/") for p in _SYSTEM_TREES):
            return True
        if home == m or home.startswith(m.rstrip("/") + "/"):
            return True
    return False


def plain(error_text):
    for key, words in ERRORS:
        if key in error_text:
            return words
    last = error_text.strip().splitlines()[-1] if error_text.strip() else "no answer"
    return "It did not work: " + last[:200]


def _call(path, iface, method, run, timeout=60):
    cmd = ["busctl", "--system", "--json=short", "--allow-interactive-authorization=yes", f"--timeout={timeout}",
           "call", _BUS, path, f"{_BUS}.{iface}", method, "a{sv}", "0"]
    try:
        out = run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return False, plain("Timeout")
    except OSError as e:
        return False, plain(str(e))
    if out.returncode != 0:
        return False, plain(out.stderr or out.stdout or "")
    try:
        data = json.loads(out.stdout).get("data") or []
    except ValueError:
        data = []
    return True, data[0] if data else ""


def can_mount(drive):
    return [v for v in drive.volumes if not v.mounts and v.fstype not in NTFS and v.fstype]


def can_unmount(drive, home=None):
    return [v for v in drive.volumes if v.mounts and not in_system_use(v, home)]


def can_remove(drive, home=None):
    """Safely remove (unmount everything, then switch the drive off): USB drives the system does not live on."""
    return (drive.usb and bool(drive.path) and not drive.other_use
            and not any(in_system_use(v, home) for v in drive.volumes))


def mount(drive, run=subprocess.run):
    todo = can_mount(drive)
    skipped = [v for v in drive.volumes if not v.mounts and v.fstype in NTFS]
    note = (" Its Windows (NTFS) partition was left alone: Linux may change a damaged NTFS drive just by "
            "mounting it. Mount it from the file manager only if Windows' disk check passed.") if skipped else ""
    if not todo:
        return False, "Nothing on it to mount." + note
    places = []
    for v in todo:
        ok, answer = _call(_BLOCK + v.device, "Filesystem", "Mount", run)
        if not ok:
            return False, answer + note
        places.append(answer)
    return True, "Mounted at " + ", ".join(p for p in places if p) + "." + note


def unmount(drive, run=subprocess.run, home=None):
    todo = can_unmount(drive, home)
    if not todo:
        return False, "Nothing on it to unmount."
    for v in todo:
        ok, answer = _call(_BLOCK + v.device, "Filesystem", "Unmount", run)
        if not ok:
            return False, answer
    return True, "Unmounted. Its files are no longer reachable until it is mounted again."


def remove(drive, run=subprocess.run, home=None):
    if drive.other_use:
        return False, f"It is in use as {drive.other_use}. Switch that off first."
    if not can_remove(drive, home):
        return False, "Only USB drives the system does not run from can be switched off here."
    if can_unmount(drive, home):
        ok, answer = unmount(drive, run, home)
        if not ok:
            return False, answer
    ok, answer = _call(drive.path, "Drive", "PowerOff", run)
    if not ok:
        return False, answer
    return True, "Switched off. It is safe to unplug it now."
