"""
tests/test_database.py — Unit tests for AppImageKit SQLite database and App model.
Covers schema initialization, WAL mode, CRUD operations, indices, filtering, rollbacks, and serialization.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.models.app import App, InstallMode, SandboxType, UpdateSource
from src.models.database import DatabaseManager


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Fixture providing a fresh DatabaseManager backed by a temporary file."""
    db_file = tmp_path / "test_registry.db"
    return DatabaseManager(db_file)


@pytest.fixture
def sample_app() -> App:
    """Fixture providing a standard test App model with all 23 domain attributes."""
    return App(
        name="TestApp",
        filename="test-app-x86_64.AppImage",
        install_path="/home/user/.local/share/appimages/test-app-x86_64.AppImage",
        desktop_entry="/home/user/.local/share/applications/test-app.desktop",
        icon_path="/home/user/.local/share/icons/hicolor/scalable/apps/test-app.svg",
        version="1.2.3",
        size_bytes=42000000,
        install_mode=InstallMode.USER,
        update_source=UpdateSource.GITHUB,
        update_url="testorg/testapp",
        zsync_url="https://example.com/test.zsync",
        category="Development",
        sandboxed=True,
        sandbox_type=SandboxType.BUBBLEWRAP,
        autostart=False,
        checksum_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        exec_args="--verbose",
        comment="A sample test development application",
        terminal=False,
        startup_wm_class="TestApp",
        pinned=False,
        hub_id="test-app-hub",
    )


def test_schema_initialization(temp_db: DatabaseManager):
    """1. Verify tables, indices, and WAL mode are created upon initialization."""
    with temp_db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        mode = cur.fetchone()[0]
        assert mode.lower() == "wal"

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='apps';")
        assert cur.fetchone() is not None

        cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indices = [row[0] for row in cur.fetchall()]
        assert "idx_apps_name" in indices
        assert "idx_apps_category" in indices
        assert "idx_apps_install_mode" in indices
        assert "idx_apps_autostart" in indices
        assert "idx_apps_pinned" in indices
        assert "idx_apps_checksum" in indices
        assert "idx_apps_hub_id" in indices


def test_schema_idempotency(temp_db: DatabaseManager):
    """2. Verify calling initialize_schema multiple times is safe and preserves data."""
    temp_db.initialize_schema()
    temp_db.initialize_schema()
    assert temp_db.count_apps() == 0


def test_app_to_db_dict(sample_app: App):
    """3. Verify App serialization to_db_dict converts enums and booleans appropriately."""
    db_dict = sample_app.to_db_dict()
    assert db_dict["install_mode"] == "user"
    assert db_dict["update_source"] == "github"
    assert db_dict["sandbox_type"] == "bubblewrap"
    assert db_dict["sandboxed"] == 1
    assert db_dict["autostart"] == 0
    assert db_dict["terminal"] == 0
    assert db_dict["pinned"] == 0


def test_app_from_row_roundtrip(sample_app: App):
    """4. Verify App.from_row roundtrips correctly from db_dict."""
    db_dict = sample_app.to_db_dict()
    reconstructed = App.from_row(db_dict)
    assert reconstructed.name == sample_app.name
    assert reconstructed.install_mode == InstallMode.USER
    assert reconstructed.update_source == UpdateSource.GITHUB
    assert reconstructed.sandbox_type == SandboxType.BUBBLEWRAP
    assert reconstructed.sandboxed is True
    assert reconstructed.checksum_sha256 == sample_app.checksum_sha256
    assert reconstructed.category == "Development"


def test_app_to_and_from_dict_json(sample_app: App):
    """5. Verify export to JSON dict and import from dict."""
    as_json = sample_app.to_dict()
    assert as_json["install_mode"] == "user"
    assert as_json["update_source"] == "github"
    assert as_json["sandbox_type"] == "bubblewrap"

    restored = App.from_dict(as_json)
    assert restored.name == sample_app.name
    assert restored.version == "1.2.3"
    assert restored.install_mode == InstallMode.USER


