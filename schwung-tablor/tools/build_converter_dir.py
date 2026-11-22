#!/usr/bin/env python3
"""The module's own converter, for convert on load: build/converter/, shipped as <module>/converter on the Move.

The Move converts a preset again from its original when an update brings a newer converter (converter/reconvert.py, src/dsp/reconvert.h). It runs the
same code as the remote UI's import, so this folder is the import bundle (build_import_bundle.py: the converter, its data tables, the Serum preset
reader) unpacked, with what the Move's own Python 3.10 lacks:
  - numpy: the manylinux aarch64 wheel for CPython 3.10 (the Move has glibc 2.35), unpacked into lib/ without its tests; its licence stays with it,
  - zstandard: shim/zstandard.py, a stand-in for the few calls the preset reader makes, over the Move's own libzstd (ctypes).
The wheel is downloaded once into build/wheels/.

  python tools/build_converter_dir.py [--out build/converter]
"""
import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE.parent / "build"
NUMPY = "numpy==2.2.6"                      # the last numpy for CPython 3.10 (the Move's)
WHEEL_GLOB = "numpy-2.2.6-cp310-cp310-manylinux*aarch64*.whl"
DROP = ("/tests/", "/testing/", "/f2py/", "/_pyinstaller/", "/doc/")


def numpy_wheel() -> Path:
    d = BUILD / "wheels"
    hit = sorted(d.glob(WHEEL_GLOB))
    if hit:
        return hit[0]
    d.mkdir(parents=True, exist_ok=True)
    subprocess.check_call([sys.executable, "-m", "pip", "download", NUMPY, "--platform", "manylinux2014_aarch64", "--python-version", "3.10",
                           "--implementation", "cp", "--only-binary=:all:", "--no-deps", "-d", str(d), "-q"])
    hit = sorted(d.glob(WHEEL_GLOB))
    if not hit:
        sys.exit(f"no {WHEEL_GLOB} in {d}")
    return hit[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=BUILD / "converter")
    a = ap.parse_args()
    bundle = BUILD / "import_bundle.zip"
    subprocess.check_call([sys.executable, str(HERE / "build_import_bundle.py"), "--out", str(bundle)])
    shutil.rmtree(a.out, ignore_errors=True)
    a.out.mkdir(parents=True)
    zipfile.ZipFile(bundle).extractall(a.out)
    (a.out / "shim").mkdir()
    shutil.copyfile(HERE / "import_shim/zstandard_ctypes.py", a.out / "shim/zstandard.py")
    lib = a.out / "lib"
    n = 0
    with zipfile.ZipFile(numpy_wheel()) as z:
        for m in z.infolist():
            if m.is_dir() or any(x in "/" + m.filename for x in DROP) or m.filename.endswith((".pyi", ".c", ".h", ".pxd")):
                continue
            z.extract(m, lib)
            n += 1
    size = sum(f.stat().st_size for f in a.out.rglob("*") if f.is_file())
    print(f"{a.out}: converter + numpy ({n} files), {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
