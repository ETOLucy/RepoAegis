# RepoAegis Brand Assets

RepoAegis uses the single-wing samara seed. The editable SVG files are the source of truth; PNG
and ICO files are deterministic platform exports.

Use `repo-aegis-lockup.svg` on light backgrounds and `repo-aegis-lockup-dark.svg` on dark
backgrounds. Use the full mark at 32 px and above and the small mark at 16 or 24 px. Maintain clear
space equal to the seed body's width around the symbol.

Palette:

- Verification green: `#15966B`
- Carbon: `#171C1A`
- Mist: `#F4F7F5`
- White: `#FFFFFF`

Regenerate raster exports with `python scripts/build_brand_assets.py` and validate the complete
set with `python scripts/validate_logo_assets.py docs`.

These assets are original project artwork. They do not establish trademark registration or legal
clearance.
