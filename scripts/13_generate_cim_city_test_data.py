#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Run the city workflow against the clipped 1/10 test dataset."""

from __future__ import annotations

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEST_DATA_DIR = ROOT / "data" / "Data_clip_1_10"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("CIM_ROAD_DATA_DIR", str(TEST_DATA_DIR))

from city.pipeline import main


if __name__ == "__main__":
    main()
