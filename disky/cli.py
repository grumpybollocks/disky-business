# SPDX-License-Identifier: GPL-3.0-or-later
"""The disky command."""
import argparse
import json
import os
import signal
import stat
import sys
import threading

from . import __version__, advice, engine, exfat, frag, fscare, helper, mounts, proc, safety, selftest, smartdeep, smartread, speed, tips, trim

EXIT = {"good": 0, "bad": 1, "not_verified": 2, "nothing_to_check": 3, "stopped": 130}
_UNITS = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}


def parse_size(text):
    text = text.strip().upper().removesuffix("IB").removesuffix("B")
    mult = _UNITS.get(text[-1:], 1)
    number = text[:-1] if text[-1:] in _UNITS else text
    try:
        value = int(float(number) * mult)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a size: {text!r} (use e.g. 500M, 2G)")
    if value < 0:
        raise argparse.ArgumentTypeError("size cannot be negative")
    return value


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


STOPPED_WORDS = "Stopped. The test files written so far are left in place, so they can be checked or deleted later."
# Shown after a message on the command line only; the window has buttons for these.
_COMMAND_HINT = {("test", "stopped"): " Check them: disky verify <folder>   Remove them: disky clean <folder> --yes",
                 ("write", "stopped"): " Check them: disky verify <folder>   Remove them: disky clean <folder> --yes"}


NO_FILES = "No files with data here to scan."


