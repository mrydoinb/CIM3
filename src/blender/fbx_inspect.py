#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Inspect material names after importing an FBX in Blender."""

from __future__ import annotations

from pathlib import Path
from collections import Counter
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FBX = ROOT / "output" / "fbx" / "cim_city.fbx"


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def arg_path() -> Path:
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1 :]
        if args:
            return Path(args[0]).resolve()
    return DEFAULT_FBX


def main() -> None:
    fbx_path = arg_path()
    if not fbx_path.exists():
        raise FileNotFoundError(f"Missing FBX input: {fbx_path}")

    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(fbx_path))

    mesh_count = 0
    material_counter: Counter[str] = Counter()
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        mesh_count += 1
        for material in obj.data.materials:
            material_counter[material.name] += 1

    print(f"FBX inspected: {fbx_path}")
    print(f"- mesh_objects: {mesh_count}")
    print("- materials:")
    for name, count in sorted(material_counter.items()):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
