# 📦 AppImageKit

> A modern GTK4/Libadwaita AppImage manager for Linux — install, update, organize, and launch AppImages with full desktop integration.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![GTK4](https://img.shields.io/badge/GTK-4-green.svg)

---

## ✨ Features

- **App Store-like UI** — Grid view with icons, names, versions, and categories
- **One-click Install** — Drag-and-drop, file picker, or CLI
- **Desktop Integration** — Auto-creates `.desktop` entries with extracted icons
- **Smart Updates** — AppImageUpdate (zsync), GitHub Releases, or manual URL
- **Desktop Entry Editor** — Edit app names, icons, categories, and launch args
- **Dual Storage** — User-local (`~/.local/share/appimages/`) or system-wide (`/opt/appimages/`)
- **AppImage Hub** — Browse and install from the AppImage catalog
- **Sandbox Detection** — See if apps use firejail/bubblewrap
- **Backup & Export** — Export your app list, import on another machine
- **Autostart** — Mark apps to launch on login
- **Integrity Checks** — SHA256 verification for all installed AppImages
- **CLI** — Full command-line interface for scripting and automation

## 🚀 Quick Start

```bash
# Clone
git clone https://github.com/revanthlol/appimagekit.git
cd appimagekit

# Install dependencies (Arch/Manjaro)
sudo pacman -S python gtk4 libadwaita python-gobject python-pip

# Install dependencies (Ubuntu/Debian)
sudo apt install python3 libgtk-4-dev libadwaita-1-dev python3-gi python3-pip

# Install Python deps
pip install -e .

# Run
appimagekit
```

## 🖥️ CLI Usage

```bash
appimagekit install ~/Downloads/MyApp.AppImage
appimagekit list
appimagekit update --all
appimagekit launch MyApp
appimagekit uninstall MyApp
appimagekit search "text editor"    # Search AppImage Hub
appimagekit backup export ~/backup.json
```

## 📋 Requirements

- Python 3.11+
- GTK4 & Libadwaita
- PyGObject
- Linux (any distro)

## 📄 License

MIT — see [LICENSE](LICENSE).

## 🗺️ Roadmap

See [PLAN.md](PLAN.md) for the full development plan.