def test_add_and_get_app(temp_db: DatabaseManager, sample_app: App):
    """6. Verify inserting an App populates id and timestamps, and can be retrieved."""
    saved_app = temp_db.add_app(sample_app)
    assert saved_app.id is not None
    assert saved_app.id >= 1
    assert saved_app.installed_at is not None
    assert saved_app.updated_at is not None

    fetched_app = temp_db.get_app(saved_app.id)
    assert fetched_app is not None
    assert fetched_app.id == saved_app.id
    assert fetched_app.name == "TestApp"
    assert fetched_app.size_bytes == 42000000
    assert fetched_app.sandboxed is True


def test_add_app_unique_constraint(temp_db: DatabaseManager, sample_app: App):
    """7. Verify inserting duplicate install_path raises IntegrityError."""
    temp_db.add_app(sample_app)

    dup_app = App(
        name="DuplicateApp",
        filename="dup.AppImage",
        install_path=sample_app.install_path,
        checksum_sha256="abc123456",
    )
    with pytest.raises(sqlite3.IntegrityError):
        temp_db.add_app(dup_app)


def test_get_app_nonexistent(temp_db: DatabaseManager):
    """8. Verify querying non-existent app id returns None."""
    assert temp_db.get_app(99999) is None


def test_get_app_by_name(temp_db: DatabaseManager, sample_app: App):
    """9. Verify case-insensitive retrieval by application name."""
    temp_db.add_app(sample_app)

    # Exact match
    app1 = temp_db.get_app_by_name("TestApp")
    assert app1 is not None and app1.name == "TestApp"

    # Case-insensitive match
    app2 = temp_db.get_app_by_name("testapp")
    assert app2 is not None and app2.name == "TestApp"

    # Non-existent
    assert temp_db.get_app_by_name("NoSuchApp") is None


def test_get_app_by_path(temp_db: DatabaseManager, sample_app: App):
    """10. Verify retrieval by binary install path."""
    temp_db.add_app(sample_app)

    found = temp_db.get_app_by_path(sample_app.install_path)
    assert found is not None
    assert found.name == sample_app.name

    assert temp_db.get_app_by_path("/non/existent/path.AppImage") is None


def test_update_app(temp_db: DatabaseManager, sample_app: App):
    """11. Verify updating app metadata modifies fields and updates updated_at timestamp."""
    app = temp_db.add_app(sample_app)

    app.version = "1.3.0"
    app.size_bytes = 45000000
    app.pinned = True
    app.comment = "Updated description"

    success = temp_db.update_app(app)
    assert success is True

    updated = temp_db.get_app(app.id)
    assert updated is not None
    assert updated.version == "1.3.0"
    assert updated.size_bytes == 45000000
    assert updated.pinned is True
    assert updated.comment == "Updated description"


def test_update_nonexistent_app(temp_db: DatabaseManager):
    """12. Verify updating non-existent app returns False."""
    fake_app = App(id=9999, name="Fake", install_path="/tmp/fake.AppImage", checksum_sha256="123")
    assert temp_db.update_app(fake_app) is False


def test_delete_app(temp_db: DatabaseManager, sample_app: App):
    """13. Verify deleting an app removes record from database."""
    app = temp_db.add_app(sample_app)
    assert temp_db.get_app(app.id) is not None

    deleted = temp_db.delete_app(app.id)
    assert deleted is True
    assert temp_db.get_app(app.id) is None


def test_delete_nonexistent_app(temp_db: DatabaseManager):
    """14. Deleting non-existent ID returns False."""
    assert temp_db.delete_app(99999) is False


def test_list_apps_sorting(temp_db: DatabaseManager):
    """15. Verify list_apps returns pinned apps first, followed by alphabetical order."""
    temp_db.add_app(App(name="Zebra", filename="z.AppImage", install_path="/opt/z.AppImage", checksum_sha256="1", pinned=False))
    temp_db.add_app(App(name="Charlie", filename="c.AppImage", install_path="/opt/c.AppImage", checksum_sha256="2", pinned=True))
    temp_db.add_app(App(name="Alpha", filename="a.AppImage", install_path="/opt/a.AppImage", checksum_sha256="3", pinned=False))
    temp_db.add_app(App(name="Beta", filename="b.AppImage", install_path="/opt/b.AppImage", checksum_sha256="4", pinned=True))

    apps = temp_db.list_apps()
    names = [a.name for a in apps]
    assert names == ["Beta", "Charlie", "Alpha", "Zebra"]


