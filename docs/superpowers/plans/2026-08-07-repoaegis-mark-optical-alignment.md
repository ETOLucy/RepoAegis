# RepoAegis Mark Optical Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optically align the RepoAegis leaf and seed while preserving a subtle rightward gesture and clean component separation at repository-icon sizes.

**Architecture:** Update one shared set of Bezier control points in the SVG variants and standard-library raster builder, then regenerate every derived bitmap. Extend the existing brand contract tests so future edits cannot restore the large horizontal offset or reconnect the seed and leaf.

**Tech Stack:** SVG, Python 3.12 standard-library raster renderer, pytest, Excalidraw-independent repository assets.

## Global Constraints

- Leaf visual axis remains approximately 15--20 degrees right of vertical.
- Leaf-root to seed-center horizontal offset is no more than 16 viewBox units.
- Seed remains a closed component with no connector or protrusion.
- Mark remains exactly two opaque components at 16, 32, 64, and 256 px.
- Preserve `#15966B` green, `#171C1A` carbon, monochrome, reversed, and transparent variants.
- README keeps the standalone mark separate from the `RepoAegis` H1.

---

### Task 1: Align Source Geometry And Regenerate Assets

**Files:**
- Modify: `scripts/build_brand_assets.py`
- Modify: `docs/repo-aegis-mark.svg`
- Modify: `docs/repo-aegis-mark-small.svg`
- Modify: `docs/repo-aegis-mark-mono.svg`
- Modify: `docs/repo-aegis-mark-reversed.svg`
- Modify: `docs/repo-aegis-lockup.svg`
- Modify: `docs/repo-aegis-lockup-dark.svg`
- Modify: `tests/unit/brand/test_logo_assets.py`
- Regenerate: `docs/repo-aegis-mark-{16,32,64,256}.png`, `docs/favicon.ico`, `docs/social-preview.png`

**Interfaces:**
- Consumes: `WING`, `WING_OPENING`, `SEED`, `SMALL_WING`, and `SMALL_SEED` point lists.
- Produces: the existing `render_mark(size: int, *, small: bool) -> Canvas` output contract and asset filenames without API changes.

- [ ] Add a failing geometry test that parses the primary SVG, verifies two closed paths, and asserts the leaf root is horizontally within 16 viewBox units of the seed center.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests/unit/brand/test_logo_assets.py -q` and confirm the new alignment assertion fails on the old 48-unit offset.
- [ ] Redraw the leaf and seed Bezier points with a 15--20 degree rightward leaf axis, a clean closed seed, and at least two-component separation at 16 px; mirror the geometry in all SVG variants.
- [ ] Run `.venv\Scripts\python.exe scripts/build_brand_assets.py docs` to regenerate raster assets.
- [ ] Run `.venv\Scripts\python.exe scripts/validate_logo_assets.py docs` and the focused brand tests.
- [ ] Inspect 16, 32, 64, and 256 px marks plus the social preview on light and dark backgrounds; fix any clipping, merging, or unbalanced optical weight.
- [ ] Run `git diff --check` and record the final changed asset list without pushing.
