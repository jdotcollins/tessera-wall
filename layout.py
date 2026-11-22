"""Pure layout maths for tesserawall. No tkinter, no mpv, no I/O.

Tested by test_layout.py, including the fast search against the exact one on random walls.

Model: videos keep their given order and are split into contiguous ROWS.
Every row spans the full width; row heights sum to the full height. Up to EXACT_UP_TO videos every
possible split is scored; above that a fast search is used (see _best_fast).
"""
from __future__ import annotations

import functools
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


EXACT_UP_TO = 12   # every possible split is tried up to this many videos (2^(n-1) splits: 2048 at 12)


def _prefix(aspects):
    p = [0.0]
    for a in aspects:
        p.append(p[-1] + a)
    return p


def _score(aspects, rows, W, H, free, balance_weight, prefix=None) -> float:
    """How good a split into rows is; lower is better. Shared by the exact and the fast search.

    Works from running totals, so one split costs O(rows) rather than O(videos)."""
    prefix = prefix or _prefix(aspects)
    n = len(aspects)
    i = 0
    total_h = sum_l = sum_l2 = 0.0
    for k in rows:
        h = W / (prefix[i + k] - prefix[i])   # height at which this row is exactly W wide
        i += k
        total_h += h
        lh = math.log(h)
        sum_l += k * lh                        # every video in the row shares its height
        sum_l2 += k * lh * lh
    mean = sum_l / n
    spread = math.sqrt(max(0.0, sum_l2 / n - mean * mean))
    misfit = abs(math.log(H / total_h))       # 0 means a perfect fit with no crop and no bars
    # Misfit that the crop budget can hide is nearly free; only the excess shows as bars.
    return max(0.0, misfit - free) + 0.1 * misfit + balance_weight * spread


def _best(aspects, W, H, max_crop, balance_weight) -> tuple[float, list[int]]:
    score, rows = _best_cached(tuple(aspects), W, H, max_crop, balance_weight)
    return score, list(rows)


@functools.lru_cache(maxsize=256)
def _best_cached(aspects, W, H, max_crop, balance_weight) -> tuple[float, tuple]:
    n = len(aspects)
    if n == 0:
        return 0.0, ()
    free = -math.log(1 - min(max(max_crop, 0.0), 0.9))
    if n <= EXACT_UP_TO:
        prefix = _prefix(aspects)
        best_score, best_rows = None, None
        for rows in _partitions(n):
            score = _score(aspects, rows, W, H, free, balance_weight, prefix)
            if best_score is None or score < best_score - 1e-9:
                best_score, best_rows = score, rows
        return best_score, tuple(best_rows)
    score, rows = _best_fast(aspects, W, H, free, balance_weight)
    return score, tuple(rows)


def _best_fast(aspects, W, H, free, balance_weight) -> tuple[float, list[int]]:
    """Near-optimal split for many videos, in roughly O(n^2) instead of O(2^n).

    A row's height is W / (sum of its aspect ratios), so rows of equal height are rows with equal
    aspect sums. For each row count k: cut where the running aspect sum crosses each multiple of
    total/k, then move single cuts left or right while that lowers the score.
    """
    n = len(aspects)
    prefix = _prefix(aspects)
    total = prefix[-1]

    def to_rows(cs):
        return [b - a for a, b in zip([0] + list(cs), list(cs) + [n])]

    def score_of(cs):
        return _score(aspects, to_rows(cs), W, H, free, balance_weight, prefix)

    def valid(cs):
        return all(a < b for a, b in zip([0] + list(cs), list(cs) + [n]))

    def polish(cuts):
        """Move cuts while that lowers the score: one cut by 1 or 2, or two neighbours together."""
        score = score_of(cuts)
        moves = [(c, d) for c in range(len(cuts)) for d in (-2, -1, 1, 2)]
        moves += [((c, c + 1), d) for c in range(len(cuts) - 1) for d in (-1, 1)]
        improved = True
        while improved:
            improved = False
            for which, d in moves:
                moved = list(cuts)
                for c in (which if isinstance(which, tuple) else (which,)):
                    moved[c] += d
                if valid(moved):
                    s = score_of(moved)
                    if s < score - 1e-9:
                        cuts, score, improved = moved, s, True
        return score, cuts

    best = (None, None)
    for k in range(1, min(n, 14) + 1):
        starts = set()
        for pick in (min, max, None):                    # nearest, and either side of, r/k of the width
            cuts, last = [], 0
            for r in range(1, k):
                goal = total * r / k
                options = range(last + 1, n - (k - r) + 1)
                if pick is None:
                    j = min(options, key=lambda j: abs(prefix[j] - goal))
                elif pick is min:
                    j = max([j for j in options if prefix[j] <= goal] or [options[0]])
                else:
                    j = min([j for j in options if prefix[j] >= goal] or [options[-1]])
                cuts.append(j)
                last = j
            if valid(cuts):
                starts.add(tuple(cuts))
        for start in starts:
            score, cuts = polish(list(start))
            if best[0] is None or score < best[0] - 1e-9:
                best = (score, to_rows(cuts))
    return best


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


