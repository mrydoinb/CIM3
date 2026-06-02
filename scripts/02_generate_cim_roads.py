#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Generate the CIM road OBJ, semantics, and separate junction debug models."""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TEST_DATA_DIR = ROOT / "data" / "Data_clip_1_10"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ["CIM_ROAD_DATA_DIR"] = str(TEST_DATA_DIR)

from city.pipeline import generate_roads_only


if __name__ == "__main__":
    generate_roads_only()
