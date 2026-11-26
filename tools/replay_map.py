#!/usr/bin/env python3
"""Replay a recorded engine run into the block map and save a picture. No screen needed.

    python3 -m disky test <folder> --json > run.jsonl
    python3 tools/replay_map.py run.jsonl map.png [--size 1280x600] [--upto 0.5]
"""
import argparse
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

from diskygui.mapmodel import MapModel  # noqa: E402
from diskygui.mapwidget import BlockMap  # noqa: E402


def main():
    a = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    a.add_argument("events")
    a.add_argument("picture")
    a.add_argument("--size", default="1280x600")
    a.add_argument("--upto", type=float, default=1.0, help="stop after this share of the lines (0 to 1)")
    args = a.parse_args()
    w, h = (int(v) for v in args.size.lower().split("x"))
    with open(args.events) as f:
        lines = [ln for ln in f if ln.strip()]
    lines = lines[:max(1, int(len(lines) * args.upto))]

    app = QApplication.instance() or QApplication([])
    model = MapModel()
    view = BlockMap(model)
    view.resize(w, h)
    t0 = time.perf_counter()
    for ln in lines:
        model.apply(json.loads(ln))
    t1 = time.perf_counter()
    view.refresh()
    t2 = time.perf_counter()
    view.grab().save(args.picture)
    print(f"{len(lines)} events in {1000 * (t1 - t0):.1f} ms, drawn in {1000 * (t2 - t1):.1f} ms, "
          f"{len(model.states)} blocks in {view.cols}x{view.rows} squares: {model.counts()}")
    del view, app


if __name__ == "__main__":
    main()