def grid_rows(rows: list[int], W: int, H: int) -> list[Rect]:
    """Even grid for given rows (a template's): every row the same height, every cell in a row the
    same width. Order is row by row; the screen is covered exactly."""
    out = []
    for r, k in enumerate(rows):
        y0, y1 = round(H * r / len(rows)), round(H * (r + 1) / len(rows))
        for q in range(k):
            x0, x1 = round(W * q / k), round(W * (q + 1) / k)
            out.append(Rect(x0, y0, x1 - x0, y1 - y0))
    return out


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


# ---- magnify (zoom and pan inside one video) -----------------------------------------------------------
# mpv draws the video fitted to the window, scaled by 1 + panscan * (r - 1) (see panscan_for), then by
# 2**video-zoom. video-pan-x/y move it by that fraction of the drawn size. Pointer offsets below are in
# pixels from the window's centre.

def drawn_size(cell_w: int, cell_h: int, aspect: float, panscan: float, mag: float) -> tuple[float, float]:
    """Width and height in pixels at which mpv draws the video in a cell."""
    if cell_w <= 0 or cell_h <= 0 or aspect <= 0:
        return 0.0, 0.0
    fit_w, fit_h = (cell_w, cell_w / aspect) if aspect >= cell_w / cell_h else (cell_h * aspect, cell_h)
    r = max(cell_w / fit_w, cell_h / fit_h)
    k = (1 + panscan * (r - 1)) * 2 ** mag
    return fit_w * k, fit_h * k


def pan_limit(cell_w: int, cell_h: int, aspect: float, panscan: float, mag: float) -> tuple[float, float]:
    """Largest video-pan-x / -y that still keeps the drawn video over the whole cell along that axis
    (0 where the video is not larger than the cell)."""
    dw, dh = drawn_size(cell_w, cell_h, aspect, panscan, mag)
    lx = max(0.0, (dw - cell_w) / 2 / dw) if dw else 0.0
    ly = max(0.0, (dh - cell_h) / 2 / dh) if dh else 0.0
    return lx, ly


def clamp_pan(cell_w: int, cell_h: int, aspect: float, panscan: float, mag: float,
              pan: tuple[float, float]) -> tuple[float, float]:
    lx, ly = pan_limit(cell_w, cell_h, aspect, panscan, mag)
    return max(-lx, min(lx, pan[0])), max(-ly, min(ly, pan[1]))


def magnify_at(cell_w: int, cell_h: int, aspect: float, panscan: float, mag: float, pan: tuple[float, float],
               new_mag: float, ux: float, uy: float) -> tuple[float, float]:
    """The pan that keeps the picture point under the pointer (ux, uy) in place while the zoom goes from
    mag to new_mag, clamped so no edge comes into the cell."""
    w1, h1 = drawn_size(cell_w, cell_h, aspect, panscan, mag)
    w2, h2 = drawn_size(cell_w, cell_h, aspect, panscan, new_mag)
    if not (w1 and h1 and w2 and h2):
        return 0.0, 0.0
    vx, vy = ux / w1 - pan[0], uy / h1 - pan[1]          # the picture point, as a fraction from its centre
    return clamp_pan(cell_w, cell_h, aspect, panscan, new_mag, (ux / w2 - vx, uy / h2 - vy))
