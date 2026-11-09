"""
AppImageKit — Database Manager.
Provides thread-safe SQLite persistence in WAL mode with connection management,
schema initialization, and CRUD operations.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

from .app import App, InstallMode, SandboxType, UpdateSource

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS apps (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    filename            TEXT NOT NULL,
    install_path        TEXT NOT NULL UNIQUE,
    desktop_entry       TEXT,
    icon_path           TEXT,
    version             TEXT,
    size_bytes          INTEGER NOT NULL DEFAULT 0,
    install_mode        TEXT NOT NULL DEFAULT 'user' CHECK(install_mode IN ('user', 'system')),
    update_source       TEXT NOT NULL DEFAULT 'none' CHECK(update_source IN ('none', 'appimageupdate', 'github', 'manual_url', 'local_file')),
    update_url          TEXT,
    zsync_url           TEXT,
    category            TEXT NOT NULL DEFAULT 'Utility',
    sandboxed           INTEGER NOT NULL DEFAULT 0,
    sandbox_type        TEXT CHECK(sandbox_type IN ('bubblewrap', 'firejail', NULL)),
    autostart           INTEGER NOT NULL DEFAULT 0,
    checksum_sha256     TEXT NOT NULL,
    exec_args           TEXT DEFAULT '',
    comment             TEXT DEFAULT '',
    terminal            INTEGER NOT NULL DEFAULT 0,
    startup_wm_class    TEXT,
    pinned              INTEGER NOT NULL DEFAULT 0,
    hub_id              TEXT,
    installed_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_launched_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_apps_name ON apps(name);
CREATE INDEX IF NOT EXISTS idx_apps_category ON apps(category);
CREATE INDEX IF NOT EXISTS idx_apps_install_mode ON apps(install_mode);
CREATE INDEX IF NOT EXISTS idx_apps_autostart ON apps(autostart);
CREATE INDEX IF NOT EXISTS idx_apps_pinned ON apps(pinned);
CREATE INDEX IF NOT EXISTS idx_apps_checksum ON apps(checksum_sha256);
CREATE INDEX IF NOT EXISTS idx_apps_hub_id ON apps(hub_id);
"""


