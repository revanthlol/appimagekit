# AppImageKit — Project Plan

> A GTK4/Libadwaita AppImage manager for Linux: install, update, organize, and launch AppImages with full desktop integration.

---

## 🎯 Vision

AppImageKit is a clean, native Linux application that turns the messy "download and chmod +x" AppImage workflow into a polished app-store-like experience. It handles desktop entries, icons, updates, sandboxing, and more — so users can focus on using their apps, not managing files.

---

## 🏗️ Architecture

### Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| UI Framework | GTK4 + Libadwaita (via PyGObject) |
| Data Store | SQLite (app registry, metadata, update sources) |
| CLI | `click` (for `appimagekit` CLI commands) |
| Updates | AppImageUpdate (zsync), GitHub Releases API, manual URL |
| Packaging | Will be packaged as an AppImage itself (meta!) |
| Build | Meson (standard for GTK apps) |

### Directory Structure

```
appimagekit/
├── PLAN.md                    # This file
├── README.md                  # Project README
├── LICENSE                    # MIT License
├── pyproject.toml             # Python project config
├── meson.build                # Meson build system
├── data/
│   ├── io.github.revanthlol.appimagekit.desktop
│   ├── io.github.revanthlol.appimagekit.metainfo.xml
│   ├── io.github.revanthlol.appimagekit.gschema.xml
│   ├── icons/
│   │   └── hicolor/
│   │       └── scalable/
│   │           └── apps/
│   │               └── io.github.revanthlol.appimagekit.svg
│   └── ui/
│       ├── window.ui
│       ├── app_card.ui
│       ├── app_details.ui
│       ├── preferences.ui
│       └── desktop_entry_editor.ui
├── src/
│   ├── __init__.py
│   ├── main.py                # Application entry point
│   ├── application.py         # GtkApplication subclass
│   ├── window.py              # Main window (grid view, sidebar)
│   ├── cli.py                 # Click CLI interface
│   ├── models/
│   │   ├── __init__.py
│   │   ├── app.py             # AppImage data model
│   │   └── database.py        # SQLite wrapper
│   ├── services/
│   │   ├── __init__.py
│   │   ├── installer.py       # Install/uninstall logic
│   │   ├── desktop_entry.py   # .desktop file management
│   │   ├── icon_extractor.py  # Icon extraction from AppImages
│   │   ├── updater.py         # Update checking & applying
│   │   ├── sandbox.py         # Sandbox detection & info
│   │   ├── backup.py          # Backup/export functionality
│   │   ├── hub.py             # AppImage Hub integration
│   │   ├── integrity.py       # Signature/checksum verification
│   │   └── autostart.py       # Startup app management
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── app_card.py        # Grid card widget
│   │   ├── app_details.py     # Detail view panel
│   │   ├── drop_target.py     # Drag-and-drop handler
│   │   ├── desktop_editor.py  # Desktop entry editor dialog
│   │   ├── hub_browser.py     # AppImage Hub browser view
│   │   └── sidebar.py         # Category/filter sidebar
│   └── utils/
│       ├── __init__.py
│       ├── paths.py           # Path management (user/system)
│       └── config.py          # App configuration
└── tests/
    ├── test_installer.py
    ├── test_desktop_entry.py
    ├── test_updater.py
    └── test_database.py
```

---

## 📦 Storage Strategy

### Dual-Mode Storage

| Mode | Path | When |
|---|---|---|
| **User mode** (default) | `~/.local/share/appimages/` | Default, no root needed |
| **System mode** | `/opt/appimages/` | User explicitly chooses + has root/sudo |

**Fallback logic:**
1. User selects "Install system-wide" → check for sudo/pkexec
2. If root access available → install to `/opt/appimages/`, desktop entry to `/usr/share/applications/`
3. If no root → fall back to user mode with a notification
4. Default (no choice) → user mode

### Database

SQLite database at `~/.local/share/appimagekit/registry.db`

