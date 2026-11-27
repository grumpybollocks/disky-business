# SPDX-License-Identifier: GPL-3.0-or-later
"""SMART details of one drive, asked of the system's drive service (udisks2). No root, no Qt.

Units and meanings follow the service's own documentation (org.freedesktop.UDisks2.xml, SmartGetAttributes).
Names of makers' own items come from smartmontools' drive list (smartdb.py). The items people need are given
plain names and a line saying what they mean (PLAIN); the rest are shown only when asked for.
"""
import json
import re
import subprocess
from dataclasses import dataclass

from . import smartdb

_P = "org.freedesktop.UDisks2."
_IFACE = {"ata": _P + "Drive.Ata", "nvme": _P + "NVMe.Controller"}

NO_SMART = ("The system cannot read SMART from this drive. USB cases often do not pass it on; plugged in "
            "directly (SATA or NVMe) it would show here.")
NOT_DECODED = "The system does not decode this number."
# udisks2 turns "total LBAs written/read" into megabytes with one formula for every maker (libatasmart), which
# is often wrong by far (2 EB for a 2 TB disk was seen), so the figure is not shown (unit 7 = MB).
UNIT_MB = 7
NOT_TRUSTED = ("The system's figure for this amount uses one formula for every maker and is often wrong, so it "
               "is not shown. The deep scan reads it from the drive's own statistics.")
# Error-rate items that some makers (Seagate) count every operation in: a huge raw number is normal there.
OPERATION_COUNTS = {"rawreaderrorrate", "seekerrorrate", "hardwareeccrecovered"}
RATED_NEAR = 10                 # "rated" items: warn when the score is this close to the limit (and below 50)

# Plain words for the items that matter. Key: the name with only letters and digits, lower case (smartctl's
# and the drive service's spellings both fit). Rule "zero": above 0 is worth watching. "cable": above 0 points
# at the cable. "life": the drive's own estimate of wear left, shown from its score (100 = new).
_SPOTS = "0 is normal; a growing number means the drive is wearing out."
PLAIN = {}
for _keys, _label, _about, _rule in (
        (("reallocatedsectorct", "reallocatedsectorcount", "retiredblockcount"), "Worn-out spots replaced",
         "Bad spots the drive has swapped for spare ones. " + _SPOTS, "zero"),
        (("currentpendingsector",), "Spots it could not read",
         "Waiting to be read again or replaced. Should be 0.", "zero"),
        (("offlineuncorrectable",), "Spots it could not repair", "Found by the drive's own check. Should be 0.",
         "zero"),
        (("reporteduncorrect",), "Read errors it could not fix", "Should be 0.", "zero"),
        (("reallocatedeventcount",), "Times spots were replaced", _SPOTS, "zero"),
        (("udmacrcerrorcount", "satacrcerrorcount", "crcerrorcount"), "Cable errors",
         "Data spoiled on the way through the cable or connector. Usually the cable, not the drive.", "cable"),
        (("poweronhours",), "Hours switched on", "Total running time.", ""),
        (("powercyclecount",), "Times switched on", "", ""),
        (("temperaturecelsius", "temperaturecelsius2"), "Temperature", "The drive's temperature now.", ""),
        (("airflowtemperaturecel", "airflowtemperaturecelsius"), "Air temperature",
         "The air inside the drive, as the drive measures it. Usually close to the temperature above.", "extra"),
        (("loadcyclecount", "loadunloadcyclecount"), "Head parkings",
         "Times the heads were parked to save power. Drives are rated for a set number; the drive's score falls as "
         "that is used up, and a drive past it wears faster.", "rated"),
        (("startstopcount",), "Motor starts", "Times the motor spun up. The drive's score falls as its rated "
         "number is used up.", "rated"),
        (("ssdlifeleft", "remaininglifetimeperc", "percentlifetimeremain", "mediawearoutindicator",
          "enduranceremaining"), "Life left", "The drive's own estimate of its wear. It starts at 100 %.", "life"),
        (("spinretrycount",), "Motor start retries", "Times the motor needed another try to spin up. Should stay 0.",
         ""),
        (("commandtimeout",), "Commands that timed out", "Commands the drive did not finish in time.", "packed"),
):
    for _k in _keys:
        PLAIN[_k] = (_label, _about, _rule)


