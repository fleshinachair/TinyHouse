#!/usr/bin/env bash
# Build Tablor with Zig instead of Docker: native test binaries on the dev machine and
# aarch64 binaries for the Move (glibc 2.35, like the device). Absolute paths throughout,
# because Zig's cache keys on the path it is given and reused stale objects with relative ones.
#
#   scripts/zig-build.sh native        native engine/dsp tests and the calibration renderer
#   scripts/zig-build.sh arm           dsp.so and the on-device test binaries -> build/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && { pwd -W 2>/dev/null || pwd; })"
SRC="-I$ROOT/src -I$ROOT/src/dsp -I$ROOT/src/ported -I$ROOT/src/host"
ENGINE="$ROOT/src/dsp/engine.cpp $ROOT/src/ported/wavetable.cpp $ROOT/src/ported/audiofilter/ParametricCreator.cpp"
PLUGIN="$ROOT/src/dsp/tablor_plugin.cpp $ROOT/src/dsp/engine.cpp $ROOT/src/dsp/wt/loader.cpp $ROOT/src/ported/wavetable.cpp $ROOT/src/ported/audiofilter/ParametricCreator.cpp"
OUT="${TB_OUT:-$ROOT/build}"
mkdir -p "$OUT"

case "${1:-native}" in
native)
    CXX="zig c++ -O2 -std=c++17 -D_USE_MATH_DEFINES"
    $CXX $SRC "$ROOT/tests/test_engine.cpp" $ENGINE -o "$OUT/native_engine_test.exe"
    $CXX $SRC "$ROOT/tests/test_dsp.cpp" "$ROOT/src/ported/wavetable.cpp" "$ROOT/src/ported/audiofilter/ParametricCreator.cpp" -o "$OUT/native_dsp_test.exe"
    $CXX $SRC "$ROOT/tests/test_fx.cpp" $ENGINE -o "$OUT/native_fx_test.exe"
    $CXX $SRC "$ROOT/tests/test_demo.cpp" $ENGINE -o "$OUT/native_demo_test.exe"
    $CXX $SRC "$ROOT/tests/test_conv.cpp" -o "$OUT/native_conv_test.exe"
    $CXX $SRC "$ROOT/tests/test_partjoin.cpp" -o "$OUT/native_partjoin_test.exe"
    $CXX $SRC "$ROOT/tests/test_granular.cpp" -o "$OUT/native_granular_test.exe"
    $CXX $SRC "$ROOT/../calibration/tablor_cli.cpp" $ENGINE -o "$ROOT/../calibration/tablor_cli.exe"
    # the same with the same noise every run (calibration/bank_score.py: an untouched preset scores the same before and after a fix)
    $CXX -DTB_DETERMINISTIC_NOISE $SRC "$ROOT/../calibration/tablor_cli.cpp" $ENGINE -o "$ROOT/../calibration/tablor_cli_det.exe"
    ;;
arm)
    CXX="zig c++ -target aarch64-linux-gnu.2.35 -O3 -std=c++17 "
    $CXX $SRC -fPIC -shared -s $PLUGIN -lm -lpthread -o "$OUT/dsp.so"
    $CXX $SRC "$ROOT/tools/loadtest.cpp" -ldl -lm -o "$OUT/tablor_loadtest"
    $CXX $SRC "$ROOT/tools/tailbench.cpp" -ldl -lm -o "$OUT/tailbench"
    $CXX $SRC "$ROOT/tools/soak.cpp" -ldl -lm -o "$OUT/tablor_soak"
    $CXX $SRC "$ROOT/tools/asynccheck.cpp" -ldl -lm -o "$OUT/tablor_asynccheck"
    $CXX $SRC "$ROOT/tools/asyncstall.cpp" -lpthread -o "$OUT/tablor_asyncstall"
    $CXX $SRC "$ROOT/tests/test_dsp.cpp" "$ROOT/src/ported/wavetable.cpp" "$ROOT/src/ported/audiofilter/ParametricCreator.cpp" -lm -o "$OUT/tablor_test"
    $CXX $SRC "$ROOT/tests/test_engine.cpp" $ENGINE -lm -o "$OUT/tablor_engine_test"
    $CXX $SRC "$ROOT/tests/test_fx.cpp" $ENGINE -lm -o "$OUT/tablor_fx_test"
    $CXX $SRC "$ROOT/tools/enginebench.cpp" $ENGINE -lm -o "$OUT/tablor_enginebench"
    $CXX $SRC "$ROOT/tests/test_conv.cpp" -lm -o "$OUT/tablor_conv_test"
    $CXX $SRC "$ROOT/tools/convbench.cpp" -lm -o "$OUT/tablor_convbench"
    $CXX $SRC "$ROOT/tests/test_granular.cpp" -lm -o "$OUT/tablor_granular_test"
    ;;
*) echo "usage: $0 native|arm" >&2; exit 2 ;;
esac
echo "built: ${1:-native}"