class Printer:
    """Turns events into JSON lines (for the window) or plain words (for people)."""

    def __init__(self, as_json, out=None, err=None):
        self.as_json, self.out, self.err = as_json, out or sys.stdout, err or sys.stderr
        self.line_open = False
        self.mode = None

    def __call__(self, ev):
        if self.as_json:
            self.out.write(json.dumps(ev, separators=(",", ":")) + "\n")
            self.out.flush()
            return
        kind = ev["event"]
        if kind == "start":
            self.mode = ev.get("mode")
        if kind == "phase" and self.line_open:
            self.err.write("\n")
            self.line_open = False
        elif kind == "progress":
            word = "Writing" if ev["phase"] == "write" else "Checking"
            pct = "" if not ev["total_bytes"] else f" {ev['done_bytes'] * 100 // ev['total_bytes']}%"
            eta = "" if ev["eta_s"] is None else f"  {ev['eta_s'] // 60} min {ev['eta_s'] % 60} s left"
            self.err.write(f"\r{word}{pct}  {human(ev['done_bytes'])}  {ev['mib_per_s']} MiB/s{eta}   ")
            self.err.flush()
            self.line_open = True
        elif kind in ("warning", "error"):
            self._say(ev["message"] + _COMMAND_HINT.get((self.mode, ev.get("code")), ""))
        elif kind == "blocks" and ev["state"] not in ("written", "good"):
            at = ev.get("first_bad_offset")
            self._say(f"Block {ev['first']}" + ("" if at is None else f" (at {human(at)})") + f": {ev['state']}")
        elif kind == "summary":
            self._say(self.verdict(ev))
        elif kind == "trim_result":
            got = "" if ev["trimmed_bytes"] is None else f"{human(ev['trimmed_bytes'])} "
            self._say(f"Trimmed {got}on {ev['mount']}.")
        elif kind == "defrag_result":
            if ev["exit"] != 0:
                self._say(f"The system's tool ({ev['tool']}) did not defragment it (exit {ev['exit']})"
                          + (f": {ev['message'].rstrip('.')}." if ev.get("message") else ".")
                          + f" It is in {ev['after']} piece{'s' if ev['after'] != 1 else ''}; its content is unchanged.")
            else:
                self._say(f"{ev['before']} piece{'s' if ev['before'] != 1 else ''} before, {ev['after']} after. "
                          f"Content checked: unchanged. (tool: {ev['tool']})")
        elif kind == "done" and self.mode == "clean" and not ev.get("listed_files") and not ev.get("kept_files"):
            self._say("No test files here. Nothing to remove.")
        elif kind in ("frag_files", "frag_summary") and not ev["files"] and not ev.get("skipped"):
            self._say(NO_FILES)
        elif kind == "frag_files":
            pct = 100 * ev["fragmented_files"] / ev["files"] if ev["files"] else 0
            self._say(f"{ev['fragmented_files']} of {ev['files']} files are in more than one piece ({pct:.1f} %), "
                      f"holding {human(ev['fragmented_bytes'])} of {human(ev['file_bytes'])}.")
            if ev.get("shared_files"):
                self._say(f"{ev['shared_files']} of them ({human(ev['shared_bytes'])}) share their data with a "
                          "snapshot or a copy; defragall leaves them alone, because defragmenting would store them "
                          "a second time.")
            if ev.get("skipped"):
                self._say(f"{ev['skipped']} could not be read:")
                for s in ev.get("skipped_files") or []:
                    self._say(f"  {s.get('path')}  ({s.get('why')})")
                if ev["skipped"] > len(ev.get("skipped_files") or []):
                    self._say(f"  and {ev['skipped'] - len(ev.get('skipped_files') or [])} more")
            for w in ev.get("worst", [])[:20]:
                self._say(f"  {w['pieces']} pieces  {human(w['bytes'])}  {w['path']}"
                          + ("  (shared)" if w.get("shared") else ""))
        elif kind == "defrag_progress":
            what = "skipped" if ev["skipped"] else f"{ev['before']} -> {ev['after']} pieces"
            self._say(f"[{ev['index']}/{ev['total']}] {what}  {ev['path']}")
        elif kind == "defragall_result":
            self._say(f"Defragmented {ev['done']} of {ev['files']} files: {ev['pieces_before']} pieces before, "
                      f"{ev['pieces_after']} after; content checked unchanged."
                      + (f" Left alone: {ev['shared']} shared with a snapshot or a copy." if ev.get("shared") else "")
                      + (f" {ev['skipped']} the tool could not do." if ev["skipped"] else "")
                      + (" Stopped." if ev["stopped"] else ""))
        elif kind == "frag_summary":
            pct = 100 * ev["fragmented_files"] / ev["files"] if ev["files"] else 0
            self._say(f"{ev['fragmented_files']} of {ev['files']} files are in more than one piece ({pct:.1f} %), "
                      f"holding {human(ev['fragmented_bytes'])} of {human(ev['file_bytes'])}.")
            for w in ev.get("worst", []):
                self._say(f"  {w['pieces']} pieces  {human(w['bytes'])}  {w['path']}")
        elif kind == "fs_result":
            self._say(f"{ev['action'].capitalize()} of {ev['device']} ({ev['fs']}): {ev['message']}")
        elif kind == "selftest_progress":
            self.err.write(f"\rSelf-test running: {ev['percent']} %   ")
            self.err.flush()
            self.line_open = True
        elif kind == "selftest_result":
            self._say(f"Self-test ({ev['kind']}): {ev['message']}")
        elif kind in ("smart_data", "smart_deep"):
            self._say(self.smart_words(ev))
        elif kind == "speed_result":
            extra = "" if ev["iops"] is None else f"  ({ev['iops']} per second" + (
                ")" if ev["direct"] else ", through the system's cache)")
            if ev["read_from_drive"] is False:
                extra += "  (came partly from memory, so not the drive's real speed)"
            self._say(f"{_SPEED_WORDS[ev['test']]}: {ev['mib_per_s']} MiB/s{extra}")

    def _say(self, text):
        if self.line_open:
            self.err.write("\n")
            self.line_open = False
        self.out.write(text + "\n")
        self.out.flush()

    @staticmethod
    def smart_words(ev):
        """A few plain lines; everything is in --json (the window shows it all in words)."""
        d = ev["data"]
        g = lambda *path: _dig(d, path)  # noqa: E731
        health = g("smart_status", "passed")
        lines = [f"{ev['device']}: {d.get('model_name', 'unknown model')}"
                 + (f", firmware {d['firmware_version']}" if d.get("firmware_version") else ""),
                 "Health: " + ("passed" if health else "FAILING" if health is False else "not reported")]
        if d.get("maker"):
            lines.append(f"Maker: {d['maker']}")
        if g("power_on_time", "hours") is not None:
            lines.append(f"Hours on: {g('power_on_time', 'hours')}")
        if g("temperature", "current") is not None:
            lines.append(f"Temperature: {g('temperature', 'current')} °C")
        errors = g("ata_smart_error_log", "extended", "count") or g("ata_smart_error_log", "summary", "count")
        if errors is not None:
            lines.append(f"Errors in the drive's log: {errors}")
        if d.get("serial_number"):
            lines.append(f"Serial number: {d['serial_number']}")
        lines.append("Everything: add --json (the window shows it in plain words).")
        return "\n".join(lines)

    @staticmethod
    def verdict(ev):
        s = ev["sectors"]
        if ev["verdict"] == "good":
            return f"Drive is good. {human(ev['bytes_read'])} of test data read back from the drive, all correct."
        if ev["verdict"] == "bad":
            lost = (s["changed"] + s["bad"] + s["overwritten"]) * 512 + ev["missing_bytes"]
            return (f"Drive is BAD. {human(lost)} of {human(ev['bytes_read'] + ev['missing_bytes'])} came back "
                    "wrong or missing"
                    + ("" if ev.get("first_bad_offset") is None else f", first at {human(ev['first_bad_offset'])}")
                    + ". "
                    f"Sectors: {s['bad']} wrong, {s['changed']} slightly changed, {s['overwritten']} overwritten. "
                    + advice.BAD_DRIVE)
        if ev["verdict"] == "not_verified":
            return ("NOT VERIFIED. The data matched, but it came from the computer's memory, not from the drive "
                    f"({ev['read_from_drive_pct']}% read from the drive). Unplug the drive, plug it back in and "
                    "run: disky verify <folder>")
        if ev["verdict"] == "stopped":
            return "Stopped. Test files are left in place. Remove them with: disky clean <folder>"
        return "No test files found. Nothing was checked."