```sql
CREATE TABLE apps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    filename        TEXT NOT NULL,
    install_path    TEXT NOT NULL,
    desktop_entry   TEXT,             -- path to .desktop file
    icon_path       TEXT,             -- path to extracted icon
    version         TEXT,
    size_bytes      INTEGER,
    install_mode    TEXT DEFAULT 'user',  -- 'user' or 'system'
    update_source   TEXT,             -- 'appimageupdate', 'github', 'manual'
    update_url      TEXT,             -- GitHub repo URL or manual URL
    zsync_url       TEXT,             -- zsync URL if available
    category        TEXT,
    sandboxed       BOOLEAN DEFAULT 0,
    sandbox_type    TEXT,             -- 'firejail', 'bubblewrap', or null
    autostart       BOOLEAN DEFAULT 0,
    checksum_sha256 TEXT,
    installed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 🖼️ UI Design

### Main Window Layout

```
┌─────────────────────────────────────────────────────────┐
│  ☰  AppImageKit                        🔍  ⚙️  ─ □ ✕  │
├────────────┬────────────────────────────────────────────┤
│            │                                            │
│  📁 All    │  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  │
│  🔄 Updates│  │ Icon │  │ Icon │  │ Icon │  │ Icon │  │
│  ⭐ Pinned │  │ Name │  │ Name │  │ Name │  │ Name │  │
│            │  │ v1.2 │  │ v3.0 │  │ v2.1 │  │ v1.0 │  │
│ Categories │  └──────┘  └──────┘  └──────┘  └──────┘  │
│  🎮 Games  │                                            │
│  🛠️ Dev    │  ┌──────┐  ┌──────┐  ┌──────┐            │
│  🎨 Design │  │ Icon │  │ Icon │  │  +   │            │
│  📝 Office │  │ Name │  │ Name │  │ Add  │            │
│  🌐 Web    │  │ v1.5 │  │ v4.2 │  │ New  │            │
│  📦 Other  │  └──────┘  └──────┘  └──────┘            │
│            │                                            │
│ ─────────  │                                            │
│ 💾 12 apps │                                            │
│ 📊 3.2 GB  │                                            │
│ 🔄 2 updates│                                           │
├────────────┴────────────────────────────────────────────┤
│  Drag & drop .appimage files here to install            │
└─────────────────────────────────────────────────────────┘
```

### App Detail View (clicking a card)

```
┌─────────────────────────────────────────────────────────┐
│  ← Back                                                 │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   🖼️ Large Icon                                        │
│   Firefox Nightly                                       │
│   Version 128.0a1  •  142 MB  •  Installed 3 days ago  │
│                                                         │
│   [▶ Launch]  [🔄 Update]  [✏️ Edit Desktop Entry]     │
│   [🗑️ Uninstall]  [📋 Details]                         │
│                                                         │
│   ─────────────────────────────────────────────         │
│   Path: ~/.local/share/appimages/firefox-nightly.AppImage│
│   Desktop: ~/.local/share/applications/firefox-nightly.desktop│
│   Sandbox: ❌ Not sandboxed                             │
│   Update source: GitHub (user/repo)                     │
│   SHA256: a1b2c3d4...                                   │
│   Autostart: Off                                        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Desktop Entry Editor Dialog

- Editable fields: Name, Comment, Exec (args), Icon, Categories, Terminal, StartupWMClass
- Live preview of the .desktop file
- Save / Reset / Cancel buttons

---

## ⚡ Core Features (v1)

### 1. Install AppImage
- **Drag & drop** onto the window
- **File picker** button ("+" card in grid)
- **CLI**: `appimagekit install /path/to/app.AppImage`
- Flow:
  1. Copy/move AppImage to storage directory
  2. `chmod +x` the file
  3. Extract icon via `--appimage-extract` (look for `.DirIcon`, `*.png`, `*.svg`)
  4. Parse embedded `.desktop` for metadata (name, categories, etc.)
  5. Generate `.desktop` entry pointing to the stored AppImage
  6. Compute SHA256 checksum
  7. Register in SQLite database
  8. Detect sandbox status

### 2. Uninstall AppImage
- Remove AppImage file
- Remove `.desktop` entry
- Remove extracted icon
- Remove autostart entry if exists
- Remove from database

### 3. Launch AppImage
- Direct execution from GUI (double-click card or Launch button)
- Option to launch with custom arguments

### 4. Desktop Entry Management
- Auto-generated on install
- Full editor dialog (name, icon, categories, exec args, terminal, etc.)
- Preview the raw `.desktop` file content
- Reset to auto-detected defaults

