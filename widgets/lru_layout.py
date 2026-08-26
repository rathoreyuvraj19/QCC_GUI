"""
lru_layout.py

Where each LRU sits in the per-LRU button/LED grids (Isolation, Soft Reset,
Link Test, Tx Forward Matrix).

Two layouts, picked automatically from the configured LRU count:

  - **Cold Plate** (exactly 96 LRUs, the original QTRM array): the real
    connector/power-group arrangement - 6 Cold Plate groups, CP0 through
    CP5, 16 LRUs each, physically stacked bottom (CP0) to top (CP5).
    Within each group: 2 rows x 8 columns, odd-numbered LRU on top,
    even-numbered below, both ascending left to right. This is a physical
    fact about that specific hardware, so it's kept exactly as it was
    rather than being generalized away.

  - **Sequential** (any other count): one group, LRUs 0..N-1 laid out
    row-major across an automatically chosen column count. Deliberately
    invents no grouping - an array this module knows nothing about has no
    cold plates to draw, and a made-up grouping would read as real.

Consumers iterate groups_top_to_bottom() x group_grid_positions(group) and
size their grid with matrix_cols()/group_rows(), so the same code draws
either layout.
"""

import math

from core.packet import num_lru

# The one array whose physical arrangement we actually know.
COLD_PLATE_NUM_LRU = 96
COLD_PLATE_GROUP_SIZE = 16
COLD_PLATE_COLS = 8

# Upper bound on the sequential layout's width. Past this the buttons get
# too narrow to read their own labels before the window runs out of room.
_MAX_AUTO_COLS = 16


def uses_cold_plate_layout() -> bool:
    """True when the configured array is the 96-LRU one this module has a real layout for."""
    return num_lru() == COLD_PLATE_NUM_LRU


def matrix_cols() -> int:
    """Columns in one group's grid."""
    if uses_cold_plate_layout():
        return COLD_PLATE_COLS
    # Wider than tall - these grids sit in a horizontal window - but not so
    # wide that a 200-LRU array becomes one unreadable strip.
    n = num_lru()
    return max(1, min(_MAX_AUTO_COLS, math.ceil(math.sqrt(n * 2))))


def num_groups() -> int:
    if uses_cold_plate_layout():
        return COLD_PLATE_NUM_LRU // COLD_PLATE_GROUP_SIZE  # 6
    return 1


def group_size() -> int:
    """How many LRUs one group holds."""
    if uses_cold_plate_layout():
        return COLD_PLATE_GROUP_SIZE
    return num_lru()


def group_rows() -> int:
    """Rows in one group's grid - 2 for Cold Plate, as many as it takes otherwise."""
    if uses_cold_plate_layout():
        return 2
    return max(1, math.ceil(num_lru() / matrix_cols()))


def group_label(group: int) -> str:
    """Title for a group's box."""
    if uses_cold_plate_layout():
        return f"CP{group}"
    return f"LRU 0-{num_lru() - 1}"


def groups_top_to_bottom():
    """Group numbers in physical top-to-bottom display order (CP5 first, CP0 last)."""
    return range(num_groups() - 1, -1, -1)


def group_grid_positions(group: int):
    """
    Yield (lru_index, local_row, local_col) for the LRUs in one group.

    A sequential layout's last row can be partial, so this yields only the
    cells that correspond to a real LRU - callers can add a widget for every
    position they're given without checking bounds.
    """
    cols = matrix_cols()
    if uses_cold_plate_layout():
        # Odd-numbered LRU on top, even below, both ascending left to right.
        group_start = group * COLD_PLATE_GROUP_SIZE
        for local_col in range(cols):
            yield group_start + 1 + 2 * local_col, 0, local_col
            yield group_start + 2 * local_col, 1, local_col
        return

    for lru_index in range(num_lru()):
        yield lru_index, lru_index // cols, lru_index % cols