@dataclass
class Row:
    name: str
    value: str
    status: str = "OK"          # "OK", or what is wrong in a few words
    about: str = ""             # what it means, in plain words
    main: bool = True           # False: shown only with "Show all items"
    detail: str = ""            # the drive's own score: now, worst, limit
    score: int | None = None    # the same three as numbers, for the table (None: the drive gives none)
    worst: int | None = None
    limit: int | None = None

    @property
    def bad(self):
        return self.status != "OK"


def _key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _num(n):
    return f"{n:,}".replace(",", " ")


def _hours(ms):
    return f"{_num(ms // 3_600_000)} hours"


def _size(n):
    for unit, step in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} bytes"


def _pretty(pretty, unit):
    if unit == 2:
        return _hours(pretty) if pretty >= 3_600_000 else f"{_num(pretty)} ms"
    if unit == 3:
        return f"{_num(pretty)} sectors"
    if unit == 4:
        return f"{round(pretty / 1000 - 273.15)} °C"
    if unit == 1:
        return _num(pretty)
    return ""


def _readable(name):
    return name.replace("_", " ").replace("-", " ").strip().capitalize()


def ata_rows(attrs, names=None):
    """attrs = list of (id, name, flags, value, worst, threshold, pretty, pretty_unit, expansion).
    names = {id: name} from smartmontools' drive list, which wins over the service's own names."""
    names = names or {}
    rows, used = [], set()
    for ident, name, flags, value, worst, thresh, pretty, unit, _ in attrs:
        known = names.get(ident) or ("" if name.startswith("attribute-") else name)
        plain = PLAIN.get(_key(known))
        if plain and plain[0] in used:          # a second item for the same thing: shown under its own name
            plain = None
        shown = _pretty(pretty, unit)
        status = "OK"
        if thresh > 0 and 0 <= value <= thresh:
            status = "FAILING" if flags & 1 else "Worn past its limit"
        elif thresh > 0 and 0 <= worst <= thresh:
            status = "Failed in the past"
        detail = ", ".join(f"{k} {x}" for k, x in (("score now", value), ("worst", worst), ("fails at", thresh))
                           if x >= 0)
        numbers = [x if x >= 0 else None for x in (value, worst, thresh)]
        if plain:
            label, about, rule = plain
            used.add(label)
            if rule == "life" and value >= 0:
                shown = f"{value} %"
            elif status == "OK" and rule in ("zero", "cable") and unit in (1, 3) and pretty > 0:
                status = "Watch" if rule == "zero" else "Check the cable"
            elif rule == "packed" and unit == 1 and pretty > 0xFFFF:
                # Some makers (Seagate) pack three 16-bit counts into this one number; smartctl shows them apart.
                parts = [(pretty >> s) & 0xFFFF for s in (0, 16, 32)]
                shown = _num(parts[0])
                about += f" This drive keeps three counts here: {', '.join(_num(p) for p in parts)}."
            elif (status == "OK" and rule == "rated" and 0 <= value < 50 and thresh >= 0
                  and value <= thresh + RATED_NEAR):
                status = "Near its rated limit"
            rows.append(Row(label, shown or "—", status, about or (NOT_DECODED if not shown else ""),
                            rule != "extra" or status != "OK", detail, *numbers))
        else:
            label = _readable(known) if known else f"Not named by the maker ({ident})"
            if unit == UNIT_MB:
                shown, about = "", NOT_TRUSTED
            elif not shown:
                about = NOT_DECODED + (f" Its {detail}." if detail else "")
            elif _key(known) in OPERATION_COUNTS and unit == 1 and pretty > 1_000_000:
                about = ("A huge number is normal on some makers' drives (Seagate counts every operation here). "
                         f"The drive's score is what counts: {detail}.")
            else:
                about = ""
            rows.append(Row(label, shown or "—", status, about, status != "OK", detail, *numbers))
    return rows


