#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Generate the CIM road OBJ and semantics.

Separate junction debug models are opt-in with CIM_ROAD_EXPORT_JUNCTION_DEBUG=1.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DATA_DIR = ROOT / "data" / "Data"
ROAD_SOURCE = DATA_DIR / "道路中心线300km_增加名称" / "道路中心线300km_增加名称.shp"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ["CIM_ROAD_DATA_DIR"] = str(DATA_DIR)
os.environ["CIM_ROAD_ROADS_FILE"] = str(ROAD_SOURCE)

from city.pipeline import generate_roads_only


if __name__ == "__main__":
    generate_roads_only()
