#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Inspect generated underground pipeline FBX files inside Blender."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = [
    ROOT / "output" / "fbx" / "modules" / "cim4" / "city_underground_pipelines_sys02.fbx",
    ROOT / "output" / "fbx" / "modules" / "cim4" / "city_underground_pipelines_ws.fbx",
]


def blender_script_args() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def material_color(obj: bpy.types.Object) -> tuple[float, float, float, float] | None:
    if not obj.data.materials:
        return None
    material = obj.data.materials[0]
    return tuple(round(float(value), 4) for value in material.diffuse_color)


def custom_prop(obj: bpy.types.Object, name: str) -> str:
    return str(obj.get(name) or obj.get(f"cim_{name}") or "")


def layer_name(obj: bpy.types.Object) -> str:
    return str(obj.get("cim_layer_name") or obj.get("layer_name") or "")


def median_dimensions(objects: list[bpy.types.Object]) -> tuple[float, float, float] | None:
    if not objects:
        return None
    dimensions = [[float(value) for value in obj.dimensions] for obj in objects]
    return tuple(round(median([dims[index] for dims in dimensions]), 3) for index in range(3))


def median_mesh_counts(objects: list[bpy.types.Object]) -> tuple[int, int] | None:
    if not objects:
        return None
    vertex_count = int(median([len(obj.data.vertices) for obj in objects]))
    face_count = int(median([len(obj.data.polygons) for obj in objects]))
    return vertex_count, face_count


def inspect_fbx(path: Path) -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.import_scene.fbx(filepath=str(path))

    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    roles = Counter(custom_prop(obj, "component_role") for obj in meshes)
    kinds = Counter(custom_prop(obj, "component_kind") for obj in meshes)
    layers = Counter(layer_name(obj) for obj in meshes)
    colors = Counter(material_color(obj) for obj in meshes)

    print(f"READBACK {path.name}")
    print(f" objects {len(meshes)}")
    print(f" roles {dict(sorted(roles.items()))}")
    print(f" kinds {dict(sorted(kinds.items()))}")
    print(f" layers {dict(sorted(layers.items()))}")
    print(f" colors {dict(sorted(colors.items(), key=lambda item: str(item[0])))}")

    for layer in sorted(name for name in layers if "Cover" in name or "Chamber" in name):
        objects = [obj for obj in meshes if layer_name(obj) == layer]
        print(f" dims {layer} {len(objects)} {median_dimensions(objects)}")
    for layer in sorted(name for name in layers if name):
        objects = [obj for obj in meshes if layer_name(obj) == layer]
        print(f" mesh_stats {layer} {len(objects)} {median_mesh_counts(objects)}")


def main() -> None:
    args = blender_script_args()
    paths = [Path(arg) for arg in args] if args else DEFAULT_PATHS
    for path in paths:
        inspect_fbx(path)


if __name__ == "__main__":
    main()
