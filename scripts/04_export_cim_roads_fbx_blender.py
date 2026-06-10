#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Blender-side exporter for the generated CIM road OBJ module."""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blender.fbx_export import MODULE_EXPORTS, export_junction_debug_fbxs, export_obj_to_fbx


def main() -> None:
    obj_path, fbx_path = MODULE_EXPORTS["roads"]
    export_obj_to_fbx(obj_path, fbx_path)
    if str(os.environ.get("CIM_ROAD_EXPORT_JUNCTION_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}:
        export_junction_debug_fbxs()
    else:
        print("Skipped junction debug FBX export (set CIM_ROAD_EXPORT_JUNCTION_DEBUG=1 to enable).", flush=True)


if __name__ == "__main__":
    main()
