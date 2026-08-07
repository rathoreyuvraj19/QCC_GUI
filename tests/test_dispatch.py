"""
tests/test_dispatch.py

Consistency checks on main_window.py's command dispatch table.

Only one command is ever in flight, keyed by a `kind` string that has to
agree in three places: the `_begin_wait("<kind>")` call that latches it, the
`_COMMANDS` entry that handles its reply and timeout, and the tab methods
that entry calls. A typo in any one of them fails silently at runtime - the
command just sends and then nothing ever updates - so it's checked here
instead.

These import main_window but never construct a QMainWindow, so no
QApplication (and no display) is needed.
"""

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main_window  # noqa: E402
from main_window import _COMMANDS, _Command, MainWindow  # noqa: E402

_MAIN_WINDOW_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main_window.py",
)

# Kinds that are latched without going through _begin_wait, because they
# aren't one-shot request/response commands.
_NOT_IN_TABLE = {
    "remote_programming",  # multi-frame session, owns its own timers
    "burn_test",  # fire-and-forget stream, owns its own worker/timing - see core/burn_test_worker.py
}


def _begin_wait_kinds():
    """Every literal passed to self._begin_wait(...) in main_window.py."""
    with open(_MAIN_WINDOW_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    kinds = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "_begin_wait" or not node.args:
            continue
        arg = node.args[0]
        # A non-literal here would mean this check can no longer see every
        # kind statically - fail loudly rather than quietly skipping it.
        assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), (
            f"_begin_wait called with a non-literal at line {node.lineno}; "
            "this test can only verify literal kinds"
        )
        kinds.append(arg.value)
    return kinds


class TestDispatchTable(unittest.TestCase):

    def test_every_sent_kind_has_a_table_entry(self):
        kinds = _begin_wait_kinds()
        self.assertGreater(len(kinds), 10, "found suspiciously few _begin_wait call sites")
        for kind in kinds:
            with self.subTest(kind=kind):
                self.assertIn(kind, _COMMANDS,
                              f"_begin_wait({kind!r}) has no _COMMANDS entry - its reply "
                              "and timeout would both be silently ignored")

    def test_every_table_entry_is_actually_sent(self):
        """A table entry nothing sends is dead weight - probably a stale kind."""
        kinds = set(_begin_wait_kinds())
        for kind in _COMMANDS:
            with self.subTest(kind=kind):
                self.assertIn(kind, kinds, f"_COMMANDS[{kind!r}] is never latched by _begin_wait")

    def test_awaiting_kind_literals_are_known(self):
        """
        Any other `self._awaiting_kind = "..."` assignment must be a kind this
        test knows about, so a new special-case can't slip in unnoticed.
        """
        with open(_MAIN_WINDOW_PY, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "_awaiting_kind":
                    with self.subTest(line=node.lineno):
                        self.assertIn(node.value.value, set(_COMMANDS) | _NOT_IN_TABLE)

    def test_entries_are_well_formed(self):
        for kind, spec in _COMMANDS.items():
            with self.subTest(kind=kind):
                self.assertIsInstance(spec, _Command)
                self.assertTrue(callable(spec.on_timeout), "on_timeout is required")
                # A parser without somewhere to put the result (or vice
                # versa) means the reply is parsed and dropped, or a result
                # callback is never reached.
                self.assertEqual(
                    spec.parser is None, spec.on_result is None,
                    "parser and on_result must be provided together",
                )
                if spec.target_attr is not None:
                    self.assertTrue(spec.target_attr.startswith("_"))

    def test_target_attributes_exist_on_the_window(self):
        """
        _take_target() does getattr/setattr by name - a typo'd or renamed
        attribute would only surface as an AttributeError mid-command.
        """
        declared = {spec.target_attr for spec in _COMMANDS.values() if spec.target_attr}
        self.assertTrue(declared, "expected some single-QTRM commands")
        initialized = _init_assigned_attrs()
        for attr in declared:
            with self.subTest(attr=attr):
                self.assertIn(attr, initialized,
                              f"{attr} is used as a target_attr but never set in __init__")

    def test_generic_handlers_exist(self):
        for name in ("_begin_wait", "_end_wait", "_take_target",
                     "_on_response_timeout", "_on_frame_received"):
            with self.subTest(method=name):
                self.assertTrue(callable(getattr(MainWindow, name, None)))

    def test_no_leftover_per_command_timeout_methods(self):
        """
        The 16 near-identical _on_<kind>_timeout methods this table replaced
        must stay gone - a re-added one would be dead code that looks live.
        """
        for kind in _COMMANDS:
            stale = f"_on_{kind}_timeout"
            with self.subTest(method=stale):
                self.assertFalse(hasattr(MainWindow, stale),
                                 f"{stale} is back; _on_response_timeout already handles {kind}")


def _init_assigned_attrs():
    """
    Names assigned to self.<name> in MainWindow.__init__.

    Scoped to the MainWindow class body on purpose - module-level helper
    classes (_Command) have an __init__ too, and a plain ast.walk() for the
    first one named "__init__" finds that instead.
    """
    with open(_MAIN_WINDOW_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    class_node = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow"), None,
    )
    assert class_node is not None, "MainWindow class not found in main_window.py"

    init = next(
        (n for n in class_node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"),
        None,
    )
    assert init is not None, "MainWindow.__init__ not found"

    attrs = set()
    for sub in ast.walk(init):
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Attribute):
                    attrs.add(target.attr)
        elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Attribute):
            attrs.add(sub.target.attr)
    return attrs


class TestParsersUseFrameError(unittest.TestCase):
    """
    The table's parsers must surface a malformed frame as FrameError, which
    is what _on_frame_received catches. Anything else propagates out of a Qt
    slot and is swallowed.
    """

    def test_each_parser_raises_frame_error_on_a_short_frame(self):
        from core.packet import FrameError

        class _Stub:
            _status_type_in_flight = 0x2
            _status_sub_type_in_flight = 0

        seen = {spec.parser for spec in _COMMANDS.values() if spec.parser}
        self.assertGreaterEqual(len(seen), 3, "expected link/status/header parsers")
        for parser in seen:
            with self.subTest(parser=parser.__name__):
                with self.assertRaises(FrameError):
                    parser(_Stub(), b"\x00" * 12)


class TestModuleImportIsHeadless(unittest.TestCase):
    """Importing main_window must not need a QApplication or a display."""

    def test_no_qapplication_created_on_import(self):
        from PySide6.QtWidgets import QApplication

        self.assertIsNone(QApplication.instance())
        self.assertTrue(hasattr(main_window, "MainWindow"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
