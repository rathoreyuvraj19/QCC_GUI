"""
main_window.py

Top-level window: Connection bar (local port, QCC IP, QCC port,
Connect/Disconnect, Ping Test) plus the per-command tabs (Dwell, Link
Test, Status, RX/TX Cal, Isolation, Soft Reset, Memory Operation).

The first 90 bytes of every frame (fixed header + QCC header) are zero for
now - MSG_ID, MODE, and per-QTRM MSG_ID/Frequency ID are not implemented on
the QCC/QTRM side yet.
"""

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel,
    QGroupBox, QMessageBox, QTabWidget, QInputDialog,
)

from packet import (
    build_link_test_frame, build_individual_link_frame, parse_link_test_response,
    build_cal_frame, build_soft_reset_frame, build_isolation_frame,
    build_status_frame, parse_status_frame, STATUS_TYPE_DIAGNOSTIC,
    build_dwell_frame, build_memory_write_frame,
    QCCHeaderRx, QCCHeaderTx, FIXED_HEADER_SIZE, QCC_HEADER_SIZE,
    build_sob_message_body, build_prt_message_body, build_pps_message_body,
    build_header_only_frame,
)
from rc_settings import (
    rc_settings, COMMAND_ID_DWELL, COMMAND_ID_LINK_TEST, COMMAND_ID_STATUS,
    COMMAND_ID_RX_CAL, COMMAND_ID_TX_CAL, COMMAND_ID_ISOLATION,
    COMMAND_ID_SOFT_RESET, COMMAND_ID_MEMORY_OPERATION, COMMAND_ID_QCC_STATUS,
)
from command_style import send_button_style
from connection_settings import connection_settings
from udp_worker import UdpWorker
from ping_worker import PingWorker
from header_panel import HeaderPanel
from link_test_tab import LinkTestTab
from status_tab import StatusTab
from cal_tab import CalTab
from isolation_tab import IsolationTab
from soft_reset_tab import SoftResetTab
from dwell_tab import DwellTab
from memory_tab import MemoryTab
from timing_tab import TimingTab
from rc_settings_tab import RCSettingsTab

# Plain local gate, not real security - just requires a deliberate action
# before this NVM-write-capable tab can be opened, per Yuvraj's explicit ask.
MEMORY_TAB_PASSWORD = "0145"
from spin_field import SpinField
from rx_test_app import RxTestWindow
from tx_test_window import TxTestWindow
from status_responder_app import StatusResponderWindow


RESPONSE_TIMEOUT_MS = 1000

# Ping button colors - full QPushButton{...} selector-block form (not the
# flat "background-color: x;" property-only form) since QSS :hover/:pressed
# pseudo-states are only recognized inside a selector block - the flat form
# used previously meant the button never had any hover/pressed feedback at
# all, in any state. Every state gets its own (subtle) hover/pressed shade so
# the button always looks responsive, not just while idle.
_PING_IDLE_STYLE = (
    "QPushButton { background-color: #4a515a; color: #eeeeee; border: none;"
    "border-radius: 8px; padding: 6px 14px; }"
    "QPushButton:hover { background-color: #565f6a; }"
    "QPushButton:pressed { background-color: #3d434b; }"
)
_PING_PENDING_STYLE = (
    "QPushButton { background-color: rgb(160, 165, 172); color: #1f2328; border: none;"
    "border-radius: 8px; padding: 6px 14px; }"
    "QPushButton:hover { background-color: rgb(150, 155, 162); }"
    "QPushButton:pressed { background-color: rgb(140, 145, 152); }"
)
_PING_SUCCESS_STYLE = (
    "QPushButton { background-color: rgb(146, 208, 165); color: #1f2328; border: none;"
    "border-radius: 8px; padding: 6px 14px; }"
    "QPushButton:hover { background-color: rgb(130, 195, 150); }"
    "QPushButton:pressed { background-color: rgb(115, 180, 135); }"
)
_PING_FAILURE_STYLE = (
    "QPushButton { background-color: rgb(240, 149, 149); color: #1f2328; border: none;"
    "border-radius: 8px; padding: 6px 14px; }"
    "QPushButton:hover { background-color: rgb(230, 130, 130); }"
    "QPushButton:pressed { background-color: rgb(220, 115, 115); }"
)
# Local/LAN pings often resolve in well under this, too fast to visually
# register the pending state as a distinct "something happened" flash -
# floor it so re-clicking always shows a visible grey pulse before the
# result color lands.
_PING_MIN_PENDING_MS = 350

