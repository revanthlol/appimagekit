"""
src/main.py — Main CLI and GUI application dispatcher for AppImageKit.
"""

from __future__ import annotations

import sys
from .cli import cli


def main():
    """CLI / GUI dispatch entrypoint."""
    if len(sys.argv) > 1:
        cli()
    else:
        # Default entrypoint: launch GUI when available, fallback to CLI
        try:
            from .application import AppImageKitApplication
            app = AppImageKitApplication()
            app.run(sys.argv)
        except (ImportError, Exception):
            cli()


if __name__ == "__main__":
    main()
