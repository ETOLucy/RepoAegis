# RepoAegis Mark Optical Alignment Design

## Objective

Refine the approved single-leaf RepoAegis mark so the leaf no longer appears to lean away from the
seed. Preserve a restrained rightward gesture, the existing green/carbon palette, and the deliberate
separation between leaf and circular seed.

## Geometry

- Reduce the leaf's visual-axis angle to approximately 15--20 degrees right of vertical.
- Move the leaf root over the seed's upper region so their horizontal offset is at most one third of
  the current approximately 48-unit offset in the 240-unit viewBox.
- Keep the seed a clean closed oval/circle with no connector, stem, bump, or protrusion.
- Preserve two disconnected opaque components at 16, 32, 64, and 256 px.
- Retain an internal leaf opening at 32 px and above; the 16 px small variant may omit it.
- Keep all critical geometry inside the existing safe area and avoid new strokes or gradients.

## Asset Boundary

`scripts/build_brand_assets.py` is the raster source of truth. The SVG paths must use the same
control points as the renderer. Rebuild mark PNGs, favicon, and social preview; keep lockups and
reversed/monochrome variants geometrically consistent. README continues to show a standalone mark
and separate H1.

## Validation

Extend the brand test to measure leaf-root/seed alignment and preserve the existing two-component
assertion. Run the brand builder, logo validator, focused unit tests, and visual inspection at 16,
32, 64, and 256 px on light and dark backgrounds.
