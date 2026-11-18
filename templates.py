"""Templates: open videos of matching shapes from a folder, arranged in given rows.

A template is a list of rows; each row lists the shape of each slot:
  "landscape" (wider than 1.2:1), "portrait" (taller than 1:1.2), "square" (in between), "any",
  or an exact ratio such as "16:9", "4:3", "9:16" or "1.85" (within 3 %).
All slots with the same shape name in one template get videos of one shared ratio (so a 2x2 landscape
grid is four 16:9 videos, not a mix of 16:9 and 4:3); "any" slots take any video. The built-in templates
below are written in the same format as the user's own file (templates.toml next to the settings file).
"Best fit" is not a template: it looks at the folder and picks the ratio and count that fill the screen.

Pure apart from load_templates / ensure_user_file (file access); no tkinter, no mpv.
"""
from __future__ import annotations

import math
import os
import random
import tomllib
from dataclasses import dataclass, field

from layout import choose_rows, layout, panscan_for

FORMAT_VERSION = 1
TOLERANCE = 0.03                  # ratios within 3 % count as the same shape
LANDSCAPE_FROM = 1.2              # 1.2:1 and wider is landscape; 1:1.2 and taller is portrait
BEST_FIT_MAX = 9                  # Best fit opens at most this many videos (templates.toml: best_fit_max)
BEST_NAMES = {"best", "best fit"}

BUILT_IN_TOML = """
version = 1

[[template]]
name = "2×2 landscape"
rows = [["landscape", "landscape"], ["landscape", "landscape"]]

[[template]]
name = "3×3 landscape"
rows = [["landscape", "landscape", "landscape"], ["landscape", "landscape", "landscape"],
        ["landscape", "landscape", "landscape"]]

[[template]]
name = "4×4 landscape"
rows = [["landscape", "landscape", "landscape", "landscape"], ["landscape", "landscape", "landscape", "landscape"],
        ["landscape", "landscape", "landscape", "landscape"], ["landscape", "landscape", "landscape", "landscape"]]

[[template]]
name = "3 portrait in a row"
rows = [["portrait", "portrait", "portrait"]]

[[template]]
name = "4 portrait in a row"
rows = [["portrait", "portrait", "portrait", "portrait"]]

[[template]]
name = "2 square side by side"
rows = [["square", "square"]]

[[template]]
name = "Feature + 3 portrait"
rows = [["landscape"], ["portrait", "portrait", "portrait"]]
"""

USER_FILE_TEXT = """\
# Tessera Wall templates. A template opens videos of matching shapes from a folder in fixed rows.
# Each row lists the shape of each slot: "landscape", "portrait", "square", "any", or an exact ratio
# such as "16:9", "4:3", "9:16" or "2.39". Slots with the same shape in one template get videos of the
# same ratio. Your templates appear after the built-in ones; one with the same name replaces it.
# Remove the leading '#' from the lines below to try the example.

version = 1

# Most videos "Best fit" will open.
# best_fit_max = 9

# [[template]]
# name = "Big one, four small"
# rows = [["landscape"], ["landscape", "landscape", "landscape", "landscape"]]
"""


@dataclass
class Template:
    name: str
    rows: list[list[str]]
    built_in: bool = False

    @property
    def count(self) -> int:
        return sum(len(r) for r in self.rows)

    def needs(self) -> dict[str, int]:
        """{shape: slots} in first-seen order."""
        out: dict[str, int] = {}
        for row in self.rows:
            for s in row:
                out[s] = out.get(s, 0) + 1
        return out


@dataclass
class Plan:
    """What to open: items in slot order, rows to force (None = let the wall arrange), and the shape
    group of each chosen file (N then swaps only within it)."""
    name: str
    items: list[str]
    rows: list[int] | None
    shape_of: dict[str, float] = field(default_factory=dict)
    message: str = ""


def normalise_name(name: str) -> str:
    return " ".join(name.lower().replace("×", "x").split())


def parse_shape(token) -> tuple[str, float | None] | None:
    """("class", None) for landscape/portrait/square/any, ("ratio", r) for "16:9" / "1.85"; None if bad."""
    if not isinstance(token, str):
        return None
    t = token.strip().lower()
    if t in ("landscape", "portrait", "square", "any"):
        return t, None
    try:
        if ":" in t:
            a, b = t.split(":")
            r = float(a) / float(b)
        else:
            r = float(t)
    except (ValueError, ZeroDivisionError):
        return None
    return ("ratio", r) if r > 0 and math.isfinite(r) else None


