# Contract between engine and window (version 1)

`disky <command> <folder> --json` prints one JSON object per line. The window reads these lines and draws.
Nothing else passes between the two. The same rules as data: `disky/contract.py`. Check any output with
`tools/check_events.py`.

## Units

| Thing | Unit |
|---|---|
| Sizes and offsets | bytes |
| Sector | 512 bytes |
| Block (one map unit) | 1 MiB = 2,048 sectors. Block `n` covers test-data bytes `n * 1 MiB` up to the next MiB |
| Speeds | MiB per second |
| Time left | seconds |

Test-data offset: file `N.h2w` starts at `(N - 1) * 1 GiB`, as in f3. The window groups blocks into squares.

## Events

Every line has `"event"`. Fields marked `?` may be `null`.

| Event | Fields | Meaning |
|---|---|---|
| `start` | `contract`, `version`, `mode` (`test` `write` `verify` `clean`), `block_bytes`, `sector_bytes` | First line |
| `phase` | `phase` (`write` `verify`), `total_bytes?`, `free_bytes?` | A pass begins. When filling until the drive is full, `total_bytes` is the free space (an estimate; the file system's own bookkeeping takes a little) |
| `file` | `phase`, `name` | A test file is opened |
| `blocks` | `phase`, `first`, `count`, `state` | `count` blocks from `first` share one state: `written`, `good`, `changed`, `bad`, `overwritten` |
| `progress` | `phase`, `done_bytes`, `total_bytes?`, `mib_per_s`, `avg_mib_per_s`, `eta_s?` | At most twice a second |
| `warning` | `code`, `message` | Something the user should read. `message` is ready to show |
| `error` | `code`, `message` | The run cannot go on |
| `summary` | `verdict`, `bytes_read`, `sectors`, `missing_bytes`, `first_bad_offset?`, `read_from_drive_pct?`, `read_from_drive` | Result of the verify pass |
| `done` | free-form extras | Last line of `write` and `clean` |

A damaged block (`changed`, `bad`, `overwritten`) always comes alone (`count` 1) and adds
`sectors` (`good`, `changed`, `bad`, `overwritten` counts) and `first_bad_offset`. Its `state` is the worst
found: `bad`, then `overwritten`, then `changed`.

## Sector states (f3's rules, version 10.0)

| State | Rule |
|---|---|
| `good` | Holds exactly the data for its place |
| `changed` | Right place, 1 to 7 bits differ |
| `bad` | More than 7 bits differ |
| `overwritten` | Holds valid data that belongs to another place (a drive that lies about its size) |

## Verdicts and exit codes

| `verdict` | Exit | Meaning |
|---|---|---|
| `good` | 0 | All data correct, and at least 99 % of it was fetched from the drive |
| `bad` | 1 | Any sector not good, or data missing |
| `not_verified` | 2 | Data matched, but the kernel did not fetch it from the drive, so it proves nothing |
| `nothing_to_check` | 3 | No test files |
| `stopped` | 130 | Stopped by the user; test files are left in place |

Other exits: 3 for an `error` event.

## Warning and error codes

`system_drive`, `short_file`, `stopped`, `test_file`, `kept_file` (a file with a test file's name but no test data in it; `clean` never removes it), `reserve_reached` (free space dropped during writing; writing stopped to keep the reserve), `missing_file` (a test file in the middle of the numbering is gone; counted as missing), `timeout` (a system tool ran past its time limit and was ended) (warnings); `no_folder`, `existing_files`, `os_error` (errors).

## Defragment all (added 2026-11-27, still contract version 1)

`disky defragall <folder>`: every fragmented file under the folder, the most fragmented first, each with the
system's own tool and each checked afterwards to be unchanged. Stop ends it after the file in hand (exit 130).
A file the tool cannot handle is skipped. A changed file is an `error` `content_changed` and ends the run.

| Event | Fields | Meaning |
|---|---|---|
| `defrag_progress` | `index`, `total`, `path`, `before`, `after`, `skipped` | One file done: its pieces before and after |
| `defragall_result` | `files`, `done`, `improved`, `skipped`, `pieces_before`, `pieces_after`, `stopped` | The totals |

## SMART through smartctl (added 2026-11-26, still contract version 1)

`disky smart <drive>` (a name like `sdb`), started by the window through the password prompt for drives the
drive service cannot read (USB cases). Only reads. `smart_data` has `device` and `data`: the parts of
`smartctl -j -H -A -i` the window shows (`smart_status`, `ata_smart_attributes` or
`nvme_smart_health_information_log`, model, firmware). Errors: `needs_admin`, `no_smart` (the case passes
nothing on; smartctl's own message is included).

## SMART self-test (added 2026-11-26, still contract version 1)

`disky selftest <drive> [--kind short|long]` (a name like `sdb`). The drive tests itself; the drive service
starts it (asking for the admin password itself), and the engine follows it. Stop aborts the test on the drive
(exit 130). Not possible through USB cases the service cannot talk through: `error` with code `refused`.

| Event | Fields | Meaning |
|---|---|---|
| `selftest_progress` | `status`, `percent` | Still running; sent when the percentage changes |
| `selftest_result` | `kind`, `status`, `ok`, `message` | `ok`: true passed, false failed, null not run to the end. Exit code 0 passed, 1 otherwise |

## Check and repair (added 2026-11-26, still contract version 1)

`disky fscheck <partition>` and `disky fsrepair <partition> --yes` (a name like `sdb1`; without `--yes` a
repair is refused, so it never happens by a slip). Both ask the system's drive
service, which runs the file system's own tool and asks for the admin password itself where the system wants
one. `fscheck` only reads; `fsrepair` changes the drive. Neither runs on a mounted partition or on NTFS: that
is an `error` with code `refused` and the reason in `message`. There is no Stop for these two.

| Event | Fields | Meaning |
|---|---|---|
| `fs_result` | `action` (`check` / `repair`), `device`, `fs`, `ok`, `message` | `ok`: the file system is sound (check) or was repaired (repair). Exit code 0 when `ok`, else 1 |

## The admin helper (added 2026-11-27, still contract version 1)

`disky helper <name>` is started once by the window through the password prompt and stays for the session, so
the password is asked once and not once per job. It prints `start`, then `helper_ready`. A request is one JSON
line on its input: `{"cmd": "trim", "target": "/folder", "options": []}`. It runs only `trim`, `fragscan`,
`smart`, `selftest`, `fscheck` and `fsrepair`, with only `--kind short|long` (selftest) and `--yes` (fsrepair)
as options, one job at a time; anything else is an `error` with code `refused`. A job prints its own events
(`start` first) and ends with `helper_done` (`code`: that job's exit code). The line `stop` stops the running
job. The helper ends when its input closes or after 15 minutes without a job (`helper_closed`, `reason`
`idle`).

## Stopping

SIGINT or SIGTERM stops a run cleanly (exit 130). With `--stdin-stop`, a line `stop` on standard input does the
same; the window always passes it, because it cannot send a signal to an engine running with admin rights.
`defrag`, `trim` and `fragscan` stop too: the system tool is asked to stop, and a defragmented file is still
checked afterwards. A Stop that arrives while the engine starts gives `start`, a `stopped` warning and `done`.

## Changing the contract

New fields and new events may be added without a new version; a window must ignore what it does not know.
Removing or renaming anything raises `contract`.

## Speed test (added 2026-11-26, still contract version 1)

`disky speed <folder> [--size 1G] [--seconds 5] --json`. `start` has `mode` `speed`. One temporary file,
`disky-speed.tmp`, is written in the folder and always removed at the end (also after Stop or an error). A
leftover from a killed run is never overwritten; `disky clean` lists and removes it.

| Event | Fields | Meaning |
|---|---|---|
| `speed_phase` | `test`, `total_bytes?`, `seconds?` | One of the four measurements begins |
| `speed_sample` | `test`, `offset_bytes`, `mib_per_s` | One 32 MiB slice of a sequential measurement, for the graph |
| `speed_result` | `test`, `mib_per_s`, `iops?`, `direct?`, `read_from_drive?` | The result of one measurement |
| `done` | `results` (the same four results by name), `stopped` | Last line |

| `test` | What is measured |
|---|---|
| `seq_write` | Writing the file front to back; each slice is flushed to the drive before its time is taken |
| `seq_read` | Reading it back front to back after the kernel was told to forget it. `read_from_drive` false = part came from memory, so the number is not the drive's |
| `rand_read` | 4 KiB reads at random places for `seconds`. `direct` true = past the kernel's cache |
| `rand_write` | 4 KiB writes at random places for `seconds`, each forced to the drive. `direct` as above |

Free space is checked before the file is made and again before every slice; on a system drive the same reserve
as for the fill test is kept.

## Fragmentation scan and defragmenting (added 2026-11-26, still contract version 1)

`disky fragscan <exFAT partition or image> --json`: read-only. `frag_summary` (`cluster_bytes`, `clusters`,
`used_clusters?`, `files`, `folders`, `fragmented_files`, `pieces`, `file_bytes`, `fragmented_bytes`, `worst` =
the most fragmented files), then `frag_blocks` (`first`, `count`, `state`: `free`, `whole`, `fragmented`,
`other`) covering every cluster in order, then `done`. Needs the right to read the partition.

`disky defrag <file> --json`: one file, with the system's own tool (btrfs, ext4, XFS only). `defrag_result`
(`path`, `fs`, `tool`, `exit`, `before`, `after` = pieces, `changed_content`), then `done`. The content is
hashed before and after; a change is an `error` with code `content_changed`. exFAT, FAT and NTFS are refused:
no trustworthy defragmenter exists for them on Linux (exfatprogs disabled its own after reports of data loss).

## TRIM (added 2026-11-26, still contract version 1)

`disky trim <folder> --json`: `trim_info` (`mount`, `device`, `supported`, `discard_max_bytes`,
`discard_granularity`, `rotational?`), then either an `error` (`not_supported`: the drive or its USB case does not
accept TRIM; `needs_admin`; `os_error`: fstrim failed) with nothing run, or `trim_result` (`mount`,
`trimmed_bytes?`, `message` = fstrim's own words) and `done`. Runs `fstrim -v` on the folder's mount point;
fstrim never touches files.
