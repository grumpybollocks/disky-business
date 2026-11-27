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
    flat: bool = False                           # True: a modern look (rounded, no bevels, icons on buttons)
    border: str = ""                             # flat looks: the thin line around panels and buttons
    button: str = ""                             # flat looks: a button's fill
    hover: str = ""                              # flat looks: a button's fill under the mouse
    muted: str = ""                              # flat looks: quieter text (other tabs, headings, the tip line)
    faint: str = ""                              # flat looks: text of what cannot be used now
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

_MODERN_FONTS = ("Inter", "Noto Sans", "Cantarell", "Segoe UI", "Roboto", "DejaVu Sans")

# Modern, light. Own colours: a calm grey-white with one blue for what matters.
MODERN = Theme(
    name="modern", label="Modern light",
    face="#f4f5f7", light="#ffffff", shadow="#c9ced6", dark="#9aa3af",
    title="#2563eb", title_text="#ffffff", text="#1f2937",
    field="#ffffff", field_text="#1f2937", map_bg="#ffffff",
    states={
        "untouched": "#e5e7eb", "written": "#3b82f6", "working": "#111827", "good": "#10b981",
        "changed": "#f59e0b", "overwritten": "#a855f7", "bad": "#ef4444",
    },
    frag={"free": "#e5e7eb", "other": "#9ca3af", "whole": "#0f766e", "fragmented": "#ea580c"},
    light2="#ffffff", block_style="soft", frag_style="fine",
    fonts=_MODERN_FONTS, font_px=14, smooth=True, bold_ui=False,
    flat=True, border="#d5d9e0", button="#ffffff", hover="#e8eefc", muted="#5b6472", faint="#a9b0bb",
    tab_on="#2563eb", tab_on_text="#2563eb",
)

# Modern, dark. Own colours.
MODERN_DARK = Theme(
    name="modern-dark", label="Modern dark",
    face="#15171c", light="#2a2e37", shadow="#0c0d10", dark="#08090b",
    title="#5b9cff", title_text="#0b1220", text="#e6e8ec",
    field="#1c1f26", field_text="#e6e8ec", map_bg="#1c1f26",
    states={
        "untouched": "#3d4452", "written": "#5b9cff", "working": "#ffffff", "good": "#34d399",
        "changed": "#fbbf24", "overwritten": "#c084fc", "bad": "#f87171",
    },
    frag={"free": "#2a2e37", "other": "#7b8494", "whole": "#2dd4bf", "fragmented": "#fb923c"},
    light2="#2a2e37", block_style="soft", frag_style="fine",
    fonts=_MODERN_FONTS, font_px=14, smooth=True, bold_ui=False,
    flat=True, border="#30343d", button="#232730", hover="#2c3340", muted="#a3abb8", faint="#5a6170",
    tab_on="#5b9cff", tab_on_text="#5b9cff",
)

THEMES = {t.name: t for t in (MODERN, MODERN_DARK, W98, DARK, VIOLET)}
DEFAULT_THEME = "modern"


def default_theme(dark):
    """The look a first start gets: the modern one, light or dark as the system is."""
    return "modern-dark" if dark else "modern"
