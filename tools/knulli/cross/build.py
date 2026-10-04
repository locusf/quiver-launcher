#!/usr/bin/env python3
"""Best-effort cross build. Runs only in the unprivileged, offline build container."""
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys


def detect_build_system(source):
    for filename, system in [
        ("CMakeLists.txt", "cmake"), ("meson.build", "meson"),
        ("configure", "configure"), ("configure.ac", "autoconf"),
        ("Makefile", "make"), ("makefile", "make"),
    ]:
        if (source / filename).is_file():
            return source, system
    subprojects = [
        child for child in source.iterdir()
        if child.is_dir() and child.name not in ("external", "third_party", "vendor", "deps")
        and (child / "CMakeLists.txt").is_file()
    ]
    if len(subprojects) == 1:
        return subprojects[0], "cmake"
    raise RuntimeError("No unambiguous CMake, Meson, configure, or Make build found. This game needs a recipe.")


def run(*args, cwd, env=None):
    print("+ " + shlex.join(str(arg) for arg in args), flush=True)
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def is_arm64_executable(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:6] != b"\x7fELF\x02\x01":
            return False
        kind, machine = struct.unpack_from("<HH", header, 16)
        if machine != 183 or kind not in (2, 3):
            return False
        if kind == 2:
            return True
        offset = struct.unpack_from("<Q", header, 32)[0]
        size, count = struct.unpack_from("<HH", header, 54)
        if size < 4 or count > 1024:
            return False
        for index in range(count):
            stream.seek(offset + index * size)
            if stream.read(4) == b"\x03\x00\x00\x00":  # PT_INTERP distinguishes PIE from a shared library.
                return True
    return False


def main():
    work = Path("/tmp/game")
    shutil.copytree("/source", work, ignore=shutil.ignore_patterns(".git"), symlinks=True)
    source, system = detect_build_system(work)
    print(f"Detected {system} in {source.relative_to(work)}", flush=True)
    build = Path("/tmp/build")
    staging = Path("/tmp/staging")
    prefix = "/opt/quiver-game"
    environment = dict(os.environ, DESTDIR=str(staging))
    if system == "cmake":
        run("cmake", "-S", source, "-B", build, "-G", "Ninja",
            "-DCMAKE_TOOLCHAIN_FILE=/opt/cross/aarch64.cmake",
            "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={prefix}",
            "-DBUILD_TESTING=OFF", cwd=work)
        run("cmake", "--build", build, "--parallel", "2", cwd=work)
        run("cmake", "--install", build, cwd=work, env=environment)
    elif system == "meson":
        run("meson", "setup", build, source, "--cross-file", "/opt/cross/aarch64.ini",
            "--buildtype", "release", "--prefix", prefix, cwd=work)
        run("meson", "compile", "-C", build, "-j", "2", cwd=work)
        run("meson", "install", "-C", build, "--destdir", staging, cwd=work)
    else:
        if system == "autoconf":
            run("autoreconf", "-fi", cwd=source)
        if system in ("configure", "autoconf"):
            run("sh", "./configure", "--host=aarch64-linux-gnu", "--build=x86_64-linux-gnu",
                f"--prefix={prefix}", cwd=source)
        run("make", "-j2", "CC=aarch64-linux-gnu-gcc", "CXX=aarch64-linux-gnu-g++",
            "AR=aarch64-linux-gnu-ar", cwd=source)
        run("make", "install", f"DESTDIR={staging}", f"PREFIX={prefix}",
            f"prefix={prefix}", cwd=source)

    installed = staging / prefix.lstrip("/")
    executables = sorted(path for path in installed.rglob("*") if is_arm64_executable(path))
    if len(executables) != 1:
        raise RuntimeError(
            f"Installed {len(executables)} ARM64 executables; cannot identify one game entry point. "
            "A game-specific packaging recipe is needed.")
    # Reject links out of the package rather than shipping broken or host-specific paths.
    for path in installed.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(installed.resolve()):
            raise RuntimeError(f"Installed symlink escapes the game package: {path.relative_to(installed)}")
    relative = executables[0].relative_to(installed).as_posix()
    package = Path("/output/package")
    shutil.copytree(installed, package, symlinks=True)
    licenses = package / "source-licenses"
    licenses.mkdir(exist_ok=True)
    for path in work.iterdir():
        if path.is_file() and path.name.upper().startswith(("COPYING", "LICENSE", "NOTICE")):
            shutil.copy2(path, licenses / path.name)
    launcher = package / "launch.sh"
    launcher.write_text(
        '#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\n'
        'export LD_LIBRARY_PATH="$PWD/lib:$PWD/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        f'exec {shlex.quote("./" + relative)} "$@"\n')
    launcher.chmod(0o755)
    print(f"Packaged ARM64 executable: {relative}", flush=True)
    print("Compilation succeeded. Device graphics, dependencies, and game data still require verification.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"BUILD ATTEMPT FAILED: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
