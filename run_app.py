#!/usr/bin/env python3
"""Convenience launcher for TikTok Creator Product Analytics Scraper."""

import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from shadowkreator.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
