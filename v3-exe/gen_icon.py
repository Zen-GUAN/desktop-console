# -*- coding: utf-8 -*-
"""Generate icon.ico for Desktop Console v3.

Recreates the in-app favicon (index.html line 10): rounded rect #8b9dff,
white polyline "W" with round caps/joins. Pure stdlib, no Pillow.
"""
import math
import os
import struct

SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4  # supersampling factor per axis

BLUE = (139, 157, 255, 255)   # #8b9dff
WHITE = (255, 255, 255, 255)

# favicon geometry in a 32x32 viewBox
RX = 10.0 / 32.0
SEGS = [((8, 10), (12, 22)), ((12, 22), (16, 14)), ((16, 14), (20, 22)), ((20, 22), (24, 10))]
STROKE_HALF = 2.5 / 32.0 / 2.0


def sd_rounded_rect(x, y, half, radius):
    """Signed distance to a rounded square centered at origin (negative = inside)."""
    qx = abs(x) - (half - radius)
    qy = abs(y) - (half - radius)
    ox, oy = max(qx, 0.0), max(qy, 0.0)
    return math.hypot(ox, oy) - radius


def seg_dist(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def render(size):
    half = size / 2.0
    radius = RX * size
    stroke = STROKE_HALF * size
    rgba = [[[0, 0, 0, 0] for _ in range(size)] for _ in range(size)]
    for j in range(size):
        for i in range(size):
            cover = 0.0
            stroke_cover = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    x = (i + (sx + 0.5) / SS) - half
                    y = (j + (sy + 0.5) / SS) - half
                    d = sd_rounded_rect(x, y, half, radius)
                    if d <= 0:
                        cover += 1.0
                        nx, ny = x / size, y / size  # back to 32-viewBox space, origin top-left
                        nx32, ny32 = nx * 32, ny * 32
                        dist = min(seg_dist(nx32, ny32, a, b) for a, b in SEGS)
                        if dist <= stroke:
                            stroke_cover += 1.0
            total = SS * SS
            if cover == 0:
                continue
            a = int(round(255 * cover / total))
            stroke_ratio = (stroke_cover / total) if cover else 0.0
            base = BLUE
            if stroke_ratio > 0:
                t = min(1.0, stroke_ratio)
                base = tuple(int(BLUE[c] * (1 - t) + WHITE[c] * t) for c in range(3)) + (255,)
            rgba[j][i] = [base[0], base[1], base[2], a]
    return rgba


def bmp_entry(rgba, w):
    header = struct.pack('<IiiHHIIiiII', 40, w, w * 2, 1, 32, 0, w * w * 4, 0, 0, 0, 0)
    rows = bytearray()
    for y in range(w - 1, -1, -1):
        for x in range(w):
            r, g, b, a = rgba[y][x]
            rows += bytes((b, g, r, a))
    stride = ((w + 31) // 32) * 4
    mask = b'\x00' * (stride * w)
    return header + bytes(rows) + mask


def write_ico(path):
    images = [(s, render(s)) for s in SIZES]
    entries = b''
    offset = 6 + 16 * len(images)
    blobs = []
    for w, rgba in images:
        data = bmp_entry(rgba, w)
        entries += struct.pack('<BBBBHHII', w % 256, w % 256, 0, 0, 1, 32, len(data), offset)
        blobs.append(data)
        offset += len(data)
    with open(path, 'wb') as f:
        f.write(struct.pack('<HHH', 0, 1, len(images)))
        f.write(entries)
        for blob in blobs:
            f.write(blob)


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
    write_ico(out)
    print('icon written: %s (%d bytes)' % (out, os.path.getsize(out)))
