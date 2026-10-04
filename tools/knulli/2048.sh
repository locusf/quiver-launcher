#!/bin/sh
set -eu
cd "$(dirname "$0")"
: "${SDL_GAMECONTROLLERCONFIG:?Launch Quiver from Knulli Ports to provide controller mappings}"
exec /usr/bin/retroarch --config /userdata/system/configs/retroarch/retroarchcustom.cfg \
    --appendconfig ./quiver-controls.cfg -L ./2048_libretro.so
