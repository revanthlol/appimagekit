"""
AppImageKit — Native Linux AppImage installer and manager.
"""

import sys
import types

__version__ = "0.1.0"

# Expose src.cli for downstream tests and imports if not present
if "src.cli" not in sys.modules:
    cli_mod = types.ModuleType("src.cli")

    def _getattr(name):
        if name in ("cli", "main"):
            from .main import cli, main
            return {"cli": cli, "main": main}[name]
        raise AttributeError(f"module 'src.cli' has no attribute '{name}'")

    cli_mod.__getattr__ = _getattr
    sys.modules["src.cli"] = cli_mod


def __getattr__(name):
    if name in ("cli", "main"):
        from .main import cli, main
        return {"cli": cli, "main": main}[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = ["cli", "main", "__version__"]
