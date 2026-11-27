# SPDX-License-Identifier: GPL-3.0-or-later
"""Turns a Theme into the look of buttons, tabs, lists, boxes and scroll bars. Every theme plugs in here.

Edges are drawn the classic way, two lines deep (outer and inner, lit from the top left), as small pictures
that the style sheet stretches around each widget. The pictures are made at start-up from the theme's colours.
"""
import atexit
import os
import shutil
import tempfile

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPolygon

_dir = None


def _folder():
    global _dir
    if _dir is None:
        _dir = tempfile.mkdtemp(prefix="disky-business-look-")
        atexit.register(shutil.rmtree, _dir, True)
    return _dir


def _edge(name, t, outer_tl, inner_tl, inner_br, outer_br, fill, open_bottom=False):
    """A 9 x 9 picture: fill in the middle, a two-line edge around it."""
    path = os.path.join(_folder(), f"{t.name}-{name}.png")
    if os.path.exists(path):
        return path
    n = 9
    img = QImage(n, n, QImage.Format_ARGB32)
    img.fill(QColor(fill))
    p = QPainter(img)
    for inset, tl, br in ((0, outer_tl, outer_br), (1, inner_tl, inner_br)):
        a, b = inset, n - 1 - inset
        p.setPen(QColor(br))
        p.drawLine(b, a, b, b)
        if not open_bottom:
            p.drawLine(a, b, b, b)
        p.setPen(QColor(tl))
        p.drawLine(a, a, b - 1, a)
        p.drawLine(a, a, a, n - 1 if open_bottom else b - 1)
    p.end()
    img.save(path)
    return path


