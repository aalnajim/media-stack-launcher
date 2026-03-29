#!/bin/bash
set -euo pipefail

APP_NAME="Media Stack Launcher"

if [ ! -d ".venv" ]; then
  echo "Error: .venv not found"
  echo "Create it first with:"
  echo "python3 -m venv .venv"
  exit 1
fi

source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name "$APP_NAME" \
  media_stack_launcher_gui.py

echo
echo "Build complete."
echo "App location:"
echo "dist/$APP_NAME.app"