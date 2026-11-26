# Bricks

Disky Business is built from small pieces. Each has one job, one file, one test, and one table it plugs into.
To add something, write the piece and put it in the table; no other file changes. The test
`tests/test_gui_lego.py` proves this for a theme, a block style and a tab.

## Two halves, one link

| Half | Folder | Rule |
|---|---|---|
| Engine | `disky/` | Does the work. No window code. Prints one JSON line per event (`docs/CONTRACT.md`) |
| Window | `diskygui/` | Shows it. Starts the engine as its own process and reads those lines. Takes from `disky/` only the contract table, the tip texts and two small look-up functions (file-system type, device of a folder) |

## Engine bricks

| Brick | File | Goes in | Comes out | Test |
|---|---|---|---|---|
| Command table | `disky/cli.py` `COMMANDS` | command name + one target | events, exit code | `tests/test_cli_safety.py` |
| Test data | `disky/pattern.py` | block number | f3-format data; check of data read back | `tests/test_pattern.py` |
| Fill and verify | `disky/engine.py` | folder | `phase`, `blocks`, `progress`, `summary` | `tests/test_engine.py` |
| Speed test | `disky/speed.py` | folder, size, seconds | `speed_phase`, `speed_sample`, `speed_result` | `tests/test_speed.py` |
| exFAT scan (read-only) | `disky/exfat.py` | partition or image | `frag_summary`, `frag_blocks` | `tests/test_exfat.py` |
| File fragments and defrag | `disky/frag.py` | file or folder | `frag_files`, `defrag_result` | `tests/test_frag.py` |
| TRIM | `disky/trim.py` | folder | `trim_info`, `trim_result` (runs `fstrim` only with root and only if the drive accepts it) | `tests/test_trim.py` |
| Mount table | `disky/mounts.py` | path | its mount point, file system, device; mounts below a folder (paths decoded as the kernel encodes them) | `tests/test_audit_fixes.py` |
| Stoppable tool | `disky/proc.py` | a system tool's command | its output; ends it on Stop or time-out (`.ended`) | `tests/test_audit_fixes.py` |
| SMART by smartctl | `disky/smartread.py` | a drive name | `smart_data` (admin rights; read-only; name checked strictly; no serial number passed on) | `tests/test_smartread.py` |
| SMART self-test | `disky/selftest.py` | a drive name, short or long | `selftest_progress`, `selftest_result`; Stop aborts the test | `tests/test_selftest.py` |
| Check and repair | `disky/fscare.py` | a partition name | `fs_result` (through the drive service; never mounted partitions, never NTFS) | `tests/test_fscare.py` |
| Safety rules | `disky/safety.py` | folder | system drive yes / no, space to keep free | `tests/test_cli_safety.py` |
| Event rules | `disky/contract.py` `EVENTS` | one event | list of what is wrong with it | used by every test above |
| Tips | `disky/tips.py` `TIPS` | — | lines of text | `tests/test_cli_safety.py` |

**Add an engine command:** write `run(args, emit, stop) -> exit code`, add one `Command(...)` to `COMMANDS`,
add its events to `contract.EVENTS` and its mode to `contract.MODES`, describe them in `docs/CONTRACT.md`.

## Window bricks

| Brick | File | Table | What one entry is | Test |
|---|---|---|---|---|
| Colour scheme | `diskygui/theme.py` | `THEMES` | a `Theme`: window colours, Test-map colours (`states`), Defrag-map colours (`frag`), fonts (with a smoothed fallback when the crisp fonts are missing), block styles | `test_gui_window.py`, `test_gui_lego.py` |
| Block style | `diskygui/blockstyles.py` | `STYLES` | a `BlockStyle`: size, gap, a function that draws one square | `test_gui_lego.py` |
| Widget look | `diskygui/chrome.py` | — | `stylesheet(theme)` and `font(theme)`; edges are small pictures made from the theme's colours | seen in `test_gui_window.py` |
| Tab | `diskygui/window.py` | `MainWindow.TABS` | a `TabBase` subclass (Test, Speed, SMART, Defrag, Care) | `test_gui_lego.py` |
| Care tab | `diskygui/caretab.py` (on `trimtab.py`) | — | partitions of the picked drive; `advice(volume)` says whether it can be worked on and why not; `repair_question(...)` holds the warnings shown before a repair | `test_gui_care.py`, `test_gui_trim.py` |
| Tab base | `diskygui/tabbase.py` | — | drive panel, engine runner, busy signal, legend helper, shutdown | every tab test |
| Drive panel | `diskygui/drivepanel.py` | — | drive list, health box, folder; signal `changed(folder, hint)` | `test_gui_drives.py`, `test_gui_speed.py` |
| Drive actions | `diskygui/driveactions.py` | — | `mount`, `unmount`, `remove` (unmount, then switch off) through the drive service; each returns (done, plain words). Never NTFS, never the system or home partition | `test_gui_driveactions.py` |
| Drive list | `diskygui/drives.py` | — | `read()` -> list of `Drive` (no Qt, no root) | `test_gui_drives.py` |
| Engine runner | `diskygui/runner.py` | — | `start(mode, target, *options, wrap=())`; signals `event`, `problem`, `finished` | `test_gui_window.py` |
| SMART reader | `diskygui/smart.py` | `PLAIN` | `read(drive)` -> rows (plain name, value, status, what it means, main or not, the drive's score) from the drive service; no Qt, no root. One `PLAIN` entry gives an item plain words | `test_gui_smart.py` |
| SMART names | `diskygui/smartdb.py` | — | `names(model)` -> {item number: name} from smartmontools' drive list, first matching family on top of DEFAULT, as smartctl does | `test_gui_smart.py` |
| Map model | `diskygui/mapmodel.py` | — | block states and the rule that groups them into squares (no Qt) | `test_gui_map.py` |
| Map widget | `diskygui/mapwidget.py` | — | draws a model in a theme and block style; `square_at`, `blocks_of` are ready for clicking | `test_gui_map.py` |
| Pretend engine | `tests/fake_engine.py` | — | scenarios for window tests | — |

**Add a colour scheme:** make a `Theme` in `theme.py` and add it to the tuple behind `THEMES`. The "Look"
button finds it.

**Add a block style:** write `draw(painter, size, colour)` and add a `BlockStyle` to `STYLES`. A theme picks
it with `block_style` (Test map) or `frag_style` (Defrag map).

**Add a tab:** subclass `TabBase`; set `title` and `NOTE` (and `USES_FOLDER = False` if it works on the drive itself); in `__init__` call
`super().__init__(theme, runner, tip, drives, parent)`, build the right-hand side and call
`self.assemble(layout)`. Fill in the hooks `on_folder`, `on_event`, `on_finished`, `update_buttons`,
`set_theme`. Start a job with `self.set_busy("name")` then `self.runner.start(...)`; end it with
`self.set_busy(None)`. Add the class to `MainWindow.TABS`.

## Rules every brick keeps

- A test for the brick exists, and it has been seen to fail on code broken on purpose.
- The window never does drive work itself; the engine never draws.
- Only Disky's own files are ever written or removed, and removing asks first.
- Nothing needs root except reading an exFAT partition directly (the Defrag tab says so and asks).
