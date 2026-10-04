#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec /usr/bin/retroarch --config /userdata/system/configs/retroarch/retroarchcustom.cfg \
    -L ./2048_libretro.so