def matches(token: str, aspect: float) -> bool:
    kind, r = parse_shape(token)
    if kind == "any":
        return True
    if kind == "landscape":
        return aspect >= LANDSCAPE_FROM
    if kind == "portrait":
        return aspect <= 1 / LANDSCAPE_FROM
    if kind == "square":
        return 1 / LANDSCAPE_FROM < aspect < LANDSCAPE_FROM
    return abs(math.log(aspect / r)) <= math.log(1 + TOLERANCE)


def parse(data: dict, built_in: bool = False) -> tuple[list[Template], list[str], dict]:
    """A parsed templates file -> (templates, problems, options). Bad templates are skipped and named."""
    problems: list[str] = []
    options: dict = {}
    version = data.get("version", FORMAT_VERSION)
    if not isinstance(version, int) or version > FORMAT_VERSION:
        return [], [f"templates file is version {version!r}; this Tessera Wall reads version {FORMAT_VERSION}"], {}
    m = data.get("best_fit_max")
    if m is not None:
        if isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 24:
            options["best_fit_max"] = m
        else:
            problems.append("best_fit_max should be a whole number from 1 to 24")
    out = []
    for i, t in enumerate(data.get("template", []) if isinstance(data.get("template", []), list) else []):
        label = t.get("name") if isinstance(t, dict) and isinstance(t.get("name"), str) else f"template {i + 1}"
        rows = t.get("rows") if isinstance(t, dict) else None
        if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"].strip():
            problems.append(f"{label}: needs a name")
            continue
        if (not isinstance(rows, list) or not rows
                or not all(isinstance(r, list) and r for r in rows)):
            problems.append(f"{label}: rows should be a list of rows, each a list of shapes")
            continue
        bad = [s for r in rows for s in r if parse_shape(s) is None]
        if bad:
            problems.append(f"{label}: unknown shape {bad[0]!r}")
            continue
        if normalise_name(t["name"]) in BEST_NAMES:
            problems.append(f"{label}: that name is reserved for Best fit")
            continue
        out.append(Template(t["name"].strip(), [[str(s).strip().lower() for s in r] for r in rows], built_in))
    return out, problems, options


BUILT_IN = parse(tomllib.loads(BUILT_IN_TOML), built_in=True)[0]


def user_file_path(config_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "templates.toml")


def ensure_user_file(path: str) -> None:
    if os.path.exists(path):
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(USER_FILE_TEXT)
    except OSError:
        pass


