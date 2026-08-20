import re
import shutil
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

from scripts.build_brand_assets import CARBON, build, render_mark, render_social_preview
from scripts.validate_logo_assets import validate

ROOT = Path(__file__).parents[3]


def test_committed_brand_asset_set_is_complete_and_safe() -> None:
    assert validate(ROOT / "docs") == []


def test_validator_rejects_external_svg_reference(tmp_path: Path) -> None:
    brand = tmp_path / "brand"
    brand.mkdir()
    source = ROOT / "docs"
    for path in source.iterdir():
        if path.is_file():
            (brand / path.name).write_bytes(path.read_bytes())
    (brand / "repo-aegis-mark.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        '<title>bad</title><image href="https://example.invalid/a.png"/></svg>',
        encoding="utf-8",
    )

    errors = validate(brand)

    assert any("forbidden SVG element" in error for error in errors)
    assert any("external or embedded reference" in error for error in errors)


def test_builder_generates_rasters_without_an_external_renderer(tmp_path: Path) -> None:
    source = ROOT / "docs"
    brand = tmp_path / "brand"
    brand.mkdir()
    for path in source.glob("*.svg"):
        shutil.copyfile(path, brand / path.name)

    build(brand)

    assert validate(brand) == []


def test_dark_social_preview_keeps_repo_prefix_and_seed_visible() -> None:
    preview = render_social_preview()

    def has_non_background_pixel(left: int, top: int, right: int, bottom: int) -> bool:
        for y in range(top, bottom):
            for x in range(left, right):
                offset = (y * preview.width + x) * 4
                if tuple(preview.pixels[offset : offset + 4]) != CARBON:
                    return True
        return False

    assert has_non_background_pixel(500, 205, 735, 310)
    assert has_non_background_pixel(145, 315, 270, 445)


def _opaque_component_count(*, pixels: bytearray, width: int, height: int) -> int:
    opaque = {
        (x, y) for y in range(height) for x in range(width) if pixels[(y * width + x) * 4 + 3] > 0
    }
    components = 0
    while opaque:
        components += 1
        queue = deque([opaque.pop()])
        while queue:
            x, y = queue.popleft()
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in opaque:
                    opaque.remove(neighbor)
                    queue.append(neighbor)
    return components


def test_seed_is_closed_and_visually_separated_from_wing() -> None:
    root = ET.parse(  # noqa: S314 - parses a committed local SVG fixture
        ROOT / "docs" / "repo-aegis-mark.svg"
    ).getroot()
    paths = [element for element in root if element.tag.endswith("path")]
    assert paths
    assert all(path.attrib["d"].rstrip().endswith("Z") for path in paths)

    for size in (16, 32, 64, 240, 256):
        mark = render_mark(size, small=size < 32)
        assert (
            _opaque_component_count(
                pixels=mark.pixels,
                width=mark.width,
                height=mark.height,
            )
            == 2
        )


def test_wing_root_stays_optically_aligned_over_seed() -> None:
    root = ET.parse(  # noqa: S314 - parses a committed local SVG fixture
        ROOT / "docs" / "repo-aegis-mark.svg"
    ).getroot()
    paths = [element for element in root if element.tag.endswith("path")]
    assert len(paths) == 2
    assert all(path.attrib["d"].rstrip().endswith("Z") for path in paths)

    wing_root_match = re.match(r"M(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", paths[0].attrib["d"])
    assert wing_root_match is not None
    wing_root_x = float(wing_root_match.group(1))
    seed_coordinates = [
        float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", paths[1].attrib["d"])
    ]
    seed_x_coordinates = seed_coordinates[::2]
    seed_center_x = (min(seed_x_coordinates) + max(seed_x_coordinates)) / 2

    assert abs(wing_root_x - seed_center_x) <= 16
