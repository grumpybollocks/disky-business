# SPDX-License-Identifier: GPL-3.0-or-later
"""A pretend engine for the window tests: fake_engine.py <scenario> <mode> <folder> --json [...]"""
import json
import os
import signal
import sys
import time

MIB = 1 << 20
N = 200  # blocks


def say(**ev):
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def summary(verdict, bad=0, pct=100.0):
    say(event="summary", verdict=verdict, bytes_read=N * MIB,
        sectors={"good": N * 2048 - bad, "changed": 0, "bad": bad, "overwritten": 0}, missing_bytes=0,
        first_bad_offset=57 * MIB if bad else None, read_from_drive_pct=pct, read_from_drive=pct >= 99)


def speed(scenario):
    say(event="start", contract=1, version="fake", mode="speed", block_bytes=MIB, sector_bytes=512)
    if scenario == "refuse":
        say(event="error", code="existing_files", message="A speed-test file from an earlier run is still there.")
        return 3
    total, results = 128 * MIB, {}
    say(event="speed_phase", test="seq_write", total_bytes=total, seconds=None)
    if scenario == "slow":
        stop = []
        signal.signal(signal.SIGINT, lambda *_: stop.append(1))
        while not stop:
            say(event="speed_sample", test="seq_write", offset_bytes=0, mib_per_s=40.0)
            time.sleep(0.02)
        say(event="done", results={}, stopped=True)
        return 130
    for test, rates in (("seq_write", (40.0, 50.0, 45.0, 20.0)), ("seq_read", (100.0, 110.0, 105.0, 95.0))):
        if test == "seq_read":
            say(event="speed_phase", test=test, total_bytes=total, seconds=None)
        for i, r in enumerate(rates):
            say(event="speed_sample", test=test, offset_bytes=i * 32 * MIB, mib_per_s=r)
        results[test] = dict(mib_per_s=sum(rates) / 4, iops=None, direct=None,
                             read_from_drive=False if scenario == "memory" and test == "seq_read" else None)
        say(event="speed_result", test=test, **results[test])
        if scenario == "crash":
            os.kill(os.getpid(), signal.SIGKILL)
    for test, iops in (("rand_read", 1150), ("rand_write", 106)):
        say(event="speed_phase", test=test, total_bytes=None, seconds=5.0)
        results[test] = dict(mib_per_s=round(iops * 4096 / MIB, 2), iops=iops, direct=test == "rand_read",
                             read_from_drive=None)
        say(event="speed_result", test=test, **results[test])
    say(event="done", results=results, stopped=False)
    return 0


def frag(scenario, mode, target):
    say(event="start", contract=1, version="fake", mode=mode, block_bytes=MIB, sector_bytes=512)
    if scenario == "refuse":
        say(event="error", code="not_supported", message="This is not an exFAT volume.")
        return 3
    if scenario == "nopassword":
        return 126
    worst = [{"pieces": 64, "bytes": 8 * MIB, "path": "/drive/big.bin"}, {"pieces": 3, "bytes": 4096, "path": "/drive/small.txt"}]
    if mode == "defrag" and scenario == "toolfail":
        say(event="defrag_result", path=target, fs="ext4", tool="e4defrag", exit=1, before=64, after=64,
            changed_content=False)
        say(event="done")
        return 3
    if mode == "defrag":
        say(event="defrag_result", path=target, fs="btrfs", tool="btrfs", exit=0, before=64, after=1, changed_content=False)
    elif mode == "fragfiles":
        say(event="frag_files", files=10, fragmented_files=2, pieces=75, file_bytes=20 * MIB, fragmented_bytes=8 * MIB + 4096,
            skipped=1, worst=worst)
    else:
        say(event="frag_summary", cluster_bytes=131072, clusters=1000, used_clusters=400, files=10, folders=2,
            fragmented_files=2, pieces=75, file_bytes=20 * MIB, fragmented_bytes=8 * MIB + 4096, worst=worst)
        for first, count, state in ((0, 10, "other"), (10, 300, "whole"), (310, 1, "fragmented"), (311, 89, "whole"), (400, 600, "free")):
            say(event="frag_blocks", first=first, count=count, state=state)
    say(event="done")
    return 0


