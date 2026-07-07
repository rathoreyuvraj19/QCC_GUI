"""
command_style.py

Single source of truth for the send-button and status-indicator colors/QSS
shared across every command tab (Dwell, Link Test, Status, RX/TX Cal,
Isolation, Soft Reset, Memory Operation, Timing Generation). Previously each
tab file defined its own identical (or near-identical, with drifted
padding/radius/naming) copies of these constants - a palette change meant
editing 7+ files and hoping none were missed.

Two independent concerns, kept separate on purpose:
  - The primary "Send" button's own color - purple (SEND_*) for every
    normal command, red (WRITE_*) for Memory Operation's destructive NVM
    write, deliberately distinct so a flash-write action never looks like
    a routine send. This button's color never changes once set - all
    per-command feedback (pending/success/failure) goes on a separate
    indicator instead (see indicator_style), not the button itself, so
    hover/pressed feedback never breaks and the button never gets stuck
    looking like a stale result.
  - The pending/success/failure indicator pill/matrix-button palette -
    identical grey/green/red everywhere a per-command or per-QTRM result
    is shown.
"""

TEXT_ON_ACCENT = "#eeeeee"
STATE_TEXT_COLOR = "#1f2328"  # dark text readable on the light pill/matrix colors below

SEND_COLOR = "#7C3AED"
SEND_HOVER_COLOR = "#6D28D9"
SEND_PRESSED_COLOR = "#5B21B6"

# Memory Operation's NVM write is destructive - kept visually distinct
# (red, not purple) so it never reads as "just another send".
WRITE_COLOR = "#d64545"
WRITE_HOVER_COLOR = "#e15b5b"
WRITE_PRESSED_COLOR = "#b83a3a"

PENDING_RGB = (160, 165, 172)
SUCCESS_RGB = (146, 208, 165)
FAILURE_RGB = (240, 149, 149)

# Per-QTRM matrix button idle look (Isolation/Soft Reset's 96-button grids,
# Link Test's LED matrix outline) - a light neutral grey distinct from the
# darker pending grey above.
IDLE_MATRIX_RGB = (222, 224, 227)
IDLE_MATRIX_HOVER_RGB = (200, 203, 208)
IDLE_MATRIX_PRESSED_RGB = (180, 184, 190)


def rgb_css(rgb) -> str:
    return f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"


PENDING_COLOR = rgb_css(PENDING_RGB)
SUCCESS_COLOR = rgb_css(SUCCESS_RGB)
FAILURE_COLOR = rgb_css(FAILURE_RGB)


def send_button_style(color: str = SEND_COLOR, hover: str = SEND_HOVER_COLOR,
                       pressed: str = SEND_PRESSED_COLOR, radius: int = 16,
                       padding: str = "11px 24px", font_size_px: int = None,
                       font_weight: int = 600, text_color: str = TEXT_ON_ACCENT) -> str:
    """
    The button's own fixed style - deliberately has no bg_color-swapping
    variant (unlike indicator_style/matrix_button_style below). Send
    buttons should never recolor to reflect a result; that's what the
    separate status indicator/matrix is for.
    """
    font_size_css = f"font-size: {font_size_px}px;" if font_size_px else ""
    return (
        f"QPushButton {{ background-color: {color}; color: {text_color}; border: none;"
        f"border-radius: {radius}px; padding: {padding}; font-weight: {font_weight}; {font_size_css} }}"
        f"QPushButton:hover {{ background-color: {hover}; }}"
        f"QPushButton:pressed {{ background-color: {pressed}; }}"
    )


def indicator_style(bg_color: str = None, radius: int = 14, border_color: str = "#4a515a") -> str:
    """
    A single-command status pill: quiet outlined/transparent when idle
    (bg_color=None, nothing sent yet or tab just switched to), solid flat
    color for pending/success/failure - flat on purpose, no hover/pressed
    distinction, since it's a status snapshot rather than a clickable
    control.
    """
    if bg_color is None:
        return (
            "QLabel { background: transparent; color: rgba(238, 238, 238, 0.45);"
            f"border: 1px solid {border_color}; border-radius: {radius}px;"
            "font-size: 12px; font-weight: 600; padding: 6px; }"
        )
    return (
        f"QLabel {{ background-color: {bg_color}; color: {STATE_TEXT_COLOR}; border: none;"
        f"border-radius: {radius}px; font-size: 12px; font-weight: 600; padding: 6px; }}"
    )


def matrix_button_style(bg_color: str = None, padding: str = "2px 4px", font_size_pt: int = 8,
                         radius: int = 16) -> str:
    """
    Per-QTRM matrix button (Isolation's 96-button grid, Soft Reset's
    QTRM-id buttons): light grey idle with real hover/pressed feedback
    (it's clickable), flat solid color for pending/linked/not-linked
    results (a status snapshot, not meant to invite clicking while shown).
    Default radius matches link_test_tab.py's _Led exactly (16px) - both
    are the same kind of per-QTRM indicator, just in different tabs, and
    should read as the same shape.
    """
    base = f"padding: {padding}; font-size: {font_size_pt}pt; font-weight: 500; border-radius: {radius}px;"
    if bg_color is None:
        return (
            f"QPushButton {{ {base} background-color: {rgb_css(IDLE_MATRIX_RGB)}; color: {STATE_TEXT_COLOR}; }}"
            f"QPushButton:hover {{ background-color: {rgb_css(IDLE_MATRIX_HOVER_RGB)}; }}"
            f"QPushButton:pressed {{ background-color: {rgb_css(IDLE_MATRIX_PRESSED_RGB)}; }}"
        )
    return (
        f"QPushButton {{ {base} background-color: {bg_color}; color: {STATE_TEXT_COLOR}; }}"
        f"QPushButton:hover {{ background-color: {bg_color}; }}"
        f"QPushButton:pressed {{ background-color: {bg_color}; }}"
    )
