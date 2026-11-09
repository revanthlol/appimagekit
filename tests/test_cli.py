"""
tests/test_cli.py — Unit test suite for Click CLI Suite (Milestone 4).
Tests: install, list, info, launch, update, uninstall, verify, backup, and search.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.cli import cli
from src.models.app import App, InstallMode
from src.models.database import DatabaseManager
from src.services.installer import InstallerService
from tests.conftest import create_mock_appimage


@pytest.fixture
def cli_isolated(temp_workspace):
    """Provides a CliRunner inside the sandboxed temp_workspace."""
    runner = CliRunner()
    return runner


# ---------------------------------------------------------------------------
# 1. Base CLI and Help / Version
# ---------------------------------------------------------------------------

def test_cli_help_and_version(cli_isolated):
    res_help = cli_isolated.invoke(cli, ["--help"])
    assert res_help.exit_code == 0
    assert "AppImageKit" in res_help.output
    assert "install" in res_help.output
    assert "list" in res_help.output
    assert "update" in res_help.output
    assert "uninstall" in res_help.output

    res_ver = cli_isolated.invoke(cli, ["--version"])
    assert res_ver.exit_code == 0
    assert "0.1.0" in res_ver.output


# ---------------------------------------------------------------------------
# 2. install Command
# ---------------------------------------------------------------------------

def test_cli_install(temp_workspace, cli_isolated):
    src = create_mock_appimage(temp_workspace["root"] / "MyTool.AppImage", name="MyTool", version="1.0.0")

    result = cli_isolated.invoke(cli, ["install", str(src), "--category", "Development"])
    assert result.exit_code == 0
    assert "successfully installed" in result.output.lower()

    db = DatabaseManager(temp_workspace["db_path"])
    app = db.get_app_by_name("MyTool")
    assert app is not None
    assert app.category == "Development"


def test_cli_install_nonexistent_file(cli_isolated):
    result = cli_isolated.invoke(cli, ["install", "/tmp/does_not_exist.AppImage"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 3. list Command
# ---------------------------------------------------------------------------

def test_cli_list_empty_and_table(temp_workspace, cli_isolated):
    # Empty
    res_empty = cli_isolated.invoke(cli, ["list"])
    assert res_empty.exit_code == 0
    assert "No installed applications found" in res_empty.output

    # Add apps
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db=db)
    installer.install(create_mock_appimage(temp_workspace["root"] / "AppOne.AppImage", name="AppOne", category="Graphics"))
    installer.install(create_mock_appimage(temp_workspace["root"] / "AppTwo.AppImage", name="AppTwo", category="Development"))

    # Table list
    res = cli_isolated.invoke(cli, ["list"])
    assert res.exit_code == 0
    assert "AppOne" in res.output
    assert "AppTwo" in res.output
    assert "Graphics" in res.output

    # JSON list
    res_json = cli_isolated.invoke(cli, ["list", "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert len(data) == 2
    names = [a["name"] for a in data]
    assert "AppOne" in names and "AppTwo" in names

    # Filter by category
    res_cat = cli_isolated.invoke(cli, ["list", "--category", "Graphics"])
    assert res_cat.exit_code == 0
    assert "AppOne" in res_cat.output
    assert "AppTwo" not in res_cat.output

    # Query filter
    res_q = cli_isolated.invoke(cli, ["list", "--query", "Two"])
    assert res_q.exit_code == 0
    assert "AppTwo" in res_q.output
    assert "AppOne" not in res_q.output


# ---------------------------------------------------------------------------
# 4. info Command
# ---------------------------------------------------------------------------

def test_cli_info(temp_workspace, cli_isolated):
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db=db)
    installer.install(create_mock_appimage(temp_workspace["root"] / "InfoApp.AppImage", name="InfoApp", version="3.2.1"))

    # Human-readable info
    res = cli_isolated.invoke(cli, ["info", "InfoApp"])
    assert res.exit_code == 0
    assert "InfoApp" in res.output
    assert "3.2.1" in res.output
    assert "Install Path:" in res.output

    # JSON info
    res_json = cli_isolated.invoke(cli, ["info", "InfoApp", "--json"])
    assert res_json.exit_code == 0
    info_dict = json.loads(res_json.output)
    assert info_dict["name"] == "InfoApp"
    assert info_dict["version"] == "3.2.1"

    # Nonexistent
    res_err = cli_isolated.invoke(cli, ["info", "Ghost"])
    assert res_err.exit_code != 0


# ---------------------------------------------------------------------------
# 5. launch Command
# ---------------------------------------------------------------------------

def test_cli_launch(temp_workspace, cli_isolated):
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db=db)
    installer.install(create_mock_appimage(temp_workspace["root"] / "LaunchApp.AppImage", name="LaunchApp"))

    res = cli_isolated.invoke(cli, ["launch", "LaunchApp", "--flag"])
    assert res.exit_code == 0
    assert "Launched LaunchApp" in res.output

    # Nonexistent launch
    res_err = cli_isolated.invoke(cli, ["launch", "Nonexistent"])
    assert res_err.exit_code != 0


# ---------------------------------------------------------------------------
# 6. update Command
# ---------------------------------------------------------------------------

def test_cli_update_in_place(temp_workspace, cli_isolated):
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db=db)
    v1 = create_mock_appimage(temp_workspace["root"] / "P-1.AppImage", name="PApp", version="1.0")
    v2 = create_mock_appimage(temp_workspace["root"] / "P-2.AppImage", name="PApp", version="2.0")

    installer.install(v1)
    res = cli_isolated.invoke(cli, ["update", "PApp", "--file", str(v2)])
    assert res.exit_code == 0
    assert "version 2.0" in res.output

    app = db.get_app_by_name("PApp")
    assert app.version == "2.0"


def test_cli_update_check_only_and_all(temp_workspace, cli_isolated):
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db=db)
    installer.install(create_mock_appimage(temp_workspace["root"] / "UpdCheck.AppImage", name="UpdCheck", version="1.0"))

    # Single app check-only (no update source configured)
    res = cli_isolated.invoke(cli, ["update", "UpdCheck", "--check-only"])
    assert res.exit_code == 0
    assert "up to date" in res.output.lower()

    # Update all check-only
    res_all = cli_isolated.invoke(cli, ["update", "--all", "--check-only"])
    assert res_all.exit_code == 0
    assert "Check complete" in res_all.output


def test_cli_update_missing_args(cli_isolated):
    # No app name and no --all
    res = cli_isolated.invoke(cli, ["update"])
    assert res.exit_code != 0

    # Conflict --all and --file
    res_conflict = cli_isolated.invoke(cli, ["update", "--all", "--file", "/tmp/dummy.AppImage"])
    assert res_conflict.exit_code != 0


# ---------------------------------------------------------------------------
# 7. uninstall Command
# ---------------------------------------------------------------------------

def test_cli_uninstall(temp_workspace, cli_isolated):
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db=db)
    installer.install(create_mock_appimage(temp_workspace["root"] / "DelApp.AppImage", name="DelApp"))

    res = cli_isolated.invoke(cli, ["uninstall", "DelApp"])
    assert res.exit_code == 0
    assert "Successfully uninstalled" in res.output
    assert db.get_app_by_name("DelApp") is None

    # Uninstall unknown
    res_err = cli_isolated.invoke(cli, ["uninstall", "DelApp"])
    assert res_err.exit_code != 0


# ---------------------------------------------------------------------------
# 8. verify Command
# ---------------------------------------------------------------------------

def test_cli_verify_single_and_all(temp_workspace, cli_isolated):
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db=db)
    app = installer.install(create_mock_appimage(temp_workspace["root"] / "VerTool.AppImage", name="VerTool"))

    # Single verify: success
    res = cli_isolated.invoke(cli, ["verify", "VerTool"])
    assert res.exit_code == 0
    assert "PASSED" in res.output

    # Verify all: success
    res_all = cli_isolated.invoke(cli, ["verify", "--all"])
    assert res_all.exit_code == 0
    assert "passed integrity check" in res_all.output

    # Tamper with file
    with open(app.install_path, "wb") as f:
        f.write(b"CORRUPT")

    # Single verify: fail
    res_tampered = cli_isolated.invoke(cli, ["verify", "VerTool"])
    assert res_tampered.exit_code != 0
    assert "FAILED" in res_tampered.output or "MISMATCH" in res_tampered.output

    # Verify all: reports failure
    res_all_fail = cli_isolated.invoke(cli, ["verify", "--all"])
    assert res_all_fail.exit_code != 0


# ---------------------------------------------------------------------------
# 9. backup Command (export & import)
# ---------------------------------------------------------------------------

def test_cli_backup_export_and_import(temp_workspace, cli_isolated):
    db = DatabaseManager(temp_workspace["db_path"])
    installer = InstallerService(db=db)
    installer.install(create_mock_appimage(temp_workspace["root"] / "BackupMe.AppImage", name="BackupMe", version="1.2"))

    export_path = temp_workspace["root"] / "manifest.json"
    res_exp = cli_isolated.invoke(cli, ["backup", "export", str(export_path)])
    assert res_exp.exit_code == 0
    assert export_path.exists()

    # Clear DB
    app = db.get_app_by_name("BackupMe")
    db.delete_app(app.id)
    assert db.get_app_by_name("BackupMe") is None

    # Import
    res_imp = cli_isolated.invoke(cli, ["backup", "import", str(export_path)])
    assert res_imp.exit_code == 0
    assert db.get_app_by_name("BackupMe") is not None


# ---------------------------------------------------------------------------
# 10. search Command
# ---------------------------------------------------------------------------

def test_cli_search(mock_http_server, cli_isolated):
    res = cli_isolated.invoke(cli, ["search", "Krita", "--feed-url", f"{mock_http_server}/feed.json"])
    assert res.exit_code == 0
    assert "Krita" in res.output
    assert "Download:" in res.output

    # JSON search
    res_json = cli_isolated.invoke(cli, ["search", "Krita", "--feed-url", f"{mock_http_server}/feed.json", "--json"])
    assert res_json.exit_code == 0
    items = json.loads(res_json.output)
    assert len(items) >= 1
    assert items[0]["name"] == "Krita"

    # Search with no match
    res_none = cli_isolated.invoke(cli, ["search", "NonExistentApp", "--feed-url", f"{mock_http_server}/feed.json"])
    assert res_none.exit_code == 0
    assert "No applications found" in res_none.output
