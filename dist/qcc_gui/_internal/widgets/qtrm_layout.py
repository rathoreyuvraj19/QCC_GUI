"""
qtrm_layout.py

Physical grid position for each of the 96 QTRMs, matching the array's real
connector/power-group layout: 6 Cold Plate groups, CP0 through CP5, 16
QTRMs each, physically stacked bottom (CP0) to top (CP5). Within each group:
2 rows x 8 columns - odd-numbered QTRM on top, even-numbered QTRM below,
both ascending left to right.
"""

NUM_QTRM = 96
GROUP_SIZE = 16
GROUPS = NUM_QTRM // GROUP_SIZE  # 6

MATRIX_COLS = 8
MATRIX_ROWS = GROUPS * 2  # 12


def qtrm_index_at(row: int, col: int) -> int:
    """QTRM index (0-based) placed at grid position (row, col)."""
    group_from_top = row // 2
    group = GROUPS - 1 - group_from_top
    group_start = group * GROUP_SIZE
    if row % 2 == 0:
        return group_start + 1 + 2 * col
    return group_start + 2 * col


def grid_positions():
    """Yield (qtrm_index, row, col) for all NUM_QTRM cells, one flat grid."""
    for row in range(MATRIX_ROWS):
        for col in range(MATRIX_COLS):
            yield qtrm_index_at(row, col), row, col


def group_grid_positions(group: int):
    """Yield (qtrm_index, local_row, local_col) for the 16 QTRMs in one Cold Plate group (0-5)."""
    group_start = group * GROUP_SIZE
    for local_row in range(2):
        for local_col in range(MATRIX_COLS):
            if local_row == 0:
                qtrm_index = group_start + 1 + 2 * local_col
            else:
                qtrm_index = group_start + 2 * local_col
            yield qtrm_index, local_row, local_col


def groups_top_to_bottom():
    """Group numbers in physical top-to-bottom display order (CP5 first, CP0 last)."""
    return range(GROUPS - 1, -1, -1)
