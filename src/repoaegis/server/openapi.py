"""Export the OpenAPI document so the console can generate types without a running server.

uv run python -m repoaegis.server.openapi > web/openapi.json
uv run python -m repoaegis.server.openapi --check web/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from repoaegis.server.api import create_app
from repoaegis.server.config import Settings


def render() -> str:
    app = create_app(Settings(worker_enabled=False))
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", metavar="PATH", help="exit 1 if PATH differs from current schema"
    )
    args = parser.parse_args(argv)
    current = render()
    if args.check:
        on_disk = Path(args.check).read_text(encoding="utf-8")
        if on_disk != current:
            print(f"{args.check} is stale; regenerate it", file=sys.stderr)
            return 1
        return 0
    sys.stdout.write(current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