def _arrow(name, t, points):
    path = os.path.join(_folder(), f"{t.name}-{name}.png")
    if not os.path.exists(path):
        img = QImage(9, 9, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setPen(QColor(t.text))
        p.setBrush(QColor(t.text))
        p.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
        p.end()
        img.save(path)
    return path


def _checker(t):
    path = os.path.join(_folder(), f"{t.name}-track.png")
    if not os.path.exists(path):
        img = QImage(2, 2, QImage.Format_ARGB32)
        img.fill(QColor(t.face))
        img.setPixelColor(0, 0, QColor(t.light))
        img.setPixelColor(1, 1, QColor(t.light))
        img.save(path)
    return path


def _installed(names):
    have = {name.lower() for name in QFontDatabase.families()}
    return any(name.lower() in have for name in names)


def text_look(t, installed=_installed):
    """(pixel size, smooth) for a theme's letters. The crisp look only when a font made for it is installed."""
    if t.smooth or not t.crisp_fonts or installed(t.crisp_fonts):
        return t.font_px, t.smooth
    return t.fallback_px or t.font_px + 1, True


def font(t):
    """The interface font of a theme. Without smoothing where the theme asks for the crisp old look."""
    px, smooth = text_look(t)
    f = QFont()
    f.setFamilies(list(t.fonts))
    f.setPixelSize(px)
    if not smooth:
        f.setStyleStrategy(QFont.NoAntialias)
        f.setHintingPreference(QFont.PreferFullHinting)
    return f


def flat_stylesheet(t):
    """The modern looks: thin lines, rounded corners, room to breathe. No pictures needed."""
    px = text_look(t)[0]
    families = ", ".join(f'"{name}"' for name in t.fonts)
    down = _arrow("down", t, [(1, 3), (4, 6), (7, 3)])
    up = _arrow("up", t, [(1, 5), (4, 2), (7, 5)])
    return f"""
QWidget {{ background: {t.face}; color: {t.text}; font-family: {families}; font-size: {px}px; }}
QLabel {{ background: transparent; }}
QPushButton {{ background: {t.button}; border: 1px solid {t.border}; border-radius: 7px; padding: 7px 14px;
              min-width: 70px; }}
QPushButton:hover {{ background: {t.hover}; border-color: {t.title}; }}
QPushButton:pressed {{ background: {t.title}; color: {t.title_text}; }}
QPushButton#primary {{ background: {t.title}; color: {t.title_text}; border-color: {t.title}; font-weight: bold; }}
QPushButton#primary:hover {{ background: {t.title}; border-color: {t.text}; }}
QPushButton:disabled, QPushButton#primary:disabled {{ color: {t.faint}; background: {t.face};
                                                     border-color: {t.border}; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {t.border}; top: -1px; }}
QTabBar::tab {{ background: transparent; border: none; border-bottom: 3px solid transparent;
               padding: 9px 18px; color: {t.muted}; }}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabBar::tab:selected {{ color: {t.tab_on_text or t.title}; border-bottom: 3px solid {t.tab_on or t.title};
                        font-weight: bold; }}
QTabBar::tab:disabled {{ color: {t.faint}; }}
QLineEdit, QListWidget, QTableWidget, #verdict {{ color: {t.field_text}; background: {t.field};
                                                 border: 1px solid {t.border}; border-radius: 9px; padding: 5px; }}
#verdict QLabel {{ color: {t.field_text}; }}
QListWidget::item {{ padding: 5px 6px; border-radius: 6px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {t.title}; color: {t.title_text}; }}
QTableWidget {{ gridline-color: {t.border}; }}
QHeaderView::section {{ background: {t.face}; color: {t.muted}; border: none; border-bottom: 1px solid {t.border};
                       padding: 6px 8px; font-weight: bold; }}
#mapframe {{ background: {t.map_bg}; border: 1px solid {t.border}; border-radius: 10px; }}
#tipline {{ color: {t.muted}; padding: 6px 4px; }}
#health {{ background: {t.field}; border: 1px solid {t.border}; border-radius: 9px; padding: 8px 10px; }}
#themebutton {{ margin: 4px 6px; padding: 5px 12px; }}
#heading {{ font-weight: bold; color: {t.muted}; }}
#status {{ font-size: {px + 1}px; font-weight: bold; }}
#verdict_title {{ font-size: {px + 6}px; font-weight: bold; }}
QProgressBar {{ background: {t.border}; border: none; border-radius: 6px; }}
QProgressBar::chunk {{ background: {t.title}; border-radius: 6px; }}
#progressnote {{ color: {t.muted}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {t.muted}; border-radius: 4px;
                       background: {t.field}; }}
QCheckBox::indicator:checked {{ background: {t.title}; border-color: {t.title}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; border: none; }}
QScrollBar::handle:vertical {{ background: {t.faint}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar::up-arrow:vertical {{ image: url({up}); }}
QScrollBar::down-arrow:vertical {{ image: url({down}); }}
QToolTip {{ background: {t.field}; color: {t.field_text}; border: 1px solid {t.border}; padding: 4px; }}
"""


def stylesheet(t):
    if t.flat:
        return flat_stylesheet(t)
    raised = _edge("raised", t, t.light, t.light2, t.shadow, t.dark, t.face)
    pressed = _edge("pressed", t, t.dark, t.shadow, t.light2, t.light, t.face)
    field = _edge("field", t, t.shadow, t.dark, t.light2, t.light, t.field)
    well = _edge("well", t, t.shadow, t.dark, t.light2, t.light, t.map_bg)
    sunk = _edge("sunk", t, t.shadow, t.face, t.face, t.light, t.face)
    tab = _edge("tab", t, t.light, t.light2, t.shadow, t.dark, t.tab_off or t.face, open_bottom=True)
    tab_on = _edge("tabon", t, t.light, t.light2, t.shadow, t.dark, t.tab_on or t.face, open_bottom=True)
    up = _arrow("up", t, [(1, 5), (4, 2), (7, 5)])
    down = _arrow("down", t, [(1, 3), (4, 6), (7, 3)])
    px, bold = text_look(t)[0], ("bold" if t.bold_ui else "normal")
    families = ", ".join(f'"{name}"' for name in t.fonts)
    return f"""
QWidget {{ background: {t.face}; color: {t.text}; font-family: {families}; font-size: {px}px; }}
QLabel {{ background: transparent; }}
QPushButton {{ border: 2px solid transparent; border-image: url({raised}) 2 2 2 2 stretch; padding: 3px 12px;
              min-width: 75px; min-height: {px + 6}px; font-weight: {bold}; }}
QPushButton:pressed {{ border-image: url({pressed}) 2 2 2 2 stretch; padding: 4px 11px 2px 13px; }}
QPushButton:disabled {{ color: {t.shadow}; }}
QTabWidget::pane {{ border: 2px solid transparent; border-image: url({raised}) 2 2 2 2 stretch; top: -2px; }}
QTabBar::tab {{ border: 2px solid transparent; border-bottom: 0px; border-image: url({tab}) 2 2 0 2 stretch;
               padding: 3px 12px 3px 12px; margin-top: 2px; font-weight: {bold}; }}
QTabBar::tab:selected {{ margin-top: 0px; padding-bottom: 5px; color: {t.tab_on_text or t.text};
                        border-image: url({tab_on}) 2 2 0 2 stretch; }}
QTabBar::tab:disabled {{ color: {t.shadow}; }}
QLineEdit, QListWidget, #verdict {{ color: {t.field_text}; background: {t.field}; border: 2px solid transparent;
                                   border-image: url({field}) 2 2 2 2 stretch; padding: 1px; }}
#verdict QLabel {{ color: {t.field_text}; }}
QTableWidget {{ color: {t.field_text}; background: {t.field}; border: 2px solid transparent;
                border-image: url({field}) 2 2 2 2 stretch; gridline-color: {t.face}; }}
QTableWidget::item:selected {{ background: {t.title}; color: {t.title_text}; }}
QHeaderView::section {{ border: 2px solid transparent; border-image: url({raised}) 2 2 2 2 stretch; padding: 1px 4px; }}
QListWidget::item {{ padding: 2px; }}
QListWidget::item:selected {{ background: {t.title}; color: {t.title_text}; }}
#mapframe {{ background: {t.map_bg}; border: 2px solid transparent; border-image: url({well}) 2 2 2 2 stretch; }}
#tipline, #health {{ border: 1px solid transparent; border-image: url({sunk}) 1 1 1 1 stretch; padding: 3px 6px; }}
QProgressBar {{ background: {t.field}; border: 2px solid transparent; border-image: url({field}) 2 2 2 2 stretch; }}
QProgressBar::chunk {{ background: {t.title}; width: 9px; margin: 1px; }}
#themebutton {{ margin-bottom: 3px; }}
#heading {{ font-weight: {bold}; }}
#status {{ font-size: {px + 2}px; font-weight: bold; }}
#verdict_title {{ font-size: {px + 5}px; font-weight: bold; }}
#speedvalue {{ font-size: {px + 1}px; font-weight: bold; }}
QScrollBar:vertical {{ background-image: url({_checker(t)}); width: 16px; margin: 16px 0px 16px 0px; border: none; }}
QScrollBar::handle:vertical {{ border: 2px solid transparent; border-image: url({raised}) 2 2 2 2 stretch;
                              min-height: 16px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    border: 2px solid transparent; border-image: url({raised}) 2 2 2 2 stretch; height: 12px;
    subcontrol-origin: margin; }}
QScrollBar::add-line:vertical {{ subcontrol-position: bottom; }}
QScrollBar::sub-line:vertical {{ subcontrol-position: top; }}
QScrollBar::up-arrow:vertical {{ image: url({up}); width: 9px; height: 9px; }}
QScrollBar::down-arrow:vertical {{ image: url({down}); width: 9px; height: 9px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""
