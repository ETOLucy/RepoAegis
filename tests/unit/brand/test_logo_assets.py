import shutil
from pathlib import Path

from scripts.build_brand_assets import CARBON, build, render_social_preview
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
