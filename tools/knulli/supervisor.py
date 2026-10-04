#!/usr/bin/env python3
"""Release the framebuffer and controller before starting a game."""
import json
import fcntl
import os
from pathlib import Path
import subprocess
import struct
import sys


def show_first_page():
    # Avalonia fbdev renders page zero; EmulationStation/SDL may leave page one visible.
    with open("/dev/fb0", "rb+", buffering=0) as framebuffer:
        info = bytearray(160)
        fcntl.ioctl(framebuffer, 0x4600, info)
        struct.pack_into("II", info, 16, 0, 0)
        fcntl.ioctl(framebuffer, 0x4606, info)


def main():
    root = Path(os.environ.get("QUIVER_DATA_HOME", "/userdata/system/quiver-launcher"))
    request_path = root / "launch-request.json"
    while True:
        request_path.unlink(missing_ok=True)
        show_first_page()
        result = subprocess.run(["./QuiverLauncher.Desktop", "--knulli"], check=False)
        if result.returncode != 75:
            return result.returncode
        request = json.loads(request_path.read_text())
        request_path.unlink()
        result = subprocess.run(
            [request["FileName"], *request["Arguments"]],
            cwd=request["WorkingDirectory"],
            env={key: value for key, value in request["Environment"].items() if value is not None},
            check=False,
        )
        if result.returncode:
            print(f"Game exited with status {result.returncode}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