def _c(kelvin):
    return f"{kelvin - 273} °C"


def nvme_rows(a):
    """a = {field: value} as the service documents for NVMe. All of these are worth showing."""
    rows = []
    if "percent_used" in a:
        used = a["percent_used"]
        rows.append(Row("Life used", f"{used} %", "OK" if used < 100 else "Worn out",
                        "The maker's estimate of wear. 100 % is the end of its rated life."))
    if "avail_spare" in a:
        low = a["avail_spare"] <= a.get("spare_thresh", 0)
        rows.append(Row("Spare space left", f"{a['avail_spare']} %", "Too low" if low else "OK",
                        f"Reserve used to replace worn parts. Warns at {a.get('spare_thresh', 0)} %."))
    if "media_errors" in a:
        rows.append(Row("Data errors it could not fix", _num(a["media_errors"]),
                        "OK" if a["media_errors"] == 0 else "Errors found", "Should be 0."))
    if "power_on_hours" in a:
        rows.append(Row("Hours switched on", f"{_num(a['power_on_hours'])} hours", "OK", "Total running time."))
    for key, label, about in (("num_err_log_entries", "Error log entries",
                               "Errors the drive noted. Often harmless; data errors above are what count."),
                              ("power_cycles", "Times switched on", ""),
                              ("unsafe_shutdowns", "Times power was cut", "Switched off without warning.")):
        if key in a:
            rows.append(Row(label, _num(a[key]), "OK", about))
    for key, label in (("total_data_read", "Data read"), ("total_data_written", "Data written")):
        if key in a:
            rows.append(Row(label, _size(a[key]), "OK", "In its whole life."))
    if "ctrl_busy_time" in a:
        rows.append(Row("Time busy", f"{_num(a['ctrl_busy_time'] // 60)} hours", "OK", "Time spent reading and writing."))
    temps = [k for k in a.get("temp_sensors", []) if k]
    if temps:
        limit = f"The drive warns at {_c(a['wctemp'])}." if a.get("wctemp") else ""
        rows.append(Row("Temperature", ", ".join(_c(k) for k in temps), "OK", limit))
    for key, label in (("warning_temp_time", "Minutes too hot"), ("critical_temp_time", "Minutes far too hot")):
        if key in a:
            rows.append(Row(label, _num(a[key]), "OK" if a[key] == 0 else "Has run hot", "Should be 0."))
    return rows


GLANCE = ("Life left", "Life used", "Temperature", "Hours switched on", "Data written", "Times switched on")


def glance(rows, most=4):
    """Up to four (name, value) pairs for the big figures on top of the SMART tab."""
    by_name = {r.name: r for r in rows}
    found = [(name, by_name[name].value) for name in GLANCE
             if name in by_name and by_name[name].value not in ("", "—")]
    return found[:most]


BRIEF = ("Life left", "Life used", "Spare space left", "Worn-out spots replaced", "Data written")


def brief(rows):
    """A few lines for the health box: wear and life where the drive reports them, and every warning."""
    by_name = {r.name: r for r in rows}
    lines = [f"{name}: {by_name[name].value}" for name in BRIEF
             if name in by_name and by_name[name].value not in ("", "—") and not by_name[name].bad]
    lines += [f"Warning: {r.name}: {r.value} ({r.status})" for r in rows if r.bad]
    return lines


# smartctl gives raw numbers; these say what kind each is, so they are shown like the drive service's.
_HOURS, _SECTORS, _KELVIN = 2, 3, 4
_UNIT = {"poweronhours": _HOURS, "temperaturecelsius": _KELVIN, "airflowtemperaturecel": _KELVIN,
         "reallocatedsectorct": _SECTORS, "currentpendingsector": _SECTORS, "offlineuncorrectable": _SECTORS,
         "reporteduncorrect": _SECTORS}


