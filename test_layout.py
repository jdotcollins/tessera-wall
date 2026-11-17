"""Run with: python test_layout.py   (no pytest needed; exits non-zero on failure)"""
import random

from layout import Rect, arrange, choose, choose_rows, grid, layout, panscan_for

W, H = 2560, 1440


def check_tiling(rects: list[Rect]):
    assert all(r.w > 0 and r.h > 0 for r in rects), rects
    assert sum(r.w * r.h for r in rects) == W * H, "tiles must cover the screen exactly"
    for r in rects:
        assert 0 <= r.x and 0 <= r.y and r.x + r.w <= W and r.y + r.h <= H


def test_known_cases():
    assert choose_rows([16 / 9] * 4, W, H) == [2, 2]
    assert choose_rows([16 / 9] * 9, W, H) == [3, 3, 3]
    assert choose_rows([16 / 9], W, H) == [1]
    rects = layout([16 / 9] * 4, [2, 2], W, H)
    assert rects == [Rect(0, 0, 1280, 720), Rect(1280, 0, 1280, 720),
                     Rect(0, 720, 1280, 720), Rect(1280, 720, 1280, 720)]


def test_focus_grows_and_contains():
    rng = random.Random(1)
    choices = [16 / 9, 4 / 3, 9 / 16, 21 / 9, 1.0, 1.85]
    for _ in range(2000):
        n = rng.randint(1, 12)
        aspects = [rng.choice(choices) for _ in range(n)]
        rows = choose_rows(aspects, W, H)
        base = layout(aspects, rows, W, H)
        check_tiling(base)
        for f in range(n):
            foc = layout(aspects, rows, W, H, focus=f)
            check_tiling(foc)
            b, g = base[f], foc[f]
            assert g.x <= b.x and g.y <= b.y, (aspects, f)
            assert g.x + g.w >= b.x + b.w and g.y + g.h >= b.y + b.h, (aspects, f)
            if n > 1:
                assert g.w * g.h > b.w * b.h, (aspects, f)


def test_columns():
    # portrait + two landscapes: portrait should get a full-height column
    a = [9 / 16, 16 / 9, 16 / 9]
    cols, groups = choose(a, W, H)
    assert (cols, groups) == (True, [1, 2]), (cols, groups)
    rects = layout(a, groups, W, H, columns=True)
    assert rects[0].h == H and rects[1].x == rects[2].x and rects[1].y < rects[2].y
    assert choose([16 / 9] * 4, W, H) == (False, [2, 2])
    rng = random.Random(2)
    for _ in range(1000):
        n = rng.randint(1, 12)
        aspects = [rng.choice([16 / 9, 4 / 3, 9 / 16, 21 / 9, 1.0]) for _ in range(n)]
        cols, groups = choose(aspects, W, H)
        base = layout(aspects, groups, W, H, columns=cols)
        check_tiling(base)
        for f in range(n):
            foc = layout(aspects, groups, W, H, focus=f, columns=cols)
            check_tiling(foc)
            b, g = base[f], foc[f]
            assert g.x <= b.x and g.y <= b.y and g.x + g.w >= b.x + b.w and g.y + g.h >= b.y + b.h
            if n > 1:
                assert g.w * g.h > b.w * b.h


def test_arrange():
    L, P = 16 / 9, 9 / 16
    order, cols, groups = arrange([L, P, L], W, H)
    assert (order, cols, groups) == ([1, 0, 2], True, [1, 2]), (order, cols, groups)
    assert arrange([L] * 4, W, H) == ([0, 1, 2, 3], False, [2, 2])       # nothing to gain: order kept
    assert arrange([L, P, L, L], W, H)[0] == [0, 1, 2, 3]
    assert arrange([], W, H) == ([], False, [])
    rng = random.Random(3)
    for _ in range(500):
        a = [rng.choice([L, P, 4 / 3, 1.0]) for _ in range(rng.randint(1, 12))]
        order, cols, groups = arrange(a, W, H)
        assert sorted(order) == list(range(len(a))) and sum(groups) == len(a)
        check_tiling(layout([a[i] for i in order], groups, W, H, columns=cols))


def test_grid():
    L, P = 16 / 9, 9 / 16
    assert grid([], W, H) == []
    assert grid([L] * 4, W, H) == [Rect(0, 0, 1280, 720), Rect(1280, 0, 1280, 720),
                                   Rect(0, 720, 1280, 720), Rect(1280, 720, 1280, 720)]
    nine = grid([L] * 9, W, H)
    assert max(r.w for r in nine) - min(r.w for r in nine) <= 1 and len({r.h for r in nine}) == 1, \
        "3x3: all cells the same size (within 1 px of rounding)"
    five = grid([L] * 5, W, H)                      # 3 + 2: last row stretched to full width
    assert [r.y for r in five] == [0, 0, 0, 720, 720] and five[3].w == 1280
    assert all(r.h == H for r in grid([P] * 4, W, H)), "portrait videos: one row of four"
    rng = random.Random(4)
    for _ in range(500):
        check_tiling(grid([rng.choice([L, P, 4 / 3, 1.0]) for _ in range(rng.randint(1, 12))], W, H))


def test_panscan():
    assert panscan_for(1280, 720, 16 / 9) == 0.0
    assert panscan_for(1280, 720, 16 / 9, max_crop=0.5) == 0.0
    assert panscan_for(1000, 1000, 16 / 9, max_crop=1.0) == 1.0
    # cell is 2x too tall for the video; 10% crop allows scale 1.111 -> panscan 0.111
    assert abs(panscan_for(1280, 1440, 16 / 9, max_crop=0.10) - 0.1111) < 1e-3
    # small mismatch is fully absorbed by the crop budget
    assert panscan_for(1280, 760, 16 / 9, max_crop=0.10) == 1.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    for n in (4, 6, 7, 10):
        print(n, "x 16:9 ->", choose_rows([16 / 9] * n, W, H))
    print("mixed ->", choose_rows([16 / 9, 9 / 16, 16 / 9, 4 / 3, 9 / 16, 16 / 9, 21 / 9], W, H))
