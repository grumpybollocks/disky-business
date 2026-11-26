# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour schemes as data. To add one: make a Theme and put it in THEMES. Nothing else changes."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    face: str          # window and button surface
    light: str         # raised edge, lit side
    shadow: str        # raised edge, shaded side
    dark: str          # outer dark edge
    title: str         # title bar and selected row
    title_text: str
    text: str
    field: str         # inside of lists and text boxes
    field_text: str
    map_bg: str        # behind the squares
    states: dict = field(default_factory=dict)   # Test map: square state name -> colour
    frag: dict = field(default_factory=dict)     # Defrag map: free, other, whole, fragmented -> colour
    block_style: str = "bevel"                   # Test map: entry of blockstyles.STYLES
    frag_style: str = "fine"                     # Defrag map: a different shape, so the two are not mixed up
    light2: str = ""                             # inner lit edge (between light and face)
    fonts: tuple = ("Noto Sans", "DejaVu Sans")  # first one installed is used
    font_px: int = 13
    smooth: bool = True                          # False = crisp letters without smoothing
    bold_ui: bool = True                         # bold buttons, tabs and headings
    tab_on: str = ""                             # fill of the selected tab ("" = the window colour)
    tab_on_text: str = ""                        # its text ("" = the normal text colour)
    tab_off: str = ""                            # fill of the other tabs ("" = the window colour)
    crisp_fonts: tuple = ()                      # fonts that read well without smoothing at font_px
    fallback_px: int = 0                         # size used, smoothed, when none of crisp_fonts is installed


# Classic grey desktop look of the late 1990s. A reconstruction of the general style only.
W98 = Theme(
    name="w98", label="Windows 98 style",
    face="#c0c0c0", light="#ffffff", shadow="#808080", dark="#000000",
    title="#000080", title_text="#ffffff", text="#000000",
    field="#ffffff", field_text="#000000", map_bg="#ffffff",
    states={
        "untouched": "#d4d4d4", "written": "#0000d0", "working": "#000000", "good": "#00a8a8",
        "changed": "#e8c800", "overwritten": "#c000c0", "bad": "#e00000",
    },
    frag={"free": "#e4e4e4", "other": "#808080", "whole": "#000080", "fragmented": "#e00000"},
    light2="#dfdfdf",
    tab_off="#a4a4a4",                           # the tabs behind are darker, so the open one stands out
    # The real font was MS Sans Serif at 11 pixels. It is used when installed; else the closest free one.
    fonts=("MS Sans Serif", "Microsoft Sans Serif", "Tahoma", "W95FA", "Liberation Sans", "Arimo", "Nimbus Sans"),
    font_px=11, smooth=False, bold_ui=False,
    # Without one of these, unsmoothed letters at 11 pixels are hard to read: smooth them at 12 instead.
    crisp_fonts=("MS Sans Serif", "Microsoft Sans Serif", "Tahoma", "W95FA"), fallback_px=12,
)

# Dark. Colours from the Catppuccin Mocha palette (MIT licence, github.com/catppuccin/catppuccin).
DARK = Theme(
    name="dark", label="Dark",
    face="#1e1e2e", light="#45475a", shadow="#11111b", dark="#11111b",
    title="#89b4fa", title_text="#11111b", text="#cdd6f4",
    field="#181825", field_text="#cdd6f4", map_bg="#181825",
    states={
        "untouched": "#313244", "written": "#89b4fa", "working": "#ffffff", "good": "#a6e3a1",
        "changed": "#f9e2af", "overwritten": "#cba6f7", "bad": "#f38ba8",
    },
    frag={"free": "#313244", "other": "#6c7086", "whole": "#f5e0dc", "fragmented": "#fab387"},
    light2="#313244",
    tab_on="#89b4fa", tab_on_text="#11111b",
)

# Deep violet. Own colours.
VIOLET = Theme(
    name="violet", label="Deep violet",
    face="#2a1f4d", light="#4a3a80", shadow="#150d2e", dark="#0e0820",
    title="#a06bff", title_text="#ffffff", text="#e8e0ff",
    field="#1c1338", field_text="#e8e0ff", map_bg="#1c1338",
    states={
        "untouched": "#3a2d66", "written": "#5f8dff", "working": "#ffffff", "good": "#4fe0a0",
        "changed": "#ffd24a", "overwritten": "#ff7ad9", "bad": "#ff4d6d",
    },
    frag={"free": "#3a2d66", "other": "#6a5aa0", "whole": "#c9b6ff", "fragmented": "#ffb347"},
    light2="#3a2d66",
    tab_on="#a06bff", tab_on_text="#ffffff",
)

THEMES = {t.name: t for t in (W98, DARK, VIOLET)}
DEFAULT_THEME = "w98"
