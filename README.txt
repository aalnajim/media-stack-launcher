1) Create project folder
mkdir -p ~/Desktop/media-launcher
cd ~/Desktop/media-launcher

2) Put these files inside:
- media_stack_launcher_gui.py
- build_app.sh
- requirements.txt

3) Create venv
python3 -m venv .venv

4) Activate venv
source .venv/bin/activate

5) Install build dependency inside venv only
pip install -r requirements.txt

6) Test the GUI
python media_stack_launcher_gui.py

7) Build the macOS app
chmod +x build_app.sh
./build_app.sh

8) Open the app
open "dist/Media Stack Launcher.app"

Notes:
- The app stops only the services it started itself.
- If Jackett/Bazarr/Sonarr/Radarr are already running before opening the app, it will not stop those.
- You may need to set executable paths manually if Detect does not find them.