def test_list_apps_filtering(temp_db: DatabaseManager):
    """16. Verify filtering by category, install mode, pinned status, and search query."""
    temp_db.add_app(App(
        name="Firefox", filename="firefox.AppImage", install_path="/opt/firefox.AppImage",
        checksum_sha256="1", category="Network", install_mode=InstallMode.SYSTEM, pinned=True, comment="Web Browser"
    ))
    temp_db.add_app(App(
        name="VSCode", filename="vscode.AppImage", install_path="/home/user/vscode.AppImage",
        checksum_sha256="2", category="Development", install_mode=InstallMode.USER, pinned=False, comment="Code Editor"
    ))
    temp_db.add_app(App(
        name="VLC", filename="vlc.AppImage", install_path="/home/user/vlc.AppImage",
        checksum_sha256="3", category="AudioVideo", install_mode=InstallMode.USER, pinned=True, comment="Media Player"
    ))

    # Category filter
    dev_apps = temp_db.list_apps(category="Development")
    assert len(dev_apps) == 1 and dev_apps[0].name == "VSCode"

    # "All" category filter returns all
    all_apps = temp_db.list_apps(category="All")
    assert len(all_apps) == 3

    # Mode filter
    system_apps = temp_db.list_apps(mode=InstallMode.SYSTEM)
    assert len(system_apps) == 1 and system_apps[0].name == "Firefox"

    # Pinned filter
    pinned_apps = temp_db.list_apps(pinned=True)
    assert len(pinned_apps) == 2 and {a.name for a in pinned_apps} == {"Firefox", "VLC"}

    # Search query filter (matches name)
    search_apps = temp_db.list_apps(query="fire")
    assert len(search_apps) == 1 and search_apps[0].name == "Firefox"

    # Search query filter (matches comment)
    comment_apps = temp_db.list_apps(query="player")
    assert len(comment_apps) == 1 and comment_apps[0].name == "VLC"


def test_total_disk_usage(temp_db: DatabaseManager):
    """17. Verify aggregation of total disk usage in bytes."""
    assert temp_db.get_total_disk_usage() == 0

    app1 = temp_db.add_app(App(name="App1", filename="1.AppImage", install_path="/1", checksum_sha256="1", size_bytes=1000))
    app2 = temp_db.add_app(App(name="App2", filename="2.AppImage", install_path="/2", checksum_sha256="2", size_bytes=2500))
    assert temp_db.get_total_disk_usage() == 3500

    app1.size_bytes = 2000
    temp_db.update_app(app1)
    assert temp_db.get_total_disk_usage() == 4500

    temp_db.delete_app(app2.id)
    assert temp_db.get_total_disk_usage() == 2000


def test_connection_context_rollback_on_error(temp_db: DatabaseManager):
    """18. Verify transaction rollback occurs if an exception is raised within get_connection."""
    with pytest.raises(RuntimeError):
        with temp_db.get_connection() as conn:
            conn.execute(
                "INSERT INTO apps (name, filename, install_path, checksum_sha256) VALUES ('FailApp', 'f.AppImage', '/f', 'sha');"
            )
            raise RuntimeError("Simulated transaction failure")

    assert temp_db.count_apps() == 0
    assert temp_db.get_app_by_name("FailApp") is None


def test_in_memory_database():
    """19. Verify DatabaseManager operates seamlessly in pure memory mode (:memory:)."""
    mem_db = DatabaseManager(":memory:")
    try:
        app = mem_db.add_app(App(name="MemApp", filename="mem.AppImage", install_path="/mem", checksum_sha256="mem123"))
        assert app.id == 1
        assert mem_db.get_app(1).name == "MemApp"
        assert mem_db.count_apps() == 1
    finally:
        mem_db.close()
