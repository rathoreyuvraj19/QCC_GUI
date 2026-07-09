"""
header_panel.py

A single global "Last Received Header" sidebar, owned and instantiated
once by main_window.py - decoded QCC Header fields (per
QCC_90Byte_Header_BitTable.docx, 2026-07-05: the full 90-byte response
header, since every response flowing back to the main GUI is QCC -> RC
direction) plus the raw hex of the whole 90-byte header (kept alongside
the decoded fields for byte-level verification, e.g. spotting an
unexpected non-zero reserved byte).

Positioned as a fixed-width right column spanning the FULL window height,
next to a left column holding the Connection bar + Tabs (not embedded
inside any individual tab's own layout, and not one instance per tab) -
so it shows whichever frame was most recently received from ANY tab, a
global "last received," not per-tab memory.

Also carries a "Query QCC Status" button - QCC_STATUS (0x01,
COMMAND_ID_QCC_STATUS in rc_settings.py) is a non-operational command:
QCC just returns its current header with the latest sensor/counter values
and a zero-filled body, no action taken. Lets the operator manually
refresh this panel's numbers on demand from any tab, independent of
whatever command that tab actually sends. main_window.py wires
query_status_requested to actually building/sending that frame.

Wrapped in its own QScrollArea (same reasoning as every tab's main
content, see main_window.py's window-fit history) - the decoded fields
plus the raw-hex block have real natural height, and this panel sits
outside the tabs' own scroll areas, so without this its full height would
add directly to the whole window's minimum size.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.packet import FIXED_HEADER_SIZE, QCC_HEADER_SIZE, QCCHeaderTx

_PANEL_WIDTH = 340
_HEADER_TOTAL_SIZE = FIXED_HEADER_SIZE + QCC_HEADER_SIZE

_QCC_COMMAND_NAMES = {
    QCCHeaderTx.QCC_COMMAND_DATA_DISTRIBUTION: "DATA_DISTRIBUTION",
    QCCHeaderTx.QCC_COMMAND_QCC_STATUS: "QCC_STATUS",
    QCCHeaderTx.QCC_COMMAND_QCC_RESET: "QCC_RESET",
    QCCHeaderTx.QCC_COMMAND_PRT_BYPASS: "PRT_BYPASS",
    QCCHeaderTx.QCC_COMMAND_SOB_BYPASS: "SOB_BYPASS",
    QCCHeaderTx.QCC_COMMAND_PRT_INTERNAL_GEN: "PRT_INTERNAL_GEN",
    QCCHeaderTx.QCC_COMMAND_SOB_INTERNAL_GEN: "SOB_INTERNAL_GEN",
    QCCHeaderTx.QCC_COMMAND_PPS_INTERNAL_GEN: "PPS_INTERNAL_GEN",
    QCCHeaderTx.QCC_COMMAND_REMOTE_PROGRAMMING: "REMOTE_PROGRAMMING",
}

_ACCENT = "#00adb5"
_ACCENT_HOVER = "#1fc2ca"
_ACCENT_PRESSED = "#00858c"
_CARD_BG = "#31363d"
_BORDER = "#42484f"
_LABEL_COLOR = "rgba(238, 238, 238, 0.78)"
_VALUE_COLOR = "#eeeeee"
# Roughly the width of one label:value form row - section dividers are
# capped to this instead of stretching the full card width, so they don't
# visually extend further right than the content rows they separate.
_CONTENT_COL_WIDTH = 260

# Any field whose value changed from the previous frame stays highlighted
# (not a brief flash) until the next command is sent (main_window.py calls
# clear_highlights() right before every send) - padding/radius stay
# identical between normal and highlighted so toggling doesn't nudge the
# row's layout, only background-color changes.
_GLOW_BG = "rgba(0, 173, 181, 0.35)"
_VALUE_BASE_CSS = (
    f"color: {_VALUE_COLOR}; font-weight: 600; font-size: 9pt;"
    "font-family: Consolas, monospace; border: none; border-radius: 4px; padding: 1px 3px;"
)
_VALUE_NORMAL_STYLE = _VALUE_BASE_CSS + " background: transparent;"
_VALUE_GLOW_STYLE = _VALUE_BASE_CSS + f" background-color: {_GLOW_BG};"
_CHECKSUM_OK_BASE_CSS = (
    "color: rgb(146, 208, 165); font-weight: 600; font-size: 9pt;"
    "font-family: Consolas, monospace; border: none; border-radius: 4px; padding: 1px 3px;"
)
_CHECKSUM_FAIL_BASE_CSS = (
    "color: rgb(240, 149, 149); font-weight: 600; font-size: 9pt;"
    "font-family: Consolas, monospace; border: none; border-radius: 4px; padding: 1px 3px;"
)
_CHECKSUM_OK_NORMAL_STYLE = _CHECKSUM_OK_BASE_CSS + " background: transparent;"
_CHECKSUM_OK_GLOW_STYLE = _CHECKSUM_OK_BASE_CSS + f" background-color: {_GLOW_BG};"
_CHECKSUM_FAIL_NORMAL_STYLE = _CHECKSUM_FAIL_BASE_CSS + " background: transparent;"
_CHECKSUM_FAIL_GLOW_STYLE = _CHECKSUM_FAIL_BASE_CSS + f" background-color: {_GLOW_BG};"

# (section title, [field names]) - grouped so related values read together
# instead of one long undifferentiated list of 26 rows.
_FIELD_SECTIONS = [
    ("Routing / Command", [
        "DESTINATION_ID", "SOURCE_ID", "PACKET_SIZE",
        "ECHO_BYTE", "COMMAND_ACK", "QCC_COMMAND",
        "MESSAGE_NUMBER", "CHECKSUM",
    ]),
    ("Timestamp", ["DATE", "MONTH", "YEAR", "TIME_OF_DAY"]),
    ("Board Health", ["FPGA_TEMPERATURE", "BOARD_TEMPERATURE", "BOARD_HUMIDITY"]),
    ("SOB / PRT / PPS Counters", [
        "INPUT_SOB_COUNT", "INPUT_PRT_COUNT", "INPUT_PPS_COUNT",
        "OUTPUT_PRT_COUNT", "OUTPUT_SOB_COUNT",
    ]),
    ("Pulse Widths (µs)", [
        "INPUT_SOB_WIDTH_US", "OUTPUT_SOB_WIDTH_US",
        "INPUT_PRT_WIDTH_US", "OUTPUT_PRT_WIDTH_US",
        "INPUT_PPS_WIDTH_US",
    ]),
    ("PRT PRI (µs)", ["INPUT_PRT_PRI", "OUTPUT_PRT_PRI"]),
    ("Misc", ["PPS_COUNTER", "CHIP_ID"]),
]

_QUERY_BTN_STYLE = (
    f"QPushButton {{ background-color: transparent; color: {_ACCENT};"
    f"border: 1px solid {_ACCENT}; border-radius: 8px; padding: 5px 8px; font-weight: 600; }}"
    f"QPushButton:hover {{ background-color: rgba(0, 173, 181, 0.15); }}"
    f"QPushButton:pressed {{ background-color: rgba(0, 173, 181, 0.3); }}"
    f"QPushButton:disabled {{ color: rgba(238, 238, 238, 0.35); border-color: rgba(238, 238, 238, 0.2); }}"
)


def _hex_full(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data) or "-"


class HeaderPanel(QWidget):
    """
    Single global instance owned by main_window.py, shown as a fixed-width
    sidebar spanning the full window height (beside both the Connection
    bar and the Tabs, not nested inside any one tab's own layout) - shows
    whichever frame was most recently received from ANY tab, not a
    per-tab memory. Call show_frame(raw_2970_byte_frame) whenever a
    response arrives.
    """

    query_status_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Fixed width, not a stretch-based floor - this sidebar's width
        # stays constant regardless of window size; only its height
        # stretches (to fill the whole window, top to bottom).
        self.setFixedWidth(_PANEL_WIDTH)

        # name -> normal style to revert to when cleared. A field appears
        # here as soon as its value changes at least once and stays until
        # clear_highlights() is called - main_window.py calls that at the
        # start of every send, so a field lights up on the response to
        # THAT command and stays lit until the next command is sent
        # (not until the operator manually clears it).
        self._highlighted = {}

        # No native title text - Qt's QGroupBox::title subcontrol often
        # ignores font-weight/size set via stylesheet (the style engine
        # renders it from the widget's actual font, not the QSS text
        # properties), so a real QLabel is used instead for a reliably
        # bold/larger heading. The app-wide QGroupBox chrome (theme.py) is
        # also generously padded by design for normal-content boxes, but
        # this panel is information-dense (26+ fields) and was overflowing
        # into an internal scroll in a normal-height window - a tighter
        # override here (not a global theme.py change, which would affect
        # every other box in the app) claws back real vertical room.
        box = QGroupBox("")
        box.setObjectName("HeaderPanelBox")
        box.setStyleSheet("#HeaderPanelBox { margin-top: 2px; padding: 8px 10px 6px 10px; }")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        title_label = QLabel("LAST RECEIVED HEADER")
        title_label.setStyleSheet(
            "color: #00adb5; font-size: 13pt; font-weight: 700; letter-spacing: 0.6px; background: transparent;"
        )
        layout.addWidget(title_label)

        query_row = QHBoxLayout()
        self.query_btn = QPushButton("Query QCC Status")
        self.query_btn.setStyleSheet(_QUERY_BTN_STYLE)
        self.query_btn.setToolTip(
            "Sends a non-operational QCC Status command - QCC just replies with\n"
            "its current header (latest sensor/counter values), no action taken."
        )
        self.query_btn.clicked.connect(self.query_status_requested)
        # Normal button size, not stretched to the panel's full width - a
        # stretched single-word pill reads as a section header, not a
        # clickable action. Centered with a stretch on both sides.
        query_row.addStretch(1)
        query_row.addWidget(self.query_btn)
        query_row.addStretch(1)
        layout.addLayout(query_row)

        self.query_status_label = QLabel("")
        self.query_status_label.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 8pt;")
        layout.addWidget(self.query_status_label)

        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: 10px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 6, 10, 6)
        card_layout.setSpacing(3)

        # Widest field name across the WHOLE panel (not just one section) -
        # every section's label column is fixed to this same width, so
        # every value column lines up at the same x position throughout.
        label_font = QLabel().font()
        label_font.setPointSize(8)
        metrics = QFontMetrics(label_font)
        longest_name = max((n for _, names in _FIELD_SECTIONS for n in names), key=len)
        self._label_col_width = metrics.horizontalAdvance(longest_name) + 4

        self.field_labels = {}
        for i, (section_title, names) in enumerate(_FIELD_SECTIONS):
            if i > 0:
                # Capped to roughly the same width as a label:value row
                # (not stretched full card width) so the divider doesn't
                # visually extend further right than the content it's
                # separating - wrapped with a trailing stretch, same
                # "hug left" pattern as the grid rows below.
                divider = QFrame()
                divider.setFrameShape(QFrame.HLine)
                divider.setStyleSheet(f"background-color: {_BORDER}; max-height: 1px; border: none;")
                divider.setFixedWidth(_CONTENT_COL_WIDTH)
                div_wrap = QHBoxLayout()
                div_wrap.addWidget(divider)
                div_wrap.addStretch(1)
                card_layout.addLayout(div_wrap)

            section_label = QLabel(section_title.upper())
            section_label.setStyleSheet(
                f"color: {_ACCENT}; font-size: 8pt; font-weight: 700; letter-spacing: 0.5px;"
                "margin-top: 4px;"
            )
            card_layout.addWidget(section_label)

            card_layout.addLayout(self._build_form_section(names))

        layout.addWidget(card)

        layout.addWidget(QLabel(f"Full Header ({_HEADER_TOTAL_SIZE} bytes, hex):"))
        self.header_hex_label = QLabel("-")
        self.header_hex_label.setWordWrap(True)
        self.header_hex_label.setStyleSheet(
            f"color: {_ACCENT}; font-weight: 600; font-family: Consolas, monospace; font-size: 8pt;"
        )
        self.header_hex_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.header_hex_label.setCursor(Qt.IBeamCursor)
        layout.addWidget(self.header_hex_label)

        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        scroll.setWidget(box)

        # Fixed horizontally (width set above via setFixedWidth), only
        # stretches vertically to fill the sidebar's full column height.
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _make_value_label(self, name: str) -> QLabel:
        value_label = QLabel("-")
        value_label.setStyleSheet(_VALUE_NORMAL_STYLE)
        # Selectable/copyable via mouse - lets an operator copy a single
        # value (e.g. CHIP_ID) without retyping it by hand.
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value_label.setCursor(Qt.IBeamCursor)
        self.field_labels[name] = value_label
        return value_label

    def _build_form_section(self, names) -> QFormLayout:
        """
        Left-aligned label:value rows - every section's name_label is
        fixed to the same width (the widest field name across the WHOLE
        panel, not just this section), so the value column lines up at
        the same x position across every section instead of each
        section's values starting wherever its own longest label happens
        to end.
        """
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(2)
        for name in names:
            name_label = QLabel(name)
            name_label.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 8pt;")
            name_label.setFixedWidth(self._label_col_width)
            form.addRow(name_label, self._make_value_label(name))
        return form

    def _highlight(self, name: str, normal_style: str, glow_style: str):
        """
        Marks one field's value cell as changed - stays highlighted (no
        auto-revert) until clear_highlights() is called. Storing the
        field's own normal_style (rather than a single shared one) is what
        lets clear_highlights() restore CHECKSUM to whichever of the
        OK/FAIL styles is actually current, not a generic value style.
        """
        self.field_labels[name].setStyleSheet(glow_style)
        self._highlighted[name] = normal_style

    def clear_highlights(self):
        for name, normal_style in self._highlighted.items():
            self.field_labels[name].setStyleSheet(normal_style)
        self._highlighted.clear()

    def _set_field(self, name: str, text: str):
        label = self.field_labels[name]
        if label.text() != text:
            label.setText(text)
            self._highlight(name, _VALUE_NORMAL_STYLE, _VALUE_GLOW_STYLE)

    def show_frame(self, raw: bytes):
        header_raw = raw[0:_HEADER_TOTAL_SIZE]
        h = QCCHeaderTx.from_bytes(header_raw)

        self._set_field("DESTINATION_ID", str(h.destination_id))
        self._set_field("SOURCE_ID", str(h.source_id))
        self._set_field("PACKET_SIZE", str(h.packet_size))
        self._set_field("ECHO_BYTE", str(h.echo_byte))
        self._set_field("COMMAND_ACK", str(h.command_ack))
        self._set_field("MESSAGE_NUMBER", str(h.message_number))
        self._set_field("DATE", str(h.date))
        self._set_field("MONTH", str(h.month))
        self._set_field("YEAR", str(h.year))
        self._set_field("TIME_OF_DAY", str(h.time_of_day))
        self._set_field("QCC_COMMAND", _QCC_COMMAND_NAMES.get(h.qcc_command, f"0x{h.qcc_command:02X}"))
        self._set_field("FPGA_TEMPERATURE", str(h.fpga_temperature))
        self._set_field("BOARD_TEMPERATURE", str(h.board_temperature))
        self._set_field("BOARD_HUMIDITY", str(h.board_humidity))
        self._set_field("INPUT_SOB_COUNT", str(h.input_sob_count))
        self._set_field("INPUT_PRT_COUNT", str(h.input_prt_count))
        self._set_field("INPUT_PPS_COUNT", str(h.input_pps_count))
        self._set_field("OUTPUT_PRT_COUNT", str(h.output_prt_count))
        self._set_field("OUTPUT_SOB_COUNT", str(h.output_sob_count))
        self._set_field("INPUT_SOB_WIDTH_US", str(h.input_sob_width_us))
        self._set_field("OUTPUT_SOB_WIDTH_US", str(h.output_sob_width_us))
        self._set_field("INPUT_PRT_WIDTH_US", str(h.input_prt_width_us))
        self._set_field("OUTPUT_PRT_WIDTH_US", str(h.output_prt_width_us))
        self._set_field("INPUT_PPS_WIDTH_US", str(h.input_pps_width_us))
        self._set_field("INPUT_PRT_PRI", str(h.input_prt_pri))
        self._set_field("OUTPUT_PRT_PRI", str(h.output_prt_pri))
        self._set_field("PPS_COUNTER", str(h.pps_counter))
        self._set_field("CHIP_ID", f"0x{h.chip_id:08X}")

        checksum_label = self.field_labels["CHECKSUM"]
        new_checksum_text = "OK" if h.checksum_ok else "FAIL"
        normal_style = _CHECKSUM_OK_NORMAL_STYLE if h.checksum_ok else _CHECKSUM_FAIL_NORMAL_STYLE
        glow_style = _CHECKSUM_OK_GLOW_STYLE if h.checksum_ok else _CHECKSUM_FAIL_GLOW_STYLE
        if checksum_label.text() != new_checksum_text:
            checksum_label.setText(new_checksum_text)
            self._highlight("CHECKSUM", normal_style, glow_style)

        self.header_hex_label.setText(_hex_full(header_raw))
        self.query_btn.setEnabled(True)
        self.query_status_label.setText("")

    def mark_query_pending(self):
        self.query_btn.setEnabled(False)
        self.query_status_label.setText("Querying...")

    def mark_query_no_response(self):
        self.query_btn.setEnabled(True)
        self.query_status_label.setText("No response")

    def clear(self):
        self.clear_highlights()
        for name, label in self.field_labels.items():
            label.setText("-")
            if name == "CHECKSUM":
                label.setStyleSheet(_CHECKSUM_OK_NORMAL_STYLE)
            else:
                label.setStyleSheet(_VALUE_NORMAL_STYLE)
        self.header_hex_label.setText("-")
        self.query_status_label.setText("")