def _prepare(args, emit):
    """Return the free space to leave. Warn when the folder is on the system drive."""
    if args.keep_free is not None:
        return args.keep_free
    if safety.is_system_drive(args.folder):
        vfs = os.statvfs(args.folder)
        reserve = safety.system_reserve(vfs.f_blocks * vfs.f_frsize)
        emit({"event": "warning", "code": "system_drive",
              "message": tips.SYSTEM_DRIVE.format(free=human(reserve))})
        return reserve
    return 0


def _defrag(args, emit, stop):
    try:
        r = frag.defrag_file(args.folder, stop=stop)
    except frag.NotSupported as e:
        emit({"event": "error", "code": "not_supported", "message": str(e)})
        return 3
    except RuntimeError as e:
        emit({"event": "error", "code": "content_changed", "message": str(e)})
        return 1
    except OSError as e:
        emit({"event": "error", "code": "os_error", "message": f"{e.strerror}: {e.filename or args.folder}"})
        return 3
    emit({"event": "defrag_result", "path": args.folder, "fs": r["fs"], "tool": r["tool"], "exit": r["exit"],
          "before": r["before"], "after": r["after"], "changed_content": False, "message": r["message"]})
    if r["ended"]:
        emit({"event": "warning", "code": "stopped" if r["ended"] == "stopped" else "timeout",
              "message": ("Stopped. " if r["ended"] == "stopped" else "The tool took too long and was ended. ")
                         + "The file's content was checked afterwards and is unchanged."})
    emit({"event": "done"})
    return 130 if r["ended"] == "stopped" else 0 if r["exit"] == 0 and not r["ended"] else 3


def _fragscan(args, emit, stop):
    if os.path.isdir(args.folder):
        emit({"event": "error", "code": "not_exfat",
              "message": f"{args.folder} is a folder. fragscan reads a whole exFAT partition (like /dev/sdX1) or "
                         "an image file of one. For the files in a folder use: disky fragfiles <folder>"})
        return 3
    if not _is_block_device(args.folder) and mounts.holding(args.folder)[1] in frag.HANDS_OFF:
        emit({"event": "error", "code": "not_supported",
              "message": "Files on NTFS drives are not read: Linux may change a damaged NTFS drive just by reading it."})
        return 3
    try:
        r = exfat.scan(args.folder, stop=stop)
    except proc.Stopped:
        emit({"event": "warning", "code": "stopped", "message": "The scan was stopped."})
        emit({"event": "done"})
        return 130
    except exfat.ExfatError as e:
        emit({"event": "error", "code": "not_exfat", "message": str(e)})
        return 3
    except OSError as e:
        emit({"event": "error", "code": "os_error", "message": f"{e.strerror}: {e.filename or args.folder}"})
        return 3
    state, worst = r.pop("state"), r.pop("worst")
    emit({"event": "frag_summary", **r, "worst": [{"pieces": p, "bytes": b, "path": n} for p, b, n in worst]})
    for first, count, name in exfat.ranges(state):
        emit({"event": "frag_blocks", "first": first, "count": count, "state": name})
    emit({"event": "done"})
    return 0


