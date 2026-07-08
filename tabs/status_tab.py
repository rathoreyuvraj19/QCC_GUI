"""
status_tab.py

"Status" - the remaining Status Types from Section 10 of the QTRM Message
Format IDD that don't already have their own dedicated tab: ACK, HEALTH,
TRM Err. Log, TRM Mfg. Details, and DIAGNOSTIC (which has its own further
sub-type: Detailed Health / Future Buffer / Present Buffer - ADAR Status is
not offered, not implemented on the QTRM side). LINK already has the Link
Test tab; No Status means nothing comes back, so neither is offered here.
Beam Register Address is likewise not implemented on the QTRM side, so it's
never sent (always 0) and has no UI control.

Mirrors Link Test's exact interaction pattern: a "Send All" button (with the
same optional auto-resend) queries every QTRM with whichever Status Type
(and, for ACK/DIAGNOSTIC, sub-parameters) is currently selected, and colors
a 96-cell LED matrix grey/green/red for pending/responded/no-response,
reusing link_test_tab.py's LedMatrix as-is. Clicking one LED queries just
that QTRM (mirroring Link Test's individual mode: the whole array greys out
first, then only that one LED reveals) and populates a "Details" panel
below with the actual decoded fields for that one QTRM.

On top of that, a row of mutually-exclusive checkable buttons (one per
decoded field for the current Status Type/Diagnostic Type, highlighted when
selected) lets a specific field's *value* be displayed directly on every
QTRM's cell alongside "QTRM-N" - e.g. selecting "Temperature Status" under
HEALTH shows every QTRM's temperature reading at a glance across the whole
matrix. Only one field can be shown at a time (selecting one deselects any
other); deselecting all of them reverts every cell back to its plain
"QTRM-N" label. This is separate from - and doesn't change - the Details
panel, which always shows the full decoded payload for whichever QTRM was
last individually queried. Switching to a different tab (or back to this
one) resets the whole matrix to idle, since a previous query's results
don't apply to whatever gets selected/sent next.
"""

from openpyxl import Workbook

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from core.command_style import send_button_style
from tabs.link_test_tab import LedMatrix, _IDLE_COLOR, _LINKED_COLOR, _NOT_LINKED_COLOR, _PENDING_COLOR
from core.packet import (
    STATUS_TYPE_ACK, STATUS_TYPE_HEALTH, STATUS_TYPE_ERR_LOG, STATUS_TYPE_MFG, STATUS_TYPE_DIAGNOSTIC,
    DIAGNOSTIC_TYPE_DETAILED_HEALTH, DIAGNOSTIC_TYPE_FUTURE_BUFFER, DIAGNOSTIC_TYPE_PRESENT_BUFFER,
)
from widgets.qtrm_layout import NUM_QTRM
from widgets.spin_field import DoubleSpinField, SpinField
from widgets.tx_forward_matrix import TxForwardMatrix

_DETAILS_WRAP_COLS = 5  # matches main_window.py's "Last Response" panel wrap width

_STATUS_TYPES = [
    ("ACK", STATUS_TYPE_ACK),
    ("HEALTH", STATUS_TYPE_HEALTH),
    ("TRM Err. Log", STATUS_TYPE_ERR_LOG),
    ("TRM Mfg. Details", STATUS_TYPE_MFG),
    ("DIAGNOSTIC", STATUS_TYPE_DIAGNOSTIC),
]

_DIAGNOSTIC_TYPES = [
    ("Detailed Health", DIAGNOSTIC_TYPE_DETAILED_HEALTH),
    ("Future Buffer", DIAGNOSTIC_TYPE_FUTURE_BUFFER),
    ("Present Buffer", DIAGNOSTIC_TYPE_PRESENT_BUFFER),
]

