# SPDX-License-Identifier: GPL-3.0-or-later
"""The event contract between engine and window, as data. Words: docs/CONTRACT.md."""
INT, NUM, STR, BOOL, DICT = int, (int, float), str, bool, dict
OPT_INT, OPT_NUM, OPT_BOOL = (int, type(None)), (int, float, type(None)), (bool, type(None))
_SECTORS = {"good", "changed", "bad", "overwritten"}

EVENTS = {
    "start": {"contract": INT, "version": STR, "mode": STR, "block_bytes": INT, "sector_bytes": INT},
    "phase": {"phase": STR, "total_bytes": OPT_INT, "free_bytes": OPT_INT},
    "file": {"phase": STR, "name": STR},
    "blocks": {"phase": STR, "first": INT, "count": INT, "state": STR},
    "progress": {"phase": STR, "done_bytes": INT, "total_bytes": OPT_INT, "mib_per_s": NUM,
                 "avg_mib_per_s": NUM, "eta_s": OPT_INT},
    "warning": {"code": STR, "message": STR},
    "error": {"code": STR, "message": STR},
    "summary": {"verdict": STR, "bytes_read": INT, "sectors": DICT, "missing_bytes": INT,
                "first_bad_offset": OPT_INT, "read_from_drive_pct": OPT_NUM, "read_from_drive": BOOL},
    "done": {},
    "frag_summary": {"cluster_bytes": INT, "clusters": INT, "used_clusters": OPT_INT, "files": INT, "folders": INT,
                     "fragmented_files": INT, "pieces": INT, "file_bytes": INT, "fragmented_bytes": INT},
    "frag_blocks": {"first": INT, "count": INT, "state": STR},
    "frag_files": {"files": INT, "fragmented_files": INT, "pieces": INT, "file_bytes": INT,
                   "fragmented_bytes": INT, "skipped": INT},
    "defrag_result": {"path": STR, "fs": STR, "tool": STR, "exit": INT, "before": INT, "after": INT,
                      "changed_content": BOOL},
    "trim_info": {"mount": STR, "device": STR, "supported": BOOL, "discard_max_bytes": INT,
                  "discard_granularity": INT, "rotational": (bool, type(None))},
    "trim_result": {"mount": STR, "trimmed_bytes": OPT_INT, "message": STR},
    "selftest_progress": {"status": STR, "percent": INT},
    "selftest_result": {"kind": STR, "status": STR, "ok": OPT_BOOL, "message": STR},
    "defrag_progress": {"index": INT, "total": INT, "path": STR, "before": INT, "after": INT, "skipped": BOOL},
    "defragall_result": {"files": INT, "done": INT, "improved": INT, "skipped": INT, "pieces_before": INT,
                         "pieces_after": INT, "stopped": BOOL},
    "helper_ready": {},
    "helper_done": {"code": INT},
    "helper_closed": {"reason": STR},
    "smart_data": {"device": STR, "data": DICT},
    "smart_deep": {"device": STR, "data": DICT},
    "fs_result": {"action": STR, "device": STR, "fs": STR, "ok": BOOL, "message": STR},
    "speed_phase": {"test": STR, "total_bytes": OPT_INT, "seconds": OPT_NUM},
    "speed_sample": {"test": STR, "offset_bytes": INT, "mib_per_s": NUM},
    "speed_result": {"test": STR, "mib_per_s": NUM, "iops": OPT_INT, "direct": OPT_BOOL,
                     "read_from_drive": OPT_BOOL},
}
FRAG_STATES = {"free", "whole", "fragmented", "other"}
SPEED_TESTS = {"seq_write", "seq_read", "rand_read", "rand_write"}
PHASES = {"write", "verify"}
MODES = {"test", "write", "verify", "clean", "speed", "fragscan", "defrag", "fragfiles", "trim", "fscheck", "fsrepair", "smart", "selftest", "helper", "defragall"}
BLOCK_STATES = {"written", "good", "changed", "bad", "overwritten"}
VERDICTS = {"good", "bad", "not_verified", "nothing_to_check", "stopped"}


def problems(ev):
    """Return a list of what is wrong with one event (empty = fine)."""
    if not isinstance(ev, dict) or ev.get("event") not in EVENTS:
        return [f"unknown event: {ev!r}"[:120]]
    kind, out = ev["event"], []
    for field, kinds in EVENTS[kind].items():
        if field not in ev:
            out.append(f"{kind}: missing {field}")
        elif isinstance(ev[field], bool) and kinds not in (BOOL, OPT_BOOL) or not isinstance(ev[field], kinds):
            out.append(f"{kind}: {field} has the wrong type")
    if out:
        return out
    if "phase" in ev and ev["phase"] not in PHASES:
        out.append(f"{kind}: unknown phase {ev['phase']}")
    if "test" in EVENTS[kind] and ev["test"] not in SPEED_TESTS:
        out.append(f"{kind}: unknown test {ev['test']}")
    if kind == "start" and ev["mode"] not in MODES:
        out.append(f"start: unknown mode {ev['mode']}")
    if kind == "frag_blocks" and ev["state"] not in FRAG_STATES:
        out.append(f"frag_blocks: unknown state {ev['state']}")
    if kind == "blocks":
        if ev["state"] not in BLOCK_STATES:
            out.append(f"blocks: unknown state {ev['state']}")
        if ev["first"] < 0 or ev["count"] < 1:
            out.append("blocks: first must be >= 0 and count >= 1")
        if ev["state"] in ("changed", "bad", "overwritten"):
            if ev["count"] != 1 or set(ev.get("sectors", {})) != _SECTORS or "first_bad_offset" not in ev:
                out.append("blocks: a damaged block needs count 1, sectors and first_bad_offset")
    if kind == "summary":
        if ev["verdict"] not in VERDICTS:
            out.append(f"summary: unknown verdict {ev['verdict']}")
        if set(ev["sectors"]) != _SECTORS:
            out.append("summary: sectors must hold good, changed, bad, overwritten")
    return out
