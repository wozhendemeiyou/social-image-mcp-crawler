from __future__ import annotations

"""Absolute-path launcher for MCP clients with an arbitrary working directory."""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.chdir(ROOT)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from social_image_mcp.server import main


if __name__ == "__main__":
    main()
