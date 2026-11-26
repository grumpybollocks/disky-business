# SPDX-License-Identifier: GPL-3.0-or-later
"""Start the window: python3 -m diskygui [folder]"""
import sys

from PySide6.QtWidgets import QApplication

from .window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    if len(sys.argv) > 1:
        win.pages[0].set_folder(sys.argv[1])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
