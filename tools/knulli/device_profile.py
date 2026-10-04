#!/usr/bin/env python3
"""Read-only hardware metadata for the build agent. Never exports the environment."""
import ctypes
import json
import os
from pathlib import Path
import platform
import sys


def text(path):
    return Path(path).read_text().strip("\0\n ")


def controllers():
    sdl = ctypes.CDLL("libSDL2-2.0.so.0")
    sdl.SDL_GetError.restype = ctypes.c_char_p
    sdl.SDL_JoystickNameForIndex.restype = ctypes.c_char_p
    sdl.SDL_GameControllerMappingForDeviceIndex.restype = ctypes.c_void_p
    sdl.SDL_free.argtypes = [ctypes.c_void_p]
    sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
    for name in ("SDL_JoystickNumButtons", "SDL_JoystickNumAxes", "SDL_JoystickNumHats", "SDL_JoystickClose"):
        getattr(sdl, name).argtypes = [ctypes.c_void_p]
    if sdl.SDL_InitSubSystem(0x2000) < 0:
        raise RuntimeError(sdl.SDL_GetError().decode())
    try:
        database = Path("/tmp/gamecontrollerdb.txt")
        if database.is_file():
            sdl.SDL_GameControllerAddMapping.argtypes = [ctypes.c_char_p]
            for line in database.read_text().splitlines():
                if line.strip() and not line.startswith("#"):
                    if sdl.SDL_GameControllerAddMapping(line.encode()) < 0:
                        raise RuntimeError(f"Invalid Knulli SDL mapping: {sdl.SDL_GetError().decode()}")
        result = []
        for index in range(min(sdl.SDL_NumJoysticks(), 8)):
            joystick = sdl.SDL_JoystickOpen(index)
            if not joystick:
                raise RuntimeError(sdl.SDL_GetError().decode())
            try:
                mapping = sdl.SDL_GameControllerMappingForDeviceIndex(index)
                try:
                    result.append({
                        "name": (sdl.SDL_JoystickNameForIndex(index) or b"").decode(),
                        "index": index,
                        "buttons": sdl.SDL_JoystickNumButtons(joystick),
                        "axes": sdl.SDL_JoystickNumAxes(joystick),
                        "hats": sdl.SDL_JoystickNumHats(joystick),
                        "sdl_mapping": ctypes.string_at(mapping).decode() if mapping else None,
                    })
                finally:
                    if mapping:
                        sdl.SDL_free(mapping)
            finally:
                sdl.SDL_JoystickClose(joystick)
        return result
    finally:
        sdl.SDL_QuitSubSystem(0x2000)


def collect():
    libraries = ("libSDL2-2.0.so.0", "libSDL2_image-2.0.so.0", "libSDL2_mixer-2.0.so.0",
                 "libSDL2_ttf-2.0.so.0", "libEGL.so.1", "libGLESv2.so.2", "libGL.so.1",
                 "libX11.so.6", "libvulkan.so.1", "libopenal.so.1")
    return {
        "schema": 1,
        "architecture": platform.machine(),
        "model": text("/proc/device-tree/model"),
        "kernel": platform.release(),
        "libc": list(platform.libc_ver()),
        "display": {
            "framebuffer": Path("/dev/fb0").exists(),
            "virtual_size": text("/sys/class/graphics/fb0/virtual_size"),
            "bits_per_pixel": text("/sys/class/graphics/fb0/bits_per_pixel"),
            "drm": Path("/dev/dri").exists(),
            "x11": bool(os.environ.get("DISPLAY")),
            "wayland": bool(os.environ.get("WAYLAND_DISPLAY")),
        },
        "libraries": [name for name in libraries if any(
            (Path(directory) / name).exists() for directory in ("/usr/lib", "/usr/lib64", "/lib", "/lib64"))],
        "controllers": controllers(),
    }


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), sort_keys=True))
    except (OSError, RuntimeError, UnicodeError) as error:
        print(f"Cannot inspect Knulli hardware: {error}", file=sys.stderr)
        sys.exit(1)
