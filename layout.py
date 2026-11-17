"""Pure layout maths for tesserawall. No tkinter, no mpv, no I/O.

DONE AND TESTED (see test_layout.py). Do not rewrite; import from it.

Model: videos keep their given order and are split into contiguous ROWS.
Every row spans the full width; row heights sum to the full height.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


def _partitions(n: int):
    """Yield every split of n items into contiguous rows, as a list of row lengths."""
    for mask in range(1 << (n - 1)):
        rows, run = [], 1
        for i in range(n - 1):
            if mask >> i & 1:
                rows.append(run)
                run = 1
            else:
                run += 1
        rows.append(run)
        yield rows


def choose_rows(aspects: list[float], W: int, H: int, max_crop: float = 0.10,
                balance_weight: float = 0.5) -> list[int]:
    """Pick the row split that leaves the least black bars while keeping tiles similar in size.

    aspects: width/height of each video, in display order. Returns row lengths, e.g. [3, 2].
    max_crop: same value given to panscan_for. balance_weight: higher = more equal tile sizes,
    lower = fill the screen even if one video ends up much bigger than the rest.
    """
    return _best(aspects, W, H, max_crop, balance_weight)[1]


def choose(aspects: list[float], W: int, H: int, max_crop: float = 0.10,
           balance_weight: float = 0.5) -> tuple[bool, list[int]]:
    """Like choose_rows, but also tries stacking videos in COLUMNS (good for portrait + landscape mixes).

    Returns (columns, groups). Pass both to layout(): layout(aspects, groups, W, H, columns=columns).
    With columns=True the videos run top-to-bottom down the first column, then the next column.
    """
    row_score, rows = _best(aspects, W, H, max_crop, balance_weight)
    col_score, cols = _best([1 / a for a in aspects], H, W, max_crop, balance_weight)
    return (True, cols) if col_score < row_score - 1e-9 else (False, rows)


def arrange(aspects: list[float], W: int, H: int, max_crop: float = 0.10, balance_weight: float = 0.5,
            reorder_margin: float = 0.05, allow_reorder: bool = True) -> tuple[list[int], bool, list[int]]:
    """Best arrangement, also allowed to regroup videos by shape. Returns (order, columns, groups).

    order: indices into `aspects`; reorder your tiles to [tiles[i] for i in order] first, then call
    layout() with the reordered aspects. The given order is kept unless sorting by aspect ratio
    (portrait first) is clearly better, e.g. landscape/portrait/landscape -> portrait | two landscapes.
    """
    n = len(aspects)
    best = None
    orders = [list(range(n))]
    if allow_reorder:  # False once the user has arranged tiles by hand: only pick rows/columns
        orders.append(sorted(range(n), key=lambda i: aspects[i]))
    for order in orders:
        a = [aspects[i] for i in order]
        for columns, (score, groups) in ((False, _best(a, W, H, max_crop, balance_weight)),
                                         (True, _best([1 / x for x in a], H, W, max_crop, balance_weight))):
            if best is None or score < best[0] - (reorder_margin if order != best[1] else 1e-9):
                best = (score, order, columns, groups)
    return best[1], best[2], best[3]


def _best(aspects, W, H, max_crop, balance_weight) -> tuple[float, list[int]]:
    n = len(aspects)
    if n == 0:
        return 0.0, []
    free = -math.log(1 - min(max(max_crop, 0.0), 0.9))
    best_score, best_rows = None, None
    for rows in _partitions(n):
        heights, i = [], 0
        for k in rows:
            heights.append(W / sum(aspects[i:i + k]))  # height at which this row is exactly W wide
            i += k
        stretch = H / sum(heights)  # 1.0 means a perfect fit with no crop and no bars
        logs = [math.log(h) for h, k in zip(heights, rows) for _ in range(k)]
        mean = sum(logs) / n
        spread = math.sqrt(sum((v - mean) ** 2 for v in logs) / n)
        misfit = abs(math.log(stretch))
        # Misfit that the crop budget can hide is nearly free; only the excess shows as bars.
        score = max(0.0, misfit - free) + 0.1 * misfit + balance_weight * spread
        if best_score is None or score < best_score - 1e-9:
            best_score, best_rows = score, rows
    return best_score, best_rows


def layout(aspects: list[float], rows: list[int], W: int, H: int, focus: int | None = None,
           focus_scale: float = 1.6, min_shrink: float = 0.45, columns: bool = False) -> list[Rect]:
    """Return one Rect per video (same order as aspects) tiling exactly W x H.

    columns: True if `rows` came from choose() with columns=True (groups are columns, not rows).

    focus: index of the hovered video, or None for the balanced layout.
    focus_scale: how much the focused video's row height (and its width) grows.
    min_shrink: unfocused rows/tiles never shrink below this fraction of their balanced size.

    Guarantee: the focused Rect always contains that video's balanced Rect, so the
    cursor that triggered the focus is still inside the video after the reflow.
    """
    n = len(aspects)
    if n == 0:
        return []
    assert sum(rows) == n
    if columns:  # solve the mirrored problem, then swap x/y back
        flipped = layout([1 / a for a in aspects], rows, H, W, focus, focus_scale, min_shrink)
        return [Rect(r.y, r.x, r.h, r.w) for r in flipped]
    members, nat_h, i = [], [], 0
    for k in rows:
        members.append(list(range(i, i + k)))
        nat_h.append(W / sum(aspects[i:i + k]))
        i += k
    stretch = H / sum(nat_h)
    row_h = [h * stretch for h in nat_h]
    widths = [[aspects[j] * nat_h[r] for j in members[r]] for r in range(len(rows))]

    if focus is not None:
        fr = next(r for r, m in enumerate(members) if focus in m)
        grow = focus_scale
        if len(rows) > 1:
            rest = H - row_h[fr]
            new_h = min(row_h[fr] * focus_scale, H - rest * min_shrink)
            grow = new_h / row_h[fr]
            shrink = (H - new_h) / rest
            row_h = [new_h if r == fr else h * shrink for r, h in enumerate(row_h)]
        ws, p = widths[fr], members[fr].index(focus)
        if len(ws) > 1:
            rest = W - ws[p]
            new_w = min(ws[p] * grow, W - rest * min_shrink)
            shrink = (W - new_w) / rest
            widths[fr] = [new_w if q == p else w * shrink for q, w in enumerate(ws)]

    out: list[Rect | None] = [None] * n
    y_edges, acc = [0], 0.0
    for h in row_h:
        acc += h
        y_edges.append(round(acc))
    y_edges[-1] = H
    for r, m in enumerate(members):
        x_edges, acc = [0], 0.0
        for w in widths[r]:
            acc += w
            x_edges.append(round(acc))
        x_edges[-1] = W
        for q, j in enumerate(m):
            out[j] = Rect(x_edges[q], y_edges[r], x_edges[q + 1] - x_edges[q], y_edges[r + 1] - y_edges[r])
    return out  # type: ignore[return-value]


def grid(aspects: list[float], W: int, H: int) -> list[Rect]:
    """Uniform grid: every row has the same height and every tile in a row the same width.

    The column count is the one whose cell shape is closest to the videos' shapes. A short last row
    is stretched to the full width, so the screen is still covered exactly. Order is row by row.
    """
    n = len(aspects)
    if n == 0:
        return []
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        out, i = [], 0
        for r in range(rows):
            y0, y1 = round(H * r / rows), round(H * (r + 1) / rows)
            k = min(cols, n - i)
            for q in range(k):
                x0, x1 = round(W * q / k), round(W * (q + 1) / k)
                out.append(Rect(x0, y0, x1 - x0, y1 - y0))
            i += k
        # Score the real cells, so a badly stretched last row counts against this column count.
        score = sum(abs(math.log((c.w / c.h) / a)) for c, a in zip(out, aspects))
        if best is None or score < best[0] - 1e-9:
            best = (score, out)
    return best[1]


def panscan_for(cell_w: int, cell_h: int, aspect: float, max_crop: float = 0.10) -> float:
    """mpv `panscan` value (0..1) that crops at most max_crop of the video's long-overflow axis.

    mpv scales the video by 1 + panscan * (r - 1), where r is the fit-to-fill ratio.
    0 = whole frame visible (bars), 1 = cell completely filled (max crop).
    """
    if cell_w <= 0 or cell_h <= 0:
        return 0.0
    cell = cell_w / cell_h
    r = max(cell / aspect, aspect / cell)
    if r <= 1.001 or max_crop <= 0:
        return 0.0
    if max_crop >= 1:
        return 1.0
    return min(1.0, (1 / (1 - max_crop) - 1) / (r - 1))
