import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    script_path = Path(__file__).resolve().parent / "testdata" / "universe_scanner.py"
    sys.path.insert(0, str(script_path.parent))
    runpy.run_path(str(script_path), run_name="__main__")
