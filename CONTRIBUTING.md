# Contributing

## Run it from the source

```
python3 -m diskygui                                                  # the window
python3 -m disky test /some/folder --size 64M                        # the engine alone
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests      # the tests (a few seconds)
```

Needs Python 3.11+, numpy 2 and PySide6 (below 6.8 on Python 3.11). The tests need no drive and no password.

## How it is put together

Two halves joined by one thing. The engine (`disky/`) does the work and prints one JSON line per event
([docs/CONTRACT.md](docs/CONTRACT.md)). The window (`diskygui/`) starts the engine as its own process and shows
those lines. Each piece has one job, one file and one test, and plugs into a table; adding a tab, a colour
scheme or an engine command is one entry ([docs/BRICKS.md](docs/BRICKS.md)).

## Rules

- **People's files come first.** The app writes and removes only its own test files. Anything that can change
  a drive asks first and says what it will do.
- **Never read NTFS drives automatically.** Linux may change a damaged NTFS drive just by reading it.
- **No file-system code of our own for changing drives.** Mounting, repairing, defragmenting and trimming go
  through the system's tools (udisks2, `e4defrag`, `btrfs`, `xfs_fsr`, `fstrim`).
- **Every test must be seen to fail.** Break the code it guards on purpose, watch the test fail, restore.
  A test that passes on broken code is fixed before it is trusted.
- **Say what was really checked.** "Works" means it ran on real hardware; otherwise say what was not run.
- **No personal details** in code, documents or pictures: no user names, host names, drive serial numbers.
- Connect Qt signals to plain methods, not to lambdas that hold the widget (that crashed on exit).

## Not yet tried on real hardware

Repair of a file system that is really damaged, Safely remove, stopping a self-test midway, a long self-test,
and the exFAT scan started from the window. Reports from other machines and drives are welcome.