# Field label overrides (Details panel + filter checkboxes) - falls back to
# a title-cased version of the dict key if a field isn't listed here.
_FIELD_LABELS = {
    "message_id": "Message ID",
    "echoed_bytes": "Echoed Bytes (hex)",
    "dc_voltage_status": "DC Voltage Status",
    "dc_current_status": "DC Current Status",
    "temperature_status": "Temperature Status",
    "tx_forward_rf_status": "Tx Forward RF Status",
    "rx_reverse_rf_status": "Rx/Reverse RF Status",
    "trm_shutdown_flags": "TRM Shutdown Flags (raw)",
    "header_error": "Header Error",
    "footer_crc_error": "Footer/CRC Error",
    "timeout_error": "Timeout Error",
    "prt_duty_violation_count": "PRT Duty Violation Count",
    "prt_width_violation_count": "PRT Width Violation Count",
    "mfg_agency_id": "Mfg Agency ID",
    "firmware_version": "Firmware Version",
    "serial_number": "Serial Number",
    "on_time_hours": "TRM On-Time (hours)",
    "operation_command_type": "Operation Command Type",
    "total_prt_count": "Total PRT Count",
    "processed_prt_count": "Processed PRT Count",
    "dwell_prt_count": "Dwell PRT Count",
    "total_sob_count": "Total SOB Count",
    "beam_data_register_address": "Beam Data Register Address",
}

_CHANNEL_FIELD_LABELS = {
    "temperature_status": "Temp",
    "dc_status": "DC Status",
    "rf_status": "RF Status",
    "tx_control_count": "Tx Ctrl Count",
    "rx_control_count": "Rx Ctrl Count",
    "op_mode": "Op Mode",
    "control": "Control",
    "tx_phase": "Tx Phase",
    "tx_atten": "Tx Atten",
    "rx_phase": "Rx Phase",
    "rx_atten": "Rx Atten",
}

# Which top-level decoded fields can be shown on the QTRM matrix cells, per
# Status Type - "channels" (Diagnostic's per-channel breakdown) and
# "beam_data_register_address" (not implemented on the QTRM side) are
# deliberately excluded; they still appear in the Details panel below.
_FILTERABLE_FIELDS = {
    STATUS_TYPE_ACK: ["message_id", "echoed_bytes"],
    STATUS_TYPE_HEALTH: [
        "dc_voltage_status", "dc_current_status", "temperature_status",
        "tx_forward_rf_status", "rx_reverse_rf_status",
    ],
    STATUS_TYPE_ERR_LOG: [
        "trm_shutdown_flags", "header_error", "footer_crc_error",
        "timeout_error", "prt_duty_violation_count", "prt_width_violation_count",
    ],
    STATUS_TYPE_MFG: ["mfg_agency_id", "firmware_version", "serial_number", "on_time_hours"],
}

_DIAGNOSTIC_FILTERABLE_FIELDS = {
    DIAGNOSTIC_TYPE_DETAILED_HEALTH: [
        "operation_command_type", "total_prt_count", "processed_prt_count",
        "dwell_prt_count", "total_sob_count",
    ],
    DIAGNOSTIC_TYPE_FUTURE_BUFFER: ["total_prt_count", "processed_prt_count", "dwell_prt_count", "total_sob_count"],
    DIAGNOSTIC_TYPE_PRESENT_BUFFER: ["total_prt_count", "processed_prt_count", "dwell_prt_count", "total_sob_count"],
}


# Selecting this field under HEALTH switches the matrix from the plain
# LedMatrix to tx_forward_matrix.py's TxForwardMatrix (per-channel LEDs) -
# see _is_tx_forward_mode().
_TX_FORWARD_FIELD = "tx_forward_rf_status"


def _format_value(value) -> str:
    return value.hex(" ").upper() if isinstance(value, (bytes, bytearray)) else str(value)


# Field-filter selector buttons - highlighted (accent teal) when selected,
# muted/outlined when not, instead of a checkbox+tick.
_FILTER_BTN_STYLE_OFF = (
    "QPushButton { background-color: #393e46; color: rgba(238, 238, 238, 0.7);"
    "border: 1px solid #4a515a; border-radius: 10px; padding: 6px 12px; font-weight: 600; }"
    "QPushButton:hover { background-color: #454d57; }"
)
_FILTER_BTN_STYLE_ON = (
    "QPushButton { background-color: #00adb5; color: #1f2328;"
    "border: 1px solid #00adb5; border-radius: 10px; padding: 6px 12px; font-weight: 600; }"
    "QPushButton:hover { background-color: #1fc2ca; }"
)

