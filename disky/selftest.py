# SPDX-License-Identifier: GPL-3.0-or-later
"""SMART self-tests: the drive tests itself; this starts one, follows it, and can abort it.

All through the system's drive service (udisks2), which asks for the admin password itself to start a test.
A self-test only reads; the drive stays usable while it runs.
"""
import json
import os
import re
import time

from . import proc

_BUS = "org.freedesktop.UDisks2"
_BLOCK = "/org/freedesktop/UDisks2/block_devices/"
IFACES = (_BUS + ".Drive.Ata", _BUS + ".NVMe.Controller")
KINDS = {"short": "short", "long": "extended"}
_NAME = re.compile(r"(sd[a-z]+|nvme[0-9]+n[0-9]+)")

RESULT = {
    "success": (True, "Passed: the drive found no problem."),
    "aborted": (None, "The self-test was stopped before it finished."),
    "interrupted": (None, "The self-test was interrupted (the drive was reset or lost power)."),
    "fatal": (False, "FAILED: the drive could not finish its self-test. Back up this drive now."),
    "error_unknown": (False, "FAILED (unknown part). Back up this drive now."),
    "error_electrical": (False, "FAILED (electrical part). Back up this drive now."),
    "error_servo": (False, "FAILED (head positioning). Back up this drive now."),
    "error_read": (False, "FAILED (reading). Back up this drive now."),
    "error_handling": (False, "FAILED (the drive may have been damaged in handling). Back up this drive now."),
}


class Refused(Exception):
    """Not done, for a reason the user should read."""


def _bus(args, run, timeout=60):
    done = run(["busctl", "--system", "--json=short", "--allow-interactive-authorization=yes"] + args,
               capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise Refused(_plain(done.stderr or done.stdout or ""))
    return json.loads(done.stdout).get("data") if done.stdout.strip() else None


def _plain(error):
    if "NotAuthorizedDismissed" in error:
        return "The password was not given, so no self-test was started."
    if "NotAuthorized" in error:
        return "The system did not allow it."
    if "NotSupported" in error or "not supported" in error.lower() or "No such interface" in error:
        return "This drive, or the case it sits in, offers no self-test to the system."
    last = error.strip().splitlines()[-1] if error.strip() else "no answer"
    return "It did not work: " + last[:200]


def find(device, run=proc.run):
    """(drive object path, interface) for a whole drive named like sdb. Refused if it offers no self-test."""
    name = os.path.basename(device)
    if not _NAME.fullmatch(name):
        raise Refused(f"Not a drive name: {name[:40]}")
    path = _bus(["get-property", _BUS, _BLOCK + name, _BUS + ".Block", "Drive"], run)
    for iface in IFACES:
        try:
            _bus(["get-property", _BUS, path, iface, "SmartSelftestStatus"], run)
            return path, iface
        except Refused:
            continue
    raise Refused("This drive, or the case it sits in, offers no self-test to the system.")


def state(path, iface, run=proc.run):
    """(status word, percent done or None) after asking the drive for fresh values."""
    try:
        _bus(["call", _BUS, path, iface, "SmartUpdate", "a{sv}", "0"], run)
    except Refused:
        pass                                    # stale values are still values; the next round asks again
    status = _bus(["get-property", _BUS, path, iface, "SmartSelftestStatus"], run) or ""
    left = _bus(["get-property", _BUS, path, iface, "SmartSelftestPercentRemaining"], run)
    done = 100 - left if isinstance(left, int) and 0 <= left <= 100 else None
    return status, done


def run_test(device, kind, emit, stop=None, run=proc.run, sleep=time.sleep, every=5.0):
    """Start a self-test and follow it to its end. Returns (ok, status word): ok is True, False, or None when
    it did not run to the end. Stop aborts the test on the drive."""
    path, iface = find(device, run)
    _bus(["call", _BUS, path, iface, "SmartSelftestStart", "sa{sv}", KINDS[kind], "0"], run, timeout=120)
    emit({"event": "selftest_progress", "status": "inprogress", "percent": 0})
    status, last = "inprogress", 0
    while status == "inprogress":
        waited = 0.0
        while waited < every and not (stop is not None and stop.is_set()):
            sleep(0.25)
            waited += 0.25
        if stop is not None and stop.is_set():
            try:
                _bus(["call", _BUS, path, iface, "SmartSelftestAbort", "a{sv}", "0"], run)
            except Refused as e:
                raise Refused(f"The self-test could not be stopped; it goes on inside the drive. ({e})") from None
            return None, "aborted"
        status, done = state(path, iface, run)
        if status == "inprogress" and done is not None and done != last:
            last = done
            emit({"event": "selftest_progress", "status": status, "percent": done})
    return RESULT.get(status, (None, ""))[0], status
