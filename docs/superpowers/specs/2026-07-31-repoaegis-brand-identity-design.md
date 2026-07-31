# RepoAegis Brand Identity Design

Date: 2026-07-31

## Scope

This specification defines the public brand identity and GitHub-facing asset set for RepoAegis:

> RepoAegis: A Governed Repository Maintenance Agent

It covers the primary mark, horizontal lockup, color and monochrome variants, favicon and app icon,
README branding, social preview, repository metadata recommendations, asset placement, and visual
quality assurance.

This release does not rename the repository, Python distribution, Python import package, CLI
command, deployment targets, or integrations. The following compatibility identifiers remain
unchanged:

- GitHub repository: `ETOLucy/RepoAegis`
- Python distribution: `repo-aegis`
- Python import: `repo_maintenance_agent`
- CLI command: `repo-agent`

The ignored `private/` directory and its beginner guide are outside the publication surface and
must never be added to a commit.

## Brand Decision

RepoAegis uses a single-wing samara seed as its primary symbol. The mark is a natural, organic
silhouette rather than a literal diagram of governance, repositories, verification, containment,
or AI.

The symbol consists of:

1. a broad green wing with a continuous aerodynamic curve;
2. a restrained inner opening that lightens the wing membrane;
3. a dark seed body with an organic, slightly asymmetric profile;
4. a short curved connection between the seed and wing.

The complete silhouette must feel calm, intentional, and capable of motion without appearing fast,
aggressive, ornamental, or fragile.

The mark must not acquire:

- shields, locks, gates, check marks, brackets, code glyphs, robots, or initials;
- arrows, sprouts, leaves, growth charts, or other explanatory additions;
- gradients, glow, purple-blue effects, or decorative background shapes;
- angular flowchart paths, folded ribbons, mechanical facets, or modular system diagrams.

## Brand Family Boundary

RepoAegis belongs to an Aegis visual family. Its single-wing seed is specific to RepoAegis.
A related project may use a separately maintained paired-wing seed built from the same seed and wing
language.

RepoAegis must not import, duplicate, or publish another project's production assets. Shared visual
rules are documented here only to prevent accidental divergence; each repository owns and ships
only its own mark, lockups, exports, screenshots, and social preview.

## Geometry

The editable SVG is the source of truth. Its master coordinate system is `0 0 240 240`, with the
visible silhouette optically centered rather than mathematically forced into a symmetric box.

The approved master geometry is based on these paths:

```svg
<path
  d="M72 174C77 139 92 98 126 61C151 34 181 27 205 35
     C191 73 172 110 143 141C120 166 95 178 72 174Z"
/>
<path
  d="M87 160C105 124 128 89 166 55C142 93 125 129 116 159
     C106 165 96 168 87 160Z"
/>
<path
  d="M39 184C38 163 50 147 69 145C88 143 103 157 104 176
     C105 195 91 210 72 212C53 214 40 203 39 184Z"
/>
<path
  d="M65 151C80 138 93 126 104 112"
/>
```

Implementation may make optical corrections to control points, stroke thickness, or seed-to-wing
spacing, but must preserve the approved silhouette and receive visual comparison against this
master before replacement.

At 16 px and 24 px, use the small-size mark:

- omit the inner wing opening and connector stroke;
- preserve the green wing and dark seed as two clean masses;
- increase their optical separation if rasterization causes merging.

At 32 px and above, use the full mark with the inner opening. The connector may be omitted if the
target renderer produces an uneven or broken stroke.

## Color

Primary palette:

| Role | Value | Usage |
|---|---:|---|
| Verification green | `#15966B` | Wing, `Aegis` name segment, restrained status accent |
| Carbon | `#171C1A` | Seed, `Repo` name segment, primary text |
| Mist | `#F4F7F5` | Light presentation background and wing opening |
| White | `#FFFFFF` | Reversed mark and neutral background |

The primary mark uses verification green for the wing and carbon for the seed. The wing opening
must be transparent in production SVGs rather than filled with Mist or White.

Approved accessible text pairs:

- Carbon text on White or Mist;
- White text on Carbon;
- Verification green as a large wordmark segment on White or Mist.

Verification green is not approved for small body text on White.

## Wordmark And Lockup

The production wordmark uses Inter SemiBold as its open-source base. The repository must include the
applicable font license when a font binary is distributed. Production SVG lockups must convert the
wordmark to paths so rendering does not depend on locally installed fonts.

Lockup rules:

