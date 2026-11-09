"""
src/main.py — Main CLI and GUI application dispatcher for AppImageKit.
"""

from __future__ import annotations

import logging
import sys
from .cli import cli

logger = logging.getLogger("appimagekit.main")


def main() -> None:
    """CLI / GUI dispatch entrypoint."""
    if len(sys.argv) > 1 and sys.argv[1] == "gui":
        # Explicit GUI command: appimagekit gui [files...]
        try:
            from .application import AppImageKitApplication
            app = AppImageKitApplication()
            gui_args = [sys.argv[0]] + sys.argv[2:]
            sys.exit(app.run(gui_args))
        except Exception as e:
            logger.error("Failed to launch GUI: %s", e)
            print(f"Error launching GUI: {e}", file=sys.stderr)
            sys.exit(1)
    elif len(sys.argv) > 1:
        # Standard CLI subcommands
        cli()
    else:
        # Default entrypoint without arguments: launch GUI when available, fallback to CLI
        try:
            from .application import AppImageKitApplication
            app = AppImageKitApplication()
            sys.exit(app.run(sys.argv))
        except (ImportError, Exception) as e:
            logger.info("GUI unavailable (%s), falling back to CLI help", e)
            cli()


if __name__ == "__main__":
    main()
