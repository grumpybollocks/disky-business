# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn a deep scan (`disky smart <drive> --deep`, smartctl's own sections) into words: an Identity card,
history cards and a plain-text report. No widgets here, so every rule can be tested without a window.

A section the drive does not give is left out; a fact it does not report says so.
"""
import time

NVME_FAILED = {5, 6, 7}                 # NVMe self-test results that mean a fault (0 passed; the rest: stopped)
# Lifetime statistics worth showing (names as smartctl gives them) -> plain name, unit
STATISTICS = {"Lifetime Power-On Resets": ("Times switched on", ""),
              "Logical Sectors Written": ("Data written", "sectors"),
              "Logical Sectors Read": ("Data read", "sectors"),
              "Highest Temperature": ("Hottest ever", "°C"),
              "Number of Reallocated Logical Sectors": ("Spots replaced with spares", ""),
              "Number of Reported Uncorrectable Errors": ("Errors it could not fix", ""),
              "Number of Mechanical Start Failures": ("Failed spin-ups", ""),
              "Number of Interface CRC Errors": ("Data damaged on the cable", ""),
              "Percentage Used Endurance Indicator": ("Wear used", "%")}
# SATA link counters in plain words (names as smartctl gives them); others keep smartctl's name
LINK_NAMES = {"Command failed due to ICRC error": "Data damaged on the cable",
              "Transition from drive PhyRdy to drive PhyNRdy": "Link lost and found again"}
HIDDEN = "hidden (tick \"Show serial number\")"
NOT_REPORTED = "not reported"
# Bits of an ATA error register (ATA command set): what the drive said went wrong
ERROR_BITS = ((0x40, "could not read a spot (uncorrectable)"), (0x80, "data damaged on the way (cable or port)"),
              (0x10, "address not found"), (0x04, "command refused"), (0x01, "address mark not found"))


def num(n):
    return f"{n:,}".replace(",", " ")


def size(n):
    for unit, step in (("TB", 10 ** 12), ("GB", 10 ** 9), ("MB", 10 ** 6)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} bytes"


def _get(d, *path):
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and -len(d) <= p < len(d):
            d = d[p]
        else:
            return None
    return d


def _hours(h):
    years = h / (24 * 365.25)
    return f"{num(h)} hours" + (f" (about {years:.1f} years)" if years >= 1 else "")


def identity(data, show_serial=False):
    """[(name, value)] for the Identity card."""
    rows = []

    def add(name, value):
        if value not in (None, ""):
            rows.append((name, str(value)))

    add("Maker", data.get("maker") or NOT_REPORTED)
    add("Family", data.get("model_family"))
    add("Model", data.get("model_name"))
    add("Firmware", data.get("firmware_version"))
    if "serial_number" in data:
        add("Serial number", data["serial_number"] if show_serial else HIDDEN)
    capacity = _get(data, "user_capacity", "bytes") or data.get("nvme_total_capacity")
    if isinstance(capacity, int):
        add("Size", size(capacity))
    protocol = _get(data, "device", "protocol")
    link = _get(data, "sata_version", "string") or (f"NVMe {_get(data, 'nvme_version', 'string')}"
                                                    if _get(data, "nvme_version", "string") else protocol)
    now, best = _get(data, "interface_speed", "current", "string"), _get(data, "interface_speed", "max", "string")
    if now and best and now != best:
        link = f"{link}, {now} now (the drive can do {best}: check the cable and port)"
    elif now or best:
        link = f"{link}, {now or best}"
    add("Interface", link)
    rpm = data.get("rotation_rate")
    if rpm == 0 or protocol == "NVMe":
        add("Kind", "solid state (no moving parts)")
    elif isinstance(rpm, int):
        add("Kind", f"spinning disk, {num(rpm)} rpm")
    add("Form factor", _get(data, "form_factor", "name"))
    trim = _get(data, "trim", "supported")
    if trim is not None:
        add("TRIM", "supported" if trim else "not supported")
    hours = _get(data, "power_on_time", "hours")
    if isinstance(hours, int):
        add("Hours on", _hours(hours))
    add("Production date", data.get("production_date") or f"{NOT_REPORTED} (only SAS drives report one)")
    known = data.get("in_smartctl_database")
    if known is not None:
        add("In smartctl's drive list", "yes" if known else "no (item names may be generic)")
    return rows


def _errors(data):
    log = _get(data, "ata_smart_error_log", "extended") or _get(data, "ata_smart_error_log", "summary")
    if isinstance(log, dict):
        count = log.get("count") or 0
        if not count:
            return ["None logged."], "The drive has no errors in its log. Good."
        lines = []
        for e in [e for e in log.get("table") or [] if isinstance(e, dict)][:8]:
            code = _get(e, "completion_registers", "error") or 0
            what = e.get("error_description") or ", ".join(w for bit, w in ERROR_BITS if code & bit) or "error"
            lines.append(f"At hour {num(e.get('lifetime_hours') or 0)}: {what}")
        meaning = (f"{num(count)} error{'s' if count != 1 else ''} in the drive's life; it keeps only the latest "
                   "few. Old errors that do not grow are less worrying than new ones: compare with a later scan.")
        return lines, meaning
    nvme = data.get("nvme_error_information_log")
    if isinstance(nvme, dict):
        count = _get(data, "nvme_smart_health_information_log", "num_err_log_entries") or 0
        if not count:
            return ["None logged."], "The drive has no errors in its log. Good."
        return [f"{num(count)} entries in the drive's life."], ("NVMe drives also log harmless events here "
                                                               "(for example a command the system tried that the "
                                                               "drive does not have); worry only if it keeps growing "
                                                               "together with media errors.")
    return None


def _ata_passed(entry):
    """True passed, False failed, None stopped or not known. ATA's self-test status: the high four bits are
    0 completed, 1 stopped by the host, 2 stopped by a reset, 3 to 8 a fault found, 15 still running."""
    value = _get(entry, "status", "value")
    if isinstance(value, int):
        code = value >> 4
        return True if code == 0 else False if 3 <= code <= 8 else None
    passed = _get(entry, "status", "passed")
    return passed if isinstance(passed, bool) else None


def _nvme_passed(value):
    return True if value == 0 else False if value in NVME_FAILED else None


def _selftests(data):
    rows = []
    for e in (_get(data, "ata_smart_self_test_log", "extended", "table")
              or _get(data, "ata_smart_self_test_log", "standard", "table") or []):
        if isinstance(e, dict):
            rows.append((_get(e, "type", "string"), _get(e, "status", "string"), _ata_passed(e),
                         e.get("lifetime_hours")))
    for e in [e for e in _get(data, "nvme_self_test_log", "table") or [] if isinstance(e, dict)]:
        rows.append((_get(e, "self_test_code", "string"), _get(e, "self_test_result", "string"),
                     _nvme_passed(_get(e, "self_test_result", "value")), e.get("power_on_hours")))
    if not rows and "ata_smart_self_test_log" not in data and "nvme_self_test_log" not in data:
        return None
    if not rows:
        return ["None run yet."], "The SMART tab can start a short or long self-test."
    lines = [f"{kind or 'Test'} at hour {num(h or 0)}: {status or '?'}" for kind, status, _, h in rows[:10]]
    failed = sum(1 for r in rows if r[2] is False)
    stopped = sum(1 for r in rows if r[2] is None)
    if failed:
        meaning = (f"{failed} of {len(rows)} failed. A failed self-test means the drive found a fault in itself: "
                   "copy your files off it.")
    elif stopped:
        meaning = f"None failed; {stopped} of {len(rows)} {'was' if stopped == 1 else 'were'} stopped before the end."
    else:
        meaning = "Every self-test the drive remembers passed."
    return lines, meaning


def _statistics(data):
    lines = []
    block = data.get("logical_block_size") or 512
    for page in _get(data, "ata_device_statistics", "pages") or []:
        for r in page.get("table") or []:
            name, value = r.get("name"), r.get("value")
            if name not in STATISTICS or not isinstance(value, int) or not _get(r, "flags", "valid"):
                continue
            plain, unit = STATISTICS[name]
            if unit == "sectors":
                lines.append(f"{plain}: {size(value * block)}")
            else:
                lines.append(f"{plain}: {num(value)}" + (f" {unit}" if unit else ""))
    h = data.get("nvme_smart_health_information_log")
    if isinstance(h, dict):
        for key, name, unit in (("data_units_read", "Data read", 512_000), ("data_units_written", "Data written",
                                                                           512_000),
                                ("power_cycles", "Times switched on", None),
                                ("unsafe_shutdowns", "Power lost without warning", None),
                                ("media_errors", "Media errors", None),
                                ("warning_temp_time", "Minutes too warm", None),
                                ("critical_comp_time", "Minutes far too hot", None)):
            if isinstance(h.get(key), int):
                lines.append(f"{name}: {size(h[key] * unit) if unit else num(h[key])}")
    if not lines:
        return None
    return lines, "What the drive has counted over its whole life."


def _link(data):
    table = _get(data, "sata_phy_event_counters", "table")
    if not isinstance(table, list):
        return None
    table = [r for r in table if isinstance(r, dict) and isinstance(r.get("value"), int)]
    counted = [f"{LINK_NAMES.get(r.get('name'), r.get('name'))}: {num(r['value'])}" for r in table
               if r["value"] and "COMRESET" not in str(r.get("name"))]
    crc = sum(r["value"] for r in table if "CRC" in str(r.get("name")))
    if not counted:
        return ["None counted."], "Counted since the drive was last switched on. No trouble on the cable or port."
    return counted, ("Counted since the drive was last switched on. "
                     + ("Damaged data on the way points to the cable or port, not the drive: try another."
                        if crc else "Small numbers here are normal."))


def _temperature(data):
    hist = data.get("ata_sct_temperature_history")
    temps = [t for t in (_get(hist, "table") or []) if isinstance(t, int)]
    lines = []
    if temps:
        span = (_get(hist, "logging_interval_minutes") or 1) * len(_get(hist, "table")) / 60
        lines.append(f"Last {span:.0f} hours: lowest {min(temps)} °C, highest {max(temps)} °C")
    now, limit = _get(data, "temperature", "current"), _get(data, "temperature", "op_limit_max")
    if isinstance(now, int):
        lines.append(f"Now: {now} °C" + (f" (the drive's limit: {limit} °C)" if isinstance(limit, int) and limit
                                       else ""))
    if not lines:
        return None
    return lines, "Most drives are happiest below about 50 °C; hot drives wear out sooner."


CARDS = (("Errors the drive logged", _errors), ("Self-test history", _selftests),
         ("Lifetime statistics", _statistics), ("Link errors (cable and port)", _link),
         ("Temperature record", _temperature))


def history(data):
    """[(title, lines, meaning)] for every history card the drive has data for."""
    out = []
    for title, make in CARDS:
        got = make(data)
        if got:
            out.append((title, got[0], got[1]))
    return out


def report(name, data, show_serial=False, when=None):
    """The deep scan as plain text, as the window shows it."""
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(when))
    out = [f"Disky Business deep scan: {name}", f"Made {stamp}", "", "Identity"]
    out += [f"  {k}: {v}" for k, v in identity(data, show_serial)]
    for title, lines, meaning in history(data):
        out += ["", title] + [f"  {line}" for line in lines] + [f"  ({meaning})"]
    return "\n".join(out) + "\n"