# Outlined/neutral toggle for the SOB/PRT quick-send shortcuts row - not
# the shared accent send_button_style(), since this button doesn't send
# anything itself, just shows/hides the two that do.
_QUICK_SEND_TOGGLE_STYLE = (
    "QPushButton { background-color: transparent; color: #00adb5; border: 1px solid #00adb5;"
    "border-radius: 8px; padding: 6px 12px; font-weight: 600; }"
    "QPushButton:hover { background-color: rgba(0, 173, 181, 0.15); }"
    "QPushButton:pressed { background-color: rgba(0, 173, 181, 0.3); }"
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC / 96x QTRM Control")
        # Tall enough that the Header panel's ~26 decoded fields fit
        # without its internal scrollbar kicking in on a typical 1080p
        # screen (see header_panel.py's compacted spacing) - 700 was too
        # short and forced scrolling even after that compaction.
        self.resize(1400, 900)

        self.worker: UdpWorker | None = None
        # None | "dwell" | "memory_write" | "memory_write_all" | "link_test" |
        # "individual_link_test" | "rx_cal" | "tx_cal" | "isolation_all" |
        # "isolation_individual" | "status_all" | "status_individual" |
        # "timing_sob" | "timing_prt" | "timing_pps"
        self._awaiting_kind = None
        self._individual_link_qtrm = None
        self._rx_cal_target = None
        self._tx_cal_target = None
        self._individual_isolation_qtrm = None
        self._individual_status_qtrm = None
        self._status_type_in_flight = None
        self._status_sub_type_in_flight = None
        self._memory_write_target = None
        self._last_unlocked_tab_index = 0
        self._ping_worker: PingWorker | None = None
        self._ping_pending_since: float | None = None
        self._pending_timer = None
        self._rx_test_window: RxTestWindow | None = None
        self._tx_test_window: TxTestWindow | None = None
        self._last_sent_frame: bytes | None = None
        self._last_received_frame: bytes | None = None
        self._responder_window: StatusResponderWindow | None = None
        self._qcc_ip_before_responder: str | None = None
        self._qcc_ip_overridden_for_responder = False

        self._build_ui()

    # -- UI construction ------------------------------------------------

    def _build_ui(self):
        self._build_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        # Primary horizontal split: a left column (Connection bar + Tabs,
        # expands to fill all remaining width) and a right column (a
        # single global HeaderPanel, fixed width, spanning the complete
        # window height from top to bottom) - not nested inside any one
        # tab's own layout, so it stays visible and full-height regardless
        # of which tab is active or how tall that tab's own content is.
        columns = QHBoxLayout(central)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)

        left_column = QWidget()
        root = QVBoxLayout(left_column)

        root.addWidget(self._build_connection_group())

        self.tabs = QTabWidget()

        self.dwell_tab = DwellTab()
        self.dwell_tab.send_requested.connect(self._on_dwell_send)
        self.tabs.addTab(self.dwell_tab, "Dwell")

        self.link_test_tab = LinkTestTab()
        self.link_test_tab.send_requested.connect(self._on_link_test_clicked)
        self.link_test_tab.individual_send_requested.connect(self._on_individual_link_test_clicked)
        self.tabs.addTab(self.link_test_tab, "Link Test")

        self.status_tab = StatusTab()
        self.status_tab.send_all_requested.connect(self._on_status_send_all)
        self.status_tab.individual_send_requested.connect(self._on_status_send_one)
        self.tabs.addTab(self.status_tab, "Status")

        self.rx_cal_tab = CalTab("RX Cal")
        self.rx_cal_tab.send_requested.connect(self._on_rx_cal_send)
        self.tabs.addTab(self.rx_cal_tab, "RX Cal")

        self.tx_cal_tab = CalTab("TX Cal")
        self.tx_cal_tab.send_requested.connect(self._on_tx_cal_send)
        self.tabs.addTab(self.tx_cal_tab, "TX Cal")

        self.isolation_tab = IsolationTab()
        self.isolation_tab.send_all_requested.connect(self._on_isolation_send_all)
        self.isolation_tab.send_one_requested.connect(self._on_isolation_send_one)
        self.tabs.addTab(self.isolation_tab, "Isolation")

        self.soft_reset_tab = SoftResetTab()
        self.soft_reset_tab.reset_all_requested.connect(self._on_reset_all_clicked)
        self.soft_reset_tab.reset_one_requested.connect(self._on_reset_one_clicked)
        self.tabs.addTab(self.soft_reset_tab, "Soft Reset")

        self.memory_tab = MemoryTab()
        self.memory_tab.write_requested.connect(self._on_memory_write)
        self.memory_tab.write_all_requested.connect(self._on_memory_write_all)
        self._memory_tab_index = self.tabs.addTab(self.memory_tab, "Memory Operation")

        self.timing_tab = TimingTab()
        self.timing_tab.sob_send_requested.connect(self._on_timing_sob_send)
        self.timing_tab.prt_send_requested.connect(self._on_timing_prt_send)
        self.timing_tab.pps_send_requested.connect(self._on_timing_pps_send)
        self.tabs.addTab(self.timing_tab, "Timing Generation")

        # Connection bar's SOB/PRT shortcuts (built before this tab existed,
        # see _build_connection_group) just click the real buttons here -
        # same current field values, same pending/result indicator, not a
        # separate action.
        self.conn_sob_btn.clicked.connect(self.timing_tab.sob_send_btn.click)
        self.conn_prt_btn.clicked.connect(self.timing_tab.prt_send_btn.click)

        self.rc_settings_tab = RCSettingsTab()
        self.tabs.addTab(self.rc_settings_tab, "RC Settings")

        # Status tab's matrix resets to idle whenever the current tab
        # changes (to it or away from it) - a previous query's results
        # don't apply to whatever gets selected/sent next, so they
        # shouldn't linger and look like they're still current. Also gates
        # the Memory Operation tab behind a password, per Yuvraj's ask -
        # this tab can write to real hardware's non-volatile flash.
        self.tabs.currentChanged.connect(self._on_tab_changed)

        root.addWidget(self.tabs)

        footer = QLabel("Made by Yuvraj DRAM")
        footer.setAlignment(Qt.AlignRight)
        footer.setStyleSheet("color: #9a9aa0; font-size: 8pt; padding: 2px 6px;")
        root.addWidget(footer)

        # The single global HeaderPanel - its "Query QCC Status" button
        # isn't tied to any one tab anymore, so it's wired here just once.
        self.header_panel = HeaderPanel()
        self.header_panel.query_status_requested.connect(self._on_query_qcc_status)

        columns.addWidget(left_column, 1)
        columns.addWidget(self.header_panel)

    def _build_menu_bar(self):
        # These 3 windows used to be buttons crammed into the Connection
        # bar - either squeezed into one wide row (forcing the window's
        # minimum width past the actual screen width) or stacked into their
        # own tall column (forcing the whole Connection group taller than
        # its actual content, leaving a big dead gap around the compact
        # fields row). A menu bar is the standard place for auxiliary
        # windows in engineering tools like this one, and takes no layout
        # space of its own.
        tools_menu = self.menuBar().addMenu("&Tools")

        rx_action = QAction("Open RX Test Window", self)
        rx_action.triggered.connect(self._on_open_rx_test_clicked)
        tools_menu.addAction(rx_action)

        tx_action = QAction("Open TX Test Window", self)
        tx_action.triggered.connect(self._on_open_tx_test_clicked)
        tools_menu.addAction(tx_action)

        tools_menu.addSeparator()

        responder_action = QAction("Open Status Responder", self)
        responder_action.triggered.connect(self._on_open_responder_clicked)
        tools_menu.addAction(responder_action)

    def _on_tab_changed(self, index):
        self.status_tab.reset_to_idle()
        self.rx_cal_tab.reset_to_idle()
        self.tx_cal_tab.reset_to_idle()
        self.timing_tab.reset_to_idle()
        self.memory_tab.reset_to_idle()

        if index == self.tabs.indexOf(self.rc_settings_tab):
            self.rc_settings_tab.refresh_message_number()

        # Prompts every time this tab is entered (not just once per session)
        # - per Yuvraj's explicit ask, since it can write to real hardware's
        # flash memory.
        if index == self._memory_tab_index:
            password, ok = QInputDialog.getText(
                self, "Memory Operation - Locked",
                "This tab can write to real hardware's flash memory.\nEnter password:",
                QLineEdit.Password,
            )
            if not (ok and password == MEMORY_TAB_PASSWORD):
                self.tabs.setCurrentIndex(self._last_unlocked_tab_index)
                return

        self._last_unlocked_tab_index = index

    def _build_connection_group(self):
        # Qt's QGroupBox::title subcontrol often ignores font-weight/size
        # set via stylesheet (the style engine renders it from the
        # widget's actual font, not the QSS text properties) - a real
        # QLabel as the heading, styled normally, is the reliable way to
        # get a bold/larger section title instead of the flat native one.
        box = QGroupBox("")
        box.setStyleSheet("QGroupBox { padding-top: 14px; }")

        self.local_port_edit = SpinField(1, 65535, connection_settings.local_port, field_width=64)
        self.local_port_edit.spin.valueChanged.connect(self._on_connection_field_changed)

        self.qcc_ip_edit = QLineEdit(connection_settings.qcc_ip)
        self.qcc_ip_edit.textChanged.connect(self._on_connection_field_changed)
        self.qcc_port_edit = SpinField(1, 65535, connection_settings.qcc_port, field_width=64)
        self.qcc_port_edit.spin.valueChanged.connect(self._on_connection_field_changed)
        self.qcc_port_edit.spin.valueChanged.connect(self._on_qcc_port_changed)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        self.conn_status_label = QLabel("Disconnected")

        self.ping_btn = QPushButton("Ping Test")
        self.ping_btn.setStyleSheet(_PING_IDLE_STYLE)
        self.ping_btn.clicked.connect(self._on_ping_clicked)
        self.ping_result_label = QLabel("")

        self.qcc_ip_edit.setMinimumWidth(120)

        row = QHBoxLayout()
        row.addWidget(QLabel("Local Port:"))
        row.addWidget(self.local_port_edit)
        row.addWidget(QLabel("QCC IP:"))
        row.addWidget(self.qcc_ip_edit)
        row.addWidget(QLabel("QCC Port:"))
        row.addWidget(self.qcc_port_edit)
        row.addWidget(self.connect_btn)
        row.addWidget(self.conn_status_label)
        row.addWidget(self.ping_btn)
        row.addWidget(self.ping_result_label)
        row.addStretch(1)
        self.quick_send_toggle_btn = QPushButton("Timing Generation ▾")
        self.quick_send_toggle_btn.setStyleSheet(_QUICK_SEND_TOGGLE_STYLE)
        self.quick_send_toggle_btn.setToolTip("Show/hide the SOB/PRT quick-send shortcuts")
        self.quick_send_toggle_btn.clicked.connect(self._on_quick_send_toggle_clicked)
        row.addWidget(self.quick_send_toggle_btn)

        # Quick-access shortcuts to Timing Generation's SOB/PRT sends,
        # available from every tab (not just Timing Generation itself) -
        # per Yuvraj's ask. These aren't a separate/duplicate action: they
        # just click the real buttons on the Timing Generation tab (wired
        # once that tab exists, see _build_ui), reusing its current field
        # values and pending/result indicator - not a second independent
        # copy with its own state.
        #
        # Hidden by default (per Yuvraj's later ask) and toggled via
        # quick_send_toggle_btn above - wrapped in its own QWidget (not a
        # bare QHBoxLayout) since only a widget's visibility can be
        # toggled; a hidden layout with no widget wrapper still reserves
        # its row's space.
        #
        # Own row, not packed into the row above - that row already sits
        # right at the edge of fitting a 1920px-wide display once font
        # metrics/DPI scaling vary machine to machine (this is the same
        # class of overflow already fixed once by moving the 3 window-
        # opening buttons into the Tools menu) - two more wide buttons in
        # that single row reintroduces the risk.
        self.shortcuts_container = QWidget()
        shortcuts_row = QHBoxLayout(self.shortcuts_container)
        shortcuts_row.setContentsMargins(0, 0, 0, 0)
        shortcuts_row.addWidget(QLabel("Quick send:"))
        self.conn_sob_btn = QPushButton("Send SOB")
        self.conn_sob_btn.setStyleSheet(send_button_style(radius=10, padding="8px 16px"))
        shortcuts_row.addWidget(self.conn_sob_btn)

        self.conn_prt_btn = QPushButton("Send PRT")
        self.conn_prt_btn.setStyleSheet(send_button_style(radius=10, padding="8px 16px"))
        shortcuts_row.addWidget(self.conn_prt_btn)
        shortcuts_row.addStretch(1)
        self.shortcuts_container.setVisible(False)

        # Its own row, not crammed into the row above - that row already
        # has several fixed-size buttons plus a QLineEdit with no minimum
        # width, so adding a wide banner widget directly into it stole
        # space from the QLineEdit and squeezed it down to a couple of
        # characters the moment the banner became visible.
        self.responder_warning_label = QLabel("⚠ Status Responder is open (mock QTRM, not real hardware)")
        self.responder_warning_label.setStyleSheet(
            "color: #1f2328; background-color: rgb(240, 200, 120);"
            "border-radius: 8px; padding: 4px 10px; font-weight: 600;"
        )
        self.responder_warning_label.setVisible(False)
        warning_row = QHBoxLayout()
        warning_row.addWidget(self.responder_warning_label)
        warning_row.addStretch(1)

        # No side column competing for height anymore (the 3 window
        # buttons live in the Tools menu now, see _build_menu_bar) - this
        # box's natural height now just matches its actual two rows of
        # content, no forced extra space.
        outer = QVBoxLayout(box)
        title_label = QLabel("CONNECTION")
        title_label.setStyleSheet(
            "color: #00adb5; font-size: 13pt; font-weight: 700; letter-spacing: 0.6px; background: transparent;"
        )
        outer.addWidget(title_label)
        outer.addLayout(row)
        outer.addWidget(self.shortcuts_container)
        outer.addLayout(warning_row)
        return box

    def _on_quick_send_toggle_clicked(self):
        showing = not self.shortcuts_container.isVisible()
        self.shortcuts_container.setVisible(showing)
        self.quick_send_toggle_btn.setText("Timing Generation ▴" if showing else "Timing Generation ▾")

    # -- connection handling ---------------------------------------------

    def _on_ping_clicked(self):
        host = self.qcc_ip_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "No IP", "Enter the QCC IP first.")
            return

        self.ping_btn.setEnabled(False)
        self.ping_btn.setStyleSheet(_PING_PENDING_STYLE)
        self.ping_result_label.setText("Pinging...")
        self._ping_pending_since = time.monotonic()

        self._ping_worker = PingWorker(host)
        self._ping_worker.result.connect(self._on_ping_result)
        self._ping_worker.start()

    def _on_ping_result(self, success: bool, latency_text: str):
        # Local/LAN pings can resolve faster than the pending grey state is
        # visible for - delay applying the result just enough that every
        # ping shows a real pending -> result transition, not an instant
        # jump that looks like nothing happened.
        elapsed_ms = (time.monotonic() - self._ping_pending_since) * 1000
        remaining_ms = max(0, _PING_MIN_PENDING_MS - elapsed_ms)
        QTimer.singleShot(int(remaining_ms), lambda: self._apply_ping_result(success, latency_text))

    def _apply_ping_result(self, success: bool, latency_text: str):
        self.ping_btn.setEnabled(True)
        self.ping_btn.setStyleSheet(_PING_SUCCESS_STYLE if success else _PING_FAILURE_STYLE)
        self.ping_result_label.setText(latency_text)

    def _disconnect(self, status_text: str = "Disconnected"):
        if self.worker is not None:
            # Disconnect the status signal first - worker.stop() blocks
            # until the thread actually exits, and its queued "Stopped"
            # status (delivered once the event loop next processes it)
            # would otherwise arrive after this method returns and silently
            # overwrite status_text below.
            self.worker.status.disconnect(self._on_connect_status)
            self.worker.stop()
            self.worker = None
        self.connect_btn.setText("Connect")
        self.connect_btn.setStyleSheet("")
        self.conn_status_label.setText(status_text)

    def _on_connection_field_changed(self, *_args):
        # Local Port/QCC IP/QCC Port all describe an existing connection -
        # changing any of them while connected means that connection no
        # longer reflects what's configured, so drop it rather than keep
        # sending/listening against stale settings.
        if self.worker is not None:
            self._disconnect("Disconnected (connection settings changed)")

        # Persist immediately (same "no explicit Save button" pattern as
        # RC Settings) so these three fields remember their last value
        # across restarts. Skipped while QCC IP is the temporary
        # "127.0.0.1" auto-fill for an open Status Responder - that's not
        # a real setting the user typed, and gets correctly re-persisted
        # once _on_responder_window_closed restores the real value and
        # fires this same handler again.
        if not self._qcc_ip_overridden_for_responder:
            connection_settings.local_port = self.local_port_edit.value()
            connection_settings.qcc_ip = self.qcc_ip_edit.text().strip()
            connection_settings.qcc_port = self.qcc_port_edit.value()
            connection_settings.save()

    def _on_qcc_port_changed(self, port: int):
        # The Status Responder has to listen on whatever port this GUI
        # actually sends to (QCC Port) - if it's currently open, keep its
        # Listen Port following this field live instead of silently going
        # stale and dropping every request the moment QCC Port changes.
        if self._responder_window is not None:
            self._responder_window.set_listen_port(port)

    def _on_connect_clicked(self):
        if self.worker is not None:
            self._disconnect()
            return

        local_port = self.local_port_edit.value()
        qcc_ip = self.qcc_ip_edit.text().strip()
        qcc_port = self.qcc_port_edit.value()

        self.worker = UdpWorker(local_port, qcc_ip, qcc_port)
        self.worker.frame_received.connect(self._on_frame_received)
        self.worker.frame_sent.connect(self._on_frame_sent)
        self.worker.error.connect(self._on_worker_error)
        self.worker.status.connect(self._on_connect_status)
        self.worker.start()

        self.connect_btn.setText("Disconnect")

    def _on_connect_status(self, msg: str):
        self.conn_status_label.setText(msg)
        if msg.startswith("Listening"):
            # Reusing the Ping button's success style - same green, and
            # (unlike the old flat "background-color: x;" form this
            # replaced) a real QPushButton{...} selector block, so it
            # keeps rounded corners and working hover/pressed feedback
            # instead of rendering as a flat, square, dead-looking button.
            self.connect_btn.setStyleSheet(_PING_SUCCESS_STYLE)

    def _on_worker_error(self, msg: str):
        # Only a bind failure at connect-time means the connection itself
        # failed to establish (color the button red + reset to "Connect",
        # since nothing actually got connected). Other errors (a dropped
        # malformed frame, a transient send failure) can happen on an
        # otherwise-healthy connection and shouldn't disconnect the UI state.
        if msg.startswith("Failed to bind"):
            self.connect_btn.setText("Connect")
            self.connect_btn.setStyleSheet(_PING_FAILURE_STYLE)
            self.worker = None
        QMessageBox.warning(self, "UDP Error", msg)

    def _on_frame_sent(self, raw: bytes):
        self._last_sent_frame = raw
        if self._tx_test_window is not None:
            self._tx_test_window.show_frame(raw)

    def _on_open_rx_test_clicked(self):
        # Same one-shared-instance pattern as the TX test window - this one
        # has no listener of its own either, it just displays whatever the
        # main window's own worker receives (see _on_frame_received), so
        # there's no port conflict risk.
        if self._rx_test_window is None:
            self._rx_test_window = RxTestWindow()
            # Window didn't exist yet for whatever was most recently
            # received before now - back-fill it so it doesn't start blank.
            if self._last_received_frame is not None:
                self._rx_test_window.show_frame(self._last_received_frame)
        self._rx_test_window.show()
        self._rx_test_window.raise_()
        self._rx_test_window.activateWindow()

    def _on_open_tx_test_clicked(self):
        # Same one-shared-instance pattern as the RX test window - this one
        # has no listener of its own, it just displays whatever the main
        # window's own worker sends (see _on_frame_sent), so there's no port
        # conflict risk, but reusing one instance still avoids window clutter.
        if self._tx_test_window is None:
            self._tx_test_window = TxTestWindow()
            # Window didn't exist yet for whatever was most recently sent
            # before now - back-fill it so it doesn't start blank.
            if self._last_sent_frame is not None:
                self._tx_test_window.show_frame(self._last_sent_frame)
        self._tx_test_window.show()
        self._tx_test_window.raise_()
        self._tx_test_window.activateWindow()

    def _on_open_responder_clicked(self):
        # Same one-shared-instance pattern as the RX/TX test windows.
        if self._responder_window is None:
            self._responder_window = StatusResponderWindow()
            self._responder_window.closed.connect(self._on_responder_window_closed)

        # Match whatever QCC Port this GUI is currently configured to send
        # to, so the responder is listening in the right place from the
        # moment it opens - not just whenever the field happens to change
        # afterward (see _on_qcc_port_changed).
        self._responder_window.set_listen_port(self.qcc_port_edit.value())

        # Listening by default as soon as the window is opened, rather
        # than making the user click "Start Responding" every time -
        # start_listening() is a no-op if it's already running (e.g.
        # re-showing an already-open window).
        self._responder_window.start_listening()

        # The responder only ever runs on this machine, reachable at
        # 127.0.0.1 - auto-fill that as QCC IP so it's obviously the right
        # target without the user having to know/type it, and remember
        # whatever was there before so it can be restored once the
        # responder is closed. Only capture on the first substitution (not
        # every re-click) so re-showing an already-open responder doesn't
        # overwrite the remembered IP with "127.0.0.1" itself.
        if not self._qcc_ip_overridden_for_responder:
            self._qcc_ip_before_responder = self.qcc_ip_edit.text()
            # Set True before setText, not after - setText fires
            # textChanged (-> _on_connection_field_changed) synchronously,
            # and that handler needs the flag already True to know not to
            # persist "127.0.0.1" as if it were a real, user-typed setting.
            self._qcc_ip_overridden_for_responder = True
            self.qcc_ip_edit.setText("127.0.0.1")

        self._responder_window.show()
        self._responder_window.raise_()
        self._responder_window.activateWindow()

        self.responder_warning_label.setVisible(True)

    def _on_responder_window_closed(self):
        self.responder_warning_label.setVisible(False)

        if self._qcc_ip_overridden_for_responder:
            # Flip False before setText, not after - the restored text is
            # the real value and should be persisted by
            # _on_connection_field_changed's textChanged handler, which
            # only does so once this flag reads False.
            self._qcc_ip_overridden_for_responder = False
            self.qcc_ip_edit.setText(self._qcc_ip_before_responder or "")

    # -- response timing / timeout ----------------------------------------

    def _begin_wait(self, timeout_callback):
        """Start a timeout watchdog; timeout_callback fires if nothing comes back within RESPONSE_TIMEOUT_MS."""
        if self._pending_timer is not None:
            self._pending_timer.stop()
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.timeout.connect(timeout_callback)
        self._pending_timer.start(RESPONSE_TIMEOUT_MS)

    def _end_wait(self):
        """
        Stop the timeout watchdog - that's all this does now. The actual
        response-time measurement lives in udp_worker.py, captured right
        at the real socket send/receive calls inside the worker thread, so
        it's immune to GUI processing time and Qt's cross-thread
        signal-dispatch latency - both of which polluted the old
        perf_counter()-in-the-main-thread approach this replaced.
        """
        if self._pending_timer is not None:
            self._pending_timer.stop()
            self._pending_timer = None

    def _send_frame(self, frame: bytes):
        """
        Every command send goes through here (instead of calling
        self.worker.send_frame directly) so the header panel's stale
        highlights from the PREVIOUS command are cleared right as the new
        one goes out - the fields that changed in response to THIS command
        will then re-highlight when show_frame() runs on the reply, and
        stay lit until the next send clears them again.
        """
        self.header_panel.clear_highlights()
        self.worker.send_frame(frame)

    def _on_dwell_timeout(self):
        if self._awaiting_kind != "dwell":
            return
        self._awaiting_kind = None
        self.dwell_tab.show_no_response()

    def _on_memory_write_timeout(self):
        if self._awaiting_kind != "memory_write":
            return
        self._awaiting_kind = None
        qtrm_index, self._memory_write_target = self._memory_write_target, None
        self.memory_tab.show_no_response(qtrm_index)

    def _on_memory_write_all_timeout(self):
        if self._awaiting_kind != "memory_write_all":
            return
        self._awaiting_kind = None
        self.memory_tab.show_all_no_response()

    def _on_link_test_timeout(self):
        if self._awaiting_kind != "link_test":
            return
        self._awaiting_kind = None
        self.link_test_tab.show_no_response()

    def _on_individual_link_test_timeout(self):
        if self._awaiting_kind != "individual_link_test":
            return
        self._awaiting_kind = None
        qtrm_index, self._individual_link_qtrm = self._individual_link_qtrm, None
        self.link_test_tab.show_individual_no_response(qtrm_index)

    def _on_rx_cal_timeout(self):
        if self._awaiting_kind != "rx_cal":
            return
        self._awaiting_kind = None
        self.rx_cal_tab.show_no_response()

    def _on_tx_cal_timeout(self):
        if self._awaiting_kind != "tx_cal":
            return
        self._awaiting_kind = None
        self.tx_cal_tab.show_no_response()

    def _on_isolation_all_timeout(self):
        if self._awaiting_kind != "isolation_all":
            return
        self._awaiting_kind = None
        self.isolation_tab.show_all_no_response()

    def _on_isolation_individual_timeout(self):
        if self._awaiting_kind != "isolation_individual":
            return
        self._awaiting_kind = None
        qtrm_index, self._individual_isolation_qtrm = self._individual_isolation_qtrm, None
        self.isolation_tab.show_individual_no_response(qtrm_index)

    def _on_status_all_timeout(self):
        if self._awaiting_kind != "status_all":
            return
        self._awaiting_kind = None
        self.status_tab.show_no_response()

    def _on_status_individual_timeout(self):
        if self._awaiting_kind != "status_individual":
            return
        self._awaiting_kind = None
        qtrm_index, self._individual_status_qtrm = self._individual_status_qtrm, None
        self.status_tab.show_individual_no_response(qtrm_index)

    def _on_timing_sob_timeout(self):
        if self._awaiting_kind != "timing_sob":
            return
        self._awaiting_kind = None
        self.timing_tab.show_sob_no_response()

    def _on_timing_prt_timeout(self):
        if self._awaiting_kind != "timing_prt":
            return
        self._awaiting_kind = None
        self.timing_tab.show_prt_no_response()

    def _on_timing_pps_timeout(self):
        if self._awaiting_kind != "timing_pps":
            return
        self._awaiting_kind = None
        self.timing_tab.show_pps_no_response()

    def _check_not_busy(self, silent: bool = False) -> bool:
        """
        Only one command may be in flight at a time (single request/response
        link). silent=True is for auto-resend ticks - skip quietly instead
        of popping a warning dialog every interval.
        """
        if self._awaiting_kind is not None:
            if not silent:
                QMessageBox.warning(
                    self, "Busy", "Still waiting for the previous command's response - try again shortly.",
                )
            return False
        return True

    # -- send / receive ---------------------------------------------------

    def _on_query_qcc_status(self):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        # QCC Status (Mode 3) is non-operational - QCC just returns its
        # current header with the latest sensor/counter values, body
        # zero-filled, no action taken - so it's just a header-only frame
        # with an empty message body, same shape as the timing commands.
        header = rc_settings.build_header(COMMAND_ID_QCC_STATUS)
        frame = build_header_only_frame(header)

        self._awaiting_kind = "qcc_status"
        self.header_panel.mark_query_pending()
        self._begin_wait(self._on_qcc_status_timeout)
        self._send_frame(frame)

    def _on_qcc_status_timeout(self):
        if self._awaiting_kind != "qcc_status":
            return
        self._awaiting_kind = None
        self.header_panel.mark_query_no_response()

    def _on_dwell_send(self):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_dwell_frame(self.dwell_tab.get_channels(), header=rc_settings.build_header(COMMAND_ID_DWELL))

        self._awaiting_kind = "dwell"
        self.dwell_tab.mark_pending()
        self._begin_wait(self._on_dwell_timeout)
        self._send_frame(frame)

    def _on_memory_write(self, data_type: int, qtrm_index: int, payload: bytes):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_memory_write_frame(
            data_type, payload, target_qtrm_index=qtrm_index,
            header=rc_settings.build_header(COMMAND_ID_MEMORY_OPERATION),
        )

        self._awaiting_kind = "memory_write"
        self._memory_write_target = qtrm_index
        self.memory_tab.mark_pending()
        self._begin_wait(self._on_memory_write_timeout)
        self._send_frame(frame)

    def _on_memory_write_all(self, data_type: int, payload: bytes):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_memory_write_frame(
            data_type, payload, target_qtrm_index=None,
            header=rc_settings.build_header(COMMAND_ID_MEMORY_OPERATION),
        )

        self._awaiting_kind = "memory_write_all"
        self.memory_tab.mark_all_pending()
        self._begin_wait(self._on_memory_write_all_timeout)
        self._send_frame(frame)

    def _on_link_test_clicked(self, is_auto_resend: bool = False):
        if self.worker is None:
            if not is_auto_resend:
                QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return

        if is_auto_resend:
            # Auto-resend fires on its own timer regardless of whether the
            # previous Link Test ever got a response or hit its 2-second
            # timeout. If the resend interval is shorter than that timeout,
            # this cancels the still-pending wait and sends again right away
            # instead of waiting the full timeout out. If something unrelated
            # (RX Cal, TX Cal, a manual command) is in flight, skip this tick
            # rather than stomping on it.
            if self._awaiting_kind not in (None, "link_test"):
                return
            self._awaiting_kind = None
            if self._pending_timer is not None:
                self._pending_timer.stop()
                self._pending_timer = None
        elif not self._check_not_busy():
            return

        frame = build_link_test_frame(header=rc_settings.build_header(COMMAND_ID_LINK_TEST))

        self._awaiting_kind = "link_test"
        self.link_test_tab.mark_pending()
        self._begin_wait(self._on_link_test_timeout)
        self._send_frame(frame)

    def _on_individual_link_test_clicked(self, qtrm_index: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_individual_link_frame(qtrm_index, header=rc_settings.build_header(COMMAND_ID_LINK_TEST))

        self._awaiting_kind = "individual_link_test"
        self._individual_link_qtrm = qtrm_index
        self.link_test_tab.mark_individual_pending(qtrm_index)
        self._begin_wait(self._on_individual_link_test_timeout)
        self._send_frame(frame)

    def _on_rx_cal_send(self, qtrm_index, channel, phase, atten, tx_isolation_for_others):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_cal_frame(
            False, qtrm_index, channel, phase, atten,
            tx_isolation_for_others=tx_isolation_for_others,
            header=rc_settings.build_header(COMMAND_ID_RX_CAL),
        )

        self._awaiting_kind = "rx_cal"
        self._rx_cal_target = qtrm_index
        self.rx_cal_tab.mark_pending()
        self._begin_wait(self._on_rx_cal_timeout)
        self._send_frame(frame)

    def _on_tx_cal_send(self, qtrm_index, channel, phase, atten, tx_isolation_for_others):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_cal_frame(
            True, qtrm_index, channel, phase, atten,
            tx_isolation_for_others=tx_isolation_for_others,
            header=rc_settings.build_header(COMMAND_ID_TX_CAL),
        )

        self._awaiting_kind = "tx_cal"
        self._tx_cal_target = qtrm_index
        self.tx_cal_tab.mark_pending()
        self._begin_wait(self._on_tx_cal_timeout)
        self._send_frame(frame)

    def _on_isolation_send_all(self, tx_isolation: bool):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_isolation_frame(
            tx_isolation, target_qtrm_index=None,
            header=rc_settings.build_header(COMMAND_ID_ISOLATION),
        )

        self._awaiting_kind = "isolation_all"
        self.isolation_tab.mark_all_pending()
        self._begin_wait(self._on_isolation_all_timeout)
        self._send_frame(frame)

    def _on_isolation_send_one(self, qtrm_index: int, tx_isolation: bool):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_isolation_frame(
            tx_isolation, target_qtrm_index=qtrm_index,
            header=rc_settings.build_header(COMMAND_ID_ISOLATION),
        )

        self._awaiting_kind = "isolation_individual"
        self._individual_isolation_qtrm = qtrm_index
        self.isolation_tab.mark_individual_pending(qtrm_index)
        self._begin_wait(self._on_isolation_individual_timeout)
        self._send_frame(frame)

    def _on_status_send_all(self, status_type: int, sub_status_type: int, beam_register_address: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_status_frame(
            status_type, target_qtrm_index=None,
            sub_status_type=sub_status_type, beam_register_address=beam_register_address,
            header=rc_settings.build_header(COMMAND_ID_STATUS),
        )

        self._awaiting_kind = "status_all"
        self._status_type_in_flight = status_type
        self._status_sub_type_in_flight = sub_status_type
        self.status_tab.mark_pending()
        self._begin_wait(self._on_status_all_timeout)
        self._send_frame(frame)

    def _on_status_send_one(self, qtrm_index: int, status_type: int, sub_status_type: int,
                             beam_register_address: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_status_frame(
            status_type, target_qtrm_index=qtrm_index,
            sub_status_type=sub_status_type, beam_register_address=beam_register_address,
            header=rc_settings.build_header(COMMAND_ID_STATUS),
        )

        self._awaiting_kind = "status_individual"
        self._individual_status_qtrm = qtrm_index
        self._status_type_in_flight = status_type
        self._status_sub_type_in_flight = sub_status_type
        self.status_tab.mark_individual_pending(qtrm_index)
        self._begin_wait(self._on_status_individual_timeout)
        self._send_frame(frame)

    def _on_reset_all_clicked(self):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        # Soft Reset gets no response - fire and forget, no timing/timeout tracking.
        frame = build_soft_reset_frame(target_qtrm_index=None, header=rc_settings.build_header(COMMAND_ID_SOFT_RESET))
        self._send_frame(frame)

    def _on_reset_one_clicked(self, qtrm_index):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        frame = build_soft_reset_frame(
            target_qtrm_index=qtrm_index, header=rc_settings.build_header(COMMAND_ID_SOFT_RESET),
        )
        self._send_frame(frame)

    def _on_timing_sob_send(self, external_loopback: bool, sob_width_us: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        command_id = QCCHeaderRx.MODE_EXTERNAL_LOOPBACK if external_loopback else QCCHeaderRx.MODE_INTERNAL_LOOPBACK
        header = rc_settings.build_header(command_id, message_body=build_sob_message_body(sob_width_us))
        frame = build_header_only_frame(header)

        self._awaiting_kind = "timing_sob"
        self.timing_tab.mark_sob_pending()
        self._begin_wait(self._on_timing_sob_timeout)
        self._send_frame(frame)

    def _on_timing_prt_send(self, external_loopback: bool, prt_count: int, pri_width_us: int, prt_width_us: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        command_id = QCCHeaderRx.MODE_EXTERNAL_LOOPBACK if external_loopback else QCCHeaderRx.MODE_INTERNAL_LOOPBACK
        message_body = build_prt_message_body(prt_count, pri_width_us, prt_width_us)
        header = rc_settings.build_header(command_id, message_body=message_body)
        frame = build_header_only_frame(header)

        self._awaiting_kind = "timing_prt"
        self.timing_tab.mark_prt_pending()
        self._begin_wait(self._on_timing_prt_timeout)
        self._send_frame(frame)

    def _on_timing_pps_send(self, pps_width_us: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        # PPS is External Loopback only, per the IDD - no loopback choice here.
        header = rc_settings.build_header(
            QCCHeaderRx.MODE_EXTERNAL_LOOPBACK, message_body=build_pps_message_body(pps_width_us),
        )
        frame = build_header_only_frame(header)

        self._awaiting_kind = "timing_pps"
        self.timing_tab.mark_pps_pending()
        self._begin_wait(self._on_timing_pps_timeout)
        self._send_frame(frame)

    def _on_frame_received(self, raw: bytes, elapsed_us: float):
        # elapsed_us comes straight from udp_worker.py, timestamped right
        # at the actual sendto()/recvfrom() calls inside the worker thread -
        # not measured here, so it excludes both GUI processing time (RX
        # Test Window/HeaderPanel updates below) AND the Qt cross-thread
        # signal-dispatch latency between the worker thread emitting
        # frame_received and this slot actually running on the main thread.
        # -1.0 means the worker had no prior send to time against (e.g. a
        # stray/unsolicited frame) - treat that the same as "unknown".
        if elapsed_us < 0:
            elapsed_us = None
        self._end_wait()  # stop the timeout watchdog only, see its docstring
        kind, self._awaiting_kind = self._awaiting_kind, None

        self._last_received_frame = raw
        if self._rx_test_window is not None:
            self._rx_test_window.show_frame(raw)

        # One global HeaderPanel now (not one per tab) - it always shows
        # whatever frame was most recently received, regardless of which
        # tab/command it came from.
        self.header_panel.show_frame(raw)

        if kind == "qcc_status":
            return

        if kind == "link_test":
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.link_test_tab.show_results(results)
            if elapsed_us is not None:
                self.link_test_tab.show_response_time(elapsed_us)
            return

        if kind == "dwell":
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.dwell_tab.show_results(results)
            if elapsed_us is not None:
                self.dwell_tab.show_response_time(elapsed_us)
            return

        if kind == "memory_write":
            qtrm_index, self._memory_write_target = self._memory_write_target, None
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.memory_tab.show_result(qtrm_index, results[qtrm_index])
            if elapsed_us is not None:
                self.memory_tab.show_response_time(elapsed_us)
            return

        if kind == "memory_write_all":
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.memory_tab.show_all_results(results)
            if elapsed_us is not None:
                self.memory_tab.show_response_time(elapsed_us)
            return

        if kind == "individual_link_test":
            qtrm_index, self._individual_link_qtrm = self._individual_link_qtrm, None
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.link_test_tab.show_individual_result(qtrm_index, results[qtrm_index])
            if elapsed_us is not None:
                self.link_test_tab.show_individual_response_time(elapsed_us)
            return

        if kind == "rx_cal":
            qtrm_index, self._rx_cal_target = self._rx_cal_target, None
            if elapsed_us is not None:
                self.rx_cal_tab.show_response_time(elapsed_us)
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.rx_cal_tab.show_result(results[qtrm_index])
            return

        if kind == "tx_cal":
            qtrm_index, self._tx_cal_target = self._tx_cal_target, None
            if elapsed_us is not None:
                self.tx_cal_tab.show_response_time(elapsed_us)
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.tx_cal_tab.show_result(results[qtrm_index])
            return

        if kind == "isolation_all":
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.isolation_tab.show_all_results(results)
            return

        if kind == "isolation_individual":
            qtrm_index, self._individual_isolation_qtrm = self._individual_isolation_qtrm, None
            try:
                results = parse_link_test_response(raw)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.isolation_tab.show_individual_result(qtrm_index, results[qtrm_index])
            return

        if kind == "status_all":
            status_type = self._status_type_in_flight
            diagnostic_type = self._status_sub_type_in_flight if status_type == STATUS_TYPE_DIAGNOSTIC else 0
            if elapsed_us is not None:
                self.status_tab.show_response_time(elapsed_us)
            try:
                results = parse_status_frame(raw, status_type, diagnostic_type)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.status_tab.show_results(results)
            return

        if kind == "status_individual":
            qtrm_index, self._individual_status_qtrm = self._individual_status_qtrm, None
            status_type = self._status_type_in_flight
            diagnostic_type = self._status_sub_type_in_flight if status_type == STATUS_TYPE_DIAGNOSTIC else 0
            if elapsed_us is not None:
                self.status_tab.show_individual_response_time(elapsed_us)
            try:
                results = parse_status_frame(raw, status_type, diagnostic_type)
            except AssertionError as e:
                QMessageBox.warning(self, "Parse error", str(e))
                return
            self.status_tab.show_individual_result(qtrm_index, results[qtrm_index])
            return

        if kind in ("timing_sob", "timing_prt", "timing_pps"):
            # These commands have no per-QTRM result to parse - the only
            # thing to check is that a response came back at all with a
            # valid header checksum (already shown in the header panel above).
            header = QCCHeaderTx.from_bytes(raw[0:FIXED_HEADER_SIZE + QCC_HEADER_SIZE])
            if kind == "timing_sob":
                if elapsed_us is not None:
                    self.timing_tab.show_sob_response_time(elapsed_us)
                self.timing_tab.show_sob_result(header.checksum_ok)
            elif kind == "timing_prt":
                if elapsed_us is not None:
                    self.timing_tab.show_prt_response_time(elapsed_us)
                self.timing_tab.show_prt_result(header.checksum_ok)
            else:
                if elapsed_us is not None:
                    self.timing_tab.show_pps_response_time(elapsed_us)
                self.timing_tab.show_pps_result(header.checksum_ok)
            return

        # kind is None here - a stray/unsolicited frame with nothing in
        # flight (e.g. arrived after its own timeout already fired). Nothing
        # to update.

    def closeEvent(self, event):
        if self.worker is not None:
            try:
                self.worker.stop()
            except KeyboardInterrupt:
                # worker.stop() blocks (QThread.wait()) for up to 2s - a
                # Ctrl+C landing in that window raises here rather than at
                # the top-level script, and PySide reports it as an error
                # in the closeEvent override instead of a clean exit.
                # Swallow it and let the window close normally anyway;
                # the app is already shutting down either way.
                pass
        super().closeEvent(event)
