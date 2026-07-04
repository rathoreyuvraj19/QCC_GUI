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
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel,
    QGroupBox, QMessageBox, QTabWidget, QScrollArea, QInputDialog,
)

from packet import (
    build_link_test_frame, build_individual_link_frame, parse_link_test_response,
    build_cal_frame, build_soft_reset_frame, build_isolation_frame,
    build_status_frame, parse_status_frame, STATUS_TYPE_DIAGNOSTIC,
    build_dwell_frame, build_memory_write_frame,
)
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

# Plain local gate, not real security - just requires a deliberate action
# before this NVM-write-capable tab can be opened, per Yuvraj's explicit ask.
MEMORY_TAB_PASSWORD = "0145"
from spin_field import SpinField
from rx_test_app import RxTestWindow
from tx_test_window import TxTestWindow
from status_responder_app import StatusResponderWindow


RESPONSE_TIMEOUT_MS = 1000


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC / 96x QTRM Control")
        self.resize(1400, 700)

        self.worker: UdpWorker | None = None
        # None | "dwell" | "memory_write" | "memory_write_all" | "link_test" |
        # "individual_link_test" | "rx_cal" | "tx_cal" | "isolation_all" |
        # "isolation_individual" | "status_all" | "status_individual"
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
        self._send_time = None
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
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

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

    def _on_tab_changed(self, index):
        self.status_tab.reset_to_idle()

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
        box = QGroupBox("Connection")

        self.local_port_edit = SpinField(1, 65535, 5001, field_width=64)
        self.local_port_edit.spin.valueChanged.connect(self._on_connection_field_changed)

        self.qcc_ip_edit = QLineEdit("192.168.1.10")
        self.qcc_ip_edit.textChanged.connect(self._on_connection_field_changed)
        self.qcc_port_edit = SpinField(1, 65535, 5000, field_width=64)
        self.qcc_port_edit.spin.valueChanged.connect(self._on_connection_field_changed)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        self.conn_status_label = QLabel("Disconnected")

        self.ping_btn = QPushButton("Ping Test")
        self.ping_btn.clicked.connect(self._on_ping_clicked)
        self.ping_result_label = QLabel("")

        self.rx_test_btn = QPushButton("Open RX Test Window")
        self.rx_test_btn.clicked.connect(self._on_open_rx_test_clicked)

        self.tx_test_btn = QPushButton("Open TX Test Window")
        self.tx_test_btn.clicked.connect(self._on_open_tx_test_clicked)

        self.responder_btn = QPushButton("Open Status Responder")
        self.responder_btn.clicked.connect(self._on_open_responder_clicked)

        row = QHBoxLayout(box)
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
        row.addWidget(self.rx_test_btn)
        row.addWidget(self.tx_test_btn)
        row.addWidget(self.responder_btn)
        return box

    # -- connection handling ---------------------------------------------

    def _on_ping_clicked(self):
        host = self.qcc_ip_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "No IP", "Enter the QCC IP first.")
            return

        self.ping_btn.setEnabled(False)
        self.ping_btn.setStyleSheet("background-color: rgb(160, 165, 172);")
        self.ping_result_label.setText("Pinging...")

        self._ping_worker = PingWorker(host)
        self._ping_worker.result.connect(self._on_ping_result)
        self._ping_worker.start()

    def _on_ping_result(self, success: bool, latency_text: str):
        self.ping_btn.setEnabled(True)
        color = "rgb(146, 208, 165)" if success else "rgb(240, 149, 149)"
        self.ping_btn.setStyleSheet(f"background-color: {color};")
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
            self.connect_btn.setStyleSheet("background-color: rgb(146, 208, 165);")

    def _on_worker_error(self, msg: str):
        # Only a bind failure at connect-time means the connection itself
        # failed to establish (color the button red + reset to "Connect",
        # since nothing actually got connected). Other errors (a dropped
        # malformed frame, a transient send failure) can happen on an
        # otherwise-healthy connection and shouldn't disconnect the UI state.
        if msg.startswith("Failed to bind"):
            self.connect_btn.setText("Connect")
            self.connect_btn.setStyleSheet("background-color: rgb(240, 149, 149);")
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
        # Same one-shared-instance pattern - StatusResponderWindow has its
        # own "Start Responding" button, still off by default when this
        # window is (re)shown, so opening it doesn't silently start binding
        # a UDP port until the user actually asks it to.
        if self._responder_window is None:
            self._responder_window = StatusResponderWindow()
            self._responder_window.closed.connect(self._on_responder_window_closed)

        # The responder only ever runs on this machine, reachable at
        # 127.0.0.1 - auto-fill that as QCC IP so it's obviously the right
        # target without the user having to know/type it, and remember
        # whatever was there before so it can be restored once the
        # responder is closed. Only capture on the first substitution (not
        # every re-click) so re-showing an already-open responder doesn't
        # overwrite the remembered IP with "127.0.0.1" itself.
        if not self._qcc_ip_overridden_for_responder:
            self._qcc_ip_before_responder = self.qcc_ip_edit.text()
            self.qcc_ip_edit.setText("127.0.0.1")
            self._qcc_ip_overridden_for_responder = True

        self._responder_window.show()
        self._responder_window.raise_()
        self._responder_window.activateWindow()

    def _on_responder_window_closed(self):
        if self._qcc_ip_overridden_for_responder:
            self.qcc_ip_edit.setText(self._qcc_ip_before_responder or "")
            self._qcc_ip_overridden_for_responder = False

    # -- response timing / timeout ----------------------------------------

    def _begin_wait(self, timeout_callback):
        """Start timing a round trip; timeout_callback fires if nothing comes back."""
        self._send_time = time.perf_counter()
        if self._pending_timer is not None:
            self._pending_timer.stop()
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.timeout.connect(timeout_callback)
        self._pending_timer.start(RESPONSE_TIMEOUT_MS)

    def _end_wait(self):
        """Stop timing and return elapsed microseconds, or None if nothing was pending."""
        if self._send_time is None:
            return None
        elapsed_us = (time.perf_counter() - self._send_time) * 1_000_000
        self._send_time = None
        if self._pending_timer is not None:
            self._pending_timer.stop()
            self._pending_timer = None
        return elapsed_us

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

    def _on_dwell_send(self):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_dwell_frame(self.dwell_tab.get_channels())

        self._awaiting_kind = "dwell"
        self.dwell_tab.mark_pending()
        self._begin_wait(self._on_dwell_timeout)
        self.worker.send_frame(frame)

    def _on_memory_write(self, data_type: int, qtrm_index: int, payload: bytes):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_memory_write_frame(data_type, payload, target_qtrm_index=qtrm_index)

        self._awaiting_kind = "memory_write"
        self._memory_write_target = qtrm_index
        self.memory_tab.mark_pending()
        self._begin_wait(self._on_memory_write_timeout)
        self.worker.send_frame(frame)

    def _on_memory_write_all(self, data_type: int, payload: bytes):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_memory_write_frame(data_type, payload, target_qtrm_index=None)

        self._awaiting_kind = "memory_write_all"
        self.memory_tab.mark_all_pending()
        self._begin_wait(self._on_memory_write_all_timeout)
        self.worker.send_frame(frame)

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
            self._send_time = None
        elif not self._check_not_busy():
            return

        frame = build_link_test_frame()

        self._awaiting_kind = "link_test"
        self.link_test_tab.mark_pending()
        self._begin_wait(self._on_link_test_timeout)
        self.worker.send_frame(frame)

    def _on_individual_link_test_clicked(self, qtrm_index: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_individual_link_frame(qtrm_index)

        self._awaiting_kind = "individual_link_test"
        self._individual_link_qtrm = qtrm_index
        self.link_test_tab.mark_individual_pending(qtrm_index)
        self._begin_wait(self._on_individual_link_test_timeout)
        self.worker.send_frame(frame)

    def _on_rx_cal_send(self, qtrm_index, channel, phase, atten, tx_isolation_for_others):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_cal_frame(
            False, qtrm_index, channel, phase, atten,
            tx_isolation_for_others=tx_isolation_for_others,
        )

        self._awaiting_kind = "rx_cal"
        self._rx_cal_target = qtrm_index
        self.rx_cal_tab.mark_pending()
        self._begin_wait(self._on_rx_cal_timeout)
        self.worker.send_frame(frame)

    def _on_tx_cal_send(self, qtrm_index, channel, phase, atten, tx_isolation_for_others):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_cal_frame(
            True, qtrm_index, channel, phase, atten,
            tx_isolation_for_others=tx_isolation_for_others,
        )

        self._awaiting_kind = "tx_cal"
        self._tx_cal_target = qtrm_index
        self.tx_cal_tab.mark_pending()
        self._begin_wait(self._on_tx_cal_timeout)
        self.worker.send_frame(frame)

    def _on_isolation_send_all(self, tx_isolation: bool):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_isolation_frame(tx_isolation, target_qtrm_index=None)

        self._awaiting_kind = "isolation_all"
        self.isolation_tab.mark_all_pending()
        self._begin_wait(self._on_isolation_all_timeout)
        self.worker.send_frame(frame)

    def _on_isolation_send_one(self, qtrm_index: int, tx_isolation: bool):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_isolation_frame(tx_isolation, target_qtrm_index=qtrm_index)

        self._awaiting_kind = "isolation_individual"
        self._individual_isolation_qtrm = qtrm_index
        self.isolation_tab.mark_individual_pending(qtrm_index)
        self._begin_wait(self._on_isolation_individual_timeout)
        self.worker.send_frame(frame)

    def _on_status_send_all(self, status_type: int, sub_status_type: int, beam_register_address: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_status_frame(
            status_type, target_qtrm_index=None,
            sub_status_type=sub_status_type, beam_register_address=beam_register_address,
        )

        self._awaiting_kind = "status_all"
        self._status_type_in_flight = status_type
        self._status_sub_type_in_flight = sub_status_type
        self.status_tab.mark_pending()
        self._begin_wait(self._on_status_all_timeout)
        self.worker.send_frame(frame)

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
        )

        self._awaiting_kind = "status_individual"
        self._individual_status_qtrm = qtrm_index
        self._status_type_in_flight = status_type
        self._status_sub_type_in_flight = sub_status_type
        self.status_tab.mark_individual_pending(qtrm_index)
        self._begin_wait(self._on_status_individual_timeout)
        self.worker.send_frame(frame)

    def _on_reset_all_clicked(self):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        # Soft Reset gets no response - fire and forget, no timing/timeout tracking.
        frame = build_soft_reset_frame(target_qtrm_index=None)
        self.worker.send_frame(frame)

    def _on_reset_one_clicked(self, qtrm_index):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        frame = build_soft_reset_frame(target_qtrm_index=qtrm_index)
        self.worker.send_frame(frame)

    # Which tab's HeaderPanel gets fed the raw 90-byte header for each
    # _awaiting_kind. A kind with no entry (e.g. None, for a stray/unsolicited
    # frame with nothing in flight) has no tab to update, so is skipped.
    _HEADER_PANEL_TAB_BY_KIND = {
        "dwell": "dwell_tab", "memory_write": "memory_tab", "memory_write_all": "memory_tab",
        "link_test": "link_test_tab", "individual_link_test": "link_test_tab",
        "rx_cal": "rx_cal_tab", "tx_cal": "tx_cal_tab",
        "isolation_all": "isolation_tab", "isolation_individual": "isolation_tab",
        "status_all": "status_tab", "status_individual": "status_tab",
    }

    def _update_header_panel(self, kind, raw: bytes):
        tab_attr = self._HEADER_PANEL_TAB_BY_KIND.get(kind)
        if tab_attr is not None:
            getattr(self, tab_attr).header_panel.show_frame(raw)

    def _on_frame_received(self, raw: bytes):
        self._last_received_frame = raw
        if self._rx_test_window is not None:
            self._rx_test_window.show_frame(raw)

        elapsed_us = self._end_wait()
        kind, self._awaiting_kind = self._awaiting_kind, None
        self._update_header_panel(kind, raw)

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

        # kind is None here - a stray/unsolicited frame with nothing in
        # flight (e.g. arrived after its own timeout already fired). Nothing
        # to update.

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.stop()
        super().closeEvent(event)
