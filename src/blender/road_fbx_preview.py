#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Render a top-down preview of the exported road FBX for visual QC."""

from __future__ import annotations

from pathlib import Path
import math
import sys

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FBX = ROOT / "output" / "fbx" / "modules" / "cim_city_roads.fbx"
DEFAULT_OUT = ROOT / "output" / "render" / "cim_city_roads_fbx_top.png"


def arg_path(default: Path, index: int) -> Path:
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1 :]
        if len(args) > index:
            return Path(args[index]).resolve()
    return default


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def mesh_bounds() -> tuple[Vector, Vector]:
    mins = Vector((math.inf, math.inf, math.inf))
    maxs = Vector((-math.inf, -math.inf, -math.inf))
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            mins.x = min(mins.x, point.x)
            mins.y = min(mins.y, point.y)
            mins.z = min(mins.z, point.z)
            maxs.x = max(maxs.x, point.x)
            maxs.y = max(maxs.y, point.y)
            maxs.z = max(maxs.z, point.z)
    if not math.isfinite(mins.x):
        raise RuntimeError("No mesh objects found for preview render.")
    return mins, maxs


def main() -> None:
    fbx_path = arg_path(DEFAULT_FBX, 0)
    out_path = arg_path(DEFAULT_OUT, 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(fbx_path))

    mins, maxs = mesh_bounds()
    center = (mins + maxs) * 0.5
    span_x = maxs.x - mins.x
    span_y = maxs.y - mins.y
    span_z = maxs.z - mins.z
    top_axis = min(("X", span_x), ("Y", span_y), ("Z", span_z), key=lambda item: item[1])[0]
    view_span = max(span_x, span_z) if top_axis == "Y" else max(span_x, span_y)
    camera_distance = max(view_span, span_x, span_y, span_z, 100.0)

    camera_data = bpy.data.cameras.new("QC_Top_Camera")
    camera = bpy.data.objects.new("QC_Top_Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    if top_axis == "Y":
        camera.location = (center.x, mins.y - camera_distance, center.z)
        camera.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    else:
        camera.location = (center.x, center.y, maxs.z + camera_distance)
        camera.rotation_euler = (0.0, 0.0, 0.0)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = view_span * 1.04
    camera.data.clip_start = 0.1
    camera.data.clip_end = camera_distance * 4.0
    bpy.context.scene.camera = camera

    bpy.context.scene.render.engine = "BLENDER_WORKBENCH"
    bpy.context.scene.display.shading.light = "STUDIO"
    bpy.context.scene.display.shading.color_type = "MATERIAL"
    bpy.context.scene.render.resolution_x = 1400
    bpy.context.scene.render.resolution_y = 1000
    bpy.context.scene.render.film_transparent = False
    bpy.context.scene.world.color = (0.18, 0.18, 0.18)
    bpy.context.scene.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)

    asphalt = [
        obj.name
        for obj in bpy.context.scene.objects
        if obj.type == "MESH"
        and any(material and material.name == "CIM_Road_Asphalt" for material in obj.data.materials)
    ]
    junction_visible = [obj.name for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.name.startswith("Junction_Surface")]
    print(f"Preview rendered: {out_path}")
    print(f"Bounds min: {tuple(round(v, 3) for v in mins)}")
    print(f"Bounds max: {tuple(round(v, 3) for v in maxs)}")
    print(f"Top axis: {top_axis}")
    print(f"Asphalt mesh count: {len(asphalt)}")
    print(f"Visible Junction_Surface mesh count: {len(junction_visible)}")


if __name__ == "__main__":
    main()
