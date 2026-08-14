#!/usr/bin/env python3
"""Render RepoAegis PNG and ICO assets with the Python standard library."""

from __future__ import annotations

import argparse
import struct
import zlib
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

type Point = tuple[float, float]
type Color = tuple[int, int, int, int]
GREEN: Color = (21, 150, 107, 255)
BRIGHT_GREEN: Color = (73, 201, 155, 255)
CARBON: Color = (23, 28, 26, 255)
WHITE: Color = (255, 255, 255, 255)
MUTED: Color = (201, 212, 206, 255)
TRANSPARENT: Color = (0, 0, 0, 0)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def cubic(start: Point, control_1: Point, control_2: Point, end: Point) -> list[Point]:
    points: list[Point] = []
    for index in range(1, 25):
        t = index / 24
        inverse = 1 - t
        points.append(
            (
                inverse**3 * start[0]
                + 3 * inverse**2 * t * control_1[0]
                + 3 * inverse * t**2 * control_2[0]
                + t**3 * end[0],
                inverse**3 * start[1]
                + 3 * inverse**2 * t * control_1[1]
                + 3 * inverse * t**2 * control_2[1]
                + t**3 * end[1],
            )
        )
    return points


def curved_polygon(start: Point, curves: list[tuple[Point, Point, Point]]) -> list[Point]:
    points = [start]
    current = start
    for control_1, control_2, end in curves:
        points.extend(cubic(current, control_1, control_2, end))
        current = end
    return points