# Send button color/QSS from command_style.py, the single source of truth
# every command tab shares.
_SEND_BTN_STYLE = send_button_style()


class StatusTab(QWidget):
    # status_type, sub_status_type, beam_register_address (always 0 - not implemented)
    send_all_requested = Signal(int, int, int)
    # qtrm_index (0-based), status_type, sub_status_type, beam_register_address (always 0)
    individual_send_requested = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results = None
        self._individual_target = None
        self._individual_result = None
        self._last_mode = None  # "all" | "individual" | None - which cache _on_filter_changed should reuse
        self._auto_resending = False
        self._resend_timer = QTimer(self)
        self._resend_timer.timeout.connect(lambda: self._on_send_all_clicked(is_auto_resend=True))

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        layout.addWidget(self._build_selector_group())

        top_row = QHBoxLayout()
        self.send_btn = QPushButton("Send All")
        self.send_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.send_btn.clicked.connect(self._on_send_all_clicked)
        top_row.addWidget(self.send_btn)

        top_row.addWidget(QLabel("Resend every (s):"))
        self.resend_spin = DoubleSpinField(0.0, 300.0, 0.0, step=0.1, decimals=1, field_width=64)
        top_row.addWidget(self.resend_spin)

        # Same plain-QPushButton look as dwell_tab.py's Import/Save-to-CSV
        # buttons (no custom stylesheet - relies on the app-wide default) -
        # this is a file-export action, a different category from Send All,
        # so it deliberately doesn't use send_button_style().
        self.export_btn = QPushButton("Export to Excel...")
        self.export_btn.clicked.connect(self._on_export_clicked)
        top_row.addWidget(self.export_btn)

        self.summary_label = QLabel("Not yet run")
        self.response_time_label = QLabel("")
        top_row.addWidget(self.summary_label)
        top_row.addWidget(self.response_time_label)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self.led_matrix = LedMatrix()
        self.led_matrix.led_clicked.connect(self._on_led_clicked)
        layout.addWidget(self.led_matrix, 1)

        # Alternate full-array view, shown instead of led_matrix only while
        # "Tx Forward RF Status" is the selected "Show Field" - see
        # _is_tx_forward_mode()/_update_matrix_visibility(). Both matrices
        # are kept in sync on every state change (mark_pending/show_results/
        # etc.) regardless of which is actually visible, so toggling
        # between them never shows stale data.
        self.tx_forward_matrix = TxForwardMatrix()
        self.tx_forward_matrix.cell_clicked.connect(self._on_led_clicked)
        self.tx_forward_matrix.setVisible(False)
        layout.addWidget(self.tx_forward_matrix, 1)

        self.details_box = self._build_details_group()
        layout.addWidget(self.details_box)

        # Wrapped in a QScrollArea so this tab's minimumSizeHint stays small
        # (bounded by the scroll area itself, not the matrix + details
        # panel's combined natural size) - lets the whole window shrink to
        # fit any screen, with scrollbars appearing instead of the window
        # refusing to shrink. Same pattern already used by cal_tab.py.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        # HeaderPanel is now a single global full-height sidebar owned by
        # main_window.py, not embedded per-tab - see its module docstring.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._on_status_type_changed()  # set initial control visibility + filter checkboxes

    # -- status type + sub-parameter selector + field filter, one row ------
    # Combined into a single QGroupBox/row (rather than two stacked boxes)
    # specifically to save vertical space - each QGroupBox carries the
    # app's generous default padding/margin (meant for one large standalone
    # card - see the CP-box height fixes elsewhere in this app for the same
    # lesson learned earlier), and stacking two of them was pushing the LED
    # matrix and Details panel below the visible window.

    def _build_selector_group(self):
        # Qt's QGroupBox::title subcontrol often ignores font-weight/size
        # set via stylesheet (the style engine renders it from the
        # widget's actual font, not the QSS text properties) - a real
        # QLabel as the heading, styled normally, is the reliable way to
        # get a bold/larger section title instead of the flat native one.
        box = QGroupBox("")
        box.setStyleSheet("QGroupBox { padding-top: 14px; }")
        outer = QVBoxLayout(box)
        title_label = QLabel("STATUS TYPE")
        title_label.setStyleSheet(
            "color: #00adb5; font-size: 13pt; font-weight: 700; letter-spacing: 0.6px; background: transparent;"
        )
        outer.addWidget(title_label)

        row = QHBoxLayout()
        outer.addLayout(row)

        row.addWidget(QLabel("Status Type:"))
        self.status_type_combo = QComboBox()
        for label, value in _STATUS_TYPES:
            self.status_type_combo.addItem(label, value)
        self.status_type_combo.currentIndexChanged.connect(self._on_status_type_changed)
        row.addWidget(self.status_type_combo)

        self.ack_sub_label = QLabel("ACK Sub Status (bits 1-4):")
        self.ack_sub_spin = SpinField(0, 15, 0, field_width=56)
        row.addWidget(self.ack_sub_label)
        row.addWidget(self.ack_sub_spin)

        self.diagnostic_type_label = QLabel("Diagnostic Type:")
        self.diagnostic_type_combo = QComboBox()
        for label, value in _DIAGNOSTIC_TYPES:
            self.diagnostic_type_combo.addItem(label, value)
        self.diagnostic_type_combo.currentIndexChanged.connect(self._on_diagnostic_type_changed)
        row.addWidget(self.diagnostic_type_label)
        row.addWidget(self.diagnostic_type_combo)

        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setStyleSheet("background-color: #4a515a; max-width: 1px; border: none;")
        row.addWidget(divider)

        row.addWidget(QLabel("Show Field:"))
        self.filter_button_group = QButtonGroup(self)
        # Not exclusive=True: Qt's exclusive QButtonGroup behaves like a
        # radio-button group once anything is checked (it won't let the
        # last checked button go back to unchecked), but "toggle one at a
        # time" here needs a genuine none-checked state too (that's what
        # reverts every cell back to "QTRM-N"). Exclusivity is enforced
        # manually in _on_checkbox_toggled instead.
        self.filter_button_group.setExclusive(False)
        self.filter_button_group.buttonToggled.connect(self._on_checkbox_toggled)
        self.filter_layout = QHBoxLayout()
        row.addLayout(self.filter_layout)

        row.addStretch(1)
        return box

    def _on_status_type_changed(self):
        status_type = self.status_type_combo.currentData()
        is_ack = status_type == STATUS_TYPE_ACK
        is_diagnostic = status_type == STATUS_TYPE_DIAGNOSTIC
        self.ack_sub_label.setVisible(is_ack)
        self.ack_sub_spin.setVisible(is_ack)
        self.diagnostic_type_label.setVisible(is_diagnostic)
        self.diagnostic_type_combo.setVisible(is_diagnostic)
        self._rebuild_filter_checkboxes()
        # A previous query's results were for a different Status Type and
        # no longer apply - same reasoning as resetting on tab-change.
        self.reset_to_idle()

    def _on_diagnostic_type_changed(self):
        self._rebuild_filter_checkboxes()
        self.reset_to_idle()

    def _current_params(self):
        """(status_type, sub_status_type, beam_register_address) from the currently selected controls."""
        status_type = self.status_type_combo.currentData()
        if status_type == STATUS_TYPE_ACK:
            return status_type, self.ack_sub_spin.value(), 0
        if status_type == STATUS_TYPE_DIAGNOSTIC:
            return status_type, self.diagnostic_type_combo.currentData(), 0
        return status_type, 0, 0

    # -- field filter (which decoded value to show on the matrix cells) ----

    def _current_fields(self):
        status_type = self.status_type_combo.currentData()
        if status_type == STATUS_TYPE_DIAGNOSTIC:
            diagnostic_type = self.diagnostic_type_combo.currentData()
            return _DIAGNOSTIC_FILTERABLE_FIELDS.get(diagnostic_type, [])
        return _FILTERABLE_FIELDS.get(status_type, [])

    def _rebuild_filter_checkboxes(self):
        for btn in self.filter_button_group.buttons():
            self.filter_button_group.removeButton(btn)
        while self.filter_layout.count():
            item = self.filter_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for field in self._current_fields():
            btn = QPushButton(_FIELD_LABELS.get(field, field.replace("_", " ").title()))
            btn.setCheckable(True)
            btn.setProperty("field_key", field)
            btn.setStyleSheet(_FILTER_BTN_STYLE_OFF)
            self.filter_button_group.addButton(btn)
            self.filter_layout.addWidget(btn)
        self.filter_layout.addStretch(1)

    def _current_filter_field(self):
        checked = self.filter_button_group.checkedButton()
        return checked.property("field_key") if checked is not None else None

    def _is_tx_forward_mode(self) -> bool:
        return (
            self.status_type_combo.currentData() == STATUS_TYPE_HEALTH
            and self._current_filter_field() == _TX_FORWARD_FIELD
        )

    def _update_matrix_visibility(self):
        tx_mode = self._is_tx_forward_mode()
        self.led_matrix.setVisible(not tx_mode)
        self.tx_forward_matrix.setVisible(tx_mode)

    def _on_checkbox_toggled(self, button, checked: bool):
        if checked:
            # Manual exclusivity: selecting one deselects every other one -
            # "toggle one at a time" - but unlike Qt's built-in exclusive
            # mode, deselecting the active one is still allowed (reverts to
            # no filter / plain "QTRM-N" labels).
            for other in self.filter_button_group.buttons():
                if other is not button and other.isChecked():
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
        for btn in self.filter_button_group.buttons():
            btn.setStyleSheet(_FILTER_BTN_STYLE_ON if btn.isChecked() else _FILTER_BTN_STYLE_OFF)
        self._update_matrix_visibility()
        self._on_filter_changed()

    def _on_filter_changed(self):
        if self._last_mode == "individual" and self._individual_target is not None:
            self._set_led_text_for_one(self._individual_target, self._individual_result)
        elif self._last_mode == "all" and self._results is not None:
            self._apply_filter_to_leds(self._results)

    def _reset_led_texts(self):
        for i, led in enumerate(self.led_matrix._leds):
            led.setText(f"QTRM-{i}")

    def _apply_filter_to_leds(self, results):
        field = self._current_filter_field()
        if field is None:
            self._reset_led_texts()
            return
        for i, result in enumerate(results):
            self._set_led_text_for_one(i, result, field)

    def _set_led_text_for_one(self, qtrm_index: int, decoded, field=None):
        if field is None:
            field = self._current_filter_field()
        led = self.led_matrix._leds[qtrm_index]
        if field is not None and decoded is not None and field in decoded:
            # Keep the QTRM number visible alongside the filtered value, on
            # the same line - otherwise it's ambiguous which cell is which
            # once every label has been replaced by a bare number.
            led.setText(f"QTRM-{qtrm_index}: {_format_value(decoded[field])}")
        else:
            led.setText(f"QTRM-{qtrm_index}")

    # -- export to Excel -----------------------------------------------------

    def _export_field_list(self):
        """
        Which fields to put in the export, in on-screen display order:
        just the one field currently selected via "Show Field" if one is
        selected (mirrors what's actually highlighted/visible on the QTRM
        cells right now), otherwise every filterable field for the current
        Status Type/Diagnostic Type (i.e. everything the "Show Field" row
        currently offers) - same order _current_fields() already returns,
        which is also the order its buttons are laid out in.
        """
        selected = self._current_filter_field()
        if selected is not None:
            return [selected]
        return self._current_fields()

    def _on_export_clicked(self):
        if self._results is None:
            QMessageBox.warning(
                self, "Nothing to export",
                "Run 'Send All' first to gather QTRM data before exporting.",
            )
            return

        fields = self._export_field_list()
        if not fields:
            QMessageBox.warning(self, "Nothing to export", "The current Status Type has no exportable fields.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Export Status Data", "", "Excel Files (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        wb = Workbook()
        ws = wb.active
        ws.title = "Status"
        ws.append(["QTRM"] + [_FIELD_LABELS.get(f, f.replace("_", " ").title()) for f in fields])
        for qtrm_index, decoded in enumerate(self._results):
            if decoded is None:
                row = [qtrm_index] + ["No Response"] * len(fields)
            else:
                row = [qtrm_index] + [_format_value(decoded[f]) if f in decoded else "" for f in fields]
            ws.append(row)

        try:
            wb.save(path)
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"Could not write '{path}':\n{e}")
            return
        self.response_time_label.setText(f"Exported to {path}")

    # -- details panel -------------------------------------------------------

    def _build_details_group(self):
        box = QGroupBox("Details (last individually-queried QTRM)")
        self.details_layout = QVBoxLayout(box)
        self.details_placeholder = QLabel("Click one QTRM cell above to see its decoded response here.")
        self.details_layout.addWidget(self.details_placeholder)
        return box

    @staticmethod
    def _clear_layout_recursive(layout):
        # takeAt(0) only detaches items from the layout - for a WIDGET item
        # that's enough (deleteLater() then actually removes it), but for a
        # nested LAYOUT item (the "channels" QHBoxLayout / the field
        # QGridLayout added via addLayout, both used by _populate_details)
        # item.widget() is None and the widgets *inside* that nested layout
        # were never touched - they stayed parented and visible, just no
        # longer positioned by anything, which is exactly what caused the
        # overlapping leftover text bug. Recursing into item.layout() clears
        # those nested widgets too before deleting the now-empty sub-layout.
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # hide() is immediate/synchronous; deleteLater() is
                # deferred to the next event-loop pass. Without the hide(),
                # a detached-but-not-yet-actually-deleted widget stays
                # visible at its last position for however long that
                # takes, which is exactly what produced the overlapping
                # leftover text - hide() guarantees no visual overlap even
                # for that brief window.
                widget.hide()
                widget.deleteLater()
                continue
            child_layout = item.layout()
            if child_layout is not None:
                StatusTab._clear_layout_recursive(child_layout)
                child_layout.deleteLater()

    def _clear_details_layout(self):
        self._clear_layout_recursive(self.details_layout)

    def _populate_details(self, qtrm_index: int, decoded: dict):
        self._clear_details_layout()
        self.details_box.setTitle(f"Details - QTRM-{qtrm_index}")

        # Fill columns left-to-right first, then wrap to a new row - rather
        # than one field per row (QFormLayout), which left most of the
        # panel's width empty for status types with just a handful of
        # fields.
        fields = [(k, v) for k, v in decoded.items() if k != "channels"]
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(10)
        for i, (key, value) in enumerate(fields):
            label = _FIELD_LABELS.get(key, key.replace("_", " ").title())
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(QLabel(f"{label}:"))
            cell.addWidget(QLabel(_format_value(value)))
            wrapper = QWidget()
            wrapper.setLayout(cell)
            grid.addWidget(wrapper, i // _DETAILS_WRAP_COLS, i % _DETAILS_WRAP_COLS)
        self.details_layout.addLayout(grid)

        channels = decoded.get("channels")
        if channels:
            channels_row = QHBoxLayout()
            for ch_index, ch_fields in enumerate(channels):
                ch_box = QGroupBox(f"Ch{ch_index + 1}")
                ch_form = QFormLayout(ch_box)
                for key, value in ch_fields.items():
                    label = _CHANNEL_FIELD_LABELS.get(key, key.replace("_", " ").title())
                    ch_form.addRow(f"{label}:", QLabel(str(value)))
                channels_row.addWidget(ch_box)
            self.details_layout.addLayout(channels_row)

    def _clear_details_to_placeholder(self):
        self._clear_details_layout()
        self.details_box.setTitle("Details (last individually-queried QTRM)")
        self.details_placeholder = QLabel("Click one QTRM cell above to see its decoded response here.")
        self.details_layout.addWidget(self.details_placeholder)

    # -- full-array send (Send All button, with optional auto-resend) ------

    def _on_send_all_clicked(self, is_auto_resend: bool = False):
        if self._auto_resending and not is_auto_resend:
            self._resend_timer.stop()
            self._auto_resending = False
            self.send_btn.setText("Send All")
            return

        status_type, sub_status_type, beam_register_address = self._current_params()
        interval_s = self.resend_spin.value()
        self.send_all_requested.emit(status_type, sub_status_type, beam_register_address)
        if interval_s > 0 and not self._auto_resending:
            self._auto_resending = True
            self.send_btn.setText("Stop")
            self._resend_timer.start(int(interval_s * 1000))

    def reset_to_idle(self):
        """
        Called by main_window.py whenever the main QTabWidget's current tab
        changes (to this one or away from it) - a previous query's colors/
        values don't apply to whatever gets selected/sent next, so the
        whole matrix (and the Details panel) goes back to a clean slate
        rather than showing increasingly stale results.
        """
        self._results = None
        self._individual_target = None
        self._individual_result = None
        self._last_mode = None
        self.summary_label.setText("Not yet run")
        self.response_time_label.setText("")
        self.led_matrix.set_all(_IDLE_COLOR)
        self._reset_led_texts()
        self.tx_forward_matrix.set_all_state("idle")
        self._update_matrix_visibility()
        self._clear_details_to_placeholder()

    def mark_pending(self):
        # No artificial reveal delay - LEDs turn green/red the instant a
        # real response arrives (show_results), or red on an actual
        # timeout (show_no_response, driven by main_window.py's real
        # RESPONSE_TIMEOUT_MS wait) - "delay is only there if i dont
        # recieve a command", per Yuvraj. A fixed cosmetic delay here used
        # to hold results back for a full second even when the response
        # had already arrived.
        self.summary_label.setText("Sent - waiting for response...")
        self.response_time_label.setText("")
        self._results = None
        self._last_mode = "all"
        self.led_matrix.set_all(_PENDING_COLOR)
        self._reset_led_texts()
        self.tx_forward_matrix.set_all_state("pending")

    def show_results(self, results):
        self._results = results
        valid_flags = [r is not None for r in results]
        valid_count = sum(valid_flags)
        self.summary_label.setText(f"{valid_count}/{NUM_QTRM} QTRMs responded")
        self.led_matrix.set_results(valid_flags)
        self._apply_filter_to_leds(results)
        self.tx_forward_matrix.set_results(results)

    def show_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_no_response(self):
        self.summary_label.setText("No response")
        self.response_time_label.setText("")
        self.led_matrix.set_all(_NOT_LINKED_COLOR)
        self._reset_led_texts()
        self.tx_forward_matrix.set_all_state("no_response")

    # -- individual QTRM query (click one LED) ------------------------------

    def _on_led_clicked(self, qtrm_index: int):
        status_type, sub_status_type, beam_register_address = self._current_params()
        self.individual_send_requested.emit(qtrm_index, status_type, sub_status_type, beam_register_address)

    def mark_individual_pending(self, qtrm_index: int):
        self.summary_label.setText(f"QTRM-{qtrm_index}: waiting for response...")
        self.response_time_label.setText("")
        self._individual_target = qtrm_index
        self._individual_result = None
        self._last_mode = "individual"
        self.led_matrix.set_all(_PENDING_COLOR)
        self._reset_led_texts()
        self.tx_forward_matrix.set_all_state("pending")
        self._clear_details_to_placeholder()

    def show_individual_result(self, qtrm_index: int, decoded):
        self._individual_result = decoded
        self._reveal_individual_now(qtrm_index, decoded)

    def show_individual_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_individual_no_response(self, qtrm_index: int):
        self.summary_label.setText(f"QTRM-{qtrm_index}: No response")
        self.led_matrix.set_one(qtrm_index, _NOT_LINKED_COLOR)
        self.tx_forward_matrix.set_one_result(qtrm_index, None)

    def _reveal_individual_now(self, qtrm_index: int, decoded):
        if decoded is None:
            self.led_matrix.set_one(qtrm_index, _NOT_LINKED_COLOR)
            self.summary_label.setText(f"QTRM-{qtrm_index}: No valid response")
            self._set_led_text_for_one(qtrm_index, None)
            self.tx_forward_matrix.set_one_result(qtrm_index, None)
            return
        self.led_matrix.set_one(qtrm_index, _LINKED_COLOR)
        self.summary_label.setText(f"QTRM-{qtrm_index}: Responded")
        self._populate_details(qtrm_index, decoded)
        self._set_led_text_for_one(qtrm_index, decoded)
        self.tx_forward_matrix.set_one_result(qtrm_index, decoded)
