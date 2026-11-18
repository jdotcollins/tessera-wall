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

# A slot can also take its videos from a folder of its own: absolute, or relative to the folder you
# apply the template to. Use single quotes for Windows paths.
# [[template]]
# name = "Films above, phone clips below"
# rows = [["landscape", "landscape"],
#         [{ shape = "portrait", folder = 'D:\\Videos\\Phone' }, { shape = "portrait", folder = 'D:\\Videos\\Phone' },
#          { shape = "portrait", folder = 'D:\\Videos\\Phone' }]]
"""


@dataclass
class Template:
    name: str
    rows: list[list[str]]
    built_in: bool = False
    # Per slot, parallel to rows: a folder of its own (as written: absolute, or relative to the folder the
    # template is applied to), or None for the chosen folder. None for the whole template: no own folders.
    folders: list[list[str | None]] | None = None

    @property
    def count(self) -> int:
        return sum(len(r) for r in self.rows)

    def slots(self):
        """(row, (shape, folder)) for every slot, row by row."""
        for r, row in enumerate(self.rows):
            for i, token in enumerate(row):
                yield r, (token, self.folders[r][i] if self.folders else None)

    def needs(self) -> dict[tuple[str, str | None], int]:
        """{(shape, folder): slots} in first-seen order."""
        out: dict[tuple, int] = {}
        for _, key in self.slots():
            out[key] = out.get(key, 0) + 1
        return out

    @property
    def own_folders(self) -> list[str]:
        return list(dict.fromkeys(f for _, (_, f) in self.slots() if f))

    @property
    def needs_chosen_folder(self) -> bool:
        return any(f is None for _, (_, f) in self.slots())


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
        slots = [[_parse_slot(s) for s in r] for r in rows]
        bad = next((s[2] for r in slots for s in r if s[2]), None)
        if bad:
            problems.append(f"{label}: {bad}")
            continue
        if normalise_name(t["name"]) in BEST_NAMES:
            problems.append(f"{label}: that name is reserved for Best fit")
            continue
        folders = [[s[1] for s in r] for r in slots]
        out.append(Template(t["name"].strip(), [[s[0] for s in r] for r in slots], built_in,
                            folders if any(f for r in folders for f in r) else None))
    return out, problems, options


def _parse_slot(s) -> tuple[str, str | None, str | None]:
    """A slot is a shape ("portrait") or a table { shape = "portrait", folder = 'D:\\Clips' }; the shape
    defaults to "any". Returns (shape, folder or None, problem or None)."""
    if isinstance(s, str):
        return (s.strip().lower(), None, None) if parse_shape(s) else ("", None, f"unknown shape {s!r}")
    if isinstance(s, dict):
        extra = sorted(set(s) - {"shape", "folder"})
        if extra:
            return "", None, f"unknown slot setting {extra[0]!r} (use shape and folder)"
        shape = s.get("shape", "any")
        if parse_shape(shape) is None:
            return "", None, f"unknown shape {shape!r}"
        folder = s.get("folder")
        if folder is not None and (not isinstance(folder, str) or not folder.strip()):
            return "", None, "folder should be a folder path in quotes"
        return shape.strip().lower(), folder.strip() if folder else None, None
    return "", None, f"a slot should be a shape or {{ shape = ..., folder = ... }}, not {s!r}"


def base_folder(paths: list[str]) -> str | None:
    """The folder a template is applied to: the first path given (or its folder, for a file)."""
    if not paths:
        return None
    p = os.path.abspath(paths[0])
    return os.path.dirname(p) if os.path.isfile(p) else p


def resolve_folder(raw: str, base: str | None) -> str | None:
    """A slot's folder as an absolute path: %VARS% and ~ expanded; relative to `base`. None if relative
    and there is no base."""
    p = os.path.expandvars(os.path.expanduser(raw))
    if not os.path.isabs(p):
        if not base:
            return None
        p = os.path.join(base, p)
    return os.path.normpath(p)


def gather(templates: list["Template"], base_paths: list[str], expand, probe) -> tuple[dict, dict]:
    """Read the shapes of everything these templates could use, in one probe: the chosen paths and
    every slot folder. expand(paths) -> files and probe(files) -> {file: aspect} are passed in (files.py
    and probe.py), so this stays free of I/O of its own.
    Returns (shapes of the chosen paths, {template name: {folder as written: {file: aspect}}})."""
    base = base_folder(base_paths)
    chosen = expand(list(base_paths)) if base_paths else []
    folder_files: dict[str, list[str]] = {}
    where: dict[tuple[str, str], str | None] = {}
    for t in templates:
        for raw in t.own_folders:
            path = resolve_folder(raw, base)
            where[(t.name, raw)] = path
            if path and path not in folder_files:
                folder_files[path] = expand([path]) if os.path.isdir(path) else []
    every = list(dict.fromkeys(chosen + [f for files in folder_files.values() for f in files]))
    found = probe(every) if every else {}
    shapes = {p: found.get(p) for p in chosen}
    per_template = {t.name: {raw: {p: found.get(p) for p in folder_files.get(where[(t.name, raw)] or "", [])}
                             for raw in t.own_folders} for t in templates}
    return shapes, per_template


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


def availability(template: Template, shapes: dict[str, float | None],
                 folder_shapes: dict | None = None) -> tuple[int, int]:
    """(videos a plan would get, slots in the template) without choosing at random."""
    got = sum(len(v[0]) for v in _allocate(template, shapes, random.Random(0), folder_shapes).values())
    return got, template.count


def _allocate(template: Template, shapes, rng, folder_shapes=None) -> dict[tuple, tuple[list[str], dict]]:
    """{(shape, folder): (chosen paths, {path: group ratio})}. Slots with a folder of their own draw from
    it (folder_shapes[folder]), the others from `shapes`. Exact ratios first, then classes, then "any",
    so the most specific slots get first pick; each file is used once. A class picks the single largest
    matching ratio group of its source."""
    used: set[str] = set()
    picked: dict[tuple, tuple[list[str], dict]] = {}
    for (token, folder), need in sorted(template.needs().items(), key=lambda kv: _order_key(kv[0][0])):
        source = (folder_shapes or {}).get(folder, {}) if folder else shapes
        if token == "any":
            pool = [p for p, a in source.items() if a and p not in used]
            chosen = rng.sample(pool, min(need, len(pool)))
            picked[(token, folder)] = (chosen, {p: source[p] for p in chosen})
        else:
            best: tuple[float, list[str]] = (0.0, [])
            for ratio, paths in groups({p: a for p, a in source.items() if p not in used}):
                if matches(token, ratio) and len(paths) > len(best[1]):
                    best = (ratio, paths)
            chosen = rng.sample(best[1], min(need, len(best[1])))
            picked[(token, folder)] = (chosen, {p: best[0] for p in chosen})
        used.update(picked[(token, folder)][0])
    return picked


def plan(template: Template, shapes: dict[str, float | None], rng: random.Random | None = None,
         folder_shapes: dict | None = None) -> Plan:
    """Choose files for the template: from `shapes` (the chosen folder) and, for slots with a folder of
    their own, from folder_shapes[folder as written] (see gather())."""
    rng = rng or random.Random()
    picked = _allocate(template, shapes, rng, folder_shapes)
    queues = {k: list(v[0]) for k, v in picked.items()}
    items = []
    for _, key in template.slots():
        if queues[key]:
            items.append(queues[key].pop(0))
    short = [(k, n, len(picked[k][0])) for k, n in template.needs().items() if len(picked[k][0]) < n]
    message = ""
    if short:
        (t, folder), n, got = short[0]
        where = f" in {os.path.basename(os.path.normpath(folder)) or folder}" if folder else ""
        if t == "any":
            message = f"{template.name}: found only {got} of {n} videos{where}"
        else:
            message = (f"{template.name}: found {got} of {describe_need(t, n)}{where}" if got else
                       f"{template.name}: no {t} videos {where.strip() or 'here'}")
    rows = [len(r) for r in template.rows] if not short else None
    shape_of = {p: r for _, ratios in picked.values() for p, r in ratios.items()}
    return Plan(template.name, items, rows, shape_of, message)


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
