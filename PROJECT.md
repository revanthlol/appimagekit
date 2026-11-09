# Project: AppImageKit

## Architecture
AppImageKit is a native Linux AppImage installer, manager, and updater featuring a clean GTK4/Libadwaita interface and a full-featured Click CLI.
The system is built with strict layer separation:
1. **Domain & Persistence Layer (`src/models/`):**
   - `App` dataclass: typed representations of application metadata, storage locations, update sources, checksums, sandboxing indicators, and desktop configuration.
   - `DatabaseManager`: SQLite storage in WAL mode (`~/.local/share/appimagekit/registry.db`) with automatic schema initialization and indices.
2. **Service Layer (`src/services/`):**
   - `InstallerService`: Dual-mode storage (user `~/.local/share/appimages/` and system `/opt/appimages/`), `chmod +x`, checksums, launching, uninstallation, and Tier 4 in-place local updates.
   - `DesktopEntryService`: Freedesktop.org Desktop Entry v1.5 compliant `.desktop` generation, parsing, validation, and editing.
   - `IconExtractorService`: Multi-tier icon extraction (`.DirIcon`, embedded `.desktop` Icon=, `unsquashfs`/`7z` fallback, hicolor theme resolution).
   - `UpdaterService`: 4-tier cascading update engine (Tier 1: zsync delta; Tier 2: GitHub Releases API; Tier 3: Direct URL fallback; Tier 4: Manual in-place local `.AppImage` replacement via `--file` or GUI).
   - `SandboxDetectorService`: Bubblewrap (`bwrap`) and Firejail sandbox configuration inspection.
   - `BackupManagerService`: JSON manifest export/import and archive bundling.
   - `HubCatalogService`: AppImage Hub feed consumption (`appimage.github.io/feed.json`), search, and direct install.
   - `IntegrityVerifierService`: SHA256 integrity auditing and tamper detection.
   - `AutostartManager`: Freedesktop autostart desktop entry management (`~/.config/autostart/`).
3. **Presentation Layer:**
   - **CLI (`src/cli.py`):** Click command hierarchy (`install`, `list`, `launch`, `update`, `uninstall`, `info`, `verify`, `backup`, `search`).
   - **GUI (`src/application.py`, `src/window.py`, `src/widgets/`):** GTK4 and Libadwaita window with grid view (`AppGridView`, `AppCard`), category sidebar with disk usage summary (`Sidebar`), full detail view (`AppDetailsView`), desktop entry editor dialog (`DesktopEditorDialog`), and drag-and-drop installer (`DropTarget`).
   - Zero GTK dependency in models, database, and services ensures 100% headless CI testability.

## Code Layout
```
appimagekit/
├── pyproject.toml
├── meson.build (optional / auxiliary)
├── data/
│   ├── io.github.revanthlol.appimagekit.desktop
│   ├── io.github.revanthlol.appimagekit.metainfo.xml
│   └── icons/hicolor/scalable/apps/io.github.revanthlol.appimagekit.svg
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── application.py
│   ├── window.py
│   ├── cli.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── app.py
│   │   └── database.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── installer.py
│   │   ├── desktop_entry.py
│   │   ├── icon_extractor.py
│   │   ├── updater.py
│   │   ├── sandbox.py
│   │   ├── backup.py
│   │   ├── hub.py
│   │   ├── integrity.py
│   │   └── autostart.py
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── sidebar.py
│   │   ├── app_grid.py
│   │   ├── app_card.py
│   │   ├── app_details.py
│   │   ├── desktop_editor.py
│   │   └── drop_target.py
│   └── utils/
│       ├── __init__.py
│       ├── paths.py
│       └── process.py
└── tests/
    ├── conftest.py
    ├── test_database.py
    ├── test_installer.py
    ├── test_desktop_entry.py
    ├── test_icon_extractor.py
    ├── test_updater.py
    ├── test_services.py
    ├── test_cli.py
    └── test_ui_headless.py
```

