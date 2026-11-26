# SPDX-License-Identifier: GPL-3.0-or-later
"""Test data in f3's file format, made and checked with numpy.

The format and the checking rules come from f3 (Fight Flash Fraud) by Michel
Machado, GPL-3, https://github.com/AltraMayor/f3 (src/libutils.c), which follows
H2testw by Harald Boegeholz. This file is new code; no f3 code is copied.

A sector is 512 bytes = 64 little-endian 64-bit numbers. Number 0 is the
sector's own offset in the test data. Each next number is
previous * 4294967311 + 17 (64-bit wrap-around), starting from the offset.
"""
import numpy as np

SECTOR = 512
WORDS = SECTOR // 8
_MASK = (1 << 64) - 1
_A = 4294967311
_C = 17

GOOD, CHANGED, BAD, OVERWRITTEN = 0, 1, 2, 3
STATE_NAMES = ("good", "changed", "bad", "overwritten")
# f3 10.0 calls a sector "changed" or "overwritten" when at most 7 bits differ.
BIT_TOLERANCE = 7


def _tables():
    # Number k of a sector equals seed * a^k + c_k, so a whole block is one
    # table multiply instead of a loop over every number.
    ak = np.empty(WORDS, np.uint64)
    ck = np.empty(WORDS, np.uint64)
    a, c = 1, 0
    for k in range(WORDS):
        ak[k], ck[k] = a, c
        a = (a * _A) & _MASK
        c = (c * _A + _C) & _MASK
    return ak, ck


_AK, _CK = _tables()


def _chain(seeds):
    out = seeds[:, None] * _AK[None, :] + _CK[None, :]
    out[:, 0] = seeds
    return out


def _offsets(offset, sectors):
    return np.arange(sectors, dtype=np.uint64) * np.uint64(SECTOR) + np.uint64(offset)


def make_view(offset, nbytes):
    """Return nbytes of test data for this offset as a byte view (no extra copy)."""
    if offset % SECTOR or nbytes % SECTOR:
        raise ValueError("offset and size must be multiples of 512")
    data = _chain(_offsets(offset, nbytes // SECTOR)).astype("<u8", copy=False)
    return memoryview(data).cast("B")


def make(offset, nbytes):
    """Return nbytes of test data that belong at this offset."""
    return make_view(offset, nbytes).tobytes()


def classify(buf, offset):
    """Return one state per whole sector in buf (GOOD, CHANGED, BAD, OVERWRITTEN)."""
    sectors = len(buf) // SECTOR
    got = np.frombuffer(buf, "<u8", count=sectors * WORDS).reshape(sectors, WORDS)
    want_off = _offsets(offset, sectors)
    states = np.zeros(sectors, np.uint8)
    rows = np.nonzero((got != _chain(want_off)).any(axis=1))[0]
    if rows.size == 0:
        return states
    part = got[rows]
    found = part[:, 0].astype(np.uint64)
    # f3 rebuilds the chain from the offset it FOUND, so a sector that holds
    # valid data for another place is recognised as "overwritten".
    bits = np.bitwise_count(part[:, 1:] ^ _chain(found)[:, 1:]).sum(axis=1)
    same_place = found == want_off[rows]
    close = bits <= BIT_TOLERANCE
    states[rows] = np.where(
        same_place,
        np.where(bits == 0, GOOD, np.where(close, CHANGED, BAD)),
        np.where(close, OVERWRITTEN, BAD),
    )
    return states
