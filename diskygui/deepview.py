# SPDX-License-Identifier: GPL-3.0-or-later
"""The deep-scan window: the Identity card, the history cards and "Save report". Words come from deepcard."""
import os

from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import deepcard


def _card(title):
    box = QFrame()
    box.setObjectName("verdict")
    col = QVBoxLayout(box)
    head = QLabel(title)
    head.setObjectName("verdict_title")
    col.addWidget(head)
    return box, col


class DeepView(QDialog):
    def __init__(self, name, data, device="drive", parent=None):
        super().__init__(parent)
        self.name, self.data, self.device = name, data, device
        self.setWindowTitle(f"Deep scan: {name}")
        self.resize(640, 720)
        self.ask_path = self._ask_path                  # replaced in tests
        self.saved = None
        inner = QWidget()
        col = QVBoxLayout(inner)
        box, card = _card("Identity")
        self.grid = QGridLayout()
        self.grid.setColumnStretch(1, 1)
        card.addLayout(self.grid)
        self.show_serial = QCheckBox("Show serial number")
        self.show_serial.setToolTip("The serial number identifies this one drive. It stays hidden in the "
                                    "report too, unless this is ticked")
        self.show_serial.setVisible("serial_number" in data)
        self.show_serial.toggled.connect(self.fill_identity)
        card.addWidget(self.show_serial)
        col.addWidget(box)
        self.cards = []
        for title, lines, meaning in deepcard.history(data):
            box, card = _card(title)
            body = QLabel("\n".join(lines))
            body.setWordWrap(True)
            what = QLabel(meaning)
            what.setObjectName("status")
            what.setWordWrap(True)
            card.addWidget(body)
            card.addWidget(what)
            col.addWidget(box)
            self.cards.append((title, body, what))
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self.save = QPushButton("Save report...")
        self.save.clicked.connect(self.save_report)
        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.save)
        bottom.addWidget(close)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)
        outer.addLayout(bottom)
        self.fill_identity()

    def identity(self):
        return deepcard.identity(self.data, self.show_serial.isChecked())

    def fill_identity(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for i, (k, v) in enumerate(self.identity()):
            value = QLabel(v)
            value.setWordWrap(True)
            self.grid.addWidget(QLabel(k), i, 0)
            self.grid.addWidget(value, i, 1)

    def _ask_path(self, suggested):
        path, _ = QFileDialog.getSaveFileName(self, "Save the deep-scan report", suggested, "Text (*.txt)")
        return path

    def save_report(self):
        path = self.ask_path(os.path.join(os.path.expanduser("~"), f"disky-deep-scan-{self.device}.txt"))
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(deepcard.report(self.name, self.data, self.show_serial.isChecked()))
        except OSError as e:
            self.status.setText(f"Not saved: {e.strerror}.")
            return
        self.saved = path
        self.status.setText(f"Saved to {path}" + ("" if self.show_serial.isChecked()
                                                  else " (serial number left out)."))
