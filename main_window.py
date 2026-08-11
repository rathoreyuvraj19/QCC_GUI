"""
main_window.py

Top-level window: Connection bar (local port, QCC IP, QCC port,
Connect/Disconnect, Ping Test) plus the per-command tabs (Dwell, Link
Test, Status, RX/TX Cal, Isolation, Soft Reset, Memory Operation).

The first 90 bytes of every frame (fixed header + QCC header) are zero for
now - MSG_ID, MODE, and per-QTRM MSG_ID/Frequency ID are not implemented on
the QCC/QTRM side yet.
"""

import csv
import os
import socket
import sys
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel,
    QGroupBox, QMessageBox, QTabWidget, QFileDialog,
)

from core.packet import (
    build_link_test_frame, build_individual_link_frame, parse_link_test_response,
    build_cal_frame, build_soft_reset_frame, build_isolation_frame,
    build_status_frame, parse_status_frame, STATUS_TYPE_DIAGNOSTIC,
    build_dwell_frame, build_memory_write_frame,
    QCCHeaderRx, QCCHeaderTx, FIXED_HEADER_SIZE, QCC_HEADER_SIZE,
    build_sob_body, build_prt_body, build_pps_body,
    build_header_only_frame, CHIP_ID_RESPONSE_SIZE, FrameError,
)
from core.rc_settings import (
    rc_settings, COMMAND_ID_DWELL, COMMAND_ID_LINK_TEST, COMMAND_ID_STATUS,
    COMMAND_ID_RX_CAL, COMMAND_ID_TX_CAL, COMMAND_ID_ISOLATION,
    COMMAND_ID_SOFT_RESET, COMMAND_ID_MEMORY_OPERATION, COMMAND_ID_QCC_STATUS,
    COMMAND_ID_QCC_RESET, COMMAND_ID_CHIP_ID_READ,
)
from core.command_style import send_button_style
from connection_settings import connection_settings
from core.frame_logger import FrameLogger
from core.burn_test_logger import BurnTestLogger
from core.burn_test_worker import BurnTestWorker
from core.udp_worker import UdpWorker
from ping_worker import PingWorker
from widgets.header_panel import HeaderPanel
from widgets.password_keypad_dialog import PasswordKeypadDialog
from widgets.temp_conversion_dialog import TempConversionDialog
from tabs.link_test_tab import LinkTestTab
from tabs.status_tab import StatusTab
from tabs.cal_tab import CalTab
from tabs.isolation_tab import IsolationTab
from tabs.soft_reset_tab import SoftResetTab
from tabs.dwell_tab import DwellTab
from tabs.memory_tab import MemoryTab
from tabs.timing_tab import TimingTab
from tabs.remote_programming_tab import RemoteProgrammingTab
from tabs.burn_test_tab import BurnTestTab
from apps.remote_prog_controller import (
    RemoteProgController, OP_AUTHENTICATE, OP_LINK_CHECK, OP_LRU_INFO,
    OP_MODE_BACK, OP_MODE_STEP1, OP_MODE_STEP2, OP_PROGRAM,
    OP_QTRM_HIGH_SPEED, OP_UPLOAD, OP_VERIFY,
)
from tabs.rc_settings_tab import RCSettingsTab

# Plain local gate, not real security - just requires a deliberate action
# before this NVM-write-capable tab can be opened, per Yuvraj's explicit ask.
MEMORY_TAB_PASSWORD = "0145"
from widgets.spin_field import SpinField
from apps.rx_test_app import RxTestWindow
from apps.tx_test_window import TxTestWindow
from apps.status_responder_app import StatusResponderWindow
from apps.remote_prog_tester_app import RemoteProgTesterWindow


RESPONSE_TIMEOUT_MS = 1000

# Minimum gap between HeaderPanel/RX Test Window repaints on the RX path.
# At a fast auto-resend rate (Dwell tab goes down to 0.01s) each received
# frame otherwise triggers ~30-70 widget-mutation calls synchronously on
# the GUI thread; if that falls behind the worker thread's frame_received
# emission rate, Qt queues the backlog (each holding a 2970-byte frame) in
# its cross-thread event queue - unbounded RAM growth and an apparent
# hang after enough frames. 10Hz is plenty for a human to read.
DISPLAY_UPDATE_MIN_INTERVAL_S = 0.1

