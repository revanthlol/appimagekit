"""
src/cli.py — Click Command Line Interface for AppImageKit.
Provides complete command suite: install, list, launch, update, uninstall,
info, verify, backup (export/import), and search.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

import click

from .models.app import App, InstallMode
from .models.database import DatabaseManager
from .services.autostart import AutostartManager
from .services.backup import BackupError, BackupManagerService
from .services.hub import HubCatalogError, HubCatalogService
from .services.installer import (
    AppAlreadyInstalledError,
    AppImageNotFoundError,
    ExecutionError,
    InstallerError,
    InstallerService,
    InvalidAppImageError,
    PermissionDeniedError,
)
from .services.integrity import IntegrityVerifierService
from .services.sandbox import SandboxDetectorService
from .services.updater import UpdaterService
from .utils.paths import Paths

logger = logging.getLogger("appimagekit.cli")


def _format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _resolve_app(db: DatabaseManager, name_or_path: str) -> Optional[App]:
    """Helper to find an application by name or path."""
    app = db.get_app_by_name(name_or_path) or db.get_app_by_path(name_or_path)
    if not app:
        try:
            resolved_path = str(Path(name_or_path).resolve())
            app = db.get_app_by_path(resolved_path)
        except Exception:
            pass
    return app


@click.group()
@click.version_option(version="0.1.0", prog_name="appimagekit")
def cli():
    """AppImageKit — Native Linux AppImage installer and manager."""
    pass


# ---------------------------------------------------------------------------
# 1. install
# ---------------------------------------------------------------------------
@cli.command("install")
@click.argument("appimage_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--system", is_flag=True, default=False, help="Install system-wide into /opt/appimages.")
@click.option("--category", default="Utility", help="Desktop menu category.")
def install_cmd(appimage_path: Path, system: bool, category: str):
    """Install an AppImage into managed storage."""
    db = DatabaseManager()
    installer = InstallerService(db=db)
    mode = InstallMode.SYSTEM if system else InstallMode.USER
    try:
        app = installer.install(appimage_path, mode=mode, category=category)
        click.echo(f"Successfully installed {app.name} ({app.filename}) to {app.install_path}")
    except InstallerError as e:
        click.echo(f"Error installing {appimage_path.name}: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 2. list
# ---------------------------------------------------------------------------
@cli.command("list")
@click.option("--category", "-c", help="Filter by desktop category.")
@click.option("--mode", "-m", type=click.Choice(["user", "system"], case_sensitive=False), help="Filter by install mode.")
@click.option("--pinned", is_flag=True, default=None, help="Filter pinned applications.")
@click.option("--query", "-q", help="Search query filter.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output in JSON format.")
def list_cmd(category: Optional[str], mode: Optional[str], pinned: Optional[bool], query: Optional[str], as_json: bool):
    """List installed applications."""
    db = DatabaseManager()
    apps = db.list_apps(category=category, mode=mode, pinned=pinned, query=query)
    if category and not apps:
        # Fallback for semicolon-separated categories in database (e.g. 'Graphics;')
        all_apps = db.list_apps(mode=mode, pinned=pinned, query=query)
        cat_lower = category.strip().lower()
        apps = [
            a for a in all_apps
            if cat_lower in [c.strip().lower() for c in a.category.split(";") if c.strip()]
        ]

    if as_json:
        data = [app.to_dict() for app in apps]
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if not apps:
        click.echo("No installed applications found.")
        return

    # Formatted table
    col_id = "ID"
    col_name = "Name"
    col_version = "Version"
    col_cat = "Category"
    col_mode = "Mode"
    col_size = "Size"
    col_path = "Install Path"

    w_id = max(len(col_id), max(len(str(a.id or '')) for a in apps))
    w_name = max(len(col_name), max(len(a.name) for a in apps))
    w_ver = max(len(col_version), max(len(a.version or '-') for a in apps))
    w_cat = max(len(col_cat), max(len(a.category) for a in apps))
    w_mode = max(len(col_mode), max(len(a.install_mode.value if hasattr(a.install_mode, 'value') else str(a.install_mode)) for a in apps))
    w_size = max(len(col_size), max(len(_format_size(a.size_bytes)) for a in apps))

    header = f"{col_id:<{w_id}}  {col_name:<{w_name}}  {col_version:<{w_ver}}  {col_cat:<{w_cat}}  {col_mode:<{w_mode}}  {col_size:<{w_size}}  {col_path}"
    sep = f"{'-'*w_id}  {'-'*w_name}  {'-'*w_ver}  {'-'*w_cat}  {'-'*w_mode}  {'-'*w_size}  {'-'*len(col_path)}"

    click.echo(header)
    click.echo(sep)
    for a in apps:
        mode_str = a.install_mode.value if hasattr(a.install_mode, 'value') else str(a.install_mode)
        row = (
            f"{str(a.id or '-'):<{w_id}}  "
            f"{a.name:<{w_name}}  "
            f"{(a.version or '-'):<{w_ver}}  "
            f"{a.category:<{w_cat}}  "
            f"{mode_str:<{w_mode}}  "
            f"{_format_size(a.size_bytes):<{w_size}}  "
            f"{a.install_path}"
        )
        click.echo(row)


# ---------------------------------------------------------------------------
# 3. info
# ---------------------------------------------------------------------------
@cli.command("info")
@click.argument("name_or_path")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output in JSON format.")
def info_cmd(name_or_path: str, as_json: bool):
    """Display detailed metadata for an installed application."""
    db = DatabaseManager()
    app = _resolve_app(db, name_or_path)

    if not app:
        click.echo(f"Error: Application '{name_or_path}' not found in registry.", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(app.to_dict(), indent=2, ensure_ascii=False))
        return

    mode_str = app.install_mode.value if hasattr(app.install_mode, 'value') else str(app.install_mode)
    update_str = app.update_source.value if hasattr(app.update_source, 'value') else str(app.update_source)
    sandbox_str = app.sandbox_type.value if app.sandbox_type and hasattr(app.sandbox_type, 'value') else str(app.sandbox_type or 'None')

    click.echo("=" * 60)
    click.echo(f"Application Details: {app.name}")
    click.echo("=" * 60)
    click.echo(f"  ID:                {app.id}")
    click.echo(f"  Name:              {app.name}")
    click.echo(f"  Filename:          {app.filename}")
    click.echo(f"  Version:           {app.version or 'Unknown'}")
    click.echo(f"  Category:          {app.category}")
    click.echo(f"  Install Mode:      {mode_str}")
    click.echo(f"  Size:              {_format_size(app.size_bytes)} ({app.size_bytes} bytes)")
    click.echo(f"  Install Path:      {app.install_path}")
    click.echo(f"  Desktop Entry:     {app.desktop_entry or 'None'}")
    click.echo(f"  Icon Path:         {app.icon_path or 'None'}")
    click.echo(f"  Update Source:     {update_str}")
    click.echo(f"  Update URL:        {app.update_url or 'None'}")
    click.echo(f"  Zsync URL:         {app.zsync_url or 'None'}")
    click.echo(f"  Sandboxed:         {'Yes' if app.sandboxed else 'No'} ({sandbox_str})")
    click.echo(f"  Autostart:         {'Yes' if app.autostart else 'No'}")
    click.echo(f"  Pinned:            {'Yes' if app.pinned else 'No'}")
    click.echo(f"  SHA256 Checksum:   {app.checksum_sha256}")
    click.echo(f"  Comment:           {app.comment or 'None'}")
    click.echo(f"  Exec Arguments:    {app.exec_args or 'None'}")
    click.echo(f"  Terminal:          {'Yes' if app.terminal else 'No'}")
    click.echo(f"  Installed At:      {app.installed_at or 'Unknown'}")
    click.echo(f"  Updated At:        {app.updated_at or 'Unknown'}")


# ---------------------------------------------------------------------------
# 4. launch
# ---------------------------------------------------------------------------
@cli.command("launch", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.argument("name_or_path")
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def launch_cmd(name_or_path: str, extra_args: tuple[str, ...]):
    """Launch an installed application."""
    db = DatabaseManager()
    installer = InstallerService(db=db)
    app = _resolve_app(db, name_or_path)

    if not app:
        click.echo(f"Error: Application '{name_or_path}' not found in registry.", err=True)
        sys.exit(1)

    try:
        proc = installer.launch(app, extra_args=list(extra_args) if extra_args else None)
        click.echo(f"Launched {app.name} (PID: {proc.pid})")
    except Exception as e:
        click.echo(f"Failed to launch {app.name}: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 5. update
# ---------------------------------------------------------------------------
@cli.command("update")
@click.argument("name_or_path", required=False, default=None)
@click.option(
    "--file",
    "-f",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Local .AppImage file for in-place update.",
)
@click.option("--all", "update_all", is_flag=True, default=False, help="Update all registered applications.")
@click.option("--check-only", is_flag=True, default=False, help="Check for updates without downloading or applying them.")
def update_cmd(name_or_path: Optional[str], file_path: Optional[Path], update_all: bool, check_only: bool):
    """Check for or apply updates for installed applications."""
    db = DatabaseManager()
    installer = InstallerService(db=db)
    updater = UpdaterService(db=db, installer=installer)

    if not name_or_path and not update_all:
        click.echo("Error: Please specify an application name/path or provide --all.", err=True)
        sys.exit(1)

    if update_all and file_path:
        click.echo("Error: --file cannot be combined with --all.", err=True)
        sys.exit(1)

    if update_all:
        apps = db.list_apps()
        if not apps:
            click.echo("No installed applications found.")
            return

        if check_only:
            results = updater.check_all_updates(apps)
            updates_found = 0
            for app in apps:
                info = results.get(app.id)
                if info and info.available:
                    click.echo(f"Update available for {app.name}: {info.current_version or 'unknown'} -> {info.latest_version or 'unknown'}")
                    updates_found += 1
                else:
                    click.echo(f"{app.name} is up to date.")
            click.echo(f"Check complete: {updates_found} update(s) available.")
        else:
            updated = updater.apply_all_updates(apps)
            click.echo(f"Batch update complete: updated {len(updated)} application(s).")
        return

    # Single app update
    app = _resolve_app(db, name_or_path)
    if not app:
        click.echo(f"Error: Application '{name_or_path}' not found in registry.", err=True)
        sys.exit(1)

    if file_path:
        try:
            updated_app = installer.update_in_place(app, file_path)
            click.echo(f"Successfully updated {updated_app.name} in-place to version {updated_app.version or 'latest'}")
        except Exception as e:
            click.echo(f"Error performing in-place update: {e}", err=True)
            sys.exit(1)
        return

    info = updater.check_update(app)
    if check_only:
        if info.available:
            click.echo(f"Update available for {app.name}: {info.current_version or 'current'} -> {info.latest_version or 'latest'}")
        else:
            click.echo(f"{app.name} is up to date ({app.version or 'unknown version'}).")
        return

    if not info.available:
        click.echo(f"{app.name} is already up to date ({app.version or 'unknown version'}).")
        return

    try:
        updated_app = updater.apply_update(app, info)
        click.echo(f"Successfully updated {updated_app.name} to version {info.latest_version or 'latest'}")
    except Exception as e:
        click.echo(f"Failed to apply update for {app.name}: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 6. uninstall
# ---------------------------------------------------------------------------
@cli.command("uninstall")
@click.argument("name_or_path")
def uninstall_cmd(name_or_path: str):
    """Uninstall an application and purge its desktop integration and files."""
    db = DatabaseManager()
    installer = InstallerService(db=db)
    app = _resolve_app(db, name_or_path)

    if not app:
        click.echo(f"Error: Application '{name_or_path}' not found in registry.", err=True)
        sys.exit(1)

    success = installer.uninstall(app)
    if success:
        click.echo(f"Successfully uninstalled {app.name}")
    else:
        click.echo(f"Failed to fully uninstall {app.name}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 7. verify
# ---------------------------------------------------------------------------
@cli.command("verify")
@click.argument("name_or_path", required=False, default=None)
@click.option("--all", "verify_all", is_flag=True, default=False, help="Verify integrity of all installed applications.")
def verify_cmd(name_or_path: Optional[str], verify_all: bool):
    """Verify SHA256 integrity of installed AppImages against registry checksums."""
    db = DatabaseManager()
    verifier = IntegrityVerifierService(db=db)

    if not name_or_path and not verify_all:
        click.echo("Error: Please specify an application name/path or provide --all.", err=True)
        sys.exit(1)

    if verify_all:
        apps = db.list_apps()
        if not apps:
            click.echo("No installed applications found.")
            return

        failed_count = 0
        for app in apps:
            details = verifier.verify_with_details(app)
            if details["valid"]:
                click.echo(f"Integrity PASSED: {app.name} ({details['actual_checksum'][:12]}...)")
            else:
                click.echo(f"Integrity FAILED / MISMATCH: {app.name} - {details['error']}", err=True)
                failed_count += 1

        if failed_count > 0:
            click.echo(f"Verification complete: {failed_count} application(s) failed integrity check.", err=True)
            sys.exit(1)
        else:
            click.echo(f"Verification complete: all {len(apps)} application(s) passed integrity check.")
        return

    # Single app verification
    app = _resolve_app(db, name_or_path)
    if not app:
        click.echo(f"Error: Application '{name_or_path}' not found in registry.", err=True)
        sys.exit(1)

    details = verifier.verify_with_details(app)
    if details["valid"]:
        click.echo(f"Integrity check PASSED for {app.name} (SHA256: {details['actual_checksum']})")
    else:
        click.echo(f"Integrity check FAILED / MISMATCH for {app.name}: {details['error']}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 8. backup
# ---------------------------------------------------------------------------
@cli.group("backup")
def backup_group():
    """Backup and restore application manifests and bundles."""
    pass


@backup_group.command("export")
@click.argument("destination", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--bundle", is_flag=True, default=False, help="Export as full archive bundle (.tar.gz).")
def backup_export_cmd(destination: Path, bundle: bool):
    """Export installed applications to a JSON manifest or archive bundle."""
    db = DatabaseManager()
    backup_mgr = BackupManagerService(db=db)
    is_bundle = bundle or str(destination).endswith(".tar.gz") or str(destination).endswith(".tgz")

    try:
        if is_bundle:
            out = backup_mgr.export_bundle(destination)
            click.echo(f"Successfully exported bundle to {out}")
        else:
            out = backup_mgr.export_manifest(destination)
            click.echo(f"Successfully exported manifest to {out}")
    except BackupError as e:
        click.echo(f"Backup export failed: {e}", err=True)
        sys.exit(1)


@backup_group.command("import")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--bundle", is_flag=True, default=False, help="Import from full archive bundle (.tar.gz).")
def backup_import_cmd(source: Path, bundle: bool):
    """Import and restore applications from a JSON manifest or archive bundle."""
    db = DatabaseManager()
    backup_mgr = BackupManagerService(db=db)
    is_bundle = bundle or str(source).endswith(".tar.gz") or str(source).endswith(".tgz")

    try:
        if is_bundle:
            restored = backup_mgr.import_bundle(source)
        else:
            restored = backup_mgr.import_manifest(source)
        click.echo(f"Successfully imported {len(restored)} application(s) from {source}")
    except BackupError as e:
        click.echo(f"Backup import failed: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 9. search
# ---------------------------------------------------------------------------
@cli.command("search")
@click.argument("query")
@click.option("--category", "-c", help="Filter by category.")
@click.option("--feed-url", help="Custom AppImage Hub feed URL.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output in JSON format.")
def search_cmd(query: str, category: Optional[str], feed_url: Optional[str], as_json: bool):
    """Search AppImage Hub catalog for available applications."""
    hub_svc = HubCatalogService(feed_url=feed_url)
    try:
        results = hub_svc.search(query=query, category=category)
    except HubCatalogError as e:
        click.echo(f"Hub search failed: {e}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if not results:
        click.echo(f"No applications found matching query '{query}'.")
        return

    click.echo(f"Found {len(results)} matching application(s) in AppImage Hub:\n")
    for item in results:
        name = item.get("name", "Unknown")
        desc = item.get("description", "No description available.")
        categories = ", ".join(item.get("categories", [])) or "Utility"
        license_str = item.get("license") or "Unknown"

        download_link = None
        for link in item.get("links", []):
            if link.get("type") == "Download" or link.get("url", "").endswith(".AppImage"):
                download_link = link.get("url")
                break

        click.echo(f"• {name} [{categories}] (License: {license_str})")
        click.echo(f"  {desc}")
        if download_link:
            click.echo(f"  Download: {download_link}")
        click.echo()
