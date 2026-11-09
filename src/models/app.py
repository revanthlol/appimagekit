"""
AppImageKit — Domain Models.
Defines typed representations of AppImage applications and their metadata.
Zero GTK dependency ensures 100% testability in headless environments.
"""

from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Optional, Dict, Any

from ..utils.paths import InstallMode


class UpdateSource(str, Enum):
    """Cascading update mechanism tier."""
    NONE = "none"                      # No update mechanism
    APPIMAGEUPDATE = "appimageupdate"  # Tier 1: Embedded zsync delta update
    GITHUB = "github"                  # Tier 2: GitHub Releases API
    MANUAL_URL = "manual_url"          # Tier 3: Direct HTTP/HTTPS URL
    LOCAL_FILE = "local_file"          # Tier 4: Manual in-place local .AppImage


class SandboxType(str, Enum):
    """Supported sandbox technologies."""
    BUBBLEWRAP = "bubblewrap"
    FIREJAIL = "firejail"


@dataclass
class App:
    """
    Representation of an installed AppImage and its desktop environment integration.
    Contains 23 domain attributes plus database primary key 'id' and timestamps.
    """
    id: Optional[int] = None
    name: str = ""
    filename: str = ""
    install_path: str = ""
    desktop_entry: Optional[str] = None
    icon_path: Optional[str] = None
    version: Optional[str] = None
    size_bytes: int = 0
    install_mode: InstallMode = InstallMode.USER
    update_source: UpdateSource = UpdateSource.NONE
    update_url: Optional[str] = None
    zsync_url: Optional[str] = None
    category: str = "Utility"
    sandboxed: bool = False
    sandbox_type: Optional[SandboxType] = None
    autostart: bool = False
    checksum_sha256: str = ""
    exec_args: str = ""
    comment: str = ""
    terminal: bool = False
    startup_wm_class: Optional[str] = None
    pinned: bool = False
    hub_id: Optional[str] = None
    installed_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_launched_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "App":
        """
        Instantiate an App from an sqlite3.Row, dict, or mapping.
        Handles type coercions, enum resolutions, and default values.
        """
        if row is None:
            raise ValueError("Cannot instantiate App from None row")
        
        d = dict(row)

        # Helper for enum parsing with safe fallback
        raw_mode = d.get("install_mode", "user")
        if isinstance(raw_mode, InstallMode):
            install_mode = raw_mode
        else:
            raw_mode_str = str(getattr(raw_mode, "value", raw_mode)).lower() if raw_mode is not None else "user"
            if "." in raw_mode_str:
                raw_mode_str = raw_mode_str.split(".")[-1]
            install_mode = InstallMode(raw_mode_str) if raw_mode_str in InstallMode._value2member_map_ else InstallMode.USER

        raw_update = d.get("update_source", "none")
        if isinstance(raw_update, UpdateSource):
            update_source = raw_update
        else:
            raw_update_str = str(getattr(raw_update, "value", raw_update)).lower() if raw_update is not None else "none"
            if "." in raw_update_str:
                raw_update_str = raw_update_str.split(".")[-1]
            update_source = UpdateSource(raw_update_str) if raw_update_str in UpdateSource._value2member_map_ else UpdateSource.NONE

        raw_sandbox = d.get("sandbox_type")
        if isinstance(raw_sandbox, SandboxType):
            sandbox_type = raw_sandbox
        else:
            raw_sandbox_str = str(getattr(raw_sandbox, "value", raw_sandbox)).lower() if raw_sandbox else None
            if raw_sandbox_str and "." in raw_sandbox_str:
                raw_sandbox_str = raw_sandbox_str.split(".")[-1]
            sandbox_type = SandboxType(raw_sandbox_str) if raw_sandbox_str and raw_sandbox_str in SandboxType._value2member_map_ else None

        return cls(
            id=int(d["id"]) if d.get("id") is not None else None,
            name=str(d.get("name", "")),
            filename=str(d.get("filename", "")),
            install_path=str(d.get("install_path", "")),
            desktop_entry=d.get("desktop_entry"),
            icon_path=d.get("icon_path"),
            version=d.get("version"),
            size_bytes=int(d.get("size_bytes", 0)),
            install_mode=install_mode,
            update_source=update_source,
            update_url=d.get("update_url"),
            zsync_url=d.get("zsync_url"),
            category=str(d.get("category", "Utility")),
            sandboxed=bool(d.get("sandboxed", False)),
            sandbox_type=sandbox_type,
            autostart=bool(d.get("autostart", False)),
            checksum_sha256=str(d.get("checksum_sha256", "")),
            exec_args=str(d.get("exec_args", "")),
            comment=str(d.get("comment", "")),
            terminal=bool(d.get("terminal", False)),
            startup_wm_class=d.get("startup_wm_class"),
            pinned=bool(d.get("pinned", False)),
            hub_id=d.get("hub_id"),
            installed_at=d.get("installed_at"),
            updated_at=d.get("updated_at"),
            last_launched_at=d.get("last_launched_at"),
        )

    def to_db_dict(self) -> Dict[str, Any]:
        """
        Convert App instance to dictionary suitable for SQLite queries.
        Serializes enums to string values and booleans to integer 0/1.
        """
        data = asdict(self)
        mode_val = self.install_mode.value if hasattr(self.install_mode, "value") else str(self.install_mode)
        if "." in mode_val:
            mode_val = mode_val.split(".")[-1].lower()
        data["install_mode"] = mode_val

        update_val = self.update_source.value if hasattr(self.update_source, "value") else str(self.update_source)
        if "." in update_val:
            update_val = update_val.split(".")[-1].lower()
        data["update_source"] = update_val

        if self.sandbox_type is not None:
            sb_val = self.sandbox_type.value if hasattr(self.sandbox_type, "value") else str(self.sandbox_type)
            if "." in sb_val:
                sb_val = sb_val.split(".")[-1].lower()
            data["sandbox_type"] = sb_val
        else:
            data["sandbox_type"] = None

        data["sandboxed"] = 1 if self.sandboxed else 0
        data["autostart"] = 1 if self.autostart else 0
        data["terminal"] = 1 if self.terminal else 0
        data["pinned"] = 1 if self.pinned else 0
        return data

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert App instance to JSON-serializable dictionary for backups and CLI outputs.
        """
        data = asdict(self)
        mode_val = self.install_mode.value if hasattr(self.install_mode, "value") else str(self.install_mode)
        if "." in mode_val:
            mode_val = mode_val.split(".")[-1].lower()
        data["install_mode"] = mode_val

        update_val = self.update_source.value if hasattr(self.update_source, "value") else str(self.update_source)
        if "." in update_val:
            update_val = update_val.split(".")[-1].lower()
        data["update_source"] = update_val

        if self.sandbox_type is not None:
            sb_val = self.sandbox_type.value if hasattr(self.sandbox_type, "value") else str(self.sandbox_type)
            if "." in sb_val:
                sb_val = sb_val.split(".")[-1].lower()
            data["sandbox_type"] = sb_val
        else:
            data["sandbox_type"] = None

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "App":
        """
        Instantiate App from a dictionary (e.g. parsed JSON backup manifest).
        """
        return cls.from_row(data)
