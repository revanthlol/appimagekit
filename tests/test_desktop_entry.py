"""
tests/test_desktop_entry.py — Comprehensive unit tests for DesktopEntryService.
Validates Freedesktop.org Desktop Entry v1.5 compliance, quoting, parsing, and editing.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

from src.models.app import App, InstallMode
from src.models.database import DatabaseManager
from src.services.desktop_entry import DesktopEntryService
from src.utils.paths import Paths


def test_generate_freedesktop_v1_5_compliance(tmp_path):
    """Verify all standard Freedesktop v1.5 keys and formatting are present."""
    svc = DesktopEntryService()
    app = App(
        name="Test Editor",
        filename="TestEditor.AppImage",
        install_path=str(tmp_path / "TestEditor.AppImage"),
        category="Development",
        terminal=False,
        comment="A great code editor",
        startup_wm_class="TestEditor",
        version="1.5.0",
    )
    dest_file = svc.generate_entry(app, destination_dir=tmp_path)
    assert dest_file.exists()
    assert dest_file.name == "appimagekit-test_editor.desktop"

    content = dest_file.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip()]

    assert "[Desktop Entry]" in lines
    assert "Type=Application" in lines
    assert "Name=Test Editor" in lines
    assert f'Exec="{app.install_path}" %U' in lines
    assert "Categories=Development;" in lines
    assert "Terminal=false" in lines
    assert "Comment=A great code editor" in lines
    assert "StartupWMClass=TestEditor" in lines
    assert "StartupNotify=true" in lines
    assert "X-AppImage-Version=1.5.0" in lines


def test_desktop_file_validate_tool(tmp_path):
    """Verify that generated desktop entries pass official desktop-file-validate."""
    svc = DesktopEntryService()
    app = App(
        name="CompliantApp",
        filename="CompliantApp.AppImage",
        install_path=str(tmp_path / "CompliantApp.AppImage"),
        category="Utility;System",
        terminal=False,
        comment="Validates cleanly",
    )
    dest_file = svc.generate_entry(app, destination_dir=tmp_path)
    assert svc.validate_entry(dest_file) is True

    # If desktop-file-validate binary is present on the host, run it directly
    res = subprocess.run(["desktop-file-validate", str(dest_file)], capture_output=True, text=True)
    assert res.returncode == 0, f"desktop-file-validate failed: {res.stdout} {res.stderr}"


def test_exec_quoting_spaces_and_special_chars():
    """Verify correct path quoting and escaping for spaces, backslashes, and quotes."""
    # Path with spaces
    p1 = "/opt/my apps/cool tool/tool.AppImage"
    assert DesktopEntryService.format_exec(p1) == f'"{p1}" %U'

    # Path with internal quotes and backslashes
    p2 = r'/opt/test\dir/app"name"/app.AppImage'
    expected_escaped = r'/opt/test\\dir/app\"name\"/app.AppImage'
    assert DesktopEntryService.format_exec(p2) == f'"{expected_escaped}" %U'


def test_exec_with_custom_arguments():
    """Verify that custom exec_args are placed between the binary path and %U."""
    bin_path = "/usr/bin/my-app"
    exec_args = "--no-sandbox --flag=1"
    formatted = DesktopEntryService.format_exec(bin_path, exec_args)
    assert formatted == f'"{bin_path}" --no-sandbox --flag=1 %U'


def test_categories_trailing_semicolon_enforcement():
    """Verify that categories always terminate with a trailing semicolon."""
    assert DesktopEntryService.format_categories("Development") == "Development;"
    assert DesktopEntryService.format_categories("Development;IDE") == "Development;IDE;"
    assert DesktopEntryService.format_categories("Development;IDE;") == "Development;IDE;"
    assert DesktopEntryService.format_categories(["Audio", "Video", "Player"]) == "Audio;Video;Player;"
    assert DesktopEntryService.format_categories([]) == "Utility;"
    assert DesktopEntryService.format_categories("") == "Utility;"


def test_terminal_boolean_strict_lowercase(tmp_path):
    """Verify that Terminal value is strictly lowercase 'true' or 'false'."""
    svc = DesktopEntryService()

    app_gui = App(name="GuiApp", install_path=str(tmp_path / "gui"), terminal=False)
    entry_gui = svc.generate_entry(app_gui, destination_dir=tmp_path)
    content_gui = entry_gui.read_text(encoding="utf-8")
    assert "Terminal=false" in content_gui
    assert "Terminal=False" not in content_gui
    assert svc.validate_entry(entry_gui) is True

    app_cli = App(name="CliApp", install_path=str(tmp_path / "cli"), terminal=True)
    entry_cli = svc.generate_entry(app_cli, destination_dir=tmp_path)
    content_cli = entry_cli.read_text(encoding="utf-8")
    assert "Terminal=true" in content_cli
    assert "Terminal=True" not in content_cli
    assert svc.validate_entry(entry_cli) is True


def test_parse_desktop_file(tmp_path):
    """Verify parsing existing .desktop files with comments, unescaping, and booleans."""
    sample_file = tmp_path / "sample.desktop"
    sample_file.write_text(
        """# FreeDesktop sample