def main():
    scenario, mode = sys.argv[1], sys.argv[2]
    if scenario == "waitstop":
        # Behaves like an engine running with admin rights: our signals cannot reach it, only its input can.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        say(event="start", contract=1, version="fake", mode=mode, block_bytes=MIB, sector_bytes=512)
        for line in sys.stdin:
            if line.strip() == "stop":
                break
        say(event="done")
        return 130
    if scenario == "badsummary":
        say(event="start", contract=1, version="fake", mode=mode, block_bytes=MIB, sector_bytes=512)
        say(event="summary", verdict="good", bytes_read=None, sectors=None, missing_bytes=0, first_bad_offset=None,
            read_from_drive_pct=None, read_from_drive=True)
        return 0
    if mode == "clean":
        say(event="start", contract=1, version="fake", mode="clean", block_bytes=MIB, sector_bytes=512)
        say(event="done", removed_files=1, listed_files=1, kept_files=1 if scenario == "kept" else 0)
        return 0
    if mode == "selftest":
        say(event="start", contract=1, version="fake", mode=mode, block_bytes=MIB, sector_bytes=512)
        if scenario == "refuse":
            say(event="error", code="refused", message="The password was not given, so no self-test was started.")
            return 3
        say(event="selftest_progress", status="inprogress", percent=0)
        if scenario == "slow":
            stop = []
            signal.signal(signal.SIGINT, lambda *_: stop.append(1))
            while not stop:
                say(event="selftest_progress", status="inprogress", percent=40)
                time.sleep(0.02)
            say(event="selftest_result", kind="short", status="aborted", ok=None,
                message="The self-test was stopped before it finished.")
            say(event="done")
            return 130
        ok = scenario != "bad"
        say(event="selftest_result", kind=sys.argv[sys.argv.index("--kind") + 1], status="success" if ok else "error_read",
            ok=ok, message="Passed: the drive found no problem." if ok else "FAILED (reading). Back up this drive now.")
        say(event="done")
        return 0 if ok else 1
    if mode == "smart":
        say(event="start", contract=1, version="fake", mode=mode, block_bytes=MIB, sector_bytes=512)
        if scenario == "nopassword":
            return 126
        if scenario == "refuse":
            say(event="error", code="no_smart", message="This drive gives no SMART data through its USB case.")
            return 3
        say(event="smart_data", device=sys.argv[3], data={"smart_status": {"passed": True}, "ata_smart_attributes": {
            "table": [{"id": 9, "name": "Power_On_Hours", "value": 99, "worst": 99, "thresh": 0,
                       "flags": {"prefailure": False}, "raw": {"value": 1234, "string": "1234"}}]}})
        say(event="done")
        return 0
    if mode in ("fscheck", "fsrepair"):
        action = "check" if mode == "fscheck" else "repair"
        say(event="start", contract=1, version="fake", mode=mode, block_bytes=MIB, sector_bytes=512)
        if scenario == "refuse":
            say(event="error", code="refused", message="It is mounted. Unmount it first.")
            return 3
        ok = scenario != "bad" or action == "repair"            # "bad": the check finds problems, the repair works
        say(event="fs_result", action=action, device=sys.argv[3], fs="vfat", ok=ok,
            message={("check", True): "No problems found.", ("check", False): "Problems found.",
                     ("repair", True): "Repaired."}[(action, ok)])
        say(event="done")
        return 0 if ok else 1
    if mode == "speed":
        return speed(scenario)
    if mode == "trim":
        say(event="start", contract=1, version="fake", mode="trim", block_bytes=MIB, sector_bytes=512)
        if scenario == "nopassword":
            return 126
        say(event="trim_info", mount="/drive", device="/dev/fake1", supported=True, discard_max_bytes=1 << 31,
            discard_granularity=4096, rotational=False)
        if scenario == "refuse":
            say(event="error", code="not_supported", message="This drive does not accept TRIM.")
            return 3
        say(event="trim_result", mount="/drive", trimmed_bytes=5 * (1 << 30), message="/drive: 5 GiB (5368709120 bytes) trimmed")
        say(event="done")
        return 0
    if mode in ("fragfiles", "fragscan", "defrag"):
        return frag(scenario, mode, sys.argv[3])
    say(event="start", contract=1, version="fake", mode="test", block_bytes=MIB, sector_bytes=512)
    if scenario == "warn":
        say(event="warning", code="system_drive", message="Tip: this drive runs your system. Leaving 2.0 GiB free.")
    if scenario == "refuse":
        say(event="error", code="existing_files", message="Test files are there. Check them with: disky verify <folder>")
        return 3
    say(event="phase", phase="write", total_bytes=N * MIB, free_bytes=N * MIB)
    if scenario == "slow":
        stop = []
        signal.signal(signal.SIGINT, lambda *_: stop.append(1))
        done = 0
        while not stop:
            done = min(N, done + 1)
            say(event="blocks", phase="write", first=done - 1, count=1, state="written")
            say(event="progress", phase="write", done_bytes=done * MIB, total_bytes=N * MIB, mib_per_s=50.0,
                avg_mib_per_s=50.0, eta_s=600)
            time.sleep(0.02)
        say(event="warning", code="stopped", message="Stopped. The test files are left in place.")
        say(event="done", written_bytes=done * MIB, stopped=True)
        return 130
    say(event="blocks", phase="write", first=0, count=N, state="written")
    say(event="progress", phase="write", done_bytes=N * MIB, total_bytes=N * MIB, mib_per_s=123.4,
        avg_mib_per_s=120.0, eta_s=0)
    if scenario == "crash":
        os.kill(os.getpid(), signal.SIGKILL)
    if scenario == "junk":
        sys.stdout.write("this is not JSON\n")
        say(event="from_the_future", anything=[1, 2, 3])
    say(event="phase", phase="verify", total_bytes=N * MIB, free_bytes=None)
    if scenario == "bad":
        say(event="blocks", phase="verify", first=0, count=57, state="good")
        say(event="blocks", phase="verify", first=57, count=1, state="bad",
            sectors={"good": 2040, "changed": 0, "bad": 8, "overwritten": 0}, first_bad_offset=57 * MIB)
        say(event="blocks", phase="verify", first=58, count=N - 58, state="good")
        summary("bad", bad=8)
        return 1
    say(event="blocks", phase="verify", first=0, count=N, state="good")
    if scenario == "memory":
        summary("not_verified", pct=3.0)
        return 2
    summary("good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
