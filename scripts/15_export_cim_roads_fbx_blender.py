#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Blender-side exporter for only the generated CIM road OBJ module."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from blender.fbx_export import MODULE_EXPORTS, export_obj_to_fbx


def main() -> None:
    obj_path, fbx_path = MODULE_EXPORTS["roads"]
    export_obj_to_fbx(obj_path, fbx_path)


if __name__ == "__main__":
    main()