[Desktop Entry]
Type=Application
Name=Sample\\sApplication
Exec=/usr/bin/sample %U
Icon=/usr/share/icons/sample.png
Categories=Graphics;2DGraphics;
Comment=Line1\\nLine2
Terminal=true
StartupWMClass=SampleWM
X-AppImage-Version=2.1.0

[Extra Section]
Key=Ignored
""",
        encoding="utf-8",
    )

    parsed = DesktopEntryService.parse_desktop_file(sample_file)
    assert parsed["name"] == "Sample Application"
    assert parsed["exec"] == "/usr/bin/sample %U"
    assert parsed["icon"] == "/usr/share/icons/sample.png"
    assert parsed["categories"] == "Graphics;2DGraphics;"
    assert parsed["comment"] == "Line1\nLine2"
    assert parsed["terminal"] is True
    assert parsed["startup_wm_class"] == "SampleWM"
    assert parsed["version"] == "2.1.0"


def test_update_desktop_entry_synchronization(tmp_path):
    """Verify that update_entry updates both the on-disk file and SQLite database."""
    db_file = tmp_path / "test.db"
    db = DatabaseManager(db_file)
    svc = DesktopEntryService(db=db)

    app = App(
        name="OldName",
        filename="OldName.AppImage",
        install_path=str(tmp_path / "OldName.AppImage"),
        category="Utility",
        terminal=False,
    )
    saved_app = db.add_app(app)
    dest_path = svc.generate_entry(saved_app, destination_dir=tmp_path)
    saved_app.desktop_entry = str(dest_path.resolve())
    db.update_app(saved_app)

    # Perform customization
    updated = svc.update_entry(
        saved_app,
        name="NewName",
        comment="Updated comment",
        exec_args="--enable-feature",
        categories="Office;WordProcessor;",
        terminal=True,
        startup_wm_class="NewWM",
    )

    # Check updated app object
    assert updated.name == "NewName"
    assert updated.comment == "Updated comment"
    assert updated.exec_args == "--enable-feature"
    assert updated.category == "Office;WordProcessor;"
    assert updated.terminal is True
    assert updated.startup_wm_class == "NewWM"

    # Check database persistence
    db_app = db.get_app(saved_app.id)
    assert db_app is not None
    assert db_app.name == "NewName"
    assert db_app.comment == "Updated comment"
    assert db_app.exec_args == "--enable-feature"

    # Check on-disk .desktop file
    content = Path(updated.desktop_entry).read_text(encoding="utf-8")
    assert "Name=NewName" in content
    assert "Comment=Updated comment" in content
    assert "--enable-feature" in content
    assert "Categories=Office;WordProcessor;" in content
    assert "Terminal=true" in content
    assert "StartupWMClass=NewWM" in content


def test_remove_desktop_entry(tmp_path):
    """Verify clean removal of .desktop entry file."""
    svc = DesktopEntryService()
    test_file = tmp_path / "appimagekit-toremove.desktop"
    test_file.write_text("[Desktop Entry]\nName=ToRemove\n", encoding="utf-8")
    assert test_file.exists()

    result = svc.remove_entry(test_file)
    assert result is True
    assert not test_file.exists()

    # Removing non-existent file should be non-fatal and return True
    assert svc.remove_entry(test_file) is True