WING = curved_polygon(
    (82, 156),
    [
        ((79, 124), (83, 84), (101, 58)),
        ((107, 49), (114, 42), (121, 37)),
        ((145, 61), (150, 91), (138, 117)),
        ((127, 137), (105, 151), (82, 156)),
    ],
)
WING_OPENING = curved_polygon(
    (93, 143),
    [
        ((100, 113), (111, 82), (132, 55)),
        ((118, 86), (107, 116), (100, 141)),
        ((98, 142), (95, 143), (93, 143)),
    ],
)
SEED = curved_polygon(
    (76, 179),
    [
        ((94, 179), (107, 190), (107, 204)),
        ((107, 218), (94, 228), (76, 228)),
        ((58, 228), (45, 218), (45, 204)),
        ((45, 190), (58, 179), (76, 179)),
    ],
)
SMALL_WING = curved_polygon(
    (82, 155),
    [
        ((79, 122), (83, 82), (101, 57)),
        ((107, 48), (114, 41), (121, 36)),
        ((146, 60), (151, 92), (139, 118)),
        ((128, 138), (105, 152), (82, 155)),
    ],
)
SMALL_SEED = curved_polygon(
    (76, 181),
    [
        ((95, 181), (108, 191), (108, 204)),
        ((108, 218), (95, 229), (76, 229)),
        ((57, 229), (44, 218), (44, 204)),
        ((44, 191), (57, 181), (76, 181)),
    ],
)
@dataclass
class Canvas:
    width: int
    height: int
    pixels: bytearray

    @classmethod
    def create(cls, width: int, height: int, color: Color = TRANSPARENT) -> Canvas:
        return cls(width, height, bytearray(color * (width * height)))

    def set_pixel(self, x: int, y: int, color: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 4
            self.pixels[offset : offset + 4] = bytes(color)

    def fill_polygon(self, points: list[Point], color: Color) -> None:
        minimum_y = max(0, int(min(point[1] for point in points)))
        maximum_y = min(self.height - 1, int(max(point[1] for point in points)) + 1)
        for y in range(minimum_y, maximum_y + 1):
            scan_y = y + 0.5
            intersections: list[float] = []
            for start, end in zip(points, points[1:] + points[:1], strict=True):
                if (start[1] <= scan_y < end[1]) or (end[1] <= scan_y < start[1]):
                    ratio = (scan_y - start[1]) / (end[1] - start[1])
                    intersections.append(start[0] + ratio * (end[0] - start[0]))
            intersections.sort()
            for left, right in zip(intersections[::2], intersections[1::2], strict=True):
                for x in range(max(0, int(left + 0.5)), min(self.width, int(right + 0.5))):
                    self.set_pixel(x, y, color)

    def thick_line(self, start: Point, end: Point, width: float, color: Color) -> None:
        radius = width / 2
        minimum_x = max(0, int(min(start[0], end[0]) - radius - 1))
        maximum_x = min(self.width - 1, int(max(start[0], end[0]) + radius + 1))
        minimum_y = max(0, int(min(start[1], end[1]) - radius - 1))
        maximum_y = min(self.height - 1, int(max(start[1], end[1]) + radius + 1))
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length_squared = delta_x**2 + delta_y**2
        for y in range(minimum_y, maximum_y + 1):
            for x in range(minimum_x, maximum_x + 1):
                if length_squared == 0:
                    ratio = 0.0
                else:
                    ratio = max(
                        0.0,
                        min(
                            1.0,
                            ((x + 0.5 - start[0]) * delta_x + (y + 0.5 - start[1]) * delta_y)
                            / length_squared,
                        ),
                    )
                nearest_x = start[0] + ratio * delta_x
                nearest_y = start[1] + ratio * delta_y
                if (x + 0.5 - nearest_x) ** 2 + (y + 0.5 - nearest_y) ** 2 <= radius**2:
                    self.set_pixel(x, y, color)

    def polyline(self, points: list[Point], width: float, color: Color) -> None:
        for start, end in pairwise(points):
            self.thick_line(start, end, width, color)


def transform(points: list[Point], x: float, y: float, scale: float) -> list[Point]:
    return [(x + point_x * scale, y + point_y * scale) for point_x, point_y in points]


def draw_mark(
    canvas: Canvas,
    *,
    x: float,
    y: float,
    size: float,
    background: Color,
    small: bool,
    seed_color: Color = CARBON,
    wing_color: Color = GREEN,
) -> None:
    scale = size / 240
    if small:
        canvas.fill_polygon(transform(SMALL_WING, x, y, scale), wing_color)
        canvas.fill_polygon(transform(SMALL_SEED, x, y, scale), seed_color)
        return
    canvas.fill_polygon(transform(WING, x, y, scale), wing_color)
    canvas.fill_polygon(transform(WING_OPENING, x, y, scale), background)
    canvas.fill_polygon(transform(SEED, x, y, scale), seed_color)


def downsample(source: Canvas, factor: int) -> Canvas:
    if factor == 1:
        return source
    output = Canvas.create(source.width // factor, source.height // factor)
    sample_count = factor * factor
    for output_y in range(output.height):
        for output_x in range(output.width):
            alpha_sum = 0
            red_sum = 0
            green_sum = 0
            blue_sum = 0
            for offset_y in range(factor):
                for offset_x in range(factor):
                    source_x = output_x * factor + offset_x
                    source_y = output_y * factor + offset_y
                    offset = (source_y * source.width + source_x) * 4
                    red, green, blue, alpha = source.pixels[offset : offset + 4]
                    alpha_sum += alpha
                    red_sum += red * alpha
                    green_sum += green * alpha
                    blue_sum += blue * alpha
            alpha = round(alpha_sum / sample_count)
            if alpha_sum:
                color = (
                    round(red_sum / alpha_sum),
                    round(green_sum / alpha_sum),
                    round(blue_sum / alpha_sum),
                    alpha,
                )
            else:
                color = TRANSPARENT
            output.set_pixel(output_x, output_y, color)
    return output


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def write_png(path: Path, canvas: Canvas) -> None:
    scanlines = b"".join(
        b"\0" + bytes(canvas.pixels[row * canvas.width * 4 : (row + 1) * canvas.width * 4])
        for row in range(canvas.height)
    )
    header = struct.pack(">IIBBBBB", canvas.width, canvas.height, 8, 6, 0, 0, 0)
    path.write_bytes(
        PNG_SIGNATURE
        + png_chunk(b"IHDR", header)
        + png_chunk(b"sRGB", b"\0")
        + png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + png_chunk(b"IEND", b"")
    )


STROKE_GLYPHS: dict[str, list[list[Point]]] = {
    "A": [[(0, 1), (0.5, 0), (1, 1)], [(0.22, 0.62), (0.78, 0.62)]],
    "R": [
        [(0, 1), (0, 0), (0.62, 0), (0.86, 0.18), (0.62, 0.48), (0, 0.48)],
        [(0.52, 0.48), (1, 1)],
    ],
    "e": [
        [
            (0.08, 0.55),
            (0.92, 0.55),
            (0.82, 0.24),
            (0.48, 0.15),
            (0.12, 0.34),
            (0.08, 0.65),
            (0.34, 0.88),
            (0.82, 0.82),
        ]
    ],
    "g": [
        [
            (0.9, 0.22),
            (0.55, 0.12),
            (0.18, 0.28),
            (0.1, 0.62),
            (0.36, 0.86),
            (0.84, 0.75),
            (0.84, 0.2),
            (0.84, 1.18),
            (0.55, 1.35),
            (0.2, 1.2),
        ]
    ],
    "i": [[(0.5, 0.25), (0.5, 1)], [(0.5, 0), (0.5, 0.02)]],
    "o": [
        [
            (0.5, 0.14),
            (0.18, 0.24),
            (0.08, 0.56),
            (0.22, 0.88),
            (0.58, 0.94),
            (0.9, 0.74),
            (0.9, 0.38),
            (0.7, 0.16),
            (0.5, 0.14),
        ]
    ],
    "p": [
        [(0.08, 1.35), (0.08, 0.18)],
        [
            (0.08, 0.28),
            (0.42, 0.12),
            (0.8, 0.24),
            (0.92, 0.58),
            (0.72, 0.88),
            (0.36, 0.88),
            (0.08, 0.72),
        ],
    ],
    "s": [
        [
            (0.88, 0.24),
            (0.58, 0.12),
            (0.22, 0.2),
            (0.12, 0.46),
            (0.36, 0.6),
            (0.72, 0.62),
            (0.9, 0.78),
            (0.7, 0.94),
            (0.28, 0.9),
            (0.1, 0.78),
        ]
    ],
}

PIXEL_GLYPHS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
}


def draw_wordmark(
    canvas: Canvas,
    text: str,
    x: float,
    y: float,
    height: float,
    *,
    prefix_color: Color = CARBON,
    suffix_color: Color = BRIGHT_GREEN,
) -> None:
    cursor = x
    stroke = height * 0.105
    for index, character in enumerate(text):
        width = height * (0.34 if character == "i" else 0.66)
        color = prefix_color if index < 4 else suffix_color
        for stroke_points in STROKE_GLYPHS[character]:
            points = [(cursor + px * width, y + py * height) for px, py in stroke_points]
            canvas.polyline(points, stroke, color)
        cursor += width + height * 0.16


def draw_pixel_text(canvas: Canvas, text: str, x: int, y: int, scale: int, color: Color) -> None:
    cursor = x
    for character in text.upper():
        if character == " ":
            cursor += scale * 4
            continue
        rows = PIXEL_GLYPHS[character]
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                if value == "1":
                    for offset_y in range(scale):
                        for offset_x in range(scale):
                            canvas.set_pixel(
                                cursor + column_index * scale + offset_x,
                                y + row_index * scale + offset_y,
                                color,
                            )
        cursor += scale * 6


def render_mark(size: int, *, small: bool) -> Canvas:
    factor = 4
    canvas = Canvas.create(size * factor, size * factor)
    draw_mark(
        canvas,
        x=0,
        y=0,
        size=size * factor,
        background=TRANSPARENT,
        small=small,
    )
    return downsample(canvas, factor)


def render_social_preview() -> Canvas:
    factor = 2
    canvas = Canvas.create(1280 * factor, 640 * factor, CARBON)
    draw_mark(
        canvas,
        x=96 * factor,
        y=104 * factor,
        size=360 * factor,
        background=CARBON,
        small=False,
        seed_color=WHITE,
    )
    draw_wordmark(
        canvas,
        "RepoAegis",
        500 * factor,
        205 * factor,
        84 * factor,
        prefix_color=WHITE,
    )
    draw_pixel_text(
        canvas,
        "A Governed Repository Maintenance Agent",
        506 * factor,
        350 * factor,
        3 * factor,
        MUTED,
    )
    canvas.thick_line(
        (506 * factor, 415 * factor), (1120 * factor, 415 * factor), 6 * factor, BRIGHT_GREEN
    )
    return downsample(canvas, factor)


def write_ico(output: Path, images: list[Path]) -> None:
    payloads = [image.read_bytes() for image in images]
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    entries: list[bytes] = []
    for payload in payloads:
        header_bytes = payload[:24]
        width, height = struct.unpack(">II", header_bytes[16:24])
        entries.append(
            struct.pack(
                "<BBBBHHII",
                0 if width == 256 else width,
                0 if height == 256 else height,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)
    output.write_bytes(header + b"".join(entries) + b"".join(payloads))


def build(root: Path) -> None:
    for required in (
        "repo-aegis-mark.svg",
        "repo-aegis-mark-small.svg",
        "repo-aegis-lockup.svg",
    ):
        if not (root / required).is_file():
            raise FileNotFoundError(root / required)
    for size in (16, 32, 48, 64, 256):
        output = root / f"repo-aegis-mark-{size}.png"
        write_png(output, render_mark(size, small=size < 32))
    write_ico(
        root / "favicon.ico",
        [root / f"repo-aegis-mark-{size}.png" for size in (16, 32, 48)],
    )
    (root / "repo-aegis-mark-48.png").unlink()
    write_png(root / "social-preview.png", render_social_preview())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset_directory", type=Path, nargs="?", default=Path("docs"))
    args = parser.parse_args()
    build(args.asset_directory.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
