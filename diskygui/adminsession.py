# SPDX-License-Identifier: GPL-3.0-or-later
"""One admin session for the whole window: the password is asked once, then the engine's admin helper
(`disky helper`) stays until the window closes and runs the jobs that need admin rights.

An EngineRunner hands a job to the session instead of starting its own process behind a password prompt. The
helper runs only its fixed list of jobs (disky/helper.py). The session ends when the window closes, when the
user presses "End", or when the helper closes itself after a quarter of an hour without work.
"""
import json
import os
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = ("trim", "fragscan", "smart", "selftest", "fscheck", "fsrepair")    # what the helper accepts


def admin_wrap():
    """What is put in front of the engine to run it through the system's password prompt."""
    return ["pkexec", "env", f"PYTHONPATH={_ROOT}", "PYTHONDONTWRITEBYTECODE=1"]


class AdminSession(QObject):
    changed = Signal(bool)                              # True when the helper is ready, False when it is gone

    def __init__(self, prefix=None, command=None, parent=None):
        super().__init__(parent)
        self.prefix = list(admin_wrap() if prefix is None else prefix)
        self.command = list(command or [sys.executable, "-m", "disky"])
        self._proc = None
        self._buf = b""
        self._waiting = None                            # a request written as soon as the helper is ready
        self.client = None                              # the runner whose job is running
        self.ready = False
        self.launches = 0                               # how often the password prompt was needed

    def is_open(self):
        return self.ready and self._proc is not None and self._proc.state() != QProcess.NotRunning

    def accepts(self, mode):
        return mode in JOBS

    def start(self, client, mode, target, *options):
        """Run one job for `client` (an EngineRunner). False when another job is running."""
        if self.client is not None or not self.accepts(mode):
            return False
        self.client = client
        line = (json.dumps({"cmd": mode, "target": target, "options": list(options)}) + "\n").encode()
        if self.is_open():
            self._proc.write(line)
            return True
        self._waiting = line
        if self._proc is None:
            self._launch()
        return True

    def stop(self):
        if self.is_open() and self.client is not None:
            self._proc.write(b"stop\n")

    def end(self):
        """Close the session: the helper sees its input close and ends."""
        p = self._proc
        if p is not None and p.state() != QProcess.NotRunning:
            p.closeWriteChannel()
            if not p.waitForFinished(2000):             # it did not end by itself: do not leave it behind
                p.kill()
                p.waitForFinished(1000)

    # -- internals -----------------------------------------------------------------------------------------
    def _launch(self):
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", _ROOT + os.pathsep + env.value("PYTHONPATH", ""))
        env.insert("PYTHONUNBUFFERED", "1")
        p.setProcessEnvironment(env)
        argv = self.prefix + self.command + ["helper", "session", "--json"]
        p.setProgram(argv[0])
        p.setArguments(argv[1:])
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._done)
        p.errorOccurred.connect(self._error)
        self._proc, self._buf, self.ready = p, b"", False
        self.launches += 1
        p.start()

    def _read(self):
        if self._proc is None:
            return
        self._buf += bytes(self._proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            self._line(raw)

    def _line(self, raw):
        try:
            ev = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            ev = None
        kind = ev.get("event") if isinstance(ev, dict) else None
        if kind == "helper_ready":
            self.ready = True
            self.changed.emit(True)
            if self._waiting is not None:
                self._proc.write(self._waiting)
                self._waiting = None
        elif kind == "helper_done":
            client, self.client = self.client, None
            if client is not None:
                client.finished.emit(int(ev.get("code", 0)), "")
        elif kind == "helper_closed" or (not self.ready and kind in ("start", "error")):
            if kind == "error" and self.client is not None:     # the helper could not start (no admin rights)
                self.client._line(raw)
        elif self.client is not None and raw.strip():
            self.client._line(raw)                              # the job's own events, checked by the runner

    def _error(self, err):
        if err == QProcess.FailedToStart:
            self._gone(-1, "The password prompt could not be started.")

    def _done(self, code, status):
        self._read()
        crashed = status == QProcess.CrashExit
        self._gone(code, "The admin helper ended unexpectedly." if crashed else "")

    def _gone(self, code, crash):
        was_ready, ended = self.ready, self._proc
        self._proc, self.ready, self._waiting = None, False, None
        if ended is not None:
            ended.deleteLater()                         # released now, on this thread, not whenever its owner goes
        client, self.client = self.client, None
        if was_ready:
            self.changed.emit(False)
        if client is not None:
            if not crash and was_ready and code == 0:
                crash = "The admin session ended before the job did."
            client.finished.emit(-1 if crash else code, crash)
