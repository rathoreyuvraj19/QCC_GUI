"""Offscreen command driver for the QCC/QTRM Control GUI (PySide6).

Runs the real MainWindow with QT_QPA_PLATFORM=offscreen (no visible window,
no xvfb needed even on Linux) and executes a list of commands against it,
one per line, read from a script file or passed with --script.

Usage:
    python driver.py commands.txt
    python driver.py --script "click-tab Status; ss status; quit"

Commands (semicolon- or newline-separated):
    ss <name> [objectName]      screenshot -> shots/<name>.png (whole window,
                                 or just one widget if objectName given)
    click <objectName>          QTest.mouseClick on widget found by objectName
    click-tab <tab label>       switch the main QTabWidget to the tab whose
                                 text matches (substring, case-insensitive)
    settext <objectName> <text> setText() on a QLineEdit/SpinField and emit
                                 its signals (editingFinished)
    key <objectName> <keyseq>   send a key click, e.g. "Return", "Tab"
    wait <ms>                   process events / sleep
    eval <python-expr>          eval() with `win`, `app`, `find` in scope,
                                 result printed
    dump-tabs                   print all top-level tab labels
    dump-children <objectName>  print objectName/class of every descendant
    quit                        stop processing (implicit at EOF)

Every command prints one line: "OK <cmd> ..." or "ERROR <cmd> ...: <msg>".
Screenshots are saved under shots/ next to this file unless SHOT_DIR is set.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# NOTE: QT_QPA_PLATFORM=offscreen renders all text as tofu boxes on this
# Windows box (no fontconfig backend for the offscreen plugin) even though
# layout/widgets are otherwise correct. This is a real Windows machine with
# a real desktop session, so use the native "windows" platform instead --
# widget.grab() still works to capture pixels even if the window is
# minimized/hidden, so this stays fully scriptable/headless-friendly.

import argparse
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
APP_DIR = SKILL_DIR.parent.parent.parent  # .claude/skills/<name>/ -> repo root
sys.path.insert(0, str(APP_DIR))

SHOT_DIR = Path(os.environ.get("SHOT_DIR", SKILL_DIR / "shots"))
SHOT_DIR.mkdir(parents=True, exist_ok=True)

from PySide6.QtWidgets import QApplication, QWidget, QTabWidget
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from main_window import MainWindow
from theme import STYLESHEET

app = QApplication.instance() or QApplication(sys.argv)
app.setStyleSheet(STYLESHEET)
win = MainWindow()
win.resize(1400, 900)
win.show()
app.processEvents()


def find(object_name: str) -> QWidget:
    w = win.findChild(QWidget, object_name)
    if w is None:
        raise ValueError(f"no widget with objectName={object_name!r}")
    return w


def find_tab_widget() -> QTabWidget:
    return win.findChild(QTabWidget)


KEY_MAP = {
    "Return": Qt.Key_Return,
    "Enter": Qt.Key_Enter,
    "Tab": Qt.Key_Tab,
    "Escape": Qt.Key_Escape,
}


def cmd_ss(args):
    parts = args.split(maxsplit=1)
    name = parts[0]
    target = win
    if len(parts) > 1:
        target = find(parts[1])
    path = SHOT_DIR / f"{name}.png"
    app.processEvents()
    target.grab().save(str(path))
    return f"-> {path}"


def cmd_click(args):
    w = find(args.strip())
    QTest.mouseClick(w, Qt.LeftButton)
    app.processEvents()
    return f"clicked {args.strip()} ({type(w).__name__})"


def cmd_click_text(args):
    from PySide6.QtWidgets import QAbstractButton
    text = args.strip().lower()
    candidates = [b for b in win.findChildren(QAbstractButton) if b.isVisible()]
    match = next((b for b in candidates if b.text().strip().lower() == text), None) \
        or next((b for b in candidates if text in b.text().strip().lower()), None)
    if match is None:
        visible_texts = sorted({b.text().strip() for b in candidates if b.text().strip()})
        raise ValueError(f"no visible button matching {args!r}; have {visible_texts}")
    QTest.mouseClick(match, Qt.LeftButton)
    app.processEvents()
    return f"clicked button {match.text()!r} ({type(match).__name__})"


def cmd_click_tab(args):
    label = args.strip().lower()
    tabs = find_tab_widget()
    for i in range(tabs.count()):
        if label in tabs.tabText(i).lower():
            tabs.setCurrentIndex(i)
            app.processEvents()
            return f"switched to tab {i} ({tabs.tabText(i)!r})"
    raise ValueError(f"no tab matching {args!r}; have {[tabs.tabText(i) for i in range(tabs.count())]}")


def cmd_settext(args):
    object_name, text = args.split(maxsplit=1)
    w = find(object_name)
    w.setText(text)
    if hasattr(w, "editingFinished"):
        w.editingFinished.emit()
    app.processEvents()
    return f"set {object_name} = {text!r}"


def cmd_key(args):
    object_name, keyseq = args.split(maxsplit=1)
    w = find(object_name)
    key = KEY_MAP.get(keyseq, keyseq)
    QTest.keyClick(w, key)
    app.processEvents()
    return f"key {keyseq} -> {object_name}"


def cmd_wait(args):
    ms = int(args.strip())
    QTest.qWait(ms)
    return f"waited {ms}ms"


def cmd_eval(args):
    result = eval(args, {"win": win, "app": app, "find": find})
    app.processEvents()
    return repr(result)


def cmd_dump_tabs(args):
    tabs = find_tab_widget()
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    return str(labels)


def cmd_dump_children(args):
    root = find(args.strip()) if args.strip() else win
    lines = []
    for child in root.findChildren(QWidget):
        text = getattr(child, "text", None)
        text_val = text() if callable(text) else ""
        lines.append(f"{type(child).__name__} objectName={child.objectName()!r} text={text_val!r}")
    return "\n" + "\n".join(lines)


DISPATCH = {
    "ss": cmd_ss,
    "click": cmd_click,
    "click-text": cmd_click_text,
    "click-tab": cmd_click_tab,
    "settext": cmd_settext,
    "key": cmd_key,
    "wait": cmd_wait,
    "eval": cmd_eval,
    "dump-tabs": cmd_dump_tabs,
    "dump-children": cmd_dump_children,
}


def run_commands(lines):
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "quit":
            break
        cmd, _, args = line.partition(" ")
        fn = DISPATCH.get(cmd)
        if fn is None:
            print(f"ERROR {line}: unknown command {cmd!r}")
            continue
        try:
            result = fn(args)
            print(f"OK {line} {result}")
        except Exception as e:
            print(f"ERROR {line}: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("script_file", nargs="?")
    parser.add_argument("--script", help="semicolon-separated commands")
    ns = parser.parse_args()

    if ns.script:
        lines = ns.script.split(";")
    elif ns.script_file:
        lines = Path(ns.script_file).read_text().splitlines()
    else:
        lines = sys.stdin.read().splitlines()

    run_commands(lines)


if __name__ == "__main__":
    main()