def smartctl_rows(data):
    """Rows from the engine's `smart_data` (parts of `smartctl -j`): the same rules and words as ata_rows and
    nvme_rows, so a drive reads the same whichever way its record was fetched."""
    verdict = []
    if data.get("smart_status", {}).get("passed") is False:
        verdict.append(Row("The drive's own verdict", "FAILED", "FAILING",
                           "The drive itself says it is failing. Copy your files off it now."))
    nv = data.get("nvme_smart_health_information_log")
    if nv and nv.get("critical_warning"):
        verdict.append(Row("Critical warning", str(nv["critical_warning"]), "Warning from the drive",
                           "The drive raises a critical warning: spare space, temperature, reliability or "
                           "read-only mode."))
    if nv:
        a = {"avail_spare": nv.get("available_spare"), "spare_thresh": nv.get("available_spare_threshold"),
             "percent_used": nv.get("percentage_used"), "media_errors": nv.get("media_errors"),
             "num_err_log_entries": nv.get("num_err_log_entries"), "power_cycles": nv.get("power_cycles"),
             "power_on_hours": nv.get("power_on_hours"), "unsafe_shutdowns": nv.get("unsafe_shutdowns"),
             "ctrl_busy_time": nv.get("controller_busy_time"), "warning_temp_time": nv.get("warning_temp_time"),
             "critical_temp_time": nv.get("critical_comp_time")}
        for key, units in (("total_data_read", nv.get("data_units_read")),
                           ("total_data_written", nv.get("data_units_written"))):
            a[key] = None if units is None else units * 512_000       # NVMe counts in units of 512 000 bytes
        if nv.get("temperature") is not None:
            a["temp_sensors"] = [nv["temperature"] + 273]
        return verdict + nvme_rows({k: v for k, v in a.items() if v is not None})
    tuples, names = [], {}
    for at in data.get("ata_smart_attributes", {}).get("table", []):
        ident, name = int(at.get("id", 0)), str(at.get("name", ""))
        raw = at.get("raw", {})
        lead = re.match(r"\s*(-?\d+)", str(raw.get("string", "")))          # a temperature can be below zero
        n = int(lead.group(1)) if lead else int(raw.get("value") or 0)
        unit = _UNIT.get(_key(name), 1)
        pretty = n * 3_600_000 if unit == _HOURS else round((n + 273.15) * 1000) if unit == _KELVIN else n
        known = bool(name) and not name.startswith("Unknown")
        if known:
            names[ident] = name
        tuples.append((ident, name if known else f"attribute-{ident}", 1 if at.get("flags", {}).get("prefailure")
                       else 0, at.get("value", -1), at.get("worst", -1), at.get("thresh", -1), pretty, unit, {}))
    return verdict + ata_rows(tuples, names)


def _unwrap(v):
    return v.get("data") if isinstance(v, dict) and "data" in v else v


def read(drive, run=subprocess.run, names_of=smartdb.names):
    """(rows, message). rows is empty and message says why when nothing can be read."""
    if not drive.smart or not drive.path:
        return [], NO_SMART
    cmd = ["busctl", "--system", "--json=short", "call", "org.freedesktop.UDisks2", drive.path,
           _IFACE[drive.smart], "SmartGetAttributes", "a{sv}", "0"]
    try:
        out = run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return [], "The drive did not answer: " + (out.stderr.strip() or f"exit {out.returncode}")
        data = json.loads(out.stdout)["data"][0]
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as e:
        return [], f"Could not ask the system for SMART details ({e.__class__.__name__})."
    if drive.smart == "ata":
        return ata_rows(data, names_of(drive.model, drive.firmware, drive.kind)), ""
    return nvme_rows({k: _unwrap(v) for k, v in data.items()}), ""
