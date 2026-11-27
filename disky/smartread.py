# SPDX-License-Identifier: GPL-3.0-or-later
"""Read a drive's SMART record with smartctl, for drives the system's drive service cannot read (USB cases).

Only reads. smartctl needs admin rights to talk to the drive, so the window starts this through the system's
password prompt. Because it runs with admin rights, the device name is checked strictly before it is used.
"""
import json
import os
import re
import shutil
import stat

from . import proc

_NAME = re.compile(r"(sd[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+)")
KEEP = ("model_name", "firmware_version", "rotation_rate", "smart_status", "ata_smart_attributes",
        "nvme_smart_health_information_log", "temperature", "power_on_time", "power_cycle_count")


def device_path(device):
    """/dev/<name> for a whole drive named like sdb or nvme0n1; ValueError for anything else."""
    name = os.path.basename(device)
    if not _NAME.fullmatch(name):
        raise ValueError(f"Not a drive name: {name[:40]}")
    return "/dev/" + name


def smartctl(device, options, timeout, why, run=proc.run, is_root=None, is_block=None):
    """Run `smartctl -j <options> /dev/<drive>` with every check a command with admin rights needs, and return
    its JSON answer (a dict). Raises PermissionError (`why` says what needs admin rights), ValueError."""
    path = device_path(device)
    if not (os.geteuid() == 0 if is_root is None else is_root):
        raise PermissionError(why)
    block = is_block or (lambda p: os.path.exists(p) and stat.S_ISBLK(os.stat(p).st_mode))
    if not block(path):
        raise ValueError(f"{path} is not a drive (was it unplugged?).")
    if shutil.which("smartctl") is None and run is proc.run:
        raise ValueError("smartctl is not installed (package smartmontools).")
    done = run(["smartctl", "-j", *options, path], capture_output=True, text=True, timeout=timeout)
    ended = getattr(done, "ended", "")
    if ended == "timeout":
        raise ValueError(f"The drive did not answer within {timeout // 60} minutes.")
    if ended == "stopped":
        raise ValueError("Stopped before the drive answered.")
    try:
        js = json.loads(done.stdout)
    except ValueError:
        raise ValueError("smartctl gave no readable answer.") from None
    if not isinstance(js, dict):
        raise ValueError("smartctl gave no readable answer.")
    return js


def errors_of(js):
    """smartctl's own error messages, for telling the user why nothing came back."""
    msgs = js.get("smartctl", {}).get("messages", []) if isinstance(js.get("smartctl"), dict) else []
    return [m.get("string", "") for m in msgs if isinstance(m, dict) and m.get("severity") == "error"]


def read(device, run=proc.run, is_root=None, is_block=None):
    """The parts of `smartctl -j` the window shows. Raises PermissionError, ValueError (nothing to show)."""
    js = smartctl(device, ["-H", "-A", "-i"], 120, "Reading SMART through a USB case needs admin rights.",
                  run, is_root, is_block)
    if "ata_smart_attributes" not in js and "nvme_smart_health_information_log" not in js:
        errors = errors_of(js)
        raise ValueError("This drive gives no SMART data through its USB case."
                         + (f" (smartctl: {errors[0][:160]})" if errors else ""))
    return {k: js[k] for k in KEEP if k in js}