class DatabaseManager:
    """Manages SQLite storage for AppImage applications."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None:
            # Lazy import to avoid cyclic dependencies and respect XDG overrides
            try:
                from ..utils.paths import Paths
                self.db_path: Union[str, Path] = Paths.get_database_path()
            except (ImportError, AttributeError):
                self.db_path = Path.home() / ".local" / "share" / "appimagekit" / "registry.db"
        else:
            self.db_path = db_path

        self._is_memory = str(self.db_path) == ":memory:"
        self._mem_conn: Optional[sqlite3.Connection] = None

        if self._is_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            # Ensure parent directories exist
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self.initialize_schema()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Thread-safe connection context manager.
        Enables WAL mode and foreign keys, automatically committing on clean exit
        and rolling back on exceptions.
        """
        if self._is_memory and self._mem_conn is not None:
            try:
                yield self._mem_conn
                self._mem_conn.commit()
            except Exception:
                self._mem_conn.rollback()
                raise
        else:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def initialize_schema(self) -> None:
        """Create tables and indices if they do not exist. Idempotent."""
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_DDL)

    def add_app(self, app: App) -> App:
        """
        Insert a new AppImage record into the database.
        Returns the App instance with populated id and timestamps.
        Raises sqlite3.IntegrityError if install_path already exists.
        """
        data = app.to_db_dict()
        data.pop("id", None)  # Let SQLite autoincrement

        sql = """
            INSERT INTO apps (
                name, filename, install_path, desktop_entry, icon_path, version,
                size_bytes, install_mode, update_source, update_url, zsync_url,
                category, sandboxed, sandbox_type, autostart, checksum_sha256,
                exec_args, comment, terminal, startup_wm_class, pinned, hub_id,
                installed_at, updated_at, last_launched_at
            ) VALUES (
                :name, :filename, :install_path, :desktop_entry, :icon_path, :version,
                :size_bytes, :install_mode, :update_source, :update_url, :zsync_url,
                :category, :sandboxed, :sandbox_type, :autostart, :checksum_sha256,
                :exec_args, :comment, :terminal, :startup_wm_class, :pinned, :hub_id,
                COALESCE(:installed_at, CURRENT_TIMESTAMP),
                COALESCE(:updated_at, CURRENT_TIMESTAMP),
                :last_launched_at
            );
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, data)
            app_id = cur.lastrowid
            cur.execute("SELECT * FROM apps WHERE id = ?;", (app_id,))
            row = cur.fetchone()
            return App.from_row(row)

    def update_app(self, app: App) -> bool:
        """
        Update an existing App record.
        Updates updated_at timestamp automatically.
        Returns True if row was updated, False if record not found.
        """
        data = app.to_db_dict()
        where_clause = "WHERE id = :id;" if app.id is not None else "WHERE install_path = :install_path;"

        sql = f"""
            UPDATE apps SET
                name = :name,
                filename = :filename,
                install_path = :install_path,
                desktop_entry = :desktop_entry,
                icon_path = :icon_path,
                version = :version,
                size_bytes = :size_bytes,
                install_mode = :install_mode,
                update_source = :update_source,
                update_url = :update_url,
                zsync_url = :zsync_url,
                category = :category,
                sandboxed = :sandboxed,
                sandbox_type = :sandbox_type,
                autostart = :autostart,
                checksum_sha256 = :checksum_sha256,
                exec_args = :exec_args,
                comment = :comment,
                terminal = :terminal,
                startup_wm_class = :startup_wm_class,
                pinned = :pinned,
                hub_id = :hub_id,
                updated_at = CURRENT_TIMESTAMP,
                last_launched_at = :last_launched_at
            {where_clause}
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, data)
            return cur.rowcount > 0

    def delete_app(self, app_id: int) -> bool:
        """Delete an app by its database id. Returns True if deleted, False otherwise."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM apps WHERE id = ?;", (app_id,))
            return cur.rowcount > 0

    def get_app(self, app_id: int) -> Optional[App]:
        """Retrieve an app by its database id. Returns None if not found."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM apps WHERE id = ?;", (app_id,))
            row = cur.fetchone()
            return App.from_row(row) if row else None

    def get_app_by_name(self, name: str) -> Optional[App]:
        """
        Retrieve an app by name (case-insensitive).
        Returns the most recently installed if multiple exist.
        """
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM apps WHERE name = ? COLLATE NOCASE ORDER BY id DESC LIMIT 1;",
                (name,)
            )
            row = cur.fetchone()
            return App.from_row(row) if row else None

    def get_app_by_path(self, path: Union[str, Path]) -> Optional[App]:
        """
        Retrieve an app by install_path. Matches both raw and canonical paths.
        """
        raw_path = str(path)
        try:
            resolved_path = str(Path(path).resolve())
        except Exception:
            resolved_path = raw_path

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM apps WHERE install_path = ? OR install_path = ? LIMIT 1;",
                (raw_path, resolved_path)
            )
            row = cur.fetchone()
            return App.from_row(row) if row else None

    def list_apps(
        self,
        category: Optional[str] = None,
        mode: Optional[Union[InstallMode, str]] = None,
        pinned: Optional[bool] = None,
        query: Optional[str] = None,
    ) -> List[App]:
        """
        Query apps with dynamic filtering and sorting.
        Default order: pinned apps first, then alphabetical by name.
        """
        clauses = []
        params: List[Any] = []

        if category and category.lower() not in ("all", "*", ""):
            clauses.append("category = ?")
            params.append(category)

        if mode is not None:
            mode_val = mode.value if isinstance(mode, InstallMode) else str(mode)
            clauses.append("install_mode = ?")
            params.append(mode_val)

        if pinned is not None:
            clauses.append("pinned = ?")
            params.append(1 if pinned else 0)

        if query and query.strip():
            search_pattern = f"%{query.strip()}%"
            clauses.append("(name LIKE ? OR comment LIKE ? OR filename LIKE ?)")
            params.extend([search_pattern, search_pattern, search_pattern])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM apps {where_sql} ORDER BY pinned DESC, name COLLATE NOCASE ASC;"

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [App.from_row(row) for row in cur.fetchall()]

    def get_total_disk_usage(self) -> int:
        """Return total disk usage in bytes across all registered apps."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM apps;")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def get_app_by_checksum(self, checksum: str) -> Optional[App]:
        """Retrieve an app by SHA256 checksum for integrity audits."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM apps WHERE checksum_sha256 = ? LIMIT 1;", (checksum,))
            row = cur.fetchone()
            return App.from_row(row) if row else None

    def get_categories(self) -> List[str]:
        """Return unique application categories sorted alphabetically."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT category FROM apps WHERE category IS NOT NULL AND category != '' ORDER BY category COLLATE NOCASE ASC;"
            )
            return [row[0] for row in cur.fetchall()]

    def count_apps(self) -> int:
        """Return total count of installed applications."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM apps;")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def record_launch(self, app_id: int) -> bool:
        """Update the last_launched_at timestamp when an app is executed."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE apps SET last_launched_at = CURRENT_TIMESTAMP WHERE id = ?;", (app_id,))
            return cur.rowcount > 0

    def close(self) -> None:
        """Close any persistent in-memory connection."""
        if self._is_memory and self._mem_conn:
            self._mem_conn.close()
            self._mem_conn = None
