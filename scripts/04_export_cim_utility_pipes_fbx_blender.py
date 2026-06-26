#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Blender-side exporter for the generated CIM utility pipe OBJ module."""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blender.fbx_export import MODULE_FBX_DIR, MODULE_OBJ_DIR, export_obj_to_fbx


def utility_level() -> str:
    level = str(os.environ.get("CIM_UTILITY_LEVEL", "cim4")).strip().lower()
    return level if level in {"cim3", "cim4"} else "cim4"


def main() -> None:
    level = utility_level()
    obj_path = MODULE_OBJ_DIR / level / "city_utility_pipes.obj"
    fbx_path = MODULE_FBX_DIR / level / "city_utility_pipes.fbx"
    export_obj_to_fbx(obj_path, fbx_path)


if __name__ == "__main__":
    main()
