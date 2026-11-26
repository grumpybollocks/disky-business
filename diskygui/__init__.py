# SPDX-License-Identifier: GPL-3.0-or-later
"""The Disky Business window. Reads the engine's JSON lines (docs/CONTRACT.md); shares only the contract table and the tip texts with it.

Pieces, each plugged in through a small table so more can be added without touching the others:
  theme.py        colour schemes (THEMES)
  blockstyles.py  how one square is drawn (STYLES)
  mapmodel.py     block states and their grouping into squares (no Qt)
  mapwidget.py    the block map on screen
"""
