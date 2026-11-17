"""Draws the tesserawall icon and writes tesserawall.ico (for the exe, shortcuts and installer) and
tesserawall.png (for the app's own windows). Pure standard library; run from the repository root:

    python tools/make_icon.py

The picture is a small wall: one large highlighted video beside two smaller dimmed ones, on a dark
rounded square. Change the shapes or colours in SHAPES and re-run; commit the two generated files.
"""
import struct
import zlib

BG = (0x15, 0x18, 0x1D)
# (x0, y0, x1, y1, corner radius, colour) in a 0..1 square, drawn in order.
SHAPES = [
    (0.04, 0.04, 0.96, 0.96, 0.20, BG),
    (0.16, 0.17, 0.60, 0.83, 0.06, (0x6C, 0xB6, 0xFF)),   # the focused video
    (0.66, 0.17, 0.84, 0.47, 0.05, (0x8A, 0x96, 0xA3)),
    (0.66, 0.53, 0.84, 0.83, 0.05, (0x55, 0x60, 0x6C)),
]
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SS = 4  # supersampling per axis, for smooth edges


def inside(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def render(size):
    """Rows of (r, g, b, a) tuples, top to bottom."""
    rows = []
    n = SS * SS
    for py in range(size):
        row = []
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                y = (py + (sy + 0.5) / SS) / size
                for sx in range(SS):
                    x = (px + (sx + 0.5) / SS) / size
                    colour = None
                    for x0, y0, x1, y1, r, c in SHAPES:
                        if inside(x, y, x0, y0, x1, y1, r):
                            colour = c
                    if colour:
                        acc[0] += colour[0]; acc[1] += colour[1]; acc[2] += colour[2]; acc[3] += 1
            a = acc[3]
            row.append((acc[0] // a, acc[1] // a, acc[2] // a, a * 255 // n) if a else (0, 0, 0, 0))
        rows.append(row)
    return rows


def png(rows):
    size = len(rows)
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in rows)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def dib(rows):
    """Classic 32-bit icon image (BGRA, bottom-up, plus an empty 1-bit mask): readable by everything."""
    size = len(rows)
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = b"".join(bytes(v for (r, g, b, a) in row for v in (b, g, r, a)) for row in reversed(rows))
    mask = bytes(((size + 31) // 32) * 4) * size
    return header + pixels + mask


def main():
    images = {s: render(s) for s in ICO_SIZES}
    blobs = [(s, png(images[s]) if s >= 256 else dib(images[s])) for s in ICO_SIZES]
    offset = 6 + 16 * len(blobs)
    out = struct.pack("<HHH", 0, 1, len(blobs))
    for s, data in blobs:
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open("tesserawall.ico", "wb") as f:
        f.write(out + b"".join(d for _, d in blobs))
    with open("tesserawall.png", "wb") as f:
        f.write(png(images[64]))
    with open("tesserawall-256.png", "wb") as f:       # preview only; not shipped
        f.write(png(images[256]))
    print("wrote tesserawall.ico", [s for s, _ in blobs], "and tesserawall.png (64 px)")


if __name__ == "__main__":
    main()
