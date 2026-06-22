#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Print source subway template transform diagnostics."""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy
from mathutils import Vector


DEFAULT_TEMPLATE = Path.home() / "Desktop" / "chk" / "\u5730\u94c1" / "subway01.blend"


def dims(points: list[Vector]) -> list[float]:
    return [
        max(getattr(point, axis) for point in points) - min(getattr(point, axis) for point in points)
        for axis in "xyz"
    ]


def bounds(points: list[Vector]) -> list[list[float]]:
    return [
        [min(getattr(point, axis) for point in points) for axis in "xyz"],
        [max(getattr(point, axis) for point in points) for axis in "xyz"],
    ]


def inspect_object(name: str) -> dict:
    obj = bpy.data.objects[name]
    local = [Vector(corner) for corner in obj.bound_box]
    world = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return {
        "name": obj.name,
        "type": obj.type,
        "matrix_world": [[round(float(value), 6) for value in row] for row in obj.matrix_world],
        "location": [round(float(value), 6) for value in obj.location],
        "rotation_euler": [round(float(value), 6) for value in obj.rotation_euler],
        "scale": [round(float(value), 6) for value in obj.scale],
        "local_bounds": bounds(local),
        "world_bounds": bounds(world),
        "local_dims": dims(local),
        "world_dims": dims(world),
        "data_vertex_count": len(obj.data.vertices) if getattr(obj, "data", None) else 0,
    }


def main() -> None:
    template_path = Path(os.environ.get("CIM_SUBWAY_TEMPLATE_BLEND") or DEFAULT_TEMPLATE)
    bpy.ops.wm.open_mainfile(filepath=str(template_path))
    names = ["Mesh.004", "Mesh.003", "Mesh.037", "Mesh.038", "Mesh.039", "Mesh.040", "Mesh.041"]
    report = {"template": str(template_path), "objects": [inspect_object(name) for name in names if name in bpy.data.objects]}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
