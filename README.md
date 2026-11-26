# Disky Business

**Can this drive be trusted?** Disky Business fills a drive with test data, reads every block back and shows
the answer on a live block map, the way old defragmenters did.

![The Test tab after a check: every block written, read back and found good (example drives)](docs/screenshots/test.png)

For a second-hand SSD, a USB stick that may lie about its size, or an old disk you are not sure of.
Linux only. Works, not yet released.

## What it does

| Tab | |
|---|---|
| **Test** | Fills the free space, reads it back from the drive (not from memory) and gives a plain verdict. Finds fake-capacity sticks and bad blocks. Uses the f3 / H2testw method and file format |
| **Speed** | Sequential and small random reads and writes, with a graph along the file |
| **SMART** | The drive's own health record in plain words: worn-out spots, unreadable spots, cable errors, life left, temperature. Short and long self-tests. Drives in USB cases are read with `smartctl` after asking |
| **Defrag** | How fragmented the files are. Defragments one file at a time on btrfs, ext4 and XFS with the system's own tool, then proves the content is unchanged |
| **Care** | Checks a partition's file system and repairs it with the system's own tool, after showing the warnings that apply. Also runs TRIM where the drive accepts it |

![The SMART tab in plain words (example drive)](docs/screenshots/smart.png)

Three looks: Windows 98 style (above), Dark and Deep violet (top).

## Safe by design

- Never overwrites or deletes your files. It writes only its own test files, never into a folder that already
  has some, and removes only files that really hold its test data, after asking.
- Leaves free space on the drive your system runs from, and checks it again while writing.
- Never reads Windows (NTFS) drives, which Linux may change just by reading them.
- No password needed, except for TRIM, the exFAT scan and work on drives that are not removable, and it asks
  you first.
- Stop works at any moment (a repair, once started, is left to finish).
- Mount, Unmount and Safely remove are in the drive list; the system and home partitions are never unmounted.

## Tested on real drives

| Drive | Result |
|---|---|
| 120 GB SATA SSD in a USB case | Whole drive, written and read back: good, 40 min. One sector damaged on purpose was found exactly |
| 500 GB hard disk in a USB case | 360 GiB of free space, written and read back: good, 5 h; the files already on it untouched |
| Internal NVMe | TRIM through the window: 140 GiB trimmed |
| 7 internal drives | SMART read and explained; item names match `smartctl` |

Test files are read correctly by `f3read` 8.0, and `f3write` 8.0's files by Disky Business.

## Run it

Needs Python 3.11+, numpy 2 and PySide6. Drive list and SMART come from udisks2; smartmontools, if installed,
supplies makers' names for SMART items.

```
git clone https://github.com/grumpybollocks/disky-business && cd disky-business
python3 -m diskygui                     # the window
pip install '.[gui]'                    # or install it: gives `disky-business` and `disky`
```

The engine also runs on its own: `disky test|verify|clean|speed|fragfiles|defrag|fragscan|trim <target>`
(`--json` gives one line per event; see [docs/CONTRACT.md](docs/CONTRACT.md)).

## More

[How to use it](docs/GUIDE.md) · [How it is built](docs/BRICKS.md) · [What the engine prints](docs/CONTRACT.md) ·
[Contributing](CONTRIBUTING.md)

Built on the method and file format of [f3](https://github.com/AltraMayor/f3) by Michel Machado, which follows
H2testw by Harald Bögeholz. The code is Disky Business's own.

GPL-3.0-or-later.
