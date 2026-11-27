# SPDX-License-Identifier: GPL-3.0-or-later
import signal
import sys
import threading

# Catch Stop before anything else loads, so a Stop in the first moment ends the run cleanly, not by the signal.
STOP = threading.Event()
for _sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(_sig, lambda *_: STOP.set())

from .cli import main  # noqa: E402

try:
    sys.exit(main(stop=STOP))
except BrokenPipeError:
    # Whoever read the output stopped reading (`disky ... | head`). Like any command-line tool: end quietly,
    # with no traceback. Test files written so far stay, as after Stop.
    import os
    os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    sys.exit(141)
