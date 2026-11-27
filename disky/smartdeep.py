# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep read of a drive: everything `smartctl -x -j` knows about it (identity, firmware, logs, statistics).

Only reads: `-x` asks the drive for its information pages and logs, it starts and changes nothing. smartctl
needs admin rights to talk to the drive, so the window runs this through the admin session; the device name
gets the same strict check as the plain SMART read.

The key names below were taken from real runs of smartctl 7.5 on a SATA SSD, a SATA hard disk, an NVMe drive
and two drives in USB cases (2026-11-27). SAS/SCSI drives were not available, so their keys (the only ones
that carry a production date) are not read: the date is reported as not known.

The serial number and the unique part of the world-wide name identify one drive: the serial is passed on only
when asked for, the unique part never.
"""
import json
import os
import shutil
import stat

from . import proc
from .smartread import device_path

KEEP = (
    # identity
    "device", "model_family", "model_name", "firmware_version", "user_capacity", "logical_block_size",
    "physical_block_size", "rotation_rate", "form_factor", "interface_speed", "sata_version", "ata_version",
    "nvme_version", "nvme_pci_vendor", "nvme_ieee_oui_identifier", "nvme_total_capacity", "trim", "write_cache",
    "read_lookahead", "in_smartctl_database", "smart_support",
    # health now
    "smart_status", "power_on_time", "power_cycle_count", "temperature", "endurance_used", "spare_available",
    "ata_smart_attributes", "ata_smart_data", "nvme_smart_health_information_log",
    # history
    "ata_smart_error_log", "ata_smart_self_test_log", "nvme_error_information_log", "nvme_self_test_log",
    "ata_sct_temperature_history", "ata_device_statistics", "sata_phy_event_counters",
)
OUI_FILE = "/usr/share/hwdata/oui.txt"


def maker(oui, path=OUI_FILE):
    """The registered owner of a 24-bit maker number (IEEE OUI), or None when it is not known."""
    if not isinstance(oui, int) or not 0 <= oui < 1 << 24:
        return None
    key = "-".join(f"{oui:06X}"[i:i + 2] for i in (0, 2, 4)) + " "
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith(key) and "(hex)" in line:
                    return line.split("(hex)", 1)[1].strip() or None
    except OSError:
        return None
    return None


def read(device, serial=False, run=proc.run, is_root=None, is_block=None, oui_file=OUI_FILE):
    """The parts of `smartctl -x -j` the deep scan shows. Raises PermissionError, ValueError (nothing to show)."""
    path = device_path(device)
    if not (os.geteuid() == 0 if is_root is None else is_root):
        raise PermissionError("The deep scan needs admin rights.")
    block = is_block or (lambda p: os.path.exists(p) and stat.S_ISBLK(os.stat(p).st_mode))
    if not block(path):
        raise ValueError(f"{path} is not a drive (was it unplugged?).")
    if shutil.which("smartctl") is None and run is proc.run:
        raise ValueError("smartctl is not installed (package smartmontools).")
    # smartctl's exit code is a bit mask that also flags old errors in the logs; the JSON says what was read.
    done = run(["smartctl", "-x", "-j", path], capture_output=True, text=True, timeout=300)
    try:
        js = json.loads(done.stdout)
    except ValueError:
        raise ValueError("smartctl gave no readable answer.") from None
    if not isinstance(js, dict) or "model_name" not in js:
        errors = [m.get("string", "") for m in js.get("smartctl", {}).get("messages", [])
                  if m.get("severity") == "error"] if isinstance(js, dict) else []
        raise ValueError("This drive gives no information for a deep scan."
                         + (f" (smartctl: {errors[0][:160]})" if errors else ""))
    out = {k: js[k] for k in KEEP if k in js}
    wwn = js.get("wwn")
    if isinstance(wwn, dict):
        out["wwn"] = {k: v for k, v in wwn.items() if k != "id"}
    oui = wwn.get("oui") if isinstance(wwn, dict) else js.get("nvme_ieee_oui_identifier")
    out["maker"] = maker(oui, oui_file)
    out["production_date"] = None                          # SATA and NVMe drives do not report one
    if serial and isinstance(js.get("serial_number"), str):
        out["serial_number"] = js["serial_number"]
    return out
