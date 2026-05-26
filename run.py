from __future__ import annotations

import sys
from pathlib import Path


# Set per-monitor DPI awareness V2 *before* anything imports Tkinter or creates Tk.
# Visual overlay relies on physical pixel coords; without this, on 125%/150% scaling
# winfo_screenwidth() returns logical pixels and overlay coords drift.
try:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
except Exception:
    pass


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from computer_use_anywhere.ui import launch


if __name__ == "__main__":
    raise SystemExit(launch())
