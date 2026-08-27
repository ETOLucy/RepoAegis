#!/usr/bin/env python3
"""Validate the complete RepoAegis GitHub brand asset set."""

from __future__ import annotations

import argparse
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
REQUIRED_SVGS = {
    "repo-aegis-mark.svg",
    "repo-aegis-mark-small.svg",
    "repo-aegis-mark-mono.svg",
    "repo-aegis-mark-reversed.svg",
    "repo-aegis-lockup.svg",
    "repo-aegis-lockup-dark.svg",
}
REQUIRED_PNGS = {
    "repo-aegis-mark-16.png": (16, 16),
    "repo-aegis-mark-32.png": (32, 32),
    "repo-aegis-mark-64.png": (64, 64),
    "repo-aegis-mark-256.png": (256, 256),
    "social-preview.png": (1280, 640),
}
FORBIDDEN_SVG_TAGS = {"script", "foreignObject", "iframe", "image", "text", "style"}


def local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def validate_svg(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - local SVGs; references are rejected below
    except (ET.ParseError, OSError) as exc:
        return [f"invalid SVG XML: {exc}"]
    if local_name(root.tag) != "svg" or not root.get("viewBox"):
        errors.append("SVG requires an svg root and viewBox")
    if not any(local_name(child.tag) == "title" for child in root):
        errors.append("SVG requires an accessible title")
    for element in root.iter():
        if local_name(element.tag) in FORBIDDEN_SVG_TAGS:
            errors.append(f"forbidden SVG element: {local_name(element.tag)}")
        for attribute, value in element.attrib.items():
            if local_name(attribute) in {"href", "src"}:
                errors.append(f"external or embedded reference in {local_name(attribute)}")
            if "url(" in value.lower() and not any(
                v in value.lower() for v in ("url(#",)
            ):
                errors.append("SVG URL reference is forbidden")
    return errors


def png_info(path: Path) -> tuple[int, int, int]:
    header = path.read_bytes()[:26]
    if len(header) < 26 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError("invalid PNG header")
    width, height = struct.unpack(">II", header[16:24])
    return width, height, header[25]


def ico_sizes(path: Path) -> set[tuple[int, int]]:
    data = path.read_bytes()
    if len(data) < 6:
        raise ValueError("invalid ICO header")
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or kind != 1 or len(data) < 6 + count * 16:
        raise ValueError("invalid ICO directory")
    sizes: set[tuple[int, int]] = set()
    for index in range(count):
        width, height = data[6 + index * 16 : 8 + index * 16]
        sizes.add((256 if width == 0 else width, 256 if height == 0 else height))
    return sizes


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    for name in sorted(REQUIRED_SVGS):
        path = root / name
        if not path.is_file():
            errors.append(f"missing {name}")
        else:
            errors.extend(f"{name}: {message}" for message in validate_svg(path))
    for name, expected in REQUIRED_PNGS.items():
        path = root / name
        if not path.is_file():
            errors.append(f"missing {name}")
            continue
        try:
            width, height, color_type = png_info(path)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
            continue
        if (width, height) != expected:
            errors.append(f"{name}: expected {expected}, found {(width, height)}")
        if name != "social-preview.png" and color_type not in {4, 6}:
            errors.append(f"{name}: mark PNG must retain alpha")
        if name == "social-preview.png" and path.stat().st_size >= 1_000_000:
            errors.append("social-preview.png: must be smaller than 1 MB")
    favicon = root / "favicon.ico"
    if not favicon.is_file():
        errors.append("missing favicon.ico")
    else:
        try:
            sizes = ico_sizes(favicon)
        except ValueError as exc:
            errors.append(f"favicon.ico: {exc}")
        else:
            required = {(16, 16), (32, 32), (48, 48)}
            if not required.issubset(sizes):
                errors.append(f"favicon.ico: missing frames {sorted(required - sizes)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset_directory", type=Path)
    args = parser.parse_args()
    errors = validate(args.asset_directory.resolve())
    for error in errors:
        print(f"ERROR: {error}")
    if not errors:
        print("RepoAegis brand assets passed validation")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
