---
name: run-qcc-qtrm-control
description: Build, run, and drive the QCC/QTRM Control PySide6 desktop GUI. Use when asked to start the app, take a screenshot of its UI, click a button, switch tabs, or verify a GUI change actually works.
---

QCC/QTRM Control is a PySide6 desktop app (`main.py` / `main_window.py` at
repo root). Drive it with the command-script runner at
`.claude/skills/run-qcc-qtrm-control/driver.py` — it imports `MainWindow`
directly (no separate process to attach to), executes a list of commands
against the live widget tree, and saves real screenshots via `QWidget.grab()`.

All paths below are relative to the repo root (`E:/GUI`).

## Prerequisites

Already satisfied by the app's own `requirements.txt`:

```bash
pip install -r requirements.txt   # PySide6>=6.11, openpyxl>=3.1
```

## Run (agent path)

```bash
python .claude/skills/run-qcc-qtrm-control/driver.py --script "click-tab Status; ss status"
# or put commands one-per-line in a file and pass the path instead of --script
```

Screenshots land in `.claude/skills/run-qcc-qtrm-control/shots/<name>.png`
(override with `SHOT_DIR` env var). Each command prints one line, `OK ...`
or `ERROR ...: <message>`, so a batch of commands is self-checking output.

### Commands

| command | what it does |
|---|---|
| `ss <name> [objectName]` | screenshot whole window, or one widget if `objectName` given |
| `click <objectName>` | `QTest.mouseClick` on a widget found by Qt `objectName` |
| `click-text <text>` | click the visible button whose text matches (exact, then substring) — use this one; most widgets in this codebase have no `objectName` set |
| `click-tab <label>` | switch the main `QTabWidget` to the tab whose label contains `<label>` (case-insensitive) |
| `settext <objectName> <text>` | `setText()` on a line edit + emit `editingFinished` |
| `key <objectName> <keyseq>` | `QTest.keyClick`, e.g. `Return`, `Tab`, `Escape` |
| `wait <ms>` | `QTest.qWait` — needed after clicks that trigger timers/threads (e.g. Ping) |
| `eval <python-expr>` | `eval()` with `win`, `app`, `find` in scope, prints `repr(result)` |
| `dump-tabs` | list top-level tab labels |
| `dump-children [objectName]` | list every descendant widget's class/objectName/text (use with no arg to explore the whole window) |
| `quit` | stop processing the remaining commands |

Example — verify the Ping button issue from `CLAUDE.md`:

```bash
python .claude/skills/run-qcc-qtrm-control/driver.py --script \
  "click-tab Dwell; ss before-ping; click-text Ping Test; wait 300; ss during-ping; wait 1500; ss after-ping"
```

## Run (human path)

```bash
python main.py   # opens the real window
```

## Test

No automated test suite in this repo as of 2026-07-08 (no `tests/` dir,
no pytest config). `packet.py`-style modules are written to be unit-testable
standalone but nothing currently exercises them.

## Gotchas

- **`QT_QPA_PLATFORM=offscreen` renders all text as tofu boxes** on this
  Windows machine (layout and colors are correct, but every glyph shows as
  a box) — there's no fontconfig backend wired up for the offscreen plugin
  here. The driver deliberately does **not** set `QT_QPA_PLATFORM` and lets
  Qt use the native `windows` platform instead. `QWidget.grab()` still
  captures correct pixels even though a real window briefly appears, so
  this stays fully scriptable.
- **Most widgets have no Qt `objectName`** — they're only Python attributes
  on `MainWindow` (e.g. `self.ping_btn`), not `setObjectName()` calls. `find`
  (used by `click`/`settext`/`key`) will fail on these with `no widget with
  objectName=...`. Use `click-text` (matches visible button text) or
  `dump-children` to discover what's actually there before scripting a click.
- **`click-tab` matches substrings**, so `click-tab RC` will match "RC
  Settings" but also anything else containing "rc" — pass a more specific
  label if there's ambiguity.
- **`dump-children`/`dump-tabs` can crash with a `UnicodeEncodeError`**
  (`'charmap' codec can't encode character '▲'`) on a default Windows
  terminal — some buttons use `▲`/`▾` glyphs and the console defaults to
  cp1252. The driver reconfigures `stdout` to UTF-8 at startup to fix this;
  if you see this error anyway, your terminal is overriding it — pipe
  through `iconv`/redirect to a file instead of printing directly.
