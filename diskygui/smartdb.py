# SPDX-License-Identifier: GPL-3.0-or-later
"""Names of SMART items per drive model, from smartmontools' drive list (drivedb.h), when it is installed.

The list says, for each drive family (a pattern matched against the whole model name), what the maker's own
item numbers mean, as `-v ID,format,Name`. smartctl takes the first family that matches, on top of the
"DEFAULT" entry; this does the same. Only names are taken. Nothing here runs or needs root.
"""
import functools
import re

DRIVEDB = "/usr/share/smartmontools/drivedb.h"
_V = re.compile(r"-v\s+(\d+),[^,\s]+,([^,\s]+)(?:,(HDD|SSD))?")


def entries(text):
    """drivedb.h text -> list of field lists (each field is its joined string literals).

    An entry is an innermost { ... }, so an outer array around the entries (the real file has one in a comment)
    makes no difference."""
    out, fields, cur, inside, i, n = [], [], "", False, 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        if text.startswith("/*", i):
            i = text.find("*/", i)
            i = n if i < 0 else i + 2
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            if inside:
                cur += text[i + 1:j].replace('\\"', '"').replace("\\\\", "\\")
            i = j + 1
            continue
        if c == "{":
            inside, fields, cur = True, [], ""
        elif c == "}":
            if inside:
                out.append(fields + [cur])
            inside = False
        elif c == "," and inside:
            fields.append(cur)
            cur = ""
        i += 1
    return out


def presets(text):
    """{item number: (name, "HDD" / "SSD" / "")}; a tagged name is meant for that kind of drive only."""
    return {int(m.group(1)): (m.group(2), m.group(3) or "") for m in _V.finditer(text)}


@functools.lru_cache(maxsize=4)
def _load(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            rows = entries(f.read())
    except OSError:
        return {}, []
    default, families = {}, []
    for row in rows:
        if len(row) < 5:
            continue
        family, model, firmware, _warn, opts = row[:5]
        if family == "DEFAULT":
            default = presets(opts)
        elif family.startswith(("VERSION", "USB:")) or model.startswith("USB:"):
            continue
        else:
            try:
                families.append((re.compile(model), re.compile(firmware) if firmware else None, presets(opts)))
            except re.error:
                continue
    return default, families


def names(model, firmware="", kind="", path=DRIVEDB):
    """{item number: name} for this drive: the DEFAULT names, with the first matching family's on top.

    A family matches when its model pattern matches the whole model and, if it has one, its firmware pattern
    the whole firmware. A name tagged HDD or SSD is used only when the drive is known to be that kind
    (`kind` "HDD", "SSD" or "NVMe"); for a drive of unknown kind it is left out rather than risk a wrong one."""
    default, families = _load(path)
    solid = {"SSD": "SSD", "NVMe": "SSD", "HDD": "HDD"}.get(kind, "")

    def fits(tag):
        return not tag or tag == solid
    out = {k: name for k, (name, tag) in default.items() if fits(tag)}
    for pattern, fw, own in families:
        if pattern.fullmatch(model or "") and (fw is None or fw.fullmatch(firmware or "")):
            out.update({k: name for k, (name, tag) in own.items() if fits(tag)})
            break
    return out
