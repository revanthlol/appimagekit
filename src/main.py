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
