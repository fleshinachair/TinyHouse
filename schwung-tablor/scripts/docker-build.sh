#!/usr/bin/env bash
# Runs INSIDE the ubuntu:22.04 build container on the VPS. Do not run on a host
# with newer glibc — the artifacts would not load on the Move.
set -euo pipefail
TARGET="${1:-all}"

python3 tools/gen_params.py
python3 tools/check_config.py
# Help lines that run off the 128px screen, or use a character the device font
# has no glyph for, fail silently on the device -- no wrap, no marker, no log.
python3 tools/check_help.py

# ---- Native DSP tests: compile and RUN in-container before cross-compiling.
# A red test here fails the whole build.
echo "=== native DSP tests ==="
mkdir -p build-native
g++ -O2 -std=c++17 -Wall \
    tests/test_dsp.cpp src/ported/wavetable.cpp \
    src/ported/audiofilter/ParametricCreator.cpp \
    -Isrc -Isrc/ported -o build-native/tablor_test -lm
./build-native/tablor_test
g++ -O2 -std=c++17 -Wall \
    tests/test_engine.cpp src/dsp/engine.cpp src/ported/wavetable.cpp \
    src/ported/audiofilter/ParametricCreator.cpp \
    -Isrc -Isrc/ported -Isrc/dsp -o build-native/tablor_engine_test -lm
./build-native/tablor_engine_test

cmake -B build -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64-toolchain.cmake \
    -DCMAKE_BUILD_TYPE=Release
cmake --build build --target "$TARGET" -j"$(nproc)"

# ---- Package for the Module Store ----
rm -rf dist/tinyhouse
mkdir -p dist/tinyhouse/wavetables dist/tinyhouse/presets
cp build/dsp.so           dist/tinyhouse/
cp src/module.json        dist/tinyhouse/
cp src/ui_pages.json      dist/tinyhouse/
cp src/help.json          dist/tinyhouse/
cp src/web_ui.html        dist/tinyhouse/
cp src/ui/canvas.js       dist/tinyhouse/
cp build/import_bundle.zip dist/tinyhouse/ 2>/dev/null || true    # python tools/build_import_bundle.py, run beforehand
cp -r src/wavetables/. dist/tinyhouse/wavetables/ 2>/dev/null || true
(cd dist && tar -czf tinyhouse-module.tar.gz tinyhouse/)
echo "Tarball: dist/tinyhouse-module.tar.gz"

echo; echo "=== Build output ==="
find build -maxdepth 1 -type f \( -name "*.so" -o -name "tablor_*" \) \
    -exec sh -c 'printf "%s\n  " "$1"; file -b "$1"' _ {} \;
