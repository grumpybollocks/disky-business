# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a system tool so that Stop and a time limit end it cleanly, without a crash."""
import signal
import subprocess
import time


class Stopped(Exception):
    """The user pressed Stop."""


def run(cmd, stop=None, timeout=3600, poll=0.2, **_):
    """Like subprocess.run(capture_output=True, text=True). The result has `.ended`: "" when the tool ended by
    itself, "stopped" after Stop, "timeout" after `timeout` seconds. In the last two cases the tool is asked to
    stop (SIGINT), then made to (SIGKILL) if it has not ended 30 seconds later."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    ended = ""
    while True:
        try:
            out, err = p.communicate(timeout=poll)
            break
        except subprocess.TimeoutExpired:
            if stop is not None and stop.is_set():
                ended = "stopped"
            elif time.monotonic() > deadline:
                ended = "timeout"
            else:
                continue
            p.send_signal(signal.SIGINT)
            try:
                out, err = p.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
                out, err = p.communicate()
            break
    done = subprocess.CompletedProcess(cmd, p.returncode, out, err)
    done.ended = ended
    return done