# Ping button colors - full QPushButton{...} selector-block form (not the
# flat "background-color: x;" property-only form) since QSS :hover/:pressed
# pseudo-states are only recognized inside a selector block - the flat form
# used previously meant the button never had any hover/pressed feedback at
# all, in any state. Every state gets its own (subtle) hover/pressed shade so
# the button always looks responsive, not just while idle.
#
# padding/border-radius deliberately match theme.py's global QPushButton
# rule (11px 24px / 16px) - Ping Test sits directly beside Connect in the
# same row and previously used a much smaller 6px/8px pair, so it rendered
# visibly undersized next to every other button (fix 2026-07-18). These
# constants are also reused for connect_btn's transient success/failure
# flash, so fixing them here fixes both.
_PING_IDLE_STYLE = (
    "QPushButton { background-color: #4a515a; color: #eeeeee; border: none;"
    "border-radius: 16px; padding: 11px 24px; }"
    "QPushButton:hover { background-color: #565f6a; }"
    "QPushButton:pressed { background-color: #3d434b; }"
)
_PING_PENDING_STYLE = (
    "QPushButton { background-color: rgb(160, 165, 172); color: #1f2328; border: none;"
    "border-radius: 16px; padding: 11px 24px; }"
    "QPushButton:hover { background-color: rgb(150, 155, 162); }"
    "QPushButton:pressed { background-color: rgb(140, 145, 152); }"
)
_PING_SUCCESS_STYLE = (
    "QPushButton { background-color: rgb(146, 208, 165); color: #1f2328; border: none;"
    "border-radius: 16px; padding: 11px 24px; }"
    "QPushButton:hover { background-color: rgb(130, 195, 150); }"
    "QPushButton:pressed { background-color: rgb(115, 180, 135); }"
)
_PING_FAILURE_STYLE = (
    "QPushButton { background-color: rgb(240, 149, 149); color: #1f2328; border: none;"
    "border-radius: 16px; padding: 11px 24px; }"
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

_PORT_SCAN_LIMIT = 200  # how many ports past `preferred` to try before giving up


def _find_available_udp_port(preferred: int) -> int:
    """
    Returns `preferred` if a UDP socket can actually bind it right now,
    otherwise the first free port found scanning upward (wrapping at 65535
    back to 1024, since some other process squatting on `preferred` - e.g.
    the NI Tagger Service on port 5000 - doesn't mean neighboring ports are
    taken too). Falls back to `preferred` itself if nothing in the scan
    range is free, so callers always get *a* port rather than an exception.
    """
    for offset in range(_PORT_SCAN_LIMIT + 1):
        candidate = preferred + offset
        if candidate > 65535:
            candidate = 1024 + (candidate - 65536)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(("0.0.0.0", candidate))
            return candidate
        except OSError:
            continue
        finally:
            probe.close()
    return preferred


# ---------------------------------------------------------------------------
# Command dispatch table
#
# Only one command is ever in flight (single request/response link), tracked
# by MainWindow._awaiting_kind. Each kind used to be spelled out three times -
# a near-identical _on_<kind>_timeout method, a branch of a ~200-line if-chain
# in _on_frame_received, and its own _<kind>_target instance attribute - so
# adding a command meant editing three places that had to stay in lockstep.
# One entry here now drives all three (see _begin_wait/_on_response_timeout/
# _on_frame_received).
#
# The callbacks take the MainWindow as `w` rather than being bound methods, so
# this stays a module-level constant that doesn't care when the tabs get
# built.
#
# Two kinds are deliberately NOT fully table-driven, because their RX side
# genuinely doesn't fit the shape:
#   - "chip_id_read"      - its response is a bare 10-byte frame, not the
#                           standard 90-byte header, so it can't go through
#                           show_frame()/the RX Test Window at all. Listed
#                           here for its timeout only; _on_frame_received
#                           special-cases the receive side.
#   - "remote_programming" - a multi-frame session with its own timers, owned
#                           by RemoteProgController. Not in this table;
#                           _on_frame_received routes to it before any
#                           dispatch happens.
# ---------------------------------------------------------------------------


def _parse_link(w, raw):
    """Per-QTRM Link-reply flags - the reply shape for most commands."""
    return parse_link_test_response(raw)


def _parse_status(w, raw):
    """Per-QTRM decoded status, keyed on whichever status type is in flight."""
    status_type = w._status_type_in_flight
    diagnostic_type = w._status_sub_type_in_flight if status_type == STATUS_TYPE_DIAGNOSTIC else 0
    return parse_status_frame(raw, status_type, diagnostic_type)


def _parse_header_checksum(w, raw):
    """
    The timing commands (SOB/PRT/PPS) have no per-QTRM result to decode - the
    only thing to check is that a reply came back with a valid header
    checksum.
    """
    return QCCHeaderTx.from_bytes(raw[0:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]).checksum_ok


class _Command:
    """
    One in-flight command kind.

    parser            (w, raw) -> parsed result, or None to skip parsing.
                      Raises FrameError if the frame doesn't match the shape.
    on_result         (w, parsed, target) -> None. Updates the owning tab.
    on_timeout        (w, target) -> None. Updates the owning tab.
    on_response_time  (w, microseconds) -> None, or None if the tab has no
                      response-time readout (Isolation doesn't).
    target_attr       Name of the MainWindow attribute holding the target QTRM
                      index for single-QTRM variants, popped back to None when
                      the reply lands or the wait times out. None for
                      all-QTRM commands.
    """

    __slots__ = ("parser", "on_result", "on_timeout", "on_response_time", "target_attr")

    def __init__(self, on_timeout, on_result=None, parser=None,
                 on_response_time=None, target_attr=None):
        self.on_timeout = on_timeout
        self.on_result = on_result
        self.parser = parser
        self.on_response_time = on_response_time
        self.target_attr = target_attr


_COMMANDS = {
    "qcc_status": _Command(
        on_timeout=lambda w, t: w.header_panel.mark_query_no_response(),
    ),
    "qcc_reset": _Command(
        on_timeout=lambda w, t: w.header_panel.mark_reset_no_response(),
    ),
    # Receive side is special-cased in _on_frame_received (10-byte response).
    "chip_id_read": _Command(
        on_timeout=lambda w, t: w.header_panel.mark_chip_id_no_response(),
    ),
    "dwell": _Command(
        parser=_parse_link,
        on_result=lambda w, r, t: w.dwell_tab.show_results(r),
        on_response_time=lambda w, us: w.dwell_tab.show_response_time(us),
        on_timeout=lambda w, t: w.dwell_tab.show_no_response(),
    ),
    "link_test": _Command(
        parser=_parse_link,
        on_result=lambda w, r, t: w.link_test_tab.show_results(r),
        on_response_time=lambda w, us: w.link_test_tab.show_response_time(us),
        on_timeout=lambda w, t: w.link_test_tab.show_no_response(),
    ),
    "individual_link_test": _Command(
        parser=_parse_link,
        target_attr="_individual_link_qtrm",
        on_result=lambda w, r, t: w.link_test_tab.show_individual_result(t, r[t]),
        on_response_time=lambda w, us: w.link_test_tab.show_individual_response_time(us),
        on_timeout=lambda w, t: w.link_test_tab.show_individual_no_response(t),
    ),
    "memory_write": _Command(
        parser=_parse_link,
        target_attr="_memory_write_target",
        on_result=lambda w, r, t: w.memory_tab.show_result(t, r[t]),
        on_response_time=lambda w, us: w.memory_tab.show_response_time(us),
        on_timeout=lambda w, t: w.memory_tab.show_no_response(t),
    ),
    "memory_write_all": _Command(
        parser=_parse_link,
        on_result=lambda w, r, t: w.memory_tab.show_all_results(r),
        on_response_time=lambda w, us: w.memory_tab.show_response_time(us),
        on_timeout=lambda w, t: w.memory_tab.show_all_no_response(),
    ),
    "rx_cal": _Command(
        parser=_parse_link,
        target_attr="_rx_cal_target",
        on_result=lambda w, r, t: w.rx_cal_tab.show_result(r[t]),
        on_response_time=lambda w, us: w.rx_cal_tab.show_response_time(us),
        on_timeout=lambda w, t: w.rx_cal_tab.show_no_response(),
    ),
    "tx_cal": _Command(
        parser=_parse_link,
        target_attr="_tx_cal_target",
        on_result=lambda w, r, t: w.tx_cal_tab.show_result(r[t]),
        on_response_time=lambda w, us: w.tx_cal_tab.show_response_time(us),
        on_timeout=lambda w, t: w.tx_cal_tab.show_no_response(),
    ),
    # Isolation is the one tab with no response-time readout - hence no
    # on_response_time here (it was never shown for these two kinds).
    "isolation_all": _Command(
        parser=_parse_link,
        on_result=lambda w, r, t: w.isolation_tab.show_all_results(r),
        on_timeout=lambda w, t: w.isolation_tab.show_all_no_response(),
    ),
    "isolation_individual": _Command(
        parser=_parse_link,
        target_attr="_individual_isolation_qtrm",
        on_result=lambda w, r, t: w.isolation_tab.show_individual_result(t, r[t]),
        on_timeout=lambda w, t: w.isolation_tab.show_individual_no_response(t),
    ),
    "status_all": _Command(
        parser=_parse_status,
        on_result=lambda w, r, t: w.status_tab.show_results(r),
        on_response_time=lambda w, us: w.status_tab.show_response_time(us),
        on_timeout=lambda w, t: w.status_tab.show_no_response(),
    ),
    "status_individual": _Command(
        parser=_parse_status,
        target_attr="_individual_status_qtrm",
        on_result=lambda w, r, t: w.status_tab.show_individual_result(t, r[t]),
        on_response_time=lambda w, us: w.status_tab.show_individual_response_time(us),
        on_timeout=lambda w, t: w.status_tab.show_individual_no_response(t),
    ),
    "timing_sob": _Command(
        parser=_parse_header_checksum,
        on_result=lambda w, ok, t: w.timing_tab.show_sob_result(ok),
        on_response_time=lambda w, us: w.timing_tab.show_sob_response_time(us),
        on_timeout=lambda w, t: w.timing_tab.show_sob_no_response(),
    ),
    "timing_prt": _Command(
        parser=_parse_header_checksum,
        on_result=lambda w, ok, t: w.timing_tab.show_prt_result(ok),
        on_response_time=lambda w, us: w.timing_tab.show_prt_response_time(us),
        on_timeout=lambda w, t: w.timing_tab.show_prt_no_response(),
    ),
    "timing_pps": _Command(
        parser=_parse_header_checksum,
        on_result=lambda w, ok, t: w.timing_tab.show_pps_result(ok),
        on_response_time=lambda w, us: w.timing_tab.show_pps_response_time(us),
        on_timeout=lambda w, t: w.timing_tab.show_pps_no_response(),
    ),
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC / 96x QTRM Control")
        # The Connection bar's row of widgets (Local Port/QCC IP/QCC Port/
        # Connect/Ping Test/Timing Generation toggle) has a combined layout
        # minimum around 1428px logical - wider than the 1400 requested
        # below on its own, and easily wider than a laptop screen's
        # available width once Windows DPI scaling (>100%) turns that into
        # physical pixels. Without an explicit minimum smaller than that,
        # Qt reports the layout minimum as the OS-level window minimum,
        # which can exceed the actual screen and trigger
        # "QWindowsWindow::setGeometry: Unable to set geometry" spam. Cap
        # it explicitly so the window can always be shrunk to fit.
        self.setMinimumSize(1000, 600)
        # Tall enough that the Header panel's ~26 decoded fields fit
        # without its internal scrollbar kicking in on a typical 1080p
        # screen (see header_panel.py's compacted spacing) - 700 was too
        # short and forced scrolling even after that compaction. Capped to
        # the actual available screen so it never requests more than fits.
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        # 1400 was too narrow once the connection row's right side (the
        # Timing Generation toggle + its Send SOB/Send PRT reveal) got
        # protected with real minimum widths (fix 2026-07-18) - below
        # ~1500 that content's true minimum overlapped the header panel's
        # fixed ~340px column. 1500 verified clear of the overlap.
        width = 1500 if avail is None else min(1500, avail.width() - 40)
        height = 900 if avail is None else min(900, avail.height() - 60)
        self.resize(width, height)

        self.worker: UdpWorker | None = None
        # Which command is in flight: None, any key of _COMMANDS, or
        # "remote_programming" (a multi-frame session that isn't in that
        # table - see _on_frame_received). Set via _begin_wait().
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
        self._last_display_update = 0.0
        self._last_sent_frame: bytes | None = None
        self._last_received_frame: bytes | None = None
        self._responder_window: StatusResponderWindow | None = None
        self._qcc_ip_before_responder: str | None = None
        self._qcc_ip_overridden_for_responder = False
        self._qcc_port_before_responder: int | None = None
        self._qcc_port_overridden_for_responder = False
        self._remote_prog_tester_window: RemoteProgTesterWindow | None = None
        self._rp_tester_ip_before: str | None = None
        self._rp_tester_ip_overridden = False
        self._rp_tester_port_before: int | None = None
        self._rp_tester_port_overridden = False

        # Burn-test data logger - streams every query/response pair to a
        # CSV chosen at start time (see _on_log_action_triggered). Lives on
        # the main window (not the worker) so it survives disconnect/
        # reconnect cycles during a long run.
        self._frame_logger = FrameLogger(self)
        self._frame_logger.stats_changed.connect(self._on_log_stats_changed)
        self._frame_logger.error.connect(self._on_logger_error)

        # Burn Test - fire-and-forget high-rate stream, deliberately
        # isolated from the worker/_frame_logger above (see
        # core/burn_test_worker.py's module docstring for why). The worker
        # itself is created/destroyed per run in _on_burn_test_start/_stop;
        # the logger, like _frame_logger, lives on the main window.
        self._burn_test_worker: BurnTestWorker | None = None
        self._burn_test_logger = BurnTestLogger(self)
        self._burn_test_logger.error.connect(
            lambda msg: QMessageBox.warning(self, "Burn Test logging", msg))

        # Open plot dialogs (Tools -> Plot Log File…) - kept in a list
        # rather than a single reused reference like the RX/TX/responder
        # windows above, since comparing two burn-test CSVs side by side
        # is a normal thing to want. Each entry drops itself on close.
        self._plot_log_dialogs = []

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

        # Remote Programming - firmware update over the bootloader link.
        # Unlike every other tab, its operations are multi-frame sessions
        # (30 s polls, chunk streaming), so a dedicated controller owns the
        # state machine and _on_frame_received routes frames to it wholesale
        # while a session is active instead of the one-shot kind dispatch.
        self.remote_programming_tab = RemoteProgrammingTab()
        self.remote_prog_ctrl = RemoteProgController(send_fn=self._send_frame, parent=self)
        self.remote_programming_tab.set_controller(self.remote_prog_ctrl)

        rp_tab, rp_ctrl = self.remote_programming_tab, self.remote_prog_ctrl
        rp_tab.mode_step1_requested.connect(self._on_rp_mode_step1)
        rp_tab.mode_step2_requested.connect(self._on_rp_mode_step2)
        rp_tab.link_check_requested.connect(self._on_rp_link_check)
        rp_tab.lru_info_requested.connect(self._on_rp_lru_info)
        rp_tab.qtrm_high_speed_requested.connect(self._on_rp_qtrm_high_speed)
        rp_tab.mode_back_requested.connect(self._on_rp_mode_back)
        rp_tab.authenticate_requested.connect(self._on_rp_authenticate)
        rp_tab.verify_requested.connect(self._on_rp_verify)
        rp_tab.upload_requested.connect(self._on_rp_upload)
        rp_tab.program_requested.connect(self._on_rp_program)
        rp_tab.cancel_requested.connect(rp_ctrl.cancel)
        rp_tab.iap_timeout_changed.connect(self._on_rp_iap_timeout_changed)
        rp_tab.target_qtrm_changed.connect(self._on_rp_target_qtrm_changed)

        rp_ctrl.step_result.connect(rp_tab.on_step_result)
        rp_ctrl.gate_changed.connect(rp_tab.on_gate_changed)
        rp_ctrl.lru_row_updated.connect(rp_tab.on_lru_row)
        rp_ctrl.op_row_updated.connect(rp_tab.on_op_row)
        rp_ctrl.op_window_closed.connect(rp_tab.on_op_window_closed)
        rp_ctrl.chunk_progress.connect(rp_tab.on_chunk_progress)
        rp_ctrl.ack_recorded.connect(rp_tab.on_ack_recorded)
        rp_ctrl.upload_finished.connect(rp_tab.on_upload_finished)
        rp_ctrl.session_finished.connect(rp_tab.on_session_finished)
        rp_ctrl.session_finished.connect(self._on_rp_session_finished)
        rp_ctrl.log_frame.connect(rp_tab.on_log_frame)

        self._remote_programming_tab_index = self.tabs.addTab(rp_tab, "Remote Programming")

        self.rc_settings_tab = RCSettingsTab()
        self.tabs.addTab(self.rc_settings_tab, "RC Settings")

        self.burn_test_tab = BurnTestTab()
        self.burn_test_tab.start_requested.connect(self._on_burn_test_start)
        self.burn_test_tab.stop_requested.connect(self._on_burn_test_stop_clicked)
        self.tabs.addTab(self.burn_test_tab, "Burn Test")

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

        # The single global HeaderPanel - its "Query QCC Status" and
        # "QCC Reset" buttons aren't tied to any one tab anymore, so they're
        # wired here just once.
        self.header_panel = HeaderPanel()
        self.header_panel.query_status_requested.connect(self._on_query_qcc_status)
        self.header_panel.qcc_reset_requested.connect(self._on_qcc_reset)
        self.header_panel.chip_id_requested.connect(self._on_read_chip_id)

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

        rp_tester_action = QAction("Open Remote Programming Tester", self)
        rp_tester_action.triggered.connect(self._on_open_rp_tester_clicked)
        tools_menu.addAction(rp_tester_action)

        tools_menu.addSeparator()

        # One action that toggles: pick a CSV location and start streaming
        # every query/response pair to it, or stop the run in progress.
        # The visible while-logging state lives in the connection bar's
        # indicator label (see _build_connection_group), not just here in
        # a closed menu.
        self._log_action = QAction("Start Data Logging (CSV)…", self)
        self._log_action.triggered.connect(self._on_log_action_triggered)
        tools_menu.addAction(self._log_action)

        plot_action = QAction("Plot Log File (CSV)…", self)
        plot_action.triggered.connect(self._on_plot_log_action_triggered)
        tools_menu.addAction(plot_action)

        tools_menu.addSeparator()

        temp_formula_action = QAction("Temperature Conversion Formula…", self)
        temp_formula_action.triggered.connect(self._on_temp_conversion_formula_triggered)
        tools_menu.addAction(temp_formula_action)

    def _on_tab_changed(self, index):
        self.status_tab.stop_auto_resend()
        self.link_test_tab.stop_auto_resend()
        self.dwell_tab.stop_auto_resend()
        self.status_tab.reset_to_idle()
        self.rx_cal_tab.reset_to_idle()
        self.tx_cal_tab.reset_to_idle()
        self.timing_tab.reset_to_idle()
        self.memory_tab.reset_to_idle()
        # Never mid-session - switching away and back during a Program pass
        # must not wipe the live ack matrix / progress state.
        if self._awaiting_kind != "remote_programming":
            self.remote_programming_tab.reset_to_idle()

        if index == self.tabs.indexOf(self.rc_settings_tab):
            self.rc_settings_tab.refresh_message_number()

        # Prompts every time this tab is entered (not just once per session)
        # - per Yuvraj's explicit ask, since it can write to real hardware's
        # flash memory. The Remote Programming tab shares the same gate: a
        # firmware update is at least as destructive as an NVM write.
        if index == self._memory_tab_index or (
            index == self._remote_programming_tab_index
            and self._awaiting_kind != "remote_programming"
        ):
            tab_name = ("Memory Operation" if index == self._memory_tab_index
                        else "Remote Programming")
            password, ok = PasswordKeypadDialog.get_password(
                self, f"{tab_name} - Locked",
                "This tab can write to real hardware's flash memory.\nEnter password:",
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

        # GUI Source Port - the port outgoing queries are sent FROM. Defaults
        # to (and, for anyone who never touches it, stays equal to) GUI
        # Listen Port, which keeps the original single-socket behavior.
        # Split out as its own field because some QCC firmware sends its
        # reply to a fixed destination port that doesn't necessarily match
        # whatever port a query happened to arrive from - letting the two
        # diverge here lets the GUI Listen Port be set to match that fixed
        # port independently of what source port queries go out on.
        self.source_port_edit = SpinField(1, 65535, connection_settings.source_port, field_width=64)
        self.source_port_edit.spin.valueChanged.connect(self._on_connection_field_changed)

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

        # Two stacked rows, not one - the fifth port/IP field (GUI Source
        # Port, added alongside the other four) no longer fits in a single
        # row at the app's normal window width without squeezing the
        # Connect/Disconnect/Ping/Timing Generation buttons down below
        # their own text (clipped labels like "onnect"/"ming Generation").
        fields_row = QHBoxLayout()
        fields_row.addWidget(QLabel("GUI Listen Port:"))
        fields_row.addWidget(self.local_port_edit)
        fields_row.addWidget(QLabel("GUI Source Port:"))
        fields_row.addWidget(self.source_port_edit)
        fields_row.addWidget(QLabel("QCC IP:"))
        fields_row.addWidget(self.qcc_ip_edit)
        fields_row.addWidget(QLabel("QCC Listen Port:"))
        fields_row.addWidget(self.qcc_port_edit)
        fields_row.addStretch(1)

        row = QHBoxLayout()
        row.addWidget(self.connect_btn)
        row.addWidget(self.conn_status_label)
        row.addWidget(self.ping_btn)
        row.addWidget(self.ping_result_label)
        row.addStretch(1)
        self.quick_send_toggle_btn = QPushButton("Timing Generation ▾")
        self.quick_send_toggle_btn.setStyleSheet(_QUICK_SEND_TOGGLE_STYLE)
        self.quick_send_toggle_btn.setToolTip("Show/hide the SOB/PRT quick-send shortcuts")
        self.quick_send_toggle_btn.clicked.connect(self._on_quick_send_toggle_clicked)
        # Lock in the widest label variant's width up front so the button
        # can never be compressed narrower than its text by the row's
        # layout squeeze (this row is already full: two spins, the IP
        # edit's 120px minimum, three buttons, two status labels) - without
        # this, Qt shrinks the button below its text's width and paints the
        # label center-clipped, e.g. "Timing Generation ▲" rendering as just
        # "ming Generation" (fix 2026-07-18). Measured via sizeHint() (not
        # a manual fontMetrics/padding estimate - that undershot the
        # style's real button margins and still left it 33px too narrow).
        _toggle_texts = ("Timing Generation ▾", "Timing Generation ▴")
        _toggle_widths = []
        for _t in _toggle_texts:
            self.quick_send_toggle_btn.setText(_t)
            _toggle_widths.append(self.quick_send_toggle_btn.sizeHint().width())
        self.quick_send_toggle_btn.setText(_toggle_texts[0])
        self.quick_send_toggle_btn.setMinimumWidth(max(_toggle_widths))

        # Quick-access shortcuts to Timing Generation's SOB/PRT sends,
        # available from every tab (not just Timing Generation itself) -
        # per Yuvraj's ask. These aren't a separate/duplicate action: they
        # just click the real buttons on the Timing Generation tab (wired
        # once that tab exists, see _build_ui), reusing its current field
        # values and pending/result indicator - not a second independent
        # copy with its own state.
        #
        # A plain show/hide row (not a QMenu - tried that, Yuvraj wanted
        # the original toggle back). No "Quick send:" label - just the two
        # buttons - the toggle button itself already says what this is.
        self.shortcuts_container = QWidget()
        shortcuts_row = QHBoxLayout(self.shortcuts_container)
        shortcuts_row.setContentsMargins(0, 0, 0, 0)
        self.conn_sob_btn = QPushButton("Send SOB")
        self.conn_sob_btn.setStyleSheet(send_button_style(radius=10, padding="8px 16px"))
        self.conn_sob_btn.setMinimumWidth(self.conn_sob_btn.sizeHint().width())
        shortcuts_row.addWidget(self.conn_sob_btn)

        self.conn_prt_btn = QPushButton("Send PRT")
        self.conn_prt_btn.setStyleSheet(send_button_style(radius=10, padding="8px 16px"))
        self.conn_prt_btn.setMinimumWidth(self.conn_prt_btn.sizeHint().width())
        shortcuts_row.addWidget(self.conn_prt_btn)
        self.shortcuts_container.setVisible(False)
        # The two buttons above refusing to shrink isn't enough on its own -
        # their WRAPPER widget's own size still comes from the outer row's
        # layout squeeze, not from its children's protected minimums, so it
        # was still being capped narrower than both buttons combined need
        # and the row's internal QHBoxLayout had to overlap them to fit.
        # Same fix, one level up.
        self.shortcuts_container.setMinimumWidth(self.shortcuts_container.sizeHint().width())

        # Toggle button and its shortcuts stacked as one column, both
        # horizontally centered within it - keeps the SOB/PRT pair visually
        # anchored to the toggle button that reveals them regardless of the
        # two rows' differing widths, instead of two independent
        # right-anchored rows that only coincidentally lined up on one edge.
        toggle_col = QVBoxLayout()
        toggle_col.setSpacing(6)
        toggle_col.addWidget(self.quick_send_toggle_btn, 0, Qt.AlignHCenter)
        toggle_col.addWidget(self.shortcuts_container, 0, Qt.AlignHCenter)
        row.addLayout(toggle_col)

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

        # Same pattern, for the Remote Programming tester window - it's a
        # separate mock (bootloader protocol, not the status-query path)
        # opened independently, so it gets its own banner rather than
        # reusing responder_warning_label's text.
        self.rp_tester_warning_label = QLabel("⚠ RP Tester is open (mock QTRM bootloader, not real hardware)")
        self.rp_tester_warning_label.setStyleSheet(
            "color: #1f2328; background-color: rgb(240, 200, 120);"
            "border-radius: 8px; padding: 4px 10px; font-weight: 600;"
        )
        self.rp_tester_warning_label.setVisible(False)

        # Always-visible-while-active indicator that a burn-test log is
        # being written (the Tools menu action that started it is hidden
        # inside a closed menu) - lives on the same banner row as the
        # responder warning, updated with live pair/missing counts by
        # _on_log_stats_changed.
        self.logging_indicator_label = QLabel("")
        self.logging_indicator_label.setStyleSheet(
            "color: #1f2328; background-color: rgb(240, 149, 149);"
            "border-radius: 8px; padding: 4px 10px; font-weight: 600;"
        )
        self.logging_indicator_label.setVisible(False)

        warning_row = QHBoxLayout()
        warning_row.addWidget(self.responder_warning_label)
        warning_row.addWidget(self.rp_tester_warning_label)
        warning_row.addWidget(self.logging_indicator_label)
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
        outer.addLayout(fields_row)
        outer.addLayout(row)
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
        # A Remote Programming session can't outlive the socket - kill its
        # timers/state cleanly (this also releases the _awaiting_kind busy-
        # lock via session_finished) before the worker goes away, so a
        # chunk watchdog never fires into a dead connection.
        self.remote_prog_ctrl.cancel()
        self._on_burn_test_stop()
        # A dead/absent worker can't carry any more resend ticks - stop
        # every tab's own auto-resend timer too, or their toggle buttons
        # stay latched "Resending"/"Stop" forever with nothing left to send
        # (or, before the "Stopped"-status handling above existed, kept
        # hammering a dead socket and popping a blocking error dialog on
        # every tick faster than the user could click Stop).
        self.header_panel.stop_auto_resend()
        self.link_test_tab.stop_auto_resend()
        self.status_tab.stop_auto_resend()
        self.dwell_tab.stop_auto_resend()
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
        # GUI Listen Port/GUI Source Port/QCC IP/QCC Listen Port all describe
        # an existing connection - changing any of them while connected
        # means that connection no longer reflects what's configured, so
        # drop it rather than keep sending/listening against stale settings.
        if self.worker is not None:
            self._disconnect("Disconnected (connection settings changed)")

        # Persist immediately (same "no explicit Save button" pattern as
        # RC Settings) so these fields remember their last value across
        # restarts. Skipped while QCC IP/Port is a temporary auto-fill for
        # an open Status Responder or Remote Programming Tester (both force
        # IP to 127.0.0.1 and may bump the port to find a free one) - that's
        # not a real setting the user typed, and gets correctly re-persisted
        # once the respective "_closed" handler restores the real value and
        # fires this same handler again.
        if not (self._qcc_ip_overridden_for_responder or self._qcc_port_overridden_for_responder
                or self._rp_tester_ip_overridden or self._rp_tester_port_overridden):
            connection_settings.local_port = self.local_port_edit.value()
            connection_settings.source_port = self.source_port_edit.value()
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
        source_port = self.source_port_edit.value()
        qcc_ip = self.qcc_ip_edit.text().strip()
        qcc_port = self.qcc_port_edit.value()

        self.worker = UdpWorker(local_port, qcc_ip, qcc_port, source_port=source_port)
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
        elif msg == "Stopped" and self.worker is not None:
            # This handler is disconnected from worker.status before an
            # explicit _disconnect() calls worker.stop() (see there), so a
            # "Stopped" that reaches here means the worker thread's run()
            # loop died on its own (e.g. a recv-side OSError) without the
            # user disconnecting. Left alone, self.worker stays non-None
            # forever with a dead/closed socket inside it, so every
            # send - including auto-resend ticks as fast as 0.1s - kept
            # hitting the dead socket and popping a blocking "UDP Error"
            # dialog faster than the user could reach the Stop button.
            self._disconnect("Disconnected (connection lost)")

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
        self._frame_logger.log_tx(raw)  # no-op unless a log run is active
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

        # The configured QCC Port may already be held by an unrelated
        # process (e.g. National Instruments' Tagger Service squats on the
        # 5000 default - WinError 10013 on bind, responder silently stays
        # "Stopped") - probe for a port that will actually bind before
        # pointing the responder at it, same remembered-value/restore-on-
        # close pattern as the QCC IP override below. Only capture on the
        # first substitution (not every re-click) so re-showing an already-
        # open responder doesn't overwrite the remembered port with itself.
        if not self._qcc_port_overridden_for_responder:
            self._qcc_port_before_responder = self.qcc_port_edit.value()
            self._qcc_port_overridden_for_responder = True
            available_port = _find_available_udp_port(self._qcc_port_before_responder)
            if available_port != self._qcc_port_before_responder:
                self.qcc_port_edit.setValue(available_port)

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

        # Auto-connect to the responder we just stood up, so the user
        # doesn't have to separately click Connect after opening it - if
        # already connected, leave that connection alone (it may be to a
        # different, deliberately-chosen target).
        if self.worker is None:
            self._on_connect_clicked()

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

        if self._qcc_port_overridden_for_responder:
            # Same flip-before-set ordering as the IP restore above.
            self._qcc_port_overridden_for_responder = False
            if self._qcc_port_before_responder is not None:
                self.qcc_port_edit.setValue(self._qcc_port_before_responder)

    def _on_open_rp_tester_clicked(self):
        # Same one-shared-instance pattern as the status responder - only
        # one active tester window at a time.
        if self._remote_prog_tester_window is None:
            self._remote_prog_tester_window = RemoteProgTesterWindow()
            self._remote_prog_tester_window.closed.connect(self._on_rp_tester_window_closed)

        # Same port-probing + auto-connect-on-open pattern as status responder.
        if not self._rp_tester_port_overridden:
            self._rp_tester_port_before = self.qcc_port_edit.value()
            self._rp_tester_port_overridden = True
            available_port = _find_available_udp_port(self._rp_tester_port_before)
            if available_port != self._rp_tester_port_before:
                self.qcc_port_edit.setValue(available_port)

        self._remote_prog_tester_window.set_listen_port(self.qcc_port_edit.value())
        self._remote_prog_tester_window.start_listening()

        if not self._rp_tester_ip_overridden:
            self._rp_tester_ip_before = self.qcc_ip_edit.text()
            self._rp_tester_ip_overridden = True
            self.qcc_ip_edit.setText("127.0.0.1")

        if self.worker is None:
            self._on_connect_clicked()

        self._remote_prog_tester_window.show()
        self._remote_prog_tester_window.raise_()
        self._remote_prog_tester_window.activateWindow()

        self.rp_tester_warning_label.setVisible(True)

    def _on_rp_tester_window_closed(self):
        self.rp_tester_warning_label.setVisible(False)

        if self._rp_tester_ip_overridden:
            self._rp_tester_ip_overridden = False
            self.qcc_ip_edit.setText(self._rp_tester_ip_before or "")

        if self._rp_tester_port_overridden:
            self._rp_tester_port_overridden = False
            if self._rp_tester_port_before is not None:
                self.qcc_port_edit.setValue(self._rp_tester_port_before)

    # -- burn-test data logging --------------------------------------------

    def _on_log_action_triggered(self):
        if self._frame_logger.active:
            self._frame_logger.stop()
            self._set_logging_ui_stopped()
            QMessageBox.information(
                self, "Data logging stopped",
                f"Log saved to:\n{self._frame_logger.path}",
            )
            return

        default_name = datetime.now().strftime("qcc_log_%Y%m%d_%H%M%S.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save data log as",
            os.path.join(os.path.expanduser("~"), default_name),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        err = self._frame_logger.start(path)
        if err:
            QMessageBox.warning(self, "Data logging", f"Could not start logging:\n{err}")
            return
        self._log_action.setText("Stop Data Logging")
        self._on_log_stats_changed(0, 0, 0, 0, 0)
        self.logging_indicator_label.setVisible(True)
        QMessageBox.information(
            self, "Data logging started",
            f"Logging to:\n{path}\n\n"
            "Note: per-QTRM OK/NOT_OK analysis is supported for Link Test "
            "only - run the burn test with Link Test frames. Other commands "
            "are still logged (timestamps, delay, result, raw hex) but "
            "their per-QTRM columns stay empty.",
        )

    def _set_logging_ui_stopped(self):
        self._log_action.setText("Start Data Logging (CSV)…")
        self.logging_indicator_label.setVisible(False)

    def _on_log_stats_changed(self, rows: int, ok: int, missing: int,
                              errors: int, qtrm_fails: int):
        name = os.path.basename(self._frame_logger.path or "")
        self.logging_indicator_label.setText(
            f"⏺ Logging to {name} - {rows} pairs | {ok} OK | {missing} missing"
            f" | {errors} errors | {qtrm_fails} QTRM fails"
        )

    def _on_logger_error(self, msg: str):
        # The logger already stopped itself (write failure mid-run) - just
        # reflect that in the UI and tell the user.
        self._set_logging_ui_stopped()
        QMessageBox.warning(self, "Data logging", msg)

    def _on_plot_log_action_triggered(self):
        start_dir = os.path.dirname(self._frame_logger.path) if self._frame_logger.path \
            else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open data log to plot", start_dir,
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        try:
            # Imported lazily - matplotlib is only needed if the user
            # actually opens a plot, not at GUI startup.
            from widgets.plot_log_dialog import PlotLogDialog
        except ImportError:
            QMessageBox.warning(
                self, "Plot log file",
                "matplotlib is required to plot log files:\n"
                "pip install matplotlib",
            )
            return
        try:
            dialog = PlotLogDialog(path, self)
        except (OSError, csv.Error, KeyError, ValueError) as e:
            QMessageBox.warning(self, "Plot log file",
                                f"Could not plot {path}:\n{e}")
            return
        dialog.finished.connect(lambda _=None, d=dialog: self._plot_log_dialogs.remove(d)
                                if d in self._plot_log_dialogs else None)
        self._plot_log_dialogs.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_temp_conversion_formula_triggered(self):
        # Same lock as Memory Operation/Remote Programming - misreading the
        # displayed temperature is as consequential as a bad NVM write if
        # this formula gets nudged by accident.
        password, ok = PasswordKeypadDialog.get_password(
            self, "Temperature Conversion Formula - Locked",
            "Changing this affects every temperature reading in the app.\nEnter password:",
        )
        if not (ok and password == MEMORY_TAB_PASSWORD):
            return
        TempConversionDialog(self).exec()

    # -- response timing / timeout ----------------------------------------

    def _begin_wait(self, kind: str):
        """
        Latch `kind` as the in-flight command and arm its timeout watchdog.

        _awaiting_kind is set here rather than separately at each call site
        (as it used to be) so the latched kind and the armed watchdog can
        never disagree - both come from this one argument.
        """
        self._awaiting_kind = kind
        if self._pending_timer is not None:
            # deleteLater(), not just stop()+drop the Python reference: this
            # QTimer is parented to `self` (QTimer(self) below), so Qt's C++
            # parent-child ownership keeps it alive as an orphaned child of
            # MainWindow forever otherwise - losing the Python reference
            # doesn't destroy it. Confirmed via findChildren(QTimer): 500
            # command sends without this leaked 500 QTimer objects (one per
            # _begin_wait call). At a 0.01s auto-resend interval that's ~100
            # leaked timers/sec, which is what was driving the reported GUI
            # RAM growth and eventual hang.
            self._pending_timer.stop()
            self._pending_timer.deleteLater()
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.timeout.connect(lambda: self._on_response_timeout(kind))
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
            self._pending_timer.deleteLater()
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

    def _take_target(self, spec: _Command):
        """
        Pop the in-flight target QTRM index for a single-QTRM command,
        clearing it so a later stray frame can't reuse a stale one. None for
        all-QTRM commands, which don't latch a target at all.
        """
        if spec.target_attr is None:
            return None
        target = getattr(self, spec.target_attr)
        setattr(self, spec.target_attr, None)
        return target

    def _on_response_timeout(self, kind: str):
        """
        Nothing came back within RESPONSE_TIMEOUT_MS - tell the owning tab.

        Guarded on `kind` still being the one in flight, so a watchdog left
        over from a command that already got its reply (or that an
        auto-resend tick superseded) does nothing.
        """
        if self._awaiting_kind != kind:
            return
        self._awaiting_kind = None
        spec = _COMMANDS[kind]
        spec.on_timeout(self, self._take_target(spec))

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

    def _on_query_qcc_status(self, is_auto_resend: bool = False):
        if self.worker is None:
            if not is_auto_resend:
                QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return

        if is_auto_resend:
            # Same reasoning as _on_link_test_clicked's auto-resend branch:
            # the header panel's own QTimer fires this regardless of
            # whether the previous query got a response or timed out - if
            # nothing else is in flight, cancel any still-pending wait for
            # our own last tick and send again now rather than waiting the
            # full RESPONSE_TIMEOUT_MS out; if something unrelated (Dwell,
            # RX Cal, ...) is in flight, skip this tick quietly.
            if self._awaiting_kind not in (None, "qcc_status"):
                return
            self._awaiting_kind = None
            if self._pending_timer is not None:
                self._pending_timer.stop()
                self._pending_timer.deleteLater()
                self._pending_timer = None
        elif not self._check_not_busy():
            return

        # QCC Status (Mode 3) is non-operational - QCC just returns its
        # current header with the latest sensor/counter values, body
        # zero-filled, no action taken - so it's just a header-only frame
        # with an empty message body, same shape as the timing commands.
        header = rc_settings.build_header(COMMAND_ID_QCC_STATUS)
        frame = build_header_only_frame(header)

        self.header_panel.mark_query_pending()
        self._begin_wait("qcc_status")
        self._send_frame(frame)

    def _on_qcc_reset(self):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        # QCC_RESET, like QCC Status, is header-only - no message body.
        header = rc_settings.build_header(COMMAND_ID_QCC_RESET)
        frame = build_header_only_frame(header)

        self.header_panel.mark_reset_pending()
        self._begin_wait("qcc_reset")
        self._send_frame(frame)

    def _on_read_chip_id(self):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        # CHIP_ID_READ's TX side is header-only, same shape as QCC Status/
        # QCC Reset - only its Response deviates from the standard header
        # (see _on_frame_received's "chip_id_read" branch below).
        header = rc_settings.build_header(COMMAND_ID_CHIP_ID_READ)
        frame = build_header_only_frame(header)

        self.header_panel.mark_chip_id_pending()
        self._begin_wait("chip_id_read")
        self._send_frame(frame)

    def _on_dwell_send(self, is_auto_resend: bool = False):
        if self.worker is None:
            if not is_auto_resend:
                QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return

        if is_auto_resend:
            # Same reasoning as _on_link_test_clicked's auto-resend branch:
            # if nothing else is in flight, cancel any still-pending wait for
            # our own last tick and send again now rather than waiting the
            # full timeout out; if something unrelated is in flight, skip
            # this tick quietly.
            if self._awaiting_kind not in (None, "dwell"):
                return
            self._awaiting_kind = None
            if self._pending_timer is not None:
                self._pending_timer.stop()
                self._pending_timer.deleteLater()
                self._pending_timer = None
        elif not self._check_not_busy():
            return

        frame = build_dwell_frame(self.dwell_tab.get_channels(), header=rc_settings.build_header(COMMAND_ID_DWELL))

        self.dwell_tab.mark_pending(reset_colors=not is_auto_resend)
        self._begin_wait("dwell")
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

        self._memory_write_target = qtrm_index
        self.memory_tab.mark_pending()
        self._begin_wait("memory_write")
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

        self.memory_tab.mark_all_pending()
        self._begin_wait("memory_write_all")
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
                self._pending_timer.deleteLater()
                self._pending_timer = None
        elif not self._check_not_busy():
            return

        frame = build_link_test_frame(header=rc_settings.build_header(COMMAND_ID_LINK_TEST))

        self.link_test_tab.mark_pending(reset_colors=not is_auto_resend)
        self._begin_wait("link_test")
        self._send_frame(frame)

    def _on_individual_link_test_clicked(self, qtrm_index: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        frame = build_individual_link_frame(qtrm_index, header=rc_settings.build_header(COMMAND_ID_LINK_TEST))

        self._individual_link_qtrm = qtrm_index
        self.link_test_tab.mark_individual_pending(qtrm_index)
        self._begin_wait("individual_link_test")
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

        self._rx_cal_target = qtrm_index
        self.rx_cal_tab.mark_pending()
        self._begin_wait("rx_cal")
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

        self._tx_cal_target = qtrm_index
        self.tx_cal_tab.mark_pending()
        self._begin_wait("tx_cal")
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

        self.isolation_tab.mark_all_pending()
        self._begin_wait("isolation_all")
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

        self._individual_isolation_qtrm = qtrm_index
        self.isolation_tab.mark_individual_pending(qtrm_index)
        self._begin_wait("isolation_individual")
        self._send_frame(frame)

    def _on_status_send_all(self, status_type: int, sub_status_type: int,
                            beam_register_address: int, is_auto_resend: bool = False):
        if self.worker is None:
            if not is_auto_resend:
                QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return

        if is_auto_resend:
            # Same reasoning as _on_link_test_clicked's auto-resend branch:
            # the Status tab's QTimer fires regardless of whether the
            # previous Send All ever got a response - if our own last send
            # is still unanswered, cancel its wait and send again now (no
            # modal "Busy" popup per tick on an unattended rig); if
            # something unrelated is in flight, skip this tick quietly.
            if self._awaiting_kind not in (None, "status_all"):
                return
            self._awaiting_kind = None
            if self._pending_timer is not None:
                self._pending_timer.stop()
                self._pending_timer.deleteLater()
                self._pending_timer = None
        elif not self._check_not_busy():
            return

        frame = build_status_frame(
            status_type, target_qtrm_index=None,
            sub_status_type=sub_status_type, beam_register_address=beam_register_address,
            header=rc_settings.build_header(COMMAND_ID_STATUS),
        )

        self._status_type_in_flight = status_type
        self._status_sub_type_in_flight = sub_status_type
        self.status_tab.mark_pending(reset_colors=not is_auto_resend)
        self._begin_wait("status_all")
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

        self._individual_status_qtrm = qtrm_index
        self._status_type_in_flight = status_type
        self._status_sub_type_in_flight = sub_status_type
        self.status_tab.mark_individual_pending(qtrm_index)
        self._begin_wait("status_individual")
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

        command_id = QCCHeaderRx.QCC_COMMAND_SOB_BYPASS if external_loopback else QCCHeaderRx.QCC_COMMAND_SOB_INTERNAL_GEN
        header = rc_settings.build_header(command_id, message_body=build_sob_body(sob_width_us))
        frame = build_header_only_frame(header)

        self.timing_tab.mark_sob_pending()
        self._begin_wait("timing_sob")
        self._send_frame(frame)

    def _on_timing_prt_send(self, external_loopback: bool, prt_count: int, pri_width_us: int, prt_width_us: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        command_id = QCCHeaderRx.QCC_COMMAND_PRT_BYPASS if external_loopback else QCCHeaderRx.QCC_COMMAND_PRT_INTERNAL_GEN
        message_body = build_prt_body(prt_count, pri_width_us, prt_width_us)
        header = rc_settings.build_header(command_id, message_body=message_body)
        frame = build_header_only_frame(header)

        self.timing_tab.mark_prt_pending()
        self._begin_wait("timing_prt")
        self._send_frame(frame)

    def _on_timing_pps_send(self, pps_width_us: int):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return

        # PPS_INTERNAL_GEN is the only PPS command in the redesigned IDD -
        # no Bypass counterpart exists (ASSUMPTION: the old spec's "PPS is
        # External Loopback only" doesn't carry over cleanly; see CLAUDE.md).
        header = rc_settings.build_header(
            QCCHeaderRx.QCC_COMMAND_PPS_INTERNAL_GEN, message_body=build_pps_body(pps_width_us),
        )
        frame = build_header_only_frame(header)

        self.timing_tab.mark_pps_pending()
        self._begin_wait("timing_pps")
        self._send_frame(frame)

    # -- Remote Programming session handlers ------------------------------
    # All thin: check connection/busy, set the session busy-lock, tell the
    # tab to show pending, delegate to the controller (which owns every
    # timer and sends via _send_frame). session_finished clears the lock.

    def _rp_start(self, op: str, start_fn, *args):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return
        self._awaiting_kind = "remote_programming"
        self.remote_programming_tab.mark_session_started(op)
        start_fn(*args)
        if not self.remote_prog_ctrl.busy:
            # The controller refused (its own gate/empty-image checks) - the
            # busy-lock must never stick without an active session behind it.
            self._awaiting_kind = None
            self.remote_programming_tab.on_session_finished(op, False, "Could not start")

    def _on_rp_session_finished(self, op: str, ok: bool, text: str):
        if self._awaiting_kind == "remote_programming":
            self._awaiting_kind = None

    def _on_rp_mode_step1(self):
        self._rp_start(OP_MODE_STEP1, self.remote_prog_ctrl.start_mode_step1)

    def _on_rp_mode_step2(self):
        self._rp_start(OP_MODE_STEP2, self.remote_prog_ctrl.start_mode_step2)

    def _on_rp_link_check(self):
        self._rp_start(OP_LINK_CHECK, self.remote_prog_ctrl.start_link_check)

    def _on_rp_lru_info(self):
        self._rp_start(OP_LRU_INFO, self.remote_prog_ctrl.start_lru_info)

    def _on_rp_qtrm_high_speed(self):
        self._rp_start(OP_QTRM_HIGH_SPEED, self.remote_prog_ctrl.start_qtrm_high_speed)

    def _on_rp_mode_back(self):
        self._rp_start(OP_MODE_BACK, self.remote_prog_ctrl.start_mode_back)

    def _on_rp_authenticate(self, image_is_golden: bool):
        self._rp_start(OP_AUTHENTICATE, self.remote_prog_ctrl.start_authenticate,
                       image_is_golden)

    def _on_rp_verify(self, image_is_golden: bool):
        self._rp_start(OP_VERIFY, self.remote_prog_ctrl.start_verify, image_is_golden)

    def _on_rp_upload(self, image: bytes, image_is_golden: bool):
        self._rp_start(OP_UPLOAD, self.remote_prog_ctrl.start_upload,
                       bytes(image), image_is_golden)

    def _on_rp_program(self, image_is_golden: bool):
        self._rp_start(OP_PROGRAM, self.remote_prog_ctrl.start_program, image_is_golden)

    def _on_rp_target_qtrm_changed(self, target: int):
        # 0-95 = single QTRM, RP_QTRM_SELECT_BROADCAST (0xFF) = all 96 -
        # drives Mode Step 1's slot filling and byte 35 (QTRM_SELECT) of
        # the Mode Step 2 / QCC -> High Speed frames.
        self.remote_prog_ctrl.target_qtrm = target

    def _on_rp_iap_timeout_changed(self, seconds: int):
        self.remote_prog_ctrl.iap_window_ms = seconds * 1000

    # -- Burn Test ----------------------------------------------------------
    # Fire-and-forget high-rate stream, deliberately kept off self.worker/
    # _COMMANDS/_begin_wait - see core/burn_test_worker.py's module
    # docstring. The only integration point with the rest of the app is
    # holding the _awaiting_kind busy-lock for the whole run, the same way
    # "remote_programming" does, so manual sends and RP sessions correctly
    # refuse to start while a burn test is active.

    def _on_burn_test_start(self, config: dict):
        if self.worker is None:
            QMessageBox.warning(self, "Not connected", "Connect to QCC first.")
            return
        if not self._check_not_busy():
            return
        reply = QMessageBox.question(
            self, "Start Burn Test",
            "This pauses the interactive connection and sends a sustained, "
            "fast stream of frames to the QCC until stopped - the "
            "interactive link reconnects automatically once you stop it. "
            "Continue?",
        )
        if reply != QMessageBox.Yes:
            return

        if config["log_path"]:
            err = self._burn_test_logger.start(config["log_path"])
            if err:
                QMessageBox.warning(self, "Burn Test logging", f"Could not start logging:\n{err}")
                return

        # A leftover auto-resend elsewhere would otherwise sit there
        # uselessly (its Send button refused by _check_not_busy above) for
        # the whole run - same calls _disconnect() already makes.
        self.header_panel.stop_auto_resend()
        self.link_test_tab.stop_auto_resend()
        self.status_tab.stop_auto_resend()
        self.dwell_tab.stop_auto_resend()

        # Real QCC hardware sends its responses to the port it's paired
        # with (this Local Port), not to whichever port a query actually
        # came from - see core/burn_test_worker.py's module docstring. So
        # Burn Test can't just open its own independent socket; the
        # interactive connection has to give up this exact port for the
        # run's duration. Deliberately NOT calling _disconnect() here - it
        # calls _on_burn_test_stop(), which would immediately close the
        # CSV logger just opened above, before the run even starts.
        local_port = self.local_port_edit.value()
        source_port = self.source_port_edit.value()
        qcc_ip = self.qcc_ip_edit.text().strip()
        qcc_port = self.qcc_port_edit.value()
        self.worker.status.disconnect(self._on_connect_status)
        self.worker.stop()
        self.worker = None
        self.connect_btn.setText("Connect")
        self.connect_btn.setStyleSheet("")
        self.connect_btn.setEnabled(False)
        self.conn_status_label.setText("Paused for Burn Test")

        self._burn_test_worker = BurnTestWorker(
            qcc_ip, qcc_port, config["payload"], config["interval_s"], local_port,
            source_port=source_port)
        self._burn_test_worker.error.connect(self._on_burn_test_error)
        self._burn_test_worker.stats_ready.connect(self.burn_test_tab.show_stats)
        self._burn_test_worker.rows_ready.connect(self._burn_test_logger.append_rows)
        self._burn_test_worker.link_result_ready.connect(self.burn_test_tab.show_link_results)
        # Same throttled-repaint path the interactive connection uses (both
        # tick at DISPLAY_UPDATE_MIN_INTERVAL_S/_STATS_INTERVAL_S = 100ms) -
        # keeps "LAST RECEIVED HEADER" live during a Burn Test run instead
        # of freezing at whatever it last showed before the run started.
        self._burn_test_worker.frame_received.connect(self._update_frame_display)
        self._burn_test_worker.status.connect(self._on_burn_test_worker_status)
        self._burn_test_worker.start()

        self._awaiting_kind = "burn_test"
        self.burn_test_tab.mark_running()

    def _on_burn_test_worker_status(self, msg: str):
        # The worker emits this exactly once, as the very last thing it
        # does before its thread exits (every code path in
        # BurnTestWorker.run() reaches it, including the bind-failure
        # early-return). Queued signals from the same sender thread are
        # delivered in emission order, so by the time this arrives, that
        # same final run()'s last rows_ready/stats_ready have already been
        # delivered too - closing the logger only here (instead of
        # synchronously right after worker.stop() returns, in
        # _on_burn_test_stop below) is what guarantees the last batch of
        # rows actually lands in the CSV instead of arriving into an
        # already-closed logger and being silently dropped.
        if msg == "Burn Test stopped":
            self._burn_test_logger.stop()

    def _on_burn_test_stop(self):
        """Generic cleanup - safe to call unconditionally (disconnect/close/
        bind-failure/manual stop) even when no burn test is running, same
        spirit as stop_auto_resend(). Deliberately does NOT reconnect the
        interactive link that was paused to run this - see
        _on_burn_test_stop_clicked, the one path where that's wanted;
        reconnecting from here too would fire even while the app is
        closing (closeEvent also calls this)."""
        self.connect_btn.setEnabled(True)
        if self._burn_test_worker is not None:
            self._burn_test_worker.stop()
            self._burn_test_worker = None
        else:
            # No worker ever ran (or it's already gone) - nothing pending
            # to flush, so nothing stops _on_burn_test_worker_status above
            # from ever firing to close this on its own.
            self._burn_test_logger.stop()
        if self._awaiting_kind == "burn_test":
            self._awaiting_kind = None
        self.burn_test_tab.reset_to_idle()

    def _on_burn_test_stop_clicked(self):
        """The tab's own Stop button - unlike the generic cleanup above,
        this is the one path where restoring the interactive connection
        that was paused to free its port for the run is actually wanted."""
        had_worker = self._burn_test_worker is not None
        self._on_burn_test_stop()
        if had_worker and self.worker is None:
            self._on_connect_clicked()

    def _on_burn_test_error(self, msg: str):
        # Only the fatal bind failure (thread never entered its loop) stops
        # the run. Transient per-frame send/receive errors during a live
        # run are already reflected in the tab's "Errors" counter
        # (BurnTestWorker.stats_ready) - popping a dialog for every one of
        # those would flood the UI during exactly the kind of sustained
        # stress test this feature exists for.
        if msg.startswith("Burn Test could not open a UDP socket"):
            QMessageBox.warning(self, "Burn Test", msg)
            # Also try to restore the interactive link paused to attempt
            # this run, same as a normal Stop-button click would.
            self._on_burn_test_stop_clicked()

    def _update_frame_display(self, raw: bytes) -> bool:
        """RX Test Window + HeaderPanel repaint, throttled to
        DISPLAY_UPDATE_MIN_INTERVAL_S - see that constant's comment. Always
        shows the most recently received frame; frames arriving faster than
        the throttle window are still logged (_frame_logger.log_rx runs
        unthrottled in _on_frame_received) but skip the widget repaint.
        Returns whether it actually repainted - _on_frame_received also
        gates the per-command result display (e.g. Link Test/Status/Dwell's
        96-cell LED matrix, each cell a setStyleSheet() call) on this same
        return value/cadence, since THAT turned out to be the dominant
        per-frame GUI cost during a fast resend, not the header panel."""
        # A stray/late CHIP_ID_READ response (bare 10-byte frame, see
        # CHIP_ID_RESPONSE_SIZE) can reach here if it arrives after
        # _awaiting_kind was already reset away from "chip_id_read" (e.g. a
        # slow reply landing after the request's own timeout already fired)
        # - the standard header parse below needs at least a 90-byte
        # header, so skip display for anything shorter rather than crashing.
        if len(raw) < FIXED_HEADER_SIZE + QCC_HEADER_SIZE:
            return False
        now = time.monotonic()
        if now - self._last_display_update < DISPLAY_UPDATE_MIN_INTERVAL_S:
            return False
        self._last_display_update = now
        if self._rx_test_window is not None:
            self._rx_test_window.show_frame(raw)
        self.header_panel.show_frame(raw)
        return True

    def _on_frame_received(self, raw: bytes, elapsed_us: float):
        # Fed before elapsed_us's below -1.0 -> None normalization - the
        # logger does its own "negative means unknown" handling and pairs
        # the frame with the in-flight query by MESSAGE_NUMBER.
        self._frame_logger.log_rx(raw, elapsed_us)  # no-op unless logging
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

        # Remote Programming sessions are multi-frame: keep the busy-lock
        # (_awaiting_kind) set and route EVERY frame to the controller until
        # its session_finished clears it - no one-shot dispatch, no _end_wait
        # (the controller runs its own QTimer windows/watchdogs; _begin_wait
        # was never armed for this kind).
        if self._awaiting_kind == "remote_programming":
            self._last_received_frame = raw
            self._update_frame_display(raw)
            if elapsed_us is not None:
                self.remote_programming_tab.show_response_time(elapsed_us)
            self.remote_prog_ctrl.on_frame(raw, elapsed_us)
            return

        self._end_wait()  # stop the timeout watchdog only, see its docstring
        kind, self._awaiting_kind = self._awaiting_kind, None

        self._last_received_frame = raw

        # CHIP_ID_READ's Response is a bare 10-byte frame, NOT the
        # standard 90-byte header - it can't go through show_frame() (or
        # the RX Test Window, which also expects the standard header/frame
        # shapes), so it's dispatched to its own display method and
        # returns early instead of falling through to the shared
        # show_frame() call below.
        if kind == "chip_id_read":
            if len(raw) != CHIP_ID_RESPONSE_SIZE:
                QMessageBox.warning(
                    self, "Parse error",
                    f"Expected a {CHIP_ID_RESPONSE_SIZE}-byte CHIP_ID_READ response, got "
                    f"{len(raw)} bytes - the other end may not support CHIP_ID_READ yet.",
                )
                self.header_panel.mark_chip_id_no_response()
                return
            self.header_panel.show_chip_id_response(raw)
            return

        # One global HeaderPanel now (not one per tab) - it always shows
        # whatever frame was most recently received, regardless of which
        # tab/command it came from. show_display also gates the
        # per-command result dispatch below (LED matrix repaint etc.) on
        # the same throttle cadence - see _update_frame_display's docstring.
        show_display = self._update_frame_display(raw)

        # kind is None for a stray/unsolicited frame with nothing in flight
        # (e.g. one that arrived after its own timeout already fired) -
        # nothing to update beyond the header panel above.
        spec = _COMMANDS.get(kind)
        if spec is None:
            return

        # Always taken regardless of show_display - just clears the
        # in-flight target attribute so a later stray frame can't reuse a
        # stale one, not a display update.
        target = self._take_target(spec)
        if not show_display:
            return

        # Response time first, so a round-trip that did come back still
        # reports its timing even if the payload then fails to parse.
        if elapsed_us is not None and spec.on_response_time is not None:
            spec.on_response_time(self, elapsed_us)

        if spec.parser is None:  # qcc_status/qcc_reset - header panel is the whole result
            return

        try:
            parsed = spec.parser(self, raw)
        except FrameError as e:
            QMessageBox.warning(self, "Parse error", str(e))
            return
        spec.on_result(self, parsed, target)

    def closeEvent(self, event):
        # connection_settings is only ever mutated by _on_connection_field_changed,
        # which itself skips writes while a Status Responder/RP Tester override is
        # active - so the object here already holds the last real (non-overridden)
        # Local Port/QCC IP/QCC Port the user set. Re-save explicitly on exit so a
        # crash/kill between edits can't leave the on-disk file stale.
        connection_settings.save()

        # Flushes any in-flight query row and closes the CSV cleanly - rows
        # are already on disk (flushed per-row), this just finalizes.
        self._frame_logger.stop()
        self._on_burn_test_stop()
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

        # Status Responder / RP Tester are independent top-level windows -
        # Qt doesn't close them just because this one does, and each owns a
        # QThread with its own bound UDP socket. .close() runs their own
        # closeEvent (stop_listening()), so the sockets/threads are torn
        # down cleanly instead of being killed mid-flight when the process
        # exits.
        if self._responder_window is not None:
            self._responder_window.close()
        if self._remote_prog_tester_window is not None:
            self._remote_prog_tester_window.close()

        # PingWorker finishes on its own in ~1-3s (single ping.exe subprocess,
        # no event loop) - wait() briefly rather than leaving a QThread
        # running past this object's destruction.
        if self._ping_worker is not None and self._ping_worker.isRunning():
            self._ping_worker.wait(3500)

        super().closeEvent(event)
