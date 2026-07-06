# QCC / 96x QTRM Control GUI

PySide6 desktop app for building, sending, and receiving the 2970-byte QCC
UDP frame (90-byte header + 96x30-byte QTRM data block), per
`QCC_90Byte_Header_BitTable.docx` and the QTRM Message Format IDD. See
[README.md](README.md) for setup/run instructions and a file-by-file
overview.

## Open issues (flagged 2026-07-06, not yet started)

1. **Ping button stuck / no hover-click feedback** - after one ping it stays
   green; re-clicking should flash grey briefly then resolve, but it updates
   too fast to see, and the button has no hover/pressed effect at all.
   Likely in `main_window.py`'s `_on_ping_clicked`/`_on_ping_result` and/or
   `ping_worker.py` - probably needs the full `QPushButton { ... }`
   selector-block QSS pattern (other buttons in this app hit this same issue
   before) plus a deliberate minimum "pending" display duration.

2. **Status Responder should respond on whatever port the main GUI is
   listening on**, not a fixed port of its own - if the user changes the
   main GUI's Local Port (Connection bar), the responder currently can't be
   told to match it, so the main GUI stops receiving responses. Needs
   `status_responder_app.py`'s send-target port to derive from
   `main_window.py`'s `local_port_edit` value rather than being
   independently configured.

3. **"PPS width not in GUI"** - flagged but needs verification before
   assuming it's a bug: per `QCC_90Byte_Header_BitTable.docx`, the response
   header only defines `INPUT_PPS_WIDTH_US` (already shown in
   `header_panel.py`) plus a separate `PPS_COUNTER` - there's no
   `OUTPUT_PPS_WIDTH_US` field in the spec. Confirm with Yuvraj whether he
   means something else (e.g. a different tab/panel) before changing
   anything.