- display the canonical name as `RepoAegis`;
- color `Repo` Carbon and `Aegis` Verification green on light backgrounds;
- use White for the full name on Carbon backgrounds when the green segment lacks sufficient
  contrast;
- place the mark to the left of the name;
- align the mark optically with the wordmark cap height;
- keep the gap between mark and name equal to approximately one seed-body width;
- use normal letter spacing;
- do not connect, overlap, distort, italicize, or ligate the name.

The full product name may appear as a separate supporting line:

> A Governed Repository Maintenance Agent

It is not part of the core logo and must be omitted below 320 px lockup width.

## Deliverables

Production assets belong under `docs/brand/`:

```text
docs/brand/
  README.md
  repo-aegis-mark.svg
  repo-aegis-mark-small.svg
  repo-aegis-mark-mono.svg
  repo-aegis-mark-reversed.svg
  repo-aegis-lockup.svg
  repo-aegis-lockup-dark.svg
  repo-aegis-mark-16.png
  repo-aegis-mark-32.png
  repo-aegis-mark-64.png
  repo-aegis-mark-256.png
  favicon.ico
  social-preview.png
```

Implementation must also add `scripts/validate_logo_assets.py` and focused tests for its
deterministic dimension, file-size, SVG-safety, and required-file checks. The validator may be
adapted from the local logo workflow helper, but the repository copy must be self-contained and
must not depend on a user-specific path.

Requirements:

- SVG files use a transparent background and contain no external font, image, script, or stylesheet
  dependency.
- PNG exports use sRGB and retain transparency except for `social-preview.png`.
- `favicon.ico` contains at least 16 px, 32 px, and 48 px frames.
- `social-preview.png` is exactly 1280 x 640 px and smaller than 1 MB.
- Concepts, comparison boards, temporary renders, and browser-session files are not production
  assets and must not be committed.

## README And Social Preview

Place the horizontal lockup above the current README title content and preserve restrained CI,
Python, and license badges. Use meaningful alt text:

> RepoAegis single-wing seed logo and wordmark

The README must continue to state what RepoAegis does before installation details. Existing
architecture, evaluation, security, and console content remains factual and must not be replaced by
marketing copy.

The social preview uses:

- a Carbon background;
- the full-color single-wing mark at a large but uncropped size;
- the `RepoAegis` wordmark;
- the supporting line `A Governed Repository Maintenance Agent`;
- no screenshots, gradients, badge walls, decorative particles, or feature lists;
- a safe margin of at least 96 px on every edge.

## Repository Metadata Change Set

No repository or local-directory rename is planned.

Proposed GitHub metadata:

- repository: `ETOLucy/RepoAegis`;
- description: `A production-grade repository maintenance agent with governed tools, hybrid code search, sandboxed execution, evaluation gates, and human approval.`;
- homepage: leave unset;
- topics: `ai-agents`, `repository-maintenance`, `langgraph`, `fastapi`, `hybrid-search`,
  `sandbox`, `evaluation`, `python`.

Existing topics must be inspected before application. This proposal authorizes no remote mutation,
topic removal, upload, commit push, or repository setting change. Those operations require a
separate confirmed change set after local assets pass review.

## Validation

Before delivery:

1. inspect the mark at 16, 24, 32, 64, and 256 px;
2. inspect primary, monochrome, and reversed variants on light and dark backgrounds;
3. confirm the wing opening does not close unintentionally at 32 px;
4. confirm the seed and wing remain distinct at 16 px;
5. render every SVG and check for clipping, external dependencies, and unexpected fallback fonts;
6. confirm every PNG dimension, color mode, alpha behavior, and file size;
7. run `python scripts/validate_logo_assets.py docs/brand`;
8. render the README locally and inspect desktop and narrow layouts;
9. verify the GitHub repository page after push;
10. inspect the uploaded social preview rather than relying on its SVG composition source.

Automated checks supplement visual review; they do not replace it.

## Publication Safety

Before commit and before push:

- inspect tracked and untracked files;
- confirm `private/` remains ignored and untracked;
- inspect the exact commit diff and commit message;
- verify screenshots and exported images contain no private paths, tokens, credentials, or unrelated
  project content;
- run the repository privacy scanner;
- run the existing test, Ruff, and mypy baselines when README or packaging files change.

GitHub network operations must use the approved temporary process-level proxy without persisting
proxy configuration or documenting local infrastructure addresses in the repository.

## Legal Boundary

This design process does not establish trademark registration, legal clearance, or name
availability. GitHub name searches and repository listings are not legal trademark searches.
Commercial use should receive a professional similarity and trademark review in the relevant
jurisdictions and product classes.
