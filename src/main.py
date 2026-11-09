"""
src/main.py — Main CLI and GUI application dispatcher for AppImageKit.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from .models.app import InstallMode, UpdateSource
from .models.database import DatabaseManager
from .services.installer import InstallerError, InstallerService
from .services.updater import UpdaterService
from .utils.paths import Paths


@click.group()
@click.version_option(version="0.1.0", prog_name="appimagekit")
def cli():
    """AppImageKit — Native Linux AppImage installer and manager."""
    pass


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


@cli.command("uninstall")
@click.argument("name_or_path")
def uninstall_cmd(name_or_path: str):
    """Uninstall an application and purge its desktop integration and files."""
    db = DatabaseManager()
    installer = InstallerService(db=db)
    app = db.get_app_by_name(name_or_path) or db.get_app_by_path(name_or_path)
    if not app:
        try:
            resolved_path = str(Path(name_or_path).resolve())
            app = db.get_app_by_path(resolved_path)
        except Exception:
            pass

    if not app:
        click.echo(f"Error: Application '{name_or_path}' not found in registry.", err=True)
        sys.exit(1)

    success = installer.uninstall(app)
    if success:
        click.echo(f"Successfully uninstalled {app.name}")
    else:
        click.echo(f"Failed to fully uninstall {app.name}", err=True)
        sys.exit(1)


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
    app = db.get_app_by_name(name_or_path) or db.get_app_by_path(name_or_path)
    if not app:
        try:
            resolved_path = str(Path(name_or_path).resolve())
            app = db.get_app_by_path(resolved_path)
        except Exception:
            pass

    if not app:
        click.echo(f"Error: Application '{name_or_path}' not found in registry.", err=True)
        sys.exit(1)

    if file_path:
        # Tier 4 manual local file in-place update
        try:
            updated_app = installer.update_in_place(app, file_path)
            click.echo(f"Successfully updated {updated_app.name} in-place to version {updated_app.version or 'latest'}")
        except Exception as e:
            click.echo(f"Error performing in-place update: {e}", err=True)
            sys.exit(1)
        return

    # Online update check & apply
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



def main():
    """CLI / GUI dispatch entrypoint."""
    if len(sys.argv) > 1:
        cli()
    else:
        # Default entrypoint: launch CLI or GUI when available
        try:
            from .application import AppImageKitApplication
            app = AppImageKitApplication()
            app.run(sys.argv)
        except (ImportError, Exception):
            cli()


if __name__ == "__main__":
    main()
