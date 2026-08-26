"""
tests/test_display_paths.py

Runs every _COMMANDS entry's real on_result / on_timeout callback against a
real mock-responder reply, in a real MainWindow, at several array shapes.

WHY THIS FILE EXISTS.

test_packet.py proves the bytes are right and the parsers decode them.
test_dispatch.py proves the `kind` strings agree between _begin_wait and
_COMMANDS. Neither one ever runs the callback that puts the result on
screen - and that code only executes when a response actually arrives, so
a plain NameError in it survives every other check in this repo, plus a
full GUI construction, plus a live send/receive loop.

That is not hypothetical: the QTRM -> LRU rename left
`f"{linked_count}/{NUM_LRU} LRUs linked"` in link_test_tab.show_results().
It imported fine, built fine, sent fine, and blew up the first time a Link
Test reply came back.

Two things this has to get right or it silently proves nothing:

  * _on_frame_received returns early when _update_frame_display() reports a
    throttle skip, so _last_display_update must be reset before each call.
    Without that, only the first kind dispatches and every later one
    reports a pass it never exercised.
  * on_result is therefore wrapped to count its own invocations, and a
    callback that never ran is a failure rather than a pass.

Needs a QApplication, but runs headless via the offscreen platform - no
display, nothing extra to install.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from core import lru_config  # noqa: E402
import core.packet as P  # noqa: E402
import main_window as MW  # noqa: E402
from apps.status_responder_app import build_mock_response_frame  # noqa: E402
from core.rc_settings import (  # noqa: E402
    COMMAND_ID_LINK_TEST, COMMAND_ID_STATUS, rc_settings,
)

REFERENCE = lru_config.LRUConfig(num_lru=96, channels_per_lru=4)

_app = None


def setUpModule():
    global _app
    _app = QApplication.instance() or QApplication([])


def _queries(n_lru, n_ch, target):
    """One query per command kind, shaped for the parser that kind uses."""
    link = rc_settings.build_header(COMMAND_ID_LINK_TEST)
    status = rc_settings.build_header(COMMAND_ID_STATUS)
    header_only = P.build_header_only_frame(link)
    return {
        "qcc_status": header_only,
        "qcc_reset": header_only,
        "dwell": P.build_dwell_frame(
            [[P.LRUChannel() for _ in range(n_ch)] for _ in range(n_lru)], header=link),
        "link_test": P.build_link_test_frame(header=link),
        "individual_link_test": P.build_individual_link_frame(target, header=link),
        "memory_write": P.build_memory_write_frame(
            1, b"\x01", target_lru_index=target, header=link),
        "memory_write_all": P.build_memory_write_frame(1, b"\x01", header=link),
        "rx_cal": P.build_cal_frame(False, target, 1, 0, 0, header=link),
        "tx_cal": P.build_cal_frame(True, target, 1, 0, 0, header=link),
        "isolation_all": P.build_isolation_frame(True, header=link),
        "isolation_individual": P.build_isolation_frame(
            True, target_lru_index=target, header=link),
        "status_all": P.build_status_frame(P.STATUS_TYPE_HEALTH, header=status),
        "status_individual": P.build_status_frame(
            P.STATUS_TYPE_HEALTH, target_lru_index=target, header=status),
        "timing_sob": header_only,
        "timing_prt": header_only,
        "timing_pps": header_only,
    }


class TestDisplayPaths(unittest.TestCase):

    SHAPES = [(96, 4), (48, 24), (8, 6)]

    def tearDown(self):
        lru_config.set_config(REFERENCE)

    def _dispatch(self, window, kind, spec, reply, target):
        """Run one kind's on_result, asserting it actually executed."""
        window._awaiting_kind = kind
        if spec.target_attr:
            setattr(window, spec.target_attr, target)
        # See the module docstring: without this the throttle makes
        # _on_frame_received return before on_result.
        window._last_display_update = 0.0
        if kind.startswith("status"):
            window._status_type_in_flight = P.STATUS_TYPE_HEALTH
            window._status_sub_type_in_flight = 0

        if spec.on_result is None:
            window._on_frame_received(reply, 1234.0)
            return

        calls = []
        original = spec.on_result
        spec.on_result = lambda w, r, t: (calls.append(1), original(w, r, t))[1]
        try:
            window._on_frame_received(reply, 1234.0)
        finally:
            spec.on_result = original
        self.assertTrue(
            calls,
            f"{kind}: on_result never ran - the dispatch returned early, so this "
            f"assertion would have passed without exercising anything")

    def test_every_command_displays_its_result(self):
        for n_lru, n_ch in self.SHAPES:
            lru_config.set_config(lru_config.LRUConfig(n_lru, n_ch))
            window = MW.MainWindow()
            target = n_lru - 1
            queries = _queries(n_lru, n_ch, target)
            try:
                for kind, spec in MW._COMMANDS.items():
                    if kind == "chip_id_read":
                        continue  # bare 10-byte reply, handled separately below
                    with self.subTest(num_lru=n_lru, channels=n_ch, kind=kind):
                        query = queries.get(kind)
                        self.assertIsNotNone(
                            query, f"no query defined for command kind {kind!r} - "
                                   f"add one when adding a command")
                        reply, _replied = build_mock_response_frame(query)
                        self._dispatch(window, kind, spec, reply, target)
            finally:
                window.close()
                window.deleteLater()

    def test_every_command_displays_its_timeout(self):
        for n_lru, n_ch in self.SHAPES:
            lru_config.set_config(lru_config.LRUConfig(n_lru, n_ch))
            window = MW.MainWindow()
            target = n_lru - 1
            try:
                for kind, spec in MW._COMMANDS.items():
                    with self.subTest(num_lru=n_lru, channels=n_ch, kind=kind):
                        if spec.target_attr:
                            setattr(window, spec.target_attr, target)
                        spec.on_timeout(window, target if spec.target_attr else None)
            finally:
                window.close()
                window.deleteLater()

    def test_chip_id_read_display_path(self):
        for n_lru, n_ch in self.SHAPES:
            with self.subTest(num_lru=n_lru, channels=n_ch):
                lru_config.set_config(lru_config.LRUConfig(n_lru, n_ch))
                window = MW.MainWindow()
                try:
                    window._awaiting_kind = "chip_id_read"
                    window._last_display_update = 0.0
                    window._on_frame_received(
                        P.build_chip_id_response(
                            P.QCCHeaderTx.QCC_COMMAND_CHIP_ID_READ, 0x0123456789ABCDEF),
                        900.0)
                finally:
                    window.close()
                    window.deleteLater()

    def test_every_command_kind_has_a_query(self):
        """A new _COMMANDS entry must come with a query here, or the tests above skip it."""
        queries = set(_queries(96, 4, 0))
        missing = set(MW._COMMANDS) - queries - {"chip_id_read"}
        self.assertFalse(
            missing, f"command kinds with no query in this file: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
