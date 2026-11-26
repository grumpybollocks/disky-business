#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check a file of engine output against the contract.

Usage: disky test <folder> --json | python3 tools/check_events.py -
Exit 0 = every line fits the contract, 1 = at least one does not.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from disky.contract import problems  # noqa: E402


def main(path):
    bad = 0
    stream = sys.stdin if path == "-" else open(path)
    for number, line in enumerate(stream, 1):
        try:
            found = problems(json.loads(line))
        except json.JSONDecodeError:
            found = ["not JSON"]
        for text in found:
            bad += 1
            print(f"line {number}: {text}")
    print("contract OK" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "-"))