## Feature Inventory
Every feature from user requirements (R1–R5 + local file update) is inventoried and assigned below:
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Dual-Mode Storage Paths | User-local (`~/.local/share/appimages/`) and system (`/opt/appimages/`) | M1 | R1 |
| 2 | Privilege Fallback | Automatic fallback to user-local storage if system root access is unavailable | M1 | R1 |
| 3 | Package Scaffolding | Editable Python package installation (`pip install -e .`) with entry points | M1 | Acceptance |
| 4 | File Permissions & Executable Flag | Ensure installed AppImages are executable (`chmod +x` / `0o755`) | M1 | R1 |
| 5 | Checksum Calculation | SHA256 checksum computation and file size recording upon installation | M1 | R1 |
| 6 | SQLite Registry & Data Model | SQLite database in WAL mode storing app metadata, paths, and status | M1 | R1, PLAN |
| 7 | Application Launching | Spawn AppImage processes with arguments, working directory, and environment | M1 | R5, PLAN |
| 8 | Icon Extraction (.DirIcon & Theme) | Extract `.DirIcon`, PNG/SVG icons from AppImage containers to hicolor theme | M2 | R2 |
| 9 | Unsquashfs / 7z Fallback Extraction | Fallback icon & metadata extraction when `--appimage-extract` cannot run | M2 | Survey 1, 2 |
| 10 | Desktop Entry Generation | Generate valid freedesktop.org `.desktop` entries in application paths | M2 | R2 |
| 11 | Desktop Entry Parsing & Escaping | Parse existing desktop files, escape Exec paths, handle field codes (%U) | M2 | R2, Spec |
| 12 | In-App Desktop Entry Editor Service | Inspect and customize Name, Comment, Exec args, Icon, Categories, Terminal | M2 | R2 |
| 13 | Clean Uninstallation Purge | Cleanly purge AppImage binary, desktop file, icon, autostart, and DB record | M2 | R2, Acceptance |
| 14 | Tier 1 Update (zsync / AppImageUpdate) | Check and apply updates via embedded zsync / `.upd_info` delta data | M3 | R3 |
| 15 | Tier 2 Update (GitHub Releases API) | Query GitHub Releases for latest version and download new asset | M3 | R3 |
| 16 | Tier 3 Update (Direct Manual URL) | Check updates via direct URL HTTP HEAD (ETag / Last-Modified / Length) | M3 | R3 |
| 17 | Tier 4 Update (Local File In-Place) | Manually upload/select local `.AppImage` to update existing app in-place | M3 | User Addition |
| 18 | Batch Update Engine | Check and trigger updates in batch ("Update All") | M3 | R3 |
| 19 | Autostart Management | Enable/disable app autostart via `~/.config/autostart/*.desktop` | M4 | R5 |
| 20 | Sandbox Detection | Detect Bubblewrap (`bwrap`) indicators and Firejail configurations | M4 | R5 |
| 21 | Backup Export & Import | Export installed app list as JSON manifest; import/reinstall from backup | M4 | R5 |
| 22 | AppImage Hub Catalog Browser Service | Fetch feed from `appimage.github.io`, search catalog, direct install | M4 | R5 |
| 23 | Integrity Verification Service | SHA256 verification of on-disk binaries against recorded database checksum | M4 | R5 |
| 24 | Complete Click CLI Suite | CLI subcommands: `install`, `list`, `launch`, `update` (with `--file`), `uninstall`, `info`, `verify`, `backup` | M4 | R5, Acceptance |
| 25 | Adwaita Main Window & Navigation | Libadwaita window with ToolbarView, HeaderBar, Search bar, and ViewStack | M5 | R4 |
| 26 | App Grid View & App Cards | Adwaita grid showing cards with icons, names, versions, and badges | M5 | R4 |
| 27 | Filter Sidebar & Disk Usage Summary | Sidebar with categories (All, Updates, Dev, Media, etc.) and disk usage | M5 | R4 |
| 28 | App Detail Page & Action Buttons | Detail page with Launch, Update, Edit Desktop, Autostart, Uninstall | M5 | R4 |
| 29 | Drag-and-Drop Installation Target | Drop target accepting `.AppImage` files dropped onto window | M5 | R4 |
| 30 | Desktop Entry Editor Dialog (GUI) | Libadwaita dialog editing Name, Comment, Exec, Icon, Category, Terminal | M5 | R4 |
| 31 | Full E2E Test Suite Pass (Tiers 1-4) | Pass 100% of opaque-box requirement-driven E2E tests (pytest) | M6 | Acceptance |
| 32 | Adversarial Hardening (Tier 5) | White-box stress testing, corruption handling, edge cases | M6 | Project Pattern |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Core Storage & Dual-Mode Installation | Package setup (`pyproject.toml`), SQLite database & `App` model, `InstallerService` (paths, chmod, SHA256, launch), path resolver | none | DONE |
| M2 | Desktop Entry & Icon Management | `IconExtractorService` (extraction + fallbacks), `DesktopEntryService` (freedesktop v1.5 gen/parse/edit), clean uninstallation purge | M1 | DONE |
| M3 | Multi-Tier Cascading Update Engine | `UpdaterService` (Tier 1: zsync, Tier 2: GitHub Releases, Tier 3: Direct URL, Tier 4: Local in-place file update, Batch Update) | M1, M2 | DONE |
| M4 | Auxiliary Services & Complete CLI | `SandboxDetectorService`, `AutostartManager`, `BackupManagerService`, `HubCatalogService`, `IntegrityVerifierService`, Click CLI commands | M1, M2, M3 | PLANNED |
| M5 | GTK4 / Libadwaita User Interface | Window, Sidebar (filters + disk usage), AppGrid, AppCard, AppDetailsView, DesktopEditorDialog, DropTarget, AsyncWorker | M1, M2, M3, M4 | PLANNED |
| M6 | Final Milestone: E2E Test Pass & Hardening | Phase 1: Pass 100% of E2E test suite (Tiers 1-4). Phase 2: Adversarial coverage hardening (Tier 5) | M1, M2, M3, M4, M5, TEST_READY.md | PLANNED |

