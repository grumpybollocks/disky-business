# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs the engine as its own process and hands its JSON lines to the window. The only link between the two.

    runner.event     one engine event (a dict)
    runner.problem   a line that is not a proper event; the run goes on
    runner.finished  exit code and an error text ("" when the engine ended by itself)
"""
import json
import os
import signal
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from disky.contract import EVENTS, problems

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class EngineRunner(QObject):
    event = Signal(dict)
    problem = Signal(str)
    finished = Signal(int, str)

    def __init__(self, command=None, parent=None):
        super().__init__(parent)
        self.command = list(command or [sys.executable, "-m", "disky"])
        self._proc = None
        self._buf = b""
        self.session = None                             # the window's AdminSession, when it has one

    def _via_session(self):
        return self.session is not None and self.session.client is self

    def running(self):
        return self._via_session() or (self._proc is not None and self._proc.state() != QProcess.NotRunning)

    def start(self, mode, folder, *extra, wrap=(), admin=""):
        """Start one engine job. `wrap` is the password prompt put in front of it. With the window's admin
        session, a job behind the real prompt goes to the session instead (one password for the whole
        session); `admin="if_open"` uses the session only when it is already open."""
        if self.running():
            return False
        s = self.session
        if s is not None and s.accepts(mode) and ((wrap and list(wrap) == s.prefix) or
                                                  (admin == "if_open" and s.is_open())):
            self.last_argv = list(s.prefix) + self.command + [mode, folder, "--json", "--stdin-stop", *extra]
            return s.start(self, mode, folder, *extra)
        self._buf = b""
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", _ROOT + os.pathsep + env.value("PYTHONPATH", ""))
        env.insert("PYTHONUNBUFFERED", "1")
        p.setProcessEnvironment(env)
        argv = list(wrap) + self.command + [mode, folder, "--json", "--stdin-stop", *extra]
        self.last_argv = argv
        p.setProgram(argv[0])
        p.setArguments(argv[1:])
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._done)
        p.errorOccurred.connect(self._error)
        self._proc = p
        p.start()
        return True

    def stop(self):
        """Ask the engine to stop cleanly (it finishes the block in hand and leaves the test files).

        A line "stop" on its input works also when it runs with admin rights; a signal from us would not."""
        if self._via_session():
            self.session.stop()
        elif self.running():
            self._proc.write(b"stop\n")
            try:
                os.kill(self._proc.processId(), signal.SIGINT)
            except OSError:
                pass                    # an admin process: the line above does it

    def kill(self):
        if self._via_session():
            self.session.stop()
        elif self.running():
            self._proc.kill()
            self._proc.waitForFinished(2000)

    # -- internals -----------------------------------------------------------------------------------------
    def _read(self):
        self._buf += bytes(self._proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            self._line(line)

    def _line(self, raw):
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return
        try:
            ev = json.loads(text)
        except json.JSONDecodeError:
            ev = None
        if not isinstance(ev, dict) or "event" not in ev:
            self.problem.emit("not an engine event: " + text[:120])
            return
        if ev["event"] in EVENTS:                 # events from a newer engine are passed on unchecked
            found = problems(ev)
            if found:
                self.problem.emit(f"{ev['event']}: {'; '.join(found)}")
        self.event.emit(ev)

    def _error(self, err):
        if err == QProcess.FailedToStart:
            self._proc = None
            self.finished.emit(-1, "The engine could not be started.")

    def _done(self, code, status):
        self._read()
        if self._buf:
            self._line(self._buf)
            self._buf = b""
        crashed = status == QProcess.CrashExit and code != 130
        tail = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace").strip().splitlines()
        self._proc = None
        if crashed:
            self.finished.emit(-1, "The engine stopped unexpectedly." + (f" ({tail[-1][:160]})" if tail else ""))
        else:
            self.finished.emit(code, "")
