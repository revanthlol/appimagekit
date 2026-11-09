"""
src/services/sandbox.py — Sandbox detection service for AppImageKit.
Inspects applications for Bubblewrap (bwrap) and Firejail sandboxing indicators,
configuration profiles, and runtime wrappers.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import List, Optional, Tuple, Union

from ..models.app import App, SandboxType

logger = logging.getLogger("appimagekit.sandbox")


class SandboxDetectorService:
    """
    Detects Bubblewrap (bwrap) and Firejail sandboxing configurations for AppImages.
    Checks:
    1. App's configured sandbox attributes.
    2. Desktop entry Exec line prefixes (e.g. 'firejail', 'bwrap').
    3. Application exec_args for sandbox indicators.
    4. Firejail profile files in standard directories (~/.config/firejail, /etc/firejail).
    """

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.user_firejail_dir = (
            config_dir / "firejail" if config_dir else Path.home() / ".config" / "firejail"
        )
        self.system_firejail_dir = Path("/etc/firejail")

    def detect(self, app: App) -> Tuple[bool, Optional[SandboxType]]:
        """
        Detect whether an App is configured for sandboxing.
        Returns (is_sandboxed, sandbox_type).
        """
        # 1. Check explicit app model fields
        if app.sandboxed and app.sandbox_type:
            return True, app.sandbox_type

        # 2. Check desktop entry Exec line if desktop entry exists
        if app.desktop_entry:
            desktop_path = Path(app.desktop_entry)
            if desktop_path.exists():
                is_sb, sb_type = self.detect_desktop_file(desktop_path)
                if is_sb:
                    return True, sb_type

        # 3. Check exec_args
        if app.exec_args:
            is_sb, sb_type = self.detect_command_string(app.exec_args)
            if is_sb:
                return True, sb_type

        # 4. Check for Firejail profiles matching app name or binary filename
        if self.has_firejail_profile(app.name) or (app.filename and self.has_firejail_profile(app.filename)):
            return True, SandboxType.FIREJAIL

        return False, None

    def detect_command_string(self, cmd_str: str) -> Tuple[bool, Optional[SandboxType]]:
        """Check if a command string contains bwrap or firejail invocations."""
        tokens = cmd_str.strip().split()
        if not tokens:
            return False, None

        for token in tokens:
            base = os.path.basename(token).lower()
            if base == "firejail" or base.startswith("firejail"):
                return True, SandboxType.FIREJAIL
            if base in ("bwrap", "bubblewrap") or base.startswith("bwrap"):
                return True, SandboxType.BUBBLEWRAP

        # Regex fallback
        if re.search(r'\b(firejail)\b', cmd_str, re.IGNORECASE):
            return True, SandboxType.FIREJAIL
        if re.search(r'\b(bwrap|bubblewrap)\b', cmd_str, re.IGNORECASE):
            return True, SandboxType.BUBBLEWRAP

        return False, None

    def detect_desktop_file(self, desktop_file: Union[str, Path]) -> Tuple[bool, Optional[SandboxType]]:
        """Parse desktop file Exec line and check for sandboxing."""
        path = Path(desktop_file)
        if not path.is_file():
            return False, None

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("Exec="):
                    exec_val = line[5:].strip()
                    return self.detect_command_string(exec_val)
        except Exception as e:
            logger.warning("Error reading desktop file %s: %s", path, e)

        return False, None

    def has_firejail_profile(self, identifier: str) -> bool:
        """Check if a Firejail profile exists for the given name in user or system dirs."""
        if not identifier:
            return False

        clean_id = re.sub(r'[^a-zA-Z0-9_\-\.]', '', identifier).lower()
        candidates = [
            f"{clean_id}.profile",
            f"{identifier}.profile",
        ]
        if identifier.lower().endswith(".appimage"):
            base_id = identifier[:-9]
            candidates.append(f"{base_id}.profile")

        search_dirs = [self.user_firejail_dir, self.system_firejail_dir]
        for sdir in search_dirs:
            if sdir and sdir.exists():
                for candidate in candidates:
                    if (sdir / candidate).exists():
                        return True
        return False

    @staticmethod
    def is_bubblewrap_installed() -> bool:
        """Check if bubblewrap (bwrap) binary is available on the system PATH."""
        return shutil.which("bwrap") is not None

    @staticmethod
    def is_firejail_installed() -> bool:
        """Check if firejail binary is available on the system PATH."""
        return shutil.which("firejail") is not None

    def wrap_command(
        self,
        app: App,
        sandbox_type: SandboxType,
        extra_bwrap_flags: Optional[List[str]] = None,
    ) -> List[str]:
        """Generate sandboxed command prefix for launching an AppImage."""
        install_path = str(Path(app.install_path).resolve())
        if sandbox_type == SandboxType.FIREJAIL:
            return ["firejail", "--appimage", install_path]
        elif sandbox_type == SandboxType.BUBBLEWRAP:
            cmd = [
                "bwrap",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/bin", "/bin",
                "--proc", "/proc",
                "--dev", "/dev",
                "--ro-bind", install_path, install_path,
            ]
            if Path("/lib64").exists():
                cmd.extend(["--ro-bind", "/lib64", "/lib64"])
            if extra_bwrap_flags:
                cmd.extend(extra_bwrap_flags)
            cmd.append(install_path)
            return cmd
        return [install_path]