## Interface Contracts

### `src/utils/paths.py` ↔ Core Services
```python
class Paths:
    @staticmethod
    def get_user_appimages_dir() -> Path: ...      # ~/.local/share/appimages
    @staticmethod
    def get_system_appimages_dir() -> Path: ...    # /opt/appimages
    @staticmethod
    def get_database_path() -> Path: ...           # ~/.local/share/appimagekit/registry.db
    @staticmethod
    def get_user_applications_dir() -> Path: ...   # ~/.local/share/applications
    @staticmethod
    def get_system_applications_dir() -> Path: ... # /usr/share/applications
    @staticmethod
    def get_icons_dir() -> Path: ...               # ~/.local/share/icons/hicolor
    @staticmethod
    def get_autostart_dir() -> Path: ...           # ~/.config/autostart
    @staticmethod
    def get_cache_dir() -> Path: ...               # ~/.cache/appimagekit
    @staticmethod
    def resolve_install_dir(mode: InstallMode) -> Tuple[Path, InstallMode]: ...
```

### `src/models/` ↔ Service Layer
```python
class App:
    id: Optional[int]
    name: str
    filename: str
    install_path: str
    desktop_entry: Optional[str]
    icon_path: Optional[str]
    version: Optional[str]
    size_bytes: int
    install_mode: InstallMode          # 'user' | 'system'
    update_source: UpdateSource        # 'none' | 'appimageupdate' | 'github' | 'manual_url' | 'local_file'
    update_url: Optional[str]
    zsync_url: Optional[str]
    category: str
    sandboxed: bool
    sandbox_type: Optional[SandboxType]# 'bubblewrap' | 'firejail' | None
    autostart: bool
    checksum_sha256: str
    exec_args: str
    comment: str
    terminal: bool
    startup_wm_class: Optional[str]
    pinned: bool
    hub_id: Optional[str]
    installed_at: Optional[str]
    updated_at: Optional[str]

class DatabaseManager:
    def add_app(self, app: App) -> App: ...
    def update_app(self, app: App) -> bool: ...
    def delete_app(self, app_id: int) -> bool: ...
    def get_app(self, app_id: int) -> Optional[App]: ...
    def get_app_by_name(self, name: str) -> Optional[App]: ...
    def get_app_by_path(self, path: str) -> Optional[App]: ...
    def list_apps(self, category: Optional[str] = None, mode: Optional[InstallMode] = None,
                  pinned: Optional[bool] = None, query: Optional[str] = None) -> List[App]: ...
    def get_total_disk_usage(self) -> int: ...
```

### `src/services/installer.py` ↔ Presentation & Update Layers
```python
class InstallerService:
    def install(self, appimage_path: Path, mode: InstallMode = InstallMode.USER,
                category: Optional[str] = None, update_source: Optional[UpdateSource] = None,
                update_url: Optional[str] = None) -> App: ...
    def uninstall(self, app: App) -> bool: ...
    def update_in_place(self, app: App, new_appimage_path: Path) -> App: ...
    def launch(self, app: App, extra_args: Optional[List[str]] = None) -> subprocess.Popen: ...
```

### `src/services/updater.py` ↔ Core Services
```python
class UpdaterService:
    def check_update(self, app: App) -> UpdateInfo: ...
    def apply_update(self, app: App, update_info: UpdateInfo,
                     progress_callback: Optional[Callable[[float], None]] = None) -> App: ...
    def check_all_updates(self, apps: List[App]) -> Dict[int, UpdateInfo]: ...
```
