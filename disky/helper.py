# SPDX-License-Identifier: GPL-3.0-or-later
"""The admin helper: started once through the system's password prompt, it stays until the window closes and
runs the jobs that need admin rights, so the password is asked once per session and not once per job.

Because it keeps admin rights for a while, it is strict:
- it runs only the commands in ALLOWED, with only the options listed there, one at a time;
- every target goes through the same checks the command applies when run on its own;
- it takes requests only from its own input (the window's pipe), never from a file or the network;
- it ends when its input closes (the window is gone) or after IDLE_SECONDS without a job.

A request is one JSON line: {"cmd": "trim", "target": "/some/folder", "options": []}. The line "stop" stops
the job that is running. Every job ends with {"event": "helper_done", "code": <the command's exit code>}.
"""
import json
import queue
import sys
import threading

# command -> the options the window may pass (a value is listed when the option takes one)
ALLOWED = {
    "trim": {},
    "fragscan": {},
    "smart": {"--deep": None, "--serial": None},
    "selftest": {"--kind": ("short", "long")},
    "fscheck": {},
    "fsrepair": {"--yes": None},
}
IDLE_SECONDS = 900
MAX_LINE = 8192


class Refused(Exception):
    """A request the helper will not run."""


def request(line):
    """A request line -> argv for the command line parser. Raises Refused for anything not allowed."""
    if len(line) > MAX_LINE:
        raise Refused("The request is too long.")
    try:
        req = json.loads(line)
    except ValueError:
        raise Refused("The request is not readable.") from None
    if not isinstance(req, dict):
        raise Refused("The request is not readable.")
    cmd, target, options = req.get("cmd"), req.get("target"), req.get("options", [])
    if cmd not in ALLOWED:
        raise Refused(f"The admin helper does not do {str(cmd)[:40]!r}.")
    if not isinstance(target, str) or not target or target.startswith("-") or "\0" in target or "\n" in target:
        raise Refused("The target of the request is not acceptable.")
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise Refused("The options of the request are not acceptable.")
    allowed, i, checked = ALLOWED[cmd], 0, []
    while i < len(options):
        name = options[i]
        if name not in allowed:
            raise Refused(f"The admin helper does not accept {name[:40]!r} for {cmd}.")
        checked.append(name)
        if allowed[name] is not None:
            if i + 1 >= len(options) or options[i + 1] not in allowed[name]:
                raise Refused(f"The value for {name} is not acceptable.")
            checked.append(options[i + 1])
            i += 1
        i += 1
    return [cmd, target, "--json"] + checked


def serve(emit, run_job, lines=None, idle=IDLE_SECONDS):
    """Take requests until the input closes or nothing came for `idle` seconds. `run_job(argv, stop)` runs one
    command and returns its exit code. `lines` is an iterable of input lines (standard input by default)."""
    todo = queue.Queue()
    current = {"stop": None}

    def read():
        for raw in (lines if lines is not None else sys.stdin):
            line = raw.strip()
            if line == "stop":
                if current["stop"] is not None:
                    current["stop"].set()
            elif line:
                todo.put(line)
        todo.put(None)                              # the window is gone
    threading.Thread(target=read, daemon=True).start()
    emit({"event": "helper_ready"})
    while True:
        try:
            line = todo.get(timeout=idle)
        except queue.Empty:
            emit({"event": "helper_closed", "reason": "idle"})
            return 0
        if line is None:
            return 0
        try:
            argv = request(line)
        except Refused as e:
            emit({"event": "error", "code": "refused", "message": str(e)})
            emit({"event": "helper_done", "code": 3})
            continue
        current["stop"] = threading.Event()
        try:
            code = run_job(argv, current["stop"])
        except SystemExit:                           # the command line parser turned the request down
            emit({"event": "error", "code": "refused", "message": "The request was not understood."})
            code = 3
        except Exception as e:                       # one failed job must not end the session
            emit({"event": "error", "code": "os_error", "message": f"{e.__class__.__name__}: {e}"[:300]})
            code = 3
        current["stop"] = None
        emit({"event": "helper_done", "code": int(code)})
