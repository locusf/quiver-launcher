#!/usr/bin/env python3
"""Measure cache hits and header invalidation across fresh, isolated compiler containers."""
import json
import argparse
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "agent"))
from build_agent import BuildAgent


def stats(path):
    return {key: int(value) for key, value in
            (line.split() for line in path.read_text().splitlines())}


def hits(values):
    return values.get("direct_cache_hit", 0) + values.get("preprocessed_cache_hit", 0)


def script(value, exit_code):
    return f"""set -eu
mkdir -p /tmp/game
cd /tmp/game
printf '#define VALUE {value}\\n' > value.h
printf '#include "value.h"\\nint value(void) {{ return VALUE; }}\\n' > direct.c
printf '#include "value.h"\\nint cpp_value() {{ return VALUE; }}\\n' > direct.cpp
ccache --print-stats > /output/before.stats
aarch64-linux-gnu-gcc -O2 -c direct.c -o /output/direct.o
aarch64-linux-gnu-g++ -O2 -c direct.cpp -o /output/direct-cpp.o
ccache --print-stats > /output/direct.stats
printf '#include "value.h"\\nint cmake_value(void) {{ return VALUE; }}\\n' > cmake.c
printf '#include "value.h"\\nint cmake_cpp_value() {{ return VALUE; }}\\n' > cmake.cpp
printf 'cmake_minimum_required(VERSION 3.16)\\nproject(cachecheck LANGUAGES C CXX)\\nadd_library(check STATIC cmake.c cmake.cpp)\\n' > CMakeLists.txt
cmake -S . -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE=/opt/cross/aarch64.cmake
cmake --build build
ccache --print-stats > /output/cmake.stats
printf '#include "value.h"\\nint meson_value(void) {{ return VALUE; }}\\n' > meson.c
printf '#include "value.h"\\nint meson_cpp_value() {{ return VALUE; }}\\n' > meson.cpp
printf "project('cachecheck', 'c', 'cpp')\\nstatic_library('check', ['meson.c', 'meson.cpp'])\\n" > meson.build
meson setup meson-build --cross-file /opt/cross/aarch64.ini
meson compile -C meson-build
ccache --print-stats > /output/meson.stats
exit {exit_code}
"""


def main(cache_directory=None):
    with tempfile.TemporaryDirectory(prefix="quiver-ccache-check-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        cache = cache_directory or (root / "ccache")
        outputs = []
        # The first completed compilations survive a failed recipe; the third changes a header.
        for index, (value, result) in enumerate(((1, 1), (1, 0), (2, 0)), 1):
            engine = BuildAgent(source, root / f"round-{index}", {}, cache_directory=cache)
            directory = engine.output / "attempt-1"
            (directory / "recipe").mkdir(parents=True)
            (directory / "files").mkdir()
            (directory / "recipe" / "build.sh").write_text(script(value, result))
            actual = engine.run_container(directory)
            if actual != result:
                raise RuntimeError(f"Cache verification compile failed:\n{(directory / 'build.log').read_text()}")
            outputs.append(directory / "files")
        previous = stats(outputs[1] / "before.stats")
        hit_counts = {}
        for toolchain in ("direct", "cmake", "meson"):
            current = stats(outputs[1] / f"{toolchain}.stats")
            hit_counts[toolchain] = hits(current) - hits(previous)
            if hit_counts[toolchain] < 2:
                raise RuntimeError(f"{toolchain}: expected C and C++ cache hits, found {hit_counts[toolchain]}")
            previous = current
        if (outputs[0] / "direct.o").read_bytes() != (outputs[1] / "direct.o").read_bytes():
            raise RuntimeError("An unchanged source/header produced a different cached object.")
        if (outputs[1] / "direct.o").read_bytes() == (outputs[2] / "direct.o").read_bytes():
            raise RuntimeError("A changed header incorrectly reused the old object.")
        if stats(outputs[2] / "meson.stats").get("cache_miss", 0) <= previous.get("cache_miss", 0):
            raise RuntimeError("Changed headers did not produce cache misses.")
        print(json.dumps({"cache_hits_on_second_attempt": hit_counts,
                          "failed_attempt_cache_reused": True, "header_change_invalidated": True,
                          "cache_hits_restored_from_earlier_run": hits(stats(outputs[0] / "before.stats"))}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-directory", type=Path)
    main(parser.parse_args().cache_directory)
