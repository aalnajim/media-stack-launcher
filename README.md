# media-stack-launcher

`media-stack-launcher` is a macOS desktop utility for starting, stopping, and opening a local media stack from one window.

It provides a Tkinter GUI for managing:

- Jackett
- Bazarr
- Sonarr
- Radarr
- qBittorrent

The launcher can detect app paths, start selected services, optionally open their web interfaces, run a cleanup pass before launch, and show diagnostics for ports and HTTP reachability. It is intended as a lightweight local control panel for a self-hosted media stack on macOS.

## Features

- Workflow presets for common service combinations
- Per-service executable path configuration
- Clean start option to free known ports and stop known processes
- Browser tab opening for selected service UIs
- Status table for service state and port usage
- Diagnostics window with port, PID, and HTTP health checks
- Preferences saved to `~/.media_stack_launcher.json`

## Requirements

- macOS
- Python 3
- Tkinter available in your Python installation

## Project Files

- `media_stack_launcher_gui.py` - main GUI application
- `build_app.sh` - builds a macOS `.app` bundle with PyInstaller
- `requirements.txt` - build dependency list

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python media_stack_launcher_gui.py
```

## Build the macOS App

```bash
chmod +x build_app.sh
./build_app.sh
open "dist/Media Stack Launcher.app"
```

## Notes

- The app is macOS-specific and uses `open`, `osascript`, and macOS app bundle paths.
- You may need to set executable paths manually if auto-detection does not find your installed apps.
- Cleanup logic targets known process names, app bundles, and configured ports for the supported services.
