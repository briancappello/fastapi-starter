#!/usr/bin/env python
"""Wrapper script to run alembic with dynamically resolved version_locations.

This resolves the fin package migrations path at runtime, so it works whether
fin is installed in editable mode or as a regular package.

Usage:
    python -m db history
    python -m db upgrade head
    python -m db revision --autogenerate -m "add column"

Or via uv:
    uv run python -m db history
"""

import sys

from pathlib import Path

from alembic.config import CommandLine, Config


def main() -> None:
    # Resolve version locations dynamically
    local_versions = Path(__file__).parent / "versions"
    version_locations = [local_versions]

    cli = CommandLine()
    options = cli.parser.parse_args(sys.argv[1:])

    # If no subcommand provided, print help and exit
    if not hasattr(options, "cmd") or options.cmd is None:
        cli.parser.print_help()
        sys.exit(0)

    # Load config file (defaults to alembic.ini)
    config_file = getattr(options, "config", None) or "alembic.ini"
    cfg = Config(config_file)

    # Override version_locations with dynamically resolved paths
    cfg.set_main_option(
        "version_locations",
        " ".join(str(loc) for loc in version_locations),
    )

    cli.run_cmd(cfg, options)


if __name__ == "__main__":
    main()
