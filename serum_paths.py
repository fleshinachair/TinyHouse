"""Where Serum 2 lives on this machine. Set SERUM_ROOT (the 'Serum 2 Presets' folder, the one holding Presets, Tables, Samples and Multisamples)
and SERUM_VST (the plugin) if the defaults below are not right for you."""
import os
from pathlib import Path

_HOME = Path.home()
_CANDIDATES = [_HOME / "OneDrive/Documents/Xfer/Serum 2 Presets", _HOME / "Documents/Xfer/Serum 2 Presets"]   # Documents may be redirected to OneDrive

SERUM_ROOT = Path(os.environ["SERUM_ROOT"]) if os.environ.get("SERUM_ROOT") else next((p for p in _CANDIDATES if p.is_dir()), _CANDIDATES[-1])
SERUM_TABLES = SERUM_ROOT / "Tables"
SERUM_VST = os.environ.get("SERUM_VST", r"C:\Program Files\Common Files\VST3\Serum2.vst3")

if __name__ == "__main__":                       # used by the shell scripts: python serum_paths.py tables
    import sys
    print({"root": SERUM_ROOT, "tables": SERUM_TABLES, "vst": SERUM_VST}[sys.argv[1] if len(sys.argv) > 1 else "root"].as_posix())
