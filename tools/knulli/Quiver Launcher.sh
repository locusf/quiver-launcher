#!/bin/sh
set -eu
export QUIVER_KNULLI=1
export QUIVER_DATA_HOME="${QUIVER_DATA_HOME:-/userdata/system/quiver-launcher}"
export SDL_GAMECONTROLLERCONFIG_FILE="${SDL_GAMECONTROLLERCONFIG_FILE:-/tmp/gamecontrollerdb.txt}"
mkdir -p "$QUIVER_DATA_HOME"
cd /userdata/roms/ports/quiver-launcher
exec python3 supervisor.py >> "$QUIVER_DATA_HOME/launcher.log" 2>&1
