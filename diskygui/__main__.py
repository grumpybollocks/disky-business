# SPDX-License-Identifier: GPL-3.0-or-later
"""Start the window: python3 -m diskygui [folder]"""
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .window import MainWindow


ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Disky Business")
    app.setDesktopFileName("disky-business")       # Wayland finds the window's icon through the menu entry
    app.setWindowIcon(QIcon(ICON))                  # X11 and everything else take it from here
    win = MainWindow()
    if len(sys.argv) > 1:
        win.pages[0].set_folder(sys.argv[1])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