### 5. Update System (3-tier cascade)
- **Tier 1 — AppImageUpdate/zsync:** Check for embedded update info via `--appimage-updateinformation`, use zsync for delta updates
- **Tier 2 — GitHub Releases:** User provides `owner/repo`, app queries GitHub API for latest release, downloads new `.AppImage` asset
- **Tier 3 — Manual URL:** User provides a direct download URL, app checks `Last-Modified` / `Content-Length` headers for changes
- **Batch update:** "Update All" button
- **Notification badge** on sidebar when updates are available

### 6. Disk Usage Tracking
- Show per-app file size in card and details view
- Total usage in sidebar footer
- Sort by size option

### 7. Sandbox Detection
- Detect if AppImage uses firejail or bubblewrap
- Show sandbox status in app details
- Informational only for v1 (not managing sandbox configs)

### 8. Backup & Export
- Export installed apps list as JSON (paths, metadata, update sources)
- Import from JSON to re-install on a new system
- Option to copy actual AppImage files to a backup directory

### 9. AppImage Hub Integration
- Fetch catalog from `appimage.github.io` API
- Browse/search available AppImages
- One-click download and install
- Show if an installed app is from the Hub

### 10. Startup/Autostart Management
- Toggle autostart for any installed AppImage
- Creates/removes `.desktop` in `~/.config/autostart/`
- Visual indicator in grid card

### 11. Integrity Verification
- Compute and store SHA256 on install
- Verify checksum hasn't changed (detect corruption/tampering)
- Show verification status in details

---

## 🖥️ CLI Interface

```bash
# Install
appimagekit install <path-to-appimage> [--system] [--category <cat>]

# List installed
appimagekit list [--json] [--category <cat>]

# Update
appimagekit update <app-name>    # Update specific app
appimagekit update --all         # Update all apps

# Uninstall
appimagekit uninstall <app-name>

# Launch
appimagekit launch <app-name> [-- extra-args]

# Info
appimagekit info <app-name>

# Backup
appimagekit backup export <output-path>
appimagekit backup import <backup-file>

# Verify
appimagekit verify <app-name>

# Search Hub
appimagekit search <query>
```

---

## 🗓️ Development Phases

### Phase 1 — Foundation (Core)
- [ ] Project scaffolding (meson, pyproject.toml)
- [ ] SQLite database & models
- [ ] AppImage installer service (copy, chmod, extract icon/metadata)
- [ ] Desktop entry generator
- [ ] Basic GTK4/Libadwaita window with grid view
- [ ] App cards with icon, name, version
- [ ] File picker to add AppImages
- [ ] Launch functionality
- [ ] Uninstall functionality
- [ ] CLI basics (install, list, uninstall, launch)

### Phase 2 — Polish & Updates
- [ ] Drag-and-drop support
- [ ] Desktop entry editor dialog
- [ ] Sidebar with categories and filters
- [ ] Search bar
- [ ] Update system (all 3 tiers)
- [ ] Disk usage display
- [ ] User/System mode selection

### Phase 3 — Advanced Features
- [ ] Sandbox detection
- [ ] Integrity verification (SHA256)
- [ ] Backup/export/import
- [ ] Autostart management
- [ ] AppImage Hub browser
- [ ] Batch operations (update all, verify all)
- [ ] Settings/preferences dialog

### Phase 4 — Distribution
- [ ] Package AppImageKit as an AppImage itself
- [ ] Create Flatpak manifest (optional)
- [ ] CI/CD with GitHub Actions
- [ ] AppImage Hub submission

---

## 🔧 Dependencies

### Runtime
- Python 3.11+
- GTK4 / Libadwaita (system packages)
- PyGObject (`gi` bindings)
- `click` (CLI)
- `requests` (HTTP for updates/hub)
- `pyyaml` (optional config)

### System (for AppImage operations)
- `file` command (detect AppImage type)
- `zsync` / `zsync2` (for AppImageUpdate delta updates — optional)
- `pkexec` / `sudo` (for system-wide installs)

### Development
- `meson` / `ninja`
- `pytest`
- `black` / `ruff` (formatting/linting)

---

## 📝 Notes

- The app ID follows reverse-DNS: `io.github.revanthlol.appimagekit`
- All paths use XDG Base Directory spec where applicable
- Desktop entries conform to the [freedesktop.org Desktop Entry Specification](https://specifications.freedesktop.org/desktop-entry-spec/latest/)
- The app handles both Type 1 (ISO 9660) and Type 2 (squashfs) AppImages
- GUI operations run async (GLib.idle_add / threading) to keep UI responsive
