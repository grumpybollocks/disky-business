# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only fragmentation scan of an exFAT volume (a partition or an image file).

Linux gives no extent map for exFAT files to a normal user, and there is no trustworthy exFAT defragmenter
(exfatprogs switched its own off after reports of data loss). So this reads the volume's own tables: boot
sector, FAT, allocation bitmap, directories. It opens the volume for reading only and never writes.

Layout facts used here are from Microsoft's published exFAT specification.
"""
import struct

import numpy as np

from .proc import Stopped

FREE, WHOLE, FRAGMENTED, OTHER = 0, 1, 2, 3          # state of one cluster on the map
STATE_NAMES = ("free", "whole", "fragmented", "other")
_END = 0xFFFFFFF7                                     # FAT values from here up end a chain
_FILE, _STREAM, _NAME, _BITMAP = 0x85, 0xC0, 0xC1, 0x81
_DIRECTORY = 0x10
MAX_DIR_BYTES = 256 << 20                             # the specification's limit for one directory




class ExfatError(Exception):
    """The volume is not exFAT or its tables do not make sense."""


class Volume:
    def __init__(self, f):
        self.f = f
        boot = self._read(0, 512)
        if len(boot) < 512 or boot[3:11] != b"EXFAT   ":
            raise ExfatError("This is not an exFAT volume.")
        fat_off, fat_len, heap_off, self.count, self.root = struct.unpack_from("<5I", boot, 80)
        sector_shift, cluster_shift = boot[108], boot[109]
        if not (9 <= sector_shift <= 12 and sector_shift + cluster_shift <= 25 and self.count > 0):
            raise ExfatError("The exFAT boot sector holds impossible sizes.")
        self.sector = 1 << sector_shift
        self.cluster = self.sector << cluster_shift
        self.heap = heap_off * self.sector
        need = (self.count + 2) * 4
        if fat_len * self.sector < need:
            raise ExfatError("The exFAT allocation table is shorter than the volume needs.")
        raw = self._read(fat_off * self.sector, need)
        if len(raw) < need:
            raise ExfatError("The exFAT allocation table could not be read in full.")
        self.fat = np.frombuffer(raw, dtype="<u4")

    def _read(self, offset, n):
        self.f.seek(offset)
        return self.f.read(n)

    def valid(self, c):
        return 2 <= c < self.count + 2

    def runs(self, first, no_fat_chain, length):
        """The pieces a file lies in: [[first cluster, count], ...]. One piece = not fragmented."""
        if not self.valid(first) or length <= 0:
            return []
        clusters = -(-length // self.cluster)
        if no_fat_chain:
            return [[first, min(clusters, self.count + 2 - first)]]
        out, c, steps = [], first, 0
        while self.valid(c) and steps < clusters:
            if out and out[-1][0] + out[-1][1] == c:
                out[-1][1] += 1
            else:
                out.append([c, 1])
            steps += 1
            if steps > self.count:
                raise ExfatError("The exFAT allocation table loops.")
            c = int(self.fat[c])
            if c >= _END:
                break
        return out

    def chain(self, first):
        """A FAT chain of unknown length (the root directory), as pieces."""
        out, c, steps = [], first, 0
        while self.valid(c):
            if out and out[-1][0] + out[-1][1] == c:
                out[-1][1] += 1
            else:
                out.append([c, 1])
            steps += 1
            if steps > self.count:
                raise ExfatError("The exFAT allocation table loops.")
            c = int(self.fat[c])
            if c >= _END:
                break
        return out

    def data(self, runs, limit):
        parts, left = [], limit
        for first, count in runs:
            if left <= 0:
                break
            n = min(count * self.cluster, left)
            parts.append(self._read(self.heap + (first - 2) * self.cluster, n))
            left -= n
        return b"".join(parts)

    def entries(self, runs, length):
        """Yield what a directory holds: ("file" | "dir" | "bitmap", name, size, first, no_fat_chain)."""
        data = self.data(runs, min(length, MAX_DIR_BYTES))
        i = 0
        while i + 32 <= len(data):
            kind = data[i]
            if kind == 0:
                break
            if kind == _BITMAP:
                first, size = struct.unpack_from("<IQ", data, i + 20)
                yield "bitmap", "", size, first, False
            elif kind == _FILE:
                secondary = data[i + 1]
                attrs = struct.unpack_from("<H", data, i + 4)[0]
                s = i + 32
                if secondary >= 1 and s + 32 <= len(data) and data[s] == _STREAM:
                    flags, name_len = data[s + 1], data[s + 3]
                    first, size = struct.unpack_from("<IQ", data, s + 20)
                    name = b"".join(data[n + 2:n + 32] for n in range(s + 32, i + 32 * (1 + secondary), 32)
                                    if n + 32 <= len(data) and data[n] == _NAME)
                    yield ("dir" if attrs & _DIRECTORY else "file",
                           name.decode("utf-16-le", "replace")[:name_len], size, first, bool(flags & 2))
                i += 32 * secondary
            i += 32


def scan(path, worst=20, progress=None, stop=None):
    """Scan the exFAT volume at `path`. Returns a dict; `state` in it holds one code per cluster."""
    with open(path, "rb", buffering=0) as f:
        vol = Volume(f)
        state = np.zeros(vol.count, np.uint8)
        files = dirs = fragmented = pieces = 0
        file_bytes = fragmented_bytes = 0
        top, bitmap = [], None
        todo, seen = [("", vol.chain(vol.root), MAX_DIR_BYTES)], {vol.root}
        while todo:
            if stop is not None and stop.is_set():
                raise Stopped("The scan was stopped.")
            where, runs, length = todo.pop()
            for kind, name, size, first, no_chain in vol.entries(runs, length):
                if kind == "bitmap":
                    if bitmap is None:
                        bitmap = vol.data(vol.runs(first, False, size) or [[first, -(-size // vol.cluster)]], size)
                    continue
                r = vol.runs(first, no_chain, size)
                full = where + "/" + name
                if kind == "dir":
                    dirs += 1
                    for a, n in r:
                        state[a - 2:a - 2 + n] = OTHER
                    if r and first not in seen:
                        seen.add(first)
                        todo.append((full, r, size))
                    continue
                files += 1
                file_bytes += size
                pieces += len(r)
                code = FRAGMENTED if len(r) > 1 else WHOLE
                for a, n in r:
                    state[a - 2:a - 2 + n] = code
                if len(r) > 1:
                    fragmented += 1
                    fragmented_bytes += size
                    top.append((len(r), size, full))
                    if len(top) > 4 * worst:
                        top = sorted(top, reverse=True)[:worst]
                if progress and files % 5000 == 0:
                    progress(files)
        used = None
        if bitmap is not None:
            bits = np.unpackbits(np.frombuffer(bitmap, np.uint8), bitorder="little")[:vol.count]
            used = int(bits.sum())
            state[:len(bits)][(bits == 1) & (state[:len(bits)] == FREE)] = OTHER   # in use, but by no file we found
    return {"cluster_bytes": vol.cluster, "clusters": vol.count, "used_clusters": used, "files": files,
            "folders": dirs, "fragmented_files": fragmented, "pieces": pieces, "file_bytes": file_bytes,
            "fragmented_bytes": fragmented_bytes, "worst": sorted(top, reverse=True)[:worst], "state": state}


def ranges(state):
    """Run-length form of the map: (first cluster index, count, state name)."""
    if len(state) == 0:
        return
    cuts = np.flatnonzero(np.diff(state)) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [len(state)]))
    for a, b in zip(starts.tolist(), ends.tolist()):
        yield a, b - a, STATE_NAMES[int(state[a])]