def load_templates(path: str | None) -> tuple[list[Template], list[str], dict]:
    """Built-in templates, then the user's (same name replaces the built-in). Never raises."""
    templates = list(BUILT_IN)
    if not path or not os.path.exists(path):
        return templates, [], {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return templates, [f"templates file: {e}"], {}
    own, problems, options = parse(data)
    for t in own:
        templates = [b for b in templates if normalise_name(b.name) != normalise_name(t.name)] + [t]
    return templates, problems, options


def find(templates: list[Template], name: str) -> Template | None:
    key = normalise_name(name)
    return next((t for t in templates if normalise_name(t.name) == key), None)


def groups(shapes: dict[str, float | None]) -> list[tuple[float, list[str]]]:
    """Videos grouped by ratio (within TOLERANCE of the group's first, smallest ratio), largest first.
    Returns (representative ratio, paths) pairs; the ratio is the group's median."""
    known = sorted(((a, p) for p, a in shapes.items() if a), key=lambda x: x[0])
    out: list[list[tuple[float, str]]] = []
    for a, p in known:
        if out and math.log(a / out[-1][0][0]) <= math.log(1 + TOLERANCE):
            out[-1].append((a, p))
        else:
            out.append([(a, p)])
    res = [(g[len(g) // 2][0], [p for _, p in g]) for g in out]
    return sorted(res, key=lambda x: -len(x[1]))


def _order_key(token: str) -> int:
    kind = parse_shape(token)[0]
    return {"ratio": 0, "landscape": 1, "portrait": 1, "square": 1, "any": 2}[kind]


def describe_need(token: str, n: int) -> str:
    word = {"any": "video"}.get(token, f"{token} video")
    return f"{n} {word}{'s' if n != 1 else ''}"


def availability(template: Template, shapes: dict[str, float | None]) -> tuple[int, int]:
    """(videos a plan would get, slots in the template) without choosing at random."""
    got = sum(len(v) for v in _allocate(template, shapes, random.Random(0)).values())
    return got, template.count


def _allocate(template: Template, shapes, rng) -> dict[str, list[str]]:
    """{shape token: chosen paths}. Exact ratios first, then classes, then "any", so the most specific
    slots get first pick; each file is used once. A class picks the single largest matching ratio group."""
    used: set[str] = set()
    picked: dict[str, list[str]] = {}
    for token, need in sorted(template.needs().items(), key=lambda kv: _order_key(kv[0])):
        if token == "any":
            pool = [p for p, a in shapes.items() if a and p not in used]
            picked[token] = rng.sample(pool, min(need, len(pool)))
        else:
            best: list[str] = []
            for ratio, paths in groups({p: a for p, a in shapes.items() if p not in used}):
                if matches(token, ratio) and len(paths) > len(best):
                    best = paths
            picked[token] = rng.sample(best, min(need, len(best)))
        used.update(picked[token])
    return picked


def plan(template: Template, shapes: dict[str, float | None], rng: random.Random | None = None) -> Plan:
    rng = rng or random.Random()
    picked = _allocate(template, shapes, rng)
    queues = {k: list(v) for k, v in picked.items()}
    items = []
    for row in template.rows:
        for token in row:
            if queues[token]:
                items.append(queues[token].pop(0))
    short = [(t, n, len(picked[t])) for t, n in template.needs().items() if len(picked[t]) < n]
    message = ""
    if short:
        t, n, got = short[0]
        message = (f"{template.name}: found {got} of {describe_need(t, n)}" if got else
                   f"{template.name}: no {t} videos here") if t != "any" else \
            f"{template.name}: found only {got} of {n} videos"
    rows = [len(r) for r in template.rows] if not short else None
    shape_of = {p: (shapes[p] if t == "any" else _group_ratio(p, shapes)) for t, ps in picked.items() for p in ps}
    return Plan(template.name, items, rows, shape_of, message)


def _group_ratio(path: str, shapes) -> float:
    for ratio, paths in groups(shapes):
        if path in paths:
            return ratio
    return shapes[path]


def coverage(aspects: list[float], rows: list[int], W: int, H: int, max_crop: float) -> float:
    """Share of the screen showing picture (not black bars) for these videos in these rows."""
    rects = layout(aspects, rows, W, H)
    covered = 0.0
    for a, r in zip(aspects, rects):
        cell = r.w / r.h
        ratio = max(cell / a, a / cell)                        # how much larger "fill" is than "fit"
        scale = 1 + panscan_for(r.w, r.h, a, max_crop) * (ratio - 1)
        covered += min(1.0, scale / ratio) * r.w * r.h
    return covered / (W * H)


def best_fit(shapes: dict[str, float | None], W: int, H: int, max_crop: float = 0.10,
             balance_weight: float = 0.5, most: int = BEST_FIT_MAX, rng: random.Random | None = None) -> Plan:
    """The ratio group and count that fill the screen best, preferring more videos when the fill is
    within 3 % of the best possible. Rows are fixed to the ones scored."""
    rng = rng or random.Random()
    options = []
    for ratio, paths in groups(shapes):
        for n in range(1, min(len(paths), most) + 1):
            rows = choose_rows([ratio] * n, W, H, max_crop, balance_weight)
            options.append((coverage([ratio] * n, rows, W, H, max_crop), n, ratio, paths, rows))
    if not options:
        return Plan("Best fit", [], None, {}, "Best fit: no playable videos here")
    top = max(o[0] for o in options)
    fill, n, ratio, paths, rows = max((o for o in options if o[0] >= top - 0.03), key=lambda o: (o[1], o[0]))
    items = rng.sample(paths, n)
    return Plan(f"Best fit: {describe_group(ratio, n)}", items, rows, {p: ratio for p in items},
                "")


def describe_group(ratio: float, n: int) -> str:
    kind = "landscape" if ratio >= LANDSCAPE_FROM else "portrait" if ratio <= 1 / LANDSCAPE_FROM else "square"
    return f"{n} {kind} ({ratio_label(ratio)})"


def ratio_label(r: float) -> str:
    for a, b in ((16, 9), (9, 16), (4, 3), (3, 4), (1, 1), (21, 9), (9, 21), (3, 2), (2, 3), (5, 4), (4, 5),
                 (185, 100), (239, 100)):
        if abs(math.log(r / (a / b))) <= math.log(1 + TOLERANCE):
            return f"{a / 100:g}:1" if b == 100 else f"{a}:{b}"
    return f"{r:.2f}:1"
