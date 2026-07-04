"""
qtrm_filter.py

Row-filtering for the raw TX/RX packet viewers (rx_test_app.py,
tx_test_window.py) - lets Yuvraj narrow the 96-row grid down to just the
QTRM(s) he cares about instead of scrolling through all 96 every time.

Two mutually-exclusive filter modes, both driving the same
QtrmFilterProxyModel:
  - Manual text filter: a comma/range list like "2,5,10-15", applied live as
    it's typed (QLineEdit.textEdited, not returnPressed - so no explicit
    "Apply" step is needed).
  - Auto filter: a QCheckBox (an unambiguous on/off indicator - Yuvraj found
    a checkable QPushButton's checked-vs-unchecked state too easy to miss)
    that shows only QTRMs whose slot isn't all-zero in the *current* frame -
    recomputed on every new frame via FilterBar.refresh_auto_filter(), which
    the owning window must call after every model.replace_slots(...).
Turning one on clears/disables the other, per Yuvraj's spec - the manual
filter combo is also disabled outright (not just cleared) while auto-filter
is checked, so it can't be typed into by mistake. A "Clear Filters" button
resets both to show every row. The manual filter field is an editable
QComboBox remembering the last 10 distinct filters explicitly saved (Enter
pressed, or the "Save Filter" button) - clicking into it shows that history
as a dropdown, standard QComboBox behavior.
"""

from PySide6.QtCore import QSortFilterProxyModel
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from packet import NUM_QTRM

_HISTORY_LIMIT = 10


def parse_filter_text(text: str):
    """
    Parse a comma-separated list of QTRM indices and/or ranges (e.g.
    "2,5,10-15") into a set of ints, clamped to 0..NUM_QTRM-1. Malformed or
    out-of-range tokens are silently skipped. Returns None for blank/
    whitespace-only text, meaning "no filter - show every row".
    """
    text = text.strip()
    if not text:
        return None

    rows = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo_str, _, hi_str = token.partition("-")
            try:
                lo, hi = int(lo_str), int(hi_str)
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            for i in range(max(0, lo), min(NUM_QTRM - 1, hi) + 1):
                rows.add(i)
        else:
            try:
                i = int(token)
            except ValueError:
                continue
            if 0 <= i < NUM_QTRM:
                rows.add(i)
    return rows


class QtrmFilterProxyModel(QSortFilterProxyModel):
    """Shows every row when unfiltered; otherwise only rows in allowed_rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._allowed_rows = None  # None = no filter

    def set_allowed_rows(self, rows):
        self._allowed_rows = rows
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        return self._allowed_rows is None or source_row in self._allowed_rows


class FilterBar(QWidget):
    """Manual text filter + auto (non-zero rows) toggle + clear, wired to a QtrmFilterProxyModel."""

    def __init__(self, proxy_model: QtrmFilterProxyModel, source_model, parent=None):
        super().__init__(parent)
        self.proxy_model = proxy_model
        self.source_model = source_model
        self._history: list[str] = []

        self.filter_combo = QComboBox()
        self.filter_combo.setEditable(True)
        self.filter_combo.setInsertPolicy(QComboBox.NoInsert)  # history list is managed manually below
        self.filter_combo.lineEdit().setPlaceholderText("e.g. 2,5,10-15")
        self.filter_combo.lineEdit().textEdited.connect(self._on_filter_text_edited)
        self.filter_combo.lineEdit().returnPressed.connect(self._on_filter_committed)

        self.save_filter_btn = QPushButton("Save Filter")
        self.save_filter_btn.clicked.connect(self._on_filter_committed)

        # QCheckBox instead of a checkable QPushButton - its checked/
        # unchecked box+checkmark is unambiguous at a glance, unlike a
        # checkable button's more subtle state difference.
        self.auto_filter_checkbox = QCheckBox("Auto Filter (non-zero)")
        self.auto_filter_checkbox.toggled.connect(self._on_auto_filter_toggled)

        self.clear_btn = QPushButton("Clear Filters")
        self.clear_btn.clicked.connect(self._on_clear_clicked)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Filter QTRM:"))
        layout.addWidget(self.filter_combo, 1)
        layout.addWidget(self.save_filter_btn)
        layout.addWidget(self.auto_filter_checkbox)
        layout.addWidget(self.clear_btn)

    # -- manual text filter --------------------------------------------------

    def _on_filter_text_edited(self, text: str):
        # textEdited only fires on user keystrokes, never on our own
        # programmatic setEditText() calls below - no feedback-loop risk.
        if self.auto_filter_checkbox.isChecked():
            self.auto_filter_checkbox.blockSignals(True)
            self.auto_filter_checkbox.setChecked(False)
            self.auto_filter_checkbox.blockSignals(False)
        self.proxy_model.set_allowed_rows(parse_filter_text(text))

    def _on_filter_committed(self):
        text = self.filter_combo.currentText().strip()
        if text:
            self._remember(text)

    def _remember(self, text: str):
        if text in self._history:
            self._history.remove(text)
        self._history.insert(0, text)
        del self._history[_HISTORY_LIMIT:]

        current_text = self.filter_combo.currentText()
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItems(self._history)
        self.filter_combo.setEditText(current_text)
        self.filter_combo.blockSignals(False)

    # -- auto (non-zero rows) filter ------------------------------------------

    def _on_auto_filter_toggled(self, checked: bool):
        # Locked (disabled), not just cleared - so it can't be typed into by
        # mistake while auto-filter is driving the view.
        self.filter_combo.setEnabled(not checked)
        if checked:
            self.filter_combo.blockSignals(True)
            self.filter_combo.setEditText("")
            self.filter_combo.blockSignals(False)
            self._apply_auto_filter()
        else:
            self.proxy_model.set_allowed_rows(None)

    def _apply_auto_filter(self):
        allowed = {i for i, slot in enumerate(self.source_model.slots) if any(slot)}
        self.proxy_model.set_allowed_rows(allowed)

    def refresh_auto_filter(self):
        """Call after every source_model.replace_slots(...) - recomputes which rows are non-zero this frame."""
        if self.auto_filter_checkbox.isChecked():
            self._apply_auto_filter()

    # -- clear ----------------------------------------------------------------

    def _on_clear_clicked(self):
        self.auto_filter_checkbox.blockSignals(True)
        self.auto_filter_checkbox.setChecked(False)
        self.auto_filter_checkbox.blockSignals(False)
        self.filter_combo.setEnabled(True)
        self.filter_combo.blockSignals(True)
        self.filter_combo.setEditText("")
        self.filter_combo.blockSignals(False)
        self.proxy_model.set_allowed_rows(None)