def _dig(d, path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


_SPEED_WORDS = {"seq_write": "Writing in one go", "seq_read": "Reading in one go",
                "rand_read": "Small reads, random places", "rand_write": "Small writes, random places"}


def _speed(args, emit, stop):
    keep = 0
    if safety.is_system_drive(args.folder):
        vfs = os.statvfs(args.folder)
        keep = safety.system_reserve(vfs.f_blocks * vfs.f_frsize)
        emit({"event": "warning", "code": "system_drive",
              "message": "This drive runs your system: programs reading and writing at the same time lower the "
                         "figures, so they can differ from run to run. Run it twice to compare."})
    results = speed.run(args.folder, emit, size=args.size, seconds=args.seconds, stop=stop, keep_free=keep)
    emit({"event": "done", "results": results, "stopped": stop.is_set()})
    return 130 if stop.is_set() else 0


def _clean(args, emit, stop):
    files = engine.test_files(args.folder)
    leftover = os.path.join(args.folder, speed.NAME)
    try:
        if stat.S_ISREG(os.lstat(leftover).st_mode):
            files.append((-1, leftover))
    except OSError:
        pass
    kept = [p for n, p in files if n >= 0 and not engine.is_test_data(p)]
    files = [(n, p) for n, p in files if p not in kept]
    for path in kept:
        emit({"event": "warning", "code": "kept_file",
              "message": f"Kept {path}: it does not hold test data, so it may not be ours. Delete it yourself "
                         "if you are sure."})
    removed = 0
    for _, path in files:
        emit({"event": "warning", "code": "test_file",
              "message": f"{'Removing' if args.yes else 'Would remove'} {path}"})
        if args.yes:
            os.unlink(path)
            removed += 1
    emit({"event": "done", "removed_files": removed, "listed_files": len(files), "kept_files": len(kept)})
    return 0


def _fill(args, emit, stop):
    """test, write and verify: the fill-and-read-back test."""
    written = None
    if args.cmd in ("test", "write"):
        written = engine.write(args.folder, emit, size=args.size, reserve=_prepare(args, emit), stop=stop)
        if args.cmd == "write" or stop.is_set():
            emit({"event": "done", "written_bytes": written, "stopped": stop.is_set()})
            if stop.is_set():
                emit({"event": "warning", "code": "stopped", "message": STOPPED_WORDS})
            return 130 if stop.is_set() else 0
    summary = engine.verify(args.folder, emit, expect_bytes=written, stop=stop)
    return EXIT[summary["verdict"]]


def _fragfiles(args, emit, stop):
    try:
        r = frag.scan_folder(args.folder, stop=stop, with_layout=True)
    except frag.NotSupported as e:
        emit({"event": "error", "code": "not_supported", "message": str(e)})
        return 3
    base, block, state, kind = r.pop("layout")
    top = mounts.holding(args.folder)[0]
    emit({"event": "frag_files", **r})
    emit({"event": "frag_layout", "base_bytes": int(base), "block_bytes": int(block), "blocks": len(state),
          "address": "btrfs" if kind == "btrfs" else "disk",
          "whole_drive": os.path.realpath(args.folder) == top})
    for first, count, name in frag.layout_ranges(state):
        emit({"event": "frag_blocks", "first": first, "count": count, "state": name})
    emit({"event": "done", "stopped": stop.is_set()})
    return 130 if stop.is_set() else 0


def _defragall(args, emit, stop):
    try:
        r = frag.defrag_all(args.folder, emit, stop)
    except frag.NotSupported as e:
        emit({"event": "error", "code": "not_supported", "message": str(e)})
        return 3
    except RuntimeError as e:
        emit({"event": "error", "code": "content_changed", "message": str(e)})
        return 1
    emit({"event": "defragall_result", **r})
    emit({"event": "done", "stopped": r["stopped"]})
    return 130 if r["stopped"] else 0


def _smart_options(s):
    s.add_argument("--deep", action="store_true",
                   help="everything the drive knows: identity, firmware, error and self-test logs, statistics")
    s.add_argument("--serial", action="store_true", help="with --deep: include the serial number (left out by "
                   "default, because it identifies the drive)")


def _smart(args, emit, stop):
    if args.serial and not args.deep:
        emit({"event": "error", "code": "bad_options", "message": "--serial goes with --deep."})
        return 2
    try:
        data = smartdeep.read(args.folder, serial=args.serial) if args.deep else smartread.read(args.folder)
    except PermissionError as e:
        emit({"event": "error", "code": "needs_admin", "message": str(e)})
        return 3
    except ValueError as e:
        emit({"event": "error", "code": "no_smart", "message": str(e)})
        return 3
    emit({"event": "smart_deep" if args.deep else "smart_data", "device": os.path.basename(args.folder),
          "data": data})
    emit({"event": "done"})
    return 0


def _selftest(args, emit, stop):
    try:
        ok, status = selftest.run_test(args.folder, args.kind, emit, stop)
    except selftest.Refused as e:
        emit({"event": "error", "code": "refused", "message": str(e)})
        return 3
    words = selftest.RESULT.get(status, (None, f"The drive reports: {status or 'nothing'}."))[1]
    emit({"event": "selftest_result", "kind": args.kind, "status": status, "ok": ok, "message": words})
    emit({"event": "done"})
    return 130 if status == "aborted" and stop.is_set() else 0 if ok else 1


def _selftest_options(s):
    s.add_argument("--kind", choices=sorted(selftest.KINDS), default="short",
                   help="short (a few minutes) or long (the whole surface; can take hours)")


def _fscare(args, emit, stop):
    if args.cmd == "fsrepair" and not args.yes:
        emit({"event": "error", "code": "refused",
              "message": "Repair changes the drive and can remove damaged files. Copy off what matters first, "
                         "run fscheck to see whether it is needed, then add --yes."})
        return 3
    try:
        r = fscare.act("check" if args.cmd == "fscheck" else "repair", args.folder)
    except fscare.Refused as e:
        emit({"event": "error", "code": "refused", "message": str(e)})
        return 3
    emit({"event": "fs_result", **r})
    emit({"event": "done"})
    return 0 if r["ok"] else 1


def _repair_options(s):
    s.add_argument("--yes", action="store_true", help="really repair: I have a copy of what matters")


def _is_block_device(path):
    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError:
        return False


def _trim(args, emit, stop):
    facts = trim.info(args.folder)
    emit({"event": "trim_info", **facts})
    try:
        r = trim.run(args.folder, stop=stop)
    except proc.Stopped as e:
        emit({"event": "warning", "code": "stopped", "message": str(e)})
        emit({"event": "done"})
        return 130
    except ValueError as e:
        emit({"event": "error", "code": "not_supported", "message": str(e)})
        return 3
    except PermissionError as e:
        emit({"event": "error", "code": "needs_admin", "message": str(e)})
        return 3
    emit({"event": "trim_result", **r})
    emit({"event": "done"})
    return 0


def _fill_options(s):
    s.add_argument("--size", type=parse_size, help="test only this much, e.g. 500M (default: all free space)")
    s.add_argument("--keep-free", type=parse_size, help="leave this much free space, e.g. 2G")


def _speed_options(s):
    s.add_argument("--size", type=parse_size, default=1 << 30, help="size of the temporary file (default 1G)")
    s.add_argument("--seconds", type=float, default=5.0, help="length of each random test (default 5)")


def _clean_options(s):
    s.add_argument("--yes", action="store_true", help="really remove the listed test files")


_FILL_THERE = ("Test files (.h2w) are already in this folder. Disky never overwrites or deletes them on its own. "
               "Check them with: disky verify <folder>   Remove them with: disky clean <folder> --yes")
_SPEED_THERE = ("A speed-test file from an earlier run is still there: {e}. "
                "Remove it with: disky clean <folder> --yes")


def _helper(args, emit, stop):
    if os.geteuid() != 0:
        emit({"event": "error", "code": "needs_admin", "message": "The admin helper needs admin rights."})
        return 3

    def job(argv, job_stop):
        parsed = _parser().parse_args(argv)
        return _run(COMMANDS[parsed.cmd], parsed, emit, job_stop)
    return helper.serve(emit, job)


class Command:
    """One engine command. To add one: write its handler `run(args, emit, stop) -> exit code` and put a
    Command in COMMANDS. `target` says what the one argument is: "folder" must exist as a folder."""

    def __init__(self, name, help, run, options=None, target="folder", target_help="a folder on the drive",
                 existing=_FILL_THERE):
        self.name, self.help, self.run, self.options = name, help, run, options
        self.target, self.target_help, self.existing = target, target_help, existing


COMMANDS = {c.name: c for c in (
    Command("test", "write test files, then check them", _fill, _fill_options),
    Command("write", "write test files only", _fill, _fill_options),
    Command("verify", "check test files that are already there", _fill),
    Command("clean", "list the test files; remove them with --yes", _clean, _clean_options),
    Command("speed", "measure read and write speed with one temporary file", _speed, _speed_options,
            existing=_SPEED_THERE),
    Command("fragfiles", "read-only: how fragmented the files in a folder are (btrfs, ext4, XFS)", _fragfiles),
    Command("defrag", "defragment one file with the system's own tool (btrfs, ext4, XFS)", _defrag,
            target="any", target_help="the file"),
    Command("defragall", "defragment every fragmented file in a folder with the system's own tool, content "
            "checked after each (btrfs, ext4, XFS)", _defragall),
    Command("trim", "tell the drive which space is free (the system's fstrim; needs admin rights)", _trim),
    Command("helper", "the admin helper the window keeps for a session: runs trim, fragscan, smart, selftest, "
            "fscheck and fsrepair on request (see disky/helper.py)", _helper, target="any",
            target_help="a name for the session; not used"),
    Command("smart", "read-only: the drive's SMART record through smartctl (needs admin rights; for USB cases "
            "and the deep scan)", _smart, _smart_options, target="any", target_help="the drive, e.g. sdX"),
    Command("selftest", "let the drive run its own SMART self-test and follow it (the system asks for the "
            "admin password)", _selftest, _selftest_options, target="any", target_help="the drive, e.g. sdX"),
    Command("fscheck", "read-only: is this partition's file system sound? (the system's own tool)", _fscare,
            target="any", target_help="the partition, e.g. sdX1; it must not be mounted"),
    Command("fsrepair", "repair this partition's file system with the system's own tool; it CHANGES the drive",
            _fscare, _repair_options, target="any", target_help="the partition, e.g. sdX1; it must not be mounted"),
    Command("fragscan", "read-only fragmentation report of an exFAT partition or image (needs the right to read it)",
            _fragscan, target="any", target_help="the partition, e.g. /dev/sdX1, or an image file"),
)}


def _listen(stop):
    """With --stdin-stop: a line "stop" on standard input stops the run. The window uses this, because it
    cannot send a signal to an engine that runs with admin rights."""
    for line in sys.stdin:
        if line.strip() == "stop":
            stop.set()
            return


def _parser():
    p = argparse.ArgumentParser(prog="disky", description="Fill a drive, read it back, see every block.")
    p.add_argument("--version", action="version", version=f"disky {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    for c in COMMANDS.values():
        s = sub.add_parser(c.name, help=c.help)
        s.add_argument("folder", metavar="target" if c.target == "any" else "folder", help=c.target_help)
        s.add_argument("--json", action="store_true", help="print one JSON line per event")
        s.add_argument("--stdin-stop", action="store_true", help="stop when the line 'stop' arrives on input")
        if c.options:
            c.options(s)
    return p


def main(argv=None, stop=None):
    args = _parser().parse_args(argv)
    command = COMMANDS[args.cmd]
    emit = Printer(args.json)
    stop = stop or threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if args.stdin_stop and args.cmd != "helper":        # the helper reads its input itself
        threading.Thread(target=_listen, args=(stop,), daemon=True).start()
    return _run(command, args, emit, stop)


def _run(command, args, emit, stop):
    """One command from start event to exit code. Used by main and, job after job, by the admin helper."""
    if command.target == "folder" and not os.path.isdir(args.folder):
        emit({"event": "error", "code": "no_folder", "message": f"Not a folder: {args.folder}"})
        return 3
    emit({"event": "start", "contract": engine.CONTRACT_VERSION, "version": __version__, "mode": args.cmd,
          "block_bytes": engine.BLOCK, "sector_bytes": 512})
    if stop.is_set():                   # Stop came while starting up
        emit({"event": "warning", "code": "stopped", "message": "Stopped before it began. Nothing was done."})
        emit({"event": "done"})
        return 130
    try:
        return command.run(args, emit, stop)
    except engine.ExistingFiles as e:
        emit({"event": "error", "code": "existing_files", "message": command.existing.format(e=e)})
        return 3
    except BrokenPipeError:
        raise                           # the reader is gone (e.g. `| head`): nobody to tell; __main__ ends quietly
    except OSError as e:
        emit({"event": "error", "code": "os_error", "message": f"{e.strerror}: {e.filename or args.folder}"})
        return 3
