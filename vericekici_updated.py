from __future__ import annotations

import os
import runpy
from pathlib import Path


if __name__ == "__main__":
    script = Path(__file__).resolve().parent / "testdata" / "vericekici_updated.py"
    os.chdir(script.parent)
    runpy.run_path(str(script), run_name="__main__")